"""
微信小程序认证服务
- 微信 code2Session 登录
- 自定义 Token 签发与校验
"""
import hashlib
import hmac
import json
import time
import urllib.request

from django.conf import settings


def code_to_session(code):
    """
    调用微信 code2Session 接口获取 openid
    https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/user-login/code2Session.html
    """
    appid = _get_config('MP_APPID', 'mp_appid')
    secret = _get_config('MP_SECRET', 'mp_secret')

    if not appid or not secret:
        raise ValueError('微信小程序 AppID/Secret 未配置')

    url = (
        f"https://api.weixin.qq.com/sns/jscode2session"
        f"?appid={appid}&secret={secret}&js_code={code}"
        f"&grant_type=authorization_code"
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        raise ValueError(f'调用微信接口失败: {e}')

    if 'errcode' in data and data['errcode'] != 0:
        raise ValueError(f"微信登录失败: {data.get('errmsg', '未知错误')}")

    return {
        'openid': data['openid'],
        'session_key': data.get('session_key', ''),
        'unionid': data.get('unionid', ''),
    }


def generate_token(customer_id, openid):
    """
    生成自定义 Token（HMAC 签名，含过期时间）
    格式：{customer_id}.{expire_ts}.{signature}
    有效期：7 天
    """
    expire_ts = int(time.time()) + 86400 * 7
    payload = f"{customer_id}.{expire_ts}"
    secret_key = settings.SECRET_KEY
    signature = hmac.new(
        secret_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{payload}.{signature}"


def verify_token(token):
    """
    验证 Token 有效性
    返回 customer_id（int）或 None
    """
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        customer_id, expire_ts, signature = int(parts[0]), int(parts[1]), parts[2]

        # 检查过期
        if time.time() > expire_ts:
            return None

        # 验证签名
        payload = f"{customer_id}.{expire_ts}"
        expected = hmac.new(
            settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()[:32]
        if not hmac.compare_digest(signature, expected):
            return None

        return customer_id
    except Exception:
        return None


def _get_config(env_key, settings_key):
    """优先从环境变量获取，降级到 SystemSettings"""
    import os
    val = os.environ.get(env_key)
    if val:
        return val
    try:
        from apps.core.utils import get_system_settings
        return get_system_settings().get(settings_key, '')
    except Exception:
        return ''
