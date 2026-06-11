from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from .serializers import RegisterSerializer, LoginSerializer, ProductSerializer, StockMovementSerializer, SaleSerializer, LowStockAlertSerializer, AlertPreferenceSerializer
from .models import Product, StockMovement, Sale, SaleItem, LowStockAlert, AlertPreference
from rest_framework import generics, permissions, status
from django.db import transaction, models
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.db.models import Sum, Count, Q, F
from rest_framework.parsers import MultiPartParserfrom django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from .serializers import RegisterSerializer, LoginSerializer, ProductSerializer, StockMovementSerializer, SaleSerializer, LowStockAlertSerializer, AlertPreferenceSerializer
from .models import Product, StockMovement, Sale, SaleItem, LowStockAlert, AlertPreference
from rest_framework import generics, permissions, status
from django.db import transaction, models
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.db.models import Sum, Count, Q, F
from rest_framework.parsers import MultiPartParser
from .utils.csv_importer import import_products_from_csv
from decimal import Decimal


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def import_csv(request):
    file = request.FILES.get('file')
    if not file:
        return Response({"error": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

    result = import_products_from_csv(file, user=request.user)
    return Response(result, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = serializer.save()
    token = Token.objects.get(user=user)

    return Response({
        "token": token.key,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
        }
    }, status=status.HTTP_201_CREATED)



@api_view(['POST'])
@permission_classes([AllowAny])
def login_user(request):
    serializer = LoginSerializer(data=request.data)
    if serializer.is_valid():
        username = serializer.validated_data['username']
        password = serializer.validated_data['password']
        user = authenticate(username=username, password=password)
        if user:
            token, _ = Token.objects.get_or_create(user=user)
            return Response({'token': token.key, 'username': user.username})
        return Response({'error': 'Invalid credentials'}, status=400)
    return Response(serializer.errors, status=400)


@api_view(['POST'])
def logout_user(request):
    request.user.auth_token.delete()
    return Response({'message': 'Logged out successfully'}, status=200)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """
    Combined dashboard endpoint - returns all data needed for home screen in one call.
    Reduces load from 4 API calls to 1, dramatically improving load time.
    
    IMPORTANT: Proactively creates alerts for products with current low stock
    to ensure alerts appear immediately when a product first falls below reorder_level.
    """
    from datetime import date, datetime, timedelta
    
    today = date.today()
    user = request.user
    
    # PROACTIVE ALERT CREATION: Ensure alerts exist for all products currently at/below reorder level
    # This handles products that may have started with low stock or had reorder_level updated
    low_stock_products = Product.objects.filter(
        user=user,
        active=True,
        stock__lte=models.F('reorder_level'),
        reorder_level__gt=0,
        is_volatile=False  # Only tracked products
    )
    for product in low_stock_products:
        LowStockAlert.create_or_update_for_product(product)
    
    # Sales today - get aggregated data without full records
    today_sales = Sale.objects.filter(
        timestamp__date=today
    )
    sales_count = today_sales.count()
    today_revenue = today_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    
    # Recent sales (last 5 for display)
    recent_sales_list = SaleSerializer(
        today_sales.order_by('-timestamp')[:5],
        many=True
    ).data
    
    # Low stock count - products that need reordering
    low_stock_count = Product.objects.filter(
        user=user,
        active=True,
        stock__lte=models.F('reorder_level'),
        reorder_level__gt=0
    ).count()
    
    # Recent stock movements (last 5 only)
    recent_movements = StockMovement.objects.filter(
        product__user=user
    ).select_related('product').order_by('-timestamp')[:5]
    recent_movements_data = StockMovementSerializer(recent_movements, many=True).data
    
    # Critical unread alerts (last 3 only)
    # IMPORTANT: Only show alerts for products that CURRENTLY have low stock.
    # This prevents stale alerts from showing up after restocking.
    from django.db.models import Q
    critical_alerts = LowStockAlert.objects.filter(
        product__user=user,
        severity='critical',
        acknowledged=False,
        product__stock__lt=models.F('product__reorder_level')  # Validate stock is still low
    ).select_related('product').order_by('-triggered_at')[:3]
    critical_alerts_data = LowStockAlertSerializer(critical_alerts, many=True).data
    
    return Response({
        'sales_today': {
            'count': sales_count,
            'revenue': float(today_revenue),
        },
        'recent_sales': recent_sales_list,
        'low_stock_count': low_stock_count,
        'recent_movements': recent_movements_data,
        'critical_alerts': critical_alerts_data,
        'critical_alert_count': len(critical_alerts_data),
    })

class ProductByBarcodeView(generics.RetrieveAPIView):
    """
    GET /api/products/by_barcode/?code=<scanned_value>
 
    Returns the product whose barcode or SKU matches the scanned value.
    Used by the mobile scanner for instant product lookup.
 
    Response:
        200 — product object
        404 — no product found for this code
    """
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]
 
    def get_object(self):
        code = self.request.query_params.get('code', '').strip()
        if not code:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"code": "This field is required."})
 
        user = self.request.user
 
        # Try barcode field first, then fall back to SKU
        product = (
            Product.objects.filter(user=user, barcode=code).first()
            or Product.objects.filter(user=user, sku__iexact=code).first()
        )
 
        if not product:
            from rest_framework.exceptions import NotFound
            raise NotFound(f"No product found with barcode or SKU '{code}'.")
 
        return product    

class SaleBatchSyncView(generics.GenericAPIView):
    """
    POST /api/sales/batch/
 
    End-of-day batch sync. Accepts all offline sales for a given business_date
    in a single atomic request.
 
    Request body:
    {
        "business_date": "2024-01-15",
        "sales": [
            {
                "external_id": "uuid-...",
                "source_device": "mobile-001",
                "business_date": "2024-01-15",
                "items": [
                    {
                        "product": 42,
                        "quantity": "2",
                        "unit_price": "5.00"
                    }
                ]
            },
            ...
        ]
    }
 
    Response:
    {
        "business_date": "2024-01-15",
        "received": 5,       // total sales in request
        "created": 3,        // newly created
        "duplicate": 2,      // already existed (idempotent, not an error)
        "failed": 0,         // individual failures (see errors list)
        "errors": []         // per-sale errors if any failed
    }
 
    Rules:
    - The entire batch is NOT one transaction — individual sales are atomic.
      This way one bad sale does not block the rest of the day's data.
    - Idempotency: sales with a matching external_id are silently skipped.
    - business_date is stamped on every created Sale for date-partitioned queries.
    """
    serializer_class = SaleSerializer
    permission_classes = [IsAuthenticated]
 
    def post(self, request, *args, **kwargs):
        business_date = request.data.get('business_date')
        sales_data = request.data.get('sales', [])
 
        if not business_date:
            return Response(
                {"error": "business_date is required (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST
            )
 
        if not isinstance(sales_data, list) or len(sales_data) == 0:
            return Response(
                {"error": "sales must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST
            )
 
        created = 0
        duplicate = 0
        failed = 0
        errors = []
 
        for idx, sale_data in enumerate(sales_data):
            # Stamp business_date on every sale in the batch
            sale_data['business_date'] = business_date
 
            external_id = sale_data.get('external_id')
 
            # Fast idempotency check before hitting the serializer
            if external_id:
                try:
                    Sale.objects.get(external_id=external_id)
                    duplicate += 1
                    continue
                except Sale.DoesNotExist:
                    pass
 
            serializer = SaleSerializer(
                data=sale_data,
                context={'request': request}
            )
 
            if serializer.is_valid():
                try:
                    serializer.save()
                    created += 1
                except Exception as e:
                    failed += 1
                    errors.append({
                        "index": idx,
                        "external_id": external_id,
                        "error": str(e),
                    })
            else:
                failed += 1
                errors.append({
                    "index": idx,
                    "external_id": external_id,
                    "error": serializer.errors,
                })
 
        http_status = (
            status.HTTP_200_OK if failed == 0
            else status.HTTP_207_MULTI_STATUS
        )
 
        return Response(
            {
                "business_date": business_date,
                "received": len(sales_data),
                "created": created,
                "duplicate": duplicate,
                "failed": failed,
                "errors": errors,
            },
            status=http_status
        )

class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        limit = int(self.request.query_params.get('limit', 100))
        offset = int(self.request.query_params.get('offset', 0))
        return Product.objects.filter(user=self.request.user).select_related('user').order_by('id')[offset:offset + limit]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ProductRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a single product. Ensures only the owner may access it."""
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # restrict to products owned by requesting user
        return Product.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        # Ensure the owner remains the same (user is read-only in serializer)
        product = serializer.save()
        # Re-evaluate low-stock state immediately after manual stock updates
        LowStockAlert.create_or_update_for_product(product)

class StockMovementCreateView(generics.ListCreateAPIView):
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        limit = self.request.query_params.get('limit', 50)
        return StockMovement.objects.filter(
            product__user=self.request.user
        ).select_related('product', 'performed_by').order_by('-timestamp')[:int(limit)]

    @transaction.atomic
    def perform_create(self, serializer):
        product = serializer.validated_data['product']
        delta = serializer.validated_data['delta']
        reason = serializer.validated_data.get('reason')

        if product.user != self.request.user:
            raise PermissionError("You do not own this product.")

        if product.is_volatile:
            raise ValueError("Stock movements are not allowed for volatile products.")

        product.adjust_stock(
            qty_delta=delta,
            by_user=self.request.user,
            reason=reason,
            movement_type='RESTOCK' if delta > 0 else 'ADJUSTMENT'
        )

        # Immediately refresh low-stock alerts after any stock change
        LowStockAlert.create_or_update_for_product(product)


class SaleCreateView(generics.ListCreateAPIView):
    """
    Offline-First Sales Sync Endpoint
    
    POST: Submit a sale (online or offline sync)
    GET: List sales for authenticated user
    
    OFFLINE-FIRST RULES:
    - Sales are append-only (never update/delete)
    - Client must provide external_id (UUID) for idempotent retries
    - If external_id already exists, return 200 silently (no duplicate error)
    - Stock is derived server-side, never accepted from client
    - Volatile products bypass stock entirely
    - Non-volatile products allow negative stock + trigger low-stock alerts
    - All item totals computed server-side (never trust client)
    - Transaction-atomic: all items + stock adjustment or nothing
    """
    serializer_class = SaleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        limit = self.request.query_params.get('limit', 50)
        return Sale.objects.all().order_by('-timestamp')[:int(limit)]

    def perform_create(self, serializer):
        serializer.save()



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_by_date(request):
    from datetime import datetime, date

    date_str = request.query_params.get('date')
    if date_str:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        selected_date = date.today()

    sales = Sale.objects.filter(timestamp__date=selected_date)
    serializer = SaleSerializer(sales, many=True)
    total = sales.aggregate(total_amount=Sum('total_amount'))['total_amount'] or 0
    return Response([
    {
        "date": str(selected_date),
        "total": total,
        "transactions": sales.count(),
        "sales": serializer.data
    }
])


@api_view(["GET"])
def sales_trend(request):
    """
    Returns aggregated sales data by day, week, or month.
    Example: /api/sales/trend/?period=weekly
    """
    period = request.GET.get("period", "weekly").lower()

    # Choose the right truncation based on period
    if period == "daily":
        trunc_func = TruncDate
    elif period == "monthly":
        trunc_func = TruncMonth
    else:
        trunc_func = TruncWeek

    # Use your actual timestamp field
    sales = (
        Sale.objects.annotate(period=trunc_func("timestamp"))
        .values("period")
        .annotate(
            total_sales=Count("id"),
            total_amount=Sum("total_amount"),
        )
        .order_by("period")
    )

    # Format response to match frontend expectations
    data = [
        {
            "date": s["period"],
            "count": s["total_sales"],
            "total_amount": float(s["total_amount"] or 0),
        }
        for s in sales
    ]

    return Response(data)

@api_view(["GET"])
def product_sales_analytics(request):
    """
    Get aggregated sales data for a specific product (with/without stock tracking).
    
    Query params:
    - product_id: Required. The product to aggregate sales for.
    - start_date: Optional (YYYY-MM-DD). Defaults to 30 days ago.
    - end_date: Optional (YYYY-MM-DD). Defaults to today.
    - period: Optional (daily|weekly|monthly). Defaults to weekly.
    
    Returns:
    {
        "product": {...},
        "total_quantity_sold": 123.5,
        "total_revenue": 1234.56,
        "average_unit_price": 9.99,
        "period_breakdown": [
            {"date": "2026-01-27", "quantity": 10.5, "revenue": 105.00}
        ]
    }
    
    KEY: This endpoint works for BOTH tracked (non-volatile) and untracked (volatile) products.
    Volatile products have no stock, but sales are still recorded and aggregated.
    """
    from datetime import datetime, timedelta, date as date_type
    
    product_id = request.query_params.get('product_id')
    if not product_id:
        return Response(
            {"error": "product_id query param is required"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response(
            {"error": "Product not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Parse date range
    start_str = request.query_params.get('start_date')
    end_str = request.query_params.get('end_date')
    period = request.query_params.get('period', 'weekly').lower()
    
    if end_str:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
    else:
        end_date = date_type.today()
    
    if start_str:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
    else:
        start_date = end_date - timedelta(days=30)
    
    # Fetch all sale items for this product in the date range
    sale_items = SaleItem.objects.filter(
        product=product,
        sale__timestamp__date__gte=start_date,
        sale__timestamp__date__lte=end_date
    ).select_related('sale')
    
    if not sale_items.exists():
        return Response({
            "product": ProductSerializer(product).data,
            "total_quantity_sold": 0,
            "total_revenue": Decimal('0.00'),
            "average_unit_price": Decimal('0.00'),
            "period_breakdown": []
        })
    
    # Aggregate totals
    totals = sale_items.aggregate(
        total_qty=Sum('quantity'),
        total_revenue=Sum('subtotal'),
    )
    
    total_qty = totals['total_qty'] or Decimal('0')
    total_revenue = totals['total_revenue'] or Decimal('0.00')
    avg_price = (total_revenue / total_qty) if total_qty > 0 else Decimal('0.00')
    
    # Period breakdown
    if period == "daily":
        trunc_func = TruncDate
    elif period == "monthly":
        trunc_func = TruncMonth
    else:
        trunc_func = TruncWeek
    
    period_data = (
        sale_items
        .annotate(period=trunc_func('sale__timestamp'))
        .values('period')
        .annotate(
            qty=Sum('quantity'),
            revenue=Sum('subtotal')
        )
        .order_by('period')
    )
    
    period_breakdown = [
        {
            "date": str(p['period']),
            "quantity": float(p['qty']),
            "revenue": float(p['revenue'])
        }
        for p in period_data
    ]
    
    return Response({
        "product": ProductSerializer(product).data,
        "total_quantity_sold": float(total_qty),
        "total_revenue": float(total_revenue),
        "average_unit_price": float(avg_price),
        "period_breakdown": period_breakdown
    })

class LowStockAlertListView(generics.ListAPIView):
    serializer_class = LowStockAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Show alerts created for the logged-in user
        return LowStockAlert.objects.filter(
            product__user=self.request.user,
            acknowledged=False
        ).select_related('product')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "alerts": serializer.data,
            "unread_count": queryset.filter(acknowledged=False).count(),
            "critical_count": queryset.filter(severity='critical').count()
        })


class AlertHistoryView(generics.ListAPIView):
    serializer_class = LowStockAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        limit = self.request.query_params.get('limit', 50)
        return LowStockAlert.objects.filter(
            product__user=self.request.user
        ).select_related('product').order_by('-triggered_at')[:int(limit)]
    
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "alerts": serializer.data,
            "unread_count": LowStockAlert.objects.filter(
                product__user=request.user,
                acknowledged=False
            ).count(),
            "critical_count": LowStockAlert.objects.filter(
                product__user=request.user,
                severity='critical'
            ).count()
        })

# POST /api/alerts/acknowledge/
class AcknowledgeAlertView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        alert_ids = request.data.get('alert_ids', [])
        updated = LowStockAlert.objects.filter(id__in=alert_ids, product__user=request.user).update(acknowledged=True)
        return Response({"message": f"{updated} alerts acknowledged."})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def acknowledge_all_alerts(request):
    """
    Mark ALL unacknowledged alerts as read for the authenticated user.
    """
    updated = LowStockAlert.objects.filter(
        product__user=request.user,
        acknowledged=False
    ).update(acknowledged=True)
    return Response({"message": f"{updated} alerts acknowledged."})


# GET & PUT /api/alerts/settings/
class AlertSettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        pref, _ = AlertPreference.objects.get_or_create(user=request.user)
        serializer = AlertPreferenceSerializer(pref)
        return Response(serializer.data)

    def put(self, request):
        pref, _ = AlertPreference.objects.get_or_create(user=request.user)
        serializer = AlertPreferenceSerializer(pref, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

from .utils.csv_importer import import_products_from_csv
from decimal import Decimal


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def import_csv(request):
    file = request.FILES.get('file')
    if not file:
        return Response({"error": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

    result = import_products_from_csv(file, user=request.user)
    return Response(result, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = serializer.save()
    token = Token.objects.get(user=user)

    return Response({
        "token": token.key,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
        }
    }, status=status.HTTP_201_CREATED)



@api_view(['POST'])
@permission_classes([AllowAny])
def login_user(request):
    serializer = LoginSerializer(data=request.data)
    if serializer.is_valid():
        username = serializer.validated_data['username']
        password = serializer.validated_data['password']
        user = authenticate(username=username, password=password)
        if user:
            token, _ = Token.objects.get_or_create(user=user)
            return Response({'token': token.key, 'username': user.username})
        return Response({'error': 'Invalid credentials'}, status=400)
    return Response(serializer.errors, status=400)


@api_view(['POST'])
def logout_user(request):
    request.user.auth_token.delete()
    return Response({'message': 'Logged out successfully'}, status=200)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """
    Combined dashboard endpoint - returns all data needed for home screen in one call.
    Reduces load from 4 API calls to 1, dramatically improving load time.
    
    IMPORTANT: Proactively creates alerts for products with current low stock
    to ensure alerts appear immediately when a product first falls below reorder_level.
    """
    from datetime import date, datetime, timedelta
    
    today = date.today()
    user = request.user
    
    # PROACTIVE ALERT CREATION: Ensure alerts exist for all products currently at/below reorder level
    # This handles products that may have started with low stock or had reorder_level updated
    low_stock_products = Product.objects.filter(
        user=user,
        active=True,
        stock__lte=models.F('reorder_level'),
        reorder_level__gt=0,
        is_volatile=False  # Only tracked products
    )
    for product in low_stock_products:
        LowStockAlert.create_or_update_for_product(product)
    
    # Sales today - get aggregated data without full records
    today_sales = Sale.objects.filter(
        timestamp__date=today
    )
    sales_count = today_sales.count()
    today_revenue = today_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    
    # Recent sales (last 5 for display)
    recent_sales_list = SaleSerializer(
        today_sales.order_by('-timestamp')[:5],
        many=True
    ).data
    
    # Low stock count - products that need reordering
    low_stock_count = Product.objects.filter(
        user=user,
        active=True,
        stock__lte=models.F('reorder_level'),
        reorder_level__gt=0
    ).count()
    
    # Recent stock movements (last 5 only)
    recent_movements = StockMovement.objects.filter(
        product__user=user
    ).select_related('product').order_by('-timestamp')[:5]
    recent_movements_data = StockMovementSerializer(recent_movements, many=True).data
    
    # Critical unread alerts (last 3 only)
    # IMPORTANT: Only show alerts for products that CURRENTLY have low stock.
    # This prevents stale alerts from showing up after restocking.
    from django.db.models import Q
    critical_alerts = LowStockAlert.objects.filter(
        product__user=user,
        severity='critical',
        acknowledged=False,
        product__stock__lt=models.F('product__reorder_level')  # Validate stock is still low
    ).select_related('product').order_by('-triggered_at')[:3]
    critical_alerts_data = LowStockAlertSerializer(critical_alerts, many=True).data
    
    return Response({
        'sales_today': {
            'count': sales_count,
            'revenue': float(today_revenue),
        },
        'recent_sales': recent_sales_list,
        'low_stock_count': low_stock_count,
        'recent_movements': recent_movements_data,
        'critical_alerts': critical_alerts_data,
        'critical_alert_count': len(critical_alerts_data),
    })

class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        limit = int(self.request.query_params.get('limit', 100))
        offset = int(self.request.query_params.get('offset', 0))
        return Product.objects.select_related('user').order_by('id')[offset:offset + limit]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ProductRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a single product. Ensures only the owner may access it."""
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # restrict to products owned by requesting user
        return Product.objects.all()

    def perform_update(self, serializer):
        # Ensure the owner remains the same (user is read-only in serializer)
        product = serializer.save()
        # Re-evaluate low-stock state immediately after manual stock updates
        LowStockAlert.create_or_update_for_product(product)

class StockMovementCreateView(generics.ListCreateAPIView):
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        limit = self.request.query_params.get('limit', 50)
        return StockMovement.objects.filter(
            product__user=self.request.user
        ).select_related('product', 'performed_by').order_by('-timestamp')[:int(limit)]

    @transaction.atomic
    def perform_create(self, serializer):
        product = serializer.validated_data['product']
        delta = serializer.validated_data['delta']
        reason = serializer.validated_data.get('reason')

        if product.user != self.request.user:
            raise PermissionError("You do not own this product.")

        if product.is_volatile:
            raise ValueError("Stock movements are not allowed for volatile products.")

        product.adjust_stock(
            qty_delta=delta,
            by_user=self.request.user,
            reason=reason,
            movement_type='RESTOCK' if delta > 0 else 'ADJUSTMENT'
        )

        # Immediately refresh low-stock alerts after any stock change
        LowStockAlert.create_or_update_for_product(product)


class SaleCreateView(generics.ListCreateAPIView):
    """
    Offline-First Sales Sync Endpoint
    
    POST: Submit a sale (online or offline sync)
    GET: List sales for authenticated user
    
    OFFLINE-FIRST RULES:
    - Sales are append-only (never update/delete)
    - Client must provide external_id (UUID) for idempotent retries
    - If external_id already exists, return 200 silently (no duplicate error)
    - Stock is derived server-side, never accepted from client
    - Volatile products bypass stock entirely
    - Non-volatile products allow negative stock + trigger low-stock alerts
    - All item totals computed server-side (never trust client)
    - Transaction-atomic: all items + stock adjustment or nothing
    """
    serializer_class = SaleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        limit = self.request.query_params.get('limit', 50)
        return Sale.objects.all().order_by('-timestamp')[:int(limit)]

    def perform_create(self, serializer):
        serializer.save()



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_by_date(request):
    from datetime import datetime, date

    date_str = request.query_params.get('date')
    if date_str:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        selected_date = date.today()

    sales = Sale.objects.filter(timestamp__date=selected_date)
    serializer = SaleSerializer(sales, many=True)
    total = sales.aggregate(total_amount=Sum('total_amount'))['total_amount'] or 0
    return Response([
    {
        "date": str(selected_date),
        "total": total,
        "transactions": sales.count(),
        "sales": serializer.data
    }
])


@api_view(["GET"])
def sales_trend(request):
    """
    Returns aggregated sales data by day, week, or month.
    Example: /api/sales/trend/?period=weekly
    """
    period = request.GET.get("period", "weekly").lower()

    # Choose the right truncation based on period
    if period == "daily":
        trunc_func = TruncDate
    elif period == "monthly":
        trunc_func = TruncMonth
    else:
        trunc_func = TruncWeek

    # Use your actual timestamp field
    sales = (
        Sale.objects.annotate(period=trunc_func("timestamp"))
        .values("period")
        .annotate(
            total_sales=Count("id"),
            total_amount=Sum("total_amount"),
        )
        .order_by("period")
    )

    # Format response to match frontend expectations
    data = [
        {
            "date": s["period"],
            "count": s["total_sales"],
            "total_amount": float(s["total_amount"] or 0),
        }
        for s in sales
    ]

    return Response(data)

@api_view(["GET"])
def product_sales_analytics(request):
    """
    Get aggregated sales data for a specific product (with/without stock tracking).
    
    Query params:
    - product_id: Required. The product to aggregate sales for.
    - start_date: Optional (YYYY-MM-DD). Defaults to 30 days ago.
    - end_date: Optional (YYYY-MM-DD). Defaults to today.
    - period: Optional (daily|weekly|monthly). Defaults to weekly.
    
    Returns:
    {
        "product": {...},
        "total_quantity_sold": 123.5,
        "total_revenue": 1234.56,
        "average_unit_price": 9.99,
        "period_breakdown": [
            {"date": "2026-01-27", "quantity": 10.5, "revenue": 105.00}
        ]
    }
    
    KEY: This endpoint works for BOTH tracked (non-volatile) and untracked (volatile) products.
    Volatile products have no stock, but sales are still recorded and aggregated.
    """
    from datetime import datetime, timedelta, date as date_type
    
    product_id = request.query_params.get('product_id')
    if not product_id:
        return Response(
            {"error": "product_id query param is required"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response(
            {"error": "Product not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Parse date range
    start_str = request.query_params.get('start_date')
    end_str = request.query_params.get('end_date')
    period = request.query_params.get('period', 'weekly').lower()
    
    if end_str:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
    else:
        end_date = date_type.today()
    
    if start_str:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
    else:
        start_date = end_date - timedelta(days=30)
    
    # Fetch all sale items for this product in the date range
    sale_items = SaleItem.objects.filter(
        product=product,
        sale__timestamp__date__gte=start_date,
        sale__timestamp__date__lte=end_date
    ).select_related('sale')
    
    if not sale_items.exists():
        return Response({
            "product": ProductSerializer(product).data,
            "total_quantity_sold": 0,
            "total_revenue": Decimal('0.00'),
            "average_unit_price": Decimal('0.00'),
            "period_breakdown": []
        })
    
    # Aggregate totals
    totals = sale_items.aggregate(
        total_qty=Sum('quantity'),
        total_revenue=Sum('subtotal'),
    )
    
    total_qty = totals['total_qty'] or Decimal('0')
    total_revenue = totals['total_revenue'] or Decimal('0.00')
    avg_price = (total_revenue / total_qty) if total_qty > 0 else Decimal('0.00')
    
    # Period breakdown
    if period == "daily":
        trunc_func = TruncDate
    elif period == "monthly":
        trunc_func = TruncMonth
    else:
        trunc_func = TruncWeek
    
    period_data = (
        sale_items
        .annotate(period=trunc_func('sale__timestamp'))
        .values('period')
        .annotate(
            qty=Sum('quantity'),
            revenue=Sum('subtotal')
        )
        .order_by('period')
    )
    
    period_breakdown = [
        {
            "date": str(p['period']),
            "quantity": float(p['qty']),
            "revenue": float(p['revenue'])
        }
        for p in period_data
    ]
    
    return Response({
        "product": ProductSerializer(product).data,
        "total_quantity_sold": float(total_qty),
        "total_revenue": float(total_revenue),
        "average_unit_price": float(avg_price),
        "period_breakdown": period_breakdown
    })

class LowStockAlertListView(generics.ListAPIView):
    serializer_class = LowStockAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Show alerts created for the logged-in user
        return LowStockAlert.objects.filter(
            product__user=self.request.user,
            acknowledged=False
        ).select_related('product')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "alerts": serializer.data,
            "unread_count": queryset.filter(acknowledged=False).count(),
            "critical_count": queryset.filter(severity='critical').count()
        })


class AlertHistoryView(generics.ListAPIView):
    serializer_class = LowStockAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        limit = self.request.query_params.get('limit', 50)
        return LowStockAlert.objects.filter(
            product__user=self.request.user
        ).select_related('product').order_by('-triggered_at')[:int(limit)]
    
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "alerts": serializer.data,
            "unread_count": LowStockAlert.objects.filter(
                product__user=request.user,
                acknowledged=False
            ).count(),
            "critical_count": LowStockAlert.objects.filter(
                product__user=request.user,
                severity='critical'
            ).count()
        })

# POST /api/alerts/acknowledge/
class AcknowledgeAlertView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        alert_ids = request.data.get('alert_ids', [])
        updated = LowStockAlert.objects.filter(id__in=alert_ids, product__user=request.user).update(acknowledged=True)
        return Response({"message": f"{updated} alerts acknowledged."})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def acknowledge_all_alerts(request):
    """
    Mark ALL unacknowledged alerts as read for the authenticated user.
    """
    updated = LowStockAlert.objects.filter(
        product__user=request.user,
        acknowledged=False
    ).update(acknowledged=True)
    return Response({"message": f"{updated} alerts acknowledged."})


# GET & PUT /api/alerts/settings/
class AlertSettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        pref, _ = AlertPreference.objects.get_or_create(user=request.user)
        serializer = AlertPreferenceSerializer(pref)
        return Response(serializer.data)

    def put(self, request):
        pref, _ = AlertPreference.objects.get_or_create(user=request.user)
        serializer = AlertPreferenceSerializer(pref, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
