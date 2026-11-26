# 变更日志（CHANGELOG）

> 项目：智慧自习室预约管理系统
> 版本口径以本文件与根目录 [`README.md`](README.md) 为准；历史设计文档中的版本/测试数如与本文冲突，以本文为准。
> 测试口径：Python `pytest` + Node `node --test`，跳过项为未安装 Playwright 的真实浏览器端到端用例（1 条）。

## v2.0.3 — 2026-11-26（最终产品体验与文档归一化）

### 账号与密码

- 管理员“新建账号（留空密码）”与“重置密码”统一使用临时密码 **`12345678`**，并强制 `must_change_password=True`。
- 首次登录/重置后的强制改密执行强密码策略：至少 8 位，且大写字母、小写字母、数字、符号至少包含 3 类。
- 禁止把新密码继续设置为 `12345678`；旧密码与会话在重置时立即失效。
- 首次安装的系统管理员 `Y0001` 仍使用高强度随机密码，写入 `data/initial_admin_credentials.txt`，首次改密成功后删除。
- 命令行 `user create` / `user passwd` 未显式指定密码时同样使用 `12345678` 临时密码。

### 文档与仓库

- 根目录 `README.md` 作为**唯一权威总说明**，合并原项目目录 README 中的交付、启动、账号、测试、部署信息。
- 删除子目录重复 README；需求阶段原型说明由 `prototype/README.md` 更名为 `prototype/原型说明.md`，同步更新所有引用。
- `智慧自习室预约管理系统/初始账号说明.txt` 与根 README 第 3 节账号规则保持一致，全仓库仅保留这一份账号说明副本。
- 新增本 `CHANGELOG.md`，完整记录 v1.2 → v2.0.3 的功能与缺陷修复。
- 统一版本、测试数、启动方式（桌面安装包）口径，移除 `启动-演示.bat` 等过时描述。

### 测试与发布

- 新增 `tests/test_temp_password.py`：临时密码、强密码策略、禁止沿用弱密码、管理员初始密码仍随机。
- `pytest`：**499 passed / 1 skipped**；Node：**43 passed**。
- 重新构建 v2.0.3 `setup.exe`，完成旧实例清理、全新安装与打包版 smoke test。
- 提交 `91b9df053946078274ec6c82a99952f17a8a4462`，合并 `main` 后更新 `v1.2-final` 标签。

## v2.0.2 — 2026-11-26（桌面端 7 项交互/逻辑缺陷修复）

### 预约与选座

- 学生座位列表新增每个自习室真实物理启用座位数 `total_seats`，副标题改为“可用数 / 物理启用数”（如 `7 / 8`，不再错误显示 `7 / 7`）。
- 预约冲突精确区分：`USER_TIME_CONFLICT`（本人同时段已有预约）、`SEAT_OCCUPIED`（正常座位被他人预约）、`SEAT_DISABLED` / `SEAT_UNAVAILABLE`（停用/维护/封禁）、`ROOM_UNAVAILABLE`（自习室停用）。
- 预约校验先判本人时段冲突，再判座位占用；仅 `SEAT_OCCUPIED` 弹“加入候补”，其余仅提示原因并关闭。
- 停用/维护座位在列表中显示灰色斜纹与“维护中/停用”，信息弹窗不再出现候补按钮。

### 演示数据与桌面能力

- 初始化演示数据保留已有活跃系统管理员的密码哈希、强制改密标记与个人资料；仅演示学生/教师/自习室管理员使用统一演示密码。
- 演示初始化成功弹窗明确“当前系统管理员原有密码已保留”。
- 桌面版数据导出接入 pywebview 原生“另存为”对话框，网页版回退 Blob 下载；7 类 CSV 导出均可保存。
- 新建投诉的“座位（可选）”改为实时 `<select>`，每次打开动态拉取最新自习室与座位。
- 运营分析“指标口径”改为纯中文 4×2 卡片网格，公式用浅蓝胶囊展示，不再暴露数据库英文字段名。

### 测试与发布

- 新增 `tests/test_desktop_ux_api.py` 8 条、`tests/desktop_ux.test.cjs` 11 条。
- `pytest`：**493 passed / 1 skipped**；Node：**43 passed**。
- 发布 v2.0.2 `setup.exe`。

## v2.0.1 — 2026-11-26（系统页与演示初始化体验统一）

### 缺陷修复

- 修复管理员「系统」页 `Cannot read properties of undefined (reading 'schema_version')`：
  - 新增 sysadmin 专用 `GET /api/admin/system`，返回版本、环境、运行状态、Schema、库大小、账号数、调度器详情；
  - 前端系统页全部使用可选链与完整兜底（Schema 兜底 `v4`），接口异常时仍渲染导出与运维面板。
- 修复新建账号院系/班级输入体验：标签改为“选填，可直接输入”，补 placeholder，`<datalist>` 合并历史值与常用预设。
- 将演示模式融入桌面版：「系统」页新增“初始化演示数据”，二次确认 + 固定确认短语，仅 sysadmin、非生产环境可用；统一演示密码只在成功弹窗显示一次，成功后会话失效并引导重新登录；删除 `启动-演示.bat`。
- 修复 `seed_demo` 重复初始化与 append-only 审计冲突：保留历史审计并推进审计序列，重复初始化只追加审计。

### 测试与发布

- 新增 `tests/test_system_demo_api.py` 10 条、`tests/system_demo_ui.test.cjs` 13 条；补齐 `tests/account_frontend.test.cjs` 19 条。
- `pytest`：**485 passed / 1 skipped**；Node：**32 passed**。
- 发布 v2.0.1 `setup.exe`。

## v2.0.0 — 2026-11-26（桌面安装包首个发布基线）

- 在 v1.2 安全整改基线上打包 Windows 桌面版：内嵌 WebView2 窗口、用户级安装、开始菜单/桌面快捷方式、卸载保留业务数据。
- 首次启动自动创建系统管理员 Y0001（高强度随机密码 + `data/initial_admin_credentials.txt`）。
- 桌面版与网页版共用同一套 FastAPI + SQLite + 原生前端业务代码。
- 数据目录跟随安装位置，支持 SQLite WAL 与在线备份。

## v1.2 — 2026-11-26（安全整改与结题验收基线）

- A/B/C/D 四模块与共享层安全整改：H-01、H-02、M-01～M-07、L-01～L-10。
- 账号/资源/预约/分析/导出权限矩阵、CSRF 双提交、PBKDF2、CSV 公式注入防护、审计只追加。
- 跨模块集成 IT-01～IT-14 + IT-02B（强制改密死锁热修复）。
- 初始管理员凭据文件：首次初始化写入、改密自动删除、其他账号不误删、幂等。
- `pytest`：475 passed / 1 skipped；Node：19 passed。
- 结题验收报告与测试统计表归档至 `docs/security-remediation/reports/`。
