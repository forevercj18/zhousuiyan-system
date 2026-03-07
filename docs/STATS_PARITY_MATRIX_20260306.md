# 工作台统计口径对照表（2026-03-06）

## 1. 目的
1. 明确“页面工作台”与“API工作台统计”字段口径一致。
2. 约束后续改动必须走同一统计函数，避免重复实现导致漂移。

---

## 2. 统一实现入口
1. 统一函数：`apps/core/utils.py#get_dashboard_stats_payload(include_transfer_available=True)`
2. 页面调用：`apps/core/views.py#dashboard`
3. API调用：`apps/api/views.py#api_dashboard_stats`

---

## 3. 字段口径矩阵

| 字段 | 页面展示 | API返回 | 口径说明 |
|---|---|---|---|
| `pending_orders` | 是 | 是 | `Order.status='pending'` 数量 |
| `delivered_orders` | 是 | 是 | `Order.status='delivered'` 数量 |
| `completed_orders` | 是 | 是 | `Order.status='completed'` 数量 |
| `warehouse_available_stock` | 是 | 是 | `SKU总库存 - max(订单占用-转寄占用,0)` |
| `transfer_available_count` | 可扩展 | 是 | `find_transfer_candidates()` 数量 |
| `total_orders` | 是 | 是 | 订单总数 |
| `total_skus` | 是 | 是 | 启用 SKU 总数 |
| `low_stock_parts` | 是 | 是 | `Part.is_active=True and current_stock < safety_stock` |
| `total_revenue` | 是 | 是 | `completed` 订单 `total_amount` 汇总 |
| `pending_revenue` | 是 | 是 | 非 `completed/cancelled` 订单 `balance` 汇总 |

---

## 4. 自动化校验
1. 测试文件：`apps/api/tests.py`
2. 用例：`test_dashboard_stats_should_match_dashboard_page_core_fields`
3. 校验内容：
   - 核心计数字段逐一相等
   - 金额字段用 `Decimal` 比对，避免字符串/浮点差异

---

## 5. 变更规则
1. 任何工作台统计字段新增/修改，必须先改 `get_dashboard_stats_payload`。
2. 页面与API禁止各自单独重写统计SQL。
3. 必须同步更新本对照表与自动化测试。
