# 设计说明 · B · 资源与座位治理模块

> 文档阶段：v1.2–v2.0.3 过程记录（2026-11）。文中的版本号、测试数量、交付形态与启动方式均为当时基线；当前实现为 v3.1.0 / schema v5，采用浏览器访问的 Web 交付，口径见根目录 `README.md` 与 `CHANGELOG.md`。


> 版本：v1.2（对应《四人协作分工与开发计划-v1.2》第 6.2、8.9 节）
> 责任人：成员 B（资源与座位治理 + 架构牵头 + 共享文件守门）
> 代码基线：`智慧自习室预约管理系统/seat_reservation/`
> 当前集成基线：`integration/v1.2` @ `be8f2de`；迁移 **v4**（`resource_constraints`）；全量 **470 passed, 1 skipped**
> 适用范围：`domain/engine.py` 资源区、`storage/repository.py` 座位/封禁区、`domain/resource_rules.py`、`api/routes/resource_routes.py`、`web/app.js` 的资源/现场视图
> 说明：本文第 2～6 节的 v2/138 passed 为 B 模块首次交付的设计历史；后续 M-04/M-05/L-07/L-08 整改已升至 v4，最终测试与契约以《B模块安全整改报告.md》为准。

---

## 1. 模块边界

### 1.1 负责

| 能力 | 说明 |
|---|---|
| 座位封禁生命周期 | 创建封禁记录、未来封禁不立即生效、到期自动关闭、按记录解封、按座位整体解封 |
| 封禁列表 | 管理员查看封禁历史、起止时间、是否生效、座位当前维护来源 |
| 批量建座 | 一次最多 100 个座位，全量成功或全量回滚 |
| 停用影响预览 | 只读统计关闭自习室会影响的预约（待签到/使用中/暂离） |
| 座位跨自习室移动保护 | 存在进行中或未开始预约时禁止改写房间归属 |
| 手工维护优先级 | 管理员显式设置 `admin_status` 时清零封禁来源标记，到期封禁不覆盖人工决定 |

### 1.2 不负责（明确不碰）

| 不属于本模块 | 归属 |
|---|---|
| 预约创建、签到、暂离、签退、取消、额度与时长规则 | C（`engine.py` 预约主流程） |
| 候补加入/确认/取消、候补顺延 | C |
| 账号创建/停用/重置密码/会话 | A |
| 运营统计、CSV 导出、CI | D |
| `api/schemas.py`、`domain/params.py` | 本期不修改（新路由用文件内局部模型） |
| `api/app.py` 的 A/D router 注册 | B 唯一修改人，但只应用他人的 SFCR，不代写业务 |

---

## 2. 数据模型变更（迁移 v2）

### 2.1 迁移定义

`storage/migrations.py` 新增：

```python
Migration(2, "seat_block_lifecycle",
          ("ALTER TABLE seats ADD COLUMN maintenance_by_block INTEGER NOT NULL DEFAULT 0",))
```

- **只加列，不回填**（Q-15 技术方案默认 A）。
- 语义：`1` = 维护状态由封禁流程设置、可自动恢复；`0` = 手工维护 / 停用 / 正常，**任何封禁到期都不得自动恢复**。
- 迁移幂等：`storage/database.py::Database.migrate()` 按 `schema_migrations` 版本号跳过已应用版本，重复执行返回 `[]`。

### 2.2 为什么零回填

v1 的 `seats` 没有记录“维护来源”，无法区分“管理员手工设为维护”和“旧封禁流程设为维护”。如果按 `admin_status='maintenance'` 回填 `1`，升级后一次旧封禁到期就会把人工维护的座位恢复成可预约。零回填的代价是升级后需要一次性人工核对历史维护座位（见第 8 节），收益是**绝不会误恢复**。

### 2.3 模型变更

`domain/models.py::Seat` 增加 `maintenance_by_block: bool = False`，并加入 `Seat.as_dict()` 输出，使管理端能区分「维护 · 封禁」与「维护 · 手工」。

---

## 3. 状态机

### 3.1 新增封禁时的状态迁移（`resource_rules.seat_state_after_block`）

| 座位初始状态 | 封禁窗口 | 结果 `admin_status` | 结果 `maintenance_by_block` | 说明 |
|---|---|---|---|---|
| `available` | 覆盖当前时刻 | `maintenance` | `1` | 立即进入维护 |
| `available` | 未来 | `available`（不变） | `1` | 维护态由 `seat_views` 按时间窗派生 |
| `available` | 已过去 | `available`（不变） | `1` | 记录仍会被下次扫描关闭 |
| `maintenance` + 标记 `0`（手工维护） | 任意 | `maintenance`（不变） | `0` | 人工意图优先，不写标记 |
| `maintenance` + 标记 `1`（封禁管理） | 覆盖当前时刻 | `maintenance` | `1` | 继续由封禁管理 |
| `maintenance` + 标记 `1` | 未来 | `maintenance`（不变） | `1` | 到期后才处理 |
| `disabled` | 任意 | `disabled`（不变） | `0` | 封禁只通过时间窗生效，**绝不允许到期变 `available`** |

判定顺序就是实现顺序，代码不根据 `admin_status` 反推维护来源。

### 3.2 封禁关闭后的状态迁移（`resource_rules.seat_state_after_blocks_closed`）

| 条件 | 结果 |
|---|---|
| 仍有其他未释放封禁 | 维持现状（多封禁不提前恢复） |
| 标记 `1` 且 `admin_status='maintenance'` | `available` + 标记归零 |
| `disabled` | 保持 `disabled`，标记归零 |
| `available` | 保持 `available`，标记归零 |
| 手工维护（标记 `0`、`maintenance`） | 保持 `maintenance`，标记 `0` |

两个入口的行为差异（有意设计）：

| 触发方式 | 是否自动恢复座位 | 原因 |
|---|---|---|
| 定时扫描到期（`run_timeout_sweep` → `expire_seat_blocks`） | 是（仅标记 `1` + `maintenance`） | 封禁到期即维护结束，属客观时间驱动 |
| 既有 `DELETE /admin/seat-blocks/{seat_id}`（`unblock_seat`） | 是（同上） | 既有接口语义为“解封该座位”，保持兼容 |
| 新 `POST /admin/seat-blocks/{block_id}/release`（`release_seat_block`） | 否，需显式 `restore_seat=true` | 只关一条记录时不猜测管理员意图（v1.2 第 6.2 接口表） |

---

## 4. 接口契约

### 4.1 新增路由（`api/routes/resource_routes.py`，已在 `api/app.py` 注册）

| 方法 | 路径 | 角色 | 请求 | 成功响应 | 主要错误 |
|---|---|---|---|---|---|
| GET | `/api/admin/seat-blocks` | room_admin / sys_admin | `seat_id? room_id? active_only? page? page_size?` | 200 `{items,total,page,page_size,generated_at}` | 403 `PR-06`；401 |
| POST | `/api/admin/seat-blocks/{block_id}/release` | room_admin / sys_admin | `{reason, restore_seat?}` | 200 `{block, seat_id, seat_status, maintenance_by_block, restored, remaining_blocks}` | 404 记录不存在；400 `ST-12` 已释放；400 `PR-06` 停用座位拒绝恢复；422 原因过短 |
| POST | `/api/admin/seats/bulk` | room_admin / sys_admin | `{room_id, seats:[{id,type,capacity,has_power,is_window,note}]}` | 201 `{room_id, created[], count}` | 409 `PR-05` 重复/已存在；422 条数>100 或类型/容量非法；404 自习室不存在 |
| GET | `/api/admin/rooms/{room_id}/impact` | room_admin / sys_admin | `status?`（默认 `closed`） | 200 `{room_id, room_name, current_status, target_status, affected_count, by_status, items[:20], total, page, page_size}` | 404 自习室不存在；422 状态非法 |

> 计划中的 `DELETE /api/admin/seat-blocks/{seat_id}` 仍是 `admin_routes.py` 的既有路由，只调整了引擎内行为（关闭全部未释放封禁 + 仅在封禁管理时恢复）。`PUT /api/admin/seats/{id}` 的跨房间移动保护是行为变更，不新增路由。

### 4.2 封禁列表字段

`seat_blocks` 原字段 + 联表补充：

| 字段 | 来源 | 用途 |
|---|---|---|
| `id` / `seat_id` / `reason` / `start_at` / `end_at` / `created_by` / `created_at` / `released_at` | `seat_blocks` | 记录本体 |
| `room_id` / `room_name` | `seats` + `rooms` | 按自习室筛选与展示 |
| `seat_status` | `seats.admin_status` | 座位当前管理状态 |
| `seat_maintenance_by_block` | `seats.maintenance_by_block` | 区分「封禁管理的维护」与「手工维护」 |

`active_only=true` 只过滤 `released_at IS NULL`；**是否此刻生效由前端按 `start_at/end_at` 与当前时间判断**（`blockPhase()`），避免把“未来的封禁”漏掉。这与第 6.2 节“未来封禁期间 `admin_status` 可能仍是 `available`，管理端必须同时展示封禁记录”的展示口径一致。

### 4.3 审计动作

| action | target | 触发 |
|---|---|---|
| `seat_block` | seat | 新增封禁（含 `block_start/block_end/affected`） |
| `seat_unblock` | seat | 按座位解封（含 `released_blocks`、`restored`） |
| `seat_block_release` | seat | 按记录解封（含 `block_id`、`reason`、`restored`） |
| `seat_block_expired` | seat（actor=SYSTEM） | 定时扫描到期且状态确有变化 |
| `seat_bulk_create` | room | 批量建座 |
| `seat_update` | seat | 座位修改（既有） |

---

## 5. 代码分区与实现要点

### 5.1 新增文件

| 文件 | 内容 |
|---|---|
| `domain/resource_rules.py` | 纯规则：`is_manual_maintenance`、`block_covers`、`seat_state_after_block`、`seat_state_after_blocks_closed`、`can_restore_seat`、`has_occupying_reservation`、`bulk_limit_violation`、`duplicate_ids`、`seat_status_label`；常量 `BULK_SEAT_LIMIT=100` |
| `api/routes/resource_routes.py` | 4 个新路由 + 文件内请求模型（`SeatBlockReleaseIn`、`BulkSeatItemIn`、`BulkSeatIn`） |
| `tests/test_resource_lifecycle.py` | 23 个用例，覆盖 7 种状态组合、解封分支、批量建座、影响预览、移动保护、权限矩阵、HTTP 契约、迁移幂等 |

### 5.2 修改文件（仅本模块分区）

| 文件 | 分区/方法 | 修改内容 |
|---|---|---|
| `storage/migrations.py` | 迁移注册 | 新增 `Migration(2, "seat_block_lifecycle", ...)` |
| `domain/models.py` | `Seat` | 新增 `maintenance_by_block` 字段 + `as_dict()` 输出 |
| `domain/ports.py` | 座位封禁区 | 追加 `get_seat_block`、`get_seat_blocks`、`list_seat_blocks`、`expire_seat_blocks`、`release_seat_block` 协议 |
| `storage/repository.py` | `_seat`、`save_seat` | 读写 `maintenance_by_block` |
| `storage/repository.py` | `seat_block_overlaps` 之后的新分区 | `_seat_block`、`get_seat_block`、`get_seat_blocks`、`list_seat_blocks`、`expire_seat_blocks`、`release_seat_block`（只做 SQL，不做状态判断） |
| `domain/engine.py` | `block_seat` | 用规则函数决定状态迁移，不再无脑写 `maintenance`；审计补充封禁窗口与标记 |
| `domain/engine.py` | `unblock_seat` | 关闭全部未释放封禁；只在标记 `1` + `maintenance` 时恢复；手工维护/停用保持 |
| `domain/engine.py` | `run_timeout_sweep` | 增加 `seat_blocks_expired`，到期关闭记录并在必要时恢复座位 |
| `domain/engine.py` | `update_seat` | 跨房间移动保护（`PR-05`）；显式传 `admin_status` 时清零标记 |
| `domain/engine.py` | `unblock_seat` 之后的新分区 | `list_seat_blocks`、`release_seat_block`、`bulk_create_seats`、`room_impact` |
| `api/app.py` | router 注册 | 导入并 `include_router(resource_routes.router, prefix="/api")`（B 为唯一修改人） |
| `api/routes/admin_routes.py` | 既有 `DELETE /admin/seat-blocks/{seat_id}` | 仅补充行为说明文档串，未改签名与返回结构 |
| `web/app.js` | `resourcesView`、`openSeatAdmin`、`blockSeatSheet`、`unblockSeat` + 新函数 | 批量建座、封禁记录、按记录解封、停用影响预览；资源表区分「维护 · 封禁」/「维护 · 手工」；现场页显示封禁到期时间 |
| `web/app.js` | 共享注册表 `ACTIONS` | 新增 `seat-blocks`、`release-block`、`bulk-seat`、`room-impact`（B 唯一修改人） |
| `web/styles.css` | 文件末尾 | 追加 `.block-list`/`.block-row`/`.block-side` 等封禁列表样式，不改他人段落 |

### 5.3 事务与规则边界

1. 业务规则只在 `domain/`：仓储 `expire_seat_blocks`/`release_seat_block` 只改 `seat_blocks.released_at`；是否改 `seats.admin_status` 全部由 `resource_rules` 决定。
2. 写事务由 `AppContext.engine(write=True)` 管理，路由通过 `write_engine` 依赖注入；任一步抛 `AppError` 则整体回滚。
3. 批量建座依赖“先写后抛异常 → 回滚”实现全量原子性：`bulk_create_seats` 逐条 `save_seat`，任一条校验失败立刻抛 `ValidationFailed`/`Conflict`，外层事务回滚，**不会留下半截数据**（`test_bulk_create_seats_rolls_back_on_invalid_item` 断言）。
4. 时间由调用方注入：引擎方法与规则函数都接收 `now`，不读系统时钟；路由用 `datetime.now()`。

---

## 6. 测试说明

### 6.1 执行

```text
命令：python -m pytest -q
结果：138 passed, 1 skipped（新增 23 passed）
跳过：tests/test_e2e.py（未安装 playwright）
环境：Windows 11 / Python 3.12.13 / fastapi 0.115.6 / pydantic 2.10.4 / pytest 8.3.4
```

> 基线为 115 passed, 1 skipped；新增 `tests/test_resource_lifecycle.py` 23 个用例后为 **138 passed**。另有 2 处既有断言因迁移 v2 必须同步（见 6.3）。

### 6.2 用例与 7 种状态组合对照

| 计划要求 | 用例 |
|---|---|
| ① available + 生效封禁 → 到期恢复 | `test_available_seat_block_sets_maintenance_then_expires_to_available` |
| ② available + 未来封禁 → 当前不变、时间窗派生 | `test_future_block_does_not_change_status_and_derives_maintenance_state` |
| ③ 手工 maintenance 不被误恢复 | `test_manual_maintenance_is_not_auto_restored_by_block_expiry` |
| ④ disabled 到期绝不变 available | `test_disabled_seat_never_restored_by_block_expiry` |
| ⑤ 多封禁不提前恢复 | `test_multiple_blocks_restore_only_after_last_release` |
| ⑥ 管理员封禁期间改状态 | `test_admin_status_override_clears_block_marker` |
| ⑦ 迁移零回填安全 | `test_v1_manual_maintenance_seat_stays_manual_after_v2`、`test_v2_migration_is_idempotent_and_keeps_existing_rows` |
| 显式恢复 / 默认不改状态 | `test_release_by_record_default_keeps_seat_status_and_writes_audit`、`test_release_by_record_with_restore_restores_only_block_managed_seat` |
| disabled 拒绝恢复 | `test_release_disabled_seat_refuses_restore` |
| 按记录解封只关一条 / 整体解封 | `test_release_closes_only_target_record_and_seat_delete_closes_all` |
| 错误分支（404/400/403/422） | `test_release_by_record_error_branches`、`test_block_seat_validation` |
| 批量建座全量回滚 | `test_bulk_create_seats_rolls_back_on_invalid_item` |
| 批量建座重复/已存在/超上限/权限 | `test_bulk_create_seats_rejects_duplicate_existing_and_over_limit` |
| 停用影响预览只读 | `test_room_impact_preview_counts_and_is_read_only` |
| 座位跨房间移动 | `test_seat_move_blocked_when_active_reservations_exist`、`test_seat_move_allowed_without_active_reservations` |
| 权限矩阵（学生/教师/管理员） | `test_resource_lifecycle_permissions` |
| 封禁列表字段与过滤 | `test_seat_blocks_listing_fields_and_filters` |
| 新路由 HTTP 契约 + CSRF | `test_seat_block_routes_contract` |

### 6.3 对既有测试的两处必要改动

| 文件 | 改动 | 原因 |
|---|---|---|
| `tests/test_api.py` | `schema_version == 1` → `== 2` | B 新增迁移 v2 后健康检查必然返回 2 |
| `tests/test_api_contract.py` | 同上 | 同一断言 |

这是**唯一**两处触及他人测试文件的地方，且只改版本号常量，未改动任何路由/权限/业务断言。PR 中已注明：如需回退，只回退这两行即可（同时回退迁移 v2）。

---

## 7. 前端说明

| 位置 | 变更 |
|---|---|
| 资源页 · 自习室表 | 新增「停用影响」按钮 → `roomImpactSheet`（只读预览） |
| 资源页 · 座位表 | 状态列区分「维护 · 封禁」/「维护 · 手工」；操作列新增「封禁记录」；头部新增「封禁记录」「批量新增」与生效封禁计数 |
| 资源页 · `bulkSeatSheet` | 前缀 + 起始序号 + 数量生成编号，前端校验 1~100、序号为整数，实时预览首尾编号 |
| `seatBlocksSheet` | 封禁记录列表，显示原因、起止、是否已释放、座位当前维护来源，可直接「按记录解封」 |
| `releaseSeatBlockSheet` | 原因必填；「同时恢复座位」只在“封禁管理 + 非停用”时可勾选，停用座位给出走资源页启用的提示 |
| 现场页 · 座位弹层 | 显示当前生效封禁的原因与到期时间、未来封禁的时段；维护中座位提供「封禁记录」+「解除封禁」 |
| 现场页 · 封禁弹窗 | 说明“未来封禁不立即停用、到期只恢复封禁管理的维护”，并实时显示预计结束时间 |
| 样式 | `styles.css` 末尾追加封禁列表/表单样式，移动端（≤640px）改为纵向布局 |

前端只在 B 负责的视图函数与共享注册表（`ACTIONS`）内改动，未修改 `NAV/VIEW_META/VIEWS` 的结构，也未改 A/C/D 的视图函数。

---

## 8. 部署与升级检查（历史维护座位人工核对）

1. 升级前备份数据库；在副本上先执行 v2 迁移并演练。
2. 导出升级前 `admin_status='maintenance'` 的座位（`id`、`room_id`、`note`、`created_at`）。
3. 对每个座位查询 `seat_blocks` 中 `released_at IS NULL` 的记录（起止、原因、创建人）。
4. 能证明是旧封禁流程造成且当前无维护必要的，列入“可恢复候选”；无法证明的一律按手工维护保留。
5. 迁移后**禁止**用批量 SQL 直接把 `maintenance` 改成 `available`；必须逐条 `PUT /api/admin/seats/{id}` 或 `release` 的 `restore_seat=true` 显式恢复。
6. 释放旧封禁默认 `restore_seat=false`；确认可用后再单独设置状态并写审计。
7. 单独核对 `disabled` 座位，确保升级后仍为 `disabled`。
8. 记录核对人、日期与每个座位的处置结论，完成后附在验收记录。
9. 抽查至少 5 个座位（含 1 个 disabled、1 个手工维护、1 个旧封禁）验证状态与审计。

升级后自检命令：

```bat
python run.py --db var/web.db doctor
```

`doctor` 会检查迁移版本（应为 2）、表结构与可写性。

---

## 9. 实现偏差与待确认项

| 项 | 计划写法 | 实际实现 | 原因 |
|---|---|---|---|
| 仓储方法数量 | `expire_seat_blocks`、`list_seat_blocks`、`get_seat_blocks`、`release_seat_block` | 另有 `get_seat_block(block_id)`、`_seat_block`（行映射） | 按记录解封需要区分“记录不存在（404）”与“已释放（400 `ST-12`）”；否则只能靠时间戳猜测 |
| `release_seat_block` 参数 | `(block_id, now, restore_seat=False)` | `(block_id, now)`；恢复决策在 `engine.release_seat_block` | 保持“业务规则只在 domain”的分层约束（`resource_rules` 提供纯判断） |
| 停用影响预览统计 | 计划写 `items[:20]` | 通过 `Page.total` 取精确计数，再取前 20 条 | 只读预览要准确，避免 `page_size` 截断导致计数偏小 |
| Q-08（关闭自习室是否取消预约） | 未确认 | 预览只读，不取消任何预约 | 不擅自决定业务规则 |
| Q-16（disabled 叠加封禁到期状态） | 未确认 | 保持 `disabled`（保守方案 A） | 不改业务规则，等负责人确认 |

---

## 10. 演示脚本（验收用）

1. 用 `A9001`（自习室管理员）登录 → 「资源」页，确认座位状态列区分「维护 · 封禁」/「维护 · 手工」。
2. 「批量新增」→ 自习室选 A101，前缀 `A101-N`，起始 1、数量 5 → 提示“已新增 5 个座位”，列表出现 `A101-N001…N005`。
3. 「现场」页选一个空闲座位 → 「封禁座位」，开始时间设为**明天** 09:00、结束 11:00 → 提交。座位当前状态仍是「空闲」，但封禁记录显示「未开始」；切到明天 09:00 时段，该座位显示「维护中」（时间窗派生）。
4. 「资源」→「封禁记录」→ 找到该记录 → 「按记录解封」，不勾选恢复 → 座位状态不变；再封禁一个座位（覆盖当前时刻），勾选「同时恢复座位」→ 座位恢复「启用」。
5. 演示手工维护不被误恢复：把某座位状态改为「维护中」（资源页编辑）→ 对该座位封禁再解封 → 仍是「维护中」。
6. 演示停用影响：「资源」→ 某自习室「停用影响」→ 看到待签到/使用中/暂离数量与明细，关闭弹层后数据未改变。
