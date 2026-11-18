# SFCR-D-001：仓储端口增加运营分析与导出协议方法

| 字段 | 内容 |
|---|---|
| 编号 | `SFCR-D-001` |
| 发起人 | D（运营分析与报表模块） |
| 处理人 | B（共享文件集成） |
| 状态 | 已应用、已验收（D 侧）；B 侧登记待补 |
| 申请日期 | 2026-11-15（历史申请日期） |
| 原申请目标分支 | `feature/analytics-module`（历史） |
| 实际实施分支 | `feature/analytics-module-v1.2` |
| 实际实施提交 | `5f36aa5ede921c062aeb0df4a7e68798f27d75ed` |
| 目标文件 | `智慧自习室预约管理系统/seat_reservation/src/seat_reservation/domain/ports.py` |
| 实际修改位置 | `domain/ports.py` 第 230–235 行，`admin_stats` 与 `teacher_stats` 之间 |
| 申请范围 | 仅增加 `analytics_range`、`export_rows` 两个协议方法声明 |
| 实际实施范围 | SFCR 授权的协议声明 + D 模块自身业务实现（见 §0.2） |
| B 应用/复核 | 仓库中未见单独的 `chore/shared-integration` 应用提交；实际随 D 模块提交进入分支，B 复核记录待补 |

> 历史申请说明（原文）：本文档仅作为本地 SFCR 材料，不纳入本次 Git 提交。
>
> 实际实施后，本文档已纳入 `docs/collaboration/`，并随 D 模块提交进入 `feature/analytics-module-v1.2`。

---

## 0. 实施记录

### 0.1 实际应用情况

本 SFCR 请求的两个协议方法已经写入实际代码：

```python
    def analytics_range(
        self, *, date_from: date, date_to: date, room_id: str | None = None
    ) -> dict[str, Any]: ...
    def export_rows(
        self, kind: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> tuple[list[str], list[list[Any]]]: ...
```

- 实际文件：`domain/ports.py`
- 实际位置：第 230–235 行
- 实际提交：`5f36aa5 feat(analytics): implement operations analytics and exports`
- 实施分支：`feature/analytics-module-v1.2`
- 实施日期：2026-11-17

实际 `SqliteRepository` 已提供同名、同参数、同返回类型的实现。通过 `inspect.signature` 核对，协议与实现一致：

```text
protocol.analytics_range (self, *, date_from: 'date', date_to: 'date', room_id: 'str | None' = None) -> 'dict[str, Any]'
impl.analytics_range     (self, *, date_from: 'date', date_to: 'date', room_id: 'str | None' = None) -> 'dict[str, Any]'
protocol.export_rows     (self, kind: 'str', *, date_from: 'date | None' = None, date_to: 'date | None' = None) -> 'tuple[list[str], list[list[Any]]]'
impl.export_rows         (self, kind: 'str', *, date_from: 'date | None' = None, date_to: 'date | None' = None) -> 'tuple[list[str], list[list[Any]]]'
```

### 0.2 申请范围与实际实施范围

本 SFCR 的授权范围**只有** `domain/ports.py` 中的两个协议方法声明，不包含以下 D 模块业务实现：

- `storage/repository.py` 中两个方法的 SQL/查询实现；
- `storage/analytics_queries.py`、`storage/export_queries.py`；
- `domain/engine.py` 的日期规则、权限和业务校验；
- `api/routes/admin_routes.py` 的 `/admin/analytics` 路由；
- `web/app.js` 的运营分析页面与样式；
- D 模块的三个测试文件。

上述文件属于 D 模块自身的功能实现，随 D 提交一并合入，但不应被视为本 SFCR 的授权范围。

### 0.3 实际验收（SFCR-D-001 独立范围）

- `domain/ports.py:230–235` 已增加 `analytics_range`、`export_rows` 两个 Protocol 方法声明。
- 两个方法的参数类型、返回类型与 `SqliteRepository` 实现逐项一致；`inspect.signature` 核对结果见 §0.1。
- `DomainEngine.admin_analytics` 在 `engine.py:2567` 调用 `repo.analytics_range(...)`，传入解析后的 `date_from`、`date_to` 和可选 `room_id`。
- `DomainEngine.export_rows` 在 `engine.py:2585` 调用 `repo.export_rows(...)`，传入 `kind`、`date_from`、`date_to`。
- 本 SFCR 不把前端页面、浏览器入口、图表或 CSV 下载行为作为独立验收内容。

### 0.3.1 D 模块整体实施测试背景（不属于本 SFCR 独立验收）

以下数据是 D 模块整体实施记录，**不是 SFCR-D-001 的独立验收结果**：

- D 模块测试：`23 passed`
- 完整 Python 测试套件：`200 passed, 1 skipped`（跳过项为未安装 Playwright 的 `tests/test_e2e.py`）
- `node --check app.js`：通过
- 浏览器端运营分析与七类 CSV 导出验收已完成

这些结果用于说明 D 模块整体状态，不改变 D-001 只覆盖 `ports.py` 协议声明的授权边界。

### 0.4 与申请时信息的差异

| 项目 | 申请时信息 | 实际实施 |
|---|---|---|
| 分支 | `feature/analytics-module` | `feature/analytics-module-v1.2` |
| 状态 | 本地草案，等待 B 审核并应用 | 已应用并完成 D 侧验收；B 侧登记待补 |
| 应用方式 | 计划由 B 在 `chore/shared-integration` 应用 | 实际随 D 的 `feature/analytics-module-v1.2` 提交应用 |
| 测试基线 | 现有 115 passed / 1 skipped | 完整套件 200 passed / 1 skipped（D 模块整体背景，见 §0.3.1） |
| 测试文件 | 计划中的 `tests/test_analytics.py` | 实际拆分为 storage / engine / api 三个测试文件 |
| 行号 | 基于提交 `6cb1c5c` 的申请时行号 | 实际为 `ports.py:230–235` |

---

## 1. 申请目的

D 模块需要在 `storage/repository.py` 新增两个查询入口：

- `analytics_range`：按日期区间和可选自习室返回运营分析数据；
- `export_rows`：按导出类型和日期区间返回 CSV 表头与数据行。

`domain/engine.py` 的 `DomainEngine` 只依赖 `domain/ports.py` 的 `Repository` 协议，不能直接依赖 `storage/repository.py`。因此需要先在协议中声明这两个方法，再由 `SqliteRepository` 实现。

本申请**只增加协议声明**，不包含业务规则、SQL 实现或数据库变更。

---

## 2. 修改文件及具体位置

文件：

```text
智慧自习室预约管理系统/seat_reservation/src/seat_reservation/domain/ports.py
```

位置：`# -- 统计` 分区，现有 `admin_stats` 之后、`teacher_stats` 之前。

以下为申请时基于提交 `6cb1c5c` 的历史行号：

| 行号 | 内容 |
|---|---|
| 227 | `# -- 统计` 分区注释 |
| 228 | `seat_state_counts` |
| 229 | `admin_stats` |
| 230 | `teacher_stats` |
| 231 | `student_stats` |
| 232 | `today_active_seat_ids` |

申请时的建议插入位置：

```text
admin_stats（229）
    ↓
analytics_range（新增）
export_rows（新增）
    ↓
teacher_stats（230）
```

实际实施后，两个方法位于 `ports.py` 第 230–235 行，仍在 `admin_stats` 与 `teacher_stats` 之间。

---

## 3. 当前源码情况（申请时）

`ports.py` 现有导入已经包含 `date`、`Any`，不需要新增 import：

```python
from datetime import date, datetime
from typing import Any, Protocol, Sequence
```

申请时统计分区：

```python
    # -- 统计 -------------------------------------------------------------
    def seat_state_counts(self, day: date, start_slot: int, end_slot: int, room_id: str | None = None) -> dict[str, int]: ...
    def admin_stats(self, now: datetime, room_id: str | None = None) -> dict[str, Any]: ...
    def teacher_stats(self, class_name: str, now: datetime) -> dict[str, Any]: ...
    def student_stats(self, user_id: str, now: datetime) -> dict[str, Any]: ...
    def today_active_seat_ids(self, day: date, now: datetime) -> set[str]: ...
```

申请时协议中**没有** `analytics_range`、`export_rows`。`storage/repository.py` 的 `SqliteRepository` 是结构化实现（没有显式 `class SqliteRepository(Repository)` 声明），协议方法只影响类型约定，不会在运行时做 `isinstance` 检查。

实际实施后，两个方法已加入该分区，`SqliteRepository` 已同步实现。

---

## 4. 建议修改内容

在 `admin_stats` 之后增加两个方法声明：

```python
    def analytics_range(
        self, *, date_from: date, date_to: date, room_id: str | None = None
    ) -> dict[str, Any]: ...
    def export_rows(
        self, kind: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> tuple[list[str], list[list[Any]]]: ...
```

### 4.1 方法签名说明

#### `analytics_range`

```python
def analytics_range(
    self, *, date_from: date, date_to: date, room_id: str | None = None
) -> dict[str, Any]: ...
```

| 项目 | 说明 |
|---|---|
| `date_from` / `date_to` | 必填，闭区间；由 `DomainEngine.admin_analytics` 计算默认 7 天或校验后的区间后传入 |
| `room_id` | 可选；为空表示全部自习室 |
| 返回 | `dict[str, Any]`，包含 `range/kpi/daily/peak_hours/rooms/violations/ratings/waitlist/complaints` |
| 事务 | 由调用方 `AppContext.engine()` 开启读事务；仓储方法不自行提交 |
| 业务规则 | 不包含权限、日期上限、房间存在性校验；这些由 `DomainEngine` 负责 |

#### `export_rows`

```python
def export_rows(
    self, kind: str, *, date_from: date | None = None, date_to: date | None = None
) -> tuple[list[str], list[list[Any]]]: ...
```

| 项目 | 说明 |
|---|---|
| `kind` | 7 类之一：`reservations`、`violations`、`audit`、`complaints`、`blacklist`、`credit_logs`、`waitlist` |
| `date_from` / `date_to` | 可选，闭区间；为空表示不限制日期 |
| 返回 | `(headers, rows)`，与现有 `DomainEngine.export_rows` 返回结构一致 |
| 事务 | 由调用方开启读事务；仓储方法不自行提交 |
| 业务规则 | 不校验 `PR-16` 权限；权限由 `DomainEngine.export_rows` 负责；未知 `kind` 的拒绝由 engine 和 repository 共同防护 |

---

## 5. 完整代码片段

### 5.1 修改前

```python
    # -- 统计 -------------------------------------------------------------
    def seat_state_counts(self, day: date, start_slot: int, end_slot: int, room_id: str | None = None) -> dict[str, int]: ...
    def admin_stats(self, now: datetime, room_id: str | None = None) -> dict[str, Any]: ...
    def teacher_stats(self, class_name: str, now: datetime) -> dict[str, Any]: ...
    def student_stats(self, user_id: str, now: datetime) -> dict[str, Any]: ...
    def today_active_seat_ids(self, day: date, now: datetime) -> set[str]: ...
```

### 5.2 修改后

```python
    # -- 统计 -------------------------------------------------------------
    def seat_state_counts(self, day: date, start_slot: int, end_slot: int, room_id: str | None = None) -> dict[str, int]: ...
    def admin_stats(self, now: datetime, room_id: str | None = None) -> dict[str, Any]: ...
    def analytics_range(
        self, *, date_from: date, date_to: date, room_id: str | None = None
    ) -> dict[str, Any]: ...
    def export_rows(
        self, kind: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> tuple[list[str], list[list[Any]]]: ...
    def teacher_stats(self, class_name: str, now: datetime) -> dict[str, Any]: ...
    def student_stats(self, user_id: str, now: datetime) -> dict[str, Any]: ...
    def today_active_seat_ids(self, day: date, now: datetime) -> set[str]: ...
```

### 5.3 精确 diff

```diff
--- a/智慧自习室预约管理系统/seat_reservation/src/seat_reservation/domain/ports.py
+++ b/智慧自习室预约管理系统/seat_reservation/src/seat_reservation/domain/ports.py
@@
     # -- 统计 -------------------------------------------------------------
     def seat_state_counts(self, day: date, start_slot: int, end_slot: int, room_id: str | None = None) -> dict[str, int]: ...
     def admin_stats(self, now: datetime, room_id: str | None = None) -> dict[str, Any]: ...
+    def analytics_range(
+        self, *, date_from: date, date_to: date, room_id: str | None = None
+    ) -> dict[str, Any]: ...
+    def export_rows(
+        self, kind: str, *, date_from: date | None = None, date_to: date | None = None
+    ) -> tuple[list[str], list[list[Any]]]: ...
     def teacher_stats(self, class_name: str, now: datetime) -> dict[str, Any]: ...
     def student_stats(self, user_id: str, now: datetime) -> dict[str, Any]: ...
     def today_active_seat_ids(self, day: date, now: datetime) -> set[str]: ...
```

---

## 6. 调用方与实现方

| 角色 | 位置 | 关系 |
|---|---|---|
| 协议定义 | `domain/ports.py` `Repository` | 本 SFCR 增加声明 |
| 实现方 | `storage/repository.py` `SqliteRepository` | D 在计划锚点实现两个方法 |
| 调用方 | `domain/engine.py` `DomainEngine.admin_analytics` | 调用 `repo.analytics_range(...)` |
| 调用方 | `domain/engine.py` `DomainEngine.export_rows` | 调用 `repo.export_rows(...)` |
| 测试（申请时计划） | `tests/test_analytics.py` | 历史申请时的计划路径；实际拆分为 storage / engine / api 三个测试文件 |
| 装配 | `service/container.py` `AppContext.engine()` | 构造 `SqliteRepository`，无需改 |

`analytics_range` 内部调用：

```text
storage/analytics_queries.py
  daily_summary / peak_hours / room_ranking / violation_trend /
  rating_distribution / waitlist_funnel / complaint_sla
```

`export_rows` 内部：

```text
现有 3 类：list_reservations / list_violations / list_audit（补传 date_from/date_to）
新增 4 类：storage/export_queries.py 的 complaints / blacklist / credit_logs / waitlist
```

这样协议只表达“仓储能力”，不把 SQL 和业务规则放进 `ports.py`。

---

## 7. 影响范围

### 7.1 运行时代码

- **无运行时行为变化**：仅增加 `typing.Protocol` 方法声明；项目没有 `runtime_checkable` 或 `isinstance(..., Repository)` 使用。
- `SqliteRepository` 是结构化实现，不需要改类声明；D 实现方法后自然满足协议。
- 申请时记录的“现有 115 个测试不应受影响”是历史基线；完整套件 **200 passed / 1 skipped** 属于 D 模块整体实施背景（见 §0.3.1），不作为 D-001 的独立验收结论。

### 7.2 类型检查/静态分析

- 如果未来启用 mypy/pyright，`SqliteRepository` 必须实现这两个方法；D 的实现已同步提交。
- 其他模块没有实现 `Repository` 的类（当前搜索未发现第二实现），因此不需要其他成员补方法。

### 7.3 其他模块

| 模块 | 影响 |
|---|---|
| A 账号 | 无；A 只改用户/会话分区 |
| B 资源 | 无；B 的封禁/座位方法与统计协议无冲突 |
| C 预约 | 无；C 的预约/候补方法仍按原协议 |
| D 分析 | 提供协议入口；不改变任何现有接口 |

### 7.4 数据库/接口

- 不新增表、字段、迁移、参数。
- 不改变任何 HTTP 路径、请求/响应结构。
- 不改变权限规则；`PR-12`/`PR-16` 仍在 engine 中判断。

---

## 8. 依赖关系

| 依赖 | 说明 | 时序 |
|---|---|---|
| D 的 `repository.py` 实现 | `analytics_range` / `export_rows` 必须在 `SqliteRepository` 中实现 | 与 SFCR 同批或之后 |
| D 的 `analytics_queries.py` / `export_queries.py` | 仓储方法内部调用 | 先于/同时于仓储实现 |
| B 应用 SFCR | 原流程要求 `ports.py` 由 B 修改 | 申请时计划在 `chore/shared-integration` 应用；实际随 D 提交应用，见 §0 |
| C 的新路由契约冒烟 | `test_new_routes_contract.py` 会覆盖 `/admin/analytics` | C 维护；D 提供接口说明 |

> 本 SFCR 不依赖 C 的续约/候补实现，也不依赖 B 的迁移字段。

---

## 9. 验收方式

1. **应用 diff**：确认只增加两个协议方法，位置在 `admin_stats` 与 `teacher_stats` 之间，未改其他行。
2. **D 复核**：确认签名与 `repository.py` 实现、`engine.py` 调用一致。
3. **历史基线**：申请时记录为 `115 passed / 1 skipped`；该数字仅作历史申请信息，不作为 D-001 的独立验收结论。D 模块整体实施测试背景见 §0.3.1。
4. **接口验证**：`DomainEngine.admin_analytics` / `DomainEngine.export_rows` 对 Repository 方法的调用参数与协议一致，调用成功，不出现 `AttributeError`。
5. **静态检查（如启用）**：`SqliteRepository` 满足 `Repository` 协议；无未实现方法告警。
6. **无副作用**：不新增迁移/参数/表；不改变现有导出 3 类的表头与行数。

---

## 10. 备注

- 本 SFCR 只包含 `ports.py` 协议声明；不包含 `storage/repository.py`、`domain/engine.py` 的实现。
- 若 B 认为 `export_rows` 应放在协议文件末尾，而不是 `admin_stats` 之后，可调整位置；方法签名不变。
- 无论最终导出类型是 3 类还是 7 类，`export_rows` 的方法签名都可复用；具体 `kind` 白名单由 `DomainEngine` 维护。
- 实际实施已采用 7 类导出白名单，并由 `DomainEngine.export_rows` 校验。
- 实际实施方式与原申请流程存在差异：本 SFCR 的 `ports.py` 声明随 D 的 `feature/analytics-module-v1.2` 提交应用，未单独形成 B 的 `chore/shared-integration` 提交。该差异已在此记录，B 侧复核与 SHA 登记待补。
