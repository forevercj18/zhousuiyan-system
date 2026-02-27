"""
API URL配置
"""
from django.urls import path
from . import views

urlpatterns = [
    path('orders/', views.api_orders_list, name='api_orders_list'),
    path('orders/<int:order_id>/confirm/', views.api_order_confirm, name='api_order_confirm'),
    path('orders/<int:order_id>/deliver/', views.api_order_mark_delivered, name='api_order_mark_delivered'),
    path('orders/<int:order_id>/return/', views.api_order_mark_returned, name='api_order_mark_returned'),
    path('orders/<int:order_id>/complete/', views.api_order_complete, name='api_order_complete'),
    path('skus/', views.api_skus_list, name='api_skus_list'),
    path('parts/', views.api_parts_inventory, name='api_parts_inventory'),
    path('purchase-orders/', views.api_purchase_orders, name='api_purchase_orders'),
    path('purchase-orders/<int:po_id>/ordered/', views.api_purchase_order_mark_ordered, name='api_purchase_order_mark_ordered'),
    path('purchase-orders/<int:po_id>/arrived/', views.api_purchase_order_mark_arrived, name='api_purchase_order_mark_arrived'),
    path('purchase-orders/<int:po_id>/stocked/', views.api_purchase_order_mark_stocked, name='api_purchase_order_mark_stocked'),
    path('transfers/', views.api_transfers, name='api_transfers'),
    path('transfers/create/', views.api_transfer_create, name='api_transfer_create'),
    path('dashboard/stats/', views.api_dashboard_stats, name='api_dashboard_stats'),
]
