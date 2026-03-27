"""
微信小程序 API 视图

接口列表：
- POST /api/mp/login/           微信登录
- GET  /api/mp/skus/            产品列表
- GET  /api/mp/skus/<id>/       产品详情
- POST /api/mp/reservations/    提交意向订单
- GET  /api/mp/my-reservations/ 我的意向订单列表
- GET  /api/mp/my-reservations/<id>/  意向订单详情
"""
import json
import logging
from decimal import Decimal

from django.contrib.auth import authenticate
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.conf import settings

from apps.core.models import SKU, SKUImage, SKUComponent, Reservation, WechatCustomer, WechatStaffBinding, Order, User
from apps.core.permissions import has_permission, has_action_permission
from apps.core.services.order_service import OrderService
from apps.core.services.storage_service import StorageService
from apps.core.services.wechat_auth_service import code_to_session, generate_token, get_phone_number_by_code
from apps.core.services.audit_service import AuditService
from .mp_auth import mp_login_required, mp_login_optional, mp_staff_required

logger = logging.getLogger(__name__)

# 面向客户的预定单状态映射
RESERVATION_STATUS_MAP = {
    'pending_info': '待客服确认',
    'ready_to_convert': '确认中',
    'converted': '已下单',
    'cancelled': '已取消',
    'refunded': '已退款',
}


# ============================================================
# 登录
# ============================================================

@csrf_exempt
@require_http_methods(["POST"])
def mp_login(request):
    """微信登录：code 换 token"""
    try:
        body = json.loads(request.body)
        code = body.get('code', '')
        if not code:
            return JsonResponse({'error': 'code 不能为空'}, status=400)

        # 调微信接口换 openid
        wx_data = code_to_session(code)
        openid = wx_data['openid']

        # 查找或创建客户
        customer, created = WechatCustomer.objects.get_or_create(
            openid=openid,
            defaults={
                'unionid': wx_data.get('unionid', ''),
                'nickname': (body.get('nickname') or '').strip(),
                'avatar_url': (body.get('avatar_url') or '').strip(),
            }
        )
        if not created:
            changed_fields = []
            unionid = (wx_data.get('unionid') or '').strip()
            nickname = (body.get('nickname') or '').strip()
            avatar_url = (body.get('avatar_url') or '').strip()
            if unionid and customer.unionid != unionid:
                customer.unionid = unionid
                changed_fields.append('unionid')
            if nickname and customer.nickname != nickname:
                customer.nickname = nickname
                changed_fields.append('nickname')
            if avatar_url and customer.avatar_url != avatar_url:
                customer.avatar_url = avatar_url
                changed_fields.append('avatar_url')
            changed_fields.append('updated_at')
            customer.save(update_fields=changed_fields)

        # 生成 Token
        token = generate_token(customer.id, openid)

        return JsonResponse({
            'token': token,
            'customer': {
                'id': customer.id,
                'nickname': customer.nickname,
                'avatar_url': customer.avatar_url,
                'phone': customer.phone,
                'wechat_id': customer.wechat_id,
                'is_new': created,
            }
            ,
            'staff_bound': _get_staff_binding(customer) is not None,
        })
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)
    except Exception:
        logger.exception('小程序登录异常')
        return JsonResponse({'error': '登录失败，请稍后重试'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@mp_login_required
def mp_sync_phone(request):
    """微信手机号授权：前端 getPhoneNumber 返回 code，后端换取手机号并回写客户档案。"""
    try:
        body = json.loads(request.body)
        phone_code = (body.get('phone_code') or '').strip()
        if not phone_code:
            return JsonResponse({'error': 'phone_code 不能为空'}, status=400)
        phone_data = get_phone_number_by_code(phone_code)
        phone_number = phone_data['phone_number']
        changed_fields = []
        if request.mp_customer.phone != phone_number:
            request.mp_customer.phone = phone_number
            changed_fields.append('phone')
        if changed_fields:
            changed_fields.append('updated_at')
            request.mp_customer.save(update_fields=changed_fields)
        return JsonResponse({
            'phone': request.mp_customer.phone,
            'message': '手机号已同步',
        })
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)
    except Exception:
        logger.exception('小程序手机号同步异常')
        return JsonResponse({'error': '手机号同步失败，请稍后重试'}, status=500)


# ============================================================
# 产品展示
# ============================================================

@require_http_methods(["GET"])
@mp_login_optional
def mp_sku_list(request):
    """产品列表（仅 mp_visible=True）"""
    qs = SKU.objects.filter(
        is_active=True, mp_visible=True
    ).order_by('mp_sort_order', 'id')

    # 分类筛选
    category = request.GET.get('category')
    if category:
        qs = qs.filter(category=category)

    # 关键词搜索
    keyword = request.GET.get('keyword')
    if keyword:
        qs = qs.filter(name__icontains=keyword)

    results = []
    for sku in qs:
        cover = _get_cover_image_url(sku, request)
        stock_status = _calc_stock_status(sku)

        results.append({
            'id': sku.id,
            'name': sku.name,
            'category': sku.category,
            'cover_image': cover,
            'rental_price': str(sku.rental_price),
            'deposit': str(sku.deposit),
            'display_stock': sku.display_stock,
            'stock_status': stock_status,
            'description': (sku.description[:100] + '...') if sku.description and len(sku.description) > 100 else (sku.description or ''),
        })

    return JsonResponse({'results': results})


@require_http_methods(["GET"])
@mp_login_optional
def mp_sku_detail(request, pk):
    """产品详情 + 部件列表 + 多图"""
    try:
        sku = SKU.objects.get(pk=pk, is_active=True, mp_visible=True)
    except SKU.DoesNotExist:
        return JsonResponse({'error': '产品不存在'}, status=404)

    # 多图
    image_list = []
    for image in sku.images.all().order_by('sort_order', 'id'):
        image_url = _get_sku_image_url(image, request)
        if not image_url:
            continue
        image_list.append({
            'url': image_url,
            'is_cover': image.is_cover,
        })

    # 如果没有多图，降级使用 SKU.image
    if not image_list:
        cover_url = _get_cover_image_url(sku, request)
        if cover_url:
            image_list = [{'url': cover_url, 'is_cover': True}]

    # 部件列表（仅展示名称、规格、数量，不暴露内部供应链信息）
    components = list(
        SKUComponent.objects.filter(sku=sku)
        .select_related('part')
        .values('part__name', 'part__spec', 'quantity_per_set')
    )
    component_list = [{
        'name': c['part__name'],
        'spec': c['part__spec'],
        'quantity': c['quantity_per_set'],
    } for c in components]

    stock_status = _calc_stock_status(sku)

    return JsonResponse({
        'id': sku.id,
        'name': sku.name,
        'category': sku.category,
        'images': image_list,
        'rental_price': str(sku.rental_price),
        'deposit': str(sku.deposit),
        'display_stock': sku.display_stock,
        'stock_status': stock_status,
        'description': sku.description,
        'components': component_list,
    })


# ============================================================
# 意向下单
# ============================================================

@csrf_exempt
@require_http_methods(["POST"])
@mp_login_required
def mp_create_reservation(request):
    """提交意向订单"""
    try:
        body = json.loads(request.body)

        # 参数校验
        sku_id = body.get('sku_id')
        event_date = body.get('event_date')
        customer_wechat = body.get('customer_wechat', '')

        if not sku_id:
            return JsonResponse({'error': '请选择产品'}, status=400)
        if not event_date:
            return JsonResponse({'error': '请选择日期'}, status=400)
        if not customer_wechat:
            return JsonResponse({'error': '请填写微信号'}, status=400)

        # 验证 SKU
        try:
            sku = SKU.objects.get(pk=sku_id, is_active=True, mp_visible=True)
        except SKU.DoesNotExist:
            return JsonResponse({'error': '产品不存在或已下架'}, status=400)

        # 防刷：单客户每日最多 10 个意向订单
        today = timezone.localdate()
        today_count = Reservation.objects.filter(
            wechat_customer=request.mp_customer,
            source='miniprogram',
            created_at__date=today,
        ).count()
        if today_count >= 10:
            return JsonResponse({'error': '今日提交次数已达上限，请明日再试'}, status=429)

        # 创建预定单
        reservation = Reservation(
            customer_wechat=customer_wechat,
            customer_name=body.get('customer_name', ''),
            customer_phone=body.get('customer_phone', ''),
            city=body.get('city', ''),
            delivery_address=body.get('delivery_address', ''),
            sku=sku,
            quantity=body.get('quantity', 1),
            event_date=event_date,
            notes=body.get('notes', ''),
            source='miniprogram',
            wechat_customer=request.mp_customer,
            # created_by 留空（非内部用户创建）
            # owner 留空（由后台客服手动分配）
        )
        reservation.save()

        customer_changed_fields = []
        customer_name = (body.get('customer_name') or '').strip()
        customer_phone = (body.get('customer_phone') or '').strip()
        if customer_phone and request.mp_customer.phone != customer_phone:
            request.mp_customer.phone = customer_phone
            customer_changed_fields.append('phone')
        if customer_wechat and request.mp_customer.wechat_id != customer_wechat:
            request.mp_customer.wechat_id = customer_wechat
            customer_changed_fields.append('wechat_id')
        if customer_changed_fields:
            customer_changed_fields.append('updated_at')
            request.mp_customer.save(update_fields=customer_changed_fields)

        return JsonResponse({
            'reservation_no': reservation.reservation_no,
            'message': '意向订单提交成功，客服将尽快通过微信与您联系确认',
        }, status=201)

    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)
    except Exception:
        logger.exception('小程序提交意向订单异常')
        return JsonResponse({'error': '提交失败，请稍后重试'}, status=500)


# ============================================================
# 我的订单
# ============================================================

@require_http_methods(["GET"])
@mp_login_required
def mp_my_reservations(request):
    """我的意向订单列表"""
    qs = Reservation.objects.filter(
        wechat_customer=request.mp_customer,
        source='miniprogram',
    ).select_related('sku').order_by('-created_at')

    results = []
    for r in qs:
        cover = _get_cover_image_url(r.sku, request) if r.sku else ''
        progress = _build_reservation_progress_payload(r)
        results.append({
            'id': r.id,
            'reservation_no': r.reservation_no,
            'sku_name': r.sku.name if r.sku else '',
            'sku_cover_image': cover,
            'event_date': str(r.event_date),
            'quantity': r.quantity,
            'deposit_amount': str(r.deposit_amount),
            'status': r.status,
            'status_label': RESERVATION_STATUS_MAP.get(r.status, r.get_status_display()),
            'created_at': r.created_at.isoformat(),
            'followup_date': str(r.followup_date) if r.followup_date else '',
            **progress,
        })

    return JsonResponse({'results': results})


@require_http_methods(["GET"])
@mp_login_required
def mp_reservation_detail(request, pk):
    """意向订单详情"""
    try:
        reservation = Reservation.objects.select_related('sku').get(
            pk=pk,
            wechat_customer=request.mp_customer,
            source='miniprogram',
        )
    except Reservation.DoesNotExist:
        return JsonResponse({'error': '订单不存在'}, status=404)

    cover = _get_cover_image_url(reservation.sku, request) if reservation.sku else ''
    progress = _build_reservation_progress_payload(reservation)

    return JsonResponse({
        'id': reservation.id,
        'reservation_no': reservation.reservation_no,
        'sku_name': reservation.sku.name if reservation.sku else '',
        'sku_cover_image': cover,
        'event_date': str(reservation.event_date),
        'quantity': reservation.quantity,
        'city': reservation.city,
        'delivery_address': reservation.delivery_address,
        'customer_name': reservation.customer_name,
        'customer_phone': reservation.customer_phone,
        'customer_wechat': reservation.customer_wechat,
        'deposit_amount': str(reservation.deposit_amount),
        'notes': reservation.notes,
        'status': reservation.status,
        'status_label': RESERVATION_STATUS_MAP.get(reservation.status, reservation.get_status_display()),
        'created_at': reservation.created_at.isoformat(),
        'followup_date': str(reservation.followup_date) if reservation.followup_date else '',
        **progress,
    })


# ============================================================
# 员工模式
# ============================================================


@csrf_exempt
@require_http_methods(["POST"])
@mp_login_required
def mp_staff_bind(request):
    """绑定后台员工账号"""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)

    username = (body.get('username') or '').strip()
    password = body.get('password') or ''
    if not username or not password:
        return JsonResponse({'error': '请输入后台账号和密码'}, status=400)

    user = authenticate(username=username, password=password)
    if not user or not user.is_active:
        return JsonResponse({'error': '账号或密码错误'}, status=400)
    if user.role not in ['admin', 'manager', 'warehouse_manager', 'warehouse_staff', 'customer_service']:
        return JsonResponse({'error': '当前账号不支持绑定到员工端'}, status=403)

    existed = WechatStaffBinding.objects.select_related('customer').filter(user=user).exclude(customer=request.mp_customer).first()
    if existed:
        return JsonResponse({'error': f'该员工账号已绑定其他微信身份：{existed.customer.nickname or existed.customer.openid}'}, status=409)

    binding, _ = WechatStaffBinding.objects.update_or_create(
        customer=request.mp_customer,
        defaults={'user': user, 'is_active': True},
    )

    return JsonResponse({
        'message': '员工账号绑定成功',
        'staff': _serialize_staff_profile(binding),
    })


@require_http_methods(["GET"])
@mp_login_required
def mp_staff_profile(request):
    binding = _get_staff_binding(request.mp_customer)
    return JsonResponse({
        'is_staff_bound': binding is not None,
        'staff': _serialize_staff_profile(binding) if binding else None,
    })


@require_http_methods(["GET"])
@mp_staff_required
def mp_staff_dashboard(request):
    user = request.mp_staff_user
    today = timezone.localdate()

    reservation_qs = Reservation.objects.select_related('converted_order')
    order_qs = Order.objects.all()

    if user.role == 'customer_service':
        reservation_qs = reservation_qs.filter(owner=user)
        order_qs = order_qs.filter(
            id__in=Reservation.objects.filter(owner=user, converted_order__isnull=False).values('converted_order_id')
        )

    reservation_items = list(reservation_qs)
    today_contact = sum(1 for r in reservation_items if r.contact_status_code == 'today')
    overdue_contact = sum(1 for r in reservation_items if r.contact_status_code == 'overdue')
    ready_to_convert = sum(1 for r in reservation_items if r.status == 'ready_to_convert')
    converted_waiting_ship = sum(
        1 for r in reservation_items
        if r.status == 'converted' and r.fulfillment_stage_code == 'awaiting_shipment'
    )

    active_order_items = list(order_qs)
    waiting_ship = sum(1 for o in active_order_items if o.status == 'confirmed')
    return_service_pending = sum(
        1 for o in active_order_items
        if o.return_service_type == 'platform_return_included' and o.return_pickup_status in ['pending_schedule', 'scheduled']
    )
    balance_pending = sum(
        1 for o in active_order_items
        if (o.balance or Decimal('0.00')) > Decimal('0.00') and o.status not in ['completed', 'cancelled']
    )
    shipment_overdue = sum(
        1 for o in active_order_items
        if o.status == 'confirmed' and o.ship_date and o.ship_date <= today and not o.ship_tracking
    )

    shortcuts = []
    if has_permission(user, 'reservations', 'view'):
        shortcuts.extend([
            {'key': 'today_contact', 'label': '今日需联系', 'count': today_contact, 'target': '/pages/work-reservations/work-reservations?contact=today'},
            {'key': 'overdue_contact', 'label': '逾期未联系', 'count': overdue_contact, 'target': '/pages/work-reservations/work-reservations?contact=overdue'},
            {'key': 'ready_to_convert', 'label': '待转正式订单', 'count': ready_to_convert, 'target': '/pages/work-reservations/work-reservations?status=ready_to_convert'},
            {'key': 'converted_waiting_ship', 'label': '转单待发货', 'count': converted_waiting_ship, 'target': '/pages/work-reservations/work-reservations?journey=awaiting_shipment'},
        ])
    if has_permission(user, 'orders', 'view') or user.role in ['warehouse_manager', 'warehouse_staff']:
        shortcuts.extend([
            {'key': 'waiting_ship', 'label': '待发货', 'count': waiting_ship, 'target': '/pages/work-orders/work-orders?status=confirmed'},
            {'key': 'shipment_overdue', 'label': '待发货超时', 'count': shipment_overdue, 'target': '/pages/work-orders/work-orders?followup=shipment_overdue'},
            {'key': 'balance_pending', 'label': '待收尾款', 'count': balance_pending, 'target': '/pages/work-orders/work-orders?followup=balance_pending'},
            {'key': 'return_service_pending', 'label': '包回邮待处理', 'count': return_service_pending, 'target': '/pages/work-orders/work-orders?followup=return_service_pending'},
        ])

    return JsonResponse({
        'staff': _serialize_staff_profile(request.mp_staff_binding),
        'shortcuts': shortcuts,
    })


@require_http_methods(["GET"])
@mp_staff_required
def mp_staff_reservations(request):
    user = request.mp_staff_user
    if not has_permission(user, 'reservations', 'view'):
        return JsonResponse({'error': '没有权限查看预定单'}, status=403)

    reservations = Reservation.objects.select_related('sku', 'owner', 'converted_order').order_by('-created_at')
    if user.role == 'customer_service':
        reservations = reservations.filter(owner=user)

    status_filter = (request.GET.get('status') or '').strip()
    contact_filter = (request.GET.get('contact') or '').strip()
    journey_filter = (request.GET.get('journey') or '').strip()
    source_filter = (request.GET.get('source') or '').strip()
    owner_filter = (request.GET.get('owner_id') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()

    if status_filter:
        reservations = reservations.filter(status=status_filter)
    if keyword:
        reservations = reservations.filter(
            Q(reservation_no__icontains=keyword) |
            Q(customer_name__icontains=keyword) |
            Q(customer_phone__icontains=keyword) |
            Q(customer_wechat__icontains=keyword) |
            Q(sku__name__icontains=keyword)
        )
    if source_filter:
        reservations = reservations.filter(source=source_filter)
    if owner_filter and _staff_can_filter_reservation_owner(user) and owner_filter.isdigit():
        reservations = reservations.filter(owner_id=int(owner_filter))

    items = []
    for reservation in reservations:
        progress = _build_reservation_progress_payload(reservation)
        if contact_filter and progress['contact_status_code'] != contact_filter:
            continue
        if journey_filter and progress['journey_code'] != journey_filter:
            continue
        items.append({
            'id': reservation.id,
            'reservation_no': reservation.reservation_no,
            'customer_name': reservation.customer_name or reservation.customer_wechat,
            'customer_phone': reservation.customer_phone,
            'customer_wechat': reservation.customer_wechat,
            'source': reservation.source,
            'source_label': reservation.get_source_display(),
            'sku_name': reservation.sku.name if reservation.sku else '',
            'event_date': str(reservation.event_date) if reservation.event_date else '',
            'status': reservation.status,
            'status_label': reservation.get_status_display(),
            'owner_name': _user_display_name(reservation.owner),
            'followup_date': str(reservation.followup_date) if reservation.followup_date else '',
            'can_mark_pending_info': has_permission(user, 'reservations', 'update') and reservation.status not in ['pending_info', 'converted', 'cancelled', 'refunded'],
            'can_mark_ready_to_convert': has_permission(user, 'reservations', 'update') and reservation.status not in ['ready_to_convert', 'converted', 'cancelled', 'refunded'],
            **progress,
        })

    return JsonResponse({
        'results': items,
        'filters': {
            'sources': [
                {'value': value, 'label': label}
                for value, label in Reservation.SOURCE_CHOICES
            ],
            'owners': [
                {'value': owner.id, 'label': _user_display_name(owner)}
                for owner in _get_staff_reservation_owner_candidates()
            ] if _staff_can_filter_reservation_owner(user) else [],
        }
    })


@require_http_methods(["GET"])
@mp_staff_required
def mp_staff_reservation_detail(request, pk):
    user = request.mp_staff_user
    if not has_permission(user, 'reservations', 'view'):
        return JsonResponse({'error': '没有权限查看预定单'}, status=403)

    try:
        reservation = Reservation.objects.select_related('sku', 'owner', 'converted_order').get(pk=pk)
    except Reservation.DoesNotExist:
        return JsonResponse({'error': '预定单不存在'}, status=404)

    if user.role == 'customer_service' and reservation.owner_id != user.id:
        return JsonResponse({'error': '只能查看自己负责的预定单'}, status=403)

    progress = _build_reservation_progress_payload(reservation)
    return JsonResponse({
        'id': reservation.id,
        'reservation_no': reservation.reservation_no,
        'customer_name': reservation.customer_name,
        'customer_phone': reservation.customer_phone,
        'customer_wechat': reservation.customer_wechat,
        'city': reservation.city,
        'delivery_address': reservation.delivery_address,
        'sku_name': reservation.sku.name if reservation.sku else '',
        'sku_cover_image': _get_cover_image_url(reservation.sku, request) if reservation.sku else '',
        'quantity': reservation.quantity,
        'event_date': str(reservation.event_date) if reservation.event_date else '',
        'deposit_amount': str(reservation.deposit_amount),
        'status': reservation.status,
        'status_label': reservation.get_status_display(),
        'owner_name': _user_display_name(reservation.owner),
        'owner_id': reservation.owner_id,
        'notes': reservation.notes,
        'created_at': reservation.created_at.isoformat(),
        'can_update_status': has_permission(user, 'reservations', 'update'),
        'can_update_followup': has_permission(user, 'reservations', 'update'),
        'can_transfer_owner': _staff_can_transfer_reservation_owner(user),
        'owner_options': [
            {
                'id': owner.id,
                'label': _user_display_name(owner),
                'role': owner.role,
                'role_label': owner.get_role_display(),
            }
            for owner in _get_staff_reservation_owner_candidates()
        ] if _staff_can_transfer_reservation_owner(user) else [],
        **progress,
    })


@csrf_exempt
@require_http_methods(["POST"])
@mp_staff_required
def mp_staff_reservation_update_status(request, pk):
    user = request.mp_staff_user
    if not has_permission(user, 'reservations', 'update'):
        return JsonResponse({'error': '没有权限修改预定单'}, status=403)

    try:
        reservation = Reservation.objects.get(pk=pk)
    except Reservation.DoesNotExist:
        return JsonResponse({'error': '预定单不存在'}, status=404)

    if user.role == 'customer_service' and reservation.owner_id != user.id:
        return JsonResponse({'error': '只能操作自己负责的预定单'}, status=403)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)

    new_status = (body.get('status') or '').strip()
    if new_status not in ['pending_info', 'ready_to_convert']:
        return JsonResponse({'error': '当前移动端仅支持切换为待补信息或可转正式订单'}, status=400)
    if reservation.status in ['converted', 'cancelled', 'refunded']:
        return JsonResponse({'error': '当前状态不允许在移动端修改'}, status=400)

    reservation.status = new_status
    reservation.save(update_fields=['status', 'updated_at'])
    return JsonResponse({
        'message': '预定单状态已更新',
        'status': reservation.status,
        'status_label': reservation.get_status_display(),
    })


@csrf_exempt
@require_http_methods(["POST"])
@mp_staff_required
def mp_staff_reservation_update_followup(request, pk):
    user = request.mp_staff_user
    if not has_permission(user, 'reservations', 'update'):
        return JsonResponse({'error': '没有权限修改预定单'}, status=403)

    try:
        reservation = Reservation.objects.get(pk=pk)
    except Reservation.DoesNotExist:
        return JsonResponse({'error': '预定单不存在'}, status=404)

    if user.role == 'customer_service' and reservation.owner_id != user.id:
        return JsonResponse({'error': '只能操作自己负责的预定单'}, status=403)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)

    before = {
        'customer_name': reservation.customer_name,
        'customer_phone': reservation.customer_phone,
        'delivery_address': reservation.delivery_address,
        'notes': reservation.notes,
    }
    reservation.customer_name = (body.get('customer_name') or '').strip()
    reservation.customer_phone = (body.get('customer_phone') or '').strip()
    reservation.delivery_address = (body.get('delivery_address') or '').strip()
    reservation.notes = (body.get('notes') or '').strip()
    reservation.save(update_fields=['customer_name', 'customer_phone', 'delivery_address', 'notes', 'updated_at'])
    AuditService.log_with_diff(
        user=user,
        action='update',
        module='预定单',
        target=reservation.reservation_no,
        summary='员工端更新预定单跟进信息',
        before=before,
        after={
            'customer_name': reservation.customer_name,
            'customer_phone': reservation.customer_phone,
            'delivery_address': reservation.delivery_address,
            'notes': reservation.notes,
        },
    )
    return JsonResponse({
        'message': '跟进信息已更新',
        'customer_name': reservation.customer_name,
        'customer_phone': reservation.customer_phone,
        'delivery_address': reservation.delivery_address,
        'notes': reservation.notes,
    })


@csrf_exempt
@require_http_methods(["POST"])
@mp_staff_required
def mp_staff_reservation_transfer_owner(request, pk):
    user = request.mp_staff_user
    if not _staff_can_transfer_reservation_owner(user):
        return JsonResponse({'error': '没有转交负责人权限'}, status=403)

    try:
        reservation = Reservation.objects.select_related('owner').get(pk=pk)
    except Reservation.DoesNotExist:
        return JsonResponse({'error': '预定单不存在'}, status=404)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)

    owner_id = body.get('owner_id')
    reason = (body.get('reason') or '').strip()
    if not str(owner_id or '').isdigit():
        return JsonResponse({'error': '请选择新的负责人'}, status=400)

    try:
        new_owner = _get_staff_reservation_owner_candidates().get(id=int(owner_id))
    except User.DoesNotExist:
        return JsonResponse({'error': '负责人不存在或不可用'}, status=404)

    if reservation.owner_id == new_owner.id:
        return JsonResponse({'error': '新负责人不能与当前负责人相同'}, status=400)

    before_owner = _user_display_name(reservation.owner)
    before_owner_id = reservation.owner_id
    reservation.owner = new_owner
    reservation.save(update_fields=['owner', 'updated_at'])
    AuditService.log_with_diff(
        user=user,
        action='update',
        module='预定单',
        target=reservation.reservation_no,
        summary='员工端转交负责人',
        before={'owner_name': before_owner, 'owner_id': before_owner_id},
        after={'owner_name': _user_display_name(new_owner), 'owner_id': new_owner.id},
        extra={'reason': reason, 'source': 'mini_program_transfer_owner'},
    )
    return JsonResponse({
        'message': '负责人已转交',
        'owner_id': new_owner.id,
        'owner_name': _user_display_name(new_owner),
    })


@require_http_methods(["GET"])
@mp_staff_required
def mp_staff_orders(request):
    user = request.mp_staff_user
    if not has_permission(user, 'orders', 'view') and user.role not in ['warehouse_manager', 'warehouse_staff']:
        return JsonResponse({'error': '没有权限查看订单'}, status=403)

    orders = Order.objects.prefetch_related('items__sku').order_by('-created_at')
    if user.role == 'customer_service':
        orders = orders.filter(
            id__in=Reservation.objects.filter(owner=user, converted_order__isnull=False).values('converted_order_id')
        )

    status_filter = (request.GET.get('status') or '').strip()
    followup_filter = (request.GET.get('followup') or '').strip()
    source_filter = (request.GET.get('order_source') or '').strip()
    owner_filter = (request.GET.get('owner_id') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    if status_filter:
        orders = orders.filter(status=status_filter)
    if source_filter:
        orders = orders.filter(order_source=source_filter)
    if owner_filter and _staff_can_filter_reservation_owner(user) and owner_filter.isdigit():
        orders = orders.filter(
            id__in=Reservation.objects.filter(owner_id=int(owner_filter), converted_order__isnull=False).values('converted_order_id')
        )
    if keyword:
        orders = orders.filter(
            Q(order_no__icontains=keyword) |
            Q(customer_name__icontains=keyword) |
            Q(customer_phone__icontains=keyword) |
            Q(customer_wechat__icontains=keyword) |
            Q(source_order_no__icontains=keyword) |
            Q(xianyu_order_no__icontains=keyword) |
            Q(return_service_payment_reference__icontains=keyword) |
            Q(items__sku__name__icontains=keyword)
        ).distinct()

    today = timezone.localdate()
    owner_map = {
        row['converted_order_id']: row['owner__full_name'] or row['owner__username'] or ''
        for row in Reservation.objects.filter(
            converted_order__isnull=False,
            converted_order_id__in=orders.values_list('id', flat=True),
        ).values('converted_order_id', 'owner__full_name', 'owner__username')
    }
    results = []
    for order in orders:
        shipment_overdue = bool(order.status == 'confirmed' and order.ship_date and order.ship_date <= today and not order.ship_tracking)
        balance_pending = bool((order.balance or Decimal('0.00')) > Decimal('0.00') and order.status not in ['completed', 'cancelled'])
        return_service_pending = bool(order.return_service_type == 'platform_return_included' and order.return_pickup_status in ['pending_schedule', 'scheduled'])
        if followup_filter == 'shipment_overdue' and not shipment_overdue:
            continue
        if followup_filter == 'balance_pending' and not balance_pending:
            continue
        if followup_filter == 'return_service_pending' and not return_service_pending:
            continue
        if followup_filter == 'active' and order.status in ['completed', 'cancelled']:
            continue

        items = list(order.items.all())
        results.append({
            'id': order.id,
            'order_no': order.order_no,
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'customer_wechat': order.customer_wechat,
            'event_date': str(order.event_date) if order.event_date else '',
            'ship_date': str(order.ship_date) if order.ship_date else '',
            'status': order.status,
            'status_label': order.get_status_display(),
            'ship_tracking': order.ship_tracking,
            'return_tracking': order.return_tracking,
            'delivery_address': order.delivery_address,
            'source_order_no': order.source_order_no,
            'order_source': order.order_source,
            'order_source_label': order.get_order_source_display(),
            'owner_name': owner_map.get(order.id, ''),
            'return_service_type': order.return_service_type,
            'return_service_type_label': order.get_return_service_type_display(),
            'return_pickup_status': order.return_pickup_status,
            'return_pickup_status_label': order.get_return_pickup_status_display(),
            'balance': str(order.balance),
            'shipment_overdue': shipment_overdue,
            'balance_pending': balance_pending,
            'return_service_pending': return_service_pending,
            'can_mark_delivered': has_action_permission(user, 'order.confirm_delivery') and order.status == 'confirmed',
            'can_mark_returned': has_action_permission(user, 'order.mark_returned') and order.status in ['delivered', 'in_use'],
            'can_record_balance': _staff_can_record_balance(user) and Decimal(str(order.balance or '0.00')) > Decimal('0.00') and order.status not in ['completed', 'cancelled'],
            'can_update_return_service': _staff_can_manage_return_service(user) and order.status in ['pending', 'confirmed', 'delivered', 'returned'],
            'items_summary': '、'.join(f"{item.sku.name} x{item.quantity}" for item in items[:2]),
        })

    return JsonResponse({
        'results': results,
        'filters': {
            'sources': [
                {'value': value, 'label': label}
                for value, label in Order.ORDER_SOURCE_CHOICES
            ],
            'owners': [
                {'value': owner.id, 'label': _user_display_name(owner)}
                for owner in _get_staff_reservation_owner_candidates()
            ] if _staff_can_filter_reservation_owner(user) else [],
        }
    })


@require_http_methods(["GET"])
@mp_staff_required
def mp_staff_order_detail(request, pk):
    user = request.mp_staff_user
    if not has_permission(user, 'orders', 'view') and user.role not in ['warehouse_manager', 'warehouse_staff']:
        return JsonResponse({'error': '没有权限查看订单'}, status=403)

    try:
        order = Order.objects.prefetch_related('items__sku').get(pk=pk)
    except Order.DoesNotExist:
        return JsonResponse({'error': '订单不存在'}, status=404)

    if user.role == 'customer_service' and not Reservation.objects.filter(owner=user, converted_order=order).exists():
        return JsonResponse({'error': '只能查看自己负责来源预定单对应的订单'}, status=403)

    return JsonResponse({
        'id': order.id,
        'order_no': order.order_no,
        'customer_name': order.customer_name,
        'customer_phone': order.customer_phone,
        'customer_wechat': order.customer_wechat,
        'delivery_address': order.delivery_address,
        'event_date': str(order.event_date) if order.event_date else '',
        'ship_date': str(order.ship_date) if order.ship_date else '',
        'return_date': str(order.return_date) if order.return_date else '',
        'status': order.status,
        'status_label': order.get_status_display(),
        'ship_tracking': order.ship_tracking,
        'return_tracking': order.return_tracking,
        'notes': order.notes,
        'balance': str(order.balance),
        'deposit_paid': str(order.deposit_paid),
        'total_amount': str(order.total_amount),
        'return_service_type': order.return_service_type,
        'return_service_type_label': order.get_return_service_type_display(),
        'return_service_fee': str(order.return_service_fee),
        'return_service_payment_status': order.return_service_payment_status,
        'return_pickup_status_label': order.get_return_pickup_status_display(),
        'return_pickup_status': order.return_pickup_status,
        'return_service_payment_status_label': order.get_return_service_payment_status_display(),
        'return_service_payment_channel': order.return_service_payment_channel,
        'return_service_payment_reference': order.return_service_payment_reference,
        'items': [
            {
                'sku_name': item.sku.name,
                'quantity': item.quantity,
                'rental_price': str(item.rental_price),
            }
            for item in order.items.all()
        ],
        'can_mark_delivered': has_action_permission(user, 'order.confirm_delivery') and order.status == 'confirmed',
        'can_mark_returned': has_action_permission(user, 'order.mark_returned') and order.status in ['delivered', 'in_use'],
        'can_record_balance': _staff_can_record_balance(user) and Decimal(str(order.balance or '0')) > Decimal('0.00') and order.status not in ['completed', 'cancelled'],
        'can_update_return_service': _staff_can_manage_return_service(user) and order.status in ['pending', 'confirmed', 'delivered', 'returned'],
    })


@csrf_exempt
@require_http_methods(["POST"])
@mp_staff_required
def mp_staff_mark_order_delivered(request, pk):
    user = request.mp_staff_user
    if not has_action_permission(user, 'order.confirm_delivery'):
        return JsonResponse({'error': '没有发货权限'}, status=403)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)
    ship_tracking = (body.get('ship_tracking') or '').strip()
    try:
        order = OrderService.mark_as_delivered(pk, ship_tracking, user)
    except Order.DoesNotExist:
        return JsonResponse({'error': '订单不存在'}, status=404)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({'message': '已标记发货', 'order_no': order.order_no, 'status': order.status})


@csrf_exempt
@require_http_methods(["POST"])
@mp_staff_required
def mp_staff_mark_order_returned(request, pk):
    user = request.mp_staff_user
    if not has_action_permission(user, 'order.mark_returned'):
        return JsonResponse({'error': '没有回件权限'}, status=403)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)
    return_tracking = (body.get('return_tracking') or '').strip()
    balance_paid = body.get('balance_paid') or '0'
    try:
        order = OrderService.mark_as_returned(pk, return_tracking, Decimal(str(balance_paid or '0')), user)
    except Order.DoesNotExist:
        return JsonResponse({'error': '订单不存在'}, status=404)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({'message': '已标记归还', 'order_no': order.order_no, 'status': order.status})


@csrf_exempt
@require_http_methods(["POST"])
@mp_staff_required
def mp_staff_record_order_balance(request, pk):
    user = request.mp_staff_user
    if not _staff_can_record_balance(user):
        return JsonResponse({'error': '没有登记尾款权限'}, status=403)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)
    amount = body.get('amount') or '0'
    notes = (body.get('notes') or '').strip()
    try:
        order = OrderService.record_balance_payment(pk, amount, user, notes=notes)
    except Order.DoesNotExist:
        return JsonResponse({'error': '订单不存在'}, status=404)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({
        'message': '尾款登记成功',
        'order_no': order.order_no,
        'balance': str(order.balance),
    })


@csrf_exempt
@require_http_methods(["POST"])
@mp_staff_required
def mp_staff_update_order_return_service(request, pk):
    user = request.mp_staff_user
    if not _staff_can_manage_return_service(user):
        return JsonResponse({'error': '没有包回邮处理权限'}, status=403)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求格式错误'}, status=400)
    try:
        order = OrderService.update_return_service(pk, body, user)
    except Order.DoesNotExist:
        return JsonResponse({'error': '订单不存在'}, status=404)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({
        'message': '包回邮服务已更新',
        'order_no': order.order_no,
        'return_service_type': order.return_service_type,
        'return_service_type_label': order.get_return_service_type_display(),
        'return_service_fee': str(order.return_service_fee),
        'return_service_payment_status': order.return_service_payment_status,
        'return_service_payment_status_label': order.get_return_service_payment_status_display(),
        'return_pickup_status': order.return_pickup_status,
        'return_pickup_status_label': order.get_return_pickup_status_display(),
    })


# ============================================================
# 辅助函数
# ============================================================

def _get_cover_image_url(sku, request):
    """获取 SKU 封面图完整 URL"""
    if not sku:
        return ''

    # 优先取 SKUImage 中标记为封面的
    cover_img = SKUImage.objects.filter(sku=sku, is_cover=True).first()
    if cover_img:
        cover_url = _get_sku_image_url(cover_img, request)
        if cover_url:
            return cover_url

    # 取第一张
    first_img = SKUImage.objects.filter(sku=sku).first()
    if first_img:
        first_url = _get_sku_image_url(first_img, request)
        if first_url:
            return first_url

    # 降级到 SKU.image
    if sku.image_key:
        return StorageService.build_public_url(sku.image_key)
    if sku.image:
        return _build_mp_media_url(sku.image.url, request)

    return ''


def _get_sku_image_url(image_obj, request):
    if not image_obj:
        return ''
    image_key = getattr(image_obj, 'image_key', '') or ''
    if image_key:
        return StorageService.build_public_url(image_key)
    image_field = getattr(image_obj, 'image', None)
    if image_field:
        try:
            return _build_mp_media_url(image_field.url, request)
        except ValueError:
            return ''
    return ''


def _build_mp_media_url(path, request):
    """小程序图片统一返回正式 HTTPS 域名，避免回落到 127.0.0.1。"""
    if not path:
        return ''

    public_base = getattr(settings, 'MP_PUBLIC_BASE_URL', '').strip()
    if not public_base:
        public_base = 'https://erp.yanli.net.cn'
    public_base = public_base.rstrip('/')
    normalized_path = path if path.startswith('/') else f'/{path}'
    return f'{public_base}{normalized_path}'


def _calc_stock_status(sku):
    """计算营销库存状态"""
    if sku.display_stock <= 0:
        return 'soldout'
    if sku.display_stock_warning > 0 and sku.display_stock <= sku.display_stock_warning:
        return 'warning'
    return 'normal'


def _get_staff_binding(customer):
    if not customer:
        return None
    try:
        return WechatStaffBinding.objects.select_related('user').get(
            customer=customer,
            is_active=True,
            user__is_active=True,
        )
    except WechatStaffBinding.DoesNotExist:
        return None


def _serialize_staff_profile(binding):
    if not binding:
        return None
    user = binding.user
    return {
        'id': user.id,
        'username': user.username,
        'full_name': _user_display_name(user),
        'role': user.role,
        'role_label': user.get_role_display(),
        'can_view_reservations': has_permission(user, 'reservations', 'view'),
        'can_view_orders': has_permission(user, 'orders', 'view') or user.role in ['warehouse_manager', 'warehouse_staff'],
        'can_update_reservations': has_permission(user, 'reservations', 'update'),
        'can_mark_delivered': has_action_permission(user, 'order.confirm_delivery'),
        'can_mark_returned': has_action_permission(user, 'order.mark_returned'),
    }


def _staff_can_manage_return_service(user):
    return (
        has_permission(user, 'orders', 'update')
        or user.role in ['warehouse_manager', 'warehouse_staff']
        or has_action_permission(user, 'order.mark_returned')
    )


def _staff_can_record_balance(user):
    return (
        user.role in ['admin', 'manager', 'warehouse_manager', 'warehouse_staff']
        or has_action_permission(user, 'order.mark_returned')
    )


def _user_display_name(user):
    if not user:
        return ''
    return user.full_name or user.get_full_name() or user.username


def _staff_can_transfer_reservation_owner(user):
    return user.role in ['admin', 'manager']


def _get_staff_reservation_owner_candidates():
    return User.objects.filter(
        is_active=True,
        role__in=['admin', 'manager', 'customer_service'],
    ).order_by('role', 'full_name', 'username')


def _staff_can_filter_reservation_owner(user):
    return user.role in ['admin', 'manager']


def _build_reservation_progress_payload(reservation):
    status = reservation.status
    contact_code = reservation.contact_status_code
    contact_label = reservation.contact_status_label
    fulfillment_code = reservation.fulfillment_stage_code
    fulfillment_label = reservation.fulfillment_stage_label
    shipping_label = reservation.converted_order_shipping_followup_label
    balance_label = reservation.converted_order_balance_followup_label
    converted_order_no = reservation.converted_order.order_no if reservation.converted_order_id else ''

    if status in ['cancelled', 'refunded']:
        journey_code = 'closed'
        journey_label = '已关闭'
        status_tip = '该意向订单已关闭，如需继续租赁可重新提交新的预定需求。'
    elif status == 'converted':
        if fulfillment_code == 'awaiting_shipment':
            journey_code = 'awaiting_shipment'
            journey_label = '已转正式订单，待发货'
            status_tip = f'您的需求已转为正式订单，当前{shipping_label}。'
        elif fulfillment_code == 'in_fulfillment':
            journey_code = 'in_fulfillment'
            journey_label = '已发货履约中'
            status_tip = f'正式订单已进入履约阶段，{balance_label}。'
        elif fulfillment_code == 'completed':
            journey_code = 'completed'
            journey_label = '已履约完成'
            status_tip = '该预定需求对应的正式订单已履约完成，感谢您的信任。'
        elif fulfillment_code == 'cancelled':
            journey_code = 'closed'
            journey_label = '正式订单已取消'
            status_tip = '已转成正式订单，但该正式订单目前已取消。'
        else:
            journey_code = 'converted'
            journey_label = '已转正式订单'
            status_tip = '您的需求已转为正式订单，客服会继续跟进后续履约安排。'
    elif status == 'ready_to_convert':
        journey_code = 'confirming'
        journey_label = '客服确认中'
        status_tip = '客服正在为您确认订单细节，确认完成后会为您转为正式订单。'
    else:
        if contact_code == 'overdue':
            journey_code = 'awaiting_contact'
            journey_label = '客服跟进中'
            status_tip = '客服应尽快与您联系确认细节，如未联系请耐心等待或主动沟通。'
        elif contact_code == 'today':
            journey_code = 'awaiting_contact'
            journey_label = '今日联系确认'
            status_tip = '今天将进入客服联系确认阶段，请留意微信消息。'
        else:
            journey_code = 'submitted'
            journey_label = '已提交意向'
            status_tip = '意向订单已提交成功，客服会在临近活动日期前与您联系确认。'

    steps = [
        {
            'key': 'submitted',
            'label': '提交意向',
            'status': 'done',
            'desc': '需求已提交',
        },
        {
            'key': 'contact',
            'label': '客服联系',
            'status': 'pending',
            'desc': contact_label,
        },
        {
            'key': 'converted',
            'label': '转正式订单',
            'status': 'pending',
            'desc': converted_order_no or '待确认',
        },
        {
            'key': 'fulfillment',
            'label': '履约跟进',
            'status': 'pending',
            'desc': fulfillment_label if reservation.converted_order_id else '待转单后开始',
        },
    ]

    if status in ['pending_info', 'ready_to_convert', 'converted', 'cancelled', 'refunded']:
        steps[1]['status'] = 'done' if status in ['ready_to_convert', 'converted'] else 'current' if status == 'pending_info' else 'done'
    if status == 'pending_info' and contact_code == 'pending':
        steps[1]['status'] = 'pending'
    if status == 'pending_info' and contact_code in ['today', 'overdue']:
        steps[1]['status'] = 'current'
    if status == 'ready_to_convert':
        steps[1]['status'] = 'done'
        steps[2]['status'] = 'current'
        steps[2]['desc'] = '确认完成后转单'
    if status == 'converted':
        steps[1]['status'] = 'done'
        steps[2]['status'] = 'done'
        steps[3]['status'] = 'done' if fulfillment_code == 'completed' else 'current'
        if fulfillment_code == 'cancelled':
            steps[3]['status'] = 'done'
    if status in ['cancelled', 'refunded']:
        steps[1]['status'] = 'done' if reservation.followup_date else 'pending'
        steps[2]['status'] = 'done' if reservation.converted_order_id else 'pending'
        steps[3]['status'] = 'done' if fulfillment_code in ['completed', 'cancelled'] else 'pending'

    return {
        'contact_status_code': contact_code,
        'contact_status_label': contact_label,
        'journey_code': journey_code,
        'journey_label': journey_label,
        'status_tip': status_tip,
        'converted_order_no': converted_order_no,
        'shipping_followup_label': shipping_label,
        'balance_followup_label': balance_label,
        'fulfillment_stage_code': fulfillment_code,
        'fulfillment_stage_label': fulfillment_label,
        'steps': steps,
    }
