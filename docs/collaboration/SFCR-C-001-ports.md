# SFCR-C-001：`domain/ports.py` 增加续约协议方法

> 文档口径（v2.0.3，2026-11-26）：当前发布版本 **v2.0.3**；`pytest` **499 passed / 1 skipped**，Node **43 passed**；用户启动方式统一为安装 `setup.exe`。本文历史章节中的旧版本/测试数字仅代表当时基线，最新口径以根目录 `README.md` 与 `CHANGELOG.md` 为准。


> 按 v1.2 计划 8.7 模板提交。C 不直接修改共享文件，请 B 在
> `chore/shared-integration` 应用并审核；应用后 C 复核。

## 基本信息

| 项目 | 内容 |
|---|---|
| SFCR 编号 | `SFCR-C-001` |
| 申请人 / 模块 / feature 分支 | 成员 C / 预约核心增强 / `feature/reservation-module` |
| 目标文件 | `智慧自习室预约管理系统/seat_reservation/src/seat_reservation/domain/ports.py` |
| 目标锚点 | `# -- 预约` 章节内，`update_reservation` 之后、`occupy` 之前；以及 `occupy` 之后、`release` 之前（方法名来自当前源码） |
| 关联测试 | `tests/test_extension.py`、`tests/test_new_routes_contract.py` |
| 是否需要迁移/参数/协议变更 | 否（仅补协议声明；无迁移、无参数、无数据库结构变化） |
| 状态 | open |

## 修改前代码（before，带上下文）

```python
    def insert_reservation(self, reservation: Reservation) -> None: ...
    def update_reservation(self, reservation: Reservation) -> None: ...
    def occupy(self, reservation: Reservation) -> None: ...
    def release(self, reservation_id: str) -> None: ...
```

## 精确 diff（after，可直接 apply）

```diff
     def insert_reservation(self, reservation: Reservation) -> None: ...
     def update_reservation(self, reservation: Reservation) -> None: ...
+    def extend_reservation(
+        self,
+        reservation: Reservation,
+        new_end_slot: int,
+        new_end_at: datetime,
+        expected_version: int,
+    ) -> bool: ...
     def occupy(self, reservation: Reservation) -> None: ...
+    def occupy_extension(
+        self,
+        reservation: Reservation,
+        new_start_slot: int,
+        new_end_slot: int,
+    ) -> None: ...
     def release(self, reservation_id: str) -> None: ...
```

## 修改理由

`DomainEngine` 的类型标注为 `Repository` 协议；C 在
`storage/repository.py` 新增了 `extend_reservation` 与 `occupy_extension`，
但协议未声明这两个方法时，类型检查与接口契约不完整。运行期
`AppContext` 注入的是 `SqliteRepository`，功能可用；补协议是为了让
“引擎只依赖端口”的约定继续成立，并让其他实现/测试替身知道这两个方法。

## 为什么不能用新文件/局部模型替代

两个方法由引擎直接调用在 `self.repo` 上，必须属于 `Repository` 端口；
无法用局部模型或新文件替代协议声明。按 v1.2 8.6，`ports.py` 仅 B 可修改，
因此提交 SFCR，不在 C 的分支直接改。

## 关联测试

```bash
.venv/bin/python -m pytest tests/test_extension.py -q
.venv/bin/python -m pytest tests/test_new_routes_contract.py -q
```

预期：`36 passed`（续约）与 `12 passed, 5 skipped`（契约；B 路由未合并时 skip）。
应用本 SFCR 后，业务行为与测试结果不变，仅类型/协议层补全。

## 风险与回滚

- 风险：极低。仅新增协议方法，不改既有签名、不改实现、不影响迁移。
- 回滚：删除上述两段方法声明即可；无数据变更。

## B 处理人 / 应用提交 SHA

| 项目 | 内容 |
|---|---|
| B 处理人 | 待 B 填写 |
| 应用提交 SHA | 待 B 填写 |
| 发起人复核 | 待 C 在 B 应用后填写（diff/测试/结论） |
| 最终状态 | open |
