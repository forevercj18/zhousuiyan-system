#!/usr/bin/env python
"""创建超级用户脚本"""
import os
import sys
import django
from django.core.management.utils import get_random_secret_key

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

username = os.getenv("ADMIN_USERNAME", "admin")
email = os.getenv("ADMIN_EMAIL", "admin@example.com")
password = os.getenv("ADMIN_PASSWORD")

if not password:
    password = get_random_secret_key()
    print("未提供 ADMIN_PASSWORD，已生成随机初始密码。")

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(
        username=username,
        email=email,
        password=password
    )
    print(f"超级用户创建成功: {username}")
    print(f"初始密码: {password}")
else:
    print(f"超级用户已存在: {username}")
