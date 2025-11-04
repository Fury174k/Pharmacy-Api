from rest_framework import serializers
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from .models import Product, StockMovement, Sale, SaleItem, LowStockAlert, Alert, AlertPreference
from django.db import transaction
from decimal import Decimal

User = get_user_model()

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'pharmacy_name', 'license_number']

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email', ''),
            password=validated_data['password'],
            pharmacy_name=validated_data.get('pharmacy_name', ''),
            license_number=validated_data.get('license_number', '')
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
        # When updating, allow the same SKU for the same instance
        if getattr(self, 'instance', None):
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("SKU already exists. Try a different one.")
        return value


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
    # subtotal is computed server-side when creating a Sale; clients should not be required to provide it
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = SaleItem
        fields = ['product', 'quantity', 'unit_price', 'subtotal']


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True)

    class Meta:
        model = Sale
        fields = ['id', 'sold_by', 'total_amount', 'timestamp', 'items']
        read_only_fields = ['sold_by', 'timestamp', 'total_amount']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user = self.context['request'].user

        print(f"=== SaleSerializer.create called ===")
        print(f"User: {user}")
        print(f"Items count: {len(items_data)}")

        with transaction.atomic():
            sale = Sale.objects.create(sold_by=user)
            total = Decimal('0.00')

            for item_data in items_data:
                product = item_data['product']
                qty = item_data['quantity']
                price = item_data['unit_price']
                subtotal = Decimal(qty) * price
                total += subtotal

                print(f"Processing item: {product.name}")
                print(f"  Stock before: {product.stock}")
                print(f"  Quantity to deduct: {qty}")
                print(f"  Reorder level: {product.reorder_level}")

                # Deduct stock
                product.adjust_stock(-qty, by_user=user, reason="sale")
                
                # Refresh to see new stock
                product.refresh_from_db()
                print(f"  Stock after: {product.stock}")
                print(f"  Should trigger alert? {product.stock <= product.reorder_level}")

                # Create SaleItem record
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    quantity=qty,
                    unit_price=price,
                    subtotal=subtotal,
                )

            sale.total_amount = total
            sale.save()
            
        print(f"Sale completed. Total: {total}")
        
        # Check alerts after sale
        alerts_count = Alert.objects.filter(created_for=user, acknowledged=False).count()
        print(f"Alerts for {user.username}: {alerts_count}")
        
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
        model = Alert
        fields = [
            'id', 'product', 'severity', 'triggered_at',
            'acknowledged', 'days_low_stock', 'message'
        ]

class AlertPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertPreference
        fields = ['notify_email', 'notify_inapp', 'low_stock_threshold']