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
        'modules': ['dashboard', 'orders', 'reservations', 'transfers', 'outbound_inventory', 'audit_logs', 'risk_events', 'approvals', 'finance', 'ops_center'],
        'actions': ['view', 'create', 'update'],
    },
    'warehouse_manager': {
        'modules': ['workbench', 'orders', 'reservations', 'skus', 'procurement', 'parts', 'transfers', 'outbound_inventory', 'audit_logs', 'risk_events', 'approvals', 'finance', 'ops_center'],
        'actions': ['view', 'create', 'update', 'delete'],
    },
    'warehouse_staff': {
        'modules': ['workbench', 'skus', 'parts', 'transfers', 'outbound_inventory', 'finance'],
        'actions': ['view', 'update'],
    },
    'customer_service': {
        'modules': ['orders', 'reservations'],
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


PERMISSION_MODULE_LABELS = {
    'dashboard': '工作台',
    'workbench': '业务工作台',
    'orders': '订单中心',
    'reservations': '预定管理',
    'transfers': '转寄中心',
    'outbound_inventory': '在外库存看板',
    'skus': '产品管理',
    'procurement': '采购单',
    'parts': '部件库存与流水',
    'audit_logs': '审计日志',
    'finance': '财务流水',
    'ops_center': '运维中心',
    'risk_events': '风险事件',
    'approvals': '审批中心',
    'users': '用户管理',
    'settings': '系统设置',
}

PERMISSION_ACTION_LABELS = {
    'view': '查看',
    'create': '新增',
    'update': '编辑',
    'delete': '删除',
}

ACTION_PERMISSION_LABELS = {
    'order.confirm_delivery': '订单确认/发货',
    'order.mark_returned': '订单回库',
    'order.change_amount': '手工改价',
    'order.force_cancel': '订单强制取消',
    'transfer.recommend': '转寄重新推荐',
    'transfer.create_task': '生成转寄任务',
    'transfer.complete_task': '完成转寄任务',
    'transfer.cancel_task': '取消转寄任务',
    'inventory.export_topology': '导出库存拓扑',
    'inventory.init_units': '初始化单套库存',
    'unit.dispose': '单套处置',
    'sku.upload_image': '上传产品图片',
    'parts.adjust_stock': '调整部件库存',
    'risk.resolve_event': '处理风险事件',
    'approval.review': '审批处理',
    'finance.manual_adjust': '财务手工调整',
}

ROLE_DATA_SCOPE_DESCRIPTIONS = {
    'admin': [
        '可查看和处理全系统数据',
        '不受订单、库存、审批范围限制',
    ],
    'manager': [
        '订单类数据默认可查看全部',
        '审批、风险、财务等模块按已授权功能访问',
    ],
    'warehouse_manager': [
        '订单、SKU、部件、采购单默认可查看全部',
        '审批中心默认只能处理自己发起的任务',
    ],
    'warehouse_staff': [
        '订单中心仅能看到已确认/已发货订单',
        '其余模块以页面权限和业务动作权限为准',
    ],
    'customer_service': [
        '订单中心仅能看到自己创建的订单',
        '其余模块以页面权限和业务动作权限为准',
    ],
}


def get_user_permission_config(user):
    """获取用户当前生效的权限配置"""
    role = getattr(user, 'role', '')
    if getattr(user, 'permission_mode', 'role') == 'custom':
        return {
            'modules': list(getattr(user, 'custom_modules', []) or []),
            'actions': list(getattr(user, 'custom_actions', []) or []),
            'action_permissions': list(getattr(user, 'custom_action_permissions', []) or []),
        }
    return {
        'modules': list(ROLE_PERMISSIONS.get(role, {}).get('modules', [])),
        'actions': list(ROLE_PERMISSIONS.get(role, {}).get('actions', [])),
        'action_permissions': list(ROLE_ACTION_PERMISSIONS.get(role, [])),
    }


def get_role_permission_config(role):
    return {
        'modules': list(ROLE_PERMISSIONS.get(role, {}).get('modules', [])),
        'actions': list(ROLE_PERMISSIONS.get(role, {}).get('actions', [])),
        'action_permissions': list(ROLE_ACTION_PERMISSIONS.get(role, [])),
    }


def _build_permission_diff(current_items, baseline_items, label_map):
    current_set = set(current_items or [])
    baseline_set = set(baseline_items or [])
    if '*' in current_set or '*' in baseline_set:
        return {
            'added': [],
            'removed': [],
            'summary': '包含全量权限，差异无需逐项列出',
        }
    return {
        'added': [label_map.get(code, code) for code in sorted(current_set - baseline_set)],
        'removed': [label_map.get(code, code) for code in sorted(baseline_set - current_set)],
        'summary': '',
    }


def get_user_data_scope_descriptions(user):
    if not getattr(user, 'is_authenticated', False):
        return []
    if user.is_superuser:
        return [
            '超级用户：可查看和处理全系统数据',
            '不受模块、动作和数据范围限制',
        ]
    scopes = list(ROLE_DATA_SCOPE_DESCRIPTIONS.get(getattr(user, 'role', ''), []))
    if getattr(user, 'permission_mode', 'role') == 'custom':
        scopes.insert(0, '当前为自定义搭配权限；数据范围仍按基础角色模板生效')
    return scopes


def get_user_permission_preview(user):
    config = get_user_permission_config(user)
    baseline_config = get_role_permission_config(getattr(user, 'role', ''))
    menus = get_user_menu(user)
    menu_titles = []
    for menu in menus:
        if 'children' in menu:
            menu_titles.extend(child['title'] for child in menu['children'])
        else:
            menu_titles.append(menu['title'])
    modules_diff = _build_permission_diff(
        config.get('modules', []),
        baseline_config.get('modules', []),
        PERMISSION_MODULE_LABELS,
    )
    actions_diff = _build_permission_diff(
        config.get('actions', []),
        baseline_config.get('actions', []),
        PERMISSION_ACTION_LABELS,
    )
    action_permissions_diff = _build_permission_diff(
        config.get('action_permissions', []),
        baseline_config.get('action_permissions', []),
        ACTION_PERMISSION_LABELS,
    )
    return {
        'profile': getattr(user, 'permission_profile_display', ''),
        'baseline_role': getattr(user, 'get_role_display', lambda: '')(),
        'modules': [
            PERMISSION_MODULE_LABELS.get(code, code)
            for code in config.get('modules', [])
        ],
        'actions': [
            PERMISSION_ACTION_LABELS.get(code, code)
            for code in config.get('actions', [])
        ],
        'action_permissions': [
            ACTION_PERMISSION_LABELS.get(code, code)
            for code in config.get('action_permissions', [])
        ],
        'menus': menu_titles,
        'data_scopes': get_user_data_scope_descriptions(user),
        'diffs': {
            'modules': modules_diff,
            'actions': actions_diff,
            'action_permissions': action_permissions_diff,
        },
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

    permissions = get_user_permission_config(user)
    modules = permissions.get('modules', [])
    if '*' in modules or module in modules:
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

    actions = get_user_permission_config(user).get('action_permissions', [])
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
                {'name': 'reservations', 'title': '预定管理', 'url': 'reservations_list'},
            ]
        },
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
