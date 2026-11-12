/**
 * 测试固定时钟（唯一来源）
 * =========================
 * 目的：种子数据、领域测试、页面测试、回归测试共用同一个基准时间，
 *       避免测试依赖运行当天的真实日期。
 * 说明：只有本文件允许出现固定日期字面量；其余测试文件通过 D / NEXT_DATE / at() 派生。
 */
const seed = require('../../seed.js');

const FIXED_NOW = '2025-10-20T08:00:00';

function pad2(n) { return n < 10 ? '0' + n : '' + n; }

function addDays(dateStr, deltaDays) {
  const d = new Date(dateStr + 'T00:00:00');
  d.setDate(d.getDate() + (deltaDays || 0));
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

const FIXED_DATE = FIXED_NOW.slice(0, 10);
const NEXT_DATE = addDays(FIXED_DATE, 1);

// 生成某天的完整时刻字符串，例如 at('13:45') => 2025-10-20T13:45:00
function at(hhmm, date) {
  return (date || FIXED_DATE) + 'T' + hhmm + ':00';
}

// 保留秒的 ISO 时刻，例如 atTime('13:43:30') => 2025-10-20T13:43:30
function atTime(hhmmss, date) {
  return (date || FIXED_DATE) + 'T' + hhmmss;
}

// 生成数据字典使用的空格时间戳，例如 stamp('13:45') => '2025-10-20 13:45'
function stamp(hhmm, date) {
  return (date || FIXED_DATE) + ' ' + hhmm;
}

// 从种子状态中按用户/状态查找预约 ID，避免硬编码 RYYYYMMDD-NN
function reservationId(state, userId, status) {
  const found = state.reservations.find(function (r) {
    return r.userId === userId && (!status || r.status === status);
  });
  if (!found) {
    throw new Error('未找到预约：userId=' + userId + ', status=' + status);
  }
  return found.id;
}

function seedState(now) {
  return seed.createSeedState(now || FIXED_NOW);
}

module.exports = {
  FIXED_NOW: FIXED_NOW,
  FIXED_DATE: FIXED_DATE,
  NEXT_DATE: NEXT_DATE,
  addDays: addDays,
  at: at,
  atTime: atTime,
  stamp: stamp,
  reservationId: reservationId,
  seedState: seedState
};
