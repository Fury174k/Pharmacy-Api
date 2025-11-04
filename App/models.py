from django.db import models, transaction
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth.models import AbstractUser

class Product(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_products",
        null=True   
    )
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    unit = models.CharField(max_length=32, default='unit')  # e.g., tablet, ml, box
    stock = models.IntegerField(default=0)
    reorder_level = models.IntegerField(default=0)  # when stock <= this, trigger reorder alert
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.sku})"

    def can_deduct(self, qty: int) -> bool:
        return self.stock >= qty

    def adjust_stock(self, qty_delta: int, by_user=None, reason=None, movement_type=None):
        """
        Adjust stock by qty_delta (negative to deduct, positive to add).
        This method records a StockMovement and updates the Product.stock atomically.
        """
        from .models import StockMovement
        from django.utils import timezone
        
        print(f"=== adjust_stock called ===")
        print(f"Product: {self.name} ({self.sku})")
        print(f"qty_delta: {qty_delta}")
        print(f"by_user: {by_user}")
        print(f"Current stock: {self.stock}")
        print(f"Reorder level: {self.reorder_level}")
        
        if qty_delta == 0:
            print("qty_delta is 0, returning early")
            return

        with transaction.atomic():
            p = Product.objects.select_for_update().get(pk=self.pk)
            if qty_delta < 0 and p.stock < abs(qty_delta):
                raise ValueError(f"Insufficient stock for {p.sku}. Available={p.stock}, requested={abs(qty_delta)}")

            p.stock = p.stock + qty_delta
            p.save(update_fields=['stock', 'updated_at'])
            print(f"New stock after adjustment: {p.stock}")

            mt = movement_type if movement_type is not None else ('RESTOCK' if qty_delta > 0 else 'SALE')

            StockMovement.objects.create(
                product=p,
                delta=qty_delta,
                resulting_stock=p.stock,
                performed_by=by_user,
                reason=reason or ("adjustment" if qty_delta > 0 else "sale"),
                movement_type=mt,
            )
            print(f"StockMovement created: {mt}")

        # Check for low stock AFTER the transaction
        alert_user = by_user or p.user
        
        print(f"Checking alerts: stock={p.stock}, reorder={p.reorder_level}, alert_user={alert_user}")
        
        if not alert_user:
            print("WARNING: No alert_user found, skipping alert creation")
            return
            
        if p.stock > p.reorder_level:
            deleted_count = Alert.objects.filter(product=p, acknowledged=False, created_for=alert_user).delete()[0]
            print(f"Stock above reorder level. Deleted {deleted_count} alerts")
        elif p.stock <= p.reorder_level:
            print(f"Stock at/below reorder level! Creating/updating alert...")
            existing_alert = Alert.objects.filter(
                product=p, 
                acknowledged=False, 
                created_for=alert_user
            ).first()
            
            print(f"Existing alert found: {existing_alert}")
            
            severity = 'critical' if p.stock <= max(1, p.reorder_level // 2) else 'warning'
            message = f"Stock {'critically' if severity == 'critical' else ''} low - only {p.stock} units remaining"
            
            if existing_alert:
                print(f"Updating existing alert {existing_alert.id}")
                if existing_alert.severity != severity:
                    existing_alert.severity = severity
                    existing_alert.message = message
                    existing_alert.days_low_stock += 1
                    existing_alert.save()
                    print(f"Alert updated to severity: {severity}")
            else:
                alert = Alert.objects.create(
                    product=p,
                    severity=severity,
                    triggered_at=timezone.now(),
                    acknowledged=False,
                    days_low_stock=0,
                    message=message,
                    created_for=alert_user
                )
                print(f"NEW ALERT CREATED: ID={alert.id}, severity={severity}, user={alert.created_for}")
        
        print(f"=== adjust_stock completed ===\n")


class StockMovement(models.Model):
    MOVEMENT_TYPE_CHOICES = [
        ('SALE', 'Sale'),
        ('RESTOCK', 'Restock'),
        ('ADJUSTMENT', 'Adjustment'),
        ('IMPORT', 'Bulk Import'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    delta = models.IntegerField()  # +ve for incoming, -ve for outgoing
    resulting_stock = models.IntegerField()
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
        null=True,
        blank=True
    )
    reason = models.CharField(max_length=128, blank=True)
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPE_CHOICES, default='ADJUSTMENT')
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.product.sku}: {self.delta:+d} → {self.resulting_stock} ({self.movement_type})"
    
class Sale(models.Model):
    sold_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sales"
    )
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"Sale #{self.id} by {self.sold_by.username} ({self.total_amount})"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('App.Product', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"


class LowStockAlert(models.Model):
    SEVERITY_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ]

    product = models.ForeignKey('App.Product', on_delete=models.CASCADE, related_name='low_stock_alerts')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='info')
    message = models.CharField(max_length=255)
    triggered_at = models.DateTimeField(default=timezone.now)
    acknowledged = models.BooleanField(default=False)
    days_low_stock = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-triggered_at']
        unique_together = ('product', 'acknowledged')  # one active alert per product

    def __str__(self):
        return f"[{self.severity.upper()}] {self.product.name}: {self.message}"

    @classmethod
    def create_or_update_for_product(cls, product):
        """
        Check stock and either create or update an existing low-stock alert.
        Called whenever stock is adjusted.
        """
        from django.utils import timezone
        now = timezone.now()

        # Only trigger alert if product has reorder_level defined
        if product.reorder_level is None:
            return

        # Remove resolved alerts
        if product.stock >= product.reorder_level:
            LowStockAlert.objects.filter(product=product, acknowledged=False).delete()
            return

        # Determine severity based on how low the stock is
        ratio = product.stock / product.reorder_level if product.reorder_level > 0 else 0
        if ratio <= 0.2:
            severity = 'critical'
        elif ratio <= 0.5:
            severity = 'warning'
        else:
            severity = 'info'

        message = f"Stock {severity}ly low - only {product.stock} units remaining"

        # Either update existing or create new
        alert, created = LowStockAlert.objects.get_or_create(
            product=product,
            acknowledged=False,
            defaults={
                'severity': severity,
                'message': message,
                'triggered_at': now,
                'days_low_stock': 0,
            }
        )

        if not created:
            # Update existing alert (e.g., severity got worse)
            alert.severity = severity
            alert.message = message
            alert.days_low_stock += 1
            alert.triggered_at = now
            alert.save()

class Alert(models.Model):
    SEVERITY_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ]

    product = models.ForeignKey('App.Product', on_delete=models.CASCADE, related_name='alerts_entries')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    triggered_at = models.DateTimeField(default=timezone.now)
    acknowledged = models.BooleanField(default=False)
    days_low_stock = models.PositiveIntegerField(default=0)
    message = models.TextField()
    created_for = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='alerts',
        null=True,
        blank=True
    )

    class Meta:
        ordering = ['-triggered_at']

    def __str__(self):
        return f"{self.product.name} ({self.severity})"

class AlertPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='alert_pref')
    notify_email = models.BooleanField(default=True)
    notify_inapp = models.BooleanField(default=True)
    low_stock_threshold = models.PositiveIntegerField(default=5)  # user-level override

    def __str__(self):
        return f"Alert Preferences for {self.user.username}"
