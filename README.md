# 智慧自习室预约管理系统

> 当前版本：**v2.0.3**（桌面安装包发布版）
> 技术形态：FastAPI + SQLite + 原生 HTML/CSS/JS；桌面窗口与安装包共用同一套业务代码
> 质量基线：`pytest` **499 passed / 1 skipped**；Node 前端视图测试 **43 passed**
> 本文件是仓库**唯一权威总说明**；设计、验收与历史记录见文末文档索引。

## 1. 交付物

| 交付物 | 位置 |
|---|---|
| **桌面安装包（唯一发布产物）** | [`智慧自习室预约管理系统/setup.exe`](智慧自习室预约管理系统/setup.exe) |
| 账号说明（不含任何明文密码） | [`智慧自习室预约管理系统/初始账号说明.txt`](智慧自习室预约管理系统/初始账号说明.txt) |
| 版本变更记录 | [`CHANGELOG.md`](CHANGELOG.md) |
| 环境清单 | [`智慧自习室预约管理系统/环境清单.md`](智慧自习室预约管理系统/环境清单.md) |
| 系统说明 | [`智慧自习室预约管理系统/系统说明.md`](智慧自习室预约管理系统/系统说明.md) |
| 完整源码与测试 | [`智慧自习室预约管理系统/seat_reservation/`](智慧自习室预约管理系统/seat_reservation/) |

## 2. 快速开始（唯一推荐启动方式）

1. 双击 [`setup.exe`](智慧自习室预约管理系统/setup.exe) 完成用户级安装（无需管理员权限）。
2. 双击桌面「自习室预约」启动桌面程序。
3. 首次启动自动创建系统管理员 `Y0001`：
   - 初始密码为**高强度随机值**，在启动提示窗口中只显示一次；
   - 同时写入数据目录 `data/initial_admin_credentials.txt`，首次改密成功后自动删除。
4. 登录后如需课程演示数据，进入「系统」页点击「初始化演示数据」并二次确认，无需命令行参数。

业务数据默认跟随安装目录（`data/`、`logs/`）；卸载只删除程序文件，保留业务数据。

## 3. 账号与密码规则

| 场景 | 规则 |
|---|---|
| 首次安装的系统管理员 `Y0001` | 高强度随机密码，写入 `data/initial_admin_credentials.txt`；首次登录强制改密 |
| 管理员新建账号（留空密码） | 临时密码统一为 **`12345678`**，`must_change_password=True` |
| 管理员重置密码 | 临时密码统一为 **`12345678`**，旧密码与所有会话立即失效，下次登录强制改密 |
| 用户首次登录/重置后改密 | 新密码至少 8 位，且大写字母、小写字母、数字、符号至少包含 3 类；**禁止继续使用 `12345678`** |
| 演示数据账号 | 学生/教师/自习室管理员使用统一演示密码（初始化时只显示一次）；已有系统管理员保留原密码 |

演示账号：

| 角色 | 账号 | 姓名 |
|---|---|---|
| 学生 | `S2023001` | 张伟 |
| 教师 | `T7001` | 刘明 |
| 自习室管理员 | `A9001` | 王丽 |
| 系统管理员 | `Y0001`（已有管理员保留原密码） | 陈刚 |

## 4. 功能概览

- **学生**：找座位、预约、候补、签到/签退、暂离/返座、我的预约、信用与申诉、投诉与评价、消息。
- **教师**：本班预约汇总看板（不含学生个人明细）。
- **自习室管理员**：运营概览、现场座位板、预约查询、投诉处理、学生信用、资源与封禁治理。
- **系统管理员**：业务参数、账号管理、审计日志、运营分析、系统运行状态、CSV 导出、演示数据初始化。
- **桌面能力**：内嵌 WebView2 窗口；数据导出走原生另存为对话框；时间/调度/备份与网页版一致。
- **安全**：PBKDF2 密码哈希、CSRF 双提交、会话过期、角色权限矩阵、审计只追加、CSV 公式注入防护、生产环境禁用演示模式。

## 5. 目录结构

```text
D:\seat_hotfix_v12/              ★ 唯一仓库
├── README.md                    本文件：唯一权威总说明
├── CHANGELOG.md                 版本时间线与修复明细
├── docs/                        设计、协作与验收记录
│   ├── 系统架构设计报告.md
│   ├── 四人协作分工与开发计划-v1.2.md
│   ├── collaboration/           SFCR 与模块设计
│   └── security-remediation/    安全整改任务书、报告、统计表
└── 智慧自习室预约管理系统/
    ├── setup.exe                ★ 唯一发布产物（v2.0.3）
    ├── 初始账号说明.txt          账号说明（与 README 第 3 节一致，不含密码）
    ├── 环境清单.md
    ├── 系统说明.md
    ├── 需求分析/                 需求规格、原型与证据
    └── seat_reservation/       ★ 完整源码
        ├── run.py              开发/验收命令行入口
        ├── desktop_launcher.py 桌面窗口入口
        ├── installer.py        安装程序源码（生成 setup.exe）
        ├── src/seat_reservation/
        │   ├── domain/         领域模型、规则、状态机、引擎
        │   ├── service/        认证会话、调度器、应用装配
        │   ├── storage/        SQLite 迁移、仓储、种子数据
        │   ├── api/            路由、依赖、请求模型
        │   └── web/            HTML/CSS/JS（无构建步骤）
        ├── tests/              Python 与 Node 测试
        ├── docs/技术文档.md
        ├── scripts/            备份与交付自检
        └── var/                运行期数据（自动创建，可删除）
```

`src/seat_reservation/` 采用 src 布局：测试导入安装后的包，避免“本地能跑、换机器就崩”。

## 6. 测试

```bash
cd 智慧自习室预约管理系统/seat_reservation
python -m pytest -q -p no:cacheprovider
node --test tests/account_frontend.test.cjs tests/system_demo_ui.test.cjs tests/desktop_ux.test.cjs
```

当前基线：

- `pytest`：**499 passed, 1 skipped**（跳过项为未安装 Playwright 的 `tests/test_e2e.py`，属预期）。
- Node：**43 passed / 0 failed**。
- 交付自检：`python scripts/verify_release.py --skip-exe` → **11/11 通过**。

核心测试文件：

| 文件 | 覆盖 |
|---|---|
| `test_acceptance.py` / `test_rules.py` | AC 验收、BR/PR 规则边界 |
| `test_api.py` / `test_api_contract.py` | 接口行为、错误码、权限矩阵 |
| `test_account_engine.py` / `test_account_api.py` / `test_account_security.py` | 账号生命周期、重置密码、强制改密、越权 |
| `test_temp_password.py` | 临时密码 12345678、强密码策略、禁止沿用弱密码 |
| `test_resource_*.py` / `test_security_remediation_c.py` | 资源治理、预约冲突、候补 |
| `test_analytics_*.py` | 运营分析、导出、统计口径 |
| `test_integration_v12.py` | IT-01～IT-14、IT-02B 跨模块集成 |
| `test_system_demo_api.py` | 系统状态、演示数据初始化、管理员密码保留 |
| `test_desktop_ux_api.py` | 房间分母、冲突码、桌面导出桥接 |
| `*.test.cjs` | 系统页、建号联想、演示初始化、7 项桌面交互 |

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| [`CHANGELOG.md`](CHANGELOG.md) | v1.2 → v2.0.1 → v2.0.2 → v2.0.3 完整时间线 |
| [`docs/系统架构设计报告.md`](docs/系统架构设计报告.md) | 分层架构、数据模型、接口与规则映射 |
| [`docs/四人协作分工与开发计划-v1.2.md`](docs/四人协作分工与开发计划-v1.2.md) | 历史协作计划（版本口径以本 README 为准） |
| [`docs/collaboration/`](docs/collaboration/) | SFCR、模块设计与测试规范 |
| [`docs/security-remediation/reports/全系统回归与结题验收报告.md`](docs/security-remediation/reports/全系统回归与结题验收报告.md) | 安全整改、集成回归、桌面走查与最终验收 |
| [`docs/security-remediation/reports/测试样例统计表.md`](docs/security-remediation/reports/测试样例统计表.md) | 测试样例编号与统计口径 |
| [`智慧自习室预约管理系统/系统说明.md`](智慧自习室预约管理系统/系统说明.md) | 技术选型、状态机、规则与分发形态 |
| [`智慧自习室预约管理系统/seat_reservation/docs/技术文档.md`](智慧自习室预约管理系统/seat_reservation/docs/技术文档.md) | 部署、备份恢复、安全设计与验收对照 |
| [`智慧自习室预约管理系统/需求分析/原型说明.md`](智慧自习室预约管理系统/需求分析/原型说明.md) | 需求阶段交互原型说明 |
| [`智慧自习室预约管理系统/需求分析/需求规格说明书.md`](智慧自习室预约管理系统/需求分析/需求规格说明书.md) | 需求基线 v1.2 |

## 8. 开发与从源码运行（非交付启动方式）

```bash
cd 智慧自习室预约管理系统/seat_reservation
python -m pip install -r requirements.txt
python run.py --db var/web.db serve --port 8010
```

浏览器访问 <http://127.0.0.1:8010/>。仅在开发/验收时使用；日常使用请安装 `setup.exe`。
`--demo` / `SEAT_DEMO_MODE=true` 仅保留给自动化测试，界面上统一通过「系统 → 初始化演示数据」完成。

## 9. 部署与安全

- 生产环境必须设置 `SEAT_ENV=production`、`SEAT_COOKIE_SECURE=true`，并置于 HTTPS 反向代理之后。
- 生产环境禁止 `SEAT_DEMO_MODE=true` 与「初始化演示数据」操作。
- 数据库为单机 SQLite（WAL）；备份时同时复制 `.db` 与 `-wal`，或先停止服务。
- 演示密码与初始管理员密码均只展示一次，不写入审计/日志；审计日志只追加、不可修改。
- 详细变量见 [`环境清单.md`](智慧自习室预约管理系统/环境清单.md) 与 `seat_reservation/.env.example`。

## 10. 已知限制

- 仅提供 Windows 桌面安装包；Linux/macOS 需在对应系统重新构建。
- SQLite 单机存储，不支持多节点同时写入。
- 安装包未数字签名，首次运行可能出现「未知发布者」提示。
- 未接入真实统一身份认证、门禁与消息推送；相关能力为教学模拟。
- 未安装 Playwright 时，真实浏览器端到端用例跳过 1 条（预期）。
