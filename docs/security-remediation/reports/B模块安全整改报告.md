# B 模块安全整改报告

> 文档阶段：v1.2–v2.0.3 过程记录（2026-11）。文中的版本号、测试数量、交付形态与启动方式均为当时基线；当前实现为 v3.1.0 / schema v5，采用浏览器访问的 Web 交付，口径见根目录 `README.md` 与 `CHANGELOG.md`。


## 1. 基本信息

| 字段 | 填写内容 |
|---|---|
| 模块 | B（资源管理与座位治理） |
| 负责人 | 成员 B（待负责人签字确认） |
| 协作人 | C（预约状态/扫描）、共享层（审计/通知/迁移执行） |
| 分支 | `integration/v1.2` |
| 整改前基线 commit | `ef8721a` |
| 整改集成分支 commit | `124db92`（B 功能/测试提交 `f416555`） |
| 最终全量基线 commit | `be8f2de6a0ea0205e7a4e29455370cd05655a0d1` |
| 报告日期 | 2026-11-26 |

> 路径约定：`SR = 智慧自习室预约管理系统/seat_reservation`。

## 2. 问题完成清单

| 编号 | 原始严重程度 | 整改优先级 | 状态 | 说明 |
|---|---|---|---|---|
| M-04 | Medium | P1 | 已完成 | 房间关闭/座位停用/维护时调用 `release_affected_reservations`；受影响预约统一 `cancelled`、`end_reason="resource_unavailable"`；不记 `no_show`、不扣信用，写通知与审计 |
| M-05 | Medium | P1 | 已完成 | 同一预约的 `manual_inspection` 有效违规判重；重复登记返回 `Result.failure("DUPLICATE_VIOLATION")` 并写拒绝审计，不重复扣分 |
| L-07 | Low | P3 | 已完成 | 新增 v4 `resource_constraints` 迁移：slot 与父预约一致性触发器、waitlist slot 范围 CHECK、complaints 可空 FK；旧库升级测试通过 |
| L-08 | Low | P2 | 已完成 | `seat_views` 按房间关闭/座位状态派生不可用；`holder_reservation_id` 仅管理员可见；学生不再获得他人预约 ID |

## 3. 修改文件清单

| 文件 | 修改内容 | 是否共享文件 | SFCR 编号/状态 |
|---|---|---|---|
| `SR/src/seat_reservation/domain/engine.py` | `release_affected_reservations` / `_cancel_resource_unavailable`；`update_room`/`update_seat` 的 `cascade_release`；`manual_violation` 判重；`list_seats` holder 权限 | 共享引擎，仅 B 白名单方法 | 不需要（B 白名单） |
| `SR/src/seat_reservation/domain/resource_rules.py` | 资源不可用取消策略、派生座位状态、holder 权限、批量建座/重复编号、现场违约判重 | 否（B 独占） | 不适用 |
| `SR/src/seat_reservation/storage/repository.py` | `seat_views` 按房间/封禁/占用派生状态并裁剪 holder；`list_violations_of_reservation` | 共享仓储，仅 B 座位/封禁段 | 不需要（B 白名单） |
| `SR/src/seat_reservation/storage/migrations.py` | 新增 `Migration(4, "resource_constraints", _V4)`；`LATEST_MIGRATION_VERSION=4` | 共享迁移文件，仅 v4 资源约束 | 不需要（B 白名单，v3 保留） |
| `SR/src/seat_reservation/api/routes/admin_routes.py` | `update_room` / `update_seat` 传 `cascade_release` | 共享路由，仅 B 资源函数段 | 不需要（B 白名单） |
| `SR/src/seat_reservation/web/app.js` | 资源页/现场页停用与受影响预约提示、停用图例 | 共享前端，仅 B 资源/现场代码块 | 不需要（未改顶层注册表） |
| `SR/tests/test_resource_lifecycle.py` | B-SEC-001～025 与旧库 v2→v4 升级用例 | 否（B 测试） | 不适用 |

B 未修改：`api/app.py`、`api/deps.py`、`service/auth.py`、`common_routes.py`、`migrations.py` v3 审计段、`conftest.py`、`helpers.py`、`app.js` 顶层 `ACTIONS/NAV/VIEWS/VIEW_META`。

## 4. 接口与设计变更说明

### 4.1 M-04 跨模块 helper 签名

```python
DomainEngine.release_affected_reservations(
    room_id: str | None = None,
    seat_id: str | None = None,
    reason: str = "",
    actor: Actor | None = None,
    now: datetime | None = None,
) -> list[str]
```

- 调用方：B 的 `update_room(status="closed")`、`update_seat(admin_status in {"disabled","maintenance"})`；C 的 sweep 资源不可用分支。
- 状态契约：`pending/in_use/away` → `cancelled`，`end_reason="resource_unavailable"`。
- 副作用：站内通知（`resource_unavailable:{reservation_id}` 去重）、`reservation_resource_unavailable` 审计；不记 `no_show`、不扣信用。
- 幂等：第二次调用已无占用中预约，返回空列表。

### 4.2 接口行为变更

| 类型 | 接口/函数 | 变更前 | 变更后 | 兼容性 |
|---|---|---|---|---|
| 资源变更 | `PUT /api/admin/rooms/{id}`（`status=closed`） | 只改房间状态 | 同事务内取消受影响预约，返回 `released_reservations` | 响应新增字段，旧字段不变 |
| 资源变更 | `PUT /api/admin/seats/{id}`（`disabled`/`maintenance`） | 只改座位状态 | 同事务内取消受影响预约，返回 `released_reservations` | 响应新增字段，旧字段不变 |
| 现场违约 | `POST /api/admin/manual-violation` | 可重复登记、重复扣分 | 重复有效 `manual_inspection` 返回 409 `DUPLICATE_VIOLATION`，不重复扣分 | 首次登记行为不变 |
| 座位视图 | `GET /api/seats` | 关闭房间可能显示 `available`；所有角色可见 holder ID | 关闭房间派生 `disabled`；学生 holder 为 `null`，管理员可见 | 状态枚举不变；字段最小化 |
| 数据库 | `schema_version` | v2 | v4（`resource_constraints`） | v1/v2 保留；v3 预留给共享审计 |

### 4.3 L-07 v4 迁移内容与兼容性

`_V4` 包含：

1. `trg_slots_parent_insert` / `trg_slots_parent_update`：`reservation_slots.seat_id/user_id` 必须与父 `reservations` 一致；
2. `waitlist` 重建：`start_slot 0..47`、`end_slot 1..48`、`end_slot > start_slot`；迁移前清理越界/倒置数据；
3. `complaints` 重建：`seat_id`/`reservation_id` 改为可空 FK，孤儿引用置空；
4. 座位编号：`seats.id` 已是全局主键，重复编号插入即拒绝；“房间内序号唯一”因与既有座位跨房间移动冲突，本期不新增，见遗留风险。

迁移在一个 `BEGIN IMMEDIATE` 事务中执行，重复调用幂等；`test_v2_migration_is_idempotent_and_keeps_existing_rows` 与 `test_b_sec_022b_existing_v2_database_upgrades_to_v4` 覆盖旧库升级。回滚需同时恢复升级前数据库备份。

### 4.4 审计

- `room_close_release` / `seat_unavailable_release`：`audit_event`，`result=success|noop`，包含受影响预约 ID；
- `reservation_resource_unavailable`：单条预约取消审计；
- `manual_violation_duplicate`：重复登记拒绝审计；
- 未修改 `audit_event` 签名，敏感字段由公共底座递归脱敏。

## 5. 25 条用例逐条结果对照表

执行命令：

```bat
cd D:\seat_clean_baseline\智慧自习室预约管理系统\seat_reservation
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest tests/test_resource_lifecycle.py -q -p no:cacheprovider
```

B 模块文件结果：**49 passed, 1 warning**（其中 B-SEC-001～025 全部包含并通过）。

| 编号 | 测试名称 | 结果 | 实际测试函数 | 证据 |
|---|---|---|---|---|
| B-SEC-001 | 封禁状态机单元 | PASS | `test_b_sec_001_block_state_machine_unit` | PASSED |
| B-SEC-002 | 批量建座规则单元 | PASS | `test_b_sec_002_bulk_rules_unit` | PASSED |
| B-SEC-003 | 现场违约判重单元 | PASS | `test_b_sec_003_manual_violation_dedupe_unit` | PASSED |
| B-SEC-004 | 资源释放策略单元 | PASS | `test_b_sec_004_resource_release_policy_unit` | PASSED |
| B-SEC-005 | 座位视图状态单元 | PASS | `test_b_sec_005_seat_views_state_unit` | PASSED |
| B-SEC-006 | 关闭房间 API 与影响预览 | PASS | `test_b_sec_006_close_room_api_and_impact` | PASSED |
| B-SEC-007 | 停用座位 API | PASS | `test_b_sec_007_disable_seat_api` | PASSED |
| B-SEC-008 | 封禁生命周期 API | PASS | `test_b_sec_008_block_lifecycle_api` | PASSED |
| B-SEC-009 | 批量建座边界 API | PASS | `test_b_sec_009_bulk_api_limits` | PASSED |
| B-SEC-010 | 重复现场违约 API | PASS | `test_b_sec_010_duplicate_manual_violation_api` | PASSED |
| B-SEC-011 | 关闭房间释放三种状态 | PASS | `test_b_sec_011_close_room_releases_three_statuses` | PASSED |
| B-SEC-012 | 座位不可用释放与封禁差异 | PASS | `test_b_sec_012_seat_unavailable_releases_and_differs_from_block` | PASSED |
| B-SEC-013 | 重复关闭幂等 | PASS | `test_b_sec_013_repeat_close_is_idempotent` | PASSED |
| B-SEC-014 | 影响预览无副作用 | PASS | `test_b_sec_014_impact_preview_has_no_side_effect` | PASSED |
| B-SEC-015 | 恢复矩阵 | PASS | `test_b_sec_015_restore_matrix` | PASSED |
| B-SEC-016 | 资源权限矩阵 | PASS | `test_b_sec_016_resource_permission_matrix` | PASSED |
| B-SEC-017 | 资源 mutation CSRF | PASS | `test_b_sec_017_resource_mutations_require_csrf` | PASSED |
| B-SEC-018 | 关闭房间与 holder 最小化 | PASS | `test_b_sec_018_closed_room_and_holder_minimization` | PASSED |
| B-SEC-019 | 跨房间批量编号拒绝 | PASS | `test_b_sec_019_bulk_cross_room_id_rejected` | PASSED |
| B-SEC-020 | 非法封禁窗口 | PASS | `test_b_sec_020_invalid_block_window` | PASSED |
| B-SEC-021 | 批量建座回滚 | PASS | `test_b_sec_021_bulk_rollback` | PASSED |
| B-SEC-022 | 数据库约束与迁移 | PASS | `test_b_sec_022_db_constraints_and_migration` | PASSED |
| B-SEC-022b | v2 旧库升级 v4 | PASS | `test_b_sec_022b_existing_v2_database_upgrades_to_v4` | PASSED |
| B-SEC-023 | 关闭房间不误判 no_show | PASS | `test_b_sec_023_close_room_does_not_become_no_show` | PASSED |
| B-SEC-024 | 停用管理员不能改资源 | PASS | `test_b_sec_024_disabled_admin_cannot_mutate_resources` | PASSED |
| B-SEC-025 | 资源变更后分析与导出一致 | PASS | `test_b_sec_025_analytics_and_export_after_resource_change` | PASSED |

> 编号统计：B-SEC-001～025 共 25 条；B-SEC-022b 是旧库升级补充用例，不额外占用编号。

### 5.1 基线回归对比

| 项目 | 改前 | B 合入后 | 结论 |
|---|---:|---:|---|
| 完整 pytest | 380 passed, 1 skipped | **406 passed, 1 skipped** | +26，无回归 |
| B 安全用例 | 0 / 25 | **25 / 25** | 全部执行并通过 |
| B 测试文件 | 23 passed | **49 passed** | 新增 26 条（含 25 条安全用例与旧库升级补充） |

### 5.2 最终全量回归

```bat
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

最终结果：**470 passed, 1 skipped**（含 A/B/C/D 各 25 条与 IT-01～IT-14）；跳过项为未安装 Playwright 的 `tests/test_e2e.py`。

## 6. 跨模块协作记录

| 协作对象 | 契约/事项 | 状态 | 证据 |
|---|---|---|---|
| C（M-04） | B 提供 `release_affected_reservations`；C sweep 复用；统一 `cancelled` + `resource_unavailable`，不 no_show/不扣信用 | 已完成 | `test_b_sec_011`、`test_b_sec_023`、IT-04/IT-05 |
| C（L-07） | v4 slot 父预约一致性触发器；C 的预约/候补事务使用唯一约束兜底 | 已完成 | `test_b_sec_022`、IT-13 |
| 共享层（审计） | `audit_event` 签名不变；资源释放成功/拒绝均留痕 | 已完成 | `test_b_sec_006/007/010/025` |
| D（分析导出） | 资源取消后分析计数/导出状态一致，不误判 no_show | 已完成 | `test_b_sec_025`、IT-10 |
| A（账号） | 停用账号不能管理资源；A 未修改 B 资源方法 | 已确认 | `test_b_sec_024` |

## 7. 遗留问题与风险

| 问题 | 影响 | 临时措施 | 计划 |
|---|---|---|---|
| `seats` 房间内序号唯一未新增 | 管理员仍可用不同全局 ID 建语义重复座位 | `seats.id` 全局主键阻止完全重复；前端批量建座有编号预览 | 需产品确认“座位序号”规则后再设计兼容迁移 |
| v4 迁移会重建 `waitlist` / `complaints` | 升级期间需短暂停写 | 迁移在 `BEGIN IMMEDIATE` 内执行；升级前备份数据库 | 生产升级按技术文档停服务、备份、迁移、doctor |
| 关闭房间仍不主动取消已有预约之外的业务决策 | 目前 M-04 取消 `pending/in_use/away`；`completed/cancelled/no_show/released` 保持 | 契约明确，审计记录受影响列表 | 如产品要求保留已开始使用预约，需另行评审 |
| E2E 未执行 | Playwright 未安装 | pytest/Node 全量通过 | 环境具备后补跑 |
| 无真实浏览器视觉验收 | 资源页视觉未做截图验收 | 仅代码走查 + Node/HTTP 行为测试 | 验收人按手工步骤补充 |

## 8. 分支与集成

- [x] 基于 `ef8721a`（D/公共底座完成）创建 B-only 集成。
- [x] B 的 7 个代码/测试提交按顺序摘取；4 个整改报告 Markdown 提交未进入仓库。
- [x] 未修改 `app.py`/`deps.py`/`auth.py`/`common_routes.py`/`conftest.py`/`helpers.py` 或 `app.js` 顶层注册表。
- [x] 未修改 C 的预约/候补/签到方法；`engine.py` 仅 B 白名单资源方法及共享 M-04 helper。
- [x] 迁移仅使用预留 `RESOURCE_MIGRATION_VERSION=4`，未占用 v3。
- [x] B 合并 commit：`124db92`；最终集成分支 HEAD：`be8f2de`。

## 9. 负责人签字

| 角色 | 姓名 | 结论 | 日期 |
|---|---|---|---|
| 模块负责人 |  |  |  |
| 共享层复核 |  |  |  |
| 项目负责人 |  |  |  |

## 10. 附件

- 测试日志：B 文件 49 passed；B-SEC 25/25；B 合入后全量 406 passed；最终全量 470 passed, 1 skipped。
- 代码提交：`f416555`（B 测试/整改），集成合并 `124db92`，最终基线 `be8f2de`。
- SFCR：不适用；B 白名单内修改，审计/通知复用公共底座。
- 迁移说明：v4 `resource_constraints`，旧库 v2→v4 升级用例通过；无 v3 占用。
