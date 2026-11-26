# D 模块安全整改报告

## 1. 基本信息

| 字段 | 填写内容 |
|---|---|
| 模块 | D（运营分析与导出） |
| 负责人 | 成员 D（待负责人签字确认） |
| 协作人 | 共享层（L-06 审计事件、L-10 通知上限） |
| 分支 | `integration/v1.2` |
| 基线 commit | `2589354`（D 整改前）；最终全量基线 `be8f2de` |
| 整改 commit | `e5543115d548ab0deafecd4cdf559607c4d3c47c` |
| 报告日期 | 2026-11-26 |

## 2. 问题完成清单

| 编号 | 原始严重程度 | 整改优先级 | 状态 | 说明 |
|---|---|---|---|---|
| L-04 | Low | P2 | 已完成 | 七类导出统一 `_csv_safe` 清洗；覆盖 `= + - @`、Tab/CR/LF、NUL、BOM，正常中文/数字/日期不变 |
| L-06 | Low | P2 | 已完成 | 导出成功与拒绝均写审计：actor/kind/时间/日期过滤/行数/结果；敏感字段由共享层 `audit_event` 统一脱敏 |
| L-09 | Low | P3 | 已完成 | 单边日期窗口计算捕获 `OverflowError/ValueError`，极端日期返回 422，不再 500；7/30/90 天规则不变 |
| L-10 | Low | P3 | 已完成（D 部分） | 导出最大跨度 366 天、最大 10,000 行，超限返回 422 且不静默截断；通知 `ids` 100 条上限由共享层 `common_routes.py` 完成，D 未修改该文件 |

## 3. 修改文件清单

| 文件 | 修改内容 | 是否共享文件 | SFCR 编号/状态 |
|---|---|---|---|
| `SR/src/seat_reservation/api/routes/admin_routes.py` | `_csv_safe` 清洗规则；导出路由传 `audit=True`/`now`；拒绝导出补审计 | 共享容器，仅 D 白名单路由段与 `_csv_safe` | 不需要（D 白名单段） |
| `SR/src/seat_reservation/domain/engine.py` | 新增 `MAX_EXPORT_DAYS=366`、`MAX_EXPORT_ROWS=10000`；`export_rows(..., audit=False, now=None)`；导出跨度/行数校验；`_resolve_analytics_dates` 溢出保护（仅 D 方法段） | 共享容器，仅 D 白名单方法 | 不需要（D 白名单段） |
| `SR/src/seat_reservation/storage/export_queries.py` | 投诉/黑名单/信用流水/候补四类查询增加 `LIMIT 10001`，用于超限探测 | 否（D 独占） | 不适用 |
| `SR/src/seat_reservation/storage/repository.py` | reservations/violations/audit 三类导出 `page_size` 由 10000 改为 10001，多取 1 行供 engine 判超限 | 共享容器，仅 `export_rows` | 不需要（D 白名单方法） |
| `tests/test_analytics_security.py` | 新增 25 条 D-SEC-001～025 安全整改测试 | 否 | 不适用 |

> 说明：`e554311` 同一提交中还包含共享层/其他模块的配套改动（`common_routes.py` 通知上限与健康检查、`app.py`/`deps.py` 强制改密、`migrations.py`、`app.js` 注册表等），不属于 D 的交付文件，D 未修改 `app.py`、`deps.py`、`auth.py`、`common_routes.py`、`migrations.py`、`conftest.py`、`helpers.py` 等禁止文件。

## 4. 接口与设计变更说明

| 类型 | 接口/函数/表 | 变更前 | 变更后 | 兼容性/确认人 |
|---|---|---|---|---|
| API 行为 | `GET /api/admin/export/{kind}.csv` | 无导出跨度/行数上限；超 10000 行静默截断 | 跨度上限 366 天、行数上限 10,000；超限返回 422 `VALIDATION_ERROR`，不截断 | 现有 7 类导出列/格式不变；无需前端改造 |
| API 行为 | `GET /api/admin/analytics` | 极端单边日期可能 `OverflowError` → 500 | 返回 422；7/30/90 天、单边对称补 7 天、90 天上限、from>to 拒绝均不变 | 兼容原接口契约 |
| 跨模块 helper 签名 | `DomainEngine.export_rows(actor, kind, *, date_from, date_to, audit=False, now=None)` | 无 `audit`/`now` 参数 | 新增两个可选关键字参数，默认值保持旧调用行为 | 向后兼容；唯一正式调用方 `admin_routes.export_csv` 已传 `audit=True` |
| 审计事件 | `export_csv` | 无导出审计 | `action=export_csv`, `target_type=export`, `target_id=<kind>`, `result=success/denied`；details 含 `kind/date_from/date_to`，成功另含 `rows`，拒绝另含 `error/status` | 复用共享层 `audit_event`；不改变审计表结构 |
| 数据库迁移 | — | — | D 未新增/修改 schema 或迁移 | 不适用 |
| 通知上限 | `POST /api/notifications/read` | `ids` 无长度上限 | 单次最多 100 条，超限 422 | 由共享层完成；D 未修改该文件 |

### 4.1 导出上限参数与策略

- `MAX_EXPORT_DAYS = 366`：导出日期跨度上限，独立于分析接口的 90 天规则。
- `MAX_EXPORT_ROWS = 10_000`：导出行数上限。
- 仓库层 3 类分页导出改为取 `page_size=10001`，4 类 SQL 导出增加 `LIMIT 10001`，均只用于探测是否超 10,000 行；engine 判断 `len(rows) > 10_000` 后抛 `ValidationFailed`，HTTP 统一为 422，**不静默截断**。

### 4.2 `_csv_safe` 清洗规则

1. 非字符串原样返回；
2. 字符串先去除 NUL（`\x00`）与 BOM（`\ufeff`）；
3. 对去掉前导空格/Tab/CR/LF 后的首字符判断：若为 `=`、`+`、`-`、`@`，或原字符串以 Tab/CR/LF 开头，则在清理后的文本前加单引号 `'`；
4. 正常中文、日期、数字、普通文本保持原值；换行由 `csv.writer` 负责引用；
5. 七类导出（含表头）统一经过该函数。

### 4.3 `engine.export_rows` 新增参数与审计字段

- `audit: bool = False`：为 `True` 时写成功审计，默认不写，兼容旧调用；
- `now: datetime | None = None`：测试可注入固定时间，默认当前时间；
- 成功后审计 details：`kind`、`date_from`、`date_to`、`rows`；
- 拒绝（权限/kind/跨度/行数等 `AppError`）由路由捕获后写 `result="denied"`，details：`kind`、`date_from`、`date_to`、`error`（错误码）、`status`（HTTP 状态码）；
- 审计内容不包含 CSV 全文、密码、Token、CSRF；共享层 `audit_event` 对 password/token/secret/credential/csrf/authorization/cookie 等 key 递归脱敏。

## 5. 25 条用例逐条结果对照表

统一执行命令（详见 5.1）：

```
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest tests/test_analytics_security.py -v
```

执行时间：2026-11-26 14:15:11～14:15:17（+0800）；结果：**25 passed**。

| 编号 | 测试名称 | 结果 | 实际测试函数 | 证据 |
|---|---|---|---|---|
| D-SEC-001 | CSV 清洗函数单元 | PASS | `test_d_sec_001_csv_safe_prefixes` | PASSED |
| D-SEC-002 | 分析日期解析单元 | PASS | `test_d_sec_002_resolve_analytics_dates` | PASSED |
| D-SEC-003 | 导出 kind 白名单与权限单元 | PASS | `test_d_sec_003_export_kinds_and_permissions` | PASSED |
| D-SEC-004 | 导出限制计算单元 | PASS | `test_d_sec_004_export_limits_engine` | PASSED |
| D-SEC-005 | 导出审计 payload 单元 | PASS | `test_d_sec_005_audit_event_redacts_sensitive_fields` | PASSED |
| D-SEC-006 | 分析接口角色权限 | PASS | `test_d_sec_006_analytics_permissions_api` | PASSED |
| D-SEC-007 | 日期范围 API 边界 | PASS | `test_d_sec_007_analytics_date_api` | PASSED |
| D-SEC-008 | 七类 CSV 导出 API | PASS | `test_d_sec_008_export_all_kinds_api` | PASSED |
| D-SEC-009 | CSV 恶意字段导出 | PASS | `test_d_sec_009_csv_formula_api` | PASSED |
| D-SEC-010 | 导出限制 API | PASS | `test_d_sec_010_export_limits_api` | PASSED |
| D-SEC-011 | 分析数据与预约状态一致 | PASS | `test_d_sec_011_analytics_data_consistency` | PASSED |
| D-SEC-012 | 房间筛选不跨房间 | PASS | `test_d_sec_012_room_filter_api` | PASSED |
| D-SEC-013 | 导出数据最小化 | PASS | `test_d_sec_013_export_data_minimization` | PASSED |
| D-SEC-014 | 导出审计记录 | PASS | `test_d_sec_014_export_audit_success_and_denied` | PASSED |
| D-SEC-015 | 通知 ID 限制 | PASS | `test_d_sec_015_notification_limit_api` | PASSED（共享层 100 条上限） |
| D-SEC-016 | 导出权限边界 | PASS | `test_d_sec_016_export_permission_api` | PASSED |
| D-SEC-017 | 分析/导出 SQL 注入输入 | PASS | `test_d_sec_017_analytics_and_export_injection_inputs` | PASSED |
| D-SEC-018 | CSV NUL/BOM/LF 边缘 | PASS | `test_d_sec_018_csv_nul_bom_newline_api` | PASSED |
| D-SEC-019 | 导出字段敏感信息扫描 | PASS | `test_d_sec_019_export_audit_columns` | PASSED |
| D-SEC-020 | 极端日期 HTTP 响应 | PASS | `test_d_sec_020_extreme_date_api` | PASSED |
| D-SEC-021 | 空数据/正常数据导出 | PASS | `test_d_sec_021_empty_export_api` | PASSED |
| D-SEC-022 | 大导出请求资源边界 | PASS | `test_d_sec_022_export_row_cap_api` | PASSED |
| D-SEC-023 | D+A/B/C 事件统计一致性 | PASS | `test_d_sec_023_analytics_after_cancel` | PASSED |
| D-SEC-024 | D+共享审计/认证 | PASS | `test_d_sec_024_export_audit_has_no_secrets` | PASSED |
| D-SEC-025 | D+前端导出入口 | PASS | `test_d_sec_025_frontend_export_entries` | PASSED |

### 5.1 模块测试命令

```bat
cd D:\seat_clean_baseline\智慧自习室预约管理系统\seat_reservation
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest tests/test_analytics_security.py -v
```

结果：`25 passed, 1 warning in 4.65s`（2026-11-26 14:15:11～14:15:17）。

模板中的 D 模块回归命令（分析/存储/API/管理端）已在全量回归中一并执行：

```bat
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest tests/test_analytics_storage.py tests/test_analytics_engine.py tests/test_analytics_api.py tests/test_admin.py -q
```

### 5.2 完整回归

```bat
D:\seat_shared_p0_venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

D 合入阶段结果：`380 passed, 1 skipped, 1 warning in 45.37s`（2026-11-26 14:15:20～14:16:07）。
最终全量基线（A/B/C/D + IT-01～IT-14，`be8f2de`）：**470 passed, 1 skipped in 81.59s**。
跳过项：`tests/test_e2e.py:30`，未安装 playwright（环境限制，非 D 模块失败）。

### 5.3 基线回归测试对比

| 项目 | 改前 | 改后 | 结论 |
|---|---:|---:|---|
| 完整 pytest | 355 passed, 1 skipped（基线 `2589354`） | **380 passed, 1 skipped** | 通过；+25 为 D 新增用例，无回归 |
| 前端 Node 测试 | 不适用 | 不适用 | 本模块无独立 Node 测试；前端导出入口由 D-SEC-025 静态检查覆盖 |
| 新增模块用例 | 0 / 25 | **25 / 25** | 全部执行并通过 |

## 6. 跨模块协作记录

| 协作对象 | 契约/事项 | 状态 | 证据 |
|---|---|---|---|
| 共享层（L-06） | 统一审计入口 `engine.audit_event`、敏感字段递归脱敏；D 的 `export_csv` 成功/拒绝均调用 | 已完成 | D-SEC-005、D-SEC-014、D-SEC-024 |
| 共享层（L-10） | `POST /api/notifications/read` 的 `ids` 上限 100；D 未修改 `common_routes.py` | 已完成 | D-SEC-015 |
| C 模块（分析一致性） | 预约取消/状态变化后分析 KPI 一致；未改变预约规则或接口 | 已确认 | D-SEC-011、D-SEC-023 |
| B 模块（导出审计列） | 审计导出列不含 before/after 全文，避免泄露；不改变审计表结构 | 已确认 | D-SEC-019 |

## 7. 遗留问题与风险

| 问题 | 影响 | 临时措施 | 计划 |
|---|---|---|---|
| 超大导出采用“拒绝而非流式”策略 | 超过 10,000 行/366 天的导出不可直接下载 | 返回 422 并提示缩小范围；现有业务量下可接受 | 如后续确需批量导出，再评估流式响应/分页，不在本次扩展 |
| `_csv_safe` 仅处理 ASCII 前导空白与 NUL/BOM | 极端 Unicode 空白前缀未被主动剥离 | 公式前缀探测同时使用 `lstrip(" \t\r\n")` 并覆盖指定危险字符；正常业务字段无此形态 | 保持现有策略，出现新前缀再扩展测试 |
| 审计脱敏基于敏感 key 片段 | 非标准命名的敏感字段可能未被脱敏 | D 导出事件只写固定字段（kind/date/rows/error/status），不含自由文本 | 后续新增模块统一走 `audit_event`，避免自建审计 payload |
| E2E 未执行 | 1 个 e2e 用例因未安装 playwright 跳过 | 不影响 API/单元/契约回归 | 由环境负责人安装 playwright 后补跑 |

## 8. 分支与集成

- [x] 基于 D 整改时 `integration/v1.2`（`e554311`，含基线 `2589354`）；最终全量基线为 `be8f2de`（含 A/B/C/D 与 IT-01～IT-14）。
- [x] D 未直接修改 `app.py`、`auth.py`、`deps.py`、`common_routes.py`、`migrations.py`、`conftest.py`、`helpers.py` 或 `app.js` 顶层导航/注册表。
- [x] 无合并冲突；修改集中在 D 白名单方法/路由段。
- [x] 共享层依赖（审计入口、通知上限）已在同一基线提交中完成。
- [ ] 尚未创建 PR；本报告与测试为本地交付，推送/PR 由负责人统一安排。

## 9. 负责人签字

| 角色 | 姓名 | 结论 | 日期 |
|---|---|---|---|
| 模块负责人 |  |  |  |
| 共享层复核 |  |  |  |
| 项目负责人 |  |  |  |

## 10. 附件

- 测试日志：本次 D 专项 `25 passed`；D 合入阶段全量 `380 passed, 1 skipped`；最终全量 `470 passed, 1 skipped`。
- 代码提交：`e5543115d548ab0deafecd4cdf559607c4d3c47c`（D 整改前基线 `2589354`）；最终集成分支 `be8f2de`。
- SFCR：不适用（D 未修改共享注册表；通知/审计由共享层完成）。
- 迁移说明：无 D 模块 schema/迁移变更。
