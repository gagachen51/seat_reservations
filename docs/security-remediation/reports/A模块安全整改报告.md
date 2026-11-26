# A 模块安全整改报告

## 1. 基本信息

| 字段 | 填写内容 |
|---|---|
| 模块 | A（账号管理与个人中心） |
| 负责人 | 成员 A（待负责人签字确认） |
| 协作人 | 共享认证层（H-02 / M-03 公共底座）、C 模块（M-06 账号状态一致性） |
| 分支 | `integration/v1.2` |
| 基线 commit | `32e0131e1d15f2220843d5d643a7bc61e1ab90f6` |
| 整改 commit | `be8f2de6a0ea0205e7a4e29455370cd05655a0d1` |
| 报告日期 | 2026-11-26 |

> 路径约定：`SR = 智慧自习室预约管理系统/seat_reservation`。

## 2. 问题完成清单

| 编号 | 原始严重程度 | 整改优先级 | 状态 | 说明 |
|---|---|---|---|---|
| H-02 | High | P0 | 已完成（A 侧回归） | 公共底座已在 `api/app.py` 注册 `account_routes`，8 个账号接口在真实 `create_app()` 的 OpenAPI 中全部存在且方法正确；A 未重复修改 `app.py`，仅做真实路由/接口回归 |
| M-03 | Medium | P1 | 已完成（A 侧回归） | 公共底座 `deps.py` 已实现强制改密门禁：`must_change_password=True` 访问业务接口统一 403 `PASSWORD_CHANGE_REQUIRED`，仅放行 `/api/auth/me`、`/api/auth/change-password`、`/api/auth/logout`；A 未修改 `deps.py`/`auth.py` |
| L-01 | Low | P2 | 已完成 | `room_admin` 查询 `/api/admin/users` 默认且仅允许学生账号；查询 `sys_admin`/`room_admin`/`teacher` 返回 403 `PR-18`；返回字段裁剪为管理学生所需的最小集合；`sys_admin` 保留全量查询；`/admin/students` 兼容接口保留 |
| L-03 | Low | P3 | 已完成 | 移除 `state.lastLoginPassword` 长期明文状态；登录成功/失败、登出、进入登录页时清空密码输入框与账号上下文；改密表单不再预填旧密码；不写入 URL/localStorage/sessionStorage |

## 3. 修改文件清单

| 文件 | 修改内容 | 是否共享文件 | SFCR 编号/状态 |
|---|---|---|---|
| `SR/src/seat_reservation/api/routes/admin_routes.py` | 仅修改 `list_users` 路由函数：`room_admin` 结果字段最小化，新增 `Page` 导入 | 共享路由容器，仅 A 账号路由段 | 不需要（A 白名单段） |
| `SR/src/seat_reservation/domain/engine.py` | 仅修改 `list_users`：`room_admin` 只允许学生范围，非学生 role 查询抛 403 `PR-18`；`sys_admin` 保持全量 | 共享引擎，仅 A 方法 | 不需要（A 白名单方法） |
| `SR/src/seat_reservation/web/app.js` | A 登录/登出/改密/账号相关代码块：删除 `lastLoginPassword`，新增 `clearLoginSecrets` / `resetClientState`，改密表单不预填旧密码 | 前端共享文件，仅 A 登录与账号代码块 | 不需要（未改顶层 `ACTIONS/NAV/VIEWS/VIEW_META`） |
| `SR/tests/test_account_security.py` | 新增 A-SEC-001～025，共 25 条安全整改测试 | 否（A 测试） | 不适用 |
| `SR/tests/account_frontend.test.cjs` | 扩充 Node 测试 harness，新增登录成功/失败、登出/showLogin、改密表单密码清理用例 | 否（A 测试） | 不适用 |

只读复核、无代码改动：

- `SR/src/seat_reservation/api/routes/account_routes.py`：8 个接口的鉴权、Pydantic 模型、错误码与敏感字段过滤已核对，无需要修改的行为。
- `SR/src/seat_reservation/storage/repository.py`：`list_users` 已支持 `role`/`keyword`/分页；L-01 的范围控制在引擎层完成，未修改仓储。
- `api/app.py`、`api/deps.py`、`service/auth.py`、`common_routes.py`、`migrations.py`、`conftest.py`、`helpers.py`、`app.js` 顶层注册表：**未修改**。

## 4. 接口与设计变更说明

| 类型 | 接口/函数/表 | 变更前 | 变更后 | 兼容性/确认人 |
|---|---|---|---|---|
| API 权限范围 | `GET /api/admin/users`（room_admin） | 可查询所有角色，返回完整 `User.as_dict()` | 默认且仅允许学生账号；`role=sys_admin/room_admin/teacher` 返回 403 `PR-18` | 符合 L-01 最小权限；`/admin/students` 继续可用 |
| API 返回字段 | `GET /api/admin/users`（room_admin） | `id/name/role/role_label/department/class_name/credit/status/limit_type/limit_label/limit_until/consecutive_no_show/must_change_password` | 仅 `id/name/role/role_label/department/class_name/status` | `sys_admin` 返回结构不变；现有 room_admin 账号页未使用被裁字段 |
| API 权限范围 | `GET /api/admin/users`（sys_admin） | 全角色查询 | 不变 | 兼容 |
| 错误码 | `GET /api/admin/users?role=...`（room_admin 非学生） | 200 返回结果 | 403 `FORBIDDEN`，`rule=PR-18` | 新增明确拒绝语义 |
| 前端状态 | `app.js` `state.lastLoginPassword` | 登录后保存明文密码，改密时预填 | 字段删除；密码只存在于登录请求局部变量；请求结束清空 DOM | 改密需用户重新输入原密码；不再预填 |
| 前端状态 | `showLogin()` | 只清 `user/meta/csrf/sheet` | 同时清 `view/page/selectedSeat/seatCache/roomsAdmin/seatsAdmin/seatBlocks/lastSnapshot` 与密码输入 | 避免切换账号残留上一账号导航/数据 |
| 数据库迁移 | — | — | 无 schema/迁移变更 | 不适用 |
| 审计事件/签名 | `user_create` / `user_status_change` / `user_reset_password` / `session_revoke` / `profile_update` | 既有 | 不变；回归确认审计不含密码/hash/token | 兼容 |

H-02、M-03 只做回归验证，**A 无接口路径/HTTP 方法变更**；公共底座已实现的 `/api/admin/users` 注册与强制改密门禁未在本模块重复修改。

## 5. 25 条用例逐条结果对照表

执行命令：

```bat
cd D:\seat_clean_baseline\智慧自习室预约管理系统\seat_reservation
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest tests/test_account_security.py -v -p no:cacheprovider
```

执行结果：**25 passed, 1 warning in 9.68s**（Python 3.12.4 / pytest 8.3.4）。

| 编号 | 测试名称 | 结果 | 实际测试函数 | 证据 |
|---|---|---|---|---|
| A-SEC-001 | 账号创建参数校验 | PASS | `test_a_sec_001_create_validation_engine` | PASSED |
| A-SEC-002 | 最后管理员与自停用保护 | PASS | `test_a_sec_002_last_admin_and_self_disable` | PASSED |
| A-SEC-003 | 重置密码生成一次性密码并撤销会话 | PASS | `test_a_sec_003_reset_password_is_one_time_and_revokes_sessions` | PASSED |
| A-SEC-004 | 强制改密依赖白名单 | PASS | `test_a_sec_004_force_password_gate_unit` | PASSED |
| A-SEC-005 | 个人资料模型只允许姓名/院系 | PASS | `test_a_sec_005_profile_model_rejects_extra_fields` | PASSED |
| A-SEC-006 | sysadmin 创建账号成功 | PASS | `test_a_sec_006_sysadmin_create_account_api` | PASSED |
| A-SEC-007 | room_admin/student 创建账号被拒绝 | PASS | `test_a_sec_007_non_sysadmin_cannot_create` | PASSED |
| A-SEC-008 | 停用/启用账号与自停用 | PASS | `test_a_sec_008_disable_enable_and_self_disable` | PASSED |
| A-SEC-009 | 重置密码接口 | PASS | `test_a_sec_009_reset_password_api` | PASSED |
| A-SEC-010 | 会话列表与退出其他设备 | PASS | `test_a_sec_010_session_list_and_revoke_others` | PASSED |
| A-SEC-011 | 最后 active sysadmin 不能降级/停用 | PASS | `test_a_sec_011_last_sysadmin_api_guard` | PASSED |
| A-SEC-012 | 角色变更对现有会话立即生效 | PASS | `test_a_sec_012_role_change_is_immediate` | PASSED |
| A-SEC-013 | 个人资料不能越权修改 | PASS | `test_a_sec_013_profile_cannot_escalate` | PASSED |
| A-SEC-014 | 并发创建同 ID 账号 | PASS | `test_a_sec_014_concurrent_create_same_id` | PASSED |
| A-SEC-015 | 强制改密完整流程 | PASS | `test_a_sec_015_force_password_full_flow` | PASSED |
| A-SEC-016 | room_admin 账号列表范围与字段最小化 | PASS | `test_a_sec_016_room_admin_scope_and_fields` | PASSED |
| A-SEC-017 | student/teacher 账号管理接口拒绝 | PASS | `test_a_sec_017_student_teacher_account_admin_denied` | PASSED |
| A-SEC-018 | 账号接口 CSRF 保护 | PASS | `test_a_sec_018_account_mutations_require_csrf` | PASSED |
| A-SEC-019 | 停用/重置不泄露敏感信息 | PASS | `test_a_sec_019_responses_and_audit_have_no_credentials` | PASSED |
| A-SEC-020 | 非法账号输入边界 | PASS | `test_a_sec_020_invalid_account_input_boundaries` | PASSED |
| A-SEC-021 | 不存在用户/非法状态 | PASS | `test_a_sec_021_missing_user_and_invalid_status` | PASSED |
| A-SEC-022 | 前端登录/登出密码清理 | PASS | `test_a_sec_022_frontend_password_cleanup_static` | PASSED；另有 Node 行为测试 |
| A-SEC-023 | 真实应用 OpenAPI 包含 8 个账号接口 | PASS | `test_a_sec_023_real_openapi_has_eight_account_routes` | PASSED |
| A-SEC-024 | A 与共享认证集成（CSRF/强制改密） | PASS | `test_a_sec_024_force_password_and_csrf_integration` | PASSED |
| A-SEC-025 | A 与 C 账号状态集成（停用 + ACS） | PASS | `test_a_sec_025_disabled_account_blocks_acs_and_session` | PASSED（C M-06 已合入，未使用 xfail） |

### 5.1 A 模块专项回归

```bat
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest tests/test_account_engine.py tests/test_account_api.py tests/test_api.py -q -p no:cacheprovider
```

结果：**78 passed, 1 warning in 18.71s**。

### 5.2 前端 Node 测试

```bat
cd D:\seat_clean_baseline\智慧自习室预约管理系统\seat_reservation
node --test tests/account_frontend.test.cjs
```

结果：**15 tests / 15 pass / 0 fail**。覆盖账号页、创建/停用/重置、会话、资料，以及新增的登录成功/失败、登出/showLogin、改密表单密码清理。

### 5.3 完整回归

```bat
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

结果：**470 passed, 1 skipped, 1 warning in 81.59s**（最终加入 IT-01～IT-14 后的全量结果；A 合入当时为 456 passed）。

跳过项：`tests/test_e2e.py:30`，未安装 playwright；不属于 A 模块失败。

### 5.4 基线回归测试对比

| 项目 | 改前 | 改后 | 结论 |
|---|---:|---:|---|
| 完整 pytest | 431 passed, 1 skipped（`32e0131`） | **470 passed, 1 skipped** | 通过；+25 A +14 IT，无回归 |
| 前端 Node 测试 | 11 pass | **15 pass** | 通过；新增 4 条 A 密码清理用例 |
| 新增 A 用例 | 0 / 25 | **25 / 25** | 全部执行并通过 |

## 6. 跨模块协作记录

| 协作对象 | 契约/事项 | 状态 | 证据 |
|---|---|---|---|
| 共享认证层（H-02） | `api/app.py` 已注册 `account_routes`；A 只验证真实 OpenAPI 8 路由，不修改 `app.py` | 已完成 | A-SEC-023 PASSED |
| 共享认证层（M-03） | `deps.py` 强制改密门禁：403 `PASSWORD_CHANGE_REQUIRED`，白名单 3 个接口；A 不修改 `deps.py`/`auth.py` | 已完成 | A-SEC-004、A-SEC-015、A-SEC-024 PASSED |
| C 模块（M-06） | 账号停用后普通会话 401，ACS 凭证签到被拒；A 不修改 C 的签到/预约逻辑 | 已完成 | A-SEC-025 PASSED |
| B 模块（资源治理） | A 未修改 `engine.py` B 资源方法、`repository.py`、`admin_routes.py` 资源段 | 已确认 | `git diff` 仅 A 的 `list_users` 方法/路由段 |
| D 模块（分析导出） | A 未修改 D 分析/导出方法、`analytics_queries.py`、`export_queries.py` | 已确认 | 全量 470 passed 无回归 |

## 7. 遗留问题与风险

| 问题 | 影响 | 临时措施 | 计划 |
|---|---|---|---|
| `room_admin` 的 `/admin/users` 字段被裁剪 | 若未来给 room_admin 增加账号页并使用 `credit`/`must_change_password` 将会取不到 | 当前 room_admin 账号页不存在；`/admin/students` 保留学生治理字段 | 若未来开放 room_admin 账号页，单独评审字段最小化范围 |
| 改密不再预填旧密码 | 首次登录用户需手动输入初始密码 | 初始密码仍只在创建/重置响应展示一次；用户需按提示保存 | 如产品要求，可改为一次性临时内存变量，但不得长期保留 |
| 密码在登录请求的局部变量中存在 | 请求期间 JavaScript 运行时必然持有密码 | 请求结束清空 DOM；不写 URL/localStorage/sessionStorage；不新增第三方脚本 | 保持现状 |
| 浏览器 E2E 未执行 | 1 个 e2e 用例因未安装 playwright 跳过 | Node DOM 行为测试 + pytest 全量回归 | 环境具备 playwright 后补跑 |
| 无真实浏览器视觉验收 | 未验证 390px/桌面窗口视觉 | 现有 Node 测试覆盖行为，非视觉 | 由验收人按手工步骤补充 |

## 8. 分支与集成

- [x] 基于最新 `integration/v1.2`（`32e0131`，含 B/C/D 与公共底座）。
- [x] 未直接修改 `api/app.py`、`api/deps.py`、`service/auth.py`、`common_routes.py`、`migrations.py`、`conftest.py`、`helpers.py` 或 `app.js` 顶层注册表。
- [x] 未修改 B/C/D 方法段；`engine.py` 仅 `list_users`，`admin_routes.py` 仅 `list_users` 路由。
- [x] 无安全整改 Markdown 进入仓库；报告与统计表保存在 `D:\seat_local_docs_backup`。
- [x] 已提交：`be8f2de6a0ea0205e7a4e29455370cd05655a0d1`。
- [x] 已推送 `origin/integration/v1.2`；本地与远程一致。

## 9. 负责人签字

| 角色 | 姓名 | 结论 | 日期 |
|---|---|---|---|
| 模块负责人 |  |  |  |
| 共享层复核 |  |  |  |
| 项目负责人 |  |  |  |

## 10. 附件

- 测试日志：A 专项 `25 passed`；A 回归 `78 passed`；Node `15 pass`；全量 `470 passed, 1 skipped`。
- 代码提交：`be8f2de6a0ea0205e7a4e29455370cd05655a0d1`（A 合入时基线 `32e0131`；最终 HEAD 同 commit）。
- SFCR：不适用；H-02/M-03 公共底座已完成，A 未修改共享认证文件。
- 迁移说明：无 A 模块 schema/迁移变更。
