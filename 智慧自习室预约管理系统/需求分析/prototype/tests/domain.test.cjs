/**
 * 智慧自习室预约管理系统 —— 领域逻辑自动化测试（Node 内置测试运行器）
 * 运行方式（prototype 目录下）：node --test tests
 * 说明：这些测试针对原型中"已实现"的业务规则与状态转换（BR/ST/PR/FR、NFR-03），
 *       作为 A 报告 A.6 节"功能验证"的证据（PV 记录）；页面走查部分另见 E-04 证据（页面截图）。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../domain.js');
const seed = require('../seed.js');
const { FIXED_NOW, FIXED_DATE: D, NEXT_DATE, addDays, at, atTime, stamp, reservationId } = require('./support/_fixtures.cjs');

const ID = { zhang: 'S2023001', hua: 'S2023002', wang: 'S2023003', admin: 'A9001', sys: 'Y0001', teacher: 'T7001' };
const BASE = seed.createSeedState(FIXED_NOW);
const ZHANG_PENDING = reservationId(BASE, ID.zhang, core.RES.PENDING);
const WANG_IN_USE = reservationId(BASE, ID.wang, core.RES.IN_USE);

let credSeq = 0;
function cred(issuedAt) { credSeq += 1; return { code: 'CRED-T' + credSeq, issuedAt: issuedAt }; }
function fresh() { credSeq = 0; return seed.createSeedState(FIXED_NOW); }
function res(state, id) { return core.findReservation(state, id); }
function user(state, id) { return core.findUser(state, id); }
function violationsOf(state, id) { return core.listViolations(state, id); }

/* ---------------------------------------------------------------- *
 * BR-01 / BR-02 / BR-03 / BR-11：创建预约的规则校验
 * ---------------------------------------------------------------- */

test('T01 正常主流程：学生在开放时段内预约成功，状态为待签到（SC-01 关联 ST-01 前置）', () => {
  const s = fresh();
  const r = core.createReservation(s, {
    actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '17:00', now: at('08:00')
  });
  assert.equal(r.ok, true);
  assert.equal(r.reservation.status, core.RES.PENDING);
  assert.equal(r.reservation.seatId, 'A101-005');
  // 座位在该时段被占用（BR-11）
  assert.equal(core.seatStatusAt(s, core.findSeat(s, 'A101-005'), D, '16:00', '17:00'), core.SEAT.RESERVED);
  // BR-16：预约成功通知已发送
  assert.ok(s.notifications.some(n => n.type === 'reserve_ok' && n.userId === ID.zhang));
});

test('T02 BR-01：单次预约时长低于 30 分钟或超过 4 小时均被拒绝', () => {
  const s = fresh();
  const base005 = s.reservations.filter(r => r.seatId === 'A101-005').length;
  const short = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '16:15', now: at('08:00') });
  assert.equal(short.ok, false);
  assert.equal(short.rule, 'BR-01');
  const long = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '21:00', now: at('08:00') });
  assert.equal(long.ok, false);
  assert.equal(long.rule, 'BR-01');
  assert.equal(s.reservations.filter(r => r.seatId === 'A101-005').length, base005, '被拒绝时不产生预约记录');
});

test('T03 BR-01：预约时长必须为 30 分钟整数倍', () => {
  const s = fresh();
  const r = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '16:45', now: at('08:00') });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'BR-01');
});

test('T04 BR-02：每人每天最多 2 个时段，第 3 次被拒绝且状态保持', () => {
  const s = fresh();
  const r1 = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '17:00', now: at('08:00') });
  assert.equal(r1.ok, true);
  const before = s.reservations.length;
  const r2 = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-006', date: D, start: '18:00', end: '19:00', now: at('08:00') });
  assert.equal(r2.ok, false);
  assert.equal(r2.rule, 'BR-02');
  assert.equal(s.reservations.length, before, '被拒绝时不产生新的预约记录（ST-13 创建失败，无对象可保持）');
});

test('T05 BR-02/BR-10：取消预约后释放当日额度，可再次预约', () => {
  const s = fresh();
  const r1 = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '17:00', now: at('08:00') });
  const c = core.cancelReservation(s, { actorId: ID.zhang, reservationId: r1.reservation.id, now: at('08:05') });
  assert.equal(c.ok, true);
  assert.equal(c.outcome, 'cancelled_free');
  const r2 = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-006', date: D, start: '18:00', end: '19:00', now: at('08:10') });
  assert.equal(r2.ok, true);
});

test('T06 BR-11：座位时段已被占用时拒绝，并给出候补建议（SC-07）', () => {
  const s = fresh();
  const r = core.createReservation(s, { actorId: ID.zhang, seatId: 'B201-002', date: D, start: '09:30', end: '10:30', now: at('08:00') });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'BR-11');
  assert.equal(r.suggestWaitlist, true);
  assert.equal(r.conflictReservationId, WANG_IN_USE);
});

test('T07 BR-11：本人同一时段重复预约被拒绝', () => {
  const s = fresh();
  const r = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-004', date: D, start: '14:30', end: '15:30', now: at('08:00') });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'BR-11');
});

test('T08 BR-11：相邻时段（14:00-15:00 与 15:00-16:00）不算重叠，允许预约', () => {
  const s = fresh();
  const r = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-004', date: D, start: '15:00', end: '16:00', now: at('08:00') });
  assert.equal(r.ok, true);
});

test('T09 BR-03：超出 7 天窗口或选择过去日期被拒绝', () => {
  const s = fresh();
  const far = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: addDays(D, 8), start: '16:00', end: '17:00', now: at('08:00') });
  assert.equal(far.ok, false);
  assert.equal(far.rule, 'BR-03');
  const past = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: addDays(D, -1), start: '16:00', end: '17:00', now: at('08:00') });
  assert.equal(past.ok, false);
  assert.equal(past.rule, 'BR-03');
  const outsideOpen = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '22:30', end: '05:00', now: at('08:00') });
  assert.equal(outsideOpen.ok, false);
});

test('T10 BR-07：信用分低于 60 分禁止创建预约（李华 55 分，SC-09）', () => {
  const s = fresh();
  const r = core.createReservation(s, { actorId: ID.hua, seatId: 'B201-003', date: D, start: '16:00', end: '17:00', now: at('08:00') });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'BR-07');
  assert.equal(user(s, ID.hua).credit, 55);
});

test('T11 PR-02：管理员/教师等非学生角色不能创建预约', () => {
  const s = fresh();
  [ID.admin, ID.sys, ID.teacher].forEach(actorId => {
    const r = core.createReservation(s, { actorId, seatId: 'A101-005', date: D, start: '16:00', end: '17:00', now: at('08:00') });
    assert.equal(r.ok, false);
    assert.equal(r.rule, 'PR-02');
  });
});

test('T12 BR-09：维护中的座位不可预约', () => {
  const s = fresh();
  const r = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-009', date: D, start: '16:00', end: '17:00', now: at('08:00') });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'BR-09');
});

/* ---------------------------------------------------------------- *
 * BR-04 / BR-13 / ST-01 / ST-02：签到
 * ---------------------------------------------------------------- */

test('T13 BR-04：签到窗口开始前签到被拒绝，预约状态保持「待签到」', () => {
  const s = fresh();
  const r = core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:44'), credential: cred(atTime('13:43:30')) });
  assert.equal(r.ok, false);
  assert.equal(r.outcome, 'state_kept');
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.PENDING);
});

test('T14 BR-04/ST-01：窗口下界（开始前 15 分钟整）签到成功，状态转为使用中', () => {
  const s = fresh();
  const r = core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: cred(atTime('13:44:30')) });
  assert.equal(r.ok, true);
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.IN_USE);
  assert.equal(res(s, ZHANG_PENDING).checkInAt, stamp('13:45'));
  assert.equal(core.seatStatusAt(s, core.findSeat(s, 'A101-003'), D, '14:00', '15:00'), core.SEAT.OCCUPIED);
});

test('T15 BR-04/ST-02：窗口上界（开始后 15 分钟整）签到成功；超过 1 分钟判违约并扣 10 分（SC-05）', () => {
  // 上界内：王芳先结束 09:00-10:00 的使用，再预约 16:00-17:00 并于 16:15 签到
  const s = fresh();
  const out = core.checkOut(s, { actorId: ID.wang, reservationId: WANG_IN_USE, now: at('09:50') });
  assert.equal(out.ok, true);
  const created = core.createReservation(s, { actorId: ID.wang, seatId: 'B201-003', date: D, start: '16:00', end: '17:00', now: at('10:00') });
  assert.equal(created.ok, true);
  const inTime = core.checkIn(s, { actorId: ID.wang, reservationId: created.reservation.id, now: at('16:15'), credential: cred(atTime('16:14:30')) });
  assert.equal(inTime.ok, true);
  assert.equal(res(s, created.reservation.id).status, core.RES.IN_USE);
});

test('T16 BR-04/ST-02：超过签到窗口未签到 → 违约（扣 10 分、座位释放、产生通知）', () => {
  const s = fresh();
  const before = violationsOf(s, ID.wang).length;
  const out = core.checkOut(s, { actorId: ID.wang, reservationId: WANG_IN_USE, now: at('09:50') });
  assert.equal(out.ok, true);
  const created = core.createReservation(s, { actorId: ID.wang, seatId: 'B201-003', date: D, start: '16:00', end: '17:00', now: at('10:00') });
  const late = core.checkIn(s, { actorId: ID.wang, reservationId: created.reservation.id, now: at('16:16'), credential: cred(atTime('16:15:30')) });
  assert.equal(late.ok, false);
  assert.equal(late.outcome, 'no_show');
  assert.equal(res(s, created.reservation.id).status, core.RES.NO_SHOW);
  assert.equal(violationsOf(s, ID.wang).length, before + 1);
  assert.equal(user(s, ID.wang).credit, 90);
  assert.equal(core.seatStatusAt(s, core.findSeat(s, 'B201-003'), D, '16:00', '17:00'), core.SEAT.AVAILABLE);
});

test('T17 BR-13：签到凭证一次性、有有效期', () => {
  const s = fresh();
  const c = cred(atTime('13:44:30'));
  const first = core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: c });
  assert.equal(first.ok, true);
  // 王芳创建当天第二个时段后，复用张小明刚使用过的凭证签到 → 凭证已被使用，应被拒绝
  const createdForReuse = core.createReservation(s, { actorId: ID.wang, seatId: 'B201-003', date: D, start: '16:00', end: '17:00', now: at('10:00') });
  assert.equal(createdForReuse.ok, true);
  const reuse = core.checkIn(s, { actorId: ID.wang, reservationId: createdForReuse.reservation.id, now: at('16:00'), credential: c });
  assert.equal(reuse.ok, false);
  assert.equal(reuse.rule, 'BR-13');

  const s2 = fresh();
  const expired = core.checkIn(s2, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: cred(at('13:43')) });
  assert.equal(expired.ok, false);
  assert.equal(expired.rule, 'BR-13');
});

test('T18 BR-13：同一账号已有「使用中」预约时不能再签到其他座位', () => {
  const s = fresh();
  // 张小明先签到自己 14:00-15:00 的预约，进入「使用中」
  core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: cred(atTime('13:44:30')) });
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.IN_USE);
  // 再创建当天第二个时段（BR-02 允许 2 个时段）并尝试签到
  const created = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '17:00', now: at('08:00') });
  assert.equal(created.ok, true);
  const dup = core.checkIn(s, { actorId: ID.zhang, reservationId: created.reservation.id, now: at('16:00'), credential: cred(atTime('15:59:30')) });
  assert.equal(dup.ok, false);
  assert.equal(dup.rule, 'BR-13');
  assert.equal(res(s, created.reservation.id).status, core.RES.PENDING, '第二条预约状态保持待签到');
});

test('T19 PR-03：不能为他人的预约签到', () => {
  const s = fresh();
  const r = core.checkIn(s, { actorId: ID.zhang, reservationId: WANG_IN_USE, now: at('09:00'), credential: cred(atTime('08:59:30')) });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'PR-02');
});

/* ---------------------------------------------------------------- *
 * BR-05 / ST-05 ~ ST-09：暂离、返座、签退、超时任务
 * ---------------------------------------------------------------- */

test('T20 BR-05/ST-05/ST-06：暂离后 30 分钟内返座可继续使用（SC-06）', () => {
  const s = fresh();
  const away = core.startAway(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45') });
  assert.equal(away.ok, false, '未签到的预约不能发起暂离');
  core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: cred(atTime('13:44:30')) });
  const away2 = core.startAway(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('14:10') });
  assert.equal(away2.ok, true);
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.AWAY);
  assert.equal(res(s, ZHANG_PENDING).awayDeadlineAt, stamp('14:40'));
  assert.equal(core.seatStatusAt(s, core.findSeat(s, 'A101-003'), D, '14:00', '15:00'), core.SEAT.HELD);
  const back = core.resumeUse(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('14:35') });
  assert.equal(back.ok, true);
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.IN_USE);
});

test('T21 BR-05/ST-07：暂离超过 30 分钟未返回 → 座位释放并扣 5 分', () => {
  const s = fresh();
  core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: cred(atTime('13:44:30')) });
  core.startAway(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('14:10') });
  const sweep = core.runTimeoutSweep(s, at('14:41'));
  assert.equal(sweep.ok, true);
  assert.ok(sweep.actions.some(a => a.type === 'away_timeout'));
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.RELEASED);
  assert.equal(user(s, ID.zhang).credit, 95);
  assert.equal(core.seatStatusAt(s, core.findSeat(s, 'A101-003'), D, '14:00', '15:00'), core.SEAT.AVAILABLE);
  // 释放后其他学生可以预约该座位
  const r = core.createReservation(s, { actorId: ID.wang, seatId: 'A101-003', date: D, start: '14:00', end: '15:00', now: at('14:45') });
  assert.equal(r.ok, true);
});

test('T22 BR-06/ST-08：按时签退 → 已完成并记录信用奖励', () => {
  const s = fresh();
  core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: cred(atTime('13:44:30')) });
  const out = core.checkOut(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('14:50') });
  assert.equal(out.ok, true);
  assert.equal(out.outcome, 'completed');
  assert.equal(out.rewarded, true);
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.COMPLETED);
  assert.equal(res(s, ZHANG_PENDING).endReason, 'normal');
  assert.equal(s.creditLogs.filter(c => c.reason === '按时签到并签退' && c.userId === ID.zhang).length, 1);
});

test('T23 BR-06：每人每日信用奖励上限 2 次，跨日重新计数', () => {
  const s = fresh();
  // 当天第 1 次：张小明 14:00-15:00 按时签退（+1）
  core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: cred(atTime('13:44:30')) });
  const out1 = core.checkOut(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('14:50') });
  assert.equal(out1.rewarded, true);
  // 当天第 2 次：再约一个时段并按时签退（+1，达到每人每日上限 2）
  const r2 = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '15:00', end: '16:00', now: at('14:55') });
  assert.equal(r2.ok, true);
  core.checkIn(s, { actorId: ID.zhang, reservationId: r2.reservation.id, now: at('15:00'), credential: cred(atTime('14:59:30')) });
  const out2 = core.checkOut(s, { actorId: ID.zhang, reservationId: r2.reservation.id, now: at('15:50') });
  assert.equal(out2.rewarded, true);
  assert.equal(s.creditLogs.filter(cl => cl.reason === '按时签到并签退' && cl.userId === ID.zhang && String(cl.at).indexOf(D) === 0).length, 2);
  // 跨日：次日的按时签退重新计数并再次奖励
  const r3 = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: NEXT_DATE, start: '09:00', end: '10:00', now: at('17:00') });
  assert.equal(r3.ok, true);
  core.checkIn(s, { actorId: ID.zhang, reservationId: r3.reservation.id, now: at('09:00', NEXT_DATE), credential: cred(atTime('08:59:30', NEXT_DATE)) });
  const out3 = core.checkOut(s, { actorId: ID.zhang, reservationId: r3.reservation.id, now: at('09:50', NEXT_DATE) });
  assert.equal(out3.rewarded, true);
});

test('T24 BR-06/ST-09：到点未签退 → 定时任务判定超时完成并扣 5 分', () => {
  const s = fresh();
  core.checkIn(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:45'), credential: cred(atTime('13:44:30')) });
  const sweep = core.runTimeoutSweep(s, at('15:01'));
  assert.ok(sweep.actions.some(a => a.type === 'overstay'));
  const r = res(s, ZHANG_PENDING);
  assert.equal(r.status, core.RES.COMPLETED);
  assert.equal(r.endReason, 'overstay');
  assert.equal(user(s, ID.zhang).credit, 95);
});

test('T25 NFR-02/AC-15：定时任务重复执行幂等，不重复扣分', () => {
  const s = fresh();
  const baseVio = violationsOf(s, ID.wang).length;
  const first = core.runTimeoutSweep(s, at('10:05'));   // 王芳 09:00-10:00 到点未签退
  assert.equal(first.count, 1);
  const creditAfterFirst = user(s, ID.wang).credit;
  const second = core.runTimeoutSweep(s, at('10:06'));
  assert.equal(second.count, 0);
  assert.equal(user(s, ID.wang).credit, creditAfterFirst);
  assert.equal(violationsOf(s, ID.wang).length, baseVio + 1);
});

/* ---------------------------------------------------------------- *
 * BR-10 / ST-03 / ST-04：取消预约
 * ---------------------------------------------------------------- */

test('T26 BR-10/ST-03/ST-04：提前取消不计违规，临近取消扣 5 分（SC-07）', () => {
  const s = fresh();
  const free = core.cancelReservation(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:10') });
  assert.equal(free.ok, true);
  assert.equal(free.outcome, 'cancelled_free');
  assert.equal(free.penalty, 0);
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.CANCELLED);

  const s2 = fresh();
  const late = core.cancelReservation(s2, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('13:50') });
  assert.equal(late.ok, true);
  assert.equal(late.outcome, 'cancelled_late');
  assert.equal(late.penalty, 5);
  assert.equal(user(s2, ID.zhang).credit, 95);
  assert.equal(res(s2, ZHANG_PENDING).endReason, 'cancel_late');
});

test('T27 BR-10：预约时段开始后不可自助取消', () => {
  const s = fresh();
  const r = core.cancelReservation(s, { actorId: ID.zhang, reservationId: ZHANG_PENDING, now: at('14:10') });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'BR-10');
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.PENDING, '状态保持');
});

test('T28 PR-03：不能取消他人预约', () => {
  const s = fresh();
  const r = core.cancelReservation(s, { actorId: ID.zhang, reservationId: WANG_IN_USE, now: at('08:00') });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'PR-03');
  assert.equal(res(s, WANG_IN_USE).status, core.RES.IN_USE);
});

/* ---------------------------------------------------------------- *
 * BR-08：候补队列
 * ---------------------------------------------------------------- */

test('T29 BR-08：候补登记 → 座位释放后通知 → 时限内确认生成预约（SC-02）', () => {
  const s = fresh();
  const join = core.joinWaitlist(s, { actorId: ID.zhang, seatId: 'B201-002', date: D, start: '09:00', end: '10:00', now: at('08:10') });
  assert.equal(join.ok, true);
  assert.equal(join.entry.seq, 1);

  // 管理员强制释放该座位（UC-13 / ST-10）触发候补补位
  const rel = core.forceRelease(s, { actorId: ID.admin, reservationId: WANG_IN_USE, reason: '实测座位无人使用（走查场景）', now: at('09:20') });
  assert.equal(rel.ok, true);
  const entry = s.waitlist.find(w => w.userId === ID.zhang);
  assert.equal(entry.status, 'notified');
  assert.equal(entry.confirmDeadline, stamp('09:25'));
  assert.ok(s.notifications.some(n => n.type === 'waitlist' && n.userId === ID.zhang));

  const confirm = core.confirmWaitlist(s, { actorId: ID.zhang, waitlistId: entry.id, now: at('09:22') });
  assert.equal(confirm.ok, true);
  assert.equal(entry.status, 'confirmed');
  assert.equal(confirm.reservation.seatId, 'B201-002');
  assert.equal(confirm.reservation.source, 'waitlist');
});

test('T30 BR-08：候补确认超时 → 名额顺延给下一位候补者', () => {
  const s = fresh();
  // 系统管理员将"一次通知候补人数"改为 1（BR-08 参数化，仅影响新流程）
  const param = core.setParam(s, { actorId: ID.sys, code: 'waitlistNotifyCount', value: 1, now: at('08:00') });
  assert.equal(param.ok, true);
  core.joinWaitlist(s, { actorId: ID.zhang, seatId: 'B201-002', date: D, start: '09:00', end: '10:00', now: at('08:10') });
  core.joinWaitlist(s, { actorId: ID.wang, seatId: 'B201-002', date: D, start: '09:00', end: '10:00', now: at('08:11') });
  core.forceRelease(s, { actorId: ID.admin, reservationId: WANG_IN_USE, reason: '走查：释放后验证候补顺延', now: at('09:20') });
  const zhangEntry = s.waitlist.find(w => w.userId === ID.zhang);
  const wangEntry = s.waitlist.find(w => w.userId === ID.wang);
  assert.equal(zhangEntry.status, 'notified', '只通知队首 1 位');
  assert.equal(wangEntry.status, 'queued');
  const sweep = core.runTimeoutSweep(s, at('09:26'));
  assert.ok(sweep.actions.some(a => a.type === 'waitlist_expired'));
  assert.equal(zhangEntry.status, 'expired');
  assert.equal(wangEntry.status, 'notified', '名额顺延给下一位候补者');
  assert.equal(wangEntry.confirmDeadline, stamp('09:31'));
});

/* ---------------------------------------------------------------- *
 * BR-06 / BR-15 / BR-07：连续违约限制与信用分恢复
 * ---------------------------------------------------------------- */

test('T31 BR-15：连续 3 次未签到 → 限制 7 天内不得创建预约', () => {
  const s = fresh();
  core.checkOut(s, { actorId: ID.wang, reservationId: WANG_IN_USE, now: at('09:50') });
  const a = core.createReservation(s, { actorId: ID.wang, seatId: 'B201-003', date: D, start: '16:00', end: '17:00', now: at('10:00') });
  assert.equal(a.ok, true);
  core.runTimeoutSweep(s, at('16:16'));                     // 第 1 次未签到
  const b = core.createReservation(s, { actorId: ID.wang, seatId: 'B201-004', date: D, start: '18:00', end: '19:00', now: at('17:00') });
  assert.equal(b.ok, true);
  core.runTimeoutSweep(s, at('18:16'));                     // 第 2 次未签到
  const c = core.createReservation(s, { actorId: ID.wang, seatId: 'B201-005', date: NEXT_DATE, start: '09:00', end: '10:00', now: at('19:00') });
  assert.equal(c.ok, true);
  core.runTimeoutSweep(s, at('09:16', NEXT_DATE));                     // 第 3 次未签到 → 触发限制
  assert.equal(user(s, ID.wang).consecutiveNoShow, 3);
  assert.ok(user(s, ID.wang).limit, '应产生限制');
  assert.equal(user(s, ID.wang).limit.type, 'no_show');
  assert.equal(user(s, ID.wang).limit.until, stamp('09:16', addDays(D, 8)));
  const blocked = core.createReservation(s, { actorId: ID.wang, seatId: 'B201-006', date: addDays(D, 2), start: '09:00', end: '10:00', now: at('10:00', NEXT_DATE) });
  assert.equal(blocked.ok, false);
  assert.equal(blocked.rule, 'BR-15');
});

test('T32 BR-06/BR-07：申诉成立后信用分恢复并可重新预约', () => {
  const s = fresh();
  // 李华对 2025-10-01 的未签到违规提出申诉（第 2 日，在 3 日窗口内）
  const appeal = core.submitAppeal(s, { actorId: ID.hua, violationId: 'VIO-S1', now: at('10:00', addDays(D, -18)), reason: '当天系统故障无法扫码签到，附现场照片' });
  assert.equal(appeal.ok, true);
  assert.equal(appeal.violation.status, 'appealing');
  // 非管理员审核被拒绝（PR-10）
  const deny = core.reviewAppeal(s, { actorId: ID.zhang, violationId: 'VIO-S1', decision: 'approve', now: at('11:00', addDays(D, -18)) });
  assert.equal(deny.ok, false);
  assert.equal(deny.rule, 'PR-10');
  // 管理员审核成立（UC-14 / BR-17）
  const review = core.reviewAppeal(s, { actorId: ID.admin, violationId: 'VIO-S1', decision: 'approve', note: '核查系统日志确认当日扫码服务异常，申诉成立', now: at('11:30', addDays(D, -18)) });
  assert.equal(review.ok, true);
  assert.equal(review.violation.status, 'revoked');
  assert.equal(user(s, ID.hua).credit, 65);
  assert.equal(user(s, ID.hua).consecutiveNoShow, 2);
  assert.equal(user(s, ID.hua).limit, null);
  const rebook = core.createReservation(s, { actorId: ID.hua, seatId: 'B201-003', date: addDays(D, -17), start: '09:00', end: '10:00', now: at('12:00', addDays(D, -18)) });
  assert.equal(rebook.ok, true);
});

test('T33 BR-17：超过 3 日的申诉被拒绝', () => {
  const s = fresh();
  const r = core.submitAppeal(s, { actorId: ID.hua, violationId: 'VIO-S1', now: at('10:00', addDays(D, -10)), reason: '过晚申诉' });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'BR-17');
});

/* ---------------------------------------------------------------- *
 * BR-09 / ST-11 / ST-10 / PR-05 ~ PR-07：管理员操作与权限
 * ---------------------------------------------------------------- */

test('T34 BR-09/ST-11：封禁座位后受影响预约自动取消且不记违规（SC-08）', () => {
  const s = fresh();
  const creditBefore = user(s, ID.wang).credit;
  const vBefore = violationsOf(s, ID.wang).length;
  const r = core.blockSeat(s, { actorId: ID.admin, seatId: 'B201-002', reason: '座椅气压杆故障，需维修', from: D, to: D, now: at('09:30') });
  assert.equal(r.ok, true);
  assert.deepEqual(r.affectedReservations, [WANG_IN_USE]);
  assert.equal(res(s, WANG_IN_USE).status, core.RES.CANCELLED);
  assert.equal(res(s, WANG_IN_USE).endReason, 'seat_blocked');
  assert.equal(user(s, ID.wang).credit, creditBefore, '封禁导致取消不扣信用分');
  assert.equal(violationsOf(s, ID.wang).length, vBefore, '不产生违规记录');
  assert.ok(s.notifications.some(n => n.type === 'seat_blocked' && n.userId === ID.wang));
  assert.equal(core.findSeat(s, 'B201-002').status, core.SEAT.MAINTENANCE);
  // 封禁后该座位不可预约
  const blocked = core.createReservation(s, { actorId: ID.zhang, seatId: 'B201-002', date: D, start: '14:00', end: '15:00', now: at('09:31') });
  assert.equal(blocked.ok, false);
  assert.equal(blocked.rule, 'BR-09');
});

test('T35 PR-06：学生不能封禁座位，且数据不发生任何变化', () => {
  const s = fresh();
  const r = core.blockSeat(s, { actorId: ID.zhang, seatId: 'B201-002', reason: '我想占座', now: at('09:30') });
  assert.equal(r.ok, false);
  assert.equal(r.rule, 'PR-06');
  assert.equal(core.findSeat(s, 'B201-002').status, core.SEAT.AVAILABLE);
  assert.equal(res(s, WANG_IN_USE).status, core.RES.IN_USE);
  assert.equal(s.auditLogs.length, 0, '拒绝操作不产生审计日志');
});

test('T36 PR-07/ST-10：仅自习室管理员可强制释放且必须填写原因，操作进入审计日志（SC-12）', () => {
  const s = fresh();
  const noReason = core.forceRelease(s, { actorId: ID.admin, reservationId: WANG_IN_USE, reason: '', now: at('09:20') });
  assert.equal(noReason.ok, false);
  const asSys = core.forceRelease(s, { actorId: ID.sys, reservationId: WANG_IN_USE, reason: '信息中心调试', now: at('09:20') });
  assert.equal(asSys.ok, false);
  assert.equal(asSys.rule, 'PR-07');
  const asStudent = core.forceRelease(s, { actorId: ID.zhang, reservationId: WANG_IN_USE, reason: '我要用这个座位', now: at('09:20') });
  assert.equal(asStudent.ok, false);
  assert.equal(asStudent.rule, 'PR-07');
  assert.equal(res(s, WANG_IN_USE).status, core.RES.IN_USE, '被拒绝时状态保持');

  const good = core.forceRelease(s, { actorId: ID.admin, reservationId: WANG_IN_USE, reason: '现场核查座位无人且个人物品已清理', now: at('09:25') });
  assert.equal(good.ok, true);
  assert.equal(res(s, WANG_IN_USE).status, core.RES.RELEASED);
  assert.equal(res(s, WANG_IN_USE).endReason, 'admin_release');
  assert.ok(!s.violations.some(v => v.reservationId === WANG_IN_USE), '强制释放不产生违规记录');
  assert.ok(s.auditLogs.some(a => a.action === '强制释放座位' && a.target === WANG_IN_USE));
});

/* ---------------------------------------------------------------- *
 * FR-61 / FR-71 / BR-14 / PR-13 / PR-15：看板、参数与审计
 * ---------------------------------------------------------------- */

test('T37 FR-61：使用率看板统计当前时段各状态座位数', () => {
  const s = fresh();
  const board = core.seatBoard(s, D, '09:00', '10:00');
  assert.equal(board.length, s.seats.length);
  const util = core.utilization(s, D, '09:00', '10:00');
  assert.equal(util.occupied, 1, '王芳 09:00-10:00 使用中');
  assert.equal(util.reserved, 0, '张小明 14:00-15:00 与该时段不重叠，不计入');
  assert.equal(util.maintenance, 1, 'A101-009 维护中');
  assert.equal(util.total, s.seats.length);
});

test('T38 FR-61：不同时段看板结果不同（14:00-15:00 时段张小明为已预约）', () => {
  const s = fresh();
  const util = core.utilization(s, D, '14:00', '15:00');
  assert.equal(util.reserved, 1);
  assert.equal(util.occupied, 0);
  assert.equal(util.maintenance, 1);
  assert.ok(util.usable > 0 && util.usedRate > 0);
});

test('T39 BR-14/PR-13：仅系统管理员可改参数，且变更只影响新预约', () => {
  const s = fresh();
  const deny = core.setParam(s, { actorId: ID.admin, code: 'maxReservationMinutes', value: 60, now: at('08:00') });
  assert.equal(deny.ok, false);
  assert.equal(deny.rule, 'PR-13');
  const change = core.setParam(s, { actorId: ID.sys, code: 'maxReservationMinutes', value: 60, now: at('08:05') });
  assert.equal(change.ok, true);
  assert.equal(change.before, 240);
  assert.equal(change.after, 60);
  assert.ok(s.auditLogs.some(a => a.action === '修改系统参数' && a.target === 'maxReservationMinutes'));
  // 已有预约（张小明 14:00-15:00，60 分钟）不受影响
  assert.equal(res(s, ZHANG_PENDING).status, core.RES.PENDING);
  assert.equal(res(s, ZHANG_PENDING).end, '15:00');
  // 新预约 2 小时被拒绝
  const tooLong = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '18:00', now: at('08:10') });
  assert.equal(tooLong.ok, false);
  assert.equal(tooLong.rule, 'BR-01');
  // 新预约 1 小时可通过
  const okOne = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '17:00', now: at('08:11') });
  assert.equal(okOne.ok, true);
});

test('T40 PR-15/PR-12：审计日志仅系统管理员可查；统计报表学生无权访问', () => {
  const s = fresh();
  core.blockSeat(s, { actorId: ID.admin, seatId: 'B201-002', reason: '维护', from: D, to: D, now: at('09:30') });
  const denied = core.listAuditLogs(s, ID.admin);
  assert.equal(denied.ok, false);
  assert.equal(denied.rule, 'PR-15');
  const allowed = core.listAuditLogs(s, ID.sys);
  assert.equal(allowed.ok, true);
  assert.ok(allowed.logs.length >= 1);
  const studentStats = core.violationStats(s, ID.zhang);
  assert.equal(studentStats.ok, false);
  assert.equal(studentStats.rule, 'PR-12');
  const adminStats = core.violationStats(s, ID.admin);
  assert.equal(adminStats.ok, true);
});

test('T41 PR-01/PR-11：学生仅能查看本人预约与违规记录', () => {
  const s = fresh();
  const zhangRes = core.listReservations(s, ID.zhang);
  assert.ok(zhangRes.length > 0);
  assert.ok(zhangRes.every(r => r.userId === ID.zhang));
  const zhangVio = core.listViolations(s, ID.zhang);
  assert.equal(zhangVio.length, 0);
  const huaVio = core.listViolations(s, ID.hua);
  assert.ok(huaVio.length > 0 && huaVio.every(v => v.userId === ID.hua), '学生可见的违规记录均属本人');
  const adminVio = core.listViolations(s, ID.admin);
  assert.ok(adminVio.length >= huaVio.length && adminVio.length > 0, '管理员可查看全部（含其他学生）违规记录');
});

test('T42 NFR-03/BR-11：同一座位同一时段的并发创建只有一条成功', () => {
  const s = fresh();
  const a = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-007', date: D, start: '10:00', end: '11:00', now: at('08:00') });
  const b = core.createReservation(s, { actorId: ID.wang, seatId: 'A101-007', date: D, start: '10:00', end: '11:00', now: atTime('08:00:01') });
  assert.equal(a.ok, true);
  assert.equal(b.ok, false);
  assert.equal(b.rule, 'BR-11');
  const holders = s.reservations.filter(r => r.seatId === 'A101-007' && [core.RES.PENDING, core.RES.IN_USE, core.RES.AWAY].includes(r.status));
  assert.equal(holders.length, 1);
});

test('T43 BR-12（候选规则）：讨论间按整体预约且容量为 4~8', () => {
  const s = fresh();
  const room = core.findSeat(s, 'C301-D01');
  assert.equal(room.type, '讨论间');
  assert.ok(room.capacity >= 4 && room.capacity <= 8);
  const r = core.createReservation(s, { actorId: ID.zhang, seatId: 'C301-D01', date: D, start: '18:00', end: '20:00', now: at('08:00') });
  assert.equal(r.ok, true);
});

test('T44 端到端：预约 → 签到 → 暂离 → 返座 → 签退 全流程状态链（SC-01 + SC-06）', () => {
  const s = fresh();
  const states = [];
  const created = core.createReservation(s, { actorId: ID.zhang, seatId: 'A101-005', date: D, start: '16:00', end: '17:00', now: at('08:00') });
  states.push(res(s, created.reservation.id).status);
  core.checkIn(s, { actorId: ID.zhang, reservationId: created.reservation.id, now: at('16:00'), credential: cred(atTime('15:59:30')) });
  states.push(res(s, created.reservation.id).status);
  core.startAway(s, { actorId: ID.zhang, reservationId: created.reservation.id, now: at('16:20') });
  states.push(res(s, created.reservation.id).status);
  core.resumeUse(s, { actorId: ID.zhang, reservationId: created.reservation.id, now: at('16:40') });
  states.push(res(s, created.reservation.id).status);
  core.checkOut(s, { actorId: ID.zhang, reservationId: created.reservation.id, now: at('16:55') });
  states.push(res(s, created.reservation.id).status);
  assert.deepEqual(states, ['pending', 'in_use', 'away', 'in_use', 'completed']);
});
