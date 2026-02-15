from decimal import Decimal
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


# ============================================================================
# PRODUCT
# ============================================================================

class Product(models.Model):
    STOCK_MODE_CHOICES = [
        ('TRACKED', 'Tracked Stock'),
        ('UNTRACKED', 'Untracked / Variable'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_products",
        null=True
    )

    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Used as:
    # - Fixed price for TRACKED
    # - Suggested / last-used price for UNTRACKED
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )

    unit = models.CharField(max_length=32, default='unit')

    stock_mode = models.CharField(
        max_length=16,
        choices=STOCK_MODE_CHOICES,
        default='TRACKED'
    )

    # Whether this product is volatile (price/quantity not persisted to stock)
    is_volatile = models.BooleanField(default=False)

    # Only meaningful for TRACKED products
    stock = models.IntegerField(null=True, blank=True)
    reorder_level = models.IntegerField(null=True, blank=True)

    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ------------------------------------------------------------------------
    # Business logic
    # ------------------------------------------------------------------------

    def __str__(self):
        return f"{self.name} ({self.sku})"

    def is_tracked(self) -> bool:
        # A product is considered tracked when it is not marked volatile
        return not self.is_volatile

    def can_deduct(self, qty: int) -> bool:
        if not self.is_tracked():
            return True
        return self.stock is not None and self.stock >= qty

    def adjust_stock(self, qty_delta: int, by_user=None, reason=None, movement_type=None):
        import traceback
        print(f"\n[ADJUST_STOCK] 🔴 Called for {self.sku}")
        print(f"[ADJUST_STOCK] Delta: {qty_delta}, Reason: {reason}")
        print(f"[ADJUST_STOCK] Stack trace:")
        traceback.print_stack()
        print("\n")
        """
        Only applies to TRACKED products.
        UNTRACKED products intentionally skip stock logic.
        Allows negative stock - alerts will be triggered instead.
        """
        if not self.is_tracked():
            return

        if qty_delta == 0:
            return

        with transaction.atomic():
            p = Product.objects.select_for_update().get(pk=self.pk)

            if p.stock is None:
                p.stock = 0

            # REMOVED: Stock validation - allow negative stock
            # if qty_delta < 0 and p.stock < abs(qty_delta):
            #     raise ValueError(...)

            p.stock += qty_delta
            p.save(update_fields=['stock', 'updated_at'])

            StockMovement.objects.create(
                product=p,
                delta=qty_delta,
                resulting_stock=p.stock,
                performed_by=by_user,
                reason=reason or ("restock" if qty_delta > 0 else "sale"),
                movement_type=movement_type or ('RESTOCK' if qty_delta > 0 else 'SALE')
            )

        # Alert will trigger if stock is low or negative
        LowStockAlert.create_or_update_for_product(p)


# ============================================================================
# STOCK MOVEMENTS (TRACKED ONLY)
# ============================================================================

class StockMovement(models.Model):
    MOVEMENT_TYPE_CHOICES = [
        ('SALE', 'Sale'),
        ('RESTOCK', 'Restock'),
        ('ADJUSTMENT', 'Adjustment'),
        ('IMPORT', 'Bulk Import'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='movements'
    )

    delta = models.IntegerField()
    resulting_stock = models.IntegerField()

    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
        null=True,
        blank=True
    )

    reason = models.CharField(max_length=128, blank=True)
    movement_type = models.CharField(
        max_length=20,
        choices=MOVEMENT_TYPE_CHOICES,
        default='ADJUSTMENT'
    )

    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.product.sku}: {self.delta:+d} → {self.resulting_stock}"


# ============================================================================
# SALES
# ============================================================================

class Sale(models.Model):
    sold_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sales"
    )
    
    # OFFLINE-FIRST: Unique ID from client to detect duplicate submissions (idempotency key)
    # If client retries, same external_id means same sale — backend ignores duplicate silently
    external_id = models.UUIDField(
        unique=True,
        null=True,
        blank=True,
        help_text="Client-generated UUID for idempotent sync (offline deduplication)"
    )
    
    # OFFLINE-FIRST: Device/client that submitted this sale for audit & debugging
    source_device = models.CharField(
        max_length=128,
        default='web',
        help_text="Device/client identifier (e.g., 'web', 'mobile-001', 'pos-02')"
    )
    
    # OFFLINE-FIRST: Timestamp when client created the sale (may differ from server receipt time)
    client_timestamp = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When client created the sale (timezone-aware, used for analytics)"
    )
    
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )
    
    # Server timestamp when sale was received/created
    timestamp = models.DateTimeField(default=timezone.now)
    
    # OFFLINE-FIRST: Timestamp when all SaleItems were synced and stock adjusted
    # Used to ensure alerts are created only once per sale
    synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When stock was deducted and alerts were created (idempotency marker)"
    )
    
    # Track when this sale record was last modified (for soft-audit purposes)
    updated_at = models.DateTimeField(auto_now=True)
    
    client_uuid = models.UUIDField(unique=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"Sale #{self.id} ({self.total_amount})"

    def recalc_total(self):
        total = sum(item.subtotal for item in self.items.all())
        self.total_amount = total
        self.save(update_fields=['total_amount'])


class SaleItem(models.Model):
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)  # support fractional for volatile
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, editable=False)

    def save(self, *args, **kwargs):
        # Calculate subtotal
        self.subtotal = Decimal(self.quantity) * self.unit_price
        super().save(*args, **kwargs)

# ============================================================================
# LOW STOCK ALERTS (TRACKED ONLY)
# ============================================================================

class LowStockAlert(models.Model):
    SEVERITY_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='low_stock_alerts'
    )

    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    message = models.CharField(max_length=255)

    triggered_at = models.DateTimeField(default=timezone.now)
    acknowledged = models.BooleanField(default=False)
    days_low_stock = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-triggered_at']
        unique_together = ('product', 'acknowledged')

    def __str__(self):
        return f"[{self.severity.upper()}] {self.product.name}"

    @classmethod
    def create_or_update_for_product(cls, product):
        if not product.is_tracked():
            return

        if product.reorder_level is None or product.stock is None:
            return

        if product.stock > product.reorder_level:
            cls.objects.filter(product=product, acknowledged=False).delete()
            return

        ratio = product.stock / max(product.reorder_level, 1)

        if ratio <= 0.2:
            severity = 'critical'
        elif ratio <= 0.5:
            severity = 'warning'
        else:
            severity = 'info'

        message = f"Stock {severity}ly low — {product.stock} units remaining"

        alert, created = cls.objects.get_or_create(
            product=product,
            acknowledged=False,
            defaults={
                'severity': severity,
                'message': message,
                'days_low_stock': 0
            }
        )

        if not created:
            alert.severity = severity
            alert.message = message
            alert.days_low_stock += 1
            alert.triggered_at = timezone.now()
            alert.save()


# ============================================================================
# USER ALERT PREFERENCES
# ============================================================================

class AlertPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='alert_pref'
    )

    notify_email = models.BooleanField(default=True)
    notify_inapp = models.BooleanField(default=True)

    def __str__(self):
        return f"Alert Preferences for {self.user.username}"
