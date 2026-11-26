# 智慧自习室预约管理系统

高校图书馆／自习室座位预约系统。浏览器访问，无需客户端；主机运行一个 FastAPI/Uvicorn 服务，业务数据保存在主机本地的 SQLite 文件中，支持本机、局域网与反向代理三种访问方式。

## 快速开始

在 `seat_reservation` 目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --db var/delivery.db serve --lan --port 8010
```

本机打开 http://127.0.0.1:8010/ 。其他设备连接同一局域网后，打开 `http://<主机IPv4>:8010/`；外网访问另开 `ngrok http 8010`，分享其 HTTPS 地址。电脑与 Python 服务需保持运行，ngrok 只负责转发。

## 初始账号

交付库内置一个系统管理员：`Y0001`，初始密码见 `初始账号说明.txt`。

管理员在「账号」页新建用户，所有新账号使用统一临时密码；学生、教师、自习室管理员首次登录必须修改密码，系统管理员免首次强制改密。密码重置同样使用临时密码，并立即失效旧密码与全部会话。

## 功能

- 学生：找座位、预约、签到（网页 / 动态凭证 / 桌贴二维码）、暂离、返座、续约、签退、取消、候补、信用与申诉、投诉与评价。
- 教师：本班预约与违约汇总。
- 自习室管理员：本室座位治理、封禁、现场巡检、违约登记、强制释放、投诉办结。
- 系统管理员：账号、资源、业务参数、审计日志、运营分析与 CSV 导出。

## 文档

| 文档 | 内容 |
|---|---|
| [`系统说明.md`](系统说明.md) | 角色、业务闭环、关键规则、功能模块、已知边界 |
| [`环境清单.md`](环境清单.md) | 运行环境、依赖版本、端口、环境变量 |
| [`初始账号说明.txt`](初始账号说明.txt) | 交付库账号与密码策略 |
| [`seat_reservation/docs/技术文档.md`](seat_reservation/docs/技术文档.md) | 部署、备份恢复、安全设计、验收对照 |
| [`seat_reservation/docs/局域网使用说明.md`](seat_reservation/docs/局域网使用说明.md) | 局域网访问与故障排查 |
| [`../README.md`](../README.md) | 仓库总览与完整交付说明 |

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q     # 510 passed, 1 skipped
node --test tests/*.test.cjs                 # 39 passed
```

## 座位数据初始化

若交付库尚无座位，可从一个已有资源的数据库导入自习室与座位：

```powershell
.\.venv\Scripts\python.exe scripts/restore_resources.py --source var/web.db --target var/delivery.db
```

脚本会先在线备份交付库到 `var/backups/`，再以事务导入缺失的房间与座位；同编号资源不覆盖，账号与预约不迁移，失败自动回滚。
