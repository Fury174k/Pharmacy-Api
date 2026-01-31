# Architecture & Flow Diagrams

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND CLIENT                       │
│  (Web, Mobile, POS)                                          │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 1. Generate UUID (external_id)                      │   │
│  │ 2. Build sale payload (items, source_device)       │   │
│  │ 3. Submit online OR queue offline                  │   │
│  │ 4. On retry: use SAME external_id (idempotency)    │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ▼                                   │
│              ┌────────────────────────┐                     │
│              │  POST /api/sales/      │                     │
│              │  (with external_id)    │                     │
│              └────────────────────────┘                     │
└─────────────────────────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      BACKEND (Django)                        │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │ STEP 1: Idempotency Check                          │    │
│  │ if external_id exists → RETURN 200 OK (no dup)    │    │
│  └────────────────────────────────────────────────────┘    │
│                           ▼                                  │
│  ┌────────────────────────────────────────────────────┐    │
│  │ STEP 2: Atomic Transaction Begins                  │    │
│  │ (All-or-nothing semantics)                         │    │
│  │                                                    │    │
│  │ ┌─ Create Sale record                             │    │
│  │ │  - external_id (unique constraint)              │    │
│  │ │  - source_device (audit)                        │    │
│  │ │  - client_timestamp (offline support)           │    │
│  │ │                                                 │    │
│  │ ├─ For each item:                                 │    │
│  │ │  - Create/get Product (if product_data)         │    │
│  │ │  - Create SaleItem (quantity, unit_price)       │    │
│  │ │  - Calculate subtotal                           │    │
│  │ │                                                 │    │
│  │ │  IF is_volatile (volatile product):             │    │
│  │ │    └─ Update product.unit_price (suggestion)    │    │
│  │ │       NO stock deduction                        │    │
│  │ │                                                 │    │
│  │ │  IF NOT is_volatile (tracked product):          │    │
│  │ │    └─ Deduct stock (allow negative)             │    │
│  │ │    └─ Mark for alert creation                   │    │
│  │ │                                                 │    │
│  │ ├─ Compute total (sum all subtotals)              │    │
│  │ │                                                 │    │
│  │ ├─ For each tracked product:                      │    │
│  │ │  └─ Create/update low-stock alert               │    │
│  │ │     (if stock < reorder_level)                 │    │
│  │ │                                                 │    │
│  │ └─ Update Sale.synced_at (idempotency marker)     │    │
│  │                                                    │    │
│  │ Commit Transaction ✓                               │    │
│  └────────────────────────────────────────────────────┘    │
│                           ▼                                  │
│  ┌────────────────────────────────────────────────────┐    │
│  │ STEP 3: Return Response                            │    │
│  │ 201 Created OR 200 OK (idempotent)                │    │
│  │ with full sale details                            │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                           ▼
        ┌─────────────────────────────────────┐
        │   Frontend handles response:         │
        │   - 201: Clear queue                │
        │   - 200: Clear queue (duplicate)    │
        │   - Error: Keep in queue, retry     │
        └─────────────────────────────────────┘
```

---

## Idempotency Flow (Offline Retry)

```
TIME 0: First Submission (Online)
┌─────────────────────────────┐
│ Client generates UUID:       │
│ external_id = "UUID-ABC"     │
│                              │
│ → POST /api/sales/          │
│   {                          │
│     external_id: "UUID-ABC", │
│     items: [...]             │
│   }                          │
└─────────────────────────────┘
           ▼
┌─────────────────────────────┐
│ Backend:                     │
│ Check: exists(UUID-ABC)?     │
│ No → Create new sale        │
│ Return 201 Created          │
└─────────────────────────────┘
           ▼
┌─────────────────────────────┐
│ Client receives 201         │
│ → Clear local queue         │
│ → Mark as synced            │
└─────────────────────────────┘


TIME 1: Network Error (Still Offline)
┌─────────────────────────────┐
│ Client queues locally:       │
│ {                            │
│   external_id: "UUID-ABC",  │
│   items: [...]              │
│ }                            │
│                              │
│ (App crashed, retried, etc) │
└─────────────────────────────┘
           ▼
┌─────────────────────────────┐
│ Client comes online         │
│                              │
│ Retry with SAME UUID-ABC    │
│ → POST /api/sales/          │
│   {                          │
│     external_id: "UUID-ABC", │
│     items: [...]             │
│   }                          │
└─────────────────────────────┘
           ▼
┌─────────────────────────────┐
│ Backend:                     │
│ Check: exists(UUID-ABC)?     │
│ YES → Return existing sale  │
│ Return 200 OK               │
│ (NO DUPLICATE CREATED)      │
└─────────────────────────────┘
           ▼
┌─────────────────────────────┐
│ Client receives 200         │
│ → Recognize duplicate       │
│ → Clear local queue         │
│ → No duplicate sale!        │
└─────────────────────────────┘
```

---

## Stock Deduction Flow (Tracked vs Volatile)

```
                    POST /api/sales/ received
                            ▼
                 ┌──────────────────────┐
                 │ For each SaleItem:   │
                 └──────────────────────┘
                            ▼
            ┌───────────────────────────────────┐
            │ Determine product type:           │
            │ product.is_volatile?              │
            └───────────────────────────────────┘
                       ▼                ▼
            ╔══════════════╗    ╔═════════════╗
            ║  VOLATILE    ║    ║  TRACKED    ║
            ║  is_volatile ║    ║  non-volat. ║
            ║   = TRUE     ║    ║   = FALSE   ║
            ╚══════════════╝    ╚═════════════╝
                   ▼                    ▼
        ┌──────────────────┐    ┌──────────────────┐
        │ NO STOCK         │    │ DEDUCT STOCK     │
        │ DEDUCTION        │    │                  │
        │                  │    │ qty_delta =      │
        │ ✓ Update price   │    │  -item.quantity  │
        │   suggestion:    │    │                  │
        │   product.       │    │ product.stock += │
        │   unit_price =   │    │   qty_delta      │
        │   item.unit_     │    │                  │
        │   price          │    │ ✓ Allow negative │
        │                  │    │ ✓ Create stock   │
        │ ✓ Still in       │    │   movement       │
        │   SaleItem       │    │ ✓ Mark for       │
        │   (analytics)    │    │   alert          │
        │                  │    │                  │
        │ ✓ No inventory   │    │ ✓ Track history  │
        │   mgmt           │    │   (StockMovement)│
        └──────────────────┘    └──────────────────┘
                   ▼                    ▼
        ┌──────────────────┐    ┌──────────────────┐
        │ Continue to next │    │ If stock now <   │
        │ item             │    │ reorder_level:   │
        │                  │    │                  │
        │                  │    │ Create/update    │
        │                  │    │ LowStockAlert    │
        │                  │    │                  │
        │                  │    │ Continue to next │
        │                  │    │ item             │
        └──────────────────┘    └──────────────────┘
                   ▼                    ▼
                   └────────┬───────────┘
                           ▼
                  ┌────────────────────┐
                  │ Compute total from │
                  │ all SaleItems      │
                  │                    │
                  │ Update Sale.total  │
                  │ Update Sale.synced │
                  │                    │
                  │ Commit transaction │
                  └────────────────────┘
```

---

## Data Model Relationships

```
┌─────────────────────────────────────────────────────────────┐
│                        USER (Django)                        │
├─────────────────────────────────────────────────────────────┤
│ id (PK)                                                     │
│ username, email, password_hash                             │
└─────────────────────────────────────────────────────────────┘
       ▲                           ▲
       │                           │
       │ sold_by (FK)              │ user (FK)
       │                           │
       ▼                           ▼
┌──────────────────────┐  ┌──────────────────────┐
│       SALE           │  │     PRODUCT          │
├──────────────────────┤  ├──────────────────────┤
│ id (PK)              │  │ id (PK)              │
│ external_id (unique) │  │ sku (unique)         │
│ source_device        │  │ name                 │
│ client_timestamp     │  │ unit_price           │
│ timestamp            │  │ is_volatile (bool)   │
│ synced_at            │  │ stock (int, null)    │
│ total_amount         │  │ reorder_level (null) │
│ updated_at           │  │ active (bool)        │
│ client_uuid (unique) │  │ created_at, updated_ │
└──────────────────────┘  └──────────────────────┘
       ▲                           ▲
       │                           │
       │ items (1-to-many)         │ product (FK)
       │                           │
       ▼                           │
┌──────────────────────┐          │
│      SALEITEM        │          │
├──────────────────────┤◄─────────┘
│ id (PK)              │
│ sale (FK)            │
│ product (FK)         │
│ quantity (Decimal)   │
│ unit_price (Decimal) │
│ subtotal (Decimal)   │
└──────────────────────┘

┌──────────────────────────────────┐
│    STOCKMOVEMENT                 │
├──────────────────────────────────┤
│ id (PK)                          │
│ product (FK)                     │
│ delta (int)                      │
│ resulting_stock (int)            │
│ performed_by (FK User)           │
│ movement_type (SALE|RESTOCK|...) │
│ timestamp                        │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│    LOWSTOCKALERT                 │
├──────────────────────────────────┤
│ id (PK)                          │
│ product (FK)                     │
│ severity (info|warning|critical) │
│ message                          │
│ triggered_at                     │
│ acknowledged (bool)              │
│ days_low_stock (int)             │
└──────────────────────────────────┘
```

---

## Transaction Semantics (ACID)

```
BEFORE TRANSACTION:
┌─────────────────────────────────────┐
│ Product Bread:                      │
│   stock = 100                       │
│   reorder_level = 20                │
│                                     │
│ No SaleItems, No alerts             │
└─────────────────────────────────────┘


TRANSACTION BEGINS (atomic block):
┌─────────────────────────────────────┐
│ 1. Create Sale record               │
│ 2. Create SaleItem (qty=50)         │
│ 3. Deduct stock: 100 - 50 = 50      │
│ 4. Create StockMovement record      │
│ 5. stock 50 < reorder_level 20?     │
│    YES → Create LowStockAlert       │
│ 6. Update Sale.total_amount         │
│ 7. Update Sale.synced_at            │
│                                     │
│ EVERYTHING SUCCEEDS → COMMIT ✓      │
└─────────────────────────────────────┘


AFTER TRANSACTION (All changes or nothing):
┌─────────────────────────────────────┐
│ Product Bread:                      │
│   stock = 50 ✓                      │
│   reorder_level = 20                │
│                                     │
│ Sale created ✓                      │
│ SaleItem created ✓                  │
│ StockMovement created ✓             │
│ LowStockAlert created ✓             │
│                                     │
│ All changes persisted or ROLLBACK   │
└─────────────────────────────────────┘


IF ERROR DURING TRANSACTION:
┌─────────────────────────────────────┐
│ 1. Create Sale record ✓             │
│ 2. Create SaleItem ✓                │
│ 3. Deduct stock ✓                   │
│ 4. Create StockMovement ✓           │
│ 5. Alert creation FAILS ✗           │
│                                     │
│ → ROLLBACK ALL (1-5)                │
│    Database unchanged               │
│    Return 500 error to client       │
│    Client retries later             │
└─────────────────────────────────────┘
```

---

## Query Patterns (Analytics)

```
QUERY: "How much Bread did I sell in January?"

GET /api/sales/analytics/?product_id=5&start_date=2026-01-01&end_date=2026-01-31

┌─────────────────────────────────────────┐
│ 1. Get product (id=5)                  │
│    Verify: product.user = request.user │
└─────────────────────────────────────────┘
           ▼
┌─────────────────────────────────────────┐
│ 2. Query SaleItems:                     │
│    SELECT * FROM SaleItem               │
│    WHERE product=5                      │
│    AND sale__timestamp BETWEEN          │
│        2026-01-01 AND 2026-01-31       │
└─────────────────────────────────────────┘
           ▼
┌─────────────────────────────────────────┐
│ 3. Aggregate:                           │
│    total_qty = SUM(quantity)            │
│    total_revenue = SUM(subtotal)        │
│    avg_price = total_revenue/total_qty  │
└─────────────────────────────────────────┘
           ▼
┌─────────────────────────────────────────┐
│ 4. Period breakdown (weekly):           │
│    GROUP BY WEEK(sale__timestamp)       │
│    SUM quantity per week                │
│    SUM subtotal per week                │
└─────────────────────────────────────────┘
           ▼
┌─────────────────────────────────────────┐
│ Response:                               │
│ {                                       │
│   "product": { ... },                  │
│   "total_quantity_sold": 123.5,        │
│   "total_revenue": 247.00,             │
│   "average_unit_price": 2.00,          │
│   "period_breakdown": [                │
│     {                                  │
│       "date": "2026-01-27",            │
│       "quantity": 10.5,                │
│       "revenue": 21.00                 │
│     },                                 │
│     ...                                │
│   ]                                    │
│ }                                      │
└─────────────────────────────────────────┘
```

---

## Error Handling Flow

```
                    Client Request
                           ▼
            ┌──────────────────────────────┐
            │ Serialize & Validate Input   │
            └──────────────────────────────┘
                           ▼
        ┌──────────────────────────────────────────┐
        │ Input validation error?                  │
        │ (invalid quantity, missing field, etc)   │
        └──────────────────────────────────────────┘
                    YES ▼           NO ▼
            ┌─────────────────┐  ┌────────────────┐
            │ Return 400 Bad  │  │ Check external │
            │ Request (error  │  │ _id exists?    │
            │ details)        │  └────────────────┘
            └─────────────────┘       YES ▼  NO ▼
                                   ┌─────────┐┌──────────┐
                                   │Return   ││Begin     │
                                   │200 OK   ││trans-    │
                                   │(existing)││action   │
                                   └─────────┘└──────────┘
                                              ▼
                                    ┌────────────────────┐
                                    │ Product check      │
                                    │ exists & owned?    │
                                    └────────────────────┘
                                       YES ▼      NO ▼
                                    ┌────────┐ ┌───────────┐
                                    │Continue││ Return 404 │
                                    │        │ │ Not Found  │
                                    └────────┘ └───────────┘
                                    ▼
                                ┌──────────────────┐
                                │ Stock deduction  │
                                │ (if tracked)     │
                                │                  │
                                │ Allow negative   │
                                │ No validation    │
                                │ error here       │
                                └──────────────────┘
                                    ▼
                            ┌───────────────────┐
                            │ Transaction       │
                            │ succeeds?         │
                            └───────────────────┘
                           YES ▼         NO ▼
                        ┌────────┐   ┌──────────┐
                        │Commit  │   │ Rollback │
                        │Return  │   │ Return   │
                        │201     │   │500 Error │
                        │Created │   │(retry)   │
                        └────────┘   └──────────┘
```

---

## Offline Queue Lifecycle

```
TIME 1: User is Online
┌──────────────────────────────┐
│ Sale submitted               │
│                              │
│ POST /api/sales/ → 201      │
│ Clear from local storage     │
│ User sees "Saved!" ✓         │
└──────────────────────────────┘


TIME 2: User Goes Offline
┌──────────────────────────────┐
│ Network drops                │
│ → Fetch error               │
│ → Store in localStorage     │
│ → Show "Offline - Queued"   │
└──────────────────────────────┘
           ▼
┌──────────────────────────────┐
│ Local Storage:               │
│ {                            │
│   "sale_UUID-123": {         │
│     external_id: UUID-123,   │
│     items: [...],            │
│     queuedAt: timestamp      │
│   }                          │
│ }                            │
└──────────────────────────────┘


TIME 3: App Closed & Reopened (Offline)
┌──────────────────────────────┐
│ App loads                    │
│ Check navigator.onLine       │
│ Still offline                │
│ → Load queue from storage    │
│ → Show pending sales         │
└──────────────────────────────┘


TIME 4: User Comes Online
┌──────────────────────────────┐
│ Network reconnected          │
│ window.addEventListener(     │
│   'online',                  │
│   syncPendingSales           │
│ )                            │
└──────────────────────────────┘
           ▼
┌──────────────────────────────┐
│ Iterate localStorage:        │
│ For each queued sale:        │
│   POST with same external_id │
└──────────────────────────────┘
           ▼
        ┌─────────────────────────────────────┐
        │ Response status?                    │
        └─────────────────────────────────────┘
           │
    201 ───┼──── 200 ──── Error
    New    │   Duplicate  (retry later)
           ▼        ▼         ▼
        Delete   Delete   Keep in
        from     from     queue
        storage  storage
           │        │         │
           └────┬───┴────┬────┘
                ▼
        ┌─────────────────────┐
        │ Clear "Offline -    │
        │  Queued" message    │
        │                     │
        │ Show "All synced!" ✓│
        └─────────────────────┘
```

---

**These diagrams provide visual clarity on the system architecture, data flow, and edge cases.**
