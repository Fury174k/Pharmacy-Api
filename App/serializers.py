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

class SaleItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = ['product', 'quantity', 'unit_price']

    def validate(self, data):
        product = data['product']
        quantity = data['quantity']
        unit_price = data.get('unit_price')

        if quantity <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")

        # Volatile items → price must be provided, stock ignored
        if product.is_volatile:
            if unit_price is None:
                raise serializers.ValidationError(
                    f"Unit price is required for volatile product '{product.name}'."
                )
        else:
            # Non-volatile → enforce stock
            if not product.can_deduct(quantity):
                raise serializers.ValidationError(
                    f"Insufficient stock for '{product.name}'."
                )

            # Lock price if client didn't pass one
            if unit_price is None:
                data['unit_price'] = product.unit_price

        return data


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemCreateSerializer(many=True)

    class Meta:
        model = Sale
        fields = ['id', 'total_amount', 'timestamp', 'items']
        read_only_fields = ['id', 'total_amount', 'timestamp']

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user = self.context['request'].user

        if not items_data:
            raise serializers.ValidationError("A sale must contain at least one item.")

        sale = Sale.objects.create(
            sold_by=user,
            total_amount=Decimal('0.00')
        )

        total = Decimal('0.00')

        for item in items_data:
            product = item['product']
            quantity = item['quantity']
            unit_price = Decimal(item['unit_price'])

            subtotal = unit_price * Decimal(quantity)

            SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=quantity,
                unit_price=unit_price,
                subtotal=subtotal
            )

            # Deduct stock ONLY for non-volatile products
            if not product.is_volatile:
                product.adjust_stock(
                    qty_delta=-quantity,
                    by_user=user,
                    reason=f"Sale #{sale.id}",
                    movement_type='SALE'
                )

            total += subtotal

        sale.total_amount = total
        sale.save(update_fields=['total_amount'])

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
