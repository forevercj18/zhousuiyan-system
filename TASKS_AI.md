# TASKS_AI.md
## 1. 文档用途
这份文件用于让后续接手者快速判断：

- 当前项目已经做到了什么
- 哪些目录是主工程
- 哪些模块已进入可用状态
- 哪些模块还在继续完善
- 接下来最值得做什么

本文件基于当前真实代码扫描结果更新到 `2026-03-24`。

## 2. 当前项目全景
### 2.1 主工程
- Django 后台与 API：`apps/`、`config/`、`templates/`
- 微信小程序前端：`miniprogram/`

### 2.2 非主工程 / 历史目录
- `zhousuiyan-mp/`：旧小程序脚手架示例，当前不是主小程序工程

### 2.3 当前建议默认关注目录
- `apps/core/`
- `apps/api/`
- `templates/orders/`
- `templates/reservations/`
- `templates/dashboard.html`
- `miniprogram/`

## 3. 已完成主模块
### 3.1 订单中心
已完成：

- 新建订单
- 编辑订单
- 订单详情
- 发货、归还、完成、取消
- 时效状态判断与列表筛选
- 订单来源 / 平台单号
- 包回邮服务
- 搜索增强（平台单号 / 回邮支付参考号）
- 订单 CSV 导出
- 订单历史表格 CSV / XLSX 导入
- 导入模板下载
- 导入预检查（先校验再入库）
- 兼容旧表字段：客户昵称 / 手机号码 / 地址 / 款式 / 客订来源 / 租金 / 预收押金 / 预定时间 / 发货时间 / 状态 / 发货单号
- 旧表中无法结构化落库的字段（如租金渠道、押金渠道、经手人、操作时间及其它扩展列）会自动保存在订单备注中，避免历史信息丢失

### 3.2 转寄中心
已完成：

- 转寄候选池
- 推荐来源计算
- 当前挂靠展示
- 转寄任务生成、完成、取消
- 重推与分栏展示

### 3.3 单套库存与仓储链路
已完成：

- SKU
- BOM
- 装配
- 单套库存
- 部件库存
- 维修
- 回件质检
- 处置

### 3.4 财务、审批、风控、审计、运维
已完成：

- 财务流水
- 对账基础页面
- 审批中心
- 风险事件
- 审计日志
- 运维中心

### 3.5 预定单主线
已完成：

- `Reservation` 模型与完整后台页面
- 极简预定录入
- 订金收款/退款/结转
- 转正式订单
- 负责人机制
- 联系提醒
- 批量跟进
- 批量转交负责人
- 负责人分布、移交建议、履约跟进看板
- 工作台提醒横幅 + 当天不再提醒

### 3.5A 日历排期
当前状态：

- 已下线后台 `日历排期` 功能
- 侧边栏入口、工作台快捷入口、权限分配选项中已移除
- 历史地址 `/calendar/` 现统一跳回工作台并提示“功能已下线”，避免旧链接进入损坏页面
- 旧 `templates/calendar.html` 与 `static/js/calendar.js` 已移除，不再继续维护

### 3.6 包回邮服务
已完成：

- 订单附加服务字段
- 包回邮服务费收款/退款流水
- 订单详情独立登记区
- 支持发货后补录
- 按渠道、支付参考号快速找单

### 3.7 微信小程序后端
已完成：

- 迁移：`0025_miniprogram_models`
- `WechatCustomer`
- `SKUImage`
- SKU 小程序展示字段
- Reservation 小程序来源字段
- `/api/mp/` 6 个接口
- 小程序认证服务
- 小程序 API 测试

### 3.8 微信小程序前端
当前 `miniprogram/` 已存在一版真实业务前端，包含：

- 首页
- 产品详情
- 意向下单
- 我的意向单列表
- 意向单详情
- 登录与接口封装
- 已修复 `apps/core/views.py` 中因缺失 `SKUImage` 与 `SKUComponent` 导入导致的 SKU 创建/编辑多图保存失败（NameError）问题，当前 `apps.core.tests` 273 项全绿。
- 已同步修复 `pages/my-orders/my-orders.wxml` 预览编译报错：原因为 WXML `class` 属性中使用了多行三元表达式，预览编译器报 `unexpected character '\n'`；现改为在 JS 中预计算 `statusClass`
- 已同步修复 `pages/order-detail/order-detail.wxml` 同类风险写法，避免订单详情页在预览阶段继续因多行三元表达式报错
- 已将小程序前端默认 API 地址切换为正式外网域名 `https://erp.yanli.net.cn`，避免真机预览继续请求本地 `127.0.0.1` 导致网络错误；后续仍需在微信公众平台配置 request 合法域名
- 已完成小程序端预定单状态可视化：我的订单列表会显示当前进度标签与跟进摘要，意向单详情页会显示 4 段进度（提交意向 / 客服联系 / 转正式订单 / 履约跟进），并展示预计联系日期、正式订单号、发货跟进、尾款跟进
- 小程序接口 `/api/mp/my-reservations/`、`/api/mp/my-reservations/<id>/` 已补充联系状态、进度标签、状态说明、履约跟进字段，前端不再靠本地写死文案猜状态
- 小程序图片展示现在优先走七牛云：SKU 主图和 SKUImage 多图优先返回七牛 HTTPS 地址，历史本地 SKU 图片也已迁移并清理数据库旧字段引用；后续真机仍需在微信公众平台配置 `downloadFile` 合法域名
- 已完成员工端第一阶段 MVP：同一小程序内新增 `工作模式` 入口，支持后台账号绑定到微信身份，已上线页面包括：
  - `pages/work-bind/work-bind`
  - `pages/work-home/work-home`
  - `pages/work-reservations/work-reservations`
  - `pages/work-reservation-detail/work-reservation-detail`
  - `pages/work-orders/work-orders`
  - `pages/work-order-detail/work-order-detail`
- 已完成员工端后端接口：
  - `POST /api/mp/staff/bind/`
  - `GET /api/mp/staff/profile/`
  - `GET /api/mp/staff/dashboard/`
  - `GET /api/mp/staff/reservations/`
  - `GET /api/mp/staff/reservations/<id>/`
  - `POST /api/mp/staff/reservations/<id>/status/`
  - `POST /api/mp/staff/reservations/<id>/followup/`
  - `POST /api/mp/staff/reservations/<id>/transfer/`
  - `GET /api/mp/staff/orders/`
  - `GET /api/mp/staff/orders/<id>/`
  - `POST /api/mp/staff/orders/<id>/deliver/`
  - `POST /api/mp/staff/orders/<id>/return/`
  - `POST /api/mp/staff/orders/<id>/balance/`
  - `POST /api/mp/staff/orders/<id>/return-service/`
- 已新增 `WechatStaffBinding`，允许同一个微信身份绑定后台员工账号，复用现有 Django 角色与权限体系
- 当前员工端已支持：
  - 客服查看自己负责的预定待办、逾期联系、待转正式订单
  - 客服查看预定详情并将状态改为 `待补信息 / 可转正式订单`
  - 客服在手机端补录预定跟进信息：客户称呼、手机号、地址、备注
  - 客服在手机端快速复制微信号、直接拨打电话
  - 管理员 / 经理在手机端直接转交预定单负责人，并可填写转交原因
  - 仓库/有权限员工查看订单详情
  - 仓库/有权限员工在手机端执行 `标记已发货 / 标记已归还`
  - 仓库/有权限员工在手机端独立登记尾款
  - 仓库/有权限员工在手机端处理包回邮服务状态、收款状态与取件进度
  - 管理员 / 经理在手机端按负责人、来源筛选预定单与订单，便于移动排单
- 已继续增强员工端移动体验：
  - 首页员工入口改为更明显的 `客户模式 / 工作模式` 切换样式
  - 绑定成功后自动记住工作模式偏好
  - 工作模式首页新增“切回客户模式”与按场景分组的待办卡片
  - 预定跟进页新增快捷筛选：`今日需联系 / 逾期未联系 / 待转正式订单 / 转单待发货`
  - 订单跟进页新增快捷筛选：`待发货 / 待发货超时 / 待收尾款 / 包回邮待处理`
  - 订单详情与预定详情关键操作增加确认弹窗、按钮处理中态
  - 员工端订单详情新增 `尾款登记`、`包回邮处理` 两个独立操作区，不再只能查看
  - 员工端预定详情新增 `跟进信息` 编辑区，不再只能改状态
  - 员工端预定详情新增 `转交负责人` 区，管理角色可直接在手机端改派跟进客服
  - 订单详情支持一键复制订单号、手机号
  - 工作模式首页、预定跟进页、订单跟进页已开启下拉刷新
  - 员工端预定跟进页新增 `来源 / 负责人` 筛选器；订单跟进页新增 `来源 / 负责人` 筛选器
  - 员工端预定跟进列表现在支持直接在卡片上快捷标记 `待补信息 / 可转正式订单`，减少频繁进入详情页的操作成本
  - 员工端订单跟进列表现在支持卡片级快捷入口：`去发货 / 去收尾款 / 处理回邮 / 去归还`，并在详情页自动定位到对应操作区
  - 员工端工作模式提醒已统一增强：首页员工入口会显示高优先待办总数，工作台会显示高优先待办横幅和分组角标，手机端更容易一眼发现紧急事项
  - 员工端已新增统一“全局搜索页”，可在一个页面同时搜索预定单和订单，并直接执行发货、收尾款、包回邮、归还等快捷入口
  - 已补强员工端搜索接口：`/api/mp/staff/orders/` 现在支持按订单商品名搜索，避免移动端全局搜索只能命中订单号/客户信息却漏掉 SKU 名称
  - 已补充员工端搜索相关测试：锁定 `/api/mp/staff/reservations/` 与 `/api/mp/staff/orders/` 的 `keyword` 搜索行为，降低后续迭代回归风险

### 3.9 生产访问排障
已完成：

- 已定位外网访问后台 `Bad Request (400)` 的直接原因是 Django `DisallowedHost`
- 已确认 `.env.prod` 中 `ALLOWED_HOSTS` 配置本身正确，问题来自旧 Python 进程仍占用 `8000`
- 已清理旧的 `8000` 端口占用进程，避免 Cloudflare Tunnel 继续打到旧实例
- 后续重新启动生产服务时，应只保留一个生产 Waitress 进程监听 `8000`

注意：

- 仓库中这部分代码不是空壳，后续接手要把它作为真实工程看待
- 但联调、发布、真机验收是否全部完成，当前仍需要继续确认

## 4. 当前核心迁移
当前接手必须知道的迁移：

- `0022_reservation_and_finance_transaction_updates`
- `0023_reservation_owner`
- `0024_order_return_service_fields`
- `0025_miniprogram_models`
- `0026_reservation_delivery_address`
- `0027_sku_qiniu_image_keys`

## 5. 当前关键接口 / 页面
### 5.1 小程序 API
实际已存在：

- `POST /api/mp/login/`
- `GET /api/mp/skus/`
- `GET /api/mp/skus/<id>/`
- `POST /api/mp/reservations/`
- `GET /api/mp/my-reservations/`
- `GET /api/mp/my-reservations/<id>/`
- `POST /api/mp/staff/bind/`
- `GET /api/mp/staff/profile/`
- `GET /api/mp/staff/dashboard/`
- `GET /api/mp/staff/reservations/`
- `GET /api/mp/staff/reservations/<id>/`
- `POST /api/mp/staff/reservations/<id>/status/`
- `GET /api/mp/staff/orders/`
- `GET /api/mp/staff/orders/<id>/`
- `POST /api/mp/staff/orders/<id>/deliver/`
- `POST /api/mp/staff/orders/<id>/return/`
- `POST /api/mp/staff/orders/<id>/balance/`
- `POST /api/mp/staff/orders/<id>/return-service/`

### 5.2 小程序前端页面
当前 `miniprogram/app.json` 注册页面：

- `pages/index/index`
- `pages/detail/detail`
- `pages/order/order`
- `pages/my-orders/my-orders`
- `pages/order-detail/order-detail`
- `pages/work-bind/work-bind`
- `pages/work-home/work-home`
- `pages/work-reservations/work-reservations`
- `pages/work-reservation-detail/work-reservation-detail`
- `pages/work-orders/work-orders`
- `pages/work-order-detail/work-order-detail`

## 6. 当前高风险点
### 6.1 模板层空值兼容
近期真实问题已证明：

- 老数据里后加字段可能为空
- 模板如果直接链式取值会报错

典型字段：

- `Reservation.owner`
- `Reservation.source`
- `Reservation.city`
- `Order.return_service_payment_reference`

### 6.2 多条业务主线并行
现在已经不是只有订单一条线，而是并行存在：

- 正式订单
- 预定单
- 转寄任务
- 包回邮服务
- 小程序意向单

接手时必须先判断需求属于哪条线，避免误改。

### 6.3 小程序存在双目录
必须明确：

- `miniprogram/` 是当前主工程
- `zhousuiyan-mp/` 是旧脚手架

这是当前最容易让接手者误判的目录风险。

### 6.4 外网和本机部署并存
当前仓库已经支持：

- Windows 本机生产部署
- Cloudflare Tunnel 外网访问

后续上线问题常见来源：

- `.env.prod`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- Cloudflare SSL/Tunnel 配置

## 7. 当前未完成 / 持续完善中的部分
### 7.1 生产环境最终验收
仍需要继续确认：

- 外网完整业务验收
- HTTPS/安全项最终收口
- 正式数据库上线演练
- 备份与回滚方案

### 7.2 微信小程序联调与发布
当前最明确的后续工作之一：

- 微信公众平台 `request` / `downloadFile` 合法域名配置
- 真机登录、商品图、意向下单、我的订单全链路验收
- 七牛图片域名正式化（如 `img.yanli.net.cn`）与小程序图片加载稳定性验证
- 员工端真机验收：绑定、移动发货、移动归还、尾款登记、包回邮处理
- 如需正式上线，继续补手机号授权、地址体验、售后/包回邮入口

- 已完成：SKU 图片已切到“七牛直传 + 数据库存 key + 前后端统一拼公开 URL”的第一阶段
- 已完成：后台 SKU 上传页新增七牛上传凭证接口与直传逻辑，小程序商品接口优先返回七牛 HTTPS 图片地址
- 已完成：兼容旧本地图，读取顺序为 `image_key -> 本地 image -> 占位`
- 已完成：新增 `scripts/migrate_sku_images_to_qiniu.py`，可批量把旧本地 SKU 主图/画廊图迁到七牛
- 已完成：新增 `scripts/cleanup_local_sku_images.py`，可在迁移确认后清理本地 FileField 引用与旧磁盘图片
- `miniprogram/` 与 `/api/mp/` 真机联调
- `MP_APPID` / `MP_SECRET` 正式配置
- 微信公众平台 `request` / `downloadFile` 合法域名配置
- 七牛云 `bucket / 自定义域名 / 上传 token` 正式配置
- 微信开发者工具项目配置确认
- 小程序上传与发布流程打通
- 真机下单 / 我的订单 / 登录链路验收
- 员工端真机绑定、工作模式首页、预定跟进、仓库发货/归还链路验收
- 员工端模式切换体验优化（客户模式 / 工作模式）
- 员工端是否需要追加订阅消息、扫码搜单、包回邮待处理入口仍待后续确认
- 员工端后续仍可继续增强：
  - 订阅消息 / 红点提醒
  - 扫码搜单
  - 包回邮独立移动处理入口
  - 负责人转交、尾款登记等更深操作

### 7.3 SKU 多图后台管理
当前 `SKUImage` 模型已存在，但后台运营层面的上传/管理体验还不够完整。

当前状态：

- 已新增 `SKU.image_key`、`SKUImage.image_key`
- 小程序详情页多图展示已支持优先读取 `SKUImage.image_key`
- 已完成：后台 SKU 编辑弹窗支持多图直传七牛、设置封面、调整排序、删除图片
- 已完成：创建/编辑 SKU 时支持保存 `gallery_payload` 到 `SKUImage`
- 已完成：生产环境示例配置补充七牛参数模板（`.env.example`、`.env.prod.example`）
- 已完成：系统设置页增加七牛配置就绪状态摘要；SKU 页面在未配置七牛时会明确提示“直传当前不可用”
- 已完成：七牛真实配置已接入 `.env.prod`，并确认 `zhousuiyan-img` 需使用华南上传域名 `https://up-z2.qiniup.com`；已完成一次真实上传连通性测试
- 已完成：历史 SKU 图片已正式迁移到七牛，当前迁移结果为 `SKU 主图 1 张成功`、`SKU 画廊 0 张`
- 已完成：旧本地 SKU 图片数据库引用已清理完成，当前 `cleanup_local_sku_images.py --dry-run` 结果为 `主图 0 条、画廊 0 条待清理`
- 推荐执行顺序：先配置七牛 -> `migrate_sku_images_to_qiniu.py --dry-run` -> 正式迁移 -> `cleanup_local_sku_images.py --dry-run` -> 确认后清理本地旧图
- 后续仍可继续做独立的多图管理页、旧本地图片清理和订单/质检图片同样切到对象存储

### 7.4 包回邮服务继续增强
当前已解决记账和补录，但后续还可继续做：

- 取件状态快捷流转
- 包回邮服务看板
- 回邮成本记录
- 毛利分析
- 快递接口对接

### 7.5 预定单继续增强
当前主线已通，但后续还可继续做：

- 多款式明细
- 一键按建议批量转交
- 外部 webhook 提醒
- 历史数据清洗
- 更强的城市/档期冲突判断

### 7.6 页面性能继续优化
当前仍需持续关注：

- 工作台首页聚合统计
- 订单中心主文档耗时
- Cloudflare 外网下的体感速度

## 8. 当前推荐的下一步
如果后续继续开发，优先级建议如下：

1. 微信小程序联调与发布
2. Cloudflare R2 真实上传配置已补齐并完成最小验证；继续做真机联调，确认后台上传与小程序图片读取都稳定走 `https://pic.yanli.net.cn`
3. 如确认不再需要兜底，可再决定是否用 `cleanup_local_sku_images.py --clear-fields --delete-files` 清理本地磁盘文件
4. 生产环境最终验收
5. 包回邮服务看板 / 成本链路
6. 预定单多款式与自动化交接

补充：
- 已完成：后台 SKU 图片上传失败提示已从浏览器原始 `Failed to fetch` 改为可诊断文案，能区分 R2 Endpoint / Bucket / CORS 等常见问题
- 已完成：产品创建/编辑成功后的消息提示已合并为单条，避免全局弹窗体系连续弹出多条 `success/info` 提示框
- 已修复本地测试环境与生产环境的对象存储配置不同步问题：`start.ps1` 现在会优先读取 `.env`，若本地未配置则自动从 `.env.prod` 继承 Cloudflare R2 / 历史七牛 / `MP_PUBLIC_BASE_URL` 相关变量，避免 `localhost` 下 SKU 页面误报“Cloudflare R2 图片直传未就绪”

## 9. 接手时建议先看的文件
建议阅读顺序：

1. `README_AI.md`
2. `RULES_AI.md`
3. `TASKS_AI.md`
4. `apps/core/models.py`
5. `apps/core/services/`
6. `apps/core/views.py`
7. `apps/api/mp_views.py`
8. `miniprogram/utils/api.js`
9. `miniprogram/app.json`
10. `docs/MINIPROGRAM_DESIGN_20260322.md`
11. `docs/MINIPROGRAM_DEV_20260322.md`

## 10. 当前一句话结论
当前仓库已经不是“单后台项目”，而是：

- 一个成熟的 Django 租赁业务后台
- 加上一条已打通后端的小程序业务线
- 再叠加预定、转寄、仓储、财务、包回邮等多条闭环链路

后续开发策略应当是：

- 继续沿现有主线增量收口
- 不要轻易推翻
- 明确区分后台、API、小程序三层职责
