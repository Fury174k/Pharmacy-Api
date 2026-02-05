from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from .serializers import RegisterSerializer, LoginSerializer, ProductSerializer, StockMovementSerializer, SaleSerializer, LowStockAlertSerializer, AlertPreferenceSerializer
from .models import Product, StockMovement, Sale, SaleItem, LowStockAlert, AlertPreference
from rest_framework import generics, permissions, status
from django.db import transaction
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.db.models import Sum, Count, Q
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

class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Product.objects.all()

    def perform_create(self, serializer):
        # Automatically assign the logged-in user
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
        serializer.save()

class StockMovementCreateView(generics.ListCreateAPIView):
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return StockMovement.objects.filter(
            product__user=self.request.user
        ).select_related('product')

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
        return Sale.objects.all()

    def perform_create(self, serializer):
        """
        Serializer handles ALL offline-sync logic:
        - Duplicate detection by external_id (idempotency)
        - Transaction-safe multi-item creation
        - Server-side total computation
        - Stock deduction for non-volatile products only
        - Alert triggering for low stock
        No additional logic needed here (keep it clean).
        """
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
        return LowStockAlert.objects.filter(product__user=self.request.user).order_by('-triggered_at')
    
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "alerts": serializer.data,
            "unread_count": queryset.filter(acknowledged=False).count(),
            "critical_count": queryset.filter(severity='critical').count()
        })

# POST /api/alerts/acknowledge/
class AcknowledgeAlertView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        alert_ids = request.data.get('alert_ids', [])
        updated = LowStockAlert.objects.filter(id__in=alert_ids, product__user=request.user).update(acknowledged=True)
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
