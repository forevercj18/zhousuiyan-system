"""
业务工具函数
包含：系统设置获取、库存校验、排期计算、转寄匹配等
"""
from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher
import math
import re
from django.db.models import Sum, Q, Count
from .models import SystemSettings, Order, OrderItem, SKU, Transfer, TransferAllocation


def get_system_settings():
    """获取系统设置（返回字典）"""
    settings = {}
    for item in SystemSettings.objects.all():
        try:
            # 尝试转换为整数
            settings[item.key] = int(item.value)
        except ValueError:
            settings[item.key] = item.value

    # 设置默认值
    settings.setdefault('ship_lead_days', 2)
    settings.setdefault('return_offset_days', 1)
    settings.setdefault('buffer_days', 1)
    settings.setdefault('max_transfer_gap_days', 3)
    settings.setdefault('warehouse_sender_name', '仓库发货员')
    settings.setdefault('warehouse_sender_phone', '')
    settings.setdefault('warehouse_sender_address', '仓库地址未配置')

    return settings


def check_sku_availability(sku_id, event_date, quantity=1, exclude_order_id=None, rental_days=1):
    """
    检查SKU在指定日期的可用性

    Args:
        sku_id: SKU ID
        event_date: 活动日期
        quantity: 需要的数量
        exclude_order_id: 排除的订单ID（用于编辑订单时）

    Returns:
        dict: {
            'available': bool,  # 是否可用
            'current_stock': int,  # 总库存
            'occupied': int,  # 已占用
            'available_count': int,  # 可用数量
            'message': str  # 提示信息
        }
    """
    try:
        sku = SKU.objects.get(id=sku_id, is_active=True)
    except SKU.DoesNotExist:
        return {
            'available': False,
            'current_stock': 0,
            'occupied': 0,
            'available_count': 0,
            'message': 'SKU不存在或已禁用'
        }

    # 仓库实时可用库存：仅按“当前未回仓”的占用计算，不按预定日期做时间复用
    query = Q(status__in=['pending', 'confirmed', 'delivered', 'in_use'])

    if exclude_order_id:
        query &= ~Q(id=exclude_order_id)

    active_orders = Order.objects.filter(query)
    # 仓库占用 = 订单明细数量 - 已锁定/已消耗转寄数量
    occupied_raw = OrderItem.objects.filter(
        order__in=active_orders,
        sku_id=sku_id
    ).aggregate(total=Sum('quantity'))['total'] or 0
    transfer_allocated = TransferAllocation.objects.filter(
        target_order__in=active_orders,
        sku_id=sku_id,
        status__in=['locked', 'consumed']
    ).aggregate(total=Sum('quantity'))['total'] or 0
    occupied = max(occupied_raw - transfer_allocated, 0)

    raw_available_count = sku.stock - occupied
    available_count = max(raw_available_count, 0)
    overbooked_count = max(-raw_available_count, 0)

    return {
        'available': raw_available_count >= quantity,
        'current_stock': sku.stock,
        'occupied': occupied,
        'available_count': available_count,
        'overbooked_count': overbooked_count,
        'message': (
            f'仓库可用：{available_count}/{sku.stock}（占用：{occupied}）'
            if raw_available_count >= quantity
            else (
                f'仓库库存不足，仅剩{available_count}套（占用：{occupied}）'
                if overbooked_count == 0
                else f'仓库库存不足，当前超占{overbooked_count}套（占用：{occupied}）'
            )
        )
    }


CITY_COORDS = {
    '北京市': (39.9042, 116.4074),
    '上海市': (31.2304, 121.4737),
    '天津市': (39.0842, 117.2009),
    '重庆市': (29.5630, 106.5516),
    '石家庄市': (38.0428, 114.5149),
    '太原市': (37.8706, 112.5489),
    '呼和浩特市': (40.8426, 111.7492),
    '沈阳市': (41.8057, 123.4315),
    '长春市': (43.8171, 125.3235),
    '哈尔滨市': (45.8038, 126.5349),
    '南京市': (32.0603, 118.7969),
    '杭州市': (30.2741, 120.1551),
    '合肥市': (31.8206, 117.2290),
    '福州市': (26.0745, 119.2965),
    '厦门市': (24.4798, 118.0894),
    '莆田市': (25.4541, 119.0076),
    '三明市': (26.2638, 117.6392),
    '漳州市': (24.5130, 117.6618),
    '南平市': (26.6419, 118.1785),
    '龙岩市': (25.0751, 117.0174),
    '宁德市': (26.6657, 119.5479),
    '泉州市': (24.8741, 118.6759),
    '南昌市': (28.6820, 115.8579),
    '济南市': (36.6512, 117.1201),
    '郑州市': (34.7466, 113.6254),
    '武汉市': (30.5928, 114.3055),
    '长沙市': (28.2282, 112.9388),
    '广州市': (23.1291, 113.2644),
    '珠海市': (22.2710, 113.5767),
    '汕头市': (23.3535, 116.6822),
    '佛山市': (23.0215, 113.1214),
    '江门市': (22.5787, 113.0819),
    '湛江市': (21.2707, 110.3594),
    '茂名市': (21.6633, 110.9252),
    '肇庆市': (23.0469, 112.4651),
    '惠州市': (23.1115, 114.4168),
    '梅州市': (24.2991, 116.1176),
    '汕尾市': (22.7862, 115.3751),
    '河源市': (23.7463, 114.6978),
    '阳江市': (21.8583, 111.9822),
    '清远市': (23.6820, 113.0560),
    '东莞市': (23.0207, 113.7518),
    '中山市': (22.5176, 113.3928),
    '潮州市': (23.6567, 116.6226),
    '韶关市': (24.8104, 113.5972),
    '深圳市': (22.5431, 114.0579),
    '揭阳市': (23.5497, 116.3728),
    '云浮市': (22.9152, 112.0445),
    '南宁市': (22.8170, 108.3669),
    '海口市': (20.0442, 110.1999),
    '成都市': (30.5728, 104.0668),
    '贵阳市': (26.6470, 106.6302),
    '昆明市': (25.0389, 102.7183),
    '拉萨市': (29.6525, 91.1721),
    '西安市': (34.3416, 108.9398),
    '咸阳市': (34.3296, 108.7093),
    '铜川市': (34.8967, 108.9451),
    '宝鸡市': (34.3619, 107.2373),
    '渭南市': (34.4994, 109.5102),
    '延安市': (36.5853, 109.4897),
    '汉中市': (33.0676, 107.0238),
    '榆林市': (38.2852, 109.7341),
    '安康市': (32.6847, 109.0293),
    '商洛市': (33.8739, 109.9186),
    '兰州市': (36.0611, 103.8343),
    '西宁市': (36.6171, 101.7782),
    '银川市': (38.4872, 106.2309),
    '乌鲁木齐市': (43.8256, 87.6168),
    '香港特别行政区': (22.3193, 114.1694),
    '澳门特别行政区': (22.1987, 113.5439),
    '台北市': (25.0330, 121.5654),
}

PROVINCE_TO_CAPITAL = {
    '北京市': '北京市',
    '上海市': '上海市',
    '天津市': '天津市',
    '重庆市': '重庆市',
    '河北省': '石家庄市',
    '山西省': '太原市',
    '内蒙古自治区': '呼和浩特市',
    '辽宁省': '沈阳市',
    '吉林省': '长春市',
    '黑龙江省': '哈尔滨市',
    '江苏省': '南京市',
    '浙江省': '杭州市',
    '安徽省': '合肥市',
    '福建省': '福州市',
    '江西省': '南昌市',
    '山东省': '济南市',
    '河南省': '郑州市',
    '湖北省': '武汉市',
    '湖南省': '长沙市',
    '广东省': '广州市',
    '广西壮族自治区': '南宁市',
    '海南省': '海口市',
    '四川省': '成都市',
    '贵州省': '贵阳市',
    '云南省': '昆明市',
    '西藏自治区': '拉萨市',
    '陕西省': '西安市',
    '甘肃省': '兰州市',
    '青海省': '西宁市',
    '宁夏回族自治区': '银川市',
    '新疆维吾尔自治区': '乌鲁木齐市',
    '香港特别行政区': '香港特别行政区',
    '澳门特别行政区': '澳门特别行政区',
    '台湾省': '台北市',
}

CITY_TO_PROVINCE = {
    v: k for k, v in PROVINCE_TO_CAPITAL.items()
}
CITY_TO_PROVINCE.update({
    '深圳市': '广东省',
    '珠海市': '广东省',
    '汕头市': '广东省',
    '佛山市': '广东省',
    '江门市': '广东省',
    '湛江市': '广东省',
    '茂名市': '广东省',
    '肇庆市': '广东省',
    '惠州市': '广东省',
    '梅州市': '广东省',
    '汕尾市': '广东省',
    '河源市': '广东省',
    '阳江市': '广东省',
    '清远市': '广东省',
    '东莞市': '广东省',
    '中山市': '广东省',
    '潮州市': '广东省',
    '韶关市': '广东省',
    '揭阳市': '广东省',
    '云浮市': '广东省',
    '厦门市': '福建省',
    '莆田市': '福建省',
    '三明市': '福建省',
    '漳州市': '福建省',
    '南平市': '福建省',
    '龙岩市': '福建省',
    '宁德市': '福建省',
    '泉州市': '福建省',
    '咸阳市': '陕西省',
    '铜川市': '陕西省',
    '宝鸡市': '陕西省',
    '渭南市': '陕西省',
    '延安市': '陕西省',
    '汉中市': '陕西省',
    '榆林市': '陕西省',
    '安康市': '陕西省',
    '商洛市': '陕西省',
})

CITY_ALIASES = {
    '北京': '北京市',
    '上海': '上海市',
    '天津': '天津市',
    '重庆': '重庆市',
    '广州': '广州市',
    '珠海': '珠海市',
    '汕头': '汕头市',
    '佛山': '佛山市',
    '江门': '江门市',
    '湛江': '湛江市',
    '茂名': '茂名市',
    '肇庆': '肇庆市',
    '惠州': '惠州市',
    '梅州': '梅州市',
    '汕尾': '汕尾市',
    '河源': '河源市',
    '阳江': '阳江市',
    '清远': '清远市',
    '东莞': '东莞市',
    '中山': '中山市',
    '潮州': '潮州市',
    '深圳': '深圳市',
    '韶关': '韶关市',
    '揭阳': '揭阳市',
    '云浮': '云浮市',
    '福州': '福州市',
    '厦门': '厦门市',
    '莆田': '莆田市',
    '三明': '三明市',
    '漳州': '漳州市',
    '南平': '南平市',
    '龙岩': '龙岩市',
    '宁德': '宁德市',
    '泉州': '泉州市',
    '杭州': '杭州市',
    '南京': '南京市',
    '武汉': '武汉市',
    '长沙': '长沙市',
    '成都': '成都市',
    '西安': '西安市',
    '咸阳': '咸阳市',
    '铜川': '铜川市',
    '宝鸡': '宝鸡市',
    '渭南': '渭南市',
    '延安': '延安市',
    '汉中': '汉中市',
    '榆林': '榆林市',
    '安康': '安康市',
    '商洛': '商洛市',
}

PROVINCE_ALIASES = {
    '北京': '北京市',
    '上海': '上海市',
    '天津': '天津市',
    '重庆': '重庆市',
    '河北': '河北省',
    '山西': '山西省',
    '内蒙古': '内蒙古自治区',
    '辽宁': '辽宁省',
    '吉林': '吉林省',
    '黑龙江': '黑龙江省',
    '江苏': '江苏省',
    '浙江': '浙江省',
    '安徽': '安徽省',
    '福建': '福建省',
    '江西': '江西省',
    '山东': '山东省',
    '河南': '河南省',
    '湖北': '湖北省',
    '湖南': '湖南省',
    '广东': '广东省',
    '广西': '广西壮族自治区',
    '海南': '海南省',
    '四川': '四川省',
    '贵州': '贵州省',
    '云南': '云南省',
    '西藏': '西藏自治区',
    '陕西': '陕西省',
    '甘肃': '甘肃省',
    '青海': '青海省',
    '宁夏': '宁夏回族自治区',
    '新疆': '新疆维吾尔自治区',
    '香港': '香港特别行政区',
    '澳门': '澳门特别行政区',
    '台湾': '台湾省',
}

# 省 -> 地级市（含自治州/地区/盟）名称库（用于本地地址解析，不依赖外部API）
PROVINCE_CITY_NAMES = {
    '北京市': ['北京市'],
    '上海市': ['上海市'],
    '天津市': ['天津市'],
    '重庆市': ['重庆市'],
    '河北省': ['石家庄市', '唐山市', '秦皇岛市', '邯郸市', '邢台市', '保定市', '张家口市', '承德市', '沧州市', '廊坊市', '衡水市'],
    '山西省': ['太原市', '大同市', '阳泉市', '长治市', '晋城市', '朔州市', '晋中市', '运城市', '忻州市', '临汾市', '吕梁市'],
    '内蒙古自治区': ['呼和浩特市', '包头市', '乌海市', '赤峰市', '通辽市', '鄂尔多斯市', '呼伦贝尔市', '巴彦淖尔市', '乌兰察布市', '兴安盟', '锡林郭勒盟', '阿拉善盟'],
    '辽宁省': ['沈阳市', '大连市', '鞍山市', '抚顺市', '本溪市', '丹东市', '锦州市', '营口市', '阜新市', '辽阳市', '盘锦市', '铁岭市', '朝阳市', '葫芦岛市'],
    '吉林省': ['长春市', '吉林市', '四平市', '辽源市', '通化市', '白山市', '松原市', '白城市', '延边朝鲜族自治州'],
    '黑龙江省': ['哈尔滨市', '齐齐哈尔市', '鸡西市', '鹤岗市', '双鸭山市', '大庆市', '伊春市', '佳木斯市', '七台河市', '牡丹江市', '黑河市', '绥化市', '大兴安岭地区'],
    '江苏省': ['南京市', '无锡市', '徐州市', '常州市', '苏州市', '南通市', '连云港市', '淮安市', '盐城市', '扬州市', '镇江市', '泰州市', '宿迁市'],
    '浙江省': ['杭州市', '宁波市', '温州市', '嘉兴市', '湖州市', '绍兴市', '金华市', '衢州市', '舟山市', '台州市', '丽水市'],
    '安徽省': ['合肥市', '芜湖市', '蚌埠市', '淮南市', '马鞍山市', '淮北市', '铜陵市', '安庆市', '黄山市', '滁州市', '阜阳市', '宿州市', '六安市', '亳州市', '池州市', '宣城市'],
    '福建省': ['福州市', '厦门市', '莆田市', '三明市', '泉州市', '漳州市', '南平市', '龙岩市', '宁德市'],
    '江西省': ['南昌市', '景德镇市', '萍乡市', '九江市', '新余市', '鹰潭市', '赣州市', '吉安市', '宜春市', '抚州市', '上饶市'],
    '山东省': ['济南市', '青岛市', '淄博市', '枣庄市', '东营市', '烟台市', '潍坊市', '济宁市', '泰安市', '威海市', '日照市', '临沂市', '德州市', '聊城市', '滨州市', '菏泽市'],
    '河南省': ['郑州市', '开封市', '洛阳市', '平顶山市', '安阳市', '鹤壁市', '新乡市', '焦作市', '濮阳市', '许昌市', '漯河市', '三门峡市', '南阳市', '商丘市', '信阳市', '周口市', '驻马店市', '济源市'],
    '湖北省': ['武汉市', '黄石市', '十堰市', '宜昌市', '襄阳市', '鄂州市', '荆门市', '孝感市', '荆州市', '黄冈市', '咸宁市', '随州市', '恩施土家族苗族自治州'],
    '湖南省': ['长沙市', '株洲市', '湘潭市', '衡阳市', '邵阳市', '岳阳市', '常德市', '张家界市', '益阳市', '郴州市', '永州市', '怀化市', '娄底市', '湘西土家族苗族自治州'],
    '广东省': ['广州市', '深圳市', '珠海市', '汕头市', '佛山市', '韶关市', '湛江市', '肇庆市', '江门市', '茂名市', '惠州市', '梅州市', '汕尾市', '河源市', '阳江市', '清远市', '东莞市', '中山市', '潮州市', '揭阳市', '云浮市'],
    '广西壮族自治区': ['南宁市', '柳州市', '桂林市', '梧州市', '北海市', '防城港市', '钦州市', '贵港市', '玉林市', '百色市', '贺州市', '河池市', '来宾市', '崇左市'],
    '海南省': ['海口市', '三亚市', '三沙市', '儋州市'],
    '四川省': ['成都市', '自贡市', '攀枝花市', '泸州市', '德阳市', '绵阳市', '广元市', '遂宁市', '内江市', '乐山市', '南充市', '眉山市', '宜宾市', '广安市', '达州市', '雅安市', '巴中市', '资阳市', '阿坝藏族羌族自治州', '甘孜藏族自治州', '凉山彝族自治州'],
    '贵州省': ['贵阳市', '六盘水市', '遵义市', '安顺市', '毕节市', '铜仁市', '黔西南布依族苗族自治州', '黔东南苗族侗族自治州', '黔南布依族苗族自治州'],
    '云南省': ['昆明市', '曲靖市', '玉溪市', '保山市', '昭通市', '丽江市', '普洱市', '临沧市', '楚雄彝族自治州', '红河哈尼族彝族自治州', '文山壮族苗族自治州', '西双版纳傣族自治州', '大理白族自治州', '德宏傣族景颇族自治州', '怒江傈僳族自治州', '迪庆藏族自治州'],
    '西藏自治区': ['拉萨市', '日喀则市', '昌都市', '林芝市', '山南市', '那曲市', '阿里地区'],
    '陕西省': ['西安市', '铜川市', '宝鸡市', '咸阳市', '渭南市', '延安市', '汉中市', '榆林市', '安康市', '商洛市'],
    '甘肃省': ['兰州市', '嘉峪关市', '金昌市', '白银市', '天水市', '武威市', '张掖市', '平凉市', '酒泉市', '庆阳市', '定西市', '陇南市', '临夏回族自治州', '甘南藏族自治州'],
    '青海省': ['西宁市', '海东市', '海北藏族自治州', '黄南藏族自治州', '海南藏族自治州', '果洛藏族自治州', '玉树藏族自治州', '海西蒙古族藏族自治州'],
    '宁夏回族自治区': ['银川市', '石嘴山市', '吴忠市', '固原市', '中卫市'],
    '新疆维吾尔自治区': ['乌鲁木齐市', '克拉玛依市', '吐鲁番市', '哈密市', '昌吉回族自治州', '博尔塔拉蒙古自治州', '巴音郭楞蒙古自治州', '阿克苏地区', '克孜勒苏柯尔克孜自治州', '喀什地区', '和田地区', '伊犁哈萨克自治州', '塔城地区', '阿勒泰地区'],
    '香港特别行政区': ['香港特别行政区'],
    '澳门特别行政区': ['澳门特别行政区'],
    '台湾省': ['台北市', '新北市', '桃园市', '台中市', '台南市', '高雄市', '基隆市', '新竹市', '嘉义市'],
}


def _city_short_alias(city):
    if not city:
        return None
    for suffix in ['特别行政区', '自治州', '地区', '自治县', '盟', '州', '市']:
        if city.endswith(suffix):
            short = city[:-len(suffix)]
            return short if short else None
    return city


for _province_name, _city_list in PROVINCE_CITY_NAMES.items():
    for _city_name in _city_list:
        CITY_TO_PROVINCE.setdefault(_city_name, _province_name)
        _short = _city_short_alias(_city_name)
        if _short and _short not in CITY_ALIASES:
            CITY_ALIASES[_short] = _city_name


def _normalize_address_text(address):
    text = (address or '').strip()
    if not text:
        return ''
    text = re.sub(r'\s+', '', text)
    text = text.replace('省份', '省').replace('城市', '市')
    return text


def _normalize_city_name(city):
    if not city:
        return None
    city = city.strip()
    if city in CITY_COORDS:
        return city
    if city in CITY_ALIASES:
        return CITY_ALIASES[city]
    if not city.endswith(('市', '州', '地区', '盟')):
        candidate = f'{city}市'
        if candidate in CITY_COORDS:
            return candidate
    return city if city in CITY_COORDS else None


def _normalize_city_display_name(city):
    """用于展示与省市归属判断，不要求必须存在坐标。"""
    if not city:
        return None
    city = city.strip()
    if city in CITY_ALIASES:
        return CITY_ALIASES[city]
    if city.endswith(('市', '州', '地区', '盟')):
        return city
    # 无后缀时按“市”补全用于展示
    return f'{city}市'


def _find_city_from_text(text):
    if not text:
        return None
    for alias, standard in CITY_ALIASES.items():
        if alias in text:
            return standard
    for city in CITY_COORDS.keys():
        if city in text:
            return city
        short = city[:-1] if city.endswith('市') else city
        if short and short in text:
            return city
    return None


def _extract_province_city(address):
    raw = _normalize_address_text(address)
    if not raw:
        return None, None, 'low'
    province_match = re.search(r'(北京市|上海市|天津市|重庆市|[^省]+省|[^区]+自治区|香港特别行政区|澳门特别行政区|台湾省)', raw)
    province = province_match.group(1) if province_match else None
    if not province:
        for alias, full_name in PROVINCE_ALIASES.items():
            if raw.startswith(alias):
                province = full_name
                break
    city_match = re.search(r'([^省市区县]+市|[^省市区县]+自治州|[^省市区县]+州|[^省市区县]+地区|[^省市区县]+盟)', raw)
    city = _normalize_city_display_name(city_match.group(1)) if city_match else None
    inferred_city_from_text = _normalize_city_display_name(_find_city_from_text(raw))
    if city and not _normalize_city_name(city) and inferred_city_from_text:
        # 例如“泉州晋江”被提成“泉州晋江市”时，优先纠偏为文本中可识别城市（泉州市）
        city = inferred_city_from_text
    if not city:
        city = inferred_city_from_text
    if province in ('北京市', '上海市', '天津市', '重庆市') and not city:
        city = province
    # 只有在完全提取不到城市时，才降级到省会
    if not city and province in PROVINCE_TO_CAPITAL:
        city = PROVINCE_TO_CAPITAL[province]
        return province, city, 'low'
    if city and not province:
        province = CITY_TO_PROVINCE.get(city)
        return province, city, 'medium'
    if city and province:
        inferred = CITY_TO_PROVINCE.get(city)
        if inferred and inferred != province:
            province = inferred
            return province, city, 'low'
        return province, city, 'high'
    return province, city, 'low'


def _resolve_city_coord(address):
    province, city, confidence = _extract_province_city(address)
    city_for_coord = _normalize_city_name(city)
    if not city_for_coord:
        inferred = _find_city_from_text(_normalize_address_text(address))
        city_for_coord = _normalize_city_name(inferred)
    if city_for_coord and city_for_coord in CITY_COORDS:
        return CITY_COORDS[city_for_coord], confidence
    if province and province in PROVINCE_TO_CAPITAL:
        capital = PROVINCE_TO_CAPITAL[province]
        coord = CITY_COORDS.get(capital)
        if coord:
            return coord, 'low'
    return None, 'low'


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _address_distance_metrics(source_address, target_address):
    """
    计算地址距离指标。
    返回:
    - score: Decimal, 用于排序（越小越近）
    - mode: 'km' | 'similarity'
    """
    left_coord, _ = _resolve_city_coord(source_address)
    right_coord, _ = _resolve_city_coord(target_address)
    if left_coord and right_coord:
        km = _haversine_km(left_coord[0], left_coord[1], right_coord[0], right_coord[1])
        return Decimal(str(round(km, 4))), 'km'

    left = (source_address or '').strip().lower()
    right = (target_address or '').strip().lower()
    if not left or not right:
        return Decimal('999.0000'), 'similarity'
    # 省市提取失败时，使用字符串相似度兜底:
    # score = (1-ratio)*100，越小表示越像
    ratio = SequenceMatcher(None, left, right).ratio()
    return Decimal(str(round((1 - ratio) * 100, 4))), 'similarity'


def get_transfer_match_candidates(
    delivery_address,
    target_event_date,
    sku_id,
    exclude_target_order_id=None,
):
    """
    获取创建订单时的转寄候选（不分配数量）
    规则：
    1) 来源订单待处理/待发货/已发货；
    2) 同SKU；
    3) 来源预定日期严格早于目标5天（不含5）；
    4) 排序：
       - 主排序：来源预定日期与(目标预定日期+5天)的差值越小越优先
       - 次排序：省市直线距离越近越优先
       - 三排序：来源单号 ASC
    """
    settings = get_system_settings()
    buffer_days = int(settings.get('buffer_days', 5) or 0)
    target_province, target_city, _ = _extract_province_city(delivery_address)
    # 严格 > 5 天：source_date <= target_date - 6
    min_source_date = target_event_date - timedelta(days=6)
    lock_start = target_event_date - timedelta(days=5)
    lock_end = target_event_date + timedelta(days=5)

    source_orders = Order.objects.filter(
        status__in=['pending', 'confirmed', 'delivered'],
        event_date__lte=min_source_date,
        items__sku_id=sku_id
    ).distinct().prefetch_related('items__sku')

    candidates = []
    for order in source_orders:
        source_province, source_city, _ = _extract_province_city(order.delivery_address)
        source_qty = OrderItem.objects.filter(order=order, sku_id=sku_id).aggregate(total=Sum('quantity'))['total'] or 0
        reserved_query = TransferAllocation.objects.filter(
            source_order=order,
            sku_id=sku_id,
            status__in=['locked', 'consumed'],
            target_event_date__gte=lock_start,
            target_event_date__lte=lock_end
        )
        if exclude_target_order_id:
            reserved_query = reserved_query.exclude(target_order_id=exclude_target_order_id)
        reserved_qty = reserved_query.aggregate(total=Sum('quantity'))['total'] or 0
        available_qty = max(source_qty - reserved_qty, 0)
        if available_qty <= 0:
            continue

        distance_score, distance_mode = _address_distance_metrics(order.delivery_address, delivery_address)
        _, source_confidence = _resolve_city_coord(order.delivery_address)
        _, target_confidence = _resolve_city_coord(delivery_address)
        confidence_rank = {'high': 0, 'medium': 1, 'low': 2}
        distance_confidence = source_confidence if confidence_rank[source_confidence] >= confidence_rank[target_confidence] else target_confidence
        target_plus_buffer = target_event_date + timedelta(days=buffer_days)
        date_gap_score = abs((target_plus_buffer - order.event_date).days)
        candidates.append({
            'source_order': order,
            'available_qty': available_qty,
            'date_gap_score': date_gap_score,
            'distance_score': distance_score,
            'distance_mode': distance_mode,
            'distance_confidence': distance_confidence,
            'buffer_days': buffer_days,
            'source_province': source_province,
            'source_city': source_city,
            'target_province': target_province,
            'target_city': target_city,
            'lock_window_start': lock_start,
            'lock_window_end': lock_end,
        })

    confidence_rank = {'high': 0, 'medium': 1, 'low': 2}
    candidates.sort(
        key=lambda item: (
            item['date_gap_score'],
            confidence_rank[item['distance_confidence']],
            item['distance_score'],
            item['source_order'].order_no
        )
    )
    return candidates


def build_transfer_allocation_plan(
    delivery_address,
    target_event_date,
    sku_id,
    quantity,
    preferred_source_order_id=None,
    exclude_target_order_id=None,
):
    """根据候选生成分配方案：优先转寄，不足部分走仓库。"""
    candidates = get_transfer_match_candidates(
        delivery_address,
        target_event_date,
        sku_id,
        exclude_target_order_id=exclude_target_order_id,
    )
    if preferred_source_order_id:
        preferred = [c for c in candidates if c['source_order'].id == preferred_source_order_id]
        others = [c for c in candidates if c['source_order'].id != preferred_source_order_id]
        candidates = preferred + others

    remaining = quantity
    allocations = []
    for c in candidates:
        if remaining <= 0:
            break
        alloc_qty = min(remaining, c['available_qty'])
        if alloc_qty <= 0:
            continue
        allocations.append({
            'source_order_id': c['source_order'].id,
            'source_order_no': c['source_order'].order_no,
            'source_event_date': c['source_order'].event_date,
            'sku_id': sku_id,
            'quantity': alloc_qty,
            'target_event_date': target_event_date,
            'window_start': c['lock_window_start'],
            'window_end': c['lock_window_end'],
            'distance_score': c['distance_score'],
        })
        remaining -= alloc_qty

    return {
        'allocations': allocations,
        'warehouse_needed': max(remaining, 0),
        'candidates': candidates,
    }


def calculate_order_dates(event_date, rental_days=1):
    """
    计算订单的发货日期和回收日期

    Args:
        event_date: 活动日期
        rental_days: 租赁天数

    Returns:
        dict: {
            'ship_date': date,  # 发货日期
            'return_date': date  # 回收日期
        }
    """
    settings = get_system_settings()

    ship_date = event_date - timedelta(days=settings['ship_lead_days'])
    return_date = event_date + timedelta(days=rental_days) + timedelta(days=settings['return_offset_days'])

    return {
        'ship_date': ship_date,
        'return_date': return_date
    }


def find_transfer_candidates():
    """
    查找可转寄的订单对

    Returns:
        list: [
            {
                'order_from': Order,  # 回收订单
                'order_to': Order,  # 发货订单
                'sku': SKU,
                'gap_days': int,  # 间隔天数
                'cost_saved': Decimal  # 节省成本
            }
        ]
    """
    candidates = []
    pending_orders = Order.objects.filter(
        status='pending'
    ).prefetch_related('items__sku')

    for order_to in pending_orders:
        for item_to in order_to.items.all():
            # 该待处理订单明细已挂靠（或已生成任务）则不再进入候选池
            if TransferAllocation.objects.filter(
                target_order=order_to,
                sku_id=item_to.sku_id,
                status__in=['locked', 'consumed']
            ).exists():
                continue
            if Transfer.objects.filter(
                order_to=order_to,
                sku_id=item_to.sku_id,
                status='pending'
            ).exists():
                continue

            match_candidates = get_transfer_match_candidates(
                order_to.delivery_address,
                order_to.event_date,
                item_to.sku_id,
                exclude_target_order_id=order_to.id,
            )
            if not match_candidates:
                continue

            # 候选池只展示“最佳候选”（排序第一名），避免组合爆炸
            c = match_candidates[0]
            order_from = c['source_order']
            gap = (order_to.event_date - order_from.event_date).days
            date_gap_score = int(c.get('date_gap_score', 0) or 0)
            if date_gap_score <= 1:
                date_match_label = '高'
            elif date_gap_score <= 3:
                date_match_label = '中'
            else:
                date_match_label = '低'

            distance_mode = c.get('distance_mode', 'similarity')
            distance_score = c.get('distance_score')
            if distance_mode == 'km':
                distance_desc = f"约 {distance_score} km"
            else:
                distance_desc = f"文本差异分 {distance_score}"
            candidates.append({
                'order_from': order_from,
                'order_to': order_to,
                'sku': item_to.sku,
                'available_qty': c.get('available_qty', 0),
                'date_gap_score': date_gap_score,
                'date_match_label': date_match_label,
                'distance_score': distance_score,
                'distance_mode': distance_mode,
                'distance_desc': distance_desc,
                'suggested_ship_date': order_from.event_date + timedelta(days=1),
                'gap_days': gap,
                'cost_saved': Decimal('100.00'),
            })

    for idx, item in enumerate(candidates, start=1):
        item['priority_text'] = f"P{idx}"
    candidates.sort(key=lambda x: (x['order_to'].event_date, x['gap_days'], x['order_to'].order_no))
    for idx, item in enumerate(candidates, start=1):
        item['priority_text'] = f"P{idx}"
    return candidates


def build_transfer_pool_rows():
    """
    构建转寄中心候选池：
    - 范围：未发货订单（pending/confirmed）的每条订单明细
    - 展示：当前挂靠、推荐来源、是否可重新推荐
    """
    settings = get_system_settings()
    ship_lead_days = int(settings.get('ship_lead_days', 2) or 0)
    warehouse_sender = {
        'name': settings.get('warehouse_sender_name', '仓库发货员'),
        'phone': settings.get('warehouse_sender_phone', '-'),
        'address': settings.get('warehouse_sender_address', '仓库地址未配置'),
    }

    def _distance_desc(from_address, to_address):
        score, mode = _address_distance_metrics(from_address, to_address)
        if mode == 'km':
            return f"约 {score} km"
        return f"文本差异分 {score}"

    target_orders = Order.objects.filter(
        status__in=['pending', 'confirmed']
    ).prefetch_related('items__sku').order_by('event_date', 'created_at')

    allocations = TransferAllocation.objects.filter(
        target_order__status__in=['pending', 'confirmed'],
        status='locked'
    ).select_related('source_order')
    alloc_map = {}
    for alloc in allocations:
        alloc_map.setdefault((alloc.target_order_id, alloc.sku_id), []).append(alloc)

    pending_pairs = set(
        Transfer.objects.filter(
            order_to__status__in=['pending', 'confirmed'],
            status='pending'
        ).values_list('order_to_id', 'sku_id')
    )

    rows = []
    for order in target_orders:
        for item in order.items.all():
            key = (order.id, item.sku_id)
            allocs = alloc_map.get(key, [])
            has_pending_task = key in pending_pairs
            recommended = get_transfer_match_candidates(
                order.delivery_address,
                order.event_date,
                item.sku_id,
                exclude_target_order_id=order.id,
            )
            top = recommended[0] if recommended else None

            if allocs:
                source_order = allocs[0].source_order
                current_source_text = f"{source_order.order_no}（{sum(a.quantity for a in allocs)}套）"
                current_sender = {
                    'name': source_order.customer_name,
                    'phone': source_order.customer_phone,
                    'address': source_order.delivery_address,
                }
                current_event_date = source_order.event_date
                current_source_type = 'transfer'
            else:
                current_source_text = '仓库发货'
                current_sender = warehouse_sender
                current_event_date = None
                current_source_type = 'warehouse'
            current_distance_desc = _distance_desc(current_sender.get('address', ''), order.delivery_address)

            if top:
                rec_source = top['source_order']
                rec_source_text = f"{rec_source.order_no}（可转寄{top['available_qty']}套）"
                rec_sender = {
                    'name': rec_source.customer_name,
                    'phone': rec_source.customer_phone,
                    'address': rec_source.delivery_address,
                }
                rec_ship_date = rec_source.event_date + timedelta(days=1)
                recommended_event_date = rec_source.event_date
                recommended_source_type = 'transfer'
                if top.get('distance_mode') == 'km':
                    recommended_distance_desc = f"约 {top.get('distance_score')} km"
                else:
                    recommended_distance_desc = f"文本差异分 {top.get('distance_score')}"
            else:
                rec_source_text = '仓库发货（当前无可用转寄来源）'
                rec_sender = warehouse_sender
                rec_ship_date = order.event_date - timedelta(days=ship_lead_days)
                recommended_event_date = None
                recommended_source_type = 'warehouse'
                recommended_distance_desc = _distance_desc(rec_sender.get('address', ''), order.delivery_address)

            rows.append({
                'row_key': f'{order.id}:{item.sku_id}',
                'order': order,
                'item': item,
                'current_source_text': current_source_text,
                'current_sender': current_sender,
                'current_event_date': current_event_date,
                'current_source_type': current_source_type,
                'current_distance_desc': current_distance_desc,
                'recommended_source_text': rec_source_text,
                'recommended_sender': rec_sender,
                'recommended_event_date': recommended_event_date,
                'recommended_source_type': recommended_source_type,
                'recommended_distance_desc': recommended_distance_desc,
                'recommended_ship_date': rec_ship_date,
                'has_pending_task': has_pending_task,
                'can_recommend': not has_pending_task,
                'can_generate_task': (current_source_type == 'transfer' and not has_pending_task),
            })

    return rows


def create_transfer_task(order_from_id, order_to_id, sku_id, user):
    """
    创建转寄任务

    Args:
        order_from_id: 回收订单ID
        order_to_id: 发货订单ID
        sku_id: SKU ID
        user: 创建人

    Returns:
        Transfer: 转寄任务对象
    """
    order_from = Order.objects.get(id=order_from_id)
    order_to = Order.objects.get(id=order_to_id)
    sku = SKU.objects.get(id=sku_id)

    transfer = Transfer.objects.filter(
        order_from=order_from,
        order_to=order_to,
        sku=sku,
        status='pending'
    ).first()
    if transfer:
        return transfer

    gap_days = (order_to.event_date - order_from.event_date).days
    cost_saved = Decimal('100.00')
    return Transfer.objects.create(
        order_from=order_from,
        order_to=order_to,
        sku=sku,
        quantity=1,
        gap_days=gap_days,
        cost_saved=cost_saved,
        status='pending',
        created_by=user
    )


def sync_transfer_tasks_for_target_order(target_order, user=None, sku_id=None):
    """
    根据目标订单当前的转寄挂靠锁，同步转寄任务。
    - 有挂靠则创建/更新 pending 任务
    - 挂靠移除则自动取消多余 pending 任务
    """
    allocations_qs = TransferAllocation.objects.filter(
        target_order=target_order,
        status='locked'
    )
    if sku_id is not None:
        allocations_qs = allocations_qs.filter(sku_id=sku_id)
    allocations = allocations_qs.values('source_order_id', 'sku_id').annotate(total_qty=Sum('quantity'))

    desired = {}
    for row in allocations:
        key = (row['source_order_id'], row['sku_id'])
        desired[key] = int(row['total_qty'] or 0)

    existing_pending = Transfer.objects.filter(order_to=target_order, status='pending')
    if sku_id is not None:
        existing_pending = existing_pending.filter(sku_id=sku_id)
    existing_pending = existing_pending.select_related('order_from', 'sku')
    existing_map = {(t.order_from_id, t.sku_id): t for t in existing_pending}

    for key, qty in desired.items():
        if qty <= 0:
            continue
        source_order_id, sku_id = key
        transfer = existing_map.get(key)
        source_order = Order.objects.get(id=source_order_id)
        gap_days = (target_order.event_date - source_order.event_date).days
        cost_saved = Decimal('100.00') * Decimal(qty)
        if transfer:
            updates = []
            if transfer.quantity != qty:
                transfer.quantity = qty
                updates.append('quantity')
            if transfer.gap_days != gap_days:
                transfer.gap_days = gap_days
                updates.append('gap_days')
            if transfer.cost_saved != cost_saved:
                transfer.cost_saved = cost_saved
                updates.append('cost_saved')
            if updates:
                transfer.save(update_fields=updates + ['updated_at'])
            continue
        Transfer.objects.create(
            order_from_id=source_order_id,
            order_to=target_order,
            sku_id=sku_id,
            quantity=qty,
            gap_days=gap_days,
            cost_saved=cost_saved,
            status='pending',
            created_by=user
        )

    for key, transfer in existing_map.items():
        if key in desired:
            continue
        transfer.status = 'cancelled'
        transfer.notes = ((transfer.notes + '\n') if transfer.notes else '') + '自动取消：挂靠已移除'
        transfer.save(update_fields=['status', 'notes', 'updated_at'])


def get_calendar_data(year, month):
    """
    获取排期看板数据（月度视图）

    Args:
        year: 年份
        month: 月份

    Returns:
        dict: {
            'dates': [date1, date2, ...],  # 日期列表
            'skus': [sku1, sku2, ...],  # SKU列表
            'data': {
                sku_id: {
                    date: {
                        'occupied': int,  # 占用数量
                        'available': int,  # 可用数量
                        'total': int,  # 总库存
                        'status': str,  # 'full'/'tight'/'ok'
                        'orders': [order1, order2, ...]  # 该日订单列表
                    }
                }
            }
        }
    """
    from datetime import date
    import calendar

    # 生成该月所有日期
    num_days = calendar.monthrange(year, month)[1]
    dates = [date(year, month, day) for day in range(1, num_days + 1)]

    # 获取所有启用的SKU
    skus = SKU.objects.filter(is_active=True)

    data = {}

    for sku in skus:
        data[sku.id] = {}

        for d in dates:
            # 查询该日期的订单
            orders = Order.objects.filter(
                status__in=['pending', 'confirmed', 'delivered', 'in_use'],
                items__sku=sku
            ).distinct()

            # 统计占用数量
            occupied = OrderItem.objects.filter(
                order__in=orders,
                sku=sku
            ).aggregate(total=Sum('quantity'))['total'] or 0

            available = sku.stock - occupied

            # 判断状态
            if available == 0:
                status = 'full'
            elif available <= sku.stock * 0.2:
                status = 'tight'
            else:
                status = 'ok'

            data[sku.id][d] = {
                'occupied': occupied,
                'available': available,
                'total': sku.stock,
                'status': status,
                'orders': list(orders)
            }

    return {
        'dates': dates,
        'skus': list(skus),
        'data': data
    }


def get_low_stock_parts():
    """
    获取库存不足的部件列表

    Returns:
        QuerySet: 库存不足的部件
    """
    from .models import Part
    from django.db.models import F

    return Part.objects.filter(
        is_active=True,
        current_stock__lt=F('safety_stock')
    ).order_by('current_stock')


def calculate_order_amount(order_items):
    """
    计算订单金额

    Args:
        order_items: 订单明细列表 [{'sku_id': 1, 'quantity': 2}, ...]

    Returns:
        dict: {
            'total_amount': Decimal,  # 总金额
            'total_deposit': Decimal,  # 总押金
            'total_rental': Decimal  # 总租金
        }
    """
    total_deposit = Decimal('0.00')
    total_rental = Decimal('0.00')

    for item in order_items:
        sku = SKU.objects.get(id=item['sku_id'])
        quantity = item['quantity']

        total_deposit += sku.deposit * quantity
        total_rental += sku.rental_price * quantity

    # 订单总额仅统计租金，押金单独管理
    total_amount = total_rental

    return {
        'total_amount': total_amount,
        'total_deposit': total_deposit,
        'total_rental': total_rental
    }
