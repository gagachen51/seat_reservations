# 设计说明：D 模块运营分析与报表

> 文档口径（v2.0.3，2026-11-26）：当前发布版本 **v2.0.3**；`pytest` **499 passed / 1 skipped**，Node **43 passed**；用户启动方式统一为安装 `setup.exe`。本文历史章节中的旧版本/测试数字仅代表当时基线，最新口径以根目录 `README.md` 与 `CHANGELOG.md` 为准。


> - 模块：运营分析与报表（成员 D）
> - 分支：`feature/analytics-module-v1.2`
> - 对应计划：`docs/collaboration/四人协作分工与开发计划-v1.2.md`
> - 本文档描述前端页面的实现口径和依赖，不替代后端接口契约。

## 1. 范围

本模块提供：

- 房间管理员、系统管理员可访问的运营分析页面；
- 近 7/30/90 天日期范围与房间筛选；
- KPI、每日趋势、高峰时段、房间排名、违约、评价、候补、投诉展示；
- 系统页七类 CSV 导出入口。

不包含：

- 新的统计 SQL 或业务规则；
- 新的图表库；
- 共享注册表、全局 `state` 字段或 `AFTER` 自动刷新；
- 管理端候补队列视图（可选增强，另行处理）。

## 2. API

```text
GET /api/admin/analytics?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&room_id=R1
```

- 日期区间首尾包含；前端固定传两端日期。
- 近 7/30/90 天分别对应 7/30/90 个自然日。
- `room_id` 为空时查询全校；房间选项来自 `state.meta.rooms`。
- 返回字段：`range`、`kpi`、`daily`、`peak_hours`、`rooms`、`violations`、`ratings`、`waitlist`、`complaints`、`metric_notes`、`generated_at`。

CSV：

```text
GET /api/admin/export/{kind}.csv
```

七类：`reservations`、`violations`、`audit`、`complaints`、`blacklist`、`credit_logs`、`waitlist`。前端使用普通链接下载，不额外实现 fetch。

## 3. 页面结构

```text
工具栏
  日期范围：近 7 天 / 近 30 天 / 近 90 天
  房间：全部 / 各自习室
  当前区间与生成时间
KPI 网格
  预约数、完成数、违约数、取消数
  签到率、完成率、投诉办结率、平均评分
主内容（dash-grid）
  每日预约与签到趋势（复用 trendChart）
  高峰时段（复用 hourlyChart）
  房间预约时段利用率排名（table.data）
  违约类型（bar-list）
  评价分布（bar-list）
  候补状态（bar-list）
  投诉处理（detail-list）
指标口径
  metric_notes 中与用户理解相关的说明
```

空数据时使用分析页自己的空状态文案，避免复用 `hourlyChart` 内部“今天还没有预约”的固定措辞。

## 4. 前端状态与日期

- 仅使用模块内局部变量：
  - `analyticsRange`：`7d | 30d | 90d`
  - `analyticsRoom`：空字符串或房间 ID
- 不新增共享 `state` 字段。
- 日期以 `state.meta.today`（服务端今天）为基准：
  - `date_to = today`
  - `date_from = today - (days - 1)`
- 始终同时传 `date_from` 和 `date_to`，避免单端日期或客户端/服务端日期差异。
- 房间筛选按钮始终使用 `state.meta.rooms`，不使用响应中的 `data.rooms`，避免选项随筛选结果缩小。
- `data.rooms` 只用于房间排名表格。

## 5. 指标口径

页面直接展示后端结果，不重算指标：

- `booking_utilization`：预约时段利用率，非取消预约的预订时段 / 容量；**不等同于实际座位使用率**。
- `checkin_rate`：签到 /（签到 + 明确未签到）。
- `completion_rate` / `no_show_rate`：以 `completed + no_show` 为出勤结果基数。
- `cancel_rate`：取消 / 全部预约；`released` 单独展示，不并入取消。
- `complaint_resolution_rate`：`(resolved + rated) / total`。
- `waitlist.confirmed_rate`：确认 / 候补总数。
- `metric_notes`：按后端返回内容展示，不硬编码新的口径。

## 6. 共享注册依赖

页面函数由 D 实现；以下注册项由 B 按 `SFCR-D-002` 应用：

- `VIEW_META.analytics`
- `NAV.room_admin` 的 analytics 项
- `NAV.sys_admin` 的“运营”组
- `VIEWS.analytics`
- `ACTIONS['analytics-range']`
- `ACTIONS['analytics-room']`

D 不直接修改上述共享注册表。未应用前页面无法通过导航进入。

## 7. 加载、错误与安全

- 加载状态沿用 `renderView` 的 skeleton。
- 错误状态沿用 `errorView`；401 由 `api()` 统一回登录。
- 所有动态文本使用 `esc()`；数字直接来自后端统计结果。
- 不新增事件监听器，沿用 `data-action` 事件委托。
- 不引入 XSS 风险较高的 `innerHTML` 拼接后未转义内容。

## 8. 验收要点

- 房间管理员、系统管理员均可进入分析页（依赖 B 应用 SFCR）。
- 日期范围切换请求包含正确的 `date_from`/`date_to`。
- 房间筛选选项不随响应缩小；切换后仅表格/图表变化。
- 7 类 CSV 下载入口存在，文件名和内容正常。
- 预约时段利用率文案不写成“实际座位使用率”。
- 原有概览、系统、资源等页面不受影响。
