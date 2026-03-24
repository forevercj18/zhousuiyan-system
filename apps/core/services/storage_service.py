"""
对象存储服务

当前实现：Cloudflare R2 直传与公开地址生成。
"""
import datetime as dt
import hashlib
import hmac
import os
import secrets
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from django.conf import settings


class StorageService:
    """对象存储服务（当前实现：Cloudflare R2）"""

    @staticmethod
    def is_storage_enabled():
        return bool(getattr(settings, 'R2_ENABLED', False))

    @staticmethod
    def get_storage_status():
        required_items = {
            'R2_ACCESS_KEY_ID': (getattr(settings, 'R2_ACCESS_KEY_ID', '') or '').strip(),
            'R2_SECRET_ACCESS_KEY': (getattr(settings, 'R2_SECRET_ACCESS_KEY', '') or '').strip(),
            'R2_BUCKET': (getattr(settings, 'R2_BUCKET', '') or '').strip(),
            'R2_ENDPOINT': (getattr(settings, 'R2_ENDPOINT', '') or '').strip(),
            'R2_PUBLIC_DOMAIN': (getattr(settings, 'R2_PUBLIC_DOMAIN', '') or '').strip(),
        }
        missing_items = [key for key, value in required_items.items() if not value]
        return {
            'enabled': not missing_items,
            'provider': 'Cloudflare R2',
            'missing_items': missing_items,
            'bucket': required_items['R2_BUCKET'],
            'domain': required_items['R2_PUBLIC_DOMAIN'],
            'upload_url': required_items['R2_ENDPOINT'],
            'prefix': (getattr(settings, 'R2_UPLOAD_PREFIX_SKU', '') or '').strip(),
        }

    @staticmethod
    def build_public_url(key):
        cleaned_key = (key or '').strip().lstrip('/')
        domain = (getattr(settings, 'R2_PUBLIC_DOMAIN', '') or '').strip().rstrip('/')
        if not cleaned_key or not domain:
            return ''
        return f"{domain}/{quote(cleaned_key, safe='/~')}"

    @staticmethod
    def generate_sku_upload_key(filename=''):
        prefix = (getattr(settings, 'R2_UPLOAD_PREFIX_SKU', 'sku-images/') or 'sku-images/').strip()
        prefix = prefix.strip('/')
        now = dt.datetime.now()
        _, ext = os.path.splitext(filename or '')
        ext = (ext or '').lower()
        if ext and len(ext) > 10:
            ext = ''
        random_part = secrets.token_hex(12)
        return f"{prefix}/{now.year:04d}/{now.month:02d}/{random_part}{ext}"

    @staticmethod
    def is_valid_sku_key(key):
        cleaned_key = (key or '').strip().lstrip('/')
        prefix = (getattr(settings, 'R2_UPLOAD_PREFIX_SKU', 'sku-images/') or 'sku-images/').strip().strip('/')
        return bool(cleaned_key) and cleaned_key.startswith(f"{prefix}/")

    @staticmethod
    def get_upload_payload(filename=''):
        if not StorageService.is_storage_enabled():
            raise ValueError('Cloudflare R2 未配置')

        key = StorageService.generate_sku_upload_key(filename)
        upload_url = StorageService.generate_presigned_put_url(key)
        return {
            'key': key,
            'upload_url': upload_url,
            'upload_method': 'PUT',
            'headers': {},
            'public_url': StorageService.build_public_url(key),
            'domain': getattr(settings, 'R2_PUBLIC_DOMAIN', ''),
        }

    @staticmethod
    def upload_local_file(local_path, key):
        if not StorageService.is_storage_enabled():
            raise ValueError('Cloudflare R2 未配置')
        if not os.path.exists(local_path):
            raise ValueError(f'文件不存在：{local_path}')
        if not StorageService.is_valid_sku_key(key):
            raise ValueError('非法图片 Key')

        upload_url = StorageService.generate_presigned_put_url(key)
        with open(local_path, 'rb') as file_obj:
            file_bytes = file_obj.read()
        request = Request(
            upload_url,
            data=file_bytes,
            headers={
                'Content-Length': str(len(file_bytes)),
            },
            method='PUT',
        )
        with urlopen(request, timeout=120) as response:
            response.read()
            if response.status not in (200, 201):
                raise ValueError(f'R2 上传失败：HTTP {response.status}')
        return {
            'key': key,
            'url': StorageService.build_public_url(key),
        }

    @staticmethod
    def generate_presigned_put_url(key):
        if not StorageService.is_storage_enabled():
            raise ValueError('Cloudflare R2 未配置')
        if not StorageService.is_valid_sku_key(key):
            raise ValueError('非法图片 Key')

        endpoint = (getattr(settings, 'R2_ENDPOINT', '') or '').strip().rstrip('/')
        parsed = urlparse(endpoint)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('R2_ENDPOINT 格式无效，应为 https://<accountid>.r2.cloudflarestorage.com')

        host = parsed.netloc
        base_path = parsed.path.rstrip('/')
        encoded_key = quote(key.lstrip('/'), safe='/~')
        canonical_uri = f"{base_path}/{settings.R2_BUCKET}/{encoded_key}" if base_path else f"/{settings.R2_BUCKET}/{encoded_key}"

        now = dt.datetime.utcnow()
        amz_date = now.strftime('%Y%m%dT%H%M%SZ')
        date_stamp = now.strftime('%Y%m%d')
        region = (getattr(settings, 'R2_REGION', 'auto') or 'auto').strip()
        service = 's3'
        credential_scope = f'{date_stamp}/{region}/{service}/aws4_request'
        expires = int(getattr(settings, 'R2_UPLOAD_EXPIRE', 900) or 900)

        query_params = {
            'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
            'X-Amz-Credential': f'{settings.R2_ACCESS_KEY_ID}/{credential_scope}',
            'X-Amz-Date': amz_date,
            'X-Amz-Expires': str(expires),
            'X-Amz-SignedHeaders': 'host',
        }
        canonical_querystring = urlencode(sorted(query_params.items()))
        canonical_headers = f'host:{host}\n'
        signed_headers = 'host'
        payload_hash = 'UNSIGNED-PAYLOAD'
        canonical_request = '\n'.join([
            'PUT',
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers,
            payload_hash,
        ])
        string_to_sign = '\n'.join([
            'AWS4-HMAC-SHA256',
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode('utf-8')).hexdigest(),
        ])
        signing_key = StorageService._get_signature_key(settings.R2_SECRET_ACCESS_KEY, date_stamp, region, service)
        signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
        return f"{endpoint}{canonical_uri}?{canonical_querystring}&X-Amz-Signature={signature}"

    @staticmethod
    def _sign(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    @staticmethod
    def _get_signature_key(secret_key, date_stamp, region_name, service_name):
        k_date = StorageService._sign(('AWS4' + secret_key).encode('utf-8'), date_stamp)
        k_region = StorageService._sign(k_date, region_name)
        k_service = StorageService._sign(k_region, service_name)
        return StorageService._sign(k_service, 'aws4_request')

    # Legacy wrappers kept temporarily to reduce spread of change.
    @staticmethod
    def is_qiniu_enabled():
        return StorageService.is_storage_enabled()

    @staticmethod
    def get_qiniu_status():
        return StorageService.get_storage_status()

    @staticmethod
    def generate_upload_token(key):
        raise ValueError('Cloudflare R2 不使用 upload_token，请改用预签名上传 URL')

    @staticmethod
    def upload_local_file_to_qiniu(local_path, key):
        return StorageService.upload_local_file(local_path, key)
