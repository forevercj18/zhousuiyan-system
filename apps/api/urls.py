"""
API URL配置
"""
from django.urls import path
from . import views

urlpatterns = [
    path('orders/', views.api_orders_list, name='api_orders_list'),
    path('orders/<int:order_id>/finance-transactions/', views.api_order_finance_transactions, name='api_order_finance_transactions'),
    path('finance/reconciliation/', views.api_finance_reconciliation, name='api_finance_reconciliation'),
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
    path('dashboard/role-view/', views.api_dashboard_role_view, name='api_dashboard_role_view'),
    path('dashboard/kpi-trend/', views.api_dashboard_kpi_trend, name='api_dashboard_kpi_trend'),
    path('dashboard/ops-alerts/', views.api_ops_alerts, name='api_ops_alerts'),
    path('risk-events/', views.api_risk_events, name='api_risk_events'),
    path('approvals/', views.api_approvals, name='api_approvals'),
    path('transfers/recommendation-logs/', views.api_transfer_recommendation_logs, name='api_transfer_recommendation_logs'),
    path('transfers/recommendation-logs/<int:log_id>/', views.api_transfer_recommendation_log_detail, name='api_transfer_recommendation_log_detail'),
]
