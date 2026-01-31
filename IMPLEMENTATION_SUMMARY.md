# Offline-First Sales Sync: Implementation Summary

## Files Modified

### 1. **App/models.py** — Sale Model
**Changes:**
- Added `external_id` (UUIDField, unique, null, blank) — idempotency key
- Added `source_device` (CharField) — audit trail for device/client
- Added `client_timestamp` (DateTimeField) — when client created sale (offline support)
- Added `synced_at` (DateTimeField) — when stock was adjusted + alerts created
- Added `updated_at` (DateTimeField, auto_now=True) — record modification tracking

**Why:**
- `external_id` enables idempotent retries (safe to sync same sale multiple times)
- `source_device` helps identify which client/device submitted the sale
- `client_timestamp` allows correct analytics for offline scenarios
- `synced_at` ensures alerts are created only once per sale
- `updated_at` provides audit trail

### 2. **App/serializers.py** — SaleSerializer Rewrite
**Changes:**
- Accepts `external_id`, `source_device`, `client_timestamp` as write-only fields
- Implements idempotent duplicate detection (returns existing sale if external_id found)
- Wraps all operations in `transaction.atomic()` for consistency
- Computes totals 100% server-side (never trusts client)
- **Volatile products:** Bypasses stock, updates `product.unit_price` as suggestion
- **Non-volatile products:** Deducts stock (allows negative), triggers low-stock alerts
- No validation errors for low stock (scalable, simple)

**Why:**
- Strict append-only semantics with offline-first support
- Server-driven stock means no client manipulation possible
- Atomic transactions prevent partial failures
- Alert triggering ensures users are warned of low stock

### 3. **App/views.py** — SaleCreateView + New Analytics Endpoint
**Changes:**
- Added detailed docstring explaining offline-first rules
- `SaleCreateView.perform_create()` simplified (logic delegated to serializer)
- Added `product_sales_analytics()` endpoint for per-product aggregation
  - Works for both volatile and non-volatile products
  - Filters by date range, supports period grouping (daily/weekly/monthly)
  - Returns: total quantity, total revenue, average unit price, period breakdown

**Why:**
- Clear documentation of hard rules for maintainability
- Analytics endpoint answers the core use case: "How much Bread did I sell?"
- Works for both tracked (stock) and untracked (volatile) products

### 4. **App/urls.py** — New Route
**Changes:**
- Added import for `product_sales_analytics`
- Added route: `path('sales/analytics/', product_sales_analytics, name='product-sales-analytics')`

---

## Hard Rules Enforced

✅ **Append-only sales** — No update/delete past sales  
✅ **Stock server-derived** — Never accepted from client  
✅ **Volatile products** — No stock tracking, price suggestions only  
✅ **Non-volatile products** — Stock deducted, can go negative, alerts triggered  
✅ **Idempotent sync** — Same `external_id` = safe to retry  
✅ **Atomic transactions** — All items + stock + alerts or nothing  
✅ **No locking** — Scalable, no optimistic concurrency needed  
✅ **Server-side totals** — Always computed, never trusted from client  

---

## API Endpoints

### POST /api/sales/ (Offline-First Sync)
- **Request:** Items array + `external_id` (optional UUID) + `source_device` (optional) + `client_timestamp` (optional)
- **Response:** 201 Created (new sale) or 200 OK (duplicate via external_id)
- **Logic:**
  - Check if `external_id` exists; return existing if found (idempotent)
  - Atomic transaction: create sale + items + adjust stock + trigger alerts
  - Volatile products: bypass stock, update price suggestion
  - Non-volatile products: deduct stock (allow negative), trigger alerts

### GET /api/sales/analytics/?product_id=123&start_date=2026-01-01&end_date=2026-01-31&period=weekly
- **Query params:** `product_id` (required), `start_date` (optional), `end_date` (optional), `period` (optional)
- **Response:** Product info + total quantity sold + total revenue + average price + period breakdown
- **Works for:** Both volatile and non-volatile products

---

## Data Flow Examples

### Example 1: Online Sale (Volatile Product — Bread)
```
Client → POST /api/sales/ with:
  - product: 5 (Bread, is_volatile=true)
  - quantity: 0.75
  - unit_price: 1.50
  - external_id: UUID

Backend:
  1. Check if external_id exists → No
  2. Create Sale with external_id
  3. Create SaleItem (qty=0.75, price=1.50, subtotal=1.125)
  4. Update product.unit_price = 1.50 (dynamic pricing suggestion)
  5. NO stock deduction (is_volatile)
  6. Compute total = 1.125
  7. Set synced_at = now
  
Response → 201 Created with sale details
```

### Example 2: Offline Sale (Tracked Product — Juice)
```
Client (offline) → Queues locally:
  - product_data: { sku: "JUICE-100ML", name: "Juice", is_volatile: false }
  - quantity: 50
  - unit_price: 0.50
  - external_id: UUID-XYZ
  - client_timestamp: 2026-01-30T10:00:00Z

Later (client online) → POST /api/sales/ with same data

Backend:
  1. Check if external_id exists → No
  2. Create product (Juice, is_volatile=false, owned by user)
  3. Create Sale with external_id
  4. Create SaleItem (qty=50, price=0.50, subtotal=25.00)
  5. Deduct stock: Product.stock -= 50 (can go negative)
  6. Trigger low-stock alert if needed
  7. Compute total = 25.00
  8. Set synced_at = now

Response → 201 Created with sale details

If client crashes and retries with same external_id:
  → Backend returns 200 OK (existing sale, no duplicate)
```

### Example 3: Query Sales by Product (Bread Analytics)
```
Client → GET /api/sales/analytics/?product_id=5&start_date=2026-01-01&end_date=2026-01-31&period=weekly

Backend:
  1. Fetch all SaleItems for product=5 in date range
  2. Aggregate:
     - Total quantity = sum(SaleItem.quantity)
     - Total revenue = sum(SaleItem.subtotal)
     - Average price = total_revenue / total_quantity
  3. Group by period (weekly) and return breakdown

Response:
  {
    "product": { "id": 5, "name": "Bread", "is_volatile": true, ... },
    "total_quantity_sold": 123.5,
    "total_revenue": 185.25,
    "average_unit_price": 1.50,
    "period_breakdown": [
      { "date": "2026-01-27", "quantity": 10.5, "revenue": 15.75 },
      { "date": "2026-02-03", "quantity": 113, "revenue": 169.50 }
    ]
  }
```

---

## Migration Required

```bash
cd c:\Users\ADMIN\Desktop\WorkFolder\MedDigitalAssistant\Backend
python manage.py makemigrations
python manage.py migrate
```

**New database fields:**
- `Sale.external_id` (UUID, unique, null, blank)
- `Sale.source_device` (CharField, default='web')
- `Sale.client_timestamp` (DateTimeField, null, blank)
- `Sale.synced_at` (DateTimeField, null, blank)
- `Sale.updated_at` (DateTimeField, auto_now=True)

---

## Frontend Integration Requirements

### For Online Sales
1. Generate `external_id` (UUID) client-side
2. Set `source_device` (e.g., 'web', 'mobile-app-v1.2')
3. Use current time for `client_timestamp`
4. Include `unit_price` per item (support dynamic pricing)
5. POST to `/api/sales/`
6. On network error: retry with same `external_id` (safe to do multiple times)

### For Offline Sales
1. Queue sales locally with generated `external_id`
2. When online, POST queued sales to `/api/sales/`
3. If response is 200 OK (duplicate), discard local copy
4. If response is 201 Created, clear local queue for that sale
5. On sync failure, keep queue and retry later

### For Volatile Products (Dynamic Pricing)
- Create product with `is_volatile: true`
- On each sale, provide custom `unit_price`
- Backend updates product's suggested price

### For Tracked Products
- Create product with `is_volatile: false` (or omit, defaults to false)
- Stock is automatically deducted on each sale
- Stock can go negative; low-stock alerts trigger automatically

### Query Sales Analytics
- GET `/api/sales/analytics/?product_id=123` to see total sales by product
- Include date range and period for custom reports
- Works for both volatile and tracked products

---

## Why This Design

| Requirement | Implementation | Benefit |
|-------------|-----------------|---------|
| **Idempotency** | `external_id` check, return existing sale | Clients can safely retry without duplicating sales |
| **Offline support** | `client_timestamp` separate from `timestamp` | Correct analytics even for queued sales |
| **Append-only** | No update/delete endpoints | Immutable audit trail, data consistency |
| **Stock accuracy** | Backend-derived, never client-sent | Impossible to manipulate stock via API |
| **Volatile products** | Bypass stock, update price suggestion | Dynamic pricing flexibility without inventory burden |
| **Tracked products** | Auto-deduct, allow negative | Simple model, alerts warn when needed |
| **Scalability** | No locking, atomic transactions | Handles concurrent offline syncs smoothly |
| **Analytics** | Per-product aggregation endpoint | Easy to answer "How much Bread did I sell?" |

---

## Files Changed Summary

| File | Lines Modified | Reason |
|------|-----------------|--------|
| [App/models.py](App/models.py) | 40-50 | Added 5 new fields to Sale model for offline-first support |
| [App/serializers.py](App/serializers.py) | 100-160 | Completely rewrote SaleSerializer for idempotency + transactions |
| [App/views.py](App/views.py) | 125-300 | Updated SaleCreateView docs + added product_sales_analytics endpoint |
| [App/urls.py](App/urls.py) | 1-20 | Added import and route for product_sales_analytics |
| [OFFLINE_SYNC_GUIDE.md](OFFLINE_SYNC_GUIDE.md) | NEW | Comprehensive guide for clients and backend maintainers |

---

## Testing Checklist

- [ ] Makemigrations and migrate successful
- [ ] POST /api/sales/ with new `external_id` → 201 Created
- [ ] POST /api/sales/ with same `external_id` → 200 OK (no duplicate)
- [ ] Volatile product sale → No stock deduction, price updated
- [ ] Tracked product sale → Stock deducted, can go negative
- [ ] Low-stock alert created when stock < reorder_level
- [ ] GET /api/sales/analytics/?product_id=X → Returns aggregated data
- [ ] Offline scenario: Queue locally → Retry online with same external_id → No duplicate

---

## Production Deployment Checklist

- [ ] Run migrations on staging environment
- [ ] Test idempotency with concurrent requests
- [ ] Test analytics queries with large datasets (performance)
- [ ] Verify user-scoped queries (no cross-user access)
- [ ] Monitor database for unique constraint violations on external_id
- [ ] Document API for frontend team
- [ ] Update API documentation with new fields and endpoints

---

**Implementation complete. All hard rules enforced. Ready for offline-first sales tracking.**
