# 验收报告（自动生成）

## 基本信息
- 项目：宝宝周岁宴道具租赁系统
- 提交：`625d522`
- 生成时间：2026-02-27 19:48:17
- 脚本：`scripts/generate_acceptance_report.py`

## 自动化检查结果
- Django 系统检查（`manage.py check`）：**PASS**
- 自动化测试（`manage.py test`）：**OK**
- 通过用例数：15
- 失败用例数：0
- 执行用例总数：15

## 手工验收清单（待勾选）
### 1. 订单全流程
- [ ] 新建订单
- [ ] 确认订单
- [ ] 标记送达
- [ ] 标记归还
- [ ] 标记完成
- [ ] 取消订单

### 2. 采购全流程
- [ ] 新建采购单（含明细）
- [ ] 标记下单
- [ ] 标记到货
- [ ] 确认入库（库存联动）

### 3. 转寄流程
- [ ] 从候选创建任务
- [ ] 任务完成
- [ ] 任务取消

### 4. API 验收
- [ ] 订单列表 API
- [ ] 订单状态流转 API
- [ ] 采购状态流转 API
- [ ] 转寄创建/列表 API

## 执行日志（check）
```text
System check identified no issues (0 silenced).
```

## 执行日志（test）
```text
Creating test database for alias 'default'...

Found 15 test(s).
System check identified no issues (0 silenced).
E:\项目\zhousuiyan-system\.venv\Lib\site-packages\django\core\handlers\base.py:61: UserWarning: No directory at: E:\项目\zhousuiyan-system\staticfiles\
  mw_instance = middleware(adapted_handler)
...............
----------------------------------------------------------------------
Ran 15 tests in 5.278s

OK
Destroying test database for alias 'default'...
```

## 验收结论
- 自动化结论：**PASS**
- 手工结论：`待填写`
