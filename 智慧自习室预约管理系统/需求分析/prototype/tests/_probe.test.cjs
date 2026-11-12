/**
 * 回归测试（由调试探针转为正式用例）
 * =====================================
 * 来历：编写原型时本文件用于定位缺陷；缺陷定位并修复后，转为正式回归测试，防止同类缺陷重现（见 A 报告 A.7 节 V-03）。
 * 缺陷 V-03：签到凭证有效期判定误将"分钟"与"秒"直接比较（60 分 > 60 秒恒不成立），
 *            导致已过期凭证仍能通过校验。修复见 domain.js 的 checkIn（改为按秒比较）。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../domain.js');
const seed = require('../seed.js');
const { FIXED_NOW, FIXED_DATE: D, at, atTime, reservationId } = require('./support/_fixtures.cjs');

test('T45 回归 V-03：凭证签发满 60 秒仍有效，超过 60 秒即失效（BR-13）', () => {
  // 签发于签到前恰好 60 秒 → 仍在有效期内
  const s = seed.createSeedState(FIXED_NOW);
  const rid = reservationId(s, 'S2023001', core.RES.PENDING);
  const okResult = core.checkIn(s, {
    actorId: 'S2023001', reservationId: rid, now: at('13:45'),
    credential: { code: 'CRED-R1', issuedAt: at('13:44') }
  });
  assert.equal(okResult.ok, true);
  assert.equal(core.findReservation(s, rid).status, core.RES.IN_USE);

  // 签发于签到前 61 秒 → 已过期
  const s2 = seed.createSeedState(FIXED_NOW);
  const rid2 = reservationId(s2, 'S2023001', core.RES.PENDING);
  const staleResult = core.checkIn(s2, {
    actorId: 'S2023001', reservationId: rid2, now: at('13:45'),
    credential: { code: 'CRED-R2', issuedAt: atTime('13:43:59') }
  });
  assert.equal(staleResult.ok, false);
  assert.equal(staleResult.rule, 'BR-13');
  assert.match(staleResult.message, /已过期/);
  assert.equal(core.findReservation(s2, rid2).status, core.RES.PENDING, '过期凭证被拒时状态保持');
});

test('T46 回归 V-03：使用过的凭证即使未过期也不能再次签到（BR-13）', () => {
  const s = seed.createSeedState(FIXED_NOW);
  const rid = reservationId(s, 'S2023001', core.RES.PENDING);
  const c = { code: 'CRED-R3', issuedAt: atTime('13:44:30') };
  const first = core.checkIn(s, {
    actorId: 'S2023001', reservationId: rid, now: at('13:45'), credential: c
  });
  assert.equal(first.ok, true);
  const created = core.createReservation(s, {
    actorId: 'S2023003', seatId: 'B201-003', date: D, start: '16:00', end: '17:00', now: at('10:00')
  });
  assert.equal(created.ok, true);
  const reuse = core.checkIn(s, {
    actorId: 'S2023003', reservationId: created.reservation.id, now: at('16:00'), credential: c
  });
  assert.equal(reuse.ok, false);
  assert.equal(reuse.rule, 'BR-13');
  assert.match(reuse.message, /已被使用/);
});
