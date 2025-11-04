from django.urls import path
from .views import register_user, login_user, logout_user, ProductListCreateView, StockMovementCreateView, SaleCreateView, sales_by_date, import_csv, sales_trend, ProductRetrieveUpdateDestroyView, LowStockAlertListView, AlertHistoryView, AcknowledgeAlertView, AlertSettingsView

urlpatterns = [
    path('register/', register_user, name='register'),
    path('login/', login_user, name='login'),
    path('logout/', logout_user, name='logout'),
    path('products/', ProductListCreateView.as_view(), name='product-list-create'),
    path('products/<int:pk>/', ProductRetrieveUpdateDestroyView.as_view(), name='product-detail'),
    path('stock-movements/', StockMovementCreateView.as_view(), name='stock-movement-create'),
    path('sales/', SaleCreateView.as_view(), name='sales'),
    path('sales/by_date/', sales_by_date, name='sales-by-date'),
    path('products/import_csv/', import_csv, name='import_csv'),
    path('sales/trend/', sales_trend, name='sales_trend'),
    path("alerts/low-stock/", LowStockAlertListView.as_view(), name="low_stock_alerts"),
    path('alerts/history/', AlertHistoryView.as_view(), name='alert-history'),
    path('alerts/acknowledge/', AcknowledgeAlertView.as_view(), name='alert-acknowledge'),
    path('alerts/settings/', AlertSettingsView.as_view(), name='alert-settings'),
]

