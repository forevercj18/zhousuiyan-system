"""
初始化数据脚本
创建管理员账号、系统设置、演示数据等
"""
import os
import sys
import django

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.core.models import SystemSettings, SKU, Part
from decimal import Decimal

User = get_user_model()


def create_users():
    """创建用户"""
    print("创建用户...")

    users_data = [
        {
            'username': 'admin',
            'password': 'admin123',
            'email': 'admin@example.com',
            'full_name': '系统管理员',
            'role': 'admin',
            'phone': '13800138000',
            'is_superuser': True,
            'is_staff': True,
        },
        {
            'username': 'manager_zhang',
            'password': 'zhang123',
            'email': 'zhang@example.com',
            'full_name': '张经理',
            'role': 'manager',
            'phone': '13800138001',
        },
        {
            'username': 'warehouse_li',
            'password': 'li123',
            'email': 'li@example.com',
            'full_name': '李主管',
            'role': 'warehouse_manager',
            'phone': '13800138002',
        },
        {
            'username': 'staff_wang',
            'password': 'wang123',
            'email': 'wang@example.com',
            'full_name': '王操作员',
            'role': 'warehouse_staff',
            'phone': '13800138003',
        },
        {
            'username': 'cs_liu',
            'password': 'liu123',
            'email': 'liu@example.com',
            'full_name': '刘客服',
            'role': 'customer_service',
            'phone': '13800138004',
        },
    ]

    for user_data in users_data:
        if not User.objects.filter(username=user_data['username']).exists():
            password = user_data.pop('password')
            user = User.objects.create(**user_data)
            user.set_password(password)
            user.save()
            print(f"  ✓ 创建用户: {user.username} ({user.get_role_display()})")
        else:
            print(f"  - 用户已存在: {user_data['username']}")


def create_system_settings():
    """创建系统设置"""
    print("\n创建系统设置...")

    settings_data = [
        {'key': 'ship_lead_days', 'value': '2', 'description': '发货提前天数'},
        {'key': 'return_offset_days', 'value': '1', 'description': '回收延后天数'},
        {'key': 'buffer_days', 'value': '1', 'description': '缓冲天数'},
        {'key': 'max_transfer_gap_days', 'value': '3', 'description': '最大转寄间隔天数'},
    ]

    for setting in settings_data:
        obj, created = SystemSettings.objects.get_or_create(
            key=setting['key'],
            defaults={'value': setting['value'], 'description': setting['description']}
        )
        if created:
            print(f"  ✓ 创建设置: {setting['key']} = {setting['value']}")
        else:
            print(f"  - 设置已存在: {setting['key']}")


def create_skus():
    """创建SKU"""
    print("\n创建SKU...")

    skus_data = [
        {
            'code': 'SKU001',
            'name': '森林主题套餐',
            'category': '主题套餐',
            'rental_price': Decimal('1200.00'),
            'deposit': Decimal('500.00'),
            'stock': 5,
            'description': '包含森林背景板、动物装饰、绿植等',
        },
        {
            'code': 'SKU002',
            'name': '海洋主题套餐',
            'category': '主题套餐',
            'rental_price': Decimal('1500.00'),
            'deposit': Decimal('600.00'),
            'stock': 4,
            'description': '包含海洋背景板、海洋生物装饰、蓝色气球等',
        },
        {
            'code': 'SKU003',
            'name': '公主主题套餐',
            'category': '主题套餐',
            'rental_price': Decimal('1800.00'),
            'deposit': Decimal('800.00'),
            'stock': 3,
            'description': '包含公主城堡背景板、皇冠、粉色装饰等',
        },
        {
            'code': 'SKU004',
            'name': '恐龙主题套餐',
            'category': '主题套餐',
            'rental_price': Decimal('1600.00'),
            'deposit': Decimal('700.00'),
            'stock': 4,
            'description': '包含恐龙模型、侏罗纪背景板、绿色装饰等',
        },
        {
            'code': 'SKU005',
            'name': '太空主题套餐',
            'category': '主题套餐',
            'rental_price': Decimal('2000.00'),
            'deposit': Decimal('900.00'),
            'stock': 2,
            'description': '包含星空背景板、火箭模型、宇航员装饰等',
        },
    ]

    for sku_data in skus_data:
        obj, created = SKU.objects.get_or_create(
            code=sku_data['code'],
            defaults=sku_data
        )
        if created:
            print(f"  ✓ 创建SKU: {sku_data['code']} - {sku_data['name']}")
        else:
            print(f"  - SKU已存在: {sku_data['code']}")


def create_parts():
    """创建部件"""
    print("\n创建部件...")

    parts_data = [
        {'name': '森林背景板', 'spec': '2m×3m', 'category': 'main', 'unit': '张', 'current_stock': 8, 'safety_stock': 3, 'location': 'A区-01'},
        {'name': '海洋背景板', 'spec': '2m×3m', 'category': 'main', 'unit': '张', 'current_stock': 6, 'safety_stock': 3, 'location': 'A区-02'},
        {'name': '公主城堡背景板', 'spec': '2.5m×3m', 'category': 'main', 'unit': '张', 'current_stock': 5, 'safety_stock': 2, 'location': 'A区-03'},
        {'name': '恐龙模型-大号', 'spec': '高度80cm', 'category': 'main', 'unit': '个', 'current_stock': 10, 'safety_stock': 4, 'location': 'B区-01'},
        {'name': '火箭模型', 'spec': '高度1.2m', 'category': 'main', 'unit': '个', 'current_stock': 4, 'safety_stock': 2, 'location': 'B区-02'},
        {'name': '气球套装-粉色', 'spec': '100个/包', 'category': 'accessory', 'unit': '包', 'current_stock': 25, 'safety_stock': 10, 'location': 'C区-01'},
        {'name': '气球套装-蓝色', 'spec': '100个/包', 'category': 'accessory', 'unit': '包', 'current_stock': 30, 'safety_stock': 10, 'location': 'C区-01'},
        {'name': '气球套装-混色', 'spec': '200个/包', 'category': 'accessory', 'unit': '包', 'current_stock': 15, 'safety_stock': 8, 'location': 'C区-01'},
        {'name': '背景墙支架', 'spec': '可调节高度', 'category': 'accessory', 'unit': '个', 'current_stock': 12, 'safety_stock': 5, 'location': 'D区-01'},
        {'name': '餐具套装-粉色', 'spec': '20人份', 'category': 'accessory', 'unit': '套', 'current_stock': 18, 'safety_stock': 8, 'location': 'E区-01'},
        {'name': '餐具套装-蓝色', 'spec': '20人份', 'category': 'accessory', 'unit': '套', 'current_stock': 15, 'safety_stock': 8, 'location': 'E区-01'},
        {'name': '一次性餐盘', 'spec': '10寸', 'category': 'consumable', 'unit': '个', 'current_stock': 500, 'safety_stock': 200, 'location': 'E区-02'},
        {'name': '一次性杯子', 'spec': '250ml', 'category': 'consumable', 'unit': '个', 'current_stock': 600, 'safety_stock': 200, 'location': 'E区-02'},
        {'name': '餐巾纸', 'spec': '100抽', 'category': 'consumable', 'unit': '包', 'current_stock': 450, 'safety_stock': 150, 'location': 'E区-03'},
    ]

    for part_data in parts_data:
        obj, created = Part.objects.get_or_create(
            name=part_data['name'],
            spec=part_data['spec'],
            defaults=part_data
        )
        if created:
            print(f"  ✓ 创建部件: {part_data['name']} ({part_data['spec']})")
        else:
            print(f"  - 部件已存在: {part_data['name']}")


def main():
    print("=" * 60)
    print("初始化数据")
    print("=" * 60)

    create_users()
    create_system_settings()
    create_skus()
    create_parts()

    print("\n" + "=" * 60)
    print("初始化完成！")
    print("=" * 60)
    print("\n默认管理员账号：")
    print("  用户名: admin")
    print("  密码: admin123")
    print("\n其他测试账号：")
    print("  manager_zhang / zhang123 (业务经理)")
    print("  warehouse_li / li123 (仓库主管)")
    print("  staff_wang / wang123 (仓库操作员)")
    print("  cs_liu / liu123 (客服)")
    print("\n⚠️  请在生产环境中修改默认密码！")
    print("=" * 60)


if __name__ == '__main__':
    main()
