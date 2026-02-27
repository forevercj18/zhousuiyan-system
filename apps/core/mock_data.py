"""
Mock数据模块 - 用于演示系统功能
包含：订单、SKU、部件库存、出入库流水、排期等数据
"""
from datetime import datetime, timedelta
from decimal import Decimal

# SKU数据（套餐）
SKUS = [
    {
        'id': 1,
        'code': 'SKU001',
        'name': '森林主题套餐',
        'category': '主题套餐',
        'rental_price': Decimal('1200.00'),
        'deposit': Decimal('500.00'),
        'stock': 5,
        'available': 3,
        'description': '包含森林背景板、动物装饰、绿植等',
        'parts': [
            {'part_id': 1, 'quantity': 1},
            {'part_id': 2, 'quantity': 10},
            {'part_id': 3, 'quantity': 5},
        ]
    },
    {
        'id': 2,
        'code': 'SKU002',
        'name': '海洋主题套餐',
        'category': '主题套餐',
        'rental_price': Decimal('1500.00'),
        'deposit': Decimal('600.00'),
        'stock': 4,
        'available': 2,
        'description': '包含海洋背景板、海洋生物装饰、蓝色气球等',
        'parts': [
            {'part_id': 4, 'quantity': 1},
            {'part_id': 5, 'quantity': 15},
            {'part_id': 6, 'quantity': 20},
        ]
    },
    {
        'id': 3,
        'code': 'SKU003',
        'name': '公主主题套餐',
        'category': '主题套餐',
        'rental_price': Decimal('1800.00'),
        'deposit': Decimal('800.00'),
        'stock': 3,
        'available': 1,
        'description': '包含城堡背景板、皇冠装饰、粉色气球等',
        'parts': [
            {'part_id': 7, 'quantity': 1},
            {'part_id': 8, 'quantity': 8},
            {'part_id': 9, 'quantity': 30},
        ]
    },
    {
        'id': 4,
        'code': 'SKU004',
        'name': '恐龙主题套餐',
        'category': '主题套餐',
        'rental_price': Decimal('1300.00'),
        'deposit': Decimal('550.00'),
        'stock': 4,
        'available': 4,
        'description': '包含恐龙背景板、恐龙模型、绿色装饰等',
        'parts': [
            {'part_id': 10, 'quantity': 1},
            {'part_id': 11, 'quantity': 6},
            {'part_id': 12, 'quantity': 10},
        ]
    },
    {
        'id': 5,
        'code': 'SKU005',
        'name': '气球拱门',
        'category': '单品',
        'rental_price': Decimal('300.00'),
        'deposit': Decimal('100.00'),
        'stock': 10,
        'available': 7,
        'description': '彩色气球拱门，可定制颜色',
        'parts': [
            {'part_id': 13, 'quantity': 100},
            {'part_id': 14, 'quantity': 1},
        ]
    },
]

# 部件库存数据
PARTS_INVENTORY = [
    {'id': 1, 'code': 'PART001', 'name': '森林背景板', 'category': '背景板', 'unit': '块', 'stock': 5, 'available': 3, 'min_stock': 2, 'unit_cost': Decimal('200.00')},
    {'id': 2, 'code': 'PART002', 'name': '动物装饰-小鹿', 'category': '装饰品', 'unit': '个', 'stock': 50, 'available': 30, 'min_stock': 20, 'unit_cost': Decimal('15.00')},
    {'id': 3, 'code': 'PART003', 'name': '仿真绿植', 'category': '装饰品', 'unit': '盆', 'stock': 30, 'available': 15, 'min_stock': 10, 'unit_cost': Decimal('25.00')},
    {'id': 4, 'code': 'PART004', 'name': '海洋背景板', 'category': '背景板', 'unit': '块', 'stock': 4, 'available': 2, 'min_stock': 2, 'unit_cost': Decimal('220.00')},
    {'id': 5, 'code': 'PART005', 'name': '海洋生物装饰', 'category': '装饰品', 'unit': '个', 'stock': 60, 'available': 45, 'min_stock': 30, 'unit_cost': Decimal('12.00')},
    {'id': 6, 'code': 'PART006', 'name': '蓝色气球', 'category': '气球', 'unit': '个', 'stock': 500, 'available': 380, 'min_stock': 200, 'unit_cost': Decimal('1.50')},
    {'id': 7, 'code': 'PART007', 'name': '城堡背景板', 'category': '背景板', 'unit': '块', 'stock': 3, 'available': 1, 'min_stock': 2, 'unit_cost': Decimal('250.00')},
    {'id': 8, 'code': 'PART008', 'name': '皇冠装饰', 'category': '装饰品', 'unit': '个', 'stock': 24, 'available': 16, 'min_stock': 10, 'unit_cost': Decimal('18.00')},
    {'id': 9, 'code': 'PART009', 'name': '粉色气球', 'category': '气球', 'unit': '个', 'stock': 600, 'available': 510, 'min_stock': 200, 'unit_cost': Decimal('1.50')},
    {'id': 10, 'code': 'PART010', 'name': '恐龙背景板', 'category': '背景板', 'unit': '块', 'stock': 4, 'available': 4, 'min_stock': 2, 'unit_cost': Decimal('210.00')},
    {'id': 11, 'code': 'PART011', 'name': '恐龙模型', 'category': '装饰品', 'unit': '个', 'stock': 24, 'available': 24, 'min_stock': 12, 'unit_cost': Decimal('30.00')},
    {'id': 12, 'code': 'PART012', 'name': '绿色装饰藤蔓', 'category': '装饰品', 'unit': '条', 'stock': 40, 'available': 40, 'min_stock': 20, 'unit_cost': Decimal('8.00')},
    {'id': 13, 'code': 'PART013', 'name': '彩色气球', 'category': '气球', 'unit': '个', 'stock': 1000, 'available': 700, 'min_stock': 500, 'unit_cost': Decimal('1.20')},
    {'id': 14, 'code': 'PART014', 'name': '气球拱门支架', 'category': '支架', 'unit': '套', 'stock': 10, 'available': 7, 'min_stock': 5, 'unit_cost': Decimal('80.00')},
]

# 订单数据
ORDERS = [
    {
        'id': 1,
        'order_no': 'ORD20240201001',
        'customer_name': '张女士',
        'customer_phone': '13800138001',
        'event_date': '2024-03-15',
        'event_address': '上海市浦东新区世纪大道1号',
        'status': 'confirmed',
        'total_amount': Decimal('1200.00'),
        'deposit_paid': Decimal('500.00'),
        'balance': Decimal('700.00'),
        'created_at': '2024-02-01 10:30:00',
        'items': [
            {'sku_id': 1, 'sku_name': '森林主题套餐', 'quantity': 1, 'rental_price': Decimal('1200.00')}
        ],
        'notes': '客户要求提前一天送达'
    },
    {
        'id': 2,
        'order_no': 'ORD20240205002',
        'customer_name': '李先生',
        'customer_phone': '13900139002',
        'event_date': '2024-03-20',
        'event_address': '上海市徐汇区淮海中路500号',
        'status': 'pending',
        'total_amount': Decimal('1500.00'),
        'deposit_paid': Decimal('0.00'),
        'balance': Decimal('1500.00'),
        'created_at': '2024-02-05 14:20:00',
        'items': [
            {'sku_id': 2, 'sku_name': '海洋主题套餐', 'quantity': 1, 'rental_price': Decimal('1500.00')}
        ],
        'notes': ''
    },
    {
        'id': 3,
        'order_no': 'ORD20240210003',
        'customer_name': '王女士',
        'customer_phone': '13700137003',
        'event_date': '2024-03-25',
        'event_address': '上海市静安区南京西路1000号',
        'status': 'confirmed',
        'total_amount': Decimal('2100.00'),
        'deposit_paid': Decimal('900.00'),
        'balance': Decimal('1200.00'),
        'created_at': '2024-02-10 09:15:00',
        'items': [
            {'sku_id': 3, 'sku_name': '公主主题套餐', 'quantity': 1, 'rental_price': Decimal('1800.00')},
            {'sku_id': 5, 'sku_name': '气球拱门', 'quantity': 1, 'rental_price': Decimal('300.00')}
        ],
        'notes': '需要粉色系为主'
    },
    {
        'id': 4,
        'order_no': 'ORD20240215004',
        'customer_name': '赵先生',
        'customer_phone': '13600136004',
        'event_date': '2024-04-05',
        'event_address': '上海市黄浦区人民广场',
        'status': 'delivered',
        'total_amount': Decimal('1300.00'),
        'deposit_paid': Decimal('550.00'),
        'balance': Decimal('750.00'),
        'created_at': '2024-02-15 16:45:00',
        'items': [
            {'sku_id': 4, 'sku_name': '恐龙主题套餐', 'quantity': 1, 'rental_price': Decimal('1300.00')}
        ],
        'notes': '已送达，等待活动结束回收'
    },
    {
        'id': 5,
        'order_no': 'ORD20240220005',
        'customer_name': '陈女士',
        'customer_phone': '13500135005',
        'event_date': '2024-02-18',
        'event_address': '上海市长宁区中山公园',
        'status': 'completed',
        'total_amount': Decimal('1200.00'),
        'deposit_paid': Decimal('500.00'),
        'balance': Decimal('0.00'),
        'created_at': '2024-02-01 11:00:00',
        'items': [
            {'sku_id': 1, 'sku_name': '森林主题套餐', 'quantity': 1, 'rental_price': Decimal('1200.00')}
        ],
        'notes': '已完成，押金已退'
    },
]

# 出入库流水数据
PARTS_MOVEMENTS = [
    {
        'id': 1,
        'movement_no': 'OUT20240215001',
        'type': 'out',
        'related_order': 'ORD20240215004',
        'movement_date': '2024-02-15',
        'operator': 'admin',
        'status': 'completed',
        'items': [
            {'part_id': 10, 'part_name': '恐龙背景板', 'quantity': 1},
            {'part_id': 11, 'part_name': '恐龙模型', 'quantity': 6},
            {'part_id': 12, 'part_name': '绿色装饰藤蔓', 'quantity': 10},
        ],
        'notes': '订单ORD20240215004出库'
    },
    {
        'id': 2,
        'movement_no': 'IN20240220001',
        'type': 'in',
        'related_order': 'ORD20240220005',
        'movement_date': '2024-02-20',
        'operator': 'admin',
        'status': 'completed',
        'items': [
            {'part_id': 1, 'part_name': '森林背景板', 'quantity': 1},
            {'part_id': 2, 'part_name': '动物装饰-小鹿', 'quantity': 10},
            {'part_id': 3, 'part_name': '仿真绿植', 'quantity': 5},
        ],
        'notes': '订单ORD20240220005归还入库'
    },
    {
        'id': 3,
        'movement_no': 'PUR20240210001',
        'type': 'purchase',
        'related_order': '',
        'movement_date': '2024-02-10',
        'operator': 'admin',
        'status': 'completed',
        'items': [
            {'part_id': 6, 'part_name': '蓝色气球', 'quantity': 200},
            {'part_id': 9, 'part_name': '粉色气球', 'quantity': 200},
            {'part_id': 13, 'part_name': '彩色气球', 'quantity': 500},
        ],
        'notes': '采购补充气球库存'
    },
    {
        'id': 4,
        'movement_no': 'OUT20240201001',
        'type': 'out',
        'related_order': 'ORD20240201001',
        'movement_date': '2024-03-14',
        'operator': 'admin',
        'status': 'pending',
        'items': [
            {'part_id': 1, 'part_name': '森林背景板', 'quantity': 1},
            {'part_id': 2, 'part_name': '动物装饰-小鹿', 'quantity': 10},
            {'part_id': 3, 'part_name': '仿真绿植', 'quantity': 5},
        ],
        'notes': '订单ORD20240201001待出库'
    },
]

# 采购单数据
PURCHASE_ORDERS = [
    {
        'id': 1,
        'po_no': 'PO20240210001',
        'supplier': '上海装饰用品有限公司',
        'order_date': '2024-02-10',
        'expected_date': '2024-02-15',
        'status': 'received',
        'total_amount': Decimal('1350.00'),
        'items': [
            {'part_id': 6, 'part_name': '蓝色气球', 'quantity': 200, 'unit_price': Decimal('1.50'), 'subtotal': Decimal('300.00')},
            {'part_id': 9, 'part_name': '粉色气球', 'quantity': 200, 'unit_price': Decimal('1.50'), 'subtotal': Decimal('300.00')},
            {'part_id': 13, 'part_name': '彩色气球', 'quantity': 500, 'unit_price': Decimal('1.20'), 'subtotal': Decimal('600.00')},
            {'part_id': 14, 'part_name': '气球拱门支架', 'quantity': 2, 'unit_price': Decimal('75.00'), 'subtotal': Decimal('150.00')},
        ],
        'notes': '补充气球库存'
    },
    {
        'id': 2,
        'po_no': 'PO20240215001',
        'supplier': '梦幻派对道具厂',
        'order_date': '2024-02-15',
        'expected_date': '2024-02-25',
        'status': 'pending',
        'total_amount': Decimal('920.00'),
        'items': [
            {'part_id': 7, 'part_name': '城堡背景板', 'quantity': 2, 'unit_price': Decimal('250.00'), 'subtotal': Decimal('500.00')},
            {'part_id': 8, 'part_name': '皇冠装饰', 'quantity': 10, 'unit_price': Decimal('18.00'), 'subtotal': Decimal('180.00')},
            {'part_id': 11, 'part_name': '恐龙模型', 'quantity': 8, 'unit_price': Decimal('30.00'), 'subtotal': Decimal('240.00')},
        ],
        'notes': '补充主题套餐部件'
    },
]

# 排期数据（日历视图用）
def get_calendar_events():
    """生成日历事件数据"""
    events = []
    base_date = datetime.now()

    for order in ORDERS:
        if order['status'] in ['confirmed', 'delivered']:
            event_date = datetime.strptime(order['event_date'], '%Y-%m-%d')
            events.append({
                'id': order['id'],
                'title': f"{order['customer_name']} - {order['items'][0]['sku_name']}",
                'start': order['event_date'],
                'end': order['event_date'],
                'color': '#4CAF50' if order['status'] == 'confirmed' else '#2196F3',
                'order_no': order['order_no'],
                'status': order['status'],
            })

    return events

# 工作台统计数据
def get_dashboard_stats():
    """获取工作台统计数据"""
    today = datetime.now().date()

    pending_orders = len([o for o in ORDERS if o['status'] == 'pending'])
    confirmed_orders = len([o for o in ORDERS if o['status'] == 'confirmed'])
    delivered_orders = len([o for o in ORDERS if o['status'] == 'delivered'])

    low_stock_parts = len([p for p in PARTS_INVENTORY if p['available'] < p['min_stock']])

    total_revenue = sum([o['total_amount'] for o in ORDERS if o['status'] == 'completed'])
    pending_revenue = sum([o['balance'] for o in ORDERS if o['status'] in ['confirmed', 'delivered']])

    return {
        'pending_orders': pending_orders,
        'confirmed_orders': confirmed_orders,
        'delivered_orders': delivered_orders,
        'low_stock_parts': low_stock_parts,
        'total_revenue': total_revenue,
        'pending_revenue': pending_revenue,
        'total_orders': len(ORDERS),
        'total_skus': len(SKUS),
    }

# 审计日志数据
AUDIT_LOGS = [
    {'id': 1, 'timestamp': '2024-02-27 10:30:00', 'user': 'admin', 'action': '创建订单', 'target': 'ORD20240201001', 'details': '创建森林主题套餐订单'},
    {'id': 2, 'timestamp': '2024-02-27 11:15:00', 'user': 'admin', 'action': '确认订单', 'target': 'ORD20240201001', 'details': '订单已确认，收取押金500元'},
    {'id': 3, 'timestamp': '2024-02-27 14:20:00', 'user': 'admin', 'action': '创建采购单', 'target': 'PO20240210001', 'details': '采购气球及支架'},
    {'id': 4, 'timestamp': '2024-02-27 15:45:00', 'user': 'admin', 'action': '出库', 'target': 'OUT20240215001', 'details': '订单ORD20240215004出库'},
    {'id': 5, 'timestamp': '2024-02-27 16:30:00', 'user': 'admin', 'action': '入库', 'target': 'IN20240220001', 'details': '订单ORD20240220005归还入库'},
]

# 用户数据
USERS = [
    {
        'id': 1,
        'username': 'admin',
        'full_name': '系统管理员',
        'role': 'admin',
        'role_display': '超级管理员',
        'email': 'admin@example.com',
        'phone': '13800138000',
        'is_active': True,
        'last_login': '2026-02-27 09:30:00'
    },
    {
        'id': 2,
        'username': 'manager_zhang',
        'full_name': '张经理',
        'role': 'manager',
        'role_display': '业务经理',
        'email': 'zhang@example.com',
        'phone': '13800138001',
        'is_active': True,
        'last_login': '2026-02-27 08:45:00'
    },
    {
        'id': 3,
        'username': 'warehouse_li',
        'full_name': '李主管',
        'role': 'warehouse-manager',
        'role_display': '仓库主管',
        'email': 'li@example.com',
        'phone': '13800138002',
        'is_active': True,
        'last_login': '2026-02-27 07:20:00'
    },
    {
        'id': 4,
        'username': 'staff_wang',
        'full_name': '王操作员',
        'role': 'warehouse-staff',
        'role_display': '仓库操作员',
        'email': 'wang@example.com',
        'phone': '13800138003',
        'is_active': True,
        'last_login': '2026-02-26 18:30:00'
    },
    {
        'id': 5,
        'username': 'cs_liu',
        'full_name': '刘客服',
        'role': 'customer-service',
        'role_display': '客服',
        'email': 'liu@example.com',
        'phone': '13800138004',
        'is_active': True,
        'last_login': '2026-02-27 10:15:00'
    },
    {
        'id': 6,
        'username': 'staff_zhao',
        'full_name': '赵操作员',
        'role': 'warehouse-staff',
        'role_display': '仓库操作员',
        'email': 'zhao@example.com',
        'phone': '13800138005',
        'is_active': False,
        'last_login': '2026-02-20 16:00:00'
    },
]
