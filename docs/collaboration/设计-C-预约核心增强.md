# C 模块：预约核心增强（续约 + 候补取消）

> 文档阶段：v1.2–v2.0.3 过程记录（2026-11）。文中的版本号、测试数量、交付形态与启动方式均为当时基线；当前实现为 v3.1.0 / schema v5，采用浏览器访问的 Web 交付，口径见根目录 `README.md` 与 `CHANGELOG.md`。


## 1. 范围与交付状态

> 当前集成状态：C 的续约/候补功能与安全整改已合入 `integration/v1.2` @ `be8f2de`；数据库迁移 **v4**，全量 **470 passed, 1 skipped**。本文保留首次交付的设计说明，M-04/M-06/M-07/L-07 的最终实现以《C模块安全整改报告.md》和 `tests/test_security_remediation_c.py` 为准。

本模块基于 `integration/v1.2`（基线提交 `397bddd`）创建 `feature/reservation-module`，
实现 v1.2 计划中成员 C 的预约核心增强：

- 使用中/暂离中的预约延长时段（续约）；
- 学生取消本人候补；
- 续约的纯规则函数、引擎事务、仓储方法、学生端路由与预约详情入口；
- 续约并发、候补取消口径、全组新路由契约冒烟与测试规范。

**v1.2 保守范围**：不新增业务规则、不新增参数、不新增表、不新增迁移；`pending`
续约、单次续约独立上限、候补取消自动顺延分别等待 Q-03/Q-04/Q-05 确认，确认前
一律不实现。共享文件 `domain/ports.py` 的协议方法通过
[`SFCR-C-001-ports.md`](SFCR-C-001-ports.md) 提交 B 应用，本分支不直接修改。

## 2. 文件与代码分区

| 文件 | 类型 | 内容 |
|---|---|---|
| `src/seat_reservation/domain/reservation_rules.py` | 新增 | 续约状态/时长/边界/额度、最大可延长分钟、候补可取消状态的纯函数 |
| `src/seat_reservation/domain/engine.py` | 修改（C 分区） | `extend_reservation`、`cancel_waitlist`；只在 `cancel_reservation` 之后、`# 候补` 之前，以及 `confirm_waitlist` 之后、`# 定时任务` 之前插入 |
| `src/seat_reservation/storage/repository.py` | 修改（C 分区） | `extend_reservation`（版本乐观更新）、`occupy_extension`（只占新增时段） |
| `src/seat_reservation/api/routes/student_routes.py` | 修改 | 两个新路由与局部请求模型 |
| `src/seat_reservation/web/app.js` | 修改（C 区域） | `openReservation` 增加“延长时段”入口、`openExtension` 弹窗；不改共享注册表 |
| `src/seat_reservation/web/styles.css` | 修改（追加段） | 延长弹窗样式，不修改既有样式 |
| `tests/test_extension.py` | 新增 | 续约规则、引擎、事务、并发、HTTP 契约 |
| `tests/test_waitlist_cancel.py` | 新增 | 候补取消规则、Q-05 口径、权限、HTTP 契约 |
| `tests/test_new_routes_contract.py` | 新增 | A/B/C/D 15 条计划路由的契约冒烟（C 作为测试牵头维护） |
| `docs/collaboration/测试规范.md` | 新增 | 测试分层、命名、执行与记录规范 |
| `docs/collaboration/SFCR-C-001-ports.md` | 新增 | 请求 B 在 `domain/ports.py` 增加两个协议方法 |

未修改：`api/app.py`、`domain/ports.py`、`domain/models.py`、`domain/params.py`、
`storage/migrations.py`、`api/schemas.py`、`tests/helpers.py`、`tests/conftest.py`、
`app.js` 的 `NAV/VIEW_META/VIEWS/AFTER/ACTIONS` 注册表，以及其他成员的代码分区。

## 3. 接口

所有路径以 `/api` 开头；写请求沿用登录 Cookie + `X-CSRF-Token`；成功为
`{ok:true,data,message}`，失败为 `{ok:false,error:{code,message,rule,details}}`。

| 方法 | 路径 | 角色 | 请求 | 成功响应 |
|---|---|---|---|---|
| POST | `/api/reservations/{id}/extend` | student（本人） | `{"extra_minutes": 30}` | 200，预约最新视图（含 `end`、`end_slot`、`minutes`） |
| POST | `/api/waitlist/{id}/cancel` | student（本人） | `{}` | 200，`{"entry": {...}}` |

`extra_minutes` 的“形状”由路由局部模型 `ReservationExtendIn` 校验（正整数）；
“30 分钟整数倍、下限、上限、额度、冲突”等业务规则全部由领域引擎判定，符合
项目“路由只做形状校验、规则集中在引擎”的约定。

## 4. 续约规则与校验顺序

1. **本人与角色**：`_own_reservation` 要求 `actor.role == student` 且
   `reservation.user_id == actor.id`，否则 403 `PR-03`；预约不存在 404 `NOT_FOUND`。
2. **状态与时间**：只允许 `in_use` / `away`，且 `now < end_at`；否则 400 `ST-12`。
   `pending` 续约按 Q-03 待确认，v1.2 先拒绝。
3. **时长粒度**：`extra_minutes` 必须为不小于 30 的 30 整数倍，否则 422
   `VALIDATION_ERROR`（纯函数 `extension_minutes_error`）。
4. **座位/自习室**：座位不存在 404；自习室非 `open` → 400 `BR-09`。
5. **边界（BR-01）**：`new_end_slot = end_slot + extra//30`，须满足
   `new_end_slot <= room.close_slot`，且 `minutes_of_slots(start_slot, new_end_slot)
   <= booking.max_minutes`。
6. **当日额度（BR-02）**：`daily_usage.minutes + extra_minutes
   <= booking.daily_max_minutes`；不会新增预约个数。
7. **座位可用性（BR-09）**：`seat.admin_status` 必须为 `available`；延长区间
   `[end_at, new_end_at)` 不得与未释放封禁重叠。
8. **冲突（BR-11）**：座位在 `[end_slot, new_end_slot)` 无其他占用；本人在该区间
   无其他有效预约。相邻时段（`new_end == 下一预约.start`）不算重叠。
9. **写入**：同一写事务内先 `occupy_extension` 插入新增时段（唯一约束兜底），
   再 `extend_reservation` 按版本号更新 `end_slot/end_at/updated_at/version`；
   任一步失败整体回滚，不产生“半截时段”。
10. **审计**：成功后写 `audit_logs`，`action=reservation_extend`，
    `before={"end_slot","end_at"}`，`after={"end_slot","end_at","extra_minutes"}`。

参数来源：续约使用引擎当前 `self.params` 的 `booking.max_minutes` /
`booking.daily_max_minutes`，与创建预约的校验口径一致；不读取/不新增独立续约参数。
`room.close_slot` 来自座位所属自习室。

## 5. 候补取消规则

1. 只允许学生本人；非学生或非本人 → 403 `PR-04`；不存在 → 404。
2. 只允许 `queued` / `notified`；`confirmed` / `expired` / `cancelled` → 400 `ST-12`。
3. v1.2 只把 `waitlist.status` 置为 `cancelled`，**不改其他字段**。
4. **Q-05 确认前不调用 `_offer_waitlist`**，不自动通知/顺延下一位；取消不记违规、
   不扣信用分。

## 6. 事务与并发

- `AppContext.engine()` 的写事务使用 `BEGIN IMMEDIATE`，同一时刻只有一个写者；
  续约在同一事务内完成“查冲突 → 占新增时段 → 更新预约 → 写审计”。
- `reservation_slots` 的主键 `(seat_id, date, slot)` 与唯一索引
  `(user_id, date, slot)` 是并发底线：两个请求同时占用同一新增时段时，只有一个
  能插入成功。
- `UPDATE reservations ... WHERE id=? AND version=?` 提供乐观并发控制；版本不匹配
  时仓储返回 `False`，引擎抛 409 `CONFLICT`，事务回滚已插入的时段。
- 并发测试见 `tests/test_extension.py::test_concurrent_extension_only_one_wins` 与
  `test_extension_version_conflict_rolls_back_extension_slots`。

## 7. 前端

- 只在 `openReservation` 内增加“延长时段”按钮：`in_use` / `away` 且当前时间早于
  `end` 时显示；不修改共享注册表。
- `openExtension` 弹窗提供 +30 / +60 两个选项，实时展示新结束时间，并提示单次最长、
  当日额度、封禁与后续预约限制。
- 成功调用 `POST /reservations/{id}/extend` 后关闭弹窗、提示并刷新当前视图；
  失败沿用统一 `handleError`，显示 `error.message` 与规则编号。
- 样式只追加 `.extend-*` 段落，不改他人选择器。

## 8. 测试与验收

- `tests/test_extension.py`：状态、粒度、BR-01/BR-02/BR-09/BR-11、审计、相邻边界、
  回滚、双线程并发、HTTP 401/403/404/409/422。
- `tests/test_waitlist_cancel.py`：queued/notified 取消、Q-05 不顺延、只改 status、
  他人/非学生拒绝、终态拒绝、HTTP 契约。
- `tests/test_new_routes_contract.py`：15 条计划路由；当前分支 A 路由临时装配、
  B 路由未合并时显式 skip；C 的两条路由必须注册；达到 12 条后强制最低覆盖。
- 回归：`python -m pytest -q` 全量通过；`tests/test_concurrency.py`、
  `tests/test_safety_fixes.py`、AC-01～AC-10 不回归。

**手工验收**：使用中预约详情 → “延长时段” → 选 30/60 → 确认 → 结束时间与
`reservation_slots` 同步更新；暂离预约同样可延长；候补页取消后状态变为“已取消”，
下一位候补仍为“排队中”（Q-05 前不顺延）。

## 9. 依赖与待确认

| 项 | 说明 |
|---|---|
| B 的座位/封禁可用性 | 复用现有 `seat.admin_status`、`seat_block_overlaps`，不依赖 B 的新字段 |
| A 的候补展示页 | A 已调用 `/waitlist/{id}/cancel`，返回结构为 `{"entry": ...}`，不影响其页面 |
| B 应用 ports SFCR | 见 `SFCR-C-001-ports.md`；未应用时运行期仍由 `SqliteRepository` 提供方法 |
| Q-03 | `pending` 续约：未确认，当前拒绝 |
| Q-04 | 单次续约独立上限：未确认，不新增参数，只用现有 `booking.max_minutes` |
| Q-05 | 候补取消是否顺延：未确认，当前只改状态，不通知下一位 |

## 10. 本地测试执行记录

```text
命令：.venv/bin/python -m pytest -q
结果：265 passed, 6 skipped
新增：test_extension.py 36 passed；test_waitlist_cancel.py 17 passed；
      test_new_routes_contract.py 12 passed, 5 skipped
跳过：tests/test_e2e.py（未安装 playwright）；
      test_new_routes_contract.py 中 B 的 4 条未合并路由 + 覆盖不足 12 条的复核用例
环境：Linux / Python 3.12 / 依赖见 requirements.txt
```

> 说明：计划中的“115 个测试”是 v1.2 编写时的基线；`integration/v1.2` 已合入 A/D
> 模块，当前全量基线为 200 passed，因此以实际执行的 265 passed 为准。
