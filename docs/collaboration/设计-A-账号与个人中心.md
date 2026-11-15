# A 模块：账号与个人中心

## 范围与交付状态

基于 `integration/v1.2` 的 `6cb1c5c`，开发分支为 `feature/account-center`（沿用已创建的分支）。
实现账号创建、启用/停用、重置密码、会话查询/注销、个人资料维护，以及学生“我的候补”页面。
复用现有 PBKDF2 密码处理、登录认证、Cookie/CSRF、审计和数据库事务；不新增表或迁移。

本提交遵守 v1.2 的所有权：只在指定锚点增加 engine/repository 方法，修改账号视图与个人菜单，
在样式文件末尾增加本模块样式。正式应用入口、仓储协议和前端共享注册表由 B 应用
[`SFCR-A-001-registration.patch`](SFCR-A-001-registration.patch)。
**未应用该补丁时，常规启动不会暴露 A 的新路由，新账号按钮和候补入口也尚未接通。**
补丁是集成请求，不应把未注册状态解释为功能已经上线。

## 分层职责

- `api/routes/account_routes.py`：8 条 HTTP 路由、局部 Pydantic 模型，认证/CSRF 沿用既有依赖。
- `domain/engine.py`：在 `set_user_role` 后、资源章节前增加 6 个账号用例。
- `storage/repository.py`：在 `students_of_class` 后、资源章节前增加会话查询和删除。
- `web/app.js`：账号列表与分页、创建/启停/重置面板、登录设备、资料编辑、候补列表与确认/取消。
- `tests/test_account_engine.py`、`tests/test_account_api.py`：模块测试；公共夹具不变。
- `tests/account_frontend.test.cjs`：使用 Node 内置测试器验证视图转义、表单请求和候补操作，不依赖 npm 包。

重置密码复用 `security.hash_password`，在现有引擎事务里完成写密码、删会话、写审计。
不在引擎写事务里调用另开事务的 `AuthService.reset_password`，避免 SQLite 嵌套写锁。

## 接口

所有路径均以 `/api` 开头；成功使用 `{ok:true,data,message}`，失败沿用 `{ok:false,error}`。
POST/PUT 需要现有会话 Cookie 及 `X-CSRF-Token`。创建返回 201，其余成功返回 200。

| 方法与路径 | 权限 | 请求与结果 |
|---|---|---|
| `POST /admin/users` | 系统管理员 | `{id,name,role,department?,class_name?,password?}` → `{user,initial_password?}` |
| `PUT /admin/users/{id}/status` | 系统管理员 | `{status:"active"或"disabled",reason}` → `{user}` |
| `POST /admin/users/{id}/reset-password` | 系统管理员 | `{}` → `{initial_password}` |
| `GET /admin/users/{id}/sessions` | 系统管理员 | `{items,current}` |
| `POST /admin/users/{id}/sessions/revoke` | 系统管理员 | `{}` → `{revoked}` |
| `GET /me/sessions` | 任意登录用户 | 仅本人，`{items,current}` |
| `POST /me/sessions/revoke-others` | 任意登录用户 | `{}` → `{revoked}`，保留当前设备 |
| `PUT /me/profile` | 任意登录用户 | `{name?,department?}` → `{user}` |

会话条目仅包含 `created_at,last_seen_at,expires_at,ip,user_agent,current`。
`current` 是布尔值，不是会话标识；条目不返回 token、摘要或 CSRF。
不显示超过绝对有效期或配置的空闲有效期的会话。`expires_at` 表示最晚绝对失效时间，
空闲超时仍可能提前失效，沿用 `Settings.session_idle_minutes`。

## 规则与边界

1. 重复账号 → 409 `CONFLICT`；非法字段/角色/状态 → 422 `VALIDATION_ERROR`。
   账号为 1～64 字符，不含空白或斜杠；姓名必填，姓名/院系/班级最长 100 字符。
2. 初始密码不填则生成 16 字符随机值；指定密码为 8～128 字符。
   新建与重置均设置 `must_change_password=true`，沿用既有登录后的改密流程。
3. 自动生成的初始密码只在创建/重置响应中返回一次，不入用户列表、审计或浏览器持久存储。
   UI 提醒操作者保存并私下交付。密码响应继承 `Cache-Control: no-store`。
4. 禁止停用自己、禁止停用最后一名启用中的系统管理员 → 400 `RULE_VIOLATION/PR-14`。
   复用现有 `count_sys_admins`，停用的管理员不计入；原有降级保护保持不变。
5. 停用立即删除所有会话，已有预约、违规、信用记录保留。重新启用不恢复旧会话，需重新登录。
   不替 Q-07 决定是否取消已有预约。
6. 重置密码、写入强制改密标记、删除会话、写审计在同一事务完成。
7. 本人只能查询本人设备、退出其他设备；管理端全部设备操作仅系统管理员可用。
   “保留当前会话”的摘要来自认证依赖，不接受用户传入；管理员可注销自己的全部设备并重新登录。
8. 个人资料只允许姓名/院系；角色、班级、信用、ID 等额外字段拒绝；空请求和显式 null 拒绝。
9. 写事务使用现有 `BEGIN IMMEDIATE`，重复账号并发只有一个成功。异常回滚业务与审计记录。

审计动作：`user_create`、`user_status_change`、`user_reset_password`、`session_revoke`、`profile_update`。
记录操作者、目标、必要的前后状态和来源 IP，不记录凭据。

## 候补页面与 C 的接口契约

学生导航增加“我的候补”，展示座位、日期/时段、排队序号、状态和确认截止时间，20 条分页。
只在 `notified` 且截止时间未到时显示可用的确认按钮；只在 `queued/notified` 时提供取消。
所有文本用 `esc()` 转义；修改请求沿用全局 CSRF。

- 列表：`GET /api/waitlist?page=1&page_size=20`，现有实现按学生身份过滤。
- 确认：`POST /api/waitlist/{id}/confirm`，已有接口，成功刷新页面/消息角标。
- 取消：`POST /api/waitlist/{id}/cancel`，由 C 实现。A 只提供按钮、二次确认与请求，
  不修改 C 的状态规则、不自行顺延队列。C 接口未合入前，取消不能完成，错误会展示给用户。
- 学生导航是唯一新增导航；教师、房间管理员、系统管理员不增加学生候补入口。

## 集成方式与人工验收

B 在 `chore/shared-integration` 基于最新集成分支应用 SFCR；A 提交前已在隔离副本验证补丁。
在仓库根目录执行（先合入 A 的业务代码，再应用补丁；不应直接 push 集成分支）：

```powershell
git apply --check --ignore-space-change docs/collaboration/SFCR-A-001-registration.patch
git apply --ignore-space-change docs/collaboration/SFCR-A-001-registration.patch
```

正式启动、测试均在 `智慧自习室预约管理系统/seat_reservation` 下：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/account_frontend.test.cjs
.\.venv\Scripts\python.exe run.py --db var/account-demo.db serve --port 8010 --demo
```

使用新演示库验收，避免重置已有业务库。演示管理员为 `Y0001`，演示密码沿用项目 README。

1. 管理员 → 账号 → 新建学生：检查初始密码仅显示一次、列表状态为启用。
2. 新账号登录 → 提示改密 → 改密后重新登录。
3. 两个独立浏览器会话登录学生 → 查看登录设备 → 退出其他设备，当前设备仍可用。
4. 管理员停用学生 → 学生旧会话失效、登录被拒；启用后可重新登录。
5. 重置密码 → 旧密码和会话失效；新密码登录提示改密。
6. 尝试停用自己或移除最后管理员 → 明确拒绝；其他管理员可正常停用。
7. 编辑姓名/院系 → 顶部用户信息更新；越权资料字段由 API 拒绝。
8. 学生候补列表 → 排队/待确认/已确认/已过期/已取消状态；确认截止控制与分页。
9. C 的取消接口合入后，再联合验收“取消候补”。
10. 宽屏与 390px 手机宽度下检查账号操作、个人菜单、候补页面和弹窗。

## 测试记录

自动化结果和浏览器验收见同目录 `SFCR-A-001-注册请求.md` 的提交前验证记录。
模块 API 测试在自己的夹具中挂载 router，不修改 B 的 `api/app.py`。
原有端到端浏览器测试仍需按项目说明显式启用。
