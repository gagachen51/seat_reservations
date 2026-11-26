# B 模块安全整改报告

> 依据：`02-B模块资源管理整改任务书.md`、`07-B模块安全整改测试用例.md`、`模块安全整改报告模板.md`（模板已复制到 `docs/security-remediation/reports/模块安全整改报告模板.md`）。
> 代码目录：`智慧自习室预约管理系统/seat_reservation/`（下文简称 `SR`）。

## 1. 基本信息

| 字段 | 填写内容 |
|---|---|
| 模块 | **B**（资源与座位治理：自习室/座位、封禁生命周期、批量建座、停用影响预览、现场处置） |
| 负责人 | 待填（B 模块负责人） |
| 协作人 | C（预约状态/扫描契约）、A（账号停用）、D（统计/导出）、共享层（审计） |
| 分支 | 本地工作副本，基于 `integration/v1.2`；整改尚未提交（见第 8 节） |
| 基线 commit | `2589354`（`integration/v1.2`）。任务书写的 `D:\seat_clean_baseline` 在本机不存在，本地以集成分支为基线 |
| 整改 commit | 待提交（工作区改动，文件清单见第 3 节） |
| 报告日期 | 2026-11-26 |

## 2. 问题完成清单

| 编号 | 原始严重程度 | 整改优先级 | 状态 | 说明 |
|---|---|---|---|---|
| M-04（资源侧） | Medium | P1 | 已完成 | `engine.update_room`/`update_seat` 新增 `cascade_release` 开关并在内部调用 `release_affected_reservations`；HTTP 资源 mutation 传 `True`。受影响预约统一 `cancelled` + `end_reason="resource_unavailable"`，不记 `no_show`、不扣信用，写通知与审计；重复操作幂等；影响预览仍只读；封禁语义未变 |
| M-05 | Medium | P1 | 已完成 | `manual_violation` 增加判重（同预约 + `manual_inspection` + `active/appealing`）；第二次返回 409 `DUPLICATE_VIOLATION`，不再扣分，并写 `manual_violation_duplicate`（result=denied）审计 |
| L-07（资源/库） | Low | P3 | 已完成 | 新增迁移 **v4**（`RESOURCE_MIGRATION_VERSION = 4`）：`reservation_slots` 一致性触发器、`waitlist` 时段范围 CHECK、`complaints` 可空外键；“房间内座位序号唯一”未加（见 7.1 与第 4 节说明） |
| L-08 | Low | P2 | 已完成 | `seat_views` 派生状态纳入 `rooms.status`：关闭房间的座位一律不可用；`holder_reservation_id` 仅对 `room_admin`/`sys_admin` 下发；前端现场页图例补「停用」 |

## 3. 修改文件清单

| 文件 | 修改内容 | 是否共享文件 | SFCR 编号/状态 |
|---|---|---|---|
| `SR/src/seat_reservation/domain/resource_rules.py` | 新增 `RESOURCE_UNAVAILABLE_REASON`、`RESOURCE_UNAVAILABLE_TARGETS`、`ACTIVE_VIOLATION_STATUSES`、`should_cancel_for_resource`、`derived_seat_state`、`can_expose_holder`、`is_duplicate_manual_violation` | 否（B 独占） | — |
| `SR/src/seat_reservation/domain/engine.py` | `update_room`/`update_seat` 增加 `cascade_release` 并在内部调用 helper 与 `audit_event`；`manual_violation` 增加判重；`list_seats` 传递 `include_holder` | 是（引擎共享） | 无 SFCR：任务书 §2.1 白名单明确允许 B 修改这 4 个方法 |
| `SR/src/seat_reservation/storage/repository.py` | `seat_views` 增加 `rooms.status` 派生与 `include_holder`；新增 `list_violations_of_reservation` | 是（仓储共享） | 无 SFCR：白名单允许 `seat_views` 与资源/封禁相关方法 |
| `SR/src/seat_reservation/storage/migrations.py` | 新增 `_V4` 与 `Migration(4, "resource_constraints", _V4)`；`LATEST_MIGRATION_VERSION = 4` | 是（迁移共享） | 无 SFCR：白名单限定 B 只用 `RESOURCE_MIGRATION_VERSION=4`，未占用 v3 |
| `SR/src/seat_reservation/api/routes/admin_routes.py` | `update_room`/`update_seat` 传入 `cascade_release` | 是（路由共享） | 无 SFCR：白名单限定“房间、座位、现场违约函数段” |
| `SR/src/seat_reservation/web/app.js` | 资源页保存提示“已取消 N 条受影响预约”；现场页图例补「停用」 | 是（前端共享） | 无 SFCR：仅 B 资源页/现场处置代码块，未动顶层 `ACTIONS/NAV/VIEWS/VIEW_META` |
| `SR/tests/test_resource_lifecycle.py` | 新增 B-SEC-001～025 共 25 条 + 旧库升级用例 1 条；迁移版本断言 2 → 4 | 否（B 自己的测试） | — |
| `docs/security-remediation/reports/B模块安全整改报告.md` | 本报告 | 否 | — |

**未修改**：`api/app.py`、`api/deps.py`、`service/auth.py`、`api/routes/common_routes.py`、`config.py`、`seed.py`、`logging_setup.py`、`domain/models.py`、`domain/ports.py`、`domain/params.py`、`api/schemas.py`、`web/app.js` 顶层注册表、`repository.py` 预约/候补/导出方法、`engine.py` 的 `list_users`/`_check_in_core`/`run_timeout_sweep`/`extend_reservation`/`join_waitlist`/`confirm_waitlist`/`cancel_waitlist`/`issue_credential`/`export_rows`/`_resolve_analytics_dates`、`migrations.py` v3、`tests/conftest.py`、`tests/helpers.py`、其他模块测试。

## 4. 接口与设计变更说明（字段/迁移/签名）

| 类型 | 接口/函数/表 | 变更前 | 变更后 | 兼容性/确认人 |
|---|---|---|---|---|
| API 字段/返回裁剪 | `PUT /admin/rooms/{id}`、`PUT /admin/seats/{id}` 响应 | 房间/座位字段 | 增加 `released_reservations: string[]`（本次取消的预约 ID） | 向后兼容（只增字段）；确认人：待 B 负责人复核 |
| API 字段/返回裁剪 | `GET /api/seats` 的 `holder_reservation_id` | 所有角色均返回 | 仅 `room_admin`/`sys_admin` 返回，学生为 `null` | **行为变更**（L-08 要求）；学生端不再依赖该字段；前端已确认 |
| API 字段/返回裁剪 | `GET /api/seats` 中关闭房间的座位 | `state="available"` | `state="disabled"`、`available=false` | 行为变更（L-08 要求）；复用既有文案「停用」 |
| 数据库迁移 | 新增 `Migration(4, "resource_constraints")`（`RESOURCE_MIGRATION_VERSION=4`） | 最高 v2 | v4：`reservation_slots` 触发器、`waitlist` CHECK（重建表）、`complaints` 外键（重建表） | 旧库兼容：先清理历史脏数据再建约束（见 4 节说明与 B-SEC-022b）；确认人：待共享层复核 |
| 跨模块 helper 签名 | `release_affected_reservations(room_id, seat_id, reason, actor, now)` | 已存在（C 整改基线） | **签名未变**；B 在 `update_room`/`update_seat` 内调用 | 与 C 契约一致：`cancelled` + `end_reason="resource_unavailable"`；C 的 `C-SEC-023` 通过 |
| 审计事件/签名 | `self.audit_event(actor, action, target_type, target_id, *, result, details, before, after, now, ip)` | 已存在 | **签名未改**；B 新增 3 个事件：`room_close_release`、`seat_unavailable_release`、`manual_violation_duplicate` | 共享层审计签名未变；敏感字段仍由底座脱敏 |

必须说明的 5 点：

1. **是否改变 API 路径/方法/请求字段**：未改。仅两个 PUT 的响应增加 `released_reservations` 字段，`GET /api/seats` 的 `holder_reservation_id` 按角色裁剪（L-08）。
2. **是否改变错误码/状态码**：新增 409 `error.code="DUPLICATE_VIOLATION"`（`manual_violation` 重复登记，`rule=BR-18`）；其余沿用既有 400/403/404/409/422 映射。
3. **数据库 schema/迁移**：仅新增 v4，未改 v1/v2，未占用 v3；`RESOURCE_MIGRATION_VERSION=4`、`LATEST_MIGRATION_VERSION=4`；旧库升级路径经 B-SEC-022b 验证（数据保留、越界 `waitlist` 裁剪为 47/48、孤儿投诉外键置 NULL）。
4. **跨模块契约**：`release_affected_reservations(room_id: str | None, seat_id: str | None, reason: str = "", actor: Actor | None = None, now: datetime | None = None) -> list[str]`；受影响状态 `pending/in_use/away` → `cancelled`、`end_reason="resource_unavailable"`，不写违规、不动信用。
5. **字段裁剪**（L-08）：`holder_reservation_id` 旧=所有角色可见 → 新=仅管理员可见；`state` 旧=关闭房间仍 available → 新=disabled。

## 5. 25 条用例逐条结果对照表

| 编号 | 测试名称 | 结果 | 实际命令/环境 | 证据 |
|---|---|---|---|---|
| B-SEC-001 | `test_b_sec_001_block_state_machine_unit` | 通过 | 见 5.1 | `tests/test_resource_lifecycle.py` |
| B-SEC-002 | `test_b_sec_002_bulk_rules_unit` | 通过 | 见 5.1 | 同上 |
| B-SEC-003 | `test_b_sec_003_manual_violation_dedupe_unit` | 通过 | 见 5.1 | 同上 |
| B-SEC-004 | `test_b_sec_004_resource_release_policy_unit` | 通过 | 见 5.1 | 同上 |
| B-SEC-005 | `test_b_sec_005_seat_views_state_unit` | 通过 | 见 5.1 | 同上 |
| B-SEC-006 | `test_b_sec_006_close_room_api_and_impact` | 通过 | 见 5.1 | 同上 |
| B-SEC-007 | `test_b_sec_007_disable_seat_api` | 通过 | 见 5.1 | 同上 |
| B-SEC-008 | `test_b_sec_008_block_lifecycle_api` | 通过 | 见 5.1 | 同上 |
| B-SEC-009 | `test_b_sec_009_bulk_api_limits` | 通过 | 见 5.1 | 同上 |
| B-SEC-010 | `test_b_sec_010_duplicate_manual_violation_api` | 通过 | 见 5.1 | 同上 |
| B-SEC-011 | `test_b_sec_011_close_room_releases_three_statuses` | 通过 | 见 5.1 | 同上 |
| B-SEC-012 | `test_b_sec_012_seat_unavailable_releases_and_differs_from_block` | 通过 | 见 5.1 | 同上 |
| B-SEC-013 | `test_b_sec_013_repeat_close_is_idempotent` | 通过 | 见 5.1 | 同上 |
| B-SEC-014 | `test_b_sec_014_impact_preview_has_no_side_effect` | 通过 | 见 5.1 | 同上 |
| B-SEC-015 | `test_b_sec_015_restore_matrix` | 通过 | 见 5.1 | 同上 |
| B-SEC-016 | `test_b_sec_016_resource_permission_matrix` | 通过 | 见 5.1 | 同上 |
| B-SEC-017 | `test_b_sec_017_resource_mutations_require_csrf` | 通过 | 见 5.1 | 同上 |
| B-SEC-018 | `test_b_sec_018_closed_room_and_holder_minimization` | 通过 | 见 5.1 | 同上 |
| B-SEC-019 | `test_b_sec_019_bulk_cross_room_id_rejected` | 通过 | 见 5.1 | 同上 |
| B-SEC-020 | `test_b_sec_020_invalid_block_window` | 通过 | 见 5.1 | 同上 |
| B-SEC-021 | `test_b_sec_021_bulk_rollback` | 通过 | 见 5.1 | 同上 |
| B-SEC-022 | `test_b_sec_022_db_constraints_and_migration` | 通过 | 见 5.1 | 同上 |
| B-SEC-023 | `test_b_sec_023_close_room_does_not_become_no_show` | 通过 | 见 5.1 | 同上 |
| B-SEC-024 | `test_b_sec_024_disabled_admin_cannot_mutate_resources` | 通过 | 见 5.1 | 同上 |
| B-SEC-025 | `test_b_sec_025_analytics_and_export_after_resource_change` | 通过 | 见 5.1 | 同上 |
| （补充） | `test_b_sec_022b_existing_v2_database_upgrades_to_v4` | 通过 | 见 5.1 | 旧 v2 库升级 v4、脏数据清理、数据保留 |

### 5.1 模块测试命令

```bash
python -m pytest tests/test_resource_lifecycle.py tests/test_safety_fixes.py tests/test_api.py -q
→ 74 passed, 1 warning in 15.40s
```

### 5.2 完整回归

```bash
python -m pytest -q -p no:cacheprovider
→ 396 passed, 1 skipped, 35 errors in 88.97s
```

**关于 35 errors 的如实说明**：全部集中在 `tests/test_security_p0_m02.py` 等依赖 `tmp_path` 的 P0 用例，报 `PermissionError: ... .lock`——当前机器沙箱不允许 pytest 在 basetemp 创建清理锁文件（pytest 内部行为），**与 B 模块代码无关**（B 的用例均不使用 `tmp_path`）。改动前后错误集合与数量完全一致。在无该限制的机器上应为 `396 passed, 1 skipped`。

### 5.3 基线回归测试对比

| 项目 | 改前 | 改后 | 结论 |
|---|---:|---:|---|
| 完整 pytest | 370 passed, 1 skipped, 35 errors（本机实测；文档 07 的 380 为另一基线口径） | 396 passed, 1 skipped, 35 errors | 未低于改前；errors 集合一致；新增 26 条全部通过 |
| 前端 Node 测试 | 未执行（无 Node 前端测试依赖变更，本次仅改 B 视图函数） | 未执行 | 如实记录：未执行，不作为通过 |
| 新增模块用例 | 0 / 25 | 26 / 25（25 条 B-SEC + 1 条旧库升级） | 全部通过 |

## 6. 跨模块协作记录

| 协作对象 | 契约/事项 | 状态 | 证据 |
|---|---|---|---|
| C（预约） | M-04：`release_affected_reservations` → `cancelled` + `end_reason="resource_unavailable"`；不 no_show、不扣信用；C 的 sweep 侧不重复处理 | 一致 | B-SEC-011/012/023 通过；C 的 `test_c_sec_023_c_b_resource_linkage` 通过 |
| C（预约） | 为兼容 C 既有契约（engine 直调 `update_seat` 不级联），B 用 `cascade_release` 默认 `False` 开关，HTTP 入口传 `True` | 已协调（需负责人复核） | `tests/test_security_remediation_c.py` 全绿 |
| A（账号） | M-06：room_admin 被停用后旧 session 不能管理资源 | 已验证 | B-SEC-024 通过（停用后旧 session 401） |
| D（统计/导出） | L-06/L-09：资源状态变更后统计与导出一致 | 已验证（数据侧） | B-SEC-025 通过（analytics 与 CSV 均 200 且包含被取消预约） |
| 共享层 | 审计签名：`audit_event(...)` 未改；新增事件 `room_close_release`/`seat_unavailable_release`/`manual_violation_duplicate` | 一致 | 代码审阅 + 审计断言（B-SEC-006/007/010/013） |
| C（迁移） | L-07：B 的 v4 只处理 slot 一致性/资源约束，未改 C 的 slot 事务代码 | 边界清晰 | B-SEC-022/022b 通过；C 测试全绿 |

## 7. 遗留问题与风险

| 问题 | 影响 | 临时措施 | 计划 |
|---|---|---|---|
| “房间内座位序号唯一”未实现 | 同房间可能出现两个同序号座位（但 `seats.id` 全局唯一仍保证不重复） | 由主键拒绝重复编号 | 需产品确认编号规则（另加 `seat_number` 列）后实现；强行加会与“座位跨自习室移动”冲突 |
| engine 直调资源变更不级联 | 脚本/CLI 直接调 `engine.update_room/update_seat` 时不会自动取消预约 | 文档要求调用方显式调用 helper（C 已如此） | 若要强制级联，需同步修改 C 的 `C-SEC-023` 契约 |
| v4 迁移重建 `waitlist`/`complaints` | 大库上需要额外磁盘与时间 | 迁移前备份 `.db` + `-wal`，在维护窗口执行 | 后续可评估“在线重建 + 双写”方案 |
| 35 个 `tmp_path` 用例在本机报错 | 本机无法给出“全绿”全量记录 | 已记录原因并单列 | 换无沙箱限制的环境复跑（预期 396 passed, 1 skipped） |

## 8. 分支与集成

- [ ] 从最新 `integration/v1.2` 创建/更新分支。**（当前：本地工作副本已基于 `integration/v1.2` `2589354`，尚未创建远程分支）**
- [ ] 共享文件已走 SFCR 并由共享层/B 应用。**（当前：B 为 engine/repository/migrations/admin_routes/app.js 的既定修改人，按任务书 §2.1 白名单直接修改，未开 SFCR；如需补登记请共享层确认）**
- [x] 未直接修改 `app.py`/`auth.py`/`deps.py`/`app.js` 共享注册表等禁止文件。**（已逐文件哈希比对确认）**
- [x] 已解决与集成分支的冲突。**（以 `integration/v1.2` 为基线直接实现，无冲突）**
- [ ] PR 目标为 `integration/v1.2`，未直接推 `main`。**（尚未提交；提交时按此规则）**

## 9. 负责人签字

| 角色 | 姓名 | 结论 | 日期 |
|---|---|---|---|
| 模块负责人 |  | 通过/有条件通过/退回 |  |
| 共享层复核 |  |  |  |
| 项目负责人 |  |  |  |

## 10. 附件

- 测试日志/截图：见 5.1 / 5.2 命令与输出摘要；截图待补（资源页保存提示、现场页「停用」图例）
- SFCR：无（见第 3 节说明；如需补登记，提交时补 SFCR-B-001）
- 迁移说明：`README`？不适用；见第 4 节“数据库迁移”行与 `migrations.py` 内 `_V4` 注释
- 其他：`docs/collaboration/设计-B-资源与座位治理.md`（原设计文档，本次未重写，按要求只在本报告列变更）
