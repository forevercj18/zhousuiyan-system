"""
核心视图模块 - 处理所有前端页面请求
"""
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from datetime import datetime, timedelta
from . import mock_data


def login_view(request):
    """登录页面"""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, '用户名或密码错误')

    return render(request, 'login.html')


def logout_view(request):
    """登出"""
    logout(request)
    return redirect('login')


@login_required
def dashboard(request):
    """工作台首页"""
    stats = mock_data.get_dashboard_stats()
    recent_orders = mock_data.ORDERS[:5]
    low_stock_parts = [p for p in mock_data.PARTS_INVENTORY if p['available'] < p['min_stock']]

    context = {
        'stats': stats,
        'recent_orders': recent_orders,
        'low_stock_parts': low_stock_parts,
    }
    return render(request, 'dashboard.html', context)


@login_required
def workbench(request):
    """工作台 - 订单处理中心"""
    pending_orders = [o for o in mock_data.ORDERS if o['status'] == 'pending']
    confirmed_orders = [o for o in mock_data.ORDERS if o['status'] == 'confirmed']
    delivered_orders = [o for o in mock_data.ORDERS if o['status'] == 'delivered']

    context = {
        'pending_orders': pending_orders,
        'confirmed_orders': confirmed_orders,
        'delivered_orders': delivered_orders,
    }
    return render(request, 'workbench.html', context)


@login_required
def orders_list(request):
    """订单列表"""
    status_filter = request.GET.get('status', '')
    orders = mock_data.ORDERS

    if status_filter:
        orders = [o for o in orders if o['status'] == status_filter]

    context = {
        'orders': orders,
        'status_filter': status_filter,
    }
    return render(request, 'orders/list.html', context)


@login_required
def order_create(request):
    """创建订单"""
    if request.method == 'POST':
        messages.success(request, '订单创建成功')
        return redirect('orders_list')

    context = {
        'skus': mock_data.SKUS,
        'mode': 'create',
    }
    return render(request, 'orders/form.html', context)


@login_required
def order_edit(request, order_id):
    """编辑订单"""
    order = next((o for o in mock_data.ORDERS if o['id'] == order_id), None)

    if not order:
        messages.error(request, '订单不存在')
        return redirect('orders_list')

    if request.method == 'POST':
        messages.success(request, '订单更新成功')
        return redirect('orders_list')

    context = {
        'order': order,
        'skus': mock_data.SKUS,
        'mode': 'edit',
    }
    return render(request, 'orders/form.html', context)


@login_required
def order_detail(request, order_id):
    """订单详情"""
    order = next((o for o in mock_data.ORDERS if o['id'] == order_id), None)

    if not order:
        messages.error(request, '订单不存在')
        return redirect('orders_list')

    context = {
        'order': order,
        'mode': 'view',
    }
    return render(request, 'orders/form.html', context)


@login_required
def calendar_view(request):
    """日历排期视图"""
    events = mock_data.get_calendar_events()

    context = {
        'events': events,
    }
    return render(request, 'calendar.html', context)


@login_required
def transfers_list(request):
    """出入库流水"""
    movement_type = request.GET.get('type', '')
    movements = mock_data.PARTS_MOVEMENTS

    if movement_type:
        movements = [m for m in movements if m['type'] == movement_type]

    context = {
        'movements': movements,
        'movement_type': movement_type,
    }
    return render(request, 'transfers.html', context)


@login_required
def skus_list(request):
    """SKU管理"""
    category_filter = request.GET.get('category', '')
    skus = mock_data.SKUS

    if category_filter:
        skus = [s for s in skus if s['category'] == category_filter]

    context = {
        'skus': skus,
        'category_filter': category_filter,
    }
    return render(request, 'skus.html', context)


@login_required
def purchase_orders_list(request):
    """采购单列表"""
    status_filter = request.GET.get('status', '')
    purchase_orders = mock_data.PURCHASE_ORDERS

    if status_filter:
        purchase_orders = [po for po in purchase_orders if po['status'] == status_filter]

    context = {
        'purchase_orders': purchase_orders,
        'status_filter': status_filter,
    }
    return render(request, 'procurement/purchase_orders.html', context)


@login_required
def purchase_order_create(request):
    """创建采购单"""
    if request.method == 'POST':
        messages.success(request, '采购单创建成功')
        return redirect('purchase_orders_list')

    context = {
        'parts': mock_data.PARTS_INVENTORY,
        'mode': 'create',
    }
    return render(request, 'procurement/purchase_order_form.html', context)


@login_required
def purchase_order_edit(request, po_id):
    """编辑采购单"""
    po = next((p for p in mock_data.PURCHASE_ORDERS if p['id'] == po_id), None)

    if not po:
        messages.error(request, '采购单不存在')
        return redirect('purchase_orders_list')

    if request.method == 'POST':
        messages.success(request, '采购单更新成功')
        return redirect('purchase_orders_list')

    context = {
        'purchase_order': po,
        'parts': mock_data.PARTS_INVENTORY,
        'mode': 'edit',
    }
    return render(request, 'procurement/purchase_order_form.html', context)


@login_required
def parts_inventory_list(request):
    """部件库存"""
    category_filter = request.GET.get('category', '')
    parts = mock_data.PARTS_INVENTORY

    if category_filter:
        parts = [p for p in parts if p['category'] == category_filter]

    context = {
        'parts': parts,
        'category_filter': category_filter,
    }
    return render(request, 'procurement/parts_inventory.html', context)


@login_required
def parts_movements_list(request):
    """部件出入库流水"""
    movement_type = request.GET.get('type', '')
    movements = mock_data.PARTS_MOVEMENTS

    if movement_type:
        movements = [m for m in movements if m['type'] == movement_type]

    context = {
        'movements': movements,
        'movement_type': movement_type,
    }
    return render(request, 'procurement/parts_movements.html', context)


@login_required
def settings_view(request):
    """系统设置"""
    if request.method == 'POST':
        messages.success(request, '设置已保存')
        return redirect('settings')

    context = {}
    return render(request, 'settings.html', context)


@login_required
def audit_logs(request):
    """审计日志"""
    logs = mock_data.AUDIT_LOGS

    context = {
        'logs': logs,
    }
    return render(request, 'audit_logs.html', context)


@login_required
def api_get_sku_details(request, sku_id):
    """API: 获取SKU详情（用于订单表单）"""
    sku = next((s for s in mock_data.SKUS if s['id'] == sku_id), None)

    if sku:
        return JsonResponse({
            'success': True,
            'data': {
                'id': sku['id'],
                'name': sku['name'],
                'rental_price': str(sku['rental_price']),
                'deposit': str(sku['deposit']),
                'available': sku['available'],
            }
        })
    else:
        return JsonResponse({'success': False, 'message': 'SKU不存在'})


@login_required
def api_check_availability(request):
    """API: 检查SKU可用性"""
    sku_id = request.GET.get('sku_id')
    event_date = request.GET.get('event_date')

    sku = next((s for s in mock_data.SKUS if s['id'] == int(sku_id)), None)

    if sku:
        return JsonResponse({
            'success': True,
            'available': sku['available'],
            'message': f'该日期可用数量：{sku["available"]}'
        })
    else:
        return JsonResponse({'success': False, 'message': 'SKU不存在'})


@login_required
def users_list(request):
    """用户管理"""
    users = mock_data.USERS

    context = {
        'users': users,
    }
    return render(request, 'users.html', context)
