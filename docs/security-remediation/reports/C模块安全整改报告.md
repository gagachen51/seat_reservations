# C 模块安全整改报告

## 1. 基本信息

| 字段 | 填写内容 |
|---|---|
| 模块 | C（预约与候补） |
| 负责人 | 成员 C（待负责人签字确认） |
| 协作人 | B（资源状态/M-04 helper）、A（账号状态）、共享层（认证/审计） |
| 分支 | `integration/v1.2` |
| 整改前基线 commit | `124db92`（B 合入后） |
| 整改集成分支 commit | `ae962e7`（C 整改代码 `dae53e2`） |
| 最终全量基线 commit | `be8f2de6a0ea0205e7a4e29455370cd05655a0d1` |
| 报告日期 | 2026-11-26 |

> 路径约定：`SR = 智慧自习室预约管理系统/seat_reservation`。

## 2. 问题完成清单

| 编号 | 原始严重程度 | 整改优先级 | 状态 | 说明 |
|---|---|---|---|---|
| M-04 | Medium | P1 | 已完成 | sweep 先识别资源不可用预约，调用 B 契约 helper 统一 `cancelled` + `end_reason="resource_unavailable"`；不 no_show、不扣信用；重复扫描不重复处理 |
| M-06 | Medium | P1 | 已完成 | 普通签到、扫码签到、ACS 凭证签到共用账号状态/`_booking_limit` 复核；停用/黑名单/低信用/连续违约账号拒绝签到与续约，返回 403 + 规则编号 |
| M-07 | Medium | P2 | 已完成 | 候补加入/确认前重新校验房间开放、座位状态、封禁时间窗、开放时间、本人冲突与额度；失败置 expired 并返回明确 4xx |
| L-07 | Low | P3 | 已完成 | 候补确认占用失败显式删除已插入 reservation；凭证签发改为版本条件更新，并发请求复用同一有效凭证或明确冲突 |

## 3. 修改文件清单

| 文件 | 修改内容 | 是否共享文件 | SFCR 编号/状态 |
|---|---|---|---|
| `SR/src/seat_reservation/domain/engine.py` | `_resource_unavailable_error`、`_cancel_resource_unavailable` 调用、sweep 资源不可用分类；`_check_in_core` 账号复核；`extend_reservation` 账号复核；`join_waitlist`/`confirm_waitlist` 资源/时间/额度；`issue_credential` 并发复用；`confirm_waitlist` 失败清理 | 共享引擎，仅 C 白名单方法 | 不需要（C 白名单） |
| `SR/src/seat_reservation/domain/reservation_rules.py` | `waitlist_confirm_state_error`、`waitlist_target_error`、`checkin_account_error`、`extension_account_error` | 否（C 独占） | 不适用 |
| `SR/src/seat_reservation/storage/repository.py` | `delete_reservation`（失败清理）、`update_credential`（版本条件更新）、`list_active_reservations`（与 B 共享查询） | 共享仓储，仅预约/候补方法 | 不需要（C 白名单） |
| `SR/tests/test_security_remediation_c.py` | C-SEC-001～025，共 25 条 | 否（C 测试） | 不适用 |

C 未修改：`api/app.py`、`api/deps.py`、`service/auth.py`、`common_routes.py`、`migrations.py`、`conftest.py`、`helpers.py`、`app.js` 顶层注册表、B 的资源方法、A 的账号方法、D 的分析/导出方法。

## 4. 接口与设计变更说明

### 4.1 M-04 资源不可用契约（C 侧）

- B 提供 `release_affected_reservations(room_id, seat_id, reason, actor, now)`；C 的 sweep 复用同一语义，不复制资源停用入口。
- 受影响预约：`cancelled` + `end_reason="resource_unavailable"`；不记 `no_show`，不扣信用，写站内通知与审计。
- sweep 新增摘要键 `resource_unavailable`；先处理资源不可用，再处理正常 `no_show`，避免关闭房间/停用座位后的误判。
- 正常未签到（资源可用）仍按原规则判 `no_show`，不改变 BR-04/BR-06。

### 4.2 M-06 账号状态复核

| 入口 | 变更前 | 变更后 |
|---|---|---|
| 普通签到 `_check_in_core` | 只校验凭证/窗口 | 先校验账号 active 与 `_booking_limit`；停用/限制账号 403 |
| ACS `check_in_by_credential` | 凭证即身份，可绕过账号状态 | 复用 `_check_in_core` 的账号复核；停用账号凭证签到 403 |
| 续约 `extend_reservation` | 只看预约状态/时段 | 增加账号状态与 `_booking_limit` 复核；限制账号 403 |

错误规则沿用：停用 `PR-02`；黑名单 `BR-20`；信用限制 `BR-07`；连续未签到 `BR-15`。

### 4.3 M-07 候补校验

- `join_waitlist`：房间 open、座位 available、无封禁重叠、开放时间内、本人无冲突、当日额度不超限。
- `confirm_waitlist`：重新读取最新房间/座位/封禁/开放时间；失败时先置 `expired`，再返回 `Result.failure`，不生成 reservation/slot。
- 正常候补队列排序、通知人数、5 分钟确认窗口不变。

### 4.4 L-07 事务/并发

| 场景 | 变更前 | 变更后 |
|---|---|---|
| 候补确认 `insert_reservation` 成功、`occupy` 失败 | 可能留下无 slot 的孤儿 reservation | 显式 `delete_reservation` 清理，再置候补 expired；无孤儿行 |
| 两请求同时签发凭证 | 后写覆盖，先返回 code 可能失效 | `update_credential` 按 `version + status='pending'` 条件更新；并发返回同一有效 code 或 409 |

未新增表字段/迁移；使用现有 `version` 与唯一约束。

### 4.5 API 错误与审计

- 资源不可用/签到拒绝：403 或 409，带 `rule`；候补失效返回 `Result.failure`，HTTP 409。
- 凭证并发冲突：409 `CONFLICT`，`rule=BR-13`。
- 审计：资源取消 `reservation_resource_unavailable`；预约/签到/续约/候补使用既有审计入口；`audit_event` 签名未修改。

## 5. 25 条用例逐条结果对照表

执行命令：

```bat
cd D:\seat_clean_baseline\智慧自习室预约管理系统\seat_reservation
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest tests/test_security_remediation_c.py -v -p no:cacheprovider
```

结果：**25 passed, 1 warning**。

| 编号 | 测试名称 | 结果 | 实际测试函数 | 证据 |
|---|---|---|---|---|
| C-SEC-001 | 续约规则单元 | PASS | `test_c_sec_001_extension_rule_unit` | PASSED |
| C-SEC-002 | 候补规则单元 | PASS | `test_c_sec_002_waitlist_rule_unit` | PASSED |
| C-SEC-003 | 签到账号状态单元 | PASS | `test_c_sec_003_checkin_account_state_unit` | PASSED |
| C-SEC-004 | 凭证签发并发 | PASS | `test_c_sec_004_credential_issue_concurrent` | PASSED |
| C-SEC-005 | sweep 分类 | PASS | `test_c_sec_005_sweep_classification` | PASSED |
| C-SEC-006 | 预约创建冲突 API | PASS | `test_c_sec_006_reservation_create_conflict_api` | PASSED |
| C-SEC-007 | 签到生命周期 API | PASS | `test_c_sec_007_checkin_lifecycle_api` | PASSED |
| C-SEC-008 | 续约 API | PASS | `test_c_sec_008_extend_api` | PASSED |
| C-SEC-009 | 候补 API | PASS | `test_c_sec_009_waitlist_api` | PASSED |
| C-SEC-010 | ACS 签到 API | PASS | `test_c_sec_010_acs_checkin_api` | PASSED |
| C-SEC-011 | 关闭房间不 no_show | PASS | `test_c_sec_011_closed_room_no_no_show` | PASSED |
| C-SEC-012 | 停用座位与在用预约 | PASS | `test_c_sec_012_seat_disabled_active_reservations` | PASSED |
| C-SEC-013 | 限制账号拒绝 | PASS | `test_c_sec_013_restricted_account_rejected` | PASSED |
| C-SEC-014 | 候补资源/开放时间 | PASS | `test_c_sec_014_waitlist_resource_and_hours` | PASSED |
| C-SEC-015 | 正常 no_show 仍生效 | PASS | `test_c_sec_015_normal_no_show_still_works` | PASSED |
| C-SEC-016 | 预约归属 | PASS | `test_c_sec_016_reservation_ownership` | PASSED |
| C-SEC-017 | 管理员/教师不能执行学生操作 | PASS | `test_c_sec_017_admin_teacher_cannot_student_ops` | PASSED |
| C-SEC-018 | 预约 CSRF | PASS | `test_c_sec_018_reservation_csrf` | PASSED |
| C-SEC-019 | ACS token 与凭证安全 | PASS | `test_c_sec_019_acs_token_and_credential_security` | PASSED |
| C-SEC-020 | 非法时段/配额 | PASS | `test_c_sec_020_illegal_time_and_quota` | PASSED |
| C-SEC-021 | 并发预约唯一性 | PASS | `test_c_sec_021_concurrent_reservation_uniqueness` | PASSED |
| C-SEC-022 | 事务回滚与凭证并发 | PASS | `test_c_sec_022_transaction_rollback_and_credential_concurrency` | PASSED |
| C-SEC-023 | C+B 资源联动 | PASS | `test_c_sec_023_c_b_resource_linkage` | PASSED |
| C-SEC-024 | C+A 账号集成 | PASS | `test_c_sec_024_c_a_account_integration` | PASSED |
| C-SEC-025 | C+D 分析导出 | PASS | `test_c_sec_025_c_d_analytics_export` | PASSED |

### 5.1 基线回归对比

| 项目 | 改前 | C 合入后 | 结论 |
|---|---:|---:|---|
| 完整 pytest | 406 passed, 1 skipped | **431 passed, 1 skipped** | +25，无回归 |
| C 安全用例 | 0 / 25 | **25 / 25** | 全部执行并通过 |
| B 模块 B-SEC | 25 / 25 | 25 / 25 | 未被 C 覆盖或回退 |

### 5.2 最终全量回归

```bat
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

最终结果：**470 passed, 1 skipped**（含 A/B/C/D 各 25 条与 IT-01～IT-14）；跳过项为未安装 Playwright 的 `tests/test_e2e.py`。

## 6. 跨模块协作记录

| 协作对象 | 契约/事项 | 状态 | 证据 |
|---|---|---|---|
| B（M-04） | 复用 `release_affected_reservations`；统一 `cancelled` + `resource_unavailable`；不重复实现资源入口 | 已完成 | C-SEC-011/012/023、IT-04/IT-05 |
| B（L-07） | `reservation_slots` 唯一约束 + v4 父预约一致性触发器；C 事务使用唯一约束兜底 | 已完成 | C-SEC-021/022、IT-13 |
| A（M-06） | 账号停用后普通/ACS 签到、续约拒绝；A-SEC-025/IT-01 联调通过 | 已完成 | C-SEC-013、A-SEC-025、IT-01 |
| D（分析导出） | 取消/释放/签到状态进入分析；导出字段与 C 事实一致 | 已完成 | C-SEC-025、IT-07/IT-08/IT-10 |
| 共享层 | `audit_event` 签名不变；CSRF/认证门禁复用 | 已完成 | C-SEC-018/019 |

## 7. 遗留问题与风险

| 问题 | 影响 | 临时措施 | 计划 |
|---|---|---|---|
| 候补取消在 Q-05 前不顺延下一位 | 取消后队首可能等待更久 | v1.2 只置 `cancelled`，不自动通知；由管理员/后续版本确认 | 产品确认后扩展 |
| 续约仅允许 `in_use/away` | `pending` 续约被拒绝 | 任务书明确 Q-03 前拒绝 | 产品确认后评估 |
| 凭证并发返回“同一有效 code 或 409” | 调用方需处理 409 | 代码/测试已明确；TTL 内复用 | 保持 |
| 候补确认占用失败清理为显式 delete | 仅用于同事务失败路径 | 测试覆盖无孤儿 reservation/slot | 保持 |
| E2E 未执行 | Playwright 未安装 | pytest/Node 全量通过 | 环境具备后补跑 |
| 无真实浏览器视觉验收 | 续约/候补弹窗视觉未截图 | 仅代码走查 + HTTP 行为测试 | 验收人补充 |

## 8. 分支与集成

- [x] 基于 B 合并后的 `124db92` 集成 C 整改。
- [x] 仅摘取 C 代码/测试提交 `115cce9`（本地 `dae53e2`），排除 C 整改报告 Markdown 提交。
- [x] `engine.py` 冲突按白名单方法解决：保留 C 的 `_resource_unavailable_error`，复用 B 的 `release_affected_reservations`，无重复 helper。
- [x] 未修改 `app.py`/`deps.py`/`auth.py`/`common_routes.py`/`migrations.py`/`conftest.py`/`helpers.py` 或 `app.js` 顶层注册表。
- [x] 未修改 B 资源方法、A 账号方法、D 分析/导出方法。
- [x] C 合并 commit：`ae962e7`；最终集成分支 HEAD：`be8f2de`。

## 9. 负责人签字

| 角色 | 姓名 | 结论 | 日期 |
|---|---|---|---|
| 模块负责人 |  |  |  |
| 共享层复核 |  |  |  |
| 项目负责人 |  |  |  |

## 10. 附件

- 测试日志：C-SEC 25/25；C 合入后全量 431 passed；最终全量 470 passed, 1 skipped。
- 代码提交：C 整改 `115cce9`（本地摘取 `dae53e2`），集成合并 `ae962e7`，最终基线 `be8f2de`。
- SFCR：不适用；C 白名单内修改，M-04 helper 由 B 提供。
- 迁移说明：C 未新增迁移；依赖 B 的 v4 `resource_constraints`。
