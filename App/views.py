from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from .serializers import RegisterSerializer, LoginSerializer, ProductSerializer, StockMovementSerializer, SaleSerializer, LowStockAlertSerializer, AlertSerializer, AlertPreferenceSerializer
from .models import Product, StockMovement, Sale, LowStockAlert, Alert, AlertPreference
from rest_framework import generics, permissions, status
from django.db import transaction
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.db.models import Sum, Count, Q
from rest_framework.parsers import MultiPartParser
from .utils.csv_importer import import_products_from_csv


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
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
    if serializer.is_valid():
        serializer.save()
        return Response({'message': 'User registered successfully'}, status=201)
    return Response(serializer.errors, status=400)


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
        # Show only products owned by this user
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
    serializer_class = SaleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Sale.objects.filter(sold_by=self.request.user)

    @transaction.atomic
    def perform_create(self, serializer):
        sale = serializer.save(sold_by=self.request.user)

        for item in sale.items.select_related('product'):
            product = item.product

            if not product.is_volatile:
                product.adjust_stock(
                    qty_delta=-item.quantity,
                    by_user=self.request.user,
                    reason="sale",
                    movement_type="SALE"
                )

    

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_by_date(request):
    from datetime import datetime, date

    date_str = request.query_params.get('date')
    if date_str:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        selected_date = date.today()

    sales = Sale.objects.filter(timestamp__date=selected_date, sold_by=request.user)
    serializer = SaleSerializer(sales, many=True)

    return Response({
        "date": str(selected_date),
        "total_sales": sum(s.total_amount for s in sales),
        "transactions": sales.count(),
        "sales": serializer.data
    })


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

class LowStockAlertListView(generics.ListAPIView):
    serializer_class = AlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Show alerts created for the logged-in user
        return Alert.objects.filter(
            created_for=self.request.user,
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
    serializer_class = AlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Alert.objects.filter(created_for=self.request.user).order_by('-triggered_at')
    
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
        updated = Alert.objects.filter(id__in=alert_ids, created_for=request.user).update(acknowledged=True)
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