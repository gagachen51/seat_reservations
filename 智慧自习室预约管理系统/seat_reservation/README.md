# seat_reservation

智慧自习室预约管理系统的源码目录。技术栈为 Python 3.11+ / FastAPI / SQLite，前端是原生 HTML/CSS/JavaScript 单页应用，没有 npm 与构建步骤。

## 目录结构

```
seat_reservation/
├── run.py                     命令行入口
├── requirements.txt           运行与测试依赖
├── pyproject.toml             包定义与 pytest 配置
├── .env.example               配置样例
├── scripts/
│   ├── backup.py              在线备份 + 完整性校验
│   ├── restore_resources.py   从旧库导入自习室与座位
│   └── verify_release.py      交付前检查
├── src/seat_reservation/
│   ├── api/                   FastAPI 应用、路由、依赖、请求模型
│   ├── domain/                领域引擎、规则、参数、仓储端口
│   ├── service/               容器、认证、调度器
│   ├── storage/               SQLite、仓储、迁移、种子
│   ├── web/                   index.html / app.js / styles.css
│   ├── config.py              环境变量与校验
│   ├── security.py            密码哈希、令牌、CSRF、签到凭证
│   └── network.py             本机 IPv4 枚举（`--lan` 启动提示）
├── tests/                     后端 pytest 与前端 Node 测试
└── docs/                      技术文档、局域网说明
```

## 常用命令

```powershell
# 初始化 / 迁移数据库
.\.venv\Scripts\python.exe run.py --db var/delivery.db init-db

# 启动（局域网可访问）
.\.venv\Scripts\python.exe run.py --db var/delivery.db serve --lan --port 8010

# 只监听本机
.\.venv\Scripts\python.exe run.py --db var/delivery.db serve --port 8010

# 执行一次超时扫描（不启动服务）
.\.venv\Scripts\python.exe run.py --db var/delivery.db sweep

# 账号
.\.venv\Scripts\python.exe run.py --db var/delivery.db user list
.\.venv\Scripts\python.exe run.py --db var/delivery.db user create S2026001 张三 --role student
.\.venv\Scripts\python.exe run.py --db var/delivery.db user passwd S2026001

# 业务参数
.\.venv\Scripts\python.exe run.py --db var/delivery.db params list
.\.venv\Scripts\python.exe run.py --db var/delivery.db params set checkin.before_minutes 20

# 导出与清理
.\.venv\Scripts\python.exe run.py --db var/delivery.db export reservations --out var/reservations.csv
.\.venv\Scripts\python.exe run.py --db var/delivery.db prune --days 730 --dry-run

# 自检
.\.venv\Scripts\python.exe run.py --db var/delivery.db doctor
```

## 配置

配置读取优先级：进程环境变量 > `.env` 文件 > 代码默认值。完整变量表见 [`../环境清单.md`](../环境清单.md)。

生产环境至少需要：

```dotenv
SEAT_ENV=production
SEAT_COOKIE_SECURE=true
SEAT_DB_PATH=var/delivery.db
SEAT_PUBLIC_BASE_URL=https://seat.example.edu
```

`SEAT_PUBLIC_BASE_URL` 决定座位桌贴二维码里的地址。经反向代理或 ngrok 访问时如果不设置，二维码会写入内网地址，手机扫码打不开。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q     # 510 passed, 1 skipped
node --test tests/*.test.cjs                 # 39 passed
```

跳过项是未安装 Playwright 的浏览器端到端用例。并发用例依赖 SQLite 的 WAL 文件锁；数据库放在云同步目录、网络盘或被安全软件实时扫描时，可能出现 `attempt to write a readonly database`，把库移到本地普通目录即可。

## 数据库迁移

迁移定义在 `src/seat_reservation/storage/migrations.py`，当前版本 **5**：

| 版本 | 内容 |
|---|---|
| v1 | 初始表结构、索引、审计触发器 |
| v2 | `seats.maintenance_by_block`，封禁生命周期 |
| v4 | `resource_constraints`，slot 父预约一致性与候补范围 |
| v5 | `app_secrets`，二维码签名密钥 |

v3 预留给共享层审计扩展，不跳号复用。`init-db` 与 `serve` 启动时都会执行迁移，重复执行安全。

## 相关文档

- [`docs/技术文档.md`](docs/技术文档.md)：部署、备份恢复、安全设计、验收对照
- [`docs/局域网使用说明.md`](docs/局域网使用说明.md)：局域网访问与排查
- [`../系统说明.md`](../系统说明.md)：业务规则与功能边界
