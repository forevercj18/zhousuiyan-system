"""
操作日志中间件
自动记录用户的关键操作
"""
import logging

from django.utils.deprecation import MiddlewareMixin
from apps.core.services import AuditService

logger = logging.getLogger(__name__)


class AuditLogMiddleware(MiddlewareMixin):
    """操作日志中间件"""

    # 需要记录的URL模式
    LOGGED_PATHS = [
        '/orders/',
        '/skus/',
        '/procurement/',
        '/users/',
        '/settings/',
    ]

    # 需要记录的HTTP方法
    LOGGED_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE']

    def process_response(self, request, response):
        """处理响应，记录操作日志"""

        # 只记录已登录用户的操作
        if not request.user.is_authenticated:
            return response

        # 只记录特定方法
        if request.method not in self.LOGGED_METHODS:
            return response

        # 只记录特定路径
        path = request.path
        should_log = any(path.startswith(logged_path) for logged_path in self.LOGGED_PATHS)

        if not should_log:
            return response

        # 只记录成功的操作（2xx状态码）
        if not (200 <= response.status_code < 300):
            return response

        # 解析操作类型和模块
        action, module, target = self._parse_request(request)

        if action and module:
            try:
                # 获取客户端IP
                ip_address = self._get_client_ip(request)

                details = self._get_details(request, response, action, module, target)
                # 创建结构化日志记录
                AuditService.log_with_diff(
                    user=request.user,
                    action=action,
                    module=module,
                    target=target,
                    summary=details.get('summary', '中间件记录请求操作'),
                    before=details.get('before', {}),
                    after=details.get('after', {}),
                    extra=details.get('extra', {}),
                    ip_address=ip_address,
                )
            except Exception as e:
                # 日志记录失败不应影响正常业务
                logger.exception("Failed to create audit log: %s", e)

        return response

    def _parse_request(self, request):
        """解析请求，确定操作类型和模块"""
        path = request.path
        method = request.method

        # 确定操作类型
        action_map = {
            'POST': 'create',
            'PUT': 'update',
            'PATCH': 'update',
            'DELETE': 'delete',
        }
        action = action_map.get(method, 'update')

        # 确定模块和目标
        module = ''
        target = ''

        if '/orders/' in path:
            module = '订单'
            if 'create' in path or method == 'POST':
                target = '新订单'
            else:
                # 尝试从路径中提取订单ID
                parts = path.split('/')
                if len(parts) > 2:
                    target = f"订单 {parts[2]}"

        elif '/skus/' in path:
            module = 'SKU'
            target = 'SKU'

        elif '/procurement/purchase-orders' in path:
            module = '采购'
            target = '采购单'

        elif '/procurement/parts' in path:
            module = '部件'
            target = '部件'

        elif '/users/' in path:
            module = '用户'
            target = '用户'

        elif '/settings/' in path:
            module = '系统设置'
            target = '设置'

        return action, module, target

    def _get_details(self, request, response, action, module, target):
        """获取结构化操作详情"""
        return {
            'summary': '中间件记录请求操作',
            'before': {},
            'after': {},
            'extra': {
                'source': 'middleware',
                'http_method': request.method,
                'path': request.path,
                'query_string': request.META.get('QUERY_STRING', ''),
                'status_code': int(response.status_code),
                'module': module,
                'target': target,
                'action': action,
            }
        }

    def _get_client_ip(self, request):
        """获取客户端IP地址"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
