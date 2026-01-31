# Offline-First Sales Sync Implementation Guide

## Overview
This implementation provides **append-only, idempotent offline sales syncing** with strict backend-driven stock management. Clients submit sales events; the backend handles all stock logic, totals computation, and alert triggering.

---

## Hard Rules (ENFORCED)

✅ **Sales are append-only** — never update, delete, or overwrite past sales  
✅ **Stock is derived server-side** — never sent or trusted from clients  
✅ **Offline clients send sales events only** — no stock values  
✅ **Volatile products bypass stock entirely** — but still appear in analytics  
✅ **Non-volatile products** — stock deducted, can go negative, alerts triggered  
✅ **Idempotent sync** — same `external_id` = safe to retry without duplicates  
✅ **No locking, no optimistic concurrency** — scalable and simple  
✅ **No rejection due to low stock** — backend allows negative stock  

---

## Data Model Changes

### Sale Model

New fields added to `Sale`:

```python
external_id = UUIDField(unique=True, null=True, blank=True)
    # Client-generated UUID for idempotent sync
    # If client retries with same external_id, server returns existing sale (no duplicate)

source_device = CharField(max_length=128, default='web')
    # Device/client identifier for audit (e.g., 'web', 'mobile-001', 'pos-02')

client_timestamp = DateTimeField(null=True, blank=True)
    # When the client created the sale (for analytics when client was offline)

synced_at = DateTimeField(null=True, blank=True)
    # When all SaleItems were processed + stock adjusted + alerts created
    # Used to ensure alerts are created only once per sale

updated_at = DateTimeField(auto_now=True)
    # Tracks record modifications for audit purposes
```

**Key Point:** `timestamp` is when the server received it; `client_timestamp` is when the client created it (differs if offline).

---

## API Endpoint: POST /api/sales/

### Request Payload

```json
{
  "items": [
    {
      "product": 123,
      "quantity": "0.5",
      "unit_price": "2.00"
    },
    {
      "product_data": {
        "sku": "BREAD-FRESH",
        "name": "Fresh Bread (Half-Loaf)",
        "unit_price": "1.50",
        "is_volatile": true
      },
      "quantity": "1",
      "unit_price": "1.50"
    }
  ],
  "external_id": "550e8400-e29b-41d4-a716-446655440000",
  "source_device": "mobile-001",
  "client_timestamp": "2026-01-31T14:30:00Z"
}
```

### Request Field Rules

- **`items`** (required, array)
  - Each item must have either `product` (existing product ID) or `product_data` (create new)
  - `quantity` (required, string/decimal) — supports fractional quantities
  - `unit_price` (optional) — defaults to `product.unit_price` if omitted

- **`external_id`** (optional, UUID)
  - Client-generated UUID for idempotent retries
  - If omitted, server generates one
  - If provided and already exists, return 200 with existing sale (no duplicate error)

- **`source_device`** (optional, string, default: `'web'`)
  - Device identifier for audit/debugging
  - Examples: `'web'`, `'mobile-001'`, `'pos-register-02'`

- **`client_timestamp`** (optional, ISO 8601 datetime)
  - When the client created the sale (for offline scenarios)
  - If omitted, defaults to server time

### Response (201 Created or 200 OK for duplicates)

```json
{
  "id": 456,
  "sold_by": 1,
  "total_amount": "3.50",
  "timestamp": "2026-01-31T14:35:22Z",
  "synced_at": "2026-01-31T14:35:23Z",
  "items": [
    {
      "product": 123,
      "product_name": "Bread",
      "quantity": "0.5",
      "unit_price": "2.00",
      "subtotal": "1.00"
    },
    {
      "product": 124,
      "product_name": "Fresh Bread (Half-Loaf)",
      "quantity": "1",
      "unit_price": "1.50",
      "subtotal": "1.50"
    }
  ],
  "external_id": "550e8400-e29b-41d4-a716-446655440000",
  "source_device": "mobile-001",
  "client_timestamp": "2026-01-31T14:30:00Z"
}
```

---

## Backend Logic Flow

### 1. Idempotency Check

```python
# If external_id provided and already exists, return it (no duplicate)
if external_id:
    try:
        existing_sale = Sale.objects.get(external_id=external_id)
        return existing_sale  # Status 200 OK
    except Sale.DoesNotExist:
        pass
else:
    # Generate if not provided
    external_id = uuid.uuid4()
```

### 2. Atomic Transaction

All operations (create sale, add items, adjust stock, trigger alerts) happen in a single DB transaction:

```python
with transaction.atomic():
    sale = Sale.objects.create(
        sold_by=user,
        external_id=external_id,
        source_device=source_device,
        client_timestamp=client_timestamp or now(),
        # ... other fields
    )
    
    # Process each item
    for item_data in items_data:
        # ... create SaleItem
        # ... update volatile product price (if applicable)
        # ... deduct stock for non-volatile products
    
    # Compute total server-side (never trust client)
    sale.total_amount = total_from_items
    sale.synced_at = now()
    sale.save()
    
    # Trigger alerts for non-volatile products
    for product in products_to_alert:
        LowStockAlert.create_or_update_for_product(product)
```

### 3. Product Handling

#### Existing Product (by ID)
```python
product = item_data['product']  # Must exist and belong to user
```

#### Create-on-the-Fly (nested `product_data`)
```python
if product_data:
    # Product created and owned by authenticated user
    product = ProductSerializer(data=product_data).save(user=user)
```

### 4. Stock Deduction Rules

#### Volatile Products (`is_volatile=True`)
- ❌ NO stock deduction
- ✅ Price updated as last-used suggestion (`product.unit_price = unit_price`)
- ✅ Still recorded in `SaleItem` (analytics available)

#### Non-Volatile Products (`is_volatile=False`)
- ✅ Stock deducted (even if it goes negative)
- ❌ NO rejection if stock < 0
- ✅ Low-stock alert triggered if stock < reorder_level
- ✅ Price stored in `SaleItem.unit_price` (per-sale price, doesn't update product)

---

## Analytics Endpoint: GET /api/sales/analytics/

Query per-product sales for any date range (works for both volatile and non-volatile):

### Request

```
GET /api/sales/analytics/?product_id=123&start_date=2026-01-01&end_date=2026-01-31&period=weekly
```

### Query Parameters

- **`product_id`** (required) — Product ID to aggregate
- **`start_date`** (optional, YYYY-MM-DD) — Defaults to 30 days ago
- **`end_date`** (optional, YYYY-MM-DD) — Defaults to today
- **`period`** (optional, `daily|weekly|monthly`) — Defaults to `weekly`

### Response

```json
{
  "product": {
    "id": 123,
    "name": "Bread",
    "sku": "BREAD-001",
    "is_volatile": false,
    "unit_price": "2.00",
    "stock": -5
  },
  "total_quantity_sold": 123.5,
  "total_revenue": 247.00,
  "average_unit_price": 2.00,
  "period_breakdown": [
    {
      "date": "2026-01-27",
      "quantity": 10.5,
      "revenue": 21.00
    },
    {
      "date": "2026-02-03",
      "quantity": 113,
      "revenue": 226.00
    }
  ]
}
```

**Key:** This works for **both volatile and non-volatile** products. Volatile products have `stock=None` or `stock=0`, but sales are still aggregated.

---

## Client Integration Checklist

### For Online Sales
1. Generate `external_id` (UUID) on client
2. Populate `source_device` (e.g., 'web', 'mobile-app-v1.2')
3. Use current timestamp for `client_timestamp`
4. Include `quantity` and `unit_price` for each item
5. POST to `/api/sales/`
6. On error: retry with same `external_id` (idempotent)

### For Offline Sales (Queue + Sync Later)
1. Generate `external_id` on client, store locally
2. Queue the sale object with `source_device`, `client_timestamp`, `items`
3. When online, POST to `/api/sales/` with same `external_id`
4. If server already has it (duplicate), response is 200 OK (safe to discard local copy)
5. If new, server creates it, deducts stock, triggers alerts

### For Volatile Products (Dynamic Pricing)
1. Create product with `is_volatile: true`
2. For each sale, include custom `unit_price`
3. Backend updates `product.unit_price` as last-used suggestion
4. Stock is ignored; no inventory management needed

### For Non-Volatile Products (Tracked Stock)
1. Create product with `is_volatile: false`
2. Manually manage initial stock (via `/api/stock-movements/`)
3. On each sale, backend automatically deducts stock
4. Stock **can go negative** (backend doesn't reject)
5. Low-stock alerts trigger when `stock < reorder_level`
6. Query analytics via `/api/sales/analytics/?product_id=...` for sales by period

---

## Error Handling

### Duplicate Sale (same `external_id`)
```
Request:  POST /api/sales/ with external_id="ABC-123"
Database: Sale already exists with external_id="ABC-123"
Response: 200 OK (returns existing sale, no error)
```

### Invalid Product
```
Request:  POST /api/sales/ with product_id=999 (doesn't exist)
Response: 400 Bad Request (validation error)
```

### Product Not Owned by User
```
Request:  POST /api/sales/ with product_id=123 (owned by another user)
Response: 403 Forbidden or 404 Not Found
```

### Invalid `product_data`
```
Request:  product_data with invalid SKU format
Response: 400 Bad Request (serializer validation)
```

---

## Example Workflows

### Workflow 1: Online Sale (Bread - Volatile)
```python
POST /api/sales/
{
  "items": [
    {
      "product": 5,  # Bread product (is_volatile=true)
      "quantity": "0.75",
      "unit_price": "1.50"
    }
  ],
  "source_device": "web",
  "external_id": "550e8400-e29b-41d4-a716-446655440000"
}

# Response: 201 Created
# - SaleItem created (quantity=0.75, unit_price=1.50, subtotal=1.125)
# - Product.unit_price updated to 1.50 (last-used suggestion)
# - Stock NOT affected (is_volatile=true)
# - Total = 1.125
# - synced_at set to now
```

### Workflow 2: Mobile Offline Sale (Retry After Coming Online)
```python
# Client was offline, queued this locally:
POST /api/sales/
{
  "items": [
    {
      "product_data": {
        "sku": "JUICE-100ML",
        "name": "Juice (100ml)",
        "unit_price": "0.50",
        "is_volatile": false
      },
      "quantity": "50",
      "unit_price": "0.50"
    }
  ],
  "source_device": "mobile-app-v1.2",
  "client_timestamp": "2026-01-30T10:00:00Z",
  "external_id": "660e8400-e29b-41d4-a716-446655440001"
}

# First sync attempt (online now):
# Response: 201 Created
# - New product created (Juice, is_volatile=false, owned by user)
# - SaleItem created (quantity=50)
# - Stock deducted: 50 units
# - Alert triggered if stock now < reorder_level
# - synced_at set to now

# Client crashes and retries with same external_id:
POST /api/sales/ (same payload, same external_id)
# Response: 200 OK (returns existing sale, no duplicate)
```

### Workflow 3: Query Sales by Product (Any Period)
```python
GET /api/sales/analytics/?product_id=5&start_date=2026-01-01&end_date=2026-01-31&period=daily

# Response: Aggregated sales for Bread (whether volatile or tracked)
{
  "product": { "id": 5, "name": "Bread", "is_volatile": true, ... },
  "total_quantity_sold": 123.5,
  "total_revenue": 185.25,
  "average_unit_price": 1.50,
  "period_breakdown": [...]
}
```

---

## Database Migrations

After applying these changes, run:

```bash
python manage.py makemigrations
python manage.py migrate
```

**New fields added to Sale:**
- `external_id` (UUIDField, unique, null, blank)
- `source_device` (CharField, default='web')
- `client_timestamp` (DateTimeField, null, blank)
- `synced_at` (DateTimeField, null, blank)
- `updated_at` (DateTimeField, auto_now=True)

---

## Summary

| Aspect | Implementation |
|--------|-----------------|
| **Idempotency** | `external_id` UUID, check before create |
| **Append-only** | No update/delete endpoints for past sales |
| **Stock (non-volatile)** | Backend deducts, allows negative, alerts triggered |
| **Stock (volatile)** | Bypassed entirely, still in analytics |
| **Transactions** | Atomic: all items + stock + alerts or nothing |
| **Totals** | Computed server-side, never trusted from client |
| **Analytics** | Per-product aggregation by date range, period |
| **Offline Support** | Client queues, retries with same `external_id` |
| **Scalability** | No locking, no optimistic concurrency |

---

## Production Safety

✅ Transactions are atomic (DB consistency)  
✅ Unique constraints on `external_id` + user prevent duplicates  
✅ Stock can go negative (no hard constraint, but alerts warn)  
✅ No API-level rejection due to low stock (simple, scalable)  
✅ Serializer validates all input before DB writes  
✅ User-scoped queries (ForeignKey to `sold_by`) prevent cross-user access  

This implementation is **MVP-safe**, **scalable**, and **production-realistic**.
