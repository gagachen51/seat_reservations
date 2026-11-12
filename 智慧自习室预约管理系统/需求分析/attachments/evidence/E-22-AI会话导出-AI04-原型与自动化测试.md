# AI 会话导出 · AI-04 原型实现与自动化测试（含缺陷定位闭环）

> **证据编号 E-22**　｜　关联任务 **AI-04**　｜　对应记录 `attachments/B-01-AI关键任务交互记录.md` 的 AI-04 记录表（证据 E-14）
> 归档依据：课程要求"完整对话可放附件、正文引用片段"（B.4 节）。
> 导出说明：本文件由小组成员自客户端导出并归档为 Markdown 原文，内容未做删改；第 3~5 轮记录了「**AI 输出存在缺陷 → 成员跑测试发现 → 修正 → 回归测试**」的完整闭环（对应问题记录 **V-03**）。

| 项 | 内容 |
|---|---|
| 证据编号 | E-22 |
| 工具 / 智能体 | Reasonix 编码智能体（测试执行：Node.js v24.13.0 的 `node:test`，非 AI 工具） |
| 模型 / 版本 | deepseekv4flash |
| 会话日期 | 2026-10-13（第 1~3 轮）～ 2026-10-14（第 4~5 轮与回归） |
| 参与者 | `沈家炜`（提问、实现与测试、缺陷定位）、`陈景标`、`陈高阳`（人工独立核验与复核） |
| 会话范围 | 需求验证原型实现（`prototype/`）、`node:test` 自动化测试（T01~T46）、缺陷 V-03 / V-04 定位与修复 |
| 导入的上下文材料 | `需求规格说明书.md` v1.0 的 BR/PR/ST/FR 与数据字典、`models/src/G-04-activity-diagram.puml`、`G-06-state-machine-reservation.puml` |
| 导出方式 | 客户端「导出对话」→ Markdown（本文件为导出结果，未做删改） |
| 关联 SRS 版本 | v1.0 → v1.2（本次提交基线，证据 E-04） |

---

## 第 1 轮

**沈家炜（学生，2026-10-13 19:05）**：

> 用原生 HTML/CSS/JS 做可点击原型，不要第三方依赖。业务规则要在 `domain.js` 里真实实现，页面只做展示，不要重复实现规则。时间要能注入，方便测超时。

**Reasonix（AI，2026-10-13 19:05）**：

> 已建立 `prototype/` 骨架，职责划分如下：
>
> | 文件 | 职责 | 约束 |
> |---|---|---|
> | `domain.js` | 参数、校验、状态转换、权限、通知/审计/信用 —— **业务规则唯一实现处** | 纯函数式，时间由入参 `now` 注入，不读系统时钟 |
> | `seed.js` | 模拟数据（模拟时钟基准 `2025-10-20`，属教学模拟） | 无外部数据源 |
> | `index.html` / `styles.css` / `app.js` | 6 个功能页的展示与交互 | 不重复实现规则，只调用 `domain.js` |
> | `tests/` | 自动化测试 | 无第三方依赖（`node:test`） |

## 第 2 轮

**沈家炜（学生，2026-10-13 20:10）**：

> 写 `node:test` 自动化测试，覆盖 BR、PR、ST；超时场景要通过注入时间测试。T17 覆盖 BR-13 的凭证有效期与一次性。

**Reasonix（AI，2026-10-13 20:10）**：

> 已生成 `tests/domain.test.cjs`（T01~T44）。T17 片段：
>
> ```js
> test('T17 BR-13：签到凭证一次性、有有效期', () => {
>   const s = fresh();
>   const c = cred('2025-10-20T13:44:30');
>   const first = core.checkIn(s, {
>     actorId: ID.zhang, reservationId: 'R20251020-02',
>     now: '2025-10-20T13:45:00', credential: c
>   });
>   assert.equal(first.ok, true);
> ```
>
> 首次运行：**44/45 通过**，T17 未按预期拒绝（另有 1 条用例设计需调整）。

## 第 3 轮（本任务最关键的一次人工介入）

**沈家炜（学生，2026-10-14 08:40）**：

> T17 失败了。**先不要改测试**，判断是实现错还是测试写错。这个用例的语义是：`credentialValidSeconds = 60`，凭证签发于 13:44:30、签到时间 13:45:00，只有 30 秒，应该在有效期内；如果过期凭证还能签进去，那就是实现有问题。手动核对一遍：把阈值 60 秒换成"已过 60 秒"的输入试一次。

**Reasonix（AI，2026-10-14 08:40）**：

> 按你的要求做了手工核对，**确认是实现缺陷，不是测试写错**：
>
> ```js
> /* 修复前（AI 首版，错误） */
> if (diffMin(cred.issuedAt, now) > p.credentialValidSeconds) { ... }
> ```
>
> `diffMin` 返回的是**分钟**，而 `credentialValidSeconds` 是**秒**（60）。"分钟数 > 60"几乎恒不成立，因此**已过期凭证也能通过校验**，削弱了 BR-13 与 NFR-04（动态码应 60 秒失效）。
>
> 修复（改为按秒比较）：
>
> ```js
> if ((now.getTime() - new Date(cred.issuedAt).getTime()) / 1000 > p.credentialValidSeconds) {
>   return fail('签到凭证已过期（有效期 ' + p.credentialValidSeconds + ' 秒）', 'BR-13');
> }
> ```

## 第 4 轮

**沈家炜（学生，2026-10-14 09:10）**：

> T23 也失败了：`c.at.indexOf` 用不了。看一下是谁的问题。

**Reasonix（AI，2026-10-14 09:10）**：

> 定位为**实现缺陷（V-04）**：`notify`/`audit`/`changeCredit` 把 `Date` 对象直接写入 `at` 等时间字段，而 SRS 3.3 数据字典规定为 `'YYYY-MM-DD HH:MM'` 字符串时间戳，导致按日过滤失效、页面时间显示格式不可控。
> 修复：新增 `stamp(at, fallback)` 统一格式化，应用于 `logEvent` / `notify` / `audit` / `changeCredit`。

## 第 5 轮

**沈家炜（学生，2026-10-14 09:35）**：

> 把这两个缺陷补成回归测试，防止以后再犯，并给出编号。

**Reasonix（AI，2026-10-14 09:35）**：

> 已新增 `tests/_probe.test.cjs`（由定位缺陷的探针转为正式回归用例）：
>
> ```js
> test('T45 回归 V-03：凭证签发满 60 秒仍有效，超过 60 秒即失效（BR-13）', () => {
>   const s = seed.createSeedState();
>   const okResult = core.checkIn(s, {
>     actorId: 'S2023001', reservationId: 'R20251020-02', now: '2025-10-20T13:45:00',
>     credential: { code: 'CRED-R1', issuedAt: '2025-10-20T13:44:00' }
>   });
>   assert.equal(okResult.ok, true);
>   // 签发于签到前 61 秒 → 已过期
>   const s2 = seed.createSeedState();
>   const staleResult = core.checkIn(s2, {
>     actorId: 'S2023001', reservationId: 'R20251020-02', now: '2025-10-20T13:45:00',
>     credential: { code: 'CRED-R2', issuedAt: '2025-10-20T13:43:59' }
>   });
>   assert.equal(staleResult.ok, false);
>   assert.equal(core.findReservation(s2, 'R20251020-02').status, core.RES.PENDING, '过期凭证被拒时状态保持');
> });
> ```
>
> 两个缺陷分别登记为 **V-03**（凭证有效期单位错误）与 **V-04**（时间字段类型不一致），进入 A 报告 A.7 台账。

---

## 人工批注

**人工批注（`沈家炜`，2026-10-14）**：

> ① 第 3 轮是本组的**关键人工动作**：AI 生成的实现把"分钟"和"秒"直接比较，是**跑测试时发现的**（T17 首次失败），并由成员手工计算期望值确认"实现错、测试对"，**拒绝了"改测试迁就实现"的省事做法**；修复后补 T45/T46 回归。
> ② 为避免"AI 同时生成实现与测试、互相迎合"（同源生成风险），人工独立核验：逐条阅读 `domain.js` 实现，对 BR-02 额度、BR-06 每日奖励上限等**手工计算期望值**并与业务语义比对，不以测试通过为唯一判据。
> ③ 测试规模为 **62 条全绿**（领域逻辑与回归 46 条：T01~T46；页面走查 16 条：U1~U16，见 E-23 与 E-TEST-02）。

**人工批注（`陈高阳`，2026-10-14）**：

> 复核 V-03：签发满 60 秒仍可签到、61 秒被拒且提示"已过期"、状态保持"待签到"；已使用凭证再次签到被拒。复核 V-04：信用记录/通知/审计日志时间均为 `YYYY-MM-DD HH:MM`。两项均通过。

## 本轮产出的可核验产物

| 产物 | 位置 | 说明 |
|---|---|---|
| 原型（6 个功能页 / 零依赖 / 可离线运行） | `prototype/`（`index.html`、`styles.css`、`app.js`、`domain.js`、`seed.js`、`README.md`） | 证据 E-07 |
| 领域逻辑测试与回归测试 | `prototype/tests/domain.test.cjs`（T01~T44）、`prototype/tests/_probe.test.cjs`（T45~T46） | 证据 E-08（`E-TEST-01`） |
| 缺陷 V-03 / V-04 修复 | `prototype/domain.js` 的 `checkIn`、`stamp()` | 见 `attachments/A-06-问题处理与复核记录.md`（证据 E-13） |
