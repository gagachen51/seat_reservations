# SFCR-D-002：app.js 共享注册表增加运营分析页面入口（room_admin + sys_admin）

| 字段 | 内容 |
|---|---|
| 编号 | `SFCR-D-002` |
| 发起人 | D（运营分析与报表模块） |
| 处理人 | B（共享文件集成） |
| 状态 | 已应用、已验收（D 侧）；B 侧登记待补 |
| 申请日期 | 2026-11-15（2026-11-16 补充 `sys_admin` 入口） |
| 原申请目标分支 | `feature/analytics-module`（历史） |
| 实际实施分支 | `feature/analytics-module-v1.2` |
| 实际实施提交 | `5f36aa5ede921c062aeb0df4a7e68798f27d75ed` |
| 目标文件 | `智慧自习室预约管理系统/seat_reservation/src/seat_reservation/web/app.js` |
| 实际修改位置 | `VIEW_META:28`、`NAV.room_admin:425`、`NAV.sys_admin:432`、`VIEWS:2431`、`ACTIONS:2479–2480` |
| 申请范围 | 仅五个共享注册点：`VIEW_META`、`NAV.room_admin`、`NAV.sys_admin`、`VIEWS`、`ACTIONS` |
| 实际实施范围 | SFCR 授权的五个注册点 + D 模块自身页面/图表/样式实现（见 §0.2） |
| B 应用/复核 | 仓库中未见单独的 `chore/shared-integration` 应用提交；实际随 D 模块提交进入分支，B 复核记录待补 |

> 历史申请说明（原文）：本文档仅作为本地 SFCR 材料，不纳入本次 Git 提交。
>
> 实际实施后，本文档已纳入 `docs/collaboration/`，并随 D 模块提交进入 `feature/analytics-module-v1.2`。

---

## 0. 实施记录

### 0.1 实际应用情况

本 SFCR 请求的五个注册点均已进入实际代码：

```javascript
// VIEW_META
analytics: ['分析', '按日期区间查看运营指标'],

// NAV.room_admin
{ title: '运营', items: [['overview', '概览', 'overview'], ['analytics', '分析', 'overview'], ['floor', '现场', 'floor'], ['resources', '资源', 'floor'], ['search', '预约查询', 'search']] },

// NAV.sys_admin
{ title: '运营', items: [['analytics', '分析', 'overview']] },

// VIEWS
analytics: analyticsView,

// ACTIONS
'analytics-range': (el) => setAnalyticsRange(el.dataset.range),
'analytics-room': (el) => setAnalyticsRoom(el.dataset.room),
```

- 实际文件：`web/app.js`
- 实际位置：
  - `VIEW_META`：第 28 行
  - `NAV.room_admin`：第 425 行
  - `NAV.sys_admin`：第 432 行
  - `VIEWS`：第 2431 行
  - `ACTIONS`：第 2479–2480 行
- 实际提交：`5f36aa5 feat(analytics): implement operations analytics and exports`
- 实施分支：`feature/analytics-module-v1.2`
- 实施日期：2026-11-17

`analyticsView`、`setAnalyticsRange`、`setAnalyticsRoom` 已作为函数声明存在于同一文件，注册项引用有效。

### 0.2 申请范围与实际实施范围

本 SFCR 的授权范围**只有** `web/app.js` 中五个共享注册点的增量：

1. `VIEW_META.analytics`
2. `NAV.room_admin` 的 `analytics` 项
3. `NAV.sys_admin` 的“运营”组及 `analytics` 项
4. `VIEWS.analytics`
5. `ACTIONS['analytics-range']`、`ACTIONS['analytics-room']`

以下内容属于 D 模块自身的业务实现，**不属于**本 SFCR 授权范围：

- `analyticsView`、`analyticsHtml`、`trendChart`、`hourlyChart` 等页面/图表函数；
- 分析页面的 HTML、SVG、筛选状态和请求逻辑；
- `web/styles.css` 中的分析页样式；
- `web/index.html` 的静态资源版本更新；
- `api/routes/admin_routes.py` 的 `/admin/analytics` 路由；
- 分析查询层、DomainEngine、仓储与测试文件。

这些内容随 D 的 `feature/analytics-module-v1.2` 提交一并合入，但不应被视为本 SFCR 的共享注册授权。

### 0.3 实际验收（SFCR-D-002 独立范围）

- `VIEW_META.analytics`、`NAV.room_admin`、`NAV.sys_admin`、`VIEWS.analytics`、`ACTIONS['analytics-range']`、`ACTIONS['analytics-room']` 五处注册点均存在，行号见 §0.1。
- 房间管理员 `A9001` 登录：左侧“运营”组出现“分析”，点击可进入分析页面。
- 系统管理员 `Y0001` 登录：左侧出现“运营”组及“分析”，点击可进入分析页面。
- 页面标题“分析”、副标题“按日期区间查看运营指标”正确；标题来自 `VIEW_META` 注册。
- 点击“近 7 天 / 近 30 天 / 近 90 天”会触发 `setAnalyticsRange` 对应动作。
- 点击自习室筛选按钮会触发 `setAnalyticsRoom` 对应动作。
- 页面无可见 `ReferenceError` / `TypeError`。当前 IAB 未暴露控制台读取接口，未逐条导出 `console.error`。
- 本 SFCR 不把 KPI/趋势图/时段图/房间表的数据正确性、CSV 导出或表格布局作为独立验收内容。

### 0.3.1 D 模块整体实施测试背景（不属于本 SFCR 独立验收）

以下数据是 D 模块整体实施记录，**不是 SFCR-D-002 的独立验收结果**：

- D 模块测试：`23 passed`
- 完整 Python 测试套件：`200 passed, 1 skipped`（跳过项为未安装 Playwright 的 E2E）
- 七类 CSV 导出、分析页表格响应式布局等整体功能验收

### 0.4 与申请时信息的差异

| 项目 | 申请时信息 | 实际实施 |
|---|---|---|
| 分支 | `feature/analytics-module` | `feature/analytics-module-v1.2` |
| 状态 | 本地草案，等待 B 审核并应用 | 已应用并完成 D 侧验收；B 侧登记待补 |
| 应用方式 | 计划由 B 在 `chore/shared-integration` 应用 | 实际随 D 的 `feature/analytics-module-v1.2` 提交应用 |
| 行号 | 基于提交 `6cb1c5c` 的申请时行号 | 实际行号见 §0.1 |
| 页面实现 | 申请时约定由 D 在业务分支完成 | 已完成，且不计入 SFCR 授权范围 |
| 验收 | 计划验证导航、动作与控制台无异常 | 导航、动作、筛选均实测；导出不属于本 SFCR 验收范围；控制台读取能力受限，已注明 |

---

## 1. 申请目的

D 模块新增运营分析页面 `analyticsView`。按已确认的 SRS PR-12 与项目负责人裁决，`room_admin` 和 `sys_admin` 都可以查看全校运营分析，因此页面需要：

- 在房间管理员左侧导航出现“分析”入口；
- 在系统管理员左侧导航也出现“分析”入口；
- 使用 `VIEW_META` 提供页面标题/副标题；
- 通过 `VIEWS` 把 `analytics` 路由映射到 `analyticsView`；
- 通过 `ACTIONS` 处理“7/30/90 天”和“自习室筛选”两个交互。

`app.js` 的 `NAV`、`VIEW_META`、`VIEWS`、`ACTIONS` 属于共享注册表，按 v1.2 只能由 B 修改。本 SFCR 只申请注册项，不包含页面实现、图表实现或业务逻辑。

> 补充理由：`allowedViews()` 只从 `NAV` 生成可访问页面集合。如果只给 `room_admin` 增加 NAV 项，`sys_admin` 即使 API 有权限，也无法通过界面进入 `#/analytics`；因此必须在 `NAV.sys_admin` 中同步增加入口。

---

## 2. 修改文件及具体位置

文件：

```text
智慧自习室预约管理系统/seat_reservation/src/seat_reservation/web/app.js
```

### 2.1 申请时行号（历史，基于提交 `6cb1c5c`）

| 对象 | 申请时行号 | 插入位置 |
|---|---|---|
| `VIEW_META` | 21–40 | `overview`（27）之后、`floor`（28）之前 |
| `NAV` 的 `room_admin` 第一组 | 424 | `overview` 项之后 |
| `NAV` 的 `sys_admin` | 430–432 | 在“系统”组之前新增“运营”组 |
| `VIEWS` | 2028–2047 | `overview: overviewView`（2034）之后 |
| `ACTIONS` | 2079 起 | `refresh`（2081）之后 |

### 2.2 实际行号（`feature/analytics-module-v1.2`）

| 对象 | 实际行号 |
|---|---|
| `VIEW_META.analytics` | 28 |
| `NAV.room_admin` | 425 |
| `NAV.sys_admin` | 432 |
| `VIEWS.analytics` | 2431 |
| `ACTIONS['analytics-range']` | 2479 |
| `ACTIONS['analytics-room']` | 2480 |

不修改：

- `ICONS`：复用已有 `overview` 图标，不新增图标。
- `AFTER`：本期分析页不做自动刷新；切换日期/房间时由 `renderView()` 重新请求。若后续需要定时刷新，另提 SFCR。
- `state`：D 使用模块内局部变量保存筛选状态，不往共享 `state` 增加字段。
- `homeView` / `allowedViews`：由 `NAV` 自动生效，不单独改。

---

## 3. 当前源码情况（申请时）

### 3.1 `VIEW_META`

```javascript
  const VIEW_META = {
    seats: ['找座位', '按日期与时段查看空闲座位'],
    reservations: ['我的预约', '凭证、签到、暂离与签退'],
    credit: ['信用', '违规记录与信用分明细'],
    feedback: ['反馈', '投诉建议与处理结果'],
    messages: ['消息', '预约提醒与处理通知'],
    overview: ['概览', '实时使用率与近 7 日趋势'],
    floor: ['现场', '座位状态与现场处置'],
    resources: ['资源', '自习室与座位的增改、启用/停用'],
    search: ['预约查询', '按状态、日期与关键词检索'],
    complaints: ['投诉', '处理学生反馈并留痕'],
    reviews: ['评价', '查看座位与环境评价并回复'],
    appeals: ['违规申诉', '审核学生申诉，成立则撤销并回退信用'],
    students: ['学生', '信用档案与黑名单'],
    class: ['班级看板', '仅统计本班汇总'],
    params: ['参数', '业务规则数值，仅对新预约生效'],
    users: ['账号', '角色与权限分配'],
    audit: ['审计', '关键操作留痕，不可修改'],
    system: ['系统', '运行状态、调度器与导出'],
  };
```

申请时没有 `analytics` 条目；实际实施后已增加。

### 3.2 `NAV`

```javascript
  const NAV = {
    student: [
      { title: '预约', items: [['seats', '找座位', 'seats'], ['reservations', '我的预约', 'calendar']] },
      { title: '账户', items: [['credit', '信用', 'credit'], ['feedback', '反馈', 'feedback'], ['messages', '消息', 'bell']] },
    ],
    room_admin: [
      { title: '运营', items: [['overview', '概览', 'overview'], ['floor', '现场', 'floor'], ['resources', '资源', 'floor'], ['search', '预约查询', 'search']] },
      { title: '服务', items: [['complaints', '投诉', 'feedback'], ['reviews', '评价', 'star'], ['appeals', '违规申诉', 'alert'], ['students', '学生', 'users']] },
    ],
    teacher: [
      { title: '教学', items: [['class', '班级看板', 'class']] },
    ],
    sys_admin: [
      { title: '系统', items: [['params', '参数', 'params'], ['resources', '资源', 'floor'], ['users', '账号', 'users'], ['audit', '审计', 'audit'], ['system', '系统', 'system']] },
    ],
  };
```

申请时 `room_admin` 没有 `analytics` 入口；`sys_admin` 只有“系统”组。实际实施后两处均已增加，具体见 §0.1。

### 3.3 `VIEWS`

```javascript
  const VIEWS = {
    seats: seatsView,
    reservations: reservationsView,
    credit: creditView,
    feedback: feedbackView,
    messages: messagesView,
    overview: overviewView,
    floor: floorView,
    resources: resourcesView,
    search: searchView,
    complaints: complaintsView,
    reviews: reviewsView,
    appeals: appealsView,
    students: studentsView,
    class: classView,
    params: paramsView,
    users: usersView,
    audit: auditView,
    system: systemView,
  };
```

申请时没有 `analytics: analyticsView`；实际实施后已增加。

### 3.4 `ACTIONS`

```javascript
  const ACTIONS = {
    nav: (el) => go(el.dataset.view),
    refresh: () => renderView(),
    'close-sheet': () => closeSheet(),
    logout: () => logout(),
    'user-menu': () => openUserMenu(),
    ...
```

申请时没有分析页的日期/房间切换动作；实际实施后已增加。

---

## 4. 建议修改内容

### 4.1 `VIEW_META`

在 `overview` 后增加：

```javascript
    analytics: ['分析', '按日期区间查看运营指标'],
```

### 4.2 `NAV.room_admin`

在“运营”组 `overview` 后增加：

```javascript
['analytics', '分析', 'overview']
```

修改后该组为：

```javascript
{ title: '运营', items: [['overview', '概览', 'overview'], ['analytics', '分析', 'overview'], ['floor', '现场', 'floor'], ['resources', '资源', 'floor'], ['search', '预约查询', 'search']] },
```

### 4.3 `NAV.sys_admin`

在 `sys_admin` 的“系统”组之前新增“运营”组：

```javascript
    sys_admin: [
      { title: '运营', items: [['analytics', '分析', 'overview']] },
      { title: '系统', items: [['params', '参数', 'params'], ['resources', '资源', 'floor'], ['users', '账号', 'users'], ['audit', '审计', 'audit'], ['system', '系统', 'system']] },
    ],
```

### 4.4 `VIEWS`

在 `overview: overviewView,` 后增加：

```javascript
    analytics: analyticsView,
```

### 4.5 `ACTIONS`

在 `refresh` 后增加两个动作：

```javascript
    'analytics-range': (el) => setAnalyticsRange(el.dataset.range),
    'analytics-room': (el) => setAnalyticsRoom(el.dataset.room),
```

---

## 5. 完整代码片段

### 5.1 `VIEW_META`

```javascript
  const VIEW_META = {
    ...
    overview: ['概览', '实时使用率与近 7 日趋势'],
    analytics: ['分析', '按日期区间查看运营指标'],
    floor: ['现场', '座位状态与现场处置'],
    ...
  };
```

### 5.2 `NAV`

```javascript
    room_admin: [
      { title: '运营', items: [['overview', '概览', 'overview'], ['analytics', '分析', 'overview'], ['floor', '现场', 'floor'], ['resources', '资源', 'floor'], ['search', '预约查询', 'search']] },
      { title: '服务', items: [['complaints', '投诉', 'feedback'], ['reviews', '评价', 'star'], ['appeals', '违规申诉', 'alert'], ['students', '学生', 'users']] },
    ],
    ...
    sys_admin: [
      { title: '运营', items: [['analytics', '分析', 'overview']] },
      { title: '系统', items: [['params', '参数', 'params'], ['resources', '资源', 'floor'], ['users', '账号', 'users'], ['audit', '审计', 'audit'], ['system', '系统', 'system']] },
    ],
```

### 5.3 `VIEWS`

```javascript
  const VIEWS = {
    ...
    overview: overviewView,
    analytics: analyticsView,
    floor: floorView,
    ...
  };
```

### 5.4 `ACTIONS`

```javascript
  const ACTIONS = {
    nav: (el) => go(el.dataset.view),
    refresh: () => renderView(),
    'analytics-range': (el) => setAnalyticsRange(el.dataset.range),
    'analytics-room': (el) => setAnalyticsRoom(el.dataset.room),
    'close-sheet': () => closeSheet(),
    ...
  };
```

### 5.5 精确 diff

```diff
--- a/智慧自习室预约管理系统/seat_reservation/src/seat_reservation/web/app.js
+++ b/智慧自习室预约管理系统/seat_reservation/src/seat_reservation/web/app.js
@@
     messages: ['消息', '预约提醒与处理通知'],
     overview: ['概览', '实时使用率与近 7 日趋势'],
+    analytics: ['分析', '按日期区间查看运营指标'],
     floor: ['现场', '座位状态与现场处置'],
@@
     room_admin: [
-      { title: '运营', items: [['overview', '概览', 'overview'], ['floor', '现场', 'floor'], ['resources', '资源', 'floor'], ['search', '预约查询', 'search']] },
+      { title: '运营', items: [['overview', '概览', 'overview'], ['analytics', '分析', 'overview'], ['floor', '现场', 'floor'], ['resources', '资源', 'floor'], ['search', '预约查询', 'search']] },
       { title: '服务', items: [['complaints', '投诉', 'feedback'], ['reviews', '评价', 'star'], ['appeals', '违规申诉', 'alert'], ['students', '学生', 'users']] },
     ],
@@
     teacher: [
       { title: '教学', items: [['class', '班级看板', 'class']] },
     ],
     sys_admin: [
+      { title: '运营', items: [['analytics', '分析', 'overview']] },
       { title: '系统', items: [['params', '参数', 'params'], ['resources', '资源', 'floor'], ['users', '账号', 'users'], ['audit', '审计', 'audit'], ['system', '系统', 'system']] },
     ],
@@
     messages: messagesView,
     overview: overviewView,
+    analytics: analyticsView,
     floor: floorView,
@@
   const ACTIONS = {
     nav: (el) => go(el.dataset.view),
     refresh: () => renderView(),
+    'analytics-range': (el) => setAnalyticsRange(el.dataset.range),
+    'analytics-room': (el) => setAnalyticsRoom(el.dataset.room),
     'close-sheet': () => closeSheet(),
```

---

## 6. 依赖的 D 模块前端实现（不属于本 SFCR 补丁）

申请时约定：B 应用注册前，D 会在 `app.js` 自己的视图实现区提供以下函数。它们是 **函数声明**，会被提升，因此 `VIEWS`/`ACTIONS` 可以引用：

```javascript
  let analyticsRange = '7d';
  let analyticsRoom = '';

  function setAnalyticsRange(range) {
    analyticsRange = range;
    renderView();
  }

  function setAnalyticsRoom(roomId) {
    analyticsRoom = roomId;
    renderView();
  }

  async function analyticsView() {
    // 调用 GET /api/admin/analytics?date_from=...&date_to=...&room_id=...
    // 返回 HTML 字符串；内部使用 analyticsHtml / trendChart / hourlyChart / ratingBars
  }
```

约定：

- `analyticsView` 必须是函数声明（`async function analyticsView()`），不能是 `const analyticsView = ...`，否则在 `VIEWS` 初始化时可能未定义。
- `setAnalyticsRange`、`setAnalyticsRoom` 也必须是函数声明。
- 页面按钮使用：
  - `data-action="analytics-range" data-range="7d|30d|90d"`
  - `data-action="analytics-room" data-room="<room_id>"`
- 不使用 `state` 新增字段，不修改 `AFTER`，不新增 `ICONS`。
- 具体页面/图表实现由 D 在自己的 feature 分支完成，不放入本 SFCR。

实际实施后，上述函数和页面实现均已存在；它们仍属于 D 模块业务实现，不计入本 SFCR 的共享注册授权。

---

## 7. 影响范围

| 范围 | 影响 |
|---|---|
| 房间管理员 | 左侧“运营”组新增“分析”入口；`allowedViews()` 自动包含 `analytics` |
| 系统管理员 | 左侧新增“运营”组及“分析”入口；`allowedViews()` 自动包含 `analytics` |
| 学生/教师 | 无变化 |
| `VIEW_META` | 页面标题/副标题增加一条 |
| `VIEWS` | 增加 `analytics` 映射；未改其他映射 |
| `ACTIONS` | 增加两个动作；未改现有动作 |
| `AFTER` | 不新增；无自动刷新 |
| `ICONS` | 不新增；复用 `overview` 图标 |
| 现有页面 | 概览、现场、资源、查询、系统页不受影响 |
| API/权限 | 不变；`GET /admin/analytics` 由 D 在 `admin_routes.py` 新增 |

---

## 8. 依赖关系

| 依赖 | 说明 | 时序 |
|---|---|---|
| D 的 `analyticsView` / `setAnalyticsRange` / `setAnalyticsRoom` | 注册表引用它们 | 必须先于/同时于注册应用 |
| D 的 `GET /api/admin/analytics` | 页面数据来源 | D 的后端 PR 先合并或同一批 |
| B 应用 SFCR | 原流程要求 `app.js` 共享注册表由 B 修改 | 申请时计划在 `chore/shared-integration` 应用；实际随 D 提交应用，见 §0 |
| A 复核 | A 负责 `app.js` 注册片段复核（v1.2 规定） | B 应用后；实际复核记录待补 |
| 不需要 C/D 的其他代码 | 不依赖续约/候补/座位迁移 | — |

---

## 9. 验收方式

1. **应用 diff**：确认只改 `VIEW_META`、`NAV.room_admin`、`NAV.sys_admin`、`VIEWS`、`ACTIONS` 五处，未改其他视图。
2. **D 复核**：确认注册项与函数名一致；`analyticsView`、`setAnalyticsRange`、`setAnalyticsRoom` 已存在。
3. **运行验证**：
   - 房间管理员登录后左侧“运营”组出现“分析”；
   - 系统管理员登录后左侧“运营”组出现“分析”；
   - 两种管理员点击“分析”都能进入页面，标题/副标题正确；
   - 7/30/90 天按钮触发 `setAnalyticsRange` 对应动作；
   - 自习室按钮触发 `setAnalyticsRoom` 对应动作；
   - 页面无可见 `ReferenceError` / `TypeError`；当前 IAB 未暴露控制台读取接口，未逐条导出 `console.error`。
4. **回归**：
   - 学生/教师导航不变；
   - 系统管理员的系统配置、资源、账号、审计、系统页仍可打开；
   - 概览、现场、资源、查询、投诉、评价、申诉、学生、系统页面可正常打开；
   - 前端注册不改变后端接口或现有视图；D 模块整体测试背景见 §0.3.1，不作为本 SFCR 独立验收结论。
5. **不做自动刷新**：确认 `AFTER` 未新增 `analytics`；若后续需要，另提 SFCR。

实际验收结果见 §0.3：两角色入口、进入页面、日期/房间动作触发均通过。CSV 导出、表格布局和完整测试属于 D 模块整体实施背景（见 §0.3.1），不属于 SFCR-D-002 的独立验收结论。

---

## 10. 备注

- 本 SFCR 只包含共享注册表，不包含分析页 HTML、图表 SVG、数据请求逻辑或样式。
- 本 SFCR 已包含 `room_admin` 与 `sys_admin` 两个入口；不再需要“仅系统管理员另提 SFCR”。
- 如果后续需要“分析页自动刷新”，需要新增 `AFTER.analytics`，另提 SFCR；本期不包含。
- 管理端候补队列（可选增强）的注册不在本 SFCR；如实施，另提 SFCR-D-003。
- 实际实施方式与原申请流程存在差异：五个注册点随 D 的 `feature/analytics-module-v1.2` 提交应用，未单独形成 B 的 `chore/shared-integration` 提交。该差异已在此记录，B 侧复核与 SHA 登记待补。
