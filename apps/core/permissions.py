"""
权限控制装饰器和工具函数
基于角色的访问控制（RBAC）
"""
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.http import JsonResponse


# 角色权限矩阵
ROLE_PERMISSIONS = {
    'admin': {
        'modules': ['*'],  # 所有模块
        'actions': ['*'],  # 所有操作
    },
    'manager': {
        'modules': ['dashboard', 'orders', 'calendar', 'transfers', 'outbound_inventory', 'audit_logs', 'risk_events', 'approvals', 'finance', 'ops_center'],
        'actions': ['view', 'create', 'update'],
    },
    'warehouse_manager': {
        'modules': ['workbench', 'orders', 'skus', 'procurement', 'parts', 'transfers', 'outbound_inventory', 'audit_logs', 'risk_events', 'approvals', 'finance', 'ops_center'],
        'actions': ['view', 'create', 'update', 'delete'],
    },
    'warehouse_staff': {
        'modules': ['workbench', 'skus', 'parts', 'transfers', 'outbound_inventory', 'finance'],
        'actions': ['view', 'update'],
    },
    'customer_service': {
        'modules': ['orders', 'calendar'],
        'actions': ['view', 'create', 'update'],
    },
}


ROLE_ACTION_PERMISSIONS = {
    'admin': ['*'],
    'manager': [
        'order.confirm_delivery',
        'order.mark_returned',
        'order.change_amount',
        'transfer.recommend',
        'transfer.create_task',
        'transfer.complete_task',
        'inventory.export_topology',
        'risk.resolve_event',
        'approval.review',
        'finance.manual_adjust',
    ],
    'warehouse_manager': [
        'order.confirm_delivery',
        'order.mark_returned',
        'transfer.recommend',
        'transfer.create_task',
        'transfer.complete_task',
        'unit.dispose',
        'inventory.init_units',
        'inventory.export_topology',
        'sku.upload_image',
        'parts.adjust_stock',
        'risk.resolve_event',
        'finance.manual_adjust',
    ],
    'warehouse_staff': [
        'order.confirm_delivery',
        'order.mark_returned',
        'transfer.recommend',
        'transfer.create_task',
        'transfer.complete_task',
        'inventory.export_topology',
        'sku.upload_image',
        'finance.manual_adjust',
    ],
    'customer_service': [],
}


def has_permission(user, module, action='view'):
    """
    检查用户是否有权限

    Args:
        user: 用户对象
        module: 模块名称
        action: 操作类型 (view/create/update/delete)

    Returns:
        bool: 是否有权限
    """
    if not user.is_authenticated:
        return False

    # 超级用户拥有所有权限
    if user.is_superuser:
        return True

    role = user.role
    permissions = ROLE_PERMISSIONS.get(role, {})

    # 检查模块权限
    modules = permissions.get('modules', [])
    if '*' in modules or module in modules:
        # 检查操作权限
        actions = permissions.get('actions', [])
        if '*' in actions or action in actions:
            return True

    return False


def has_action_permission(user, action_code):
    """检查用户是否具备业务动作级权限"""
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    role = user.role
    actions = ROLE_ACTION_PERMISSIONS.get(role, [])
    return '*' in actions or action_code in actions


def can_request_approval(user):
    """是否允许提交审批单（执行权不足时）"""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role in ['manager', 'warehouse_manager']


def require_permission(module, action='view'):
    """
    权限装饰器（用于视图函数）

    Usage:
        @require_permission('orders', 'create')
        def create_order(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')

            if not has_permission(request.user, module, action):
                messages.error(request, f'您没有权限访问此功能（需要：{module} - {action}）')
                return redirect('dashboard')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_permission_api(module, action='view'):
    """
    权限装饰器（用于API视图）

    Usage:
        @require_permission_api('orders', 'create')
        def api_create_order(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({
                    'success': False,
                    'message': '未登录'
                }, status=401)

            if not has_permission(request.user, module, action):
                return JsonResponse({
                    'success': False,
                    'message': f'没有权限（需要：{module} - {action}）'
                }, status=403)

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def get_user_menu(user):
    """
    获取用户可访问的菜单

    Args:
        user: 用户对象

    Returns:
        list: 菜单列表
    """
    if not user.is_authenticated:
        return []

    all_menus = [
        {'name': 'dashboard', 'title': '仪表盘', 'url': 'dashboard', 'icon': '📊'},
        {'name': 'workbench', 'title': '工作台', 'url': 'workbench', 'icon': '💼'},
        {
            'name': 'orders',
            'title': '订单管理',
            'icon': '📦',
            'children': [
                {'name': 'orders', 'title': '订单列表', 'url': 'orders_list'},
                {'name': 'orders', 'title': '新建订单', 'url': 'order_create'},
            ]
        },
        {'name': 'calendar', 'title': '排期看板', 'url': 'calendar', 'icon': '📅'},
        {'name': 'transfers', 'title': '转寄中心', 'url': 'transfers_list', 'icon': '🔄'},
        {'name': 'outbound_inventory', 'title': '在外库存看板', 'url': 'outbound_inventory_dashboard', 'icon': '🧭'},
        {'name': 'skus', 'title': 'SKU管理', 'url': 'skus_list', 'icon': '📋'},
        {
            'name': 'procurement',
            'title': '采购与备件',
            'icon': '🛒',
            'children': [
                {'name': 'procurement', 'title': '采购订单', 'url': 'purchase_orders_list'},
                {'name': 'parts', 'title': '部件库存', 'url': 'parts_inventory_list'},
                {'name': 'parts', 'title': '出入库流水', 'url': 'parts_movements_list'},
            ]
        },
        {'name': 'audit_logs', 'title': '操作日志', 'url': 'audit_logs', 'icon': '📜'},
        {'name': 'finance', 'title': '财务流水', 'url': 'finance_transactions_list', 'icon': '💰'},
        {'name': 'ops_center', 'title': '运维中心', 'url': 'ops_center', 'icon': '🧰'},
        {'name': 'risk_events', 'title': '风险事件', 'url': 'risk_events_list', 'icon': '⚠️'},
        {'name': 'approvals', 'title': '审批中心', 'url': 'approvals_list', 'icon': '✅'},
        {'name': 'users', 'title': '用户管理', 'url': 'users_list', 'icon': '👥'},
        {'name': 'settings', 'title': '系统设置', 'url': 'settings', 'icon': '⚙️'},
    ]

    # 过滤用户有权限的菜单
    filtered_menus = []
    for menu in all_menus:
        if 'children' in menu:
            # 有子菜单
            filtered_children = [
                child for child in menu['children']
                if has_permission(user, child['name'], 'view')
            ]
            if filtered_children:
                menu_copy = menu.copy()
                menu_copy['children'] = filtered_children
                filtered_menus.append(menu_copy)
        else:
            # 无子菜单
            if has_permission(user, menu['name'], 'view'):
                filtered_menus.append(menu)

    return filtered_menus


def filter_queryset_by_permission(queryset, user, model_name):
    """
    根据用户权限过滤查询集

    Args:
        queryset: 查询集
        user: 用户对象
        model_name: 模型名称

    Returns:
        QuerySet: 过滤后的查询集
    """
    # 超级用户和管理员可以看到所有数据
    if user.is_superuser or user.role == 'admin':
        return queryset

    # 业务经理可以看到所有订单
    if user.role == 'manager' and model_name == 'Order':
        return queryset

    # 仓库主管可以看到所有仓库相关数据
    if user.role == 'warehouse_manager' and model_name in ['Order', 'SKU', 'Part', 'PurchaseOrder']:
        return queryset

    # 仓库操作员只能看到待处理的订单
    if user.role == 'warehouse_staff' and model_name == 'Order':
        return queryset.filter(status__in=['confirmed', 'delivered'])

    # 客服只能看到自己创建的订单
    if user.role == 'customer_service' and model_name == 'Order':
        return queryset.filter(created_by=user)

    return queryset
