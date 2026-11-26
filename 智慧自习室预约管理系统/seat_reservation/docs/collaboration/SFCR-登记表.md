# SFCR 登记表（共享文件变更请求）

> 责任人：成员 B（共享文件唯一修改人）
> 依据：《四人协作分工与开发计划-v1.2》第 8.7 节
> 用途：`api/app.py`、`domain/ports.py`、`domain/models.py`、`storage/migrations.py`、`domain/params.py`、`api/schemas.py`、`web/app.js` 共享注册表、`tests/helpers.py`、`conftest.py` 的任何改动，都必须先有一条登记记录，才能在 PR 中合并。

---

## 1. 申请模板（复制到 PR 评论或 issue）

```text
SFCR-<模块>-<三位序号>（例：SFCR-A-001）
申请人 / 模块 / feature 分支：
目标文件：
目标锚点（方法名或章节注释，必须来自当前源码）：
修改前代码（before，带足够上下文）：
精确 diff（after，必须可直接 apply）：
修改理由：
为什么不能用新文件/局部模型替代：
关联测试（文件/用例名）：
预期结果（命令 + 期望输出）：
风险与回滚：
是否需要迁移/参数/协议变更（是/否 + 说明）：
B 处理人 / 应用提交 SHA：
发起人复核（diff/测试/结论）：
最终状态（open / applied / rejected / backup-applied / reverted）：
```

## 2. 登记字段

| 字段 | 说明 |
|---|---|
| SFCR 编号 | `SFCR-<A/B/C/D>-<三位序号>`，模块内递增，不重复 |
| 日期 | 申请日期 |
| 申请人 | 姓名 / GitHub 账号 |
| 目标文件 | 相对仓库路径 |
| 锚点 | 方法名或章节注释 |
| 关联测试 | 测试文件 / 用例名 |
| B 处理人 | 正常为 B；备份处理时写备份人 |
| 应用提交 SHA | 应用人的提交哈希 |
| 发起人复核 | 通过 / 不通过 + 日期 |
| 状态 | open / applied / rejected / backup-applied / reverted |
| 备注 | 升级、回滚、争议等 |

## 3. 处理时限与升级

| 时间 | 动作 |
|---|---|
| T+4 小时 | 申请人在 PR / 群里提醒 B，B 确认收到 |
| T+1 个工作日 | B 在每日 **17:00** 集中处理一批，结果写入本表 |
| T+1 个工作日仍未处理 | 升级给项目负责人，由负责人授权备份处理人 |
| 备份处理 | C 可处理 `engine.py`/`repository.py`/`ports.py`；D 可处理 `app.py`/`app.js`/CI；A 只处理文档，**A 仍不得直接改 `app.py`** |
| 备份之后 | 备份提交标注 `[backup]`；B 在 1 个工作日内补审，不认可则回滚并重走流程 |

## 4. 分支卫生

- 每批 SFCR 开始前，`chore/shared-integration` 从最新 `integration/v1.2` 重建（或 rebase）；PR 合并后删除该分支，下一批重新创建。
- `chore/shared-integration` 只放**其他成员的 SFCR 与跨模块共享变更**；B 自己的 `resource_routes.py`、`app.py` 资源 router 注册随 `feature/resource-module`，不混进 chore 分支。

## 5. 记录

| SFCR 编号 | 日期 | 申请人 | 目标文件 | 锚点 | 关联测试 | B 处理人 | 应用 SHA | 发起人复核 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|
| _（待登记）_ | | | | | | | | | | |

> 本表在 Phase 0 建立；本轮 B 自己改动共享文件（`app.py` 注册 `resource_routes`、`ports.py` 追加协议、`models.py` 加 `Seat.maintenance_by_block`、`migrations.py` 加 v2、`app.js` 共享注册表加 4 个动作）属于**所有权内的直接修改**，随 `feature/resource-module` PR 进入 `integration/v1.2`，不需要给自己开 SFCR，但 PR 说明中必须列出这 5 处共享文件改动，供 A/C/D 复核。
