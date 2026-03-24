"""
微信小程序 API 认证装饰器
"""
from functools import wraps
from django.http import JsonResponse
from apps.core.services.wechat_auth_service import verify_token
from apps.core.models import WechatCustomer, WechatStaffBinding


def mp_login_required(view_func):
    """要求小程序登录的装饰器"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return JsonResponse({'error': '未登录'}, status=401)

        token = auth_header[7:]
        customer_id = verify_token(token)
        if not customer_id:
            return JsonResponse({'error': '登录已过期，请重新登录'}, status=401)

        try:
            request.mp_customer = WechatCustomer.objects.get(
                id=customer_id, is_active=True
            )
        except WechatCustomer.DoesNotExist:
            return JsonResponse({'error': '用户不存在'}, status=401)

        return view_func(request, *args, **kwargs)
    return wrapper


def mp_login_optional(view_func):
    """可选登录的装饰器（未登录也能访问，但尝试解析用户）"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        request.mp_customer = None
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            customer_id = verify_token(token)
            if customer_id:
                try:
                    request.mp_customer = WechatCustomer.objects.get(
                        id=customer_id, is_active=True
                    )
                except WechatCustomer.DoesNotExist:
                    pass
        return view_func(request, *args, **kwargs)
    return wrapper


def mp_staff_required(view_func):
    """要求小程序登录且已绑定员工账号"""
    @wraps(view_func)
    @mp_login_required
    def wrapper(request, *args, **kwargs):
        try:
            binding = WechatStaffBinding.objects.select_related('user').get(
                customer=request.mp_customer,
                is_active=True,
                user__is_active=True,
            )
        except WechatStaffBinding.DoesNotExist:
            return JsonResponse({'error': '未绑定员工账号', 'needBind': True}, status=403)

        request.mp_staff_user = binding.user
        request.mp_staff_binding = binding
        return view_func(request, *args, **kwargs)

    return wrapper
