# 智慧自习室预约管理系统

> 当前版本：**v3.1.0**
> 技术形态：FastAPI + SQLite + 原生 HTML/CSS/JavaScript，浏览器访问
> 质量基线：`pytest` **510 passed / 1 skipped**；Node **39 passed**
> 本文件是仓库的**唯一权威总说明**；设计、验收与历史记录见文末文档索引。

## 1. 项目简介

面向高校自习室的座位预约与治理系统。把座位视为“按时段分配、可预约、可治理、可统计”的资源，围绕一条主业务闭环运转：

选座 → 预约 → 签到 → 使用 → 暂离/返座 → 签退 → 违约处置 → 候补补位 → 运营分析

系统为单体 Web 服务：主机运行一个 FastAPI/Uvicorn 进程，业务数据保存在主机本地的 SQLite 文件中，用户通过浏览器访问。支持本机、局域网和反向代理三种访问方式。

## 2. 交付物

| 交付物 | 位置 |
|---|---|
| 完整源码与测试 | [`智慧自习室预约管理系统/seat_reservation/`](智慧自习室预约管理系统/seat_reservation/) |
| 初始账号说明 | [`智慧自习室预约管理系统/初始账号说明.txt`](智慧自习室预约管理系统/初始账号说明.txt) |
| 版本变更记录 | [`CHANGELOG.md`](CHANGELOG.md) |
| 环境与依赖清单 | [`智慧自习室预约管理系统/环境清单.md`](智慧自习室预约管理系统/环境清单.md) |
| 系统说明 | [`智慧自习室预约管理系统/系统说明.md`](智慧自习室预约管理系统/系统说明.md) |
| 部署与备份文档 | [`seat_reservation/docs/技术文档.md`](智慧自习室预约管理系统/seat_reservation/docs/技术文档.md) |
| 架构设计报告 | [`docs/系统架构设计报告.md`](docs/系统架构设计报告.md) |

## 3. 快速开始

要求 Python 3.11 及以上（开发验证使用 3.13）。在 `智慧自习室预约管理系统/seat_reservation` 目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --db var/delivery.db serve --lan --port 8010
```

- 本机访问：<http://127.0.0.1:8010/>
- 局域网访问：其他设备连接同一网络后，打开 `http://<主机IPv4>:8010/`；主机需放行 8010 端口，完整步骤见 [`局域网使用说明`](智慧自习室预约管理系统/seat_reservation/docs/局域网使用说明.md)
- 外网访问：另跑 `ngrok http 8010`，分享其 HTTPS 地址；代理只转发请求，不保存业务数据

Windows 下也可在 `seat_reservation` 目录运行 `.\start-web.ps1`，效果与上面的启动命令一致。服务、Python 进程与代理需保持运行；关闭后网页无法访问，但数据仍在主机磁盘上。

## 4. 初始账号与密码规则

新交付库只创建一个系统管理员：**Y0001 / 12345678**，免首次强制改密。重复启动不会重置已修改的密码，也不会删除后来创建的账号。

| 场景 | 规则 |
|---|---|
| 新建账号（密码留空） | 临时密码统一为 **`12345678`**，`must_change_password=True` |
| 重置密码 | 临时密码统一为 **`12345678`**，旧密码与全部会话立即失效 |
| 学生 / 教师 / 自习室管理员首次登录 | 必须修改密码后才能访问业务接口 |
| 系统管理员 | 免首次强制改密 |
| 修改后的新密码 | 至少 8 位，且大写、小写、数字、符号至少包含 3 类；**禁止继续使用 `12345678`** |

## 5. 功能概览

- **学生**：按日期与时段找座位、预约与候补、签到/暂离/返座/签退、我的预约、信用分与违规申诉、投诉与评价、消息通知。
- **教师**：本班预约汇总看板，仅展示班级维度数据，不含学生个人明细。
- **自习室管理员**：运营概览与数据分析、现场座位板、预约查询、资源与封禁治理、投诉处理、评价回复、违规申诉、学生信用。
- **系统管理员**：业务参数、房间与座位资源、账号管理、审计日志、系统运行状态、运营分析、CSV 导出。
- **签到方式**：网页签到、60 秒动态凭证（对接闸机）与座位桌贴二维码；桌贴由管理员在「资源」页按自习室打印，扫码后校验本人预约与签到窗口。
- **安全**：PBKDF2 密码哈希、CSRF 双提交、会话空闲/绝对超时、服务端角色权限矩阵、审计只追加、CSV 公式注入防护、生产环境强制 Secure Cookie 并关闭调试文档。

## 6. 目录结构

```text
图书馆预约系统/                     ★ 仓库根
├── README.md                       本文件：唯一权威总说明
├── CHANGELOG.md                    版本时间线与修复明细
├── docs/
│   ├── 系统架构设计报告.md
│   ├── 四人协作分工与开发计划-v1.2.md
│   ├── collaboration/              SFCR 与模块设计
│   └── security-remediation/       安全整改任务书、报告与统计
└── 智慧自习室预约管理系统/
    ├── 初始账号说明.txt
    ├── 环境清单.md
    ├── 系统说明.md
    ├── 需求分析/                    需求规格、原型与证据
    └── seat_reservation/           ★ 完整源码
        ├── run.py                  命令行入口（建库/服务/账号/导出/自检）
        ├── start-web.ps1           Windows 一键启动
        ├── src/seat_reservation/
        │   ├── domain/             领域模型、规则、状态机、引擎
        │   ├── service/            认证会话、调度器、应用装配
        │   ├── storage/            SQLite 迁移、仓储、种子数据
        │   ├── api/                路由、依赖、请求模型
        │   └── web/                HTML / CSS / JS（无构建步骤）
        ├── tests/                  Python 与 Node 测试
        ├── docs/技术文档.md
        ├── scripts/                备份、资源恢复与交付自检
        └── var/                    运行期数据（自动创建，已被 Git 忽略）
```

`src/seat_reservation/` 采用 src 布局，测试导入安装后的包，避免“本地能跑、换机器就崩”。

## 7. 配置

配置优先级：**进程环境变量 > `.env` 文件 > 代码默认值**。`.env` 从项目根目录或 `SEAT_ENV_FILE` 指定路径读取，已存在的进程环境变量不会被覆盖。完整样例见 [`seat_reservation/.env.example`](智慧自习室预约管理系统/seat_reservation/.env.example)。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SEAT_ENV` | `development` | 只接受 development/dev/test/production/prod，未知值直接启动失败 |
| `SEAT_DB_PATH` | `var/delivery.db` | 数据库路径；命令行 `--db` 优先 |
| `SEAT_HOST` / `SEAT_PORT` | `127.0.0.1` / `8010` | 监听地址与端口；`--lan` 等价于监听 `0.0.0.0` |
| `SEAT_COOKIE_SECURE` | `false` | 生产环境必须为 `true`，否则启动失败 |
| `SEAT_TRUST_PROXY` | `false` | 仅在可信反向代理之后开启，开启后读取 `X-Forwarded-For` |
| `SEAT_SESSION_IDLE_MINUTES` / `SEAT_SESSION_ABSOLUTE_HOURS` | 30 / 8 | 会话空闲与绝对有效期 |
| `SEAT_SCHEDULER_ENABLED` / `SEAT_SCHEDULER_INTERVAL_SECONDS` | `true` / 60 | 超时扫描调度器 |
| `SEAT_ACS_TOKEN` | 空 | 门禁/闸机对接令牌，留空则禁用 ACS 端点 |
| `SEAT_PUBLIC_BASE_URL` | 空 | 桌贴二维码中的对外地址；反向代理或 ngrok 场景必须设置 |
| `SEAT_LOG_LEVEL` / `SEAT_LOG_JSON` | `INFO` / `false` | 日志级别与 JSON 输出 |

## 8. 数据与备份

- 交付默认库为 `var/delivery.db`；库中只有系统管理员，学生、教师与自习室管理员账号由管理员创建或导入。
- 首次启动自动完成迁移（当前 schema v5）并生成二维码签名密钥；重复启动不会重置已有账号与密码。
- SQLite 使用 WAL 模式，写事务统一 `BEGIN IMMEDIATE`；预约冲突由唯一约束兜底，同一座位同一时段只会成功一单。
- 在线备份（服务不用停）：

```powershell
.\.venv\Scripts\python.exe scripts/backup.py --db var/delivery.db --out var/backups
```

备份脚本使用 SQLite 在线备份接口，完成后自动执行 `integrity_check`；默认不删除任何文件，只有显式传 `--keep N` 才清理 N 天前的备份。

## 9. 测试

```powershell
cd 智慧自习室预约管理系统/seat_reservation
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/*.test.cjs
```

当前基线：

- `pytest`：**510 passed, 1 skipped**（跳过项为未安装 Playwright 的 `tests/test_e2e.py`，属预期）。
- 并发用例依赖 SQLite 的 WAL 文件锁；仓库放在 OneDrive 等同步目录、或被安全软件实时扫描时，6 条并发用例可能报 `attempt to write a readonly database`，把测试库移到本地非同步目录后重跑即可。
- Node：**39 passed / 0 failed**。
- 自检：`python run.py doctor` 输出运行环境、数据库路径与可写性、Schema 版本及账号/预约规模。

| 测试文件 | 覆盖 |
|---|---|
| `test_acceptance.py` / `test_rules.py` | 验收场景、BR/PR 规则边界 |
| `test_api.py` / `test_api_contract.py` / `test_new_routes_contract.py` | 接口行为、错误码、权限矩阵 |
| `test_account_*.py` / `test_temp_password.py` | 账号生命周期、重置密码、强制改密、临时密码策略 |
| `test_resource_*.py` / `test_waitlist_cancel.py` | 资源治理、封禁生命周期、候补 |
| `test_analytics_*.py` | 运营分析、统计口径、CSV 导出 |
| `test_integration_v12.py` | IT-01～IT-14、IT-02B 跨模块集成 |
| `test_system_api.py` / `test_web_ui_api.py` | 系统状态、座位分母、冲突码区分 |
| `test_qr_checkin.py` | 桌贴令牌签名、篡改拒绝、错座位、签到窗口、打印页权限 |
| `test_security_*.py` | CSRF、越权、并发、凭证与事务安全 |
| `*.test.cjs` | 账号中心、系统页、页面交互与审计视图 |

## 10. 部署要点

- 生产环境设置 `SEAT_ENV=production`、`SEAT_COOKIE_SECURE=true`；`SEAT_DEMO_MODE=true` 在生产环境会被拒绝启动，生产环境同时自动关闭 `/docs` 与 `/openapi.json`。
- 置于 Nginx 等反向代理之后并启用 HTTPS；`location /` 需整体转发，不能只转发 `/api`，否则静态页面 404。
- 数据库必须放在本地磁盘，目录对运行用户可写；不要把 SQLite 放到网络盘。
- 使用 systemd 或 Windows 服务托管，配置自动重启；开启 NTP，签到与超时判定依赖系统时间。
- 定时执行备份并做恢复演练；详细步骤见 `seat_reservation/docs/技术文档.md`。

## 11. 文档索引

| 文档 | 内容 |
|---|---|
| [`CHANGELOG.md`](CHANGELOG.md) | v1.2 → v2.0.1 → v2.0.2 → v2.0.3 → v3.0.0 → v3.1.0 版本时间线 |
| [`docs/文档体系与口径.md`](docs/文档体系与口径.md) | 四层文档定位、当前权威事实、历史差异说明与冲突取信顺序 |
| [`docs/系统架构设计报告.md`](docs/系统架构设计报告.md) | 分层架构、数据模型、状态机、接口与规则映射 |
| [`docs/四人协作分工与开发计划-v1.2.md`](docs/四人协作分工与开发计划-v1.2.md) | v1.2 阶段的协作分工与开发计划（历史记录） |
| [`docs/collaboration/`](docs/collaboration/) | SFCR、模块设计与测试规范（v1.2 阶段记录） |
| [`docs/security-remediation/`](docs/security-remediation/) | 安全整改任务书、测试用例、报告与统计（v1.2～v2.0.3 阶段记录） |
| [`智慧自习室预约管理系统/系统说明.md`](智慧自习室预约管理系统/系统说明.md) | 角色、业务闭环、关键规则、功能模块与已知边界 |
| [`seat_reservation/docs/技术文档.md`](智慧自习室预约管理系统/seat_reservation/docs/技术文档.md) | 部署、备份恢复、安全设计与验收对照 |
| [`智慧自习室预约管理系统/需求分析/需求规格说明书.md`](智慧自习室预约管理系统/需求分析/需求规格说明书.md) | 需求基线 v1.2 |

## 12. 已知限制

- SQLite 单机单写者，不支持多节点同时写入；横向扩容需替换为 PostgreSQL 等数据库。
- 未接入学校统一身份认证；ACS 闸机端点使用共享令牌模拟，未与真实设备联调；通知仅站内消息，没有短信、邮件或微信推送。
- 不提供实时推送：座位状态与未读消息在切页、回到窗口或刷新后更新。
- 浏览器端仅对 Chromium 内核做过完整验证；未安装 Playwright 时，真实浏览器端到端用例跳过 1 条。
- 桌贴是静态码，照片转发无法从技术上完全杜绝；真实校园系统需配合闸机或在座传感器。
- SQLite 的 WAL 依赖本地文件锁：数据库放在云同步目录或被安全软件拦截时，并发写入可能报只读错误，建议使用本地普通目录。
