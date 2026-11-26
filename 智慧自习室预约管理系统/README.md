# 智慧自习室预约管理系统 · 结课提交

> 需求基线：《需求规格说明书》v1.2（2026-10-15）
> 实现形态：FastAPI + SQLite + 原生前端；桌面窗口与安装包共用同一套业务代码
> 规模：9,300 行 Python，62 个接口，17 张业务表（另有 1 张迁移记录表），测试 493 通过 / 1 跳过（另有 Node 前端测试 43 通过）

## 1. 交付物

作业要求提交三样，对应位置如下：

| 要求 | 位置 |
|---|---|
| **环境清单** | [`环境清单.md`](环境清单.md) |
| **系统说明** | [`系统说明.md`](系统说明.md) |
| **完整项目文件** | [`seat_reservation/`](seat_reservation/) |

项目可直接安装，也可从源码运行：

```bat
:: 推荐：双击本目录的 setup.exe 安装桌面版，安装完成后双击桌面「自习室预约」
:: 首次启动自动创建系统管理员 Y0001，随机初始密码在启动提示中只显示一次
```

从源码运行（开发/验收）：

```bat
cd seat_reservation
python -m pip install -r requirements.txt
python run.py --db var/web.db serve --port 8010
```

浏览器打开 <http://127.0.0.1:8010/>。安装包为 `智慧自习室预约管理系统/setup.exe`（唯一发布产物）。
演示数据不再需要命令行参数：用系统管理员登录后进入「系统」页，点击「初始化演示数据」并二次确认即可。

## 2. 目录说明

```text
智慧自习室预约管理系统/
├── README.md            本文件：提交入口与运行步骤
├── setup.exe            ★ 唯一发布产物：桌面版安装包
├── 初始账号说明.txt      账号说明（不含任何明文密码）
├── 环境清单.md            操作系统、依赖版本、命令、存储位置、限制
├── 系统说明.md            架构、状态机、规则映射、验收覆盖
└── seat_reservation/    ★ 完整可运行项目
    ├── run.py                   命令行入口（建库、启服务、账号、导出、自检）
    ├── desktop_launcher.py      桌面窗口入口（无边框窗口）
    ├── installer.py             安装程序源码（生成 setup.exe）
    ├── pyproject.toml           包定义、依赖、测试配置
    ├── requirements.txt         运行与测试依赖
    ├── .env.example             环境变量样例
    ├── src/seat_reservation/    ★ 源码
    │   ├── domain/              领域层：模型、规则、状态机、引擎（唯一实现处）
    │   ├── service/             应用层：认证会话、调度器、依赖装配
    │   ├── storage/             持久层：SQLite 迁移、仓储、种子数据
    │   ├── api/                 接口层：路由、依赖、请求模型
    │   └── web/                 前端：HTML / CSS / JS，无构建步骤
    ├── tests/                   测试：单元、验收、并发、契约、端到端
    ├── docs/技术文档.md          部署、备份恢复、安全、验收
    ├── scripts/                 运维脚本（在线备份）
    ├── assets/                  应用图标
    └── var/                     运行期数据，自动创建，可随时删除
```

`src/seat_reservation/` 两层同名是 Python 的 src 布局：测试运行时导入的是安装后的包，避免「本地能跑、换机器就崩」。

## 3. 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 11（实测 `10.0.22631` x64） |
| Python | 3.11 及以上（实测 3.13.14） |
| SQLite | 由 Python 标准库自带，无需安装 |
| 依赖 | 见 `requirements.txt`，一条命令装完 |

详细版本号与已验证命令见 [`环境清单.md`](环境清单.md)。

## 4. 快速开始

```bat
:: 推荐：安装桌面版
setup.exe   :: 用户级安装，无需管理员权限；安装完成后双击桌面「自习室预约」
```

首次启动自动创建系统管理员 `Y0001`，随机初始密码在启动提示中**只显示一次**，
并写入数据目录的 `initial_admin_credentials.txt`；首次登录必须改密，改密成功后该文件自动删除。

演示数据已融入桌面版：登录后进入「系统」页，点击「初始化演示数据」并二次确认。
系统会写入 3 个自习室、24 个座位与四个角色账号，统一演示密码只在成功提示中显示一次；
初始化会清空业务数据（审计日志保留），并使当前会话失效。

从源码运行（开发/验收）：

```bat
cd seat_reservation
python -m pip install -r requirements.txt
python run.py --db var/web.db serve --port 8010
```

浏览器访问 <http://127.0.0.1:8010/>。直接双击 `desktop_launcher.py` 若报缺少依赖，
用 `启动.bat` 或先执行 `python -m pip install -r requirements.txt`。

> `--demo` / `SEAT_DEMO_MODE=true` 仅保留给自动化测试与命令行验收；日常使用不再需要。

## 5. 演示账号

在「系统」页初始化演示数据后，登录页会列出四个账号，点选只填入账号；
密码为初始化成功提示中**一次性显示**的统一演示密码。

| 角色 | 账号 | 姓名 | 可看内容 |
|---|---|---|---|
| 学生 | `S2023001` | 张伟 | 查座位、预约、签到、暂离、签退、我的预约 |
| 自习室管理员 | `A9001` | 王丽 | 看板、现场座位板、预约查询、投诉、学生 |
| 教师 | `T7001` | 刘明 | 仅班级汇总，无个人明细 |
| 系统管理员 | `Y0001` | 陈刚 | 参数、账号、审计、系统（按 PR-18 不含投诉与黑名单） |

## 6. 命令行

```bat
python run.py --help
```

| 子命令 | 作用 |
|---|---|
| `init-db [--demo]` | 执行迁移；加 `--demo` 同时写入演示数据并输出演示密码 |
| `seed [--at "2025-10-20 08:00"]` | 重写演示数据（会清空业务表），可固定基准时间 |
| `serve [--host] [--port] [--demo]` | 启动 HTTP 服务 |
| `sweep [--at ...]` | 手工执行一次超时扫描（判违约、释放座位） |
| `user list\|create\|passwd\|role` | 账号管理 |
| `params list\|set` | 业务参数查看与修改 |
| `export reservations\|violations\|audit --out 路径` | 导出 CSV |
| `prune [--days 730] [--dry-run]` | 清理超期终态预约 |
| `doctor` | 自检：配置、迁移、约束、调度器 |

## 7. 配置

配置读取优先级：**进程环境变量 > `.env` 文件 > 代码默认值**。
`.env` 从项目根目录读取（可用 `SEAT_ENV_FILE` 指定其他路径），已存在的进程环境变量不会被覆盖；
`.env` 已被 `.gitignore` 忽略。启动时校验，非法值直接拒绝启动。

`SEAT_ENV` 只接受 `development` / `dev` / `test` / `production` / `prod`（自动去空白、转小写），
未知值会直接启动失败，不会静默回退为开发环境。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SEAT_ENV` | `development` | 未知值启动失败；`production` 强制 Secure Cookie 且禁止 Demo |
| `SEAT_DB_PATH` | `var/seat_reservation.db` | 数据库文件路径；演示模式未显式指定时用 `var/seat_demo.db` |
| `SEAT_HOST` / `SEAT_PORT` | `127.0.0.1` / `8010` | 监听地址与端口 |
| `SEAT_LOG_LEVEL` | `INFO` | 日志级别 |
| `SEAT_LOG_JSON` | `false` | 结构化日志，便于日志系统采集 |
| `SEAT_SESSION_IDLE_MINUTES` | `30` | 空闲超时（5～720） |
| `SEAT_SESSION_ABSOLUTE_HOURS` | `8` | 会话绝对有效期（1～72） |
| `SEAT_COOKIE_SECURE` | `false` | 生产环境必须为 `true`，否则启动失败 |
| `SEAT_DEMO_MODE` | `false` | 演示模式，仅 `development`/`test` 可用；生产环境开启会启动失败 |
| `SEAT_DEMO_PASSWORD` | 空 | 演示密码，仅在初始化前可选指定；留空则随机生成并只显示一次 |
| `SEAT_TRUST_PROXY` | `false` | 仅在可信反向代理后开启（读取 `X-Forwarded-For`） |
| `SEAT_ENV_FILE` | 项目根 `.env` | 指定 `.env` 路径 |
| `SEAT_SCHEDULER_ENABLED` | `true` | 是否启用内部定时扫描 |
| `SEAT_SCHEDULER_INTERVAL_SECONDS` | `60` | 扫描间隔（10～3600） |
| `SEAT_ACS_TOKEN` | 空 | 闸机令牌，留空则关闭 ACS 端点 |

生产环境额外行为：`/docs` 与 `/openapi.json` 关闭；启动日志只输出脱敏配置摘要
（不含令牌、密码与数据库完整路径）。

## 8. 测试

```bat
python -m pytest -q
```

除需要浏览器驱动的 `tests/test_e2e.py`（设置 `SEAT_E2E=1` 且安装 playwright 后才执行）外，其余用例应全部通过。
未安装 playwright 时会跳过 1 个端到端用例，属预期行为。

当前结果：`493 passed, 1 skipped`。前端视图测试不依赖第三方包：

```bat
node --test tests/account_frontend.test.cjs tests/system_demo_ui.test.cjs tests/desktop_ux.test.cjs
```

结果：`43 pass / 0 fail`。

| 测试文件 | 覆盖 |
|---|---|
| `test_acceptance.py` | AC 验收标准逐条覆盖 |
| `test_rules.py` | BR / PR 规则边界 |
| `test_api.py` | 接口行为与错误码 |
| `test_api_contract.py` | 接口契约与权限矩阵（含越权 403） |
| `test_admin.py` | 参数、账号、审计、导出 |
| `test_appeal_review_api.py` | 违规申诉审核接口与权限 |
| `test_rule_ids.py` | 规则编号级回归 |
| `test_safety_fixes.py` | 候补/申诉/资源管理回归 |
| `test_resource_api.py` | 自习室/座位接口、PR-05 权限、审计 IP |
| `test_jobs.py` | 调度器、超时扫描、候补顺延 |
| `test_concurrency.py` | 并发预约不超卖 |
| `test_system_demo_api.py` | 系统状态接口、演示数据初始化与审计只增不删 |
| `test_desktop_ux_api.py` | 座位分母、冲突分类、管理员密码保留、桌面导出桥接 |
| `test_e2e.py` | 真实浏览器端到端（默认跳过） |

## 9. 文档

| 文档 | 内容 |
|---|---|
| [`环境清单.md`](环境清单.md) | 操作系统、依赖版本、命令、存储位置、已知限制 |
| [`系统说明.md`](系统说明.md) | 技术选型、分层架构、状态机、规则映射、验收覆盖、分发形态 |
| `seat_reservation/docs/技术文档.md` | 部署、备份与恢复、安全设计、验收对照 |
| `seat_reservation/.env.example` | 环境变量样例 |

## 10. 已知限制

- 仅 Windows 打包；Linux / macOS 需在对应系统重新打包。
- SQLite 单机存储，不支持多节点同时写入。
- 未做并发压测，200 并发指标无实测数据。
- 安装包未数字签名，首次运行可能出现「未知发布者」提示。
- 浏览器仅验证 Chromium 内核。
- 时间使用本地 naive `datetime`，不含时区换算。
- 未接入真实统一身份认证、门禁与消息推送，账号与凭证为教学模拟。
