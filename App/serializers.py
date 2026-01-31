from rest_framework import serializers
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from .models import Product, StockMovement, Sale, SaleItem, LowStockAlert, AlertPreference
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

    class Meta:
        model = Sale
        fields = ['id', 'sold_by', 'total_amount', 'timestamp', 'items']
        read_only_fields = ['sold_by', 'total_amount', 'timestamp']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user = self.context['request'].user

        # Create the Sale first
        sale = Sale.objects.create(sold_by=user)

        total_amount = Decimal('0.00')

        for item_data in items_data:
            # Support either existing product (PK) or nested product_data to create on-the-fly
            product = item_data.get('product')
            product_data = item_data.get('product_data')

            if product is None and product_data:
                # Create product owned by this user
                prod_serializer = ProductSerializer(data=product_data)
                prod_serializer.is_valid(raise_exception=True)
                product = prod_serializer.save(user=user)

            quantity = Decimal(item_data['quantity'])
            unit_price = item_data.get('unit_price')
            if unit_price is None:
                unit_price = product.unit_price if product is not None else Decimal('0.00')

            # Create SaleItem
            sale_item = SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=quantity,
                unit_price=unit_price,
            )

            # Add to total
            total_amount += sale_item.subtotal

            # If product is volatile (untracked), persist last-used price as suggestion
            if getattr(product, 'is_volatile', False):
                product.unit_price = unit_price
                product.save(update_fields=['unit_price'])

            # Deduct stock for tracked products
            if product and product.is_tracked():
                if not product.can_deduct(int(quantity)):
                    raise serializers.ValidationError(
                        f"Insufficient stock for product {product.name}. Available: {product.stock}"
                    )
                product.adjust_stock(
                    qty_delta=-int(quantity),
                    by_user=user,
                    reason=f"Sale #{sale.id}",
                    movement_type='SALE'
                )

        # Update total_amount on Sale
        sale.total_amount = total_amount
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
        model = LowStockAlert
        fields = [
            'id', 'product', 'severity', 'triggered_at',
            'acknowledged', 'days_low_stock', 'message'
        ]

class AlertPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertPreference
        fields = ['notify_email', 'notify_inapp']
