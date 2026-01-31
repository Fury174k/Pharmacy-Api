# Quick Reference Card: Offline-First Sales Sync

## One-Page Overview

### What Changed
✅ Added 5 fields to Sale model (external_id, source_device, client_timestamp, synced_at, updated_at)  
✅ Rewrote SaleSerializer with idempotency + transactions  
✅ Added product_sales_analytics endpoint  
✅ Updated SaleCreateView documentation  

### Key Features
🔄 **Idempotent Sync** — Same external_id = safe retry, no duplicates  
📱 **Offline Support** — Queue locally, sync when online  
💰 **Volatile Products** — Dynamic pricing, no inventory tracking  
📦 **Tracked Products** — Auto stock deduction, negative allowed, alerts trigger  
✅ **Server-Side Totals** — Client totals ignored, backend computes  
📊 **Analytics** — Per-product sales aggregation by period  
🔒 **Atomic Transactions** — All items + stock + alerts or nothing  

---

## API Endpoints

### Create Sale (Offline-First)
```
POST /api/sales/
{
  "external_id": "550e8400-e29b-41d4-a716-446655440000",  // UUID (optional, generated if omitted)
  "source_device": "mobile-app-v1.2",  // Optional, default: 'web'
  "client_timestamp": "2026-01-31T14:30:00Z",  // Optional, default: now
  "items": [
    {
      "product": 5,  // OR use product_data for new product
      "quantity": "0.5",
      "unit_price": "2.00"
    }
  ]
}

Response: 201 Created (new) or 200 OK (duplicate via external_id)
```

### Query Product Sales
```
GET /api/sales/analytics/?product_id=5&start_date=2026-01-01&end_date=2026-01-31&period=weekly

Response:
{
  "product": {...},
  "total_quantity_sold": 123.5,
  "total_revenue": 247.00,
  "average_unit_price": 2.00,
  "period_breakdown": [
    {"date": "2026-01-27", "quantity": 10.5, "revenue": 21.00},
    ...
  ]
}
```

---

## Data Model

### Sale Fields (New)
| Field | Type | Purpose |
|-------|------|---------|
| external_id | UUID | Idempotency key for offline sync |
| source_device | CharField | Device identifier (audit trail) |
| client_timestamp | DateTime | When client created sale |
| synced_at | DateTime | When stock adjusted + alerts created |
| updated_at | DateTime | Last modification time |

### Product Fields (Used)
| Field | Type | Purpose |
|-------|------|---------|
| is_volatile | Boolean | True = dynamic price, no stock; False = fixed inventory |
| unit_price | Decimal | For volatile: last-used suggestion; for tracked: default price |
| stock | Integer | For tracked: auto-deducted; for volatile: ignored |
| reorder_level | Integer | For tracked: triggers alert if stock < this |

---

## Business Logic

### Volatile Product (is_volatile=true)
- ❌ NO stock deduction
- ✅ Price can change per sale
- ✅ Recorded in SaleItem (analytics available)
- ✅ Backend updates product.unit_price as suggestion
- Use case: "Bread by weight", services with dynamic pricing

### Tracked Product (is_volatile=false)
- ✅ Stock deducted automatically
- ✅ Stock can go negative
- ❌ Price not updated by sales
- ✅ Low-stock alert if stock < reorder_level
- Use case: Fixed inventory items

### Idempotency
- Same external_id submitted twice = server returns existing sale (no duplicate)
- Safe for offline retries and network error recovery
- UUID generated on client (or auto-generated server if omitted)

### Server-Side Totals
- Client total ignored
- Backend computes: sum(SaleItem.quantity × SaleItem.unit_price)
- Prevents manipulation via API

---

## Frontend Integration (Quick Start)

### 1. Generate & Submit Sale
```javascript
const externalId = generateUUID();
const response = await fetch('/api/sales/', {
  method: 'POST',
  body: JSON.stringify({
    external_id: externalId,
    source_device: 'web',
    items: [...]
  })
});
```

### 2. Handle Response
```javascript
if (response.status === 201) {
  // New sale created
  clearQueue(externalId);
} else if (response.status === 200) {
  // Duplicate (safe retry); return existing sale
  clearQueue(externalId);
} else {
  // Error; queue for later
  queueOffline({external_id: externalId, items: [...]});
}
```

### 3. Sync When Online
```javascript
window.addEventListener('online', syncQueuedSales);
```

---

## Verification Commands

### Check Installation
```bash
# Verify migrations
python manage.py showmigrations | grep -A5 "App"

# Verify model fields
python manage.py shell
>>> from App.models import Sale
>>> [f.name for f in Sale._meta.get_fields() if f.name in 
...  ['external_id', 'source_device', 'synced_at']]
['external_id', 'source_device', 'synced_at']
```

### Test Idempotency
```bash
# First submission
curl -X POST http://localhost:8000/api/sales/ \
  -H "Authorization: Token ABC" \
  -H "Content-Type: application/json" \
  -d '{"external_id":"UUID-123","items":[...]}'
# Returns: 201 Created

# Retry with same UUID
curl -X POST http://localhost:8000/api/sales/ \
  -H "Authorization: Token ABC" \
  -H "Content-Type: application/json" \
  -d '{"external_id":"UUID-123","items":[...]}'
# Returns: 200 OK (no duplicate)
```

### Test Analytics
```bash
curl -X GET "http://localhost:8000/api/sales/analytics/?product_id=1&start_date=2026-01-01&end_date=2026-01-31" \
  -H "Authorization: Token ABC"
# Returns: Product sales data with totals & breakdown
```

---

## Hard Rules (Enforced by Code)

| Rule | Enforcement |
|------|------------|
| Sales are append-only | No update/delete endpoints |
| Stock server-derived | SaleSerializer ignores client stock |
| Volatile bypass stock | is_tracked() check in save logic |
| Tracked allow negative | No validation rejects low stock |
| Idempotent sync | external_id unique constraint + check before create |
| Transaction atomic | transaction.atomic() wraps all operations |
| Server totals only | Client totals ignored in serializer |

---

## Common Scenarios

### Scenario 1: Online Sale (Bread - Volatile)
```
Client: POST /api/sales/ with:
  product: 5 (Bread, is_volatile=true)
  quantity: 0.75
  unit_price: 1.50

Backend:
  ✓ Create Sale + SaleItem
  ✓ Update product.unit_price = 1.50 (dynamic price)
  ✓ NO stock deduction
  ✓ SaleItem.subtotal = 1.125

Response: 201 Created
```

### Scenario 2: Offline → Online (Juice - Tracked)
```
Day 1 (Offline):
  Queue locally: {
    external_id: UUID-A,
    product_data: {sku: JUICE, name: Juice, is_volatile: false},
    quantity: 50,
    unit_price: 0.50
  }

Day 2 (Online):
  POST /api/sales/ with same payload

Backend:
  ✓ Create product (Juice, is_volatile=false)
  ✓ Create Sale + SaleItem
  ✓ Deduct stock: 50
  ✓ Trigger alert if stock < reorder_level

Response: 201 Created

Day 2 (Retry):
  POST /api/sales/ with SAME UUID-A

Backend:
  ✓ Check: external_id UUID-A exists? YES
  ✓ Return existing sale (no duplicate)

Response: 200 OK
```

### Scenario 3: Query Sales Report
```
Client: GET /api/sales/analytics/?product_id=5&start_date=2026-01-01&end_date=2026-01-31

Backend:
  ✓ Fetch all SaleItems for product=5
  ✓ Filter by date range
  ✓ Aggregate: SUM(quantity), SUM(subtotal)
  ✓ Group by week
  ✓ Calculate average price

Response: Total + breakdown by week
{
  "total_quantity_sold": 123.5,
  "total_revenue": 185.25,
  "period_breakdown": [...]
}
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Duplicate sales | Ensure frontend generates & uses unique external_id |
| Stock not deducting | Check is_volatile=false on product |
| No alerts | Verify reorder_level set; check stock < reorder_level |
| Analytics 0 results | Check date range and product_id; verify SaleItems exist |
| 201 always (no duplicates) | Ensure external_id provided; check DB for unique constraint |

---

## Documentation Files

| File | Audience |
|------|----------|
| OFFLINE_SYNC_GUIDE.md | Backend developers |
| FRONTEND_INTEGRATION.md | Frontend developers |
| IMPLEMENTATION_SUMMARY.md | Tech leads |
| MIGRATION_GUIDE.md | DevOps, DBAs |
| ARCHITECTURE_DIAGRAMS.md | Everyone (visual) |
| README_OFFLINE_SYNC.md | Everyone (overview) |
| DEPLOYMENT_CHECKLIST.md | DevOps, QA |

---

## Migration Commands

```bash
# Generate
python manage.py makemigrations

# Apply
python manage.py migrate

# Verify
python manage.py showmigrations

# Rollback (if needed)
python manage.py migrate App <previous_number>
```

---

## Key Takeaways

✅ **Idempotency** via external_id prevents offline retry duplicates  
✅ **Volatile products** bypass inventory; dynamic pricing support  
✅ **Tracked products** auto-deduct stock; negative allowed  
✅ **Server totals** prevent API manipulation  
✅ **Atomic transactions** ensure consistency  
✅ **Analytics endpoint** answers "How much X did I sell?"  
✅ **Production-ready** with comprehensive docs & examples  

---

**Print this card. Reference it daily. You're ready to deploy!**
