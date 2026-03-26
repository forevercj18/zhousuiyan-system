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
    path('orders/export/', views.orders_export, name='orders_export'),
    path('orders/import/', views.orders_import, name='orders_import'),
    path('orders/import-template/', views.orders_import_template, name='orders_import_template'),
    path('reservations/', views.reservations_list, name='reservations_list'),
    path('reservations/create/', views.reservation_create, name='reservation_create'),
    path('reservations/bulk-status/', views.reservations_bulk_update_status, name='reservations_bulk_update_status'),
    path('reservations/bulk-transfer-owner/', views.reservations_bulk_transfer_owner, name='reservations_bulk_transfer_owner'),
    path('reservations/<int:reservation_id>/', views.reservation_detail, name='reservation_detail'),
    path('reservations/<int:reservation_id>/edit/', views.reservation_edit, name='reservation_edit'),
    path('reservations/<int:reservation_id>/cancel/', views.reservation_cancel, name='reservation_cancel'),
    path('reservations/<int:reservation_id>/refund/', views.reservation_refund, name='reservation_refund'),
    path('orders/create/', views.order_create, name='order_create'),
    path('orders/<int:order_id>/edit/', views.order_edit, name='order_edit'),
    path('orders/<int:order_id>/delete/', views.order_delete, name='order_delete'),
    path('orders/bulk-delete/', views.orders_bulk_delete, name='orders_bulk_delete'),
    path('orders/<int:order_id>/confirm/', views.order_mark_confirmed, name='order_mark_confirmed'),
    path('orders/<int:order_id>/mark-delivered/', views.order_mark_delivered, name='order_mark_delivered'),
    path('orders/<int:order_id>/mark-returned/', views.order_mark_returned, name='order_mark_returned'),
    path('orders/<int:order_id>/mark-completed/', views.order_mark_completed, name='order_mark_completed'),
    path('orders/<int:order_id>/cancel/', views.order_cancel, name='order_cancel'),
    path('orders/<int:order_id>/finance/add/', views.order_finance_add, name='order_finance_add'),
    path('orders/<int:order_id>/return-service/update/', views.order_return_service_update, name='order_return_service_update'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),

    # 历史日历入口兼容跳转
    path('calendar/', views.calendar_view, name='calendar'),

    # 转寄中心
    path('transfers/', views.transfers_list, name='transfers_list'),
    path('transfers/recommendation-logs/', views.transfer_recommendation_logs, name='transfer_recommendation_logs'),
    path('transfers/recommend/', views.transfer_recommend, name='transfer_recommend'),
    path('transfers/generate-tasks/', views.transfer_generate_tasks, name='transfer_generate_tasks'),
    path('transfers/create/', views.transfer_create, name='transfer_create'),
    path('transfers/<int:transfer_id>/complete/', views.transfer_complete, name='transfer_complete'),
    path('transfers/<int:transfer_id>/cancel/', views.transfer_cancel, name='transfer_cancel'),

    # 在外库存看板
    path('outbound-inventory/', views.outbound_inventory_dashboard, name='outbound_inventory_dashboard'),
    path('outbound-inventory/part-issues/', views.part_issue_pool, name='part_issue_pool'),
    path('outbound-inventory/maintenance/', views.maintenance_work_orders_list, name='maintenance_work_orders_list'),
    path('outbound-inventory/maintenance/export/', views.maintenance_work_orders_export, name='maintenance_work_orders_export'),
    path('outbound-inventory/disposals/', views.unit_disposal_orders_list, name='unit_disposal_orders_list'),
    path('outbound-inventory/disposals/export/', views.unit_disposal_orders_export, name='unit_disposal_orders_export'),
    path('outbound-inventory/export/', views.outbound_inventory_export, name='outbound_inventory_export'),
    path('outbound-inventory/export-topology/', views.outbound_inventory_topology_export, name='outbound_inventory_topology_export'),
    path('outbound-inventory/units/<int:unit_id>/parts/update/', views.outbound_inventory_unit_parts_update, name='outbound_inventory_unit_parts_update'),
    path('outbound-inventory/units/<int:unit_id>/dispose/', views.unit_disposal_create, name='unit_disposal_create'),
    path('outbound-inventory/units/<int:unit_id>/maintenance/create/', views.maintenance_work_order_create, name='maintenance_work_order_create'),
    path('outbound-inventory/maintenance/<int:work_order_id>/complete/', views.maintenance_work_order_complete, name='maintenance_work_order_complete'),
    path('outbound-inventory/maintenance/<int:work_order_id>/cancel/', views.maintenance_work_order_cancel, name='maintenance_work_order_cancel'),
    path('outbound-inventory/maintenance/<int:work_order_id>/reverse/', views.maintenance_work_order_reverse, name='maintenance_work_order_reverse'),

    # SKU管理
    path('skus/', views.skus_list, name='skus_list'),
    path('skus/assembly-orders/', views.assembly_orders_list, name='assembly_orders_list'),
    path('skus/assembly-orders/export/', views.assembly_orders_export, name='assembly_orders_export'),
    path('skus/assembly-orders/<int:assembly_id>/cancel/', views.assembly_order_cancel, name='assembly_order_cancel'),
    path('skus/upload-token/', views.sku_upload_token, name='sku_upload_token'),
    path('skus/create/', views.sku_create, name='sku_create'),
    path('skus/<int:sku_id>/edit/', views.sku_edit, name='sku_edit'),
    path('skus/<int:sku_id>/assemble/', views.sku_assemble, name='sku_assemble'),
    path('skus/<int:sku_id>/delete/', views.sku_delete, name='sku_delete'),
    path('skus/bulk-delete/', views.skus_bulk_delete, name='skus_bulk_delete'),

    # 采购管理
    path('procurement/purchase-orders/', views.purchase_orders_list, name='purchase_orders_list'),
    path('procurement/purchase-orders/create/', views.purchase_order_create, name='purchase_order_create'),
    path('procurement/purchase-orders/<int:po_id>/edit/', views.purchase_order_edit, name='purchase_order_edit'),
    path('procurement/purchase-orders/<int:po_id>/delete/', views.purchase_order_delete, name='purchase_order_delete'),
    path('procurement/purchase-orders/bulk-delete/', views.purchase_orders_bulk_delete, name='purchase_orders_bulk_delete'),
    path('procurement/purchase-orders/<int:po_id>/mark-ordered/', views.purchase_order_mark_ordered, name='purchase_order_mark_ordered'),
    path('procurement/purchase-orders/<int:po_id>/mark-arrived/', views.purchase_order_mark_arrived, name='purchase_order_mark_arrived'),
    path('procurement/purchase-orders/<int:po_id>/mark-stocked/', views.purchase_order_mark_stocked, name='purchase_order_mark_stocked'),
    path('procurement/parts-inventory/', views.parts_inventory_list, name='parts_inventory_list'),
    path('procurement/part-recovery-inspections/', views.part_recovery_inspections_list, name='part_recovery_inspections_list'),
    path('procurement/part-recovery-inspections/export/', views.part_recovery_inspections_export, name='part_recovery_inspections_export'),
    path('procurement/part-recovery-inspections/<int:inspection_id>/process/', views.part_recovery_inspection_process, name='part_recovery_inspection_process'),
    path('procurement/warehouse-reports/', views.warehouse_reports, name='warehouse_reports'),
    path('procurement/warehouse-reports/export/', views.warehouse_reports_export, name='warehouse_reports_export'),
    path('procurement/parts/create/', views.part_create, name='part_create'),
    path('procurement/parts/<int:part_id>/edit/', views.part_edit, name='part_edit'),
    path('procurement/parts/<int:part_id>/delete/', views.part_delete, name='part_delete'),
    path('procurement/parts/bulk-delete/', views.parts_bulk_delete, name='parts_bulk_delete'),
    path('procurement/parts/inbound/', views.part_inbound, name='part_inbound'),
    path('procurement/parts/outbound/', views.part_outbound, name='part_outbound'),
    path('procurement/parts-movements/', views.parts_movements_list, name='parts_movements_list'),

    # 系统设置
    path('settings/', views.settings_view, name='settings'),
    path('finance-transactions/', views.finance_transactions_list, name='finance_transactions_list'),
    path('finance-reconciliation/', views.finance_reconciliation, name='finance_reconciliation'),
    path('finance-reconciliation/<int:order_id>/raise-risk/', views.finance_reconciliation_raise_risk, name='finance_reconciliation_raise_risk'),
    path('ops-center/', views.ops_center, name='ops_center'),
    path('approvals/', views.approvals_list, name='approvals_list'),
    path('approvals/remind-overdue/', views.approval_remind_overdue, name='approval_remind_overdue'),
    path('approvals/<int:task_id>/approve/', views.approval_task_approve, name='approval_task_approve'),
    path('approvals/<int:task_id>/reject/', views.approval_task_reject, name='approval_task_reject'),
    path('approvals/<int:task_id>/remind/', views.approval_task_remind, name='approval_task_remind'),
    path('risk-events/', views.risk_events_list, name='risk_events_list'),
    path('risk-events/<int:event_id>/claim/', views.risk_event_claim, name='risk_event_claim'),
    path('risk-events/<int:event_id>/resolve/', views.risk_event_resolve, name='risk_event_resolve'),
    path('audit-logs/', views.audit_logs, name='audit_logs'),

    # 用户管理
    path('users/', views.users_list, name='users_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:user_id>/toggle-status/', views.user_toggle_status, name='user_toggle_status'),
    path('users/permission-templates/create/', views.permission_template_create, name='permission_template_create'),
    path('users/permission-templates/<int:template_id>/edit/', views.permission_template_edit, name='permission_template_edit'),
    path('users/permission-templates/<int:template_id>/delete/', views.permission_template_delete, name='permission_template_delete'),

    # API接口
    path('api/sku/<int:sku_id>/', views.api_get_sku_details, name='api_get_sku_details'),
    path('api/check-availability/', views.api_check_availability, name='api_check_availability'),
    path('api/transfer-match/', views.api_transfer_match, name='api_transfer_match'),
    path('api/check-duplicate-order/', views.api_check_duplicate_order, name='api_check_duplicate_order'),
]
