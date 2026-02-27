"""
核心模块URL配置
"""
from django.urls import path
from . import views

urlpatterns = [
    # 认证
    path('', views.login_view, name='login'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # 工作台
    path('dashboard/', views.dashboard, name='dashboard'),
    path('workbench/', views.workbench, name='workbench'),

    # 订单管理
    path('orders/', views.orders_list, name='orders_list'),
    path('orders/create/', views.order_create, name='order_create'),
    path('orders/<int:order_id>/edit/', views.order_edit, name='order_edit'),
    path('orders/<int:order_id>/delete/', views.order_delete, name='order_delete'),
    path('orders/<int:order_id>/confirm/', views.order_mark_confirmed, name='order_mark_confirmed'),
    path('orders/<int:order_id>/mark-delivered/', views.order_mark_delivered, name='order_mark_delivered'),
    path('orders/<int:order_id>/mark-returned/', views.order_mark_returned, name='order_mark_returned'),
    path('orders/<int:order_id>/mark-completed/', views.order_mark_completed, name='order_mark_completed'),
    path('orders/<int:order_id>/cancel/', views.order_cancel, name='order_cancel'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),

    # 日历排期
    path('calendar/', views.calendar_view, name='calendar'),

    # 出入库流水
    path('transfers/', views.transfers_list, name='transfers_list'),
    path('transfers/create/', views.transfer_create, name='transfer_create'),
    path('transfers/<int:transfer_id>/complete/', views.transfer_complete, name='transfer_complete'),
    path('transfers/<int:transfer_id>/cancel/', views.transfer_cancel, name='transfer_cancel'),

    # SKU管理
    path('skus/', views.skus_list, name='skus_list'),
    path('skus/create/', views.sku_create, name='sku_create'),
    path('skus/<int:sku_id>/edit/', views.sku_edit, name='sku_edit'),
    path('skus/<int:sku_id>/delete/', views.sku_delete, name='sku_delete'),

    # 采购管理
    path('procurement/purchase-orders/', views.purchase_orders_list, name='purchase_orders_list'),
    path('procurement/purchase-orders/create/', views.purchase_order_create, name='purchase_order_create'),
    path('procurement/purchase-orders/<int:po_id>/edit/', views.purchase_order_edit, name='purchase_order_edit'),
    path('procurement/purchase-orders/<int:po_id>/delete/', views.purchase_order_delete, name='purchase_order_delete'),
    path('procurement/purchase-orders/<int:po_id>/mark-ordered/', views.purchase_order_mark_ordered, name='purchase_order_mark_ordered'),
    path('procurement/purchase-orders/<int:po_id>/mark-arrived/', views.purchase_order_mark_arrived, name='purchase_order_mark_arrived'),
    path('procurement/purchase-orders/<int:po_id>/mark-stocked/', views.purchase_order_mark_stocked, name='purchase_order_mark_stocked'),
    path('procurement/parts-inventory/', views.parts_inventory_list, name='parts_inventory_list'),
    path('procurement/parts/create/', views.part_create, name='part_create'),
    path('procurement/parts/<int:part_id>/edit/', views.part_edit, name='part_edit'),
    path('procurement/parts/<int:part_id>/delete/', views.part_delete, name='part_delete'),
    path('procurement/parts/inbound/', views.part_inbound, name='part_inbound'),
    path('procurement/parts/outbound/', views.part_outbound, name='part_outbound'),
    path('procurement/parts-movements/', views.parts_movements_list, name='parts_movements_list'),

    # 系统设置
    path('settings/', views.settings_view, name='settings'),
    path('audit-logs/', views.audit_logs, name='audit_logs'),

    # 用户管理
    path('users/', views.users_list, name='users_list'),

    # API接口
    path('api/sku/<int:sku_id>/', views.api_get_sku_details, name='api_get_sku_details'),
    path('api/check-availability/', views.api_check_availability, name='api_check_availability'),
]
