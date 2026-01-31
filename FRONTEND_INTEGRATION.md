# Frontend Integration: Offline-First Sales Sync

## Quick Start (Frontend Developer Checklist)

### 1. Generate Sale Submission

```javascript
// Generate UUID for idempotent sync
function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Prepare sale payload
const salPayload = {
  external_id: generateUUID(),  // MUST generate on client for idempotency
  source_device: 'mobile-app-v1.2', // OR 'web', 'pos-register-01', etc.
  client_timestamp: new Date().toISOString(), // When user submitted the sale
  items: [
    {
      product: 5, // OR use product_data for new products
      quantity: '0.5', // Must be string/decimal (supports fractions)
      unit_price: '2.00' // Custom price for this sale
    },
    {
      product_data: {
        sku: 'JUICE-100ML',
        name: 'Fresh Juice (100ml)',
        unit_price: '1.50',
        is_volatile: true // For dynamic-priced items (no inventory tracking)
      },
      quantity: '1',
      unit_price: '1.50'
    }
  ]
};
```

### 2. Submit Sale (Online)

```javascript
async function submitSale(salePayload) {
  try {
    const response = await fetch('/api/sales/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Token ${authToken}`
      },
      body: JSON.stringify(salePayload)
    });

    if (response.ok) {
      const sale = await response.json();
      console.log('Sale created:', sale);
      // Clear local queue for this external_id
      removeFromLocalQueue(salePayload.external_id);
      return sale;
    } else {
      const error = await response.json();
      console.error('Error creating sale:', error);
      throw error;
    }
  } catch (error) {
    console.error('Network error:', error);
    // Queue locally and retry when online
    queueForLaterSync(salePayload);
  }
}
```

### 3. Handle Offline Queue (LocalStorage or IndexedDB)

```javascript
// Queue sale locally if offline
function queueForLaterSync(salePayload) {
  const queue = JSON.parse(localStorage.getItem('pendingSales') || '[]');
  queue.push({
    ...salePayload,
    queuedAt: new Date().toISOString()
  });
  localStorage.setItem('pendingSales', JSON.stringify(queue));
  console.log('Sale queued locally. Will sync when online.');
}

// Sync all pending sales when online
async function syncPendingSales() {
  const queue = JSON.parse(localStorage.getItem('pendingSales') || '[]');
  if (queue.length === 0) return;

  console.log(`Syncing ${queue.length} pending sales...`);

  for (const salePayload of queue) {
    try {
      const sale = await submitSale(salePayload);
      console.log(`Synced sale ${salePayload.external_id}`);
    } catch (error) {
      console.error(`Failed to sync ${salePayload.external_id}:`, error);
      // Keep in queue, retry on next attempt
    }
  }
}

// Listen for online/offline events
window.addEventListener('online', syncPendingSales);
window.addEventListener('offline', () => {
  console.log('Offline mode: Future sales will be queued.');
});
```

### 4. UI Components (React Example)

```jsx
import React, { useState } from 'react';
import { v4 as uuidv4 } from 'uuid';

function SalesForm() {
  const [items, setItems] = useState([]);
  const [syncing, setSyncing] = useState(false);
  const [lastSaleId, setLastSaleId] = useState(null);

  const handleAddItem = (product, quantity, unitPrice) => {
    setItems([
      ...items,
      {
        product,
        quantity: String(quantity),
        unit_price: String(unitPrice)
      }
    ]);
  };

  const handleSubmitSale = async () => {
    setSyncing(true);
    const salePayload = {
      external_id: uuidv4(),
      source_device: 'web',
      client_timestamp: new Date().toISOString(),
      items
    };

    try {
      const response = await fetch('/api/sales/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Token ${authToken}`
        },
        body: JSON.stringify(salePayload)
      });

      if (response.ok) {
        const sale = await response.json();
        setLastSaleId(sale.id);
        setItems([]); // Clear form
        // Show success toast
      } else {
        // Handle error or queue locally
        queueForLaterSync(salePayload);
      }
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div>
      <h2>New Sale</h2>
      {items.map((item, idx) => (
        <div key={idx}>
          Product: {item.product}, Qty: {item.quantity}, Price: ${item.unit_price}
        </div>
      ))}
      <button onClick={handleSubmitSale} disabled={syncing || items.length === 0}>
        {syncing ? 'Syncing...' : 'Submit Sale'}
      </button>
      {lastSaleId && <p>✓ Sale #{lastSaleId} saved!</p>}
    </div>
  );
}
```

---

## Sales Report: Query Product Analytics

### Get Total Sales for a Product (with Date Range)

```javascript
async function getProductSalesReport(productId, startDate, endDate, period = 'weekly') {
  const params = new URLSearchParams({
    product_id: productId,
    start_date: startDate, // YYYY-MM-DD
    end_date: endDate,     // YYYY-MM-DD
    period: period         // daily|weekly|monthly
  });

  const response = await fetch(`/api/sales/analytics/?${params}`, {
    headers: {
      'Authorization': `Token ${authToken}`
    }
  });

  if (response.ok) {
    const report = await response.json();
    return report;
  }
  throw new Error('Failed to fetch analytics');
}

// Usage
const report = await getProductSalesReport(
  5, // Bread product
  '2026-01-01',
  '2026-01-31',
  'daily'
);

console.log(`Total sales: ${report.total_quantity_sold} units`);
console.log(`Total revenue: $${report.total_revenue}`);
console.log(`Average price: $${report.average_unit_price}`);

// Display period breakdown
report.period_breakdown.forEach(period => {
  console.log(`${period.date}: ${period.quantity} units, $${period.revenue}`);
});
```

### Response Format

```json
{
  "product": {
    "id": 5,
    "name": "Bread",
    "sku": "BREAD-001",
    "is_volatile": false,
    "unit_price": "2.00",
    "stock": 45
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

---

## Product Types & Pricing

### Volatile Product (Dynamic Pricing, No Inventory)

Use for items where you set the price per sale (e.g., services, bread by weight).

```javascript
{
  product_data: {
    sku: 'BREAD-CUSTOM',
    name: 'Bread by Weight',
    unit_price: '1.50', // Default suggestion
    is_volatile: true   // KEY: No inventory tracking
  },
  quantity: '0.75', // Can be fractional (0.5, 0.75, 1.25, etc.)
  unit_price: '1.50' // Each sale can have different price
}
```

**Behavior:**
- ✓ Price can change per sale
- ✓ No stock deduction
- ✓ Still recorded in sales analytics
- ✓ Backend updates `product.unit_price` as last-used suggestion

### Tracked Product (Fixed Inventory)

Use for items with fixed quantity and inventory tracking.

```javascript
{
  product: 123, // Existing product ID
  quantity: '5', // Integer or decimal
  unit_price: '9.99' // Custom price for this sale (doesn't update product)
}
```

**Behavior:**
- ✓ Stock automatically deducted by backend
- ✓ Stock can go negative (no rejection)
- ✓ Low-stock alerts triggered if `stock < reorder_level`
- ✓ Price per sale stored, product price not updated

---

## Idempotency & Retry Logic

### Key Rule: Same `external_id` = Safe to Retry

```javascript
const externalId = generateUUID(); // Generate ONCE per sale

// First attempt
const sale1 = await submitSale({ external_id: externalId, items: [...] });
// Returns 201 Created

// Network error or user clicks "submit" again
const sale2 = await submitSale({ external_id: externalId, items: [...] });
// Returns 200 OK (same sale as sale1, no duplicate)

// Always safe to retry with same external_id
```

### Offline Scenario with Retry

```javascript
// Day 1: User offline, sales queued locally
const queuedSale = {
  external_id: 'abc-123-def',
  items: [...]
};
localStorage.setItem('sales_queue', JSON.stringify([queuedSale]));

// Day 2: User comes online
navigator.onLine // true
await submitSale(queuedSale);
// Response 201 Created (if no server has it yet)
// OR Response 200 OK (if server already has it from another device)
// Either way: No duplicate created
```

---

## Error Handling

### Validation Errors (400)

```javascript
// Invalid product data
{
  "items": ["0: product_data is not valid"]
}

// Missing required field
{
  "items": ["0: quantity is required"]
}
```

### Not Found (404)

```javascript
// Product doesn't exist
{
  "error": "Product not found or you do not own it"
}
```

### Network Errors

```javascript
// User is offline or server unreachable
try {
  await submitSale(salePayload);
} catch (error) {
  // Queue locally and retry when online
  queueForLaterSync(salePayload);
}
```

---

## Best Practices

✓ **Generate UUID on client** — Each sale needs a unique `external_id` for idempotency  
✓ **Use ISO 8601 timestamps** — `new Date().toISOString()` formats correctly  
✓ **Queue on network error** — Never discard a sale, always retry later  
✓ **Store auth token securely** — Use httpOnly cookie or secure storage  
✓ **Disable submit button while syncing** — Prevent multiple concurrent requests  
✓ **Show user feedback** — Indicate when syncing offline sales  
✓ **Use string quantities** — `"0.5"` not `0.5` to avoid precision loss  
✓ **Track synced state** — Show which sales are pending vs. synced  

---

## Complete Example: Vue.js Component

```vue
<template>
  <div class="sales-form">
    <h2>{{ isOnline ? 'Online' : 'Offline Mode' }}</h2>
    
    <div v-if="items.length === 0" class="empty-state">
      No items added yet
    </div>
    
    <div v-for="(item, idx) in items" :key="idx" class="item">
      <span>{{ item.product }} - {{ item.quantity }} @ ${{ item.unit_price }}</span>
      <button @click="removeItem(idx)">Remove</button>
    </div>
    
    <div class="actions">
      <input v-model="selectedProduct" placeholder="Product ID" />
      <input v-model="quantity" placeholder="Quantity" />
      <input v-model="unitPrice" placeholder="Unit Price" />
      <button @click="addItem">Add Item</button>
    </div>
    
    <div v-if="pendingCount > 0" class="alert">
      ⚠️ {{ pendingCount }} sales pending sync
    </div>
    
    <button @click="submitSale" :disabled="syncing || items.length === 0">
      {{ syncing ? 'Syncing...' : 'Submit Sale' }}
    </button>
    
    <div v-if="lastSaleId" class="success">
      ✓ Sale #{{ lastSaleId }} submitted!
    </div>
  </div>
</template>

<script>
import { v4 as uuidv4 } from 'uuid';

export default {
  data() {
    return {
      items: [],
      selectedProduct: '',
      quantity: '',
      unitPrice: '',
      syncing: false,
      lastSaleId: null,
      isOnline: navigator.onLine,
      pendingCount: 0
    };
  },
  mounted() {
    window.addEventListener('online', () => {
      this.isOnline = true;
      this.syncPendingSales();
    });
    window.addEventListener('offline', () => {
      this.isOnline = false;
    });
    this.loadPendingCount();
  },
  methods: {
    addItem() {
      this.items.push({
        product: parseInt(this.selectedProduct),
        quantity: String(this.quantity),
        unit_price: String(this.unitPrice)
      });
      this.selectedProduct = '';
      this.quantity = '';
      this.unitPrice = '';
    },
    removeItem(idx) {
      this.items.splice(idx, 1);
    },
    async submitSale() {
      this.syncing = true;
      const salePayload = {
        external_id: uuidv4(),
        source_device: 'web',
        client_timestamp: new Date().toISOString(),
        items: this.items
      };

      try {
        const response = await fetch('/api/sales/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Token ${this.$store.state.authToken}`
          },
          body: JSON.stringify(salePayload)
        });

        if (response.ok) {
          const sale = await response.json();
          this.lastSaleId = sale.id;
          this.items = [];
          localStorage.removeItem(`sale_${salePayload.external_id}`);
        } else {
          // Queue for later
          this.queueForLater(salePayload);
        }
      } catch (error) {
        // Network error
        this.queueForLater(salePayload);
      } finally {
        this.syncing = false;
      }
    },
    queueForLater(salePayload) {
      localStorage.setItem(`sale_${salePayload.external_id}`, JSON.stringify(salePayload));
      this.loadPendingCount();
    },
    loadPendingCount() {
      let count = 0;
      for (let i = 0; i < localStorage.length; i++) {
        if (localStorage.key(i).startsWith('sale_')) count++;
      }
      this.pendingCount = count;
    },
    async syncPendingSales() {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key.startsWith('sale_')) {
          const salePayload = JSON.parse(localStorage.getItem(key));
          try {
            await fetch('/api/sales/', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'Authorization': `Token ${this.$store.state.authToken}`
              },
              body: JSON.stringify(salePayload)
            });
            localStorage.removeItem(key);
          } catch (error) {
            // Retry next time
          }
        }
      }
      this.loadPendingCount();
    }
  }
};
</script>
```

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| **Duplicate sales** | Same `external_id` not used for retry | Ensure client stores and reuses `external_id` |
| **Negative stock** | Tracked product sales exceeding inventory | Expected behavior; check low-stock alerts instead |
| **Sales not syncing offline** | Queue not persisted correctly | Use localStorage or IndexedDB, persist all fields |
| **Wrong total amount** | Client sent custom total | Ignore it; backend always computes server-side |
| **Price not updating** | Volatile product price didn't change | Works correctly; price updates as last-used suggestion only |
| **Analytics showing 0** | Filtering by wrong date range | Check `client_timestamp` vs. `timestamp` (offline sales) |

---

**Frontend integration complete. Ready for production offline-first sales!**
