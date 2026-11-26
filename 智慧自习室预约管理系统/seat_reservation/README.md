# 智慧自习室预约管理系统（网页版）

本项目通过浏览器使用。主机运行一个 FastAPI/Uvicorn 服务，SQLite 保存在主机本地；其他设备通过局域网地址或 ngrok 的 HTTPS 地址访问同一服务。已取消桌面窗口、安装包和演示账号界面。

## 启动

在 `智慧自习室预约管理系统/seat_reservation` 目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --db var/delivery.db serve --lan --port 8010
```

本机打开 http://127.0.0.1:8010/ 。其他设备连接同一局域网后，打开主机 IPv4 地址的 8010 端口。外网访问时另外运行 `ngrok http 8010`，分享 ngrok 显示的 HTTPS 地址。电脑、Python 服务与 ngrok 必须保持运行，ngrok 不负责保存业务数据。

## 初始账号

新交付库只创建一个系统管理员：**Y0001 / 12345678**，免首次强制改密。重复启动不会重置已修改的密码，也不会删除后来创建的账号。

管理员在账号管理中创建用户，所有新账号初始密码统一为 **12345678**，界面不再允许另填初始密码。学生、教师、自习室管理员首次登录必须修改密码，系统管理员免首次强制改密。非管理员改密完成前不能访问业务接口。密码重置同样使用 12345678，并按角色决定是否必须改密。

## 从演示版切换

停止原服务，改用上面的 `var/delivery.db` 启动命令。旧 `var/web.db` 不删除、不覆盖，保留历史演示记录；不要再用它作为交付库。新库不复制演示账号、预约或座位，管理员需自行创建真实账号与资源。不再提供网页演示初始化入口和演示账号查询接口。

数据库路径也可通过 `SEAT_DB_PATH` 设置；显式 `--db` 优先。交付默认库为 `var/delivery.db`。若已有 `.env` 指向旧库，请更新配置或使用上面的显式命令。

## 多人使用

不同账号可同时登录。预约和座位状态来自同一个后端数据库；两人抢同一座位同一时段，只能有一份预约成功。其他用户刷新或重新查询后看到座位不可用，目前不提供实时推送。客户端不能直接读写 SQLite 文件。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/account_frontend.test.cjs tests/web_ui.test.cjs tests/system_ui.test.cjs
```

历史课程评审与发布记录保留供追溯，其中桌面安装、演示账号和旧密码策略已由本说明替代。

## 恢复旧库座位

如果交付库尚无座位，在项目目录执行：

```powershell
.\.venv\Scripts\python.exe scripts/restore_resources.py --source var/web.db --target var/delivery.db
```

脚本先用 SQLite 在线备份创建 `var/backups/` 下的交付库备份，再以事务导入缺失的自习室和座位；同编号资源不覆盖，账号与预约不迁移，失败自动回滚。完成后刷新页面重新查询座位。旧库保留到验收完成；维护或停用状态会保留，可在资源管理中核查。

审计页面的动作、角色和对象类型采用中文展示，筛选仍使用原始动作编号，兼容历史审计记录。
