from rest_framework import serializers
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from .models import Product, StockMovement, Sale, SaleItem, LowStockAlert, AlertPreference
from django.db import transaction
from decimal import Decimal
from django.utils import timezone
import uuid

User = get_user_model()

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password']

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email', ''),
            password=validated_data['password'],
        )
        Token.objects.create(user=user)
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'
        read_only_fields = ['user', 'created_at', 'updated_at']

    def validate_sku(self, value):
        qs = Product.objects.filter(sku=value)
        if getattr(self, 'instance', None):
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("SKU already exists. Try a different one.")
        return value

    def validate(self, data):
        """
        Volatile products:
        - Do NOT track stock
        - Do NOT use reorder levels
        """
        is_volatile = data.get(
            'is_volatile',
            getattr(self.instance, 'is_volatile', False)
        )

        if is_volatile:
            data['stock'] = 0
            data['reorder_level'] = 0

        return data


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source='product.name')
    sku = serializers.ReadOnlyField(source='product.sku')
    performed_by_name = serializers.ReadOnlyField(source='performed_by.username')

    class Meta:
        model = StockMovement
        fields = [
            'id',
            'product',
            'sku',
            'product_name',
            'delta',
            'resulting_stock',
            'performed_by',
            'performed_by_name',
            'reason',
            'movement_type',
            'timestamp',
        ]
        read_only_fields = ['performed_by', 'resulting_stock', 'timestamp', 'movement_type']

class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source='product.name')
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    product_data = ProductSerializer(write_only=True, required=False)

    class Meta:
        model = SaleItem
        fields = ['product', 'product_data', 'product_name', 'quantity', 'unit_price', 'subtotal']

class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    external_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    source_device = serializers.CharField(max_length=128, required=False, default='web', write_only=True)
    client_timestamp = serializers.DateTimeField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Sale
        fields = [
            'id', 'sold_by', 'total_amount', 'timestamp', 'synced_at',
            'items', 'external_id', 'source_device', 'client_timestamp'
        ]
        read_only_fields = ['sold_by', 'total_amount', 'timestamp', 'synced_at', 'id']

    def create(self, validated_data):
        """
        OFFLINE-FIRST SALES SYNC (APPEND-ONLY, IDEMPOTENT)
        
        Hard Rules:
        1. Sales are append-only (never update/delete past sales)
        2. Stock is derived on backend, never accepted from client
        3. Volatile products bypass stock entirely
        4. Non-volatile products allow negative stock + trigger alerts
        5. Sync is idempotent (same external_id = no duplicate)
        """
        items_data = validated_data.pop('items')
        user = self.context['request'].user
        
        # Extract offline-sync metadata
        external_id = validated_data.pop('external_id', None)
        import traceback
        
        source_device = validated_data.pop('source_device', 'web')
        client_timestamp = validated_data.pop('client_timestamp', None)

        # IDEMPOTENCY: If external_id provided and already exists, return existing sale silently
        # This allows safe retries from offline clients without duplicating sales
        if external_id:
            try:
                existing_sale = Sale.objects.get(external_id=external_id)
                # Sale already synced; return it without error
                return existing_sale
            except Sale.DoesNotExist:
                pass
        else:
            # Generate external_id if not provided (ensures all sales can be de-duplicated)
            external_id = uuid.uuid4()

        # TRANSACTION: All-or-nothing multi-item sale creation + stock adjustment
        # Ensures atomicity: if any item fails, entire sale is rolled back
        with transaction.atomic():
            # Create Sale with offline-sync metadata
            sale = Sale.objects.create(
                sold_by=user,
                external_id=external_id,
                source_device=source_device,
                client_timestamp=client_timestamp or timezone.now(),
                client_uuid=uuid.uuid4(),
                **validated_data
            )

            total_amount = Decimal('0.00')
            products_to_alert = []  # Track non-volatile products for alert triggering

            for item_data in items_data:
                # Support either existing product (PK) or nested product_data for one-off items
                product = item_data.get('product')
                product_data = item_data.get('product_data')

                if product is None and product_data:
                    # One-off product: create it owned by this user
                    prod_serializer = ProductSerializer(data=product_data)
                    prod_serializer.is_valid(raise_exception=True)
                    product = prod_serializer.save(user=user)

                quantity = Decimal(item_data['quantity'])
                unit_price = item_data.get('unit_price')
                if unit_price is None:
                    unit_price = product.unit_price if product is not None else Decimal('0.00')

                # Create SaleItem (always persisted, regardless of stock status)
                sale_item = SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    quantity=quantity,
                    unit_price=unit_price,
                )

                # Accumulate total server-side (never trust client total)
                total_amount += sale_item.subtotal

                # VOLATILE PRODUCTS: Bypass stock entirely, still record in analytics
                # Update product's last-used price for dynamic pricing suggestions
                if getattr(product, 'is_volatile', False):
                    product.unit_price = unit_price
                    product.save(update_fields=['unit_price', 'updated_at'])
                
                # NON-VOLATILE PRODUCTS: Deduct stock on backend, allow negative, trigger alerts
                else:
                    # Always deduct stock (no validation, allow negative)
                    # Backend stock is derived, never sent from client
                    qty_int = int(quantity)
                    product.adjust_stock(
                        qty_delta=-qty_int,
                        by_user=user,
                        reason=f"Sale #{sale.id}",
                        movement_type='SALE'
                    )
                    # Mark for alert creation after all items are saved
                    if product not in products_to_alert:
                        products_to_alert.append(product)

            # Server-side total computation (NEVER trust client)
            sale.total_amount = total_amount
            sale.synced_at = timezone.now()  # Mark as fully synced
            sale.save(update_fields=['total_amount', 'synced_at', 'updated_at'])

            # Trigger low-stock alerts for non-volatile products
            # (Idempotent: existing alert is updated, new one created if needed)
            for product in products_to_alert:
                LowStockAlert.create_or_update_for_product(product)

        return sale


class LowStockAlertSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)

    class Meta:
        model = LowStockAlert
        fields = [
            'id',
            'product',
            'severity',
            'message',
            'days_low_stock',
        ]
        read_only_fields = ['triggered_at', 'acknowledged']

class ProductBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name', 'sku', 'stock', 'reorder_level']

class AlertSerializer(serializers.ModelSerializer):
    product = ProductBriefSerializer(read_only=True)

    class Meta:
        model = LowStockAlert
        fields = [
            'id', 'product', 'severity', 'triggered_at',
            'acknowledged', 'days_low_stock', 'message'
        ]

class AlertPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertPreference
        fields = ['notify_email', 'notify_inapp']
