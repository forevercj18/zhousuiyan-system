#!/usr/bin/env python
"""创建超级用户脚本"""
import os
import sys
import django

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

# 检查是否已存在超级用户
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser(
        username='admin',
        email='admin@example.com',
        password='admin123'
    )
    print('超级用户创建成功: admin / admin123')
else:
    print('超级用户已存在')
