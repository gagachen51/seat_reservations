# C 模块安全整改报告

> 对应《03-C模块预约与候补整改任务书》与《08-C模块安全整改测试用例》。
> 代码目录：`智慧自习室预约管理系统/seat_reservation/`（下文简称 `SR`）。
> 分支：`feature/reservation-module`。
> 基线：`293 passed, 1 skipped`（本次整改前实测；文档 08 的 380 条为另一基线口径）。

## 1. 基本信息

| 项目 | 内容 |
|---|---|
| 模块 | C（预约、签到/签退、暂离/返座、续约、候补、超时扫描） |
| 分支 | `feature/reservation-module` |
| 修复问题 | M-04 / M-06 / M-07 / L-07 |
| B 的资源-预约契约 | `release_affected_reservations`；`cancelled`；`end_reason=resource_unavailable` |
| 测试命令 | `cd SR && .venv/bin/python -m pytest -q` |
| 测试结果 | **318 passed, 1 skipped**（新增 25 条 C-SEC 用例全部通过） |
| 未通过项 | 无 |
| 遗留风险 | 见第 9 节 |
| 需要负责人确认 | 见第 10 节 |

## 2. 修复摘要

| 编号 | 原问题 | 修复位置 | 结果 |
|---|---|---|---|
| M-04 | 房间关闭/座位停用后预约仍 pending，sweep 判 no_show、扣信用 | `engine.py` 资源判定/取消 helper、`run_timeout_sweep`、`release_affected_reservations`、`repository.list_active_reservations` | 资源不可用统一 `cancelled` + `end_reason="resource_unavailable"`；不记 no_show、不扣信用、通知/审计、重复扫描幂等 |
| M-06 | 停用账号 ACS 仍可签到；限制账号仍可续约 | `_check_in_core`、`extend_reservation`、`reservation_rules` 账号复核函数 | 普通/扫码/ACS 共用账号校验；停用/黑名单/低信用/连续违约签到与续约均拒绝（403） |
| M-07 | 候补加入/确认未校验房间/座位/封禁/开放时间；失败可能遗留状态 | `join_waitlist`、`confirm_waitlist`、`reservation_rules` 候补纯规则 | 加入/确认均校验最新资源、封禁、开放时间、本人冲突与配额；确认失败置 `expired` 且不生成 reservation/slot |
| L-07 | 候补确认 `insert_reservation` 后 `occupy` 失败可能留下孤儿预约；凭证并发后写覆盖先写 | `confirm_waitlist` 清理逻辑、`repository.delete_reservation`、`issue_credential`、`repository.update_credential` | 强制占用失败显式清理预约与 slot；凭证按版本号条件更新，未过期凭证幂等复用，不返回已失效 code |

## 3. 修改文件清单（仅白名单内源码与 C 自己的测试）

| 文件 | 修改内容 |
|---|---|
| `SR/src/seat_reservation/domain/reservation_rules.py` | 新增 `waitlist_confirm_state_error`、`waitlist_target_error`、`checkin_account_error`、`extension_account_error` 与 `WAITLIST_CONFIRMABLE_STATUSES` |
| `SR/src/seat_reservation/domain/engine.py` | 新增 `_resource_unavailable_error`、`_cancel_resource_unavailable`、`release_affected_reservations`、`_credential_payload`；修改 `issue_credential`、`_check_in_core`、`extend_reservation`、`join_waitlist`、`confirm_waitlist`、`run_timeout_sweep` |
| `SR/src/seat_reservation/storage/repository.py` | 新增 `delete_reservation`、`update_credential`、`list_active_reservations` |
| `SR/tests/test_security_remediation_c.py` | 新增 C-SEC-001～C-SEC-025 共 25 条测试 |
| `docs/security-remediation/reports/C模块安全整改报告.md` | 本报告 |

**未修改**：`api/app.py`、`api/deps.py`、`service/auth.py`、`api/routes/common_routes.py`、`config.py`、`seed.py`、`logging_setup.py`、`migrations.py`、`domain/models.py`、`web/app.js` 顶层 `ACTIONS/NAV/VIEWS/VIEW_META`、`admin_routes.py` 账号/资源/分析/导出函数段、`tests/conftest.py`、`tests/helpers.py`、其他模块测试。
**审计签名**：任务书中的 `self.audit_event(...)` 在本基线对应 `self._audit(...)` 统一封装；本次未修改其签名、未新增不兼容参数，所有新增审计都通过该入口写入。

## 4. 逐项修复说明

### 4.1 M-04 资源状态变更后的预约处理

1. **B 的入口、C 的状态判定**：C 不修改 `update_room` / `update_seat`，不在 C 侧复制资源停用入口；新增只读判定 `_resource_unavailable_error`，读取最新 `room.status`、`seat.admin_status`、`seat.maintenance_by_block`、未释放封禁与预约时段的重叠关系。
2. **统一 helper**：新增 `release_affected_reservations(room_id, seat_id, reason, actor, now)`，B 在资源状态变更后直接调用；受影响预约统一：
   - `status = "cancelled"`
   - `end_reason = "resource_unavailable"`
   - 释放 `reservation_slots`
   - 通知“因资源不可用取消……不记违约、不扣信用分”（`dedupe_key=resource_unavailable:{reservation_id}`）
   - 写审计 `reservation_resource_unavailable`
3. **sweep 防误判**：`run_timeout_sweep` 在 `no_show` / `away_timeout` / `late_checkout` 判定**之前**扫描全部 `pending/in_use/away` 预约，先取消资源不可用的预约；`no_show` 循环内再做一次防御性复核。
4. **维护态区分**：手工 `maintenance` 视为长期不可用；`maintenance_by_block=True` 的封禁维护只在封禁窗口与预约时段重叠时取消，避免误伤封禁结束后的未来预约。
5. **重复扫描幂等**：已被取消/释放的预约不再进入 `list_active_reservations` 的结果；重复 sweep、重复 helper 调用不重复写通知/审计、不重复扣分。

### 4.2 M-06 停用账号 ACS 签到与限制账号续约

1. `_check_in_core` 在状态检查之后立即加载用户并统一复核：
   - `user.is_active`：停用 → `PR-02`
   - `_booking_limit(user, now)`：黑名单 `BR-20`、信用 `BR-07`、连续违约 `BR-15`
   - 统一抛 `Forbidden`（HTTP 403），错误携带明确规则编号。
2. **ACS 共用校验**：`check_in_by_credential` 只负责用凭证定位预约，随后仍走 `_check_in_core`；凭证未过期不能绕过账号状态。
3. `extend_reservation` 在状态/时长校验之后、资源/边界校验之前复核账号限制；受限账号返回 403，不产生任何时段写入。
4. 普通账号签到/续约行为不变；`_booking_limit` 的信用/黑名单阈值未修改。

### 4.3 M-07 候补加入/确认校验

1. 新增纯规则 `waitlist_target_error(...)`，统一校验：房间 `open`、座位 `available`、无重叠封禁、`within_open_hours`、目标时段未开始；返回 `(规则编号, 提示)`。
2. `join_waitlist` 新增：
   - 房间/座位存在与状态、封禁、开放时间、目标时段已开始校验；
   - 本人重叠预约（409 `BR-11`）；
   - 当日个数/时长配额（400 `BR-02`）；
   - 非法时段对齐由 `ValidationFailed`（422）返回，避免 500。
3. `confirm_waitlist`：
   - 状态用 `waitlist_confirm_state_error`（只有 `notified` 可确认）；
   - 重新读取最新房间/座位/封禁/开放时间后再创建预约；
   - 资源/开放时间/占位失败等路径先 `_expire_waitlist(..., offer_next=False)`，再返回 `Result.failure`，由外层写事务提交 `expired`；用户限制/本人冲突/配额沿用原有顺延口径；
   - 失败路径不生成 reservation/slot。
4. 正常候补队列排序、通知人数参数、确认成功生成 `pending` 预约的行为不变。

### 4.4 L-07 候补确认与凭证签发事务/并发

1. `confirm_waitlist` 在 `insert_reservation` + `occupy` 的 `SlotConflict` 分支调用 `repo.delete_reservation(reservation.id)`：
   - 先删 `reservation_slots`，再删 `reservations`；
   - 将候补置 `expired`（不再顺延）；
   - 返回 `Result.failure`，外层事务提交，保证“候补状态”和“无孤儿预约/slot”一致。
2. `issue_credential`：
   - 未使用且 TTL 内的凭证直接复用，返回同一 code；
   - 新签发使用 `repository.update_credential(... WHERE id=? AND version=? AND status='pending')` 条件更新；
   - 条件更新失败时读取最新行：仍 pending 且已有未使用凭证则返回最新 code，否则 409 `Conflict`；
   - 不返回已失效 code，不改变凭证 TTL/签到规则。
3. 新增 `repository.list_active_reservations` 供资源 helper 与 sweep 共用。

## 5. 接口、状态与错误码

| 场景 | HTTP/结果 | 状态/副作用 |
|---|---|---|
| 停用账号普通签到 | 403 `FORBIDDEN`，`rule=PR-02` | 预约状态不变 |
| 停用账号 ACS 签到 | 403 `FORBIDDEN`，`rule=PR-02` | 预约状态不变；凭证不被消费 |
| 黑名单/低信用/连续违约签到 | 403，`BR-20`/`BR-07`/`BR-15` | 预约状态不变 |
| 受限账号续约 | 403，对应规则编号 | 无新增 slot、无审计成功记录 |
| 关闭房间/停用座位后 sweep | 预约 `cancelled` | `end_reason=resource_unavailable`；不记违规、不扣信用；通知+审计 |
| 候补加入资源/时间不合法 | 400 `RULE_VIOLATION`（`FR-21`/`BR-09`/`BR-03`/`BR-08`） | 不生成候补 |
| 候补加入本人冲突 | 409 `CONFLICT`，`BR-11` | 不生成候补 |
| 候补加入超配额 | 400 `RULE_VIOLATION`，`BR-02` | 不生成候补 |
| 候补确认时资源/开放时间已变 | `Result.failure` → 409 | 候补 `expired`；不生成 reservation/slot |
| 候补确认强制占用失败 | `Result.failure` → 409 | 候补 `expired`；预约/slot 全部清理 |
| 并发签发凭证 | 200，返回同一有效 code；或后写者返回最新 code / 409 | 最终 `credential_code` 至少与一个成功响应一致 |

**事务口径**：拒绝类参数/权限错误抛 `AppError`，外层回滚；需要保留业务后果的“拒绝”返回 `Result.failure`，由外层 `BEGIN IMMEDIATE` 写事务提交。审计统一走基线现有的 `self._audit(...)`（对应任务书的 `audit_event` 约定），未改签名。

## 6. 25 条测试用例逐条结果

测试文件：`SR/tests/test_security_remediation_c.py`。全部在临时 SQLite + `helpers.seed_fixture` 夹具上执行，时间注入 `now`，HTTP 用例冻结 `student_routes.datetime`。

| 用例 | 测试函数 | 结果 | 说明 |
|---|---|---|---|
| C-SEC-001 | `test_c_sec_001_extension_rule_unit` | 通过 | 状态/粒度/边界/额度/最大可延长分钟 |
| C-SEC-002 | `test_c_sec_002_waitlist_rule_unit` | 通过 | 取消/确认状态、开放时间、房间/座位/封禁 |
| C-SEC-003 | `test_c_sec_003_checkin_account_state_unit` | 通过 | active/disabled/黑名单/信用/连续违约 |
| C-SEC-004 | `test_c_sec_004_credential_issue_concurrent` | 通过 | 双线程签发，返回 code 与最终存储一致 |
| C-SEC-005 | `test_c_sec_005_sweep_classification` | 通过 | 房间关闭不误判；正常 no_show/away/late 分类 |
| C-SEC-006 | `test_c_sec_006_reservation_create_conflict_api` | 通过 | 合法 201、冲突 409、关闭/停用 400、无重复 slot |
| C-SEC-007 | `test_c_sec_007_checkin_lifecycle_api` | 通过 | 凭证→签到→暂离→返座→签退；重复操作拒绝 |
| C-SEC-008 | `test_c_sec_008_extend_api` | 通过 | 合法 200、非法 422、冲突 409、受限 403、无半截 slot |
| C-SEC-009 | `test_c_sec_009_waitlist_api` | 通过 | 加入 201、重复 409、非 notified 400、确认/取消 |
| C-SEC-010 | `test_c_sec_010_acs_checkin_api` | 通过 | token 校验、正常/过期/停用凭证 |
| C-SEC-011 | `test_c_sec_011_closed_room_no_no_show` | 通过 | 关闭房间→sweep：cancelled/resource_unavailable、信用不降 |
| C-SEC-012 | `test_c_sec_012_seat_disabled_active_reservations` | 通过 | pending/in_use/away 全部按资源不可用取消 |
| C-SEC-013 | `test_c_sec_013_restricted_account_rejected` | 通过 | 停用/黑名单/低信用/连续违约：普通/ACS/续约均拒绝 |
| C-SEC-014 | `test_c_sec_014_waitlist_resource_and_hours` | 通过 | 关闭/停用/封禁/开放时间外；确认失败无 reservation/slot |
| C-SEC-015 | `test_c_sec_015_normal_no_show_still_works` | 通过 | 正常 no_show 仍判违约、扣分、只处理一次 |
| C-SEC-016 | `test_c_sec_016_reservation_ownership` | 通过 | 他人预约读取/签到/取消/续约、他人候补确认/取消均 403 |
| C-SEC-017 | `test_c_sec_017_admin_teacher_cannot_student_ops` | 通过 | T1/A1/Y1 学生操作全部 403 |
| C-SEC-018 | `test_c_sec_018_reservation_csrf` | 通过 | 缺失/错误 token 403 `CSRF_FAILED`；正确成功 |
| C-SEC-019 | `test_c_sec_019_acs_token_and_credential_security` | 通过 | token 错 403、凭证一次性、重复拒绝 |
| C-SEC-020 | `test_c_sec_020_illegal_time_and_quota` | 通过 | 逆序/非 30 分钟/超长/超窗口/配额/候补边界 |
| C-SEC-021 | `test_c_sec_021_concurrent_reservation_uniqueness` | 通过 | 8 线程同座位同 slot 仅 1 成功、1 行预约、2 个 slot |
| C-SEC-022 | `test_c_sec_022_transaction_rollback_and_credential_concurrency` | 通过 | 强制 `occupy` 失败无孤儿 reservation/slot；凭证并发一致 |
| C-SEC-023 | `test_c_sec_023_c_b_resource_linkage` | 通过 | `release_affected_reservations` 房间/座位；重复调用幂等 |
| C-SEC-024 | `test_c_sec_024_c_a_account_integration` | 通过 | 停用后旧 session 401、普通/ACS 签到拒绝、续约拒绝、预约未 in_use |
| C-SEC-025 | `test_c_sec_025_c_d_analytics_export` | 通过（导出一致性） | KPI 与 DB 一致、CSV 行数与日期区间一致、无敏感字段；**导出审计属 D 模块，本次未改导出函数，未在 C 代码中断言** |

## 7. 执行命令与结果

```text
# 文档 08 指定的 C 相关测试集合
$ .venv/bin/python -m pytest tests/test_extension.py tests/test_waitlist_cancel.py tests/test_safety_fixes.py tests/test_concurrency.py -q
65 passed

# 本次新增 25 条 C-SEC 用例
$ .venv/bin/python -m pytest tests/test_security_remediation_c.py -q
25 passed

# 全量回归
$ .venv/bin/python -m pytest -q
318 passed, 1 skipped, 1 warning in 19.03s
```

跳过项：`tests/test_e2e.py:30`（未安装 playwright），与 C 整改无关。

## 8. 与 B / A / 共享层的依赖说明

| 模块 | 依赖 | C 侧处理 |
|---|---|---|
| B 资源与座位治理 | `release_affected_reservations(room_id, seat_id, reason, actor, now)`、`cancelled`、`end_reason="resource_unavailable"` | C 提供同签名 helper 并在 `run_timeout_sweep` 中兜底；**未修改 B 的 `update_room` / `update_seat`**。B 合并后应在资源状态变更入口调用该 helper，C 的 sweep 兜底可继续覆盖漏调用场景 |
| B 封禁生命周期 | `seat.admin_status`、`maintenance_by_block`、`seat_block_overlaps` | 只读复用；区分手工维护与封禁维护，避免误伤封禁结束后的预约 |
| A 账号状态 | `set_user_status` 停用账号并删除会话；`user.status`、`limit_type`、`limit_until`、`credit` | C 在签到/ACS/续约使用点复核，不修改账号写入与会话失效逻辑 |
| D 运营分析/导出 | 现有 analytics/export 查询与 CSV 路由 | C 不改导出函数；C-SEC-025 验证预约事件与 KPI/导出一致性；导出审计字段留给 D |
| 共享层 | 通知、审计 | 通知使用现有 `NotificationType.SEAT_BLOCKED` 并带 dedupe key；审计沿用 `_audit`，未改签名 |

## 9. 遗留风险

1. **B 入口尚未合并调用**：当前基线的 `update_room` / `update_seat` 不会主动调用 `release_affected_reservations`；C 的 `run_timeout_sweep` 会兜底取消，但“关闭房间后立即查询”与“等到下次扫描”之间存在时间窗。B 合并后应在其入口直接调用 helper。
2. **`end_reason` 标签**：为遵守 C 文件白名单，本次未修改 `domain/models.py`；`resource_unavailable` 作为字面量写入，`end_reason_label` 在 B/共享层补充枚举前可能为 `None`。不影响状态/统计/导出值。
3. **候补加入本人冲突/配额**：按任务书“复用预约创建校验”新增，属于行为收紧；若产品希望允许用户先排队后自行调整，需要负责人确认。
4. **导出审计**：`engine.export_rows` 属于 C 严禁修改的函数，C-SEC-025 未覆盖“导出审计存在”项；需 D 模块补测。
5. **维护态判断**：手工 `maintenance` 按长期不可用取消全部有效预约；封禁维护仅在窗口重叠时取消。若 B 调整 `maintenance_by_block` 语义，需同步复核。

## 10. 需要负责人确认

1. 停用账号已存在的 pending 预约：当前只拒绝签到/续约，预约保留至正常 no_show 扫描。是否需要在账号停用时统一取消并豁免信用？
2. 候补加入是否允许“本人已有重叠预约”时排队：当前按任务书复用创建校验，直接 409 `BR-11`。
3. `resource_unavailable` 的枚举/标签是否由 B 在共享层补充（C 未改 `models.py`）。
4. 导出审计由 D 补齐的时间点。

## 11. 完成报告格式（任务书第 6 节）

```text
模块：C
分支/commit：feature/reservation-module / 见本次提交
修复问题：M-04 / M-06 / M-07 / L-07
B 的资源-预约契约：release_affected_reservations；cancelled；end_reason=resource_unavailable
修改文件：
  SR/src/seat_reservation/domain/reservation_rules.py
  SR/src/seat_reservation/domain/engine.py
  SR/src/seat_reservation/storage/repository.py
  SR/tests/test_security_remediation_c.py
  docs/security-remediation/reports/C模块安全整改报告.md
新增/调整测试：tests/test_security_remediation_c.py（C-SEC-001～025，25 条）
测试命令：cd SR && .venv/bin/python -m pytest -q
测试结果：318 passed, 1 skipped（新增 25 passed）
未通过项：无
遗留风险：B 资源入口尚未合并调用 helper；resource_unavailable 枚举/标签待共享层补充；导出审计待 D
需要负责人确认：停用账号已存在预约的处置口径；候补加入本人冲突/配额收紧；导出审计归属
```

## 12. 线下回收自检

- [x] 只修改白名单内源码与 C 自己的测试。
- [x] 未整包覆盖。
- [x] 未提交 `app.py`、`deps.py`、`auth.py`、`common_routes.py`、`migrations.py`、`conftest.py`、`helpers.py`。
- [x] 未改 `web/app.js` 顶层注册表（本次未改前端）。
- [x] 未改 `domain/models.py`（`resource_unavailable` 以字面量使用）。
- [x] 审计调用未改签名。
- [x] 25 条用例均有执行结果。
