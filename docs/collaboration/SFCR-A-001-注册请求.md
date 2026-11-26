# SFCR-A-001：A 模块注册

> 文档口径（v2.0.3，2026-11-26）：当前发布版本 **v2.0.3**；`pytest` **499 passed / 1 skipped**，Node **43 passed**；用户启动方式统一为安装 `setup.exe`。本文历史章节中的旧版本/测试数字仅代表当时基线，最新口径以根目录 `README.md` 与 `CHANGELOG.md` 为准。


| 字段 | 内容 |
|---|---|
| 发起人 | A（账号与个人中心） |
| 处理人 | B（共享文件集成） |
| 状态 | 已准备，待 B 应用/审核 |
| 基础分支 | `integration/v1.2`（基线 `6cb1c5c`） |
| 业务分支 | `feature/account-center` |
| 目标分支 | B 的 `chore/shared-integration`，PR → `integration/v1.2` |
| 精确变更 | [SFCR-A-001-registration.patch](SFCR-A-001-registration.patch) |
| 应用 SHA | 待 B 登记 |
| A 复核 | 补丁可应用性和隔离集成验证已完成；B 应用后需登记 SHA 并复核 |

## 为什么需要共享文件变更

A 代码已经提供全部账号 API 和视图函数，但应用入口和闭包内注册表由 B 独占。
只增加业务文件不会使新接口和导航自动生效，因此请求 B 进行以下最小注册：

1. `api/app.py`：单独 import `account_routes`；在现有 admin router 后 `include_router(..., prefix="/api")`。
2. `domain/ports.py`：在 `students_of_class` 后增加 `list_sessions`、`delete_sessions`、`delete_session` 协议签名。
3. `web/app.js` 的 `VIEW_META/NAV/VIEWS/ACTIONS`：学生候补标题/导航/视图，及六个账号/候补动作。

不修改鉴权中间件、现有路由、资源/预约规则、模型、迁移或公共测试夹具。
补丁锚点均为完整原文，含 before/after diff。A 业务提交没有直接修改这些共享区域。

## 预期结果

- 正常应用出现 8 个新路由；学生可看到“我的候补”；管理员账号页按钮可以工作。
- 所有角色可在个人菜单使用“编辑资料”“登录设备”。
- 缺少注册时 404/按钮无响应；必须应用后再宣称集成完成。
- 候补取消仍依赖 C 的接口，不在本 SFCR 中替 C 实现。

## 验证与风险

关联测试：`tests/test_account_engine.py`、`tests/test_account_api.py`，以及原有完整回归。
模块测试用自己的夹具装配路由；B 应用补丁后，仍须从实际 `create_app()` 验证 OpenAPI 的 8 条新路由。

提交前验证（Python 3.13.12）：

- 模块测试：62 项通过，覆盖账号创建/并发重复、停用保留预约、最后管理员、密码重置、
  会话隔离/过期/注销、角色权限、CSRF、审计与资料字段限制。
- 原工作区完整回归：`177 passed, 1 skipped`（原有 115 + A 新增 62）。
- 隔离副本应用 SFCR 后完整回归：`177 passed, 1 skipped`，14.25 秒。
- `node --test tests/account_frontend.test.cjs`：11 项通过，验证转义、分页、表单/会话/资料请求、
  候补状态与截止时间、失败后重试、二次确认；原工作区和隔离副本均通过。
- 原工作区及注册后 JS 均通过 `node --check`；源码 diff 无空白错误。
  统一 diff 补丁保留标准的空白上下文行，源码检查命令为
  `git diff --cached --check -- . ':(exclude)docs/collaboration/*.patch'`。
- `git apply --check --ignore-space-change` 通过，补丁已实际应用到隔离副本。
- 隔离集成服务的真实 HTTP 检查通过：8 条 OpenAPI 路由已注册；首页及新导航/动作注册代码可访问；
  创建 → 首次改密 → 多设备登录 → 退出其他设备 → 改资料 → 停用/启用 → 重置密码 → 注销全部设备。
- 使用 AST 比较确认引擎和仓储的全部既有方法保持不变；前端四个共享注册表也未直接修改。
- 1 项跳过为原有 Playwright 端到端测试（浏览器依赖未安装）。浏览器工具不可用，
  **未完成真实浏览器点击与手机布局视觉验收**；Node 测试不替代这项验收。
- **候补取消后端仍待 C 交付**，本提交只验证 A 的按钮/确认/请求逻辑，不声称已完成联合业务验收。

风险：其他成员可能同时改注册表，应用前先运行 `git apply --check --ignore-space-change`；
若上下文发生变化，B 按相同函数名进行最小手工注册，不覆盖其他成员新增内容。
回退：仅撤销这份注册补丁，业务代码和数据库结构均不受影响。

按计划的 SFCR 流程由 B 处理、A 复核；本请求未通过工具给其他组员发送消息。
