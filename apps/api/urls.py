"""
API URL配置
"""
from django.urls import path
from . import views

urlpatterns = [
    path('orders/', views.api_orders_list, name='api_orders_list'),
    path('skus/', views.api_skus_list, name='api_skus_list'),
    path('parts/', views.api_parts_inventory, name='api_parts_inventory'),
    path('dashboard/stats/', views.api_dashboard_stats, name='api_dashboard_stats'),
]
