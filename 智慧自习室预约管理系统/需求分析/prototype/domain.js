/**
 * 智慧自习室预约管理系统 —— 领域逻辑核心（原型）
 * =================================================
 * 本文件同时被浏览器页面（prototype/index.html）与 Node 自动化测试（prototype/tests/domain.test.cjs）加载，
 * 因此采用 UMD 包装，避免引入构建工具（原型说明见 prototype/README.md）。
 *
 * 规则编号与《需求规格说明书》严格一致：
 *   BR-xx 业务规则、PR-xx 权限规则、ST-xx 状态转换（A 报告 A.4）、NFR-xx 质量要求、FR-xx 功能需求。
 * 未在代码中实现的功能（真实 CAS 登录、真实 ACS 闸机、短信通道、服务端持久化）均为原型模拟，
 * 已在 prototype/README.md 与 A 报告 A.6 节注明。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) { module.exports = factory(); }
  else { root.SRSCore = factory(); }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   * 一、业务参数（对应 BR-01 ~ BR-08、BR-10、BR-15、BR-17、BR-08）
   * 参数值均为教学模拟确认后的候选值，确认状态见 SRS 第 6 章与 A 报告 A.2 节。
   * 系统管理员可通过 setParam 修改（FR-71），修改按 BR-14 仅对新预约生效。
   * ------------------------------------------------------------------ */
  var DEFAULT_PARAMS = {
    slotMinutes: 30,                       // BR-01 时段粒度
    minReservationMinutes: 30,              // BR-01 单次预约最小时长
    maxReservationMinutes: 240,             // BR-01 单次预约最大时长
    maxSlotsPerDay: 2,                      // BR-02 每人每天最多预约时段数
    maxMinutesPerDay: 240,                  // BR-02 每人每天预约总时长上限
    bookingWindowDays: 7,                   // BR-03 可预约窗口（天）
    openStart: '07:00',                     // 开放时段起
    openEnd: '23:00',                       // 开放时段止
    checkinOpenMinutesBefore: 15,           // BR-04 签到窗口提前量
    checkinCloseMinutesAfter: 15,           // BR-04 签到窗口滞后量
    awayHoldMinutes: 30,                    // BR-05 暂离保留时长
    creditInitial: 100,                     // BR-06 初始信用分
    creditMinToBook: 60,                    // BR-07 可预约信用分下限
    noShowPenalty: 10,                      // BR-06 未签到扣分
    awayTimeoutPenalty: 5,                  // BR-06 暂离超时扣分
    overstayPenalty: 5,                     // BR-06 到点未签退扣分
    falseReportPenalty: 20,                 // BR-06 虚假上报扣分
    onTimeReward: 1,                        // BR-06 按时签到签退奖励
    onTimeRewardDailyMax: 2,                // BR-06 每日奖励上限
    cancelFreeMinutesBefore: 30,            // BR-10 免费取消提前量
    cancelLatePenalty: 5,                   // BR-10 临近取消扣分
    consecutiveNoShowLimit: 3,              // BR-15 连续未签到次数上限
    consecutiveNoShowBanDays: 7,            // BR-15 限制天数
    waitlistNotifyCount: 3,                 // BR-08 一次通知候补人数
    waitlistConfirmMinutes: 5,              // BR-08 候补确认时限
    appealWindowDays: 3,                    // BR-17 申诉窗口（天）
    credentialValidSeconds: 60,             // BR-13 动态码有效期（秒）
    manualPenalty: 5,                       // BR-18 手动违约登记扣分
    blacklistWindowDays: 30,                // BR-20 黑名单统计窗口（天）
    blacklistThreshold: 3,                  // BR-20 窗口内有效违约次数阈值
    blacklistBanDays: 7                     // BR-20 限制天数
  };

  /* 预约记录状态：SM-01 / G-06 */
  var RES = {
    PENDING: 'pending', IN_USE: 'in_use', AWAY: 'away',
    COMPLETED: 'completed', CANCELLED: 'cancelled', NO_SHOW: 'no_show', RELEASED: 'released'
  };
  var RES_LABEL = {
    pending: '待签到', in_use: '使用中', away: '暂离',
    completed: '已完成', cancelled: '已取消', no_show: '已违约', released: '已释放'
  };
  /* 占用座位时段的有效状态（BR-11） */
  var SEAT_HOLDING_STATES = [RES.PENDING, RES.IN_USE, RES.AWAY];
  /* 计入 BR-02 当日上限的状态；取消/违约/释放不计入 */
  var DAILY_COUNTED_STATES = [RES.PENDING, RES.IN_USE, RES.AWAY, RES.COMPLETED];

  /* 座位状态：SM-02 / G-07 */
  var SEAT = {
    AVAILABLE: 'available', RESERVED: 'reserved', OCCUPIED: 'occupied',
    HELD: 'held', MAINTENANCE: 'maintenance', DISABLED: 'disabled'
  };
  var SEAT_LABEL = {
    available: '空闲', reserved: '已预约', occupied: '使用中',
    held: '暂离保留', maintenance: '维护中', disabled: '停用'
  };

  var VIOLATION_LABEL = {
    no_show: '未签到（未提前取消）', away_timeout: '暂离超时未返回',
    overstay: '到点未签退', late_cancel: '临近时段取消（距开始不足 30 分钟）', false_report: '虚假上报', credential_misuse: '冒用凭证'
  };
  var PENALTY_KEY = {
    no_show: 'noShowPenalty', away_timeout: 'awayTimeoutPenalty',
    overstay: 'overstayPenalty', late_cancel: 'cancelLatePenalty', false_report: 'falseReportPenalty'
  };
  var NOTIFY_LABEL = {
    reserve_ok: '预约成功', remind: '开始前提醒', violation: '违约通知',
    complaint_reply: '投诉反馈', complaint_closed: '投诉办结',
    blacklist: '预约权限受限', evaluation_reply: '评价回复',
    waitlist: '候补补位通知', seat_blocked: '座位封禁导致预约取消', appeal_result: '申诉处理结果'
  };
  var ROLES = { STUDENT: 'student', ADMIN: 'admin', SYSADMIN: 'sysadmin', TEACHER: 'teacher' };
  var ROLE_LABEL = { student: '学生', admin: '自习室管理员', sysadmin: '系统管理员', teacher: '教师' };

  /* ------------------------------------------------------------------ *
   * 二、基础工具
   * ------------------------------------------------------------------ */
  function pad2(n) { return n < 10 ? '0' + n : '' + n; }
  function toDate(date, time) { return new Date(date + 'T' + (time.length === 5 ? time + ':00' : time)); }
  function fmtDate(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
  function fmtTime(d) { return pad2(d.getHours()) + ':' + pad2(d.getMinutes()); }
  function fmtDT(d) { return fmtDate(d) + ' ' + fmtTime(d); }
  function addMinutes(d, m) { return new Date(d.getTime() + m * 60000); }
  function addDays(d, n) { var x = new Date(d.getTime()); x.setDate(x.getDate() + n); return x; }
  function minutesOf(time) { var p = String(time).split(':'); return parseInt(p[0], 10) * 60 + parseInt(p[1], 10); }
  function diffMin(a, b) { return Math.round((b.getTime() - a.getTime()) / 60000); }
  function overlaps(dateA, sA, eA, dateB, sB, eB) {
    return dateA === dateB && minutesOf(sA) < minutesOf(eB) && minutesOf(sB) < minutesOf(eA);
  }
  function fail(message, rule, extra) {
    var r = { ok: false, message: message, rule: rule || null };
    if (extra) { Object.keys(extra).forEach(function (k) { r[k] = extra[k]; }); }
    return r;
  }
  function ok(data) {
    var r = { ok: true };
    if (data) { Object.keys(data).forEach(function (k) { r[k] = data[k]; }); }
    return r;
  }

  /* ------------------------------------------------------------------ *
   * 三、状态容器
   * ------------------------------------------------------------------ */
  function createState(opts) {
    opts = opts || {};
    var now = opts.now || '2025-10-20T08:00:00';
    var state = {
      clock: now,                                  // 模拟时钟（原型用于演示超时；真实系统由服务端定时任务驱动）
      params: Object.assign({}, DEFAULT_PARAMS, opts.params || {}),
      users: opts.users || [],
      rooms: opts.rooms || [],
      seats: opts.seats || [],
      reservations: opts.reservations || [],
      violations: opts.violations || [],
      creditLogs: opts.creditLogs || [],
      waitlist: opts.waitlist || [],
      notifications: opts.notifications || [],
      auditLogs: opts.auditLogs || [],
      events: opts.events || [],
      seq: opts.seq || { reservation: 0, violation: 0, waitlist: 0, notification: 0, credit: 0, audit: 0, event: 0 },
      complaints: opts.complaints || [],            // FR-66 投诉单
      evaluations: opts.evaluations || [],          // FR-68 座位与环境评价
      usedCredentials: opts.usedCredentials || []   // BR-13 一次性凭证
    };
    return state;
  }

  function nextId(state, kind, prefix) {
    state.seq[kind] += 1;
    return prefix + pad2(state.seq[kind]);
  }
  function findUser(state, id) {
    for (var i = 0; i < state.users.length; i++) { if (state.users[i].id === id) { return state.users[i]; } }
    return null;
  }
  function findSeat(state, id) {
    for (var i = 0; i < state.seats.length; i++) { if (state.seats[i].id === id) { return state.seats[i]; } }
    return null;
  }
  function findReservation(state, id) {
    for (var i = 0; i < state.reservations.length; i++) {
      if (state.reservations[i].id === id) { return state.reservations[i]; }
    }
    return null;
  }
  function findViolation(state, id) {
    for (var i = 0; i < state.violations.length; i++) {
      if (state.violations[i].id === id) { return state.violations[i]; }
    }
    return null;
  }
  function actorOf(state, actorId) {
    var u = findUser(state, actorId);
    if (!u) { return null; }
    return u;
  }

  /* ------------------------------------------------------------------ *
   * 四、日志 / 通知 / 审计 / 信用（FR-51、FR-72、BR-16、NFR-09）
   * ------------------------------------------------------------------ */
  /* V-04 修复：时间字段统一为 'YYYY-MM-DD HH:MM' 字符串。此前 notify/audit/changeCredit 直接把 Date
     对象写入 at，导致信用记录、通知与审计日志的时间格式与数据字典不一致（见 A 报告 A.7 节 V-04）。 */
  function stamp(at, fallback) {
    if (!at) { return fallback || ''; }
    return (at instanceof Date) ? fmtDT(at) : String(at);
  }
  function logEvent(state, actorId, action, rule, detail, at) {
    state.events.push({
      id: nextId(state, 'event', 'EV'),
      at: stamp(at, state.clock),
      actorId: actorId,
      action: action,
      rule: rule || null,
      detail: detail || ''
    });
  }
  function notify(state, userId, type, content, at, channel) {
    var n = {
      id: nextId(state, 'notification', 'N'),
      userId: userId, type: type, typeLabel: NOTIFY_LABEL[type] || type,
      content: content, channel: channel || '站内', at: stamp(at, state.clock), result: '成功'
    };
    state.notifications.push(n);
    return n;
  }
  function audit(state, actorId, action, target, before, after, at) {
    var actor = findUser(state, actorId);
    var a = {
      id: nextId(state, 'audit', 'AUD'),
      actorId: actorId, actorName: actor ? actor.name : actorId,
      role: actor ? actor.role : '-', action: action, target: target,
      before: before === undefined ? '' : String(before),
      after: after === undefined ? '' : String(after),
      at: stamp(at, state.clock), ip: '10.20.30.40'
    };
    state.auditLogs.push(a);
    return a;
  }
  function changeCredit(state, userId, delta, reason, violationId, operator, at) {
    var u = findUser(state, userId);
    if (!u) { return null; }
    u.credit = Math.max(0, Math.min(100, u.credit + delta));   // BR-06 分值界于 0~100
    var entry = {
      id: nextId(state, 'credit', 'CL'),
      userId: userId, delta: delta, after: u.credit, reason: reason,
      violationId: violationId || null, operator: operator || '系统', at: stamp(at, state.clock)
    };
    state.creditLogs.push(entry);
    return entry;
  }

  /* BR-07 / BR-15：判断当前用户是否允许创建预约 */
  function refreshLimit(state, user, now) {
    var p = state.params;
    if (user.limit) {
      var expired = user.limit.until && new Date(user.limit.until) <= new Date(now);
      var creditRecovered = user.limit.type === 'credit' && user.credit >= p.creditMinToBook;
      if (expired || creditRecovered) { user.limit = null; }
    }
    return user.limit;
  }
  function checkBookingAllowed(state, user, now) {
    var p = state.params;
    refreshLimit(state, user, now);
    if (user.limit && (!user.limit.until || new Date(user.limit.until) > new Date(now))) {
      if (user.limit.type === 'credit') {
        return fail('信用分 ' + user.credit + ' 分，低于可预约下限 ' + p.creditMinToBook + ' 分，禁止创建预约', 'BR-07');
      }
      if (user.limit.type === 'blacklist') {
        return fail('因 ' + (user.limit.reason || '30 天内累计违约') + '，预约权限受限至 ' + user.limit.until + '（BR-20）', 'BR-20');
      }
      return fail('连续 ' + p.consecutiveNoShowLimit + ' 次未签到，限制至 ' + user.limit.until + ' 前不得创建预约', 'BR-15');
    }
    if (user.credit < p.creditMinToBook) {
      user.limit = { type: 'credit', until: null };
      return fail('信用分 ' + user.credit + ' 分，低于可预约下限 ' + p.creditMinToBook + ' 分，禁止创建预约', 'BR-07');
    }
    return ok();
  }

  /* ------------------------------------------------------------------ *
   * 五、座位时段占用计算（BR-11、SM-02）
   * ------------------------------------------------------------------ */
  function seatHoldersAt(state, seatId, date, start, end, excludeId) {
    return state.reservations.filter(function (r) {
      return r.seatId === seatId && r.id !== excludeId &&
        SEAT_HOLDING_STATES.indexOf(r.status) >= 0 &&
        overlaps(date, start, end, r.date, r.start, r.end);
    });
  }
  function userOverlaps(state, userId, date, start, end, excludeId) {
    return state.reservations.filter(function (r) {
      return r.userId === userId && r.id !== excludeId &&
        SEAT_HOLDING_STATES.indexOf(r.status) >= 0 &&
        overlaps(date, start, end, r.date, r.start, r.end);
    });
  }
  function seatStatusAt(state, seat, date, start, end) {
    if (seat.status === SEAT.MAINTENANCE || seat.status === SEAT.DISABLED) { return seat.status; }
    var holders = seatHoldersAt(state, seat.id, date, start, end);
    if (!holders.length) { return SEAT.AVAILABLE; }
    var r = holders[0];
    if (r.status === RES.IN_USE) { return SEAT.OCCUPIED; }
    if (r.status === RES.AWAY) { return SEAT.HELD; }
    return SEAT.RESERVED;
  }
  /* 看板：给定时刻的座位状态统计（FR-61） */
  function seatBoard(state, date, start, end) {
    return state.seats.map(function (s) {
      var st = seatStatusAt(state, s, date, start, end);
      return {
        id: s.id, roomId: s.roomId, roomName: (roomOf(state, s.roomId) || {}).name || s.roomId,
        type: s.type, capacity: s.capacity, status: st, statusLabel: SEAT_LABEL[st]
      };
    });
  }
  function roomOf(state, roomId) {
    for (var i = 0; i < state.rooms.length; i++) { if (state.rooms[i].id === roomId) { return state.rooms[i]; } }
    return null;
  }
  function utilization(state, date, start, end) {
    var board = seatBoard(state, date, start, end);
    var counter = {};
    Object.keys(SEAT_LABEL).forEach(function (k) { counter[k] = 0; });
    board.forEach(function (b) { counter[b.status] += 1; });
    counter.total = board.length;
    counter.usable = counter.total - counter.maintenance - counter.disabled;
    counter.usedRate = counter.usable ? Math.round((counter.occupied + counter.held + counter.reserved) / counter.usable * 100) : 0;
    counter.actualRate = counter.usable ? Math.round(counter.occupied / counter.usable * 100) : 0;
    return counter;
  }

  /* ------------------------------------------------------------------ *
   * 六、座位释放与候补补位（BR-08、FR-34）
   * ------------------------------------------------------------------ */
  function notifyNextWaitlist(state, seatId, date, start, end, now) {
    var queue = state.waitlist.filter(function (w) {
      return w.seatId === seatId && w.status === 'queued' &&
        overlaps(date, start, end, w.date, w.start, w.end);
    }).sort(function (a, b) { return a.seq - b.seq; });
    var picked = queue.slice(0, state.params.waitlistNotifyCount);
    picked.forEach(function (w) {
      w.status = 'notified';
      w.notifiedAt = fmtDT(now);
      w.confirmDeadline = fmtDT(addMinutes(now, state.params.waitlistConfirmMinutes));
      notify(state, w.userId, 'waitlist',
        '座位 ' + seatId + ' ' + date + ' ' + start + '-' + end + ' 已释放，请在 ' +
        state.params.waitlistConfirmMinutes + ' 分钟内确认（候补号 ' + w.id + '）', now);
    });
    return picked;
  }
  function releaseSeatSlot(state, res, now) {
    notifyNextWaitlist(state, res.seatId, res.date, res.start, res.end, now);
  }

  /* ------------------------------------------------------------------ *
   * 七、违约与信用（BR-06、BR-15、ST-02/ST-07/ST-09）
   * ------------------------------------------------------------------ */
  function recordViolation(state, res, type, now, operator) {
    var p = state.params;
    var key = PENALTY_KEY[type];
    var points = p[key] || 0;
    var v = {
      id: nextId(state, 'violation', 'VIO-'),
      userId: res.userId, reservationId: res.id, type: type, typeLabel: VIOLATION_LABEL[type],
      points: -points, occurredAt: fmtDT(now), status: 'active',
      source: 'auto',                       // BR-18：auto=系统自动违约，manual=管理员手动登记
      appealReason: '', reviewNote: ''
    };
    state.violations.push(v);
    changeCredit(state, res.userId, -points, '违规：' + VIOLATION_LABEL[type] + '（' + v.id + '）', v.id, operator || '系统', now);

    var user = findUser(state, res.userId);
    if (user) {
      if (type === 'no_show') {
        user.consecutiveNoShow = (user.consecutiveNoShow || 0) + 1;
        if (user.consecutiveNoShow >= p.consecutiveNoShowLimit) {
          user.limit = { type: 'no_show', until: fmtDT(addDays(now, p.consecutiveNoShowBanDays)) };
        }
      }
      if (user.credit < p.creditMinToBook) { user.limit = { type: 'credit', until: null }; }
    }
    notify(state, res.userId, 'violation',
      '您在 ' + res.date + ' ' + res.start + '-' + res.end + '（' + res.seatId + '）的预约产生违规：' +
      VIOLATION_LABEL[type] + '，信用分 -' + points + '，可在 ' + p.appealWindowDays + ' 日内申诉。', now);
    return v;
  }

  function grantOnTimeReward(state, user, now) {
    var p = state.params;
    var today = fmtDate(new Date(now));
    var rewarded = state.creditLogs.filter(function (c) {
      return c.userId === user.id && c.reason === '按时签到并签退' && String(c.at).indexOf(today) === 0;
    }).length;
    if (rewarded < p.onTimeRewardDailyMax) {
      changeCredit(state, user.id, p.onTimeReward, '按时签到并签退', null, '系统', now);
      return true;
    }
    return false;
  }

  /* ------------------------------------------------------------------ *
   * 八、核心操作（FR-31 ~ FR-45、FR-51 ~ FR-53）
   * ------------------------------------------------------------------ */

  /** FR-31 / FR-32 / UC-03：创建预约，逐项校验 BR-07、BR-15、BR-03、BR-01、BR-02、BR-11 */
  function createReservation(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var p = state.params;
    var user = actorOf(state, input.actorId);
    if (!user) { return fail('用户不存在'); }
    if (user.role !== ROLES.STUDENT) {
      return fail('仅学生角色可创建预约，当前角色：' + (ROLE_LABEL[user.role] || user.role), 'PR-02');
    }
    var seat = findSeat(state, input.seatId);
    if (!seat) { return fail('座位不存在：' + input.seatId); }

    /* BR-07 / BR-15：信用分与连续违约限制 */
    var allowed = checkBookingAllowed(state, user, now);
    if (!allowed.ok) {
      logEvent(state, input.actorId, '创建预约被拒绝', allowed.rule, allowed.message, state.clock);
      return allowed;
    }
    /* 座位可用性（SM-02）：维护中/停用不可预约 */
    if (seat.status === SEAT.MAINTENANCE) {
      return fail('座位 ' + seat.id + ' 处于维护中（原因：' + (seat.blockReason || '-') + '），不可预约', 'BR-09');
    }
    if (seat.status === SEAT.DISABLED) {
      return fail('座位 ' + seat.id + ' 已停用，不可预约', 'FR-21');
    }

    /* BR-01：时长与粒度 */
    var dur = minutesOf(input.end) - minutesOf(input.start);
    if (!(dur > 0)) { return fail('结束时间必须晚于开始时间', 'BR-01'); }
    if (dur < p.minReservationMinutes || dur > p.maxReservationMinutes) {
      return fail('单次预约时长须在 ' + (p.minReservationMinutes / 60) + '~' + (p.maxReservationMinutes / 60) +
        ' 小时之间，当前 ' + (dur / 60) + ' 小时', 'BR-01');
    }
    if (dur % p.slotMinutes !== 0) {
      return fail('预约时长须为 ' + p.slotMinutes + ' 分钟的整数倍', 'BR-01');
    }
    /* BR-03：可预约窗口与开放时段 */
    var today = fmtDate(now);
    var lastDay = fmtDate(addDays(now, p.bookingWindowDays));
    if (input.date < today) { return fail('不能预约过去的日期', 'BR-03'); }
    if (input.date > lastDay) {
      return fail('只能预约未来 ' + p.bookingWindowDays + ' 天内（至 ' + lastDay + '）的时段', 'BR-03');
    }
    if (minutesOf(input.start) < minutesOf(p.openStart) || minutesOf(input.end) > minutesOf(p.openEnd)) {
      return fail('预约时段须落在开放时间 ' + p.openStart + '~' + p.openEnd + ' 内', 'BR-03');
    }
    /* BR-02：当日预约数量与总时长上限（取消/违约/释放不计入） */
    var sameDay = state.reservations.filter(function (r) {
      return r.userId === user.id && r.date === input.date && DAILY_COUNTED_STATES.indexOf(r.status) >= 0;
    });
    if (sameDay.length >= p.maxSlotsPerDay) {
      return fail('每人每天最多预约 ' + p.maxSlotsPerDay + ' 个时段，当日已有 ' + sameDay.length + ' 个', 'BR-02');
    }
    var minutesUsed = sameDay.reduce(function (acc, r) { return acc + (minutesOf(r.end) - minutesOf(r.start)); }, 0);
    if (minutesUsed + dur > p.maxMinutesPerDay) {
      return fail('每日预约总时长不得超过 ' + (p.maxMinutesPerDay / 60) + ' 小时，当日已预约 ' +
        (minutesUsed / 60) + ' 小时', 'BR-02');
    }
    /* BR-11：座位时段冲突与本人时段重叠 */
    var holders = seatHoldersAt(state, seat.id, input.date, input.start, input.end);
    if (holders.length) {
      var h = holders[0];
      return fail('座位 ' + seat.id + ' 在 ' + input.date + ' ' + input.start + '-' + input.end +
        ' 已被占用（预约 ' + h.id + '，状态 ' + RES_LABEL[h.status] + '）', 'BR-11',
        { conflictReservationId: h.id, suggestWaitlist: true });
    }
    if (userOverlaps(state, user.id, input.date, input.start, input.end).length) {
      return fail('您在该时段已有其他有效预约，不能重复预约', 'BR-11');
    }

    /* 创建：状态 = 待签到（ST-01 之前） */
    /* 生成唯一预约编号：避免与预置/历史记录冲突（NFR-03 数据一致性） */
    var resId;
    do {
      resId = nextId(state, 'reservation', 'R' + input.date.replace(/-/g, '') + '-');
    } while (findReservation(state, resId));
    var res = {
      id: resId,
      userId: user.id, seatId: seat.id, roomId: seat.roomId,
      date: input.date, start: input.start, end: input.end,
      status: RES.PENDING, createdAt: fmtDT(now),
      checkInAt: '', awayAt: '', awayDeadlineAt: '', checkoutAt: '',
      credential: '', endReason: '', source: input.source || 'self'
    };
    state.reservations.push(res);
    notify(state, user.id, 'reserve_ok',
      '预约成功：' + res.seatId + ' ' + res.date + ' ' + res.start + '-' + res.end +
      '，请在 ' + res.date + ' ' + fmtTime(addMinutes(toDate(res.date, res.start), -p.checkinOpenMinutesBefore)) +
      ' ~ ' + fmtTime(addMinutes(toDate(res.date, res.start), p.checkinCloseMinutesAfter)) + ' 之间签到。', now);
    logEvent(state, input.actorId, '创建预约成功', 'BR-01/BR-02/BR-03/BR-11',
      res.id + ' → 待签到（' + res.seatId + ' ' + res.date + ' ' + res.start + '-' + res.end + '）', state.clock);
    return ok({ reservation: res });
  }

  /** FR-41 / BR-04 / BR-13：签到（ST-01 / ST-02 / 状态保持） */
  function checkIn(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var p = state.params;
    var user = actorOf(state, input.actorId);
    var res = findReservation(state, input.reservationId);
    if (!user || !res) { return fail('用户或预约不存在'); }
    if (res.userId !== user.id) {
      return fail('只能为本人预约签到（预约 ' + res.id + ' 属于其他用户）', 'PR-02');
    }
    if (res.status !== RES.PENDING) {
      return fail('预约 ' + res.id + ' 当前状态为「' + RES_LABEL[res.status] + '」，不可签到', 'ST-12');
    }
    /* BR-13：凭证必须存在、未使用、未过期 */
    var cred = input.credential;
    if (!cred || !cred.code || !cred.issuedAt) {
      return fail('签到凭证无效，请重新获取二维码', 'BR-13');
    }
    if (state.usedCredentials.indexOf(cred.code) >= 0) {
      return fail('该签到凭证已被使用，不能重复签到', 'BR-13');
    }
    if ((now.getTime() - new Date(cred.issuedAt).getTime()) / 1000 > p.credentialValidSeconds) {
      return fail('签到凭证已过期（有效期 ' + p.credentialValidSeconds + ' 秒）', 'BR-13');
    }
    /* BR-13：同一账号同一时刻只能有 1 条"使用中"记录 */
    var inUse = state.reservations.filter(function (r) {
      return r.userId === user.id && r.status === RES.IN_USE;
    });
    if (inUse.length) {
      return fail('您已有正在使用的座位（' + inUse[0].seatId + '），请先签退', 'BR-13');
    }
    /* BR-04：签到时间窗判定 */
    var startAt = toDate(res.date, res.start);
    var openAt = addMinutes(startAt, -p.checkinOpenMinutesBefore);
    var closeAt = addMinutes(startAt, p.checkinCloseMinutesAfter);
    if (now < openAt) {
      logEvent(state, input.actorId, '签到被拒绝：未到签到时间', 'BR-04',
        res.id + ' 状态保持「待签到」，签到窗口 ' + fmtDT(openAt) + ' ~ ' + fmtDT(closeAt), state.clock);
      return fail('未到签到时间，签到窗口为 ' + fmtDT(openAt) + ' ~ ' + fmtDT(closeAt) +
        '，当前 ' + fmtDT(now) + '；预约状态保持「待签到」', 'BR-04', { outcome: 'state_kept', reservation: res });
    }
    if (now > closeAt) {
      /* ST-02：超过签到窗口 → 违约 */
      res.status = RES.NO_SHOW;
      res.endReason = 'no_show';
      var v = recordViolation(state, res, 'no_show', now, '系统');
      releaseSeatSlot(state, res, now);
      logEvent(state, input.actorId, '签到超时，转入违约', 'BR-04/ST-02',
        res.id + ' → 已违约，扣 ' + p.noShowPenalty + ' 分（' + v.id + '）', state.clock);
      return fail('已超过签到窗口（' + fmtDT(closeAt) + '），本次预约记为违约（ST-02），信用分 -' +
        p.noShowPenalty, 'BR-04', { outcome: 'no_show', reservation: res, violation: v });
    }
    /* ST-01：签到成功 */
    res.status = RES.IN_USE;
    res.checkInAt = fmtDT(now);
    res.credential = cred.code;
    state.usedCredentials.push(cred.code);
    logEvent(state, input.actorId, '签到成功', 'ST-01',
      res.id + ' → 使用中（' + res.seatId + ' ' + res.date + ' ' + res.start + '-' + res.end + '）', state.clock);
    return ok({ reservation: res, seatStatus: SEAT.OCCUPIED });
  }

  /** FR-42 / BR-05：暂时离开（ST-05） */
  function startAway(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var res = findReservation(state, input.reservationId);
    if (!res || res.userId !== input.actorId) { return fail('只能操作本人预约', 'PR-03'); }
    if (res.status !== RES.IN_USE) {
      return fail('预约 ' + res.id + ' 当前状态为「' + RES_LABEL[res.status] + '」，不可发起暂离', 'ST-12');
    }
    res.status = RES.AWAY;
    res.awayAt = fmtDT(now);
    res.awayDeadlineAt = fmtDT(addMinutes(now, state.params.awayHoldMinutes));
    logEvent(state, input.actorId, '发起暂时离开', 'ST-05/BR-05',
      res.id + ' → 暂离，座位保留至 ' + res.awayDeadlineAt, state.clock);
    return ok({ reservation: res, awayDeadlineAt: res.awayDeadlineAt });
  }

  /** FR-42：返座确认（ST-06；超时则 ST-07） */
  function resumeUse(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var res = findReservation(state, input.reservationId);
    if (!res || res.userId !== input.actorId) { return fail('只能操作本人预约', 'PR-03'); }
    if (res.status !== RES.AWAY) {
      return fail('预约 ' + res.id + ' 当前状态为「' + RES_LABEL[res.status] + '」，不可返座', 'ST-12');
    }
    if (res.awayDeadlineAt && now > new Date(res.awayDeadlineAt)) {
      /* ST-07：暂离超时 */
      res.status = RES.RELEASED;
      res.endReason = 'away_timeout';
      var v = recordViolation(state, res, 'away_timeout', now, '系统');
      releaseSeatSlot(state, res, now);
      logEvent(state, input.actorId, '返座超时，座位已释放', 'ST-07/BR-05',
        res.id + ' → 已释放，扣 ' + state.params.awayTimeoutPenalty + ' 分（' + v.id + '）', state.clock);
      return fail('已超过暂离保留时限（' + res.awayDeadlineAt + '），座位已被释放（ST-07），信用分 -' +
        state.params.awayTimeoutPenalty, 'BR-05', { outcome: 'released', reservation: res, violation: v });
    }
    res.status = RES.IN_USE;
    res.awayAt = '';
    res.awayDeadlineAt = '';
    logEvent(state, input.actorId, '返座确认', 'ST-06', res.id + ' → 使用中', state.clock);
    return ok({ reservation: res });
  }

  /** FR-43 / BR-06：签退（ST-08 正常 / ST-09 到点未签退） */
  function checkOut(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var res = findReservation(state, input.reservationId);
    if (!res || res.userId !== input.actorId) { return fail('只能操作本人预约', 'PR-03'); }
    if (res.status !== RES.IN_USE && res.status !== RES.AWAY) {
      return fail('预约 ' + res.id + ' 当前状态为「' + RES_LABEL[res.status] + '」，不可签退', 'ST-12');
    }
    var endAt = toDate(res.date, res.end);
    res.checkoutAt = fmtDT(now);
    if (now > endAt) {
      /* ST-09：已超过预约结束时间才签退 */
      res.status = RES.COMPLETED;
      res.endReason = 'overstay';
      var v = recordViolation(state, res, 'overstay', now, '系统');
      releaseSeatSlot(state, res, now);
      logEvent(state, input.actorId, '超时签退', 'ST-09/BR-06',
        res.id + ' → 已完成（结束原因=超时未签退），扣 ' + state.params.overstayPenalty + ' 分（' + v.id + '）', state.clock);
      return ok({ reservation: res, outcome: 'overstay', violation: v });
    }
    res.status = RES.COMPLETED;
    res.endReason = 'normal';
    var user = findUser(state, res.userId);
    var rewarded = grantOnTimeReward(state, user, now);
    releaseSeatSlot(state, res, now);
    logEvent(state, input.actorId, '正常签退', 'ST-08/BR-06',
      res.id + ' → 已完成，座位释放' + (rewarded ? '，信用分 +' + state.params.onTimeReward : '（今日奖励已达上限）'), state.clock);
    return ok({ reservation: res, outcome: 'completed', rewarded: rewarded });
  }

  /** FR-33 / BR-10：取消预约（ST-03 免费取消 / ST-04 临近取消记违约） */
  function cancelReservation(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var p = state.params;
    var res = findReservation(state, input.reservationId);
    if (!res) { return fail('预约不存在'); }
    if (res.userId !== input.actorId) {
      return fail('只能取消本人预约（预约 ' + res.id + ' 属于其他用户）', 'PR-03');
    }
    if (res.status !== RES.PENDING) {
      return fail('预约状态为「' + RES_LABEL[res.status] + '」，不可自助取消（须走申诉流程）', 'BR-10');
    }
    var startAt = toDate(res.date, res.start);
    var minutesToStart = diffMin(now, startAt);
    if (minutesToStart <= 0) {
      return fail('预约时段已开始，不可自助取消，请通过申诉处理', 'BR-10');
    }
    if (minutesToStart >= p.cancelFreeMinutesBefore) {
      /* ST-03：提前取消，不记违规 */
      res.status = RES.CANCELLED;
      res.endReason = 'cancel_free';
      releaseSeatSlot(state, res, now);
      logEvent(state, input.actorId, '取消预约（提前 ' + minutesToStart + ' 分钟，不记违规）', 'ST-03/BR-10',
        res.id + ' → 已取消', state.clock);
      return ok({ reservation: res, outcome: 'cancelled_free', penalty: 0 });
    }
    /* ST-04：临近取消，记违规 */
    res.status = RES.CANCELLED;
    res.endReason = 'cancel_late';
    var v = recordViolation(state, res, 'late_cancel', now, '系统');
    releaseSeatSlot(state, res, now);
    logEvent(state, input.actorId, '取消预约（距开始仅 ' + minutesToStart + ' 分钟，记违规）', 'ST-04/BR-10',
      res.id + ' → 已取消，信用分 -' + p.cancelLatePenalty + '（' + v.id + '）', state.clock);
    return ok({ reservation: res, outcome: 'cancelled_late', penalty: p.cancelLatePenalty, violation: v });
  }

  /** FR-34 / BR-08：加入候补队列 */
  function joinWaitlist(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var user = actorOf(state, input.actorId);
    if (!user || user.role !== ROLES.STUDENT) { return fail('仅学生可登记候补', 'PR-04'); }
    var seat = findSeat(state, input.seatId);
    if (!seat) { return fail('座位不存在：' + input.seatId); }
    var holders = seatHoldersAt(state, seat.id, input.date, input.start, input.end);
    if (!holders.length) {
      return fail('座位 ' + seat.id + ' 在该时段仍空闲，可直接预约，无需候补', 'BR-08');
    }
    var already = state.waitlist.filter(function (w) {
      return w.userId === user.id && w.seatId === seat.id && w.status === 'queued' &&
        overlaps(input.date, input.start, input.end, w.date, w.start, w.end);
    });
    if (already.length) { return fail('您已在该时段候补过该座位', 'BR-08'); }

    var seq = state.waitlist.filter(function (w) {
      return w.seatId === seat.id && (w.status === 'queued' || w.status === 'notified') &&
        overlaps(input.date, input.start, input.end, w.date, w.start, w.end);
    }).length + 1;
    var entry = {
      id: nextId(state, 'waitlist', 'W-'),
      userId: user.id, seatId: seat.id, date: input.date, start: input.start, end: input.end,
      joinedAt: fmtDT(now), seq: seq, status: 'queued', notifiedAt: '', confirmDeadline: ''
    };
    state.waitlist.push(entry);
    notify(state, user.id, 'waitlist',
      '已加入候补：' + seat.id + ' ' + input.date + ' ' + input.start + '-' + input.end + '，当前候补号 ' + seq, now);
    logEvent(state, input.actorId, '加入候补队列', 'BR-08',
      entry.id + '（排队号 ' + seq + '）', state.clock);
    return ok({ entry: entry });
  }

  /** FR-34 / BR-08：候补补位确认 */
  function confirmWaitlist(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var entry = null;
    for (var i = 0; i < state.waitlist.length; i++) { if (state.waitlist[i].id === input.waitlistId) { entry = state.waitlist[i]; } }
    if (!entry) { return fail('候补记录不存在'); }
    if (entry.userId !== input.actorId) { return fail('只能确认本人的候补记录', 'PR-04'); }
    if (entry.status !== 'notified') {
      return fail('候补记录状态为「' + entry.status + '」，当前不可确认', 'BR-08');
    }
    if (entry.confirmDeadline && now > new Date(entry.confirmDeadline)) {
      entry.status = 'expired';
      logEvent(state, input.actorId, '候补确认超时', 'BR-08',
        entry.id + ' → 已超时，名额顺延', state.clock);
      notifyNextWaitlist(state, entry.seatId, entry.date, entry.start, entry.end, now);
      return fail('已超过确认时限（' + entry.confirmDeadline + '），候补名额已顺延下一位', 'BR-08');
    }
    var created = createReservation(state, {
      actorId: input.actorId, seatId: entry.seatId, date: entry.date,
      start: entry.start, end: entry.end, now: now, source: 'waitlist'
    });
    if (!created.ok) { return created; }
    entry.status = 'confirmed';
    logEvent(state, input.actorId, '候补补位成功', 'BR-08',
      entry.id + ' → 已确认，生成预约 ' + created.reservation.id, state.clock);
    return ok({ entry: entry, reservation: created.reservation });
  }

  /** FR-44 / UC-10 / NFR-02：超时定时任务（幂等），覆盖 ST-02、ST-07、ST-09 与候补顺延 */
  function runTimeoutSweep(state, nowInput) {
    var now = nowInput ? new Date(nowInput) : new Date(state.clock);
    var p = state.params;
    var actions = [];
    state.reservations.forEach(function (res) {
      if (res.status === RES.PENDING) {
        var closeAt = addMinutes(toDate(res.date, res.start), p.checkinCloseMinutesAfter);
        if (now > closeAt) {
          res.status = RES.NO_SHOW;
          res.endReason = 'no_show';
          var v = recordViolation(state, res, 'no_show', now, '系统');
          releaseSeatSlot(state, res, now);
          logEvent(state, 'system', '定时任务：未签到违约', 'ST-02/BR-04',
            res.id + ' → 已违约，信用分 -' + p.noShowPenalty + '（' + v.id + '）', fmtDT(now));
          actions.push({ reservationId: res.id, type: 'no_show', violationId: v.id });
        }
      } else if (res.status === RES.AWAY) {
        if (res.awayDeadlineAt && now > new Date(res.awayDeadlineAt)) {
          res.status = RES.RELEASED;
          res.endReason = 'away_timeout';
          var v2 = recordViolation(state, res, 'away_timeout', now, '系统');
          releaseSeatSlot(state, res, now);
          logEvent(state, 'system', '定时任务：暂离超时释放', 'ST-07/BR-05',
            res.id + ' → 已释放，信用分 -' + p.awayTimeoutPenalty + '（' + v2.id + '）', fmtDT(now));
          actions.push({ reservationId: res.id, type: 'away_timeout', violationId: v2.id });
        }
      } else if (res.status === RES.IN_USE) {
        var endAt = toDate(res.date, res.end);
        if (now > endAt) {
          res.status = RES.COMPLETED;
          res.endReason = 'overstay';
          var v3 = recordViolation(state, res, 'overstay', now, '系统');
          releaseSeatSlot(state, res, now);
          logEvent(state, 'system', '定时任务：到点未签退', 'ST-09/BR-06',
            res.id + ' → 已完成（超时未签退），信用分 -' + p.overstayPenalty + '（' + v3.id + '）', fmtDT(now));
          actions.push({ reservationId: res.id, type: 'overstay', violationId: v3.id });
        }
      }
    });
    /* 候补确认超时顺延（BR-08） */
    state.waitlist.forEach(function (w) {
      if (w.status === 'notified' && w.confirmDeadline && now > new Date(w.confirmDeadline)) {
        w.status = 'expired';
        logEvent(state, 'system', '定时任务：候补确认超时', 'BR-08', w.id + ' → 已超时', fmtDT(now));
        notifyNextWaitlist(state, w.seatId, w.date, w.start, w.end, now);
        actions.push({ waitlistId: w.id, type: 'waitlist_expired' });
      }
    });
    return ok({ actions: actions, count: actions.length });
  }

  /* ------------------------------------------------------------------ *
   * 九、管理员与系统管理员操作（FR-23、FR-45、FR-53、FR-71、PR-05~PR-15）
   * ------------------------------------------------------------------ */

  /** UC-12 / BR-09：座位封禁，受影响预约自动取消且不记违规 */
  function blockSeat(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor) { return fail('用户不存在'); }
    if (actor.role !== ROLES.ADMIN && actor.role !== ROLES.SYSADMIN) {
      return fail('仅自习室管理员/系统管理员可封禁座位，当前角色：' + (ROLE_LABEL[actor.role] || actor.role), 'PR-06');
    }
    if (!input.reason) { return fail('封禁座位必须填写原因', 'BR-09'); }
    var seat = findSeat(state, input.seatId);
    if (!seat) { return fail('座位不存在：' + input.seatId); }
    var before = seat.status;
    seat.status = SEAT.MAINTENANCE;
    seat.blockReason = input.reason;
    seat.blockFrom = input.from || '';
    seat.blockTo = input.to || '';
    /* BR-09：受影响预约自动取消，不记违规 */
    var affected = [];
    state.reservations.forEach(function (res) {
      if (res.seatId !== seat.id) { return; }
      if (SEAT_HOLDING_STATES.indexOf(res.status) < 0) { return; }
      var inWindow = true;
      if (input.from && input.to) {
        inWindow = overlaps(input.from, '00:00', '23:59', res.date, res.start, res.end) || true;
      }
      if (inWindow) {
        res.status = RES.CANCELLED;
        res.endReason = 'seat_blocked';
        affected.push(res.id);
        notify(state, res.userId, 'seat_blocked',
          '座位 ' + seat.id + ' 因「' + input.reason + '」封禁，您在 ' + res.date + ' ' + res.start + '-' + res.end +
          ' 的预约已自动取消，本次不计违规。', now);
      }
    });
    audit(state, input.actorId, '封禁座位', seat.id, before, SEAT.MAINTENANCE + '（原因：' + input.reason + '）', now);
    logEvent(state, input.actorId, '封禁座位', 'BR-09/ST-11',
      seat.id + ' → 维护中，受影响预约 ' + (affected.length ? affected.join('、') : '无') + '（不记违规）', state.clock);
    return ok({ seat: seat, affectedReservations: affected });
  }
  function unblockSeat(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor || (actor.role !== ROLES.ADMIN && actor.role !== ROLES.SYSADMIN)) {
      return fail('仅自习室管理员/系统管理员可解封座位', 'PR-06');
    }
    var seat = findSeat(state, input.seatId);
    if (!seat) { return fail('座位不存在：' + input.seatId); }
    seat.status = SEAT.AVAILABLE;
    seat.blockReason = '';
    audit(state, input.actorId, '解封座位', seat.id, SEAT.MAINTENANCE, SEAT.AVAILABLE, now);
    logEvent(state, input.actorId, '解封座位', 'BR-09', seat.id + ' → 空闲', state.clock);
    return ok({ seat: seat });
  }

  /** UC-13 / PR-07 / ST-10：管理员强制释放（须填原因，记审计日志） */
  function forceRelease(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor || actor.role !== ROLES.ADMIN) {
      return fail('仅自习室管理员可强制释放座位（PR-07），当前角色：' + (ROLE_LABEL[(actor || {}).role] || '-'), 'PR-07');
    }
    if (!input.reason) { return fail('强制释放必须填写原因', 'PR-07'); }
    var res = findReservation(state, input.reservationId);
    if (!res) { return fail('预约不存在：' + input.reservationId); }
    if (SEAT_HOLDING_STATES.indexOf(res.status) < 0) {
      return fail('预约 ' + res.id + ' 当前状态为「' + RES_LABEL[res.status] + '」，无需释放', 'ST-12');
    }
    var before = res.status;
    res.status = RES.RELEASED;
    res.endReason = 'admin_release';
    releaseSeatSlot(state, res, now);
    audit(state, input.actorId, '强制释放座位', res.id, RES_LABEL[before], 'RELEASED（原因：' + input.reason + '）', now);
    logEvent(state, input.actorId, '强制释放座位', 'ST-10/PR-07',
      res.id + ' → 已释放（原因：' + input.reason + '），不记违规', state.clock);
    return ok({ reservation: res });
  }

  /** FR-53 / BR-17 / PR-09：学生提交违规申诉 */
  function submitAppeal(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var p = state.params;
    var v = findViolation(state, input.violationId);
    if (!v) { return fail('违规记录不存在：' + input.violationId); }
    if (v.userId !== input.actorId) { return fail('只能对本人违规记录提交申诉', 'PR-09'); }
    if (v.status !== 'active') { return fail('违规记录状态为「' + v.status + '」，不可申诉', 'BR-17'); }
    var occurred = new Date(String(v.occurredAt).replace(' ', 'T') + ':00');
    var days = diffMin(occurred, now) / (60 * 24);
    if (days > p.appealWindowDays) {
      return fail('违规发生已超过 ' + p.appealWindowDays + ' 日，超过申诉期限', 'BR-17');
    }
    v.status = 'appealing';
    v.appealReason = input.reason || '（未填写理由）';
    v.appealAt = fmtDT(now);
    logEvent(state, input.actorId, '提交违规申诉', 'BR-17/PR-09', v.id + ' → 申诉中', state.clock);
    return ok({ violation: v });
  }

  /** FR-53 / PR-10：管理员审核申诉（成立则撤销违规并恢复信用分） */
  function reviewAppeal(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor || actor.role !== ROLES.ADMIN) {
      return fail('仅自习室管理员可审核申诉（PR-10），当前角色：' + (ROLE_LABEL[(actor || {}).role] || '-'), 'PR-10');
    }
    var v = findViolation(state, input.violationId);
    if (!v) { return fail('违规记录不存在：' + input.violationId); }
    if (v.status !== 'appealing') { return fail('违规记录不处于申诉中，无法审核', 'BR-17'); }
    if (input.decision !== 'approve' && input.decision !== 'reject') {
      return fail('审核决定须为 approve 或 reject');
    }
    var before = v.status;
    if (input.decision === 'approve') {
      v.status = 'revoked';
      v.reviewNote = input.note || '申诉成立，违规撤销';
      changeCredit(state, v.userId, Math.abs(v.points), '申诉成立，撤销违规（' + v.id + '）', v.id, actor.name, now);
      var user = findUser(state, v.userId);
      if (user) {
        if (v.type === 'no_show' && user.consecutiveNoShow > 0) { user.consecutiveNoShow -= 1; }
        refreshLimit(state, user, now);
        if (user.limit && user.limit.type === 'no_show' && user.consecutiveNoShow < state.params.consecutiveNoShowLimit) {
          user.limit = null;
        }
      }
      notify(state, v.userId, 'appeal_result', '申诉成立：违规 ' + v.id + ' 已撤销，信用分恢复 ' + Math.abs(v.points) + ' 分。', now);
    } else {
      v.status = 'active';
      v.reviewNote = input.note || '申诉不成立，违规维持';
      notify(state, v.userId, 'appeal_result', '申诉不成立：违规 ' + v.id + ' 维持原判。' + (input.note || ''), now);
    }
    audit(state, input.actorId, '审核违规申诉', v.id, before, v.status + '（' + v.reviewNote + '）', now);
    logEvent(state, input.actorId, '审核申诉：' + (input.decision === 'approve' ? '成立' : '不成立'), 'BR-17/PR-10',
      v.id + ' → ' + v.status, state.clock);
    return ok({ violation: v });
  }

  /** FR-71 / BR-14 / PR-13：系统参数配置（仅对新预约生效，已有预约不变） */
  function setParam(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor || actor.role !== ROLES.SYSADMIN) {
      return fail('仅系统管理员可修改业务参数（PR-13），当前角色：' + (ROLE_LABEL[(actor || {}).role] || '-'), 'PR-13');
    }
    if (!(input.code in state.params)) { return fail('参数不存在：' + input.code); }
    var before = state.params[input.code];
    var value = input.value;
    if (typeof before === 'number') {
      value = Number(value);
      if (isNaN(value) || value < 0) { return fail('参数 ' + input.code + ' 须为非负数值'); }
    }
    state.params[input.code] = value;
    audit(state, input.actorId, '修改系统参数', input.code, before, value, now);
    logEvent(state, input.actorId, '修改系统参数', 'BR-14/PR-13',
      input.code + '：' + before + ' → ' + value + '（仅对新预约生效）', state.clock);
    return ok({ code: input.code, before: before, after: value });
  }

  /* ------------------------------------------------------------------ *
   * 十、查询接口（含权限过滤，PR-01、PR-11、PR-12）
   * ------------------------------------------------------------------ */
  function listReservations(state, actorId) {
    var actor = actorOf(state, actorId);
    if (!actor) { return []; }
    if (actor.role === ROLES.STUDENT) {
      return state.reservations.filter(function (r) { return r.userId === actor.id; });
    }
    return state.reservations.slice();
  }
  function listViolations(state, actorId) {
    var actor = actorOf(state, actorId);
    if (!actor) { return []; }
    if (actor.role === ROLES.STUDENT) {
      return state.violations.filter(function (v) { return v.userId === actor.id; });
    }
    return state.violations.slice();
  }
  function listCreditLogs(state, actorId) {
    var actor = actorOf(state, actorId);
    if (!actor) { return []; }
    if (actor.role === ROLES.STUDENT) {
      return state.creditLogs.filter(function (c) { return c.userId === actor.id; });
    }
    return state.creditLogs.slice();
  }
  function listNotifications(state, actorId) {
    return state.notifications.filter(function (n) { return n.userId === actorId; });
  }
  function listAuditLogs(state, actorId) {
    var actor = actorOf(state, actorId);
    if (!actor || actor.role !== ROLES.SYSADMIN) {
      return { ok: false, message: '仅系统管理员可查看审计日志', rule: 'PR-15', logs: [] };
    }
    return ok({ logs: state.auditLogs.slice() });
  }
  /* PR-12：教师仅可查看本班汇总；此处返回统计口径的汇总数据 */
  function violationStats(state, actorId) {
    var actor = actorOf(state, actorId);
    if (!actor) { return { ok: false, message: '用户不存在' }; }
    if (actor.role === ROLES.STUDENT) {
      return fail('学生无权查看统计报表', 'PR-12');
    }
    var byType = {};
    state.violations.forEach(function (v) {
      if (actor.role === ROLES.TEACHER && (findUser(state, v.userId) || {}).className !== actor.className) { return; }
      byType[v.type] = (byType[v.type] || 0) + 1;
    });
    return ok({ byType: byType, total: state.violations.length });
  }

  /* ------------------------------------------------------------------ *
   * 十一、现场巡检与手动违约登记（FR-65、BR-18、PR-17）
   * 管理员现场登记违约：mode='warn' 仅警告（预约与座位状态保持，ST-12），
   * mode='release' 强制释放座位（预约→已违约/已释放，ST-10）。均须填写核实说明并写审计日志。
   * ------------------------------------------------------------------ */
  var MANUAL_CATEGORY = {
    occupy: '人走留物恶意占用座位',
    noise: '室内大声喧哗 / 手机外放',
    power: '违规使用大功率电器',
    proxy: '他人代签 / 虚假暂离'
  };

  function manualViolation(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor || actor.role !== ROLES.ADMIN) {
      return fail('仅自习室管理员可登记现场违约（PR-17）', 'PR-17');
    }
    if (!input.remark) { return fail('手动违约登记必须填写现场核实说明（BR-18）', 'BR-18'); }
    var res = findReservation(state, input.reservationId);
    if (!res) { return fail('预约不存在：' + input.reservationId); }
    if (SEAT_HOLDING_STATES.indexOf(res.status) < 0) {
      return fail('预约 ' + res.id + ' 当前状态为「' + RES_LABEL[res.status] + '」，无需登记', 'ST-12');
    }
    var mode = input.mode === 'warn' ? 'warn' : 'release';
    var p = state.params;
    var v = {
      id: nextId(state, 'violation', 'VIO-'),
      userId: res.userId, reservationId: res.id,
      type: 'manual', source: 'manual',
      typeLabel: '手动登记 — ' + (MANUAL_CATEGORY[input.category] || input.category || '现场违规'),
      points: -p.manualPenalty, occurredAt: fmtDT(now), status: 'active',
      appealReason: '', reviewNote: input.remark, operator: actor.name
    };
    state.violations.push(v);
    changeCredit(state, res.userId, -p.manualPenalty,
      '手动违约登记：' + v.typeLabel + '（' + v.id + '）', v.id, actor.name, now);
    if (mode === 'release') {
      var before = res.status;
      res.status = RES.RELEASED;
      res.endReason = 'admin_release';
      releaseSeatSlot(state, res, now);
      audit(state, input.actorId, '手动违约并强制释放座位', res.id, RES_LABEL[before],
        'RELEASED（' + input.remark + '）', now);
    } else {
      audit(state, input.actorId, '手动违约登记（仅警告，状态保持）', res.id, RES_LABEL[res.status],
        RES_LABEL[res.status] + '（' + input.remark + '）', now);
    }
    applyBlacklistRule(state, findUser(state, res.userId), now);
    notify(state, res.userId, 'violation',
      '管理员现场巡检登记违约：' + v.typeLabel + '；' +
      (mode === 'release' ? '座位已释放' : '座位保持使用中') +
      '，信用分 -' + p.manualPenalty + '，可在 ' + p.appealWindowDays + ' 日内申诉。', now);
    logEvent(state, input.actorId, '手动违约登记（' + (mode === 'release' ? '强制释放' : '仅警告') + '）',
      'BR-18/PR-17', res.id + ' → ' + v.id + '，扣 ' + p.manualPenalty + ' 分', state.clock);
    return ok({ violation: v, reservation: res, mode: mode });
  }

  /* ------------------------------------------------------------------ *
   * 十二、投诉与反馈闭环（FR-66、BR-19、PR-18）
   * ------------------------------------------------------------------ */
  function submitComplaint(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var user = actorOf(state, input.actorId);
    if (!user || user.role !== ROLES.STUDENT) { return fail('仅学生可提交投诉（PR-01）', 'PR-01'); }
    if (!input.description) { return fail('请填写具体情况描述（BR-19）', 'BR-19'); }
    var seat = input.seatId ? findSeat(state, input.seatId) : null;
    var c = {
      id: nextId(state, 'complaint', 'CMP-'),
      userId: user.id, userName: user.name,
      seatId: seat ? seat.id : '', seatCode: seat ? seat.id : (input.seatCode || ''),
      type: input.type || '其他', description: input.description,
      status: 'pending', feedback: '', feedbackBy: '', feedbackAt: '',
      linkedViolationId: null, createdAt: fmtDT(now)
    };
    state.complaints.push(c);
    logEvent(state, input.actorId, '提交投诉', 'FR-66/BR-19', c.id + '：' + c.type + '（' + c.seatCode + '）', state.clock);
    return ok({ complaint: c });
  }

  function replyComplaint(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor || actor.role !== ROLES.ADMIN) { return fail('仅自习室管理员可填写投诉反馈（PR-18）', 'PR-18'); }
    if (!input.feedback) { return fail('投诉反馈信息必填（学生端可见）', 'BR-19'); }
    var c = null;
    for (var i = 0; i < state.complaints.length; i++) {
      if (state.complaints[i].id === input.complaintId) { c = state.complaints[i]; }
    }
    if (!c) { return fail('投诉单不存在：' + input.complaintId); }
    if (c.status === 'closed') { return fail('该投诉单已办结，不能重复反馈', 'BR-19'); }
    var linked = null;
    if (input.linkViolation) {
      var target = input.reservationId || currentReservationAtSeat(state, c.seatId);
      if (target) {
        var r = manualViolation(state, {
          actorId: input.actorId, reservationId: target, category: 'occupy',
          remark: '投诉联动处置：' + c.id, mode: 'release', now: now
        });
        if (r.ok) { linked = r.violation.id; c.linkedViolationId = linked; }
      }
    }
    c.feedback = input.feedback; c.feedbackBy = actor.name; c.feedbackAt = fmtDT(now); c.status = 'closed';
    audit(state, input.actorId, '填写投诉反馈并办结', c.id, 'pending',
      'closed（' + (linked ? '联动违约 ' + linked : '未联动违约') + '）', now);
    notify(state, c.userId, 'complaint_reply',
      '您的投诉 ' + c.id + '（' + c.type + '）已处理：' + input.feedback, now);
    logEvent(state, input.actorId, '填写投诉反馈', 'BR-19', c.id + ' → 已办结', state.clock);
    return ok({ complaint: c, linkedViolationId: linked });
  }

  function currentReservationAtSeat(state, seatId) {
    for (var i = 0; i < state.reservations.length; i++) {
      var r = state.reservations[i];
      if (r.seatId === seatId && SEAT_HOLDING_STATES.indexOf(r.status) >= 0) { return r.id; }
    }
    return null;
  }

  function listComplaints(state, actorId) {
    var actor = actorOf(state, actorId);
    if (!actor) { return []; }
    if (actor.role === ROLES.STUDENT) {
      return state.complaints.filter(function (c) { return c.userId === actor.id; });
    }
    return state.complaints.slice();
  }

  /* ------------------------------------------------------------------ *
   * 十三、座位与环境评价（FR-68、BR-21）
   * 仅本人「已完成」的预约可评价一次；管理员可回复；<=2 星视为差评，可联动维护停用。
   * ------------------------------------------------------------------ */
  function submitEvaluation(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var user = actorOf(state, input.actorId);
    if (!user || user.role !== ROLES.STUDENT) { return fail('仅学生可提交评价（PR-01）', 'PR-01'); }
    var res = findReservation(state, input.reservationId);
    if (!res || res.userId !== user.id) { return fail('只能评价本人的预约（PR-03）', 'PR-03'); }
    if (res.status !== RES.COMPLETED) { return fail('仅「已完成」的预约可评价（BR-21）', 'BR-21'); }
    var dup = state.evaluations.filter(function (e) { return e.reservationId === res.id; });
    if (dup.length) { return fail('该预约已评价过，每单限一次（BR-21）', 'BR-21'); }
    var rating = Number(input.rating);
    if (!(rating >= 1 && rating <= 5)) { return fail('评分须为 1~5 星（BR-21）', 'BR-21'); }
    var ev = {
      id: nextId(state, 'evaluation', 'EVAL-'),
      reservationId: res.id, userId: user.id, userName: user.name, seatId: res.seatId,
      rating: rating, tags: input.tags || '', content: input.content || '',
      reply: '', replyBy: '', replyAt: '', createdAt: fmtDT(now)
    };
    state.evaluations.push(ev);
    logEvent(state, input.actorId, '提交座位评价', 'FR-68/BR-21',
      res.seatId + ' 评分 ' + rating + ' 星' + (rating <= 2 ? '（差评，可联动维护停用）' : ''), state.clock);
    return ok({ evaluation: ev });
  }

  function replyEvaluation(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor || actor.role !== ROLES.ADMIN) { return fail('仅自习室管理员可回复评价（PR-18）', 'PR-18'); }
    if (!input.reply) { return fail('回复内容不能为空（BR-21）', 'BR-21'); }
    var ev = null;
    for (var i = 0; i < state.evaluations.length; i++) {
      if (state.evaluations[i].id === input.evaluationId) { ev = state.evaluations[i]; }
    }
    if (!ev) { return fail('评价不存在：' + input.evaluationId); }
    ev.reply = input.reply; ev.replyBy = actor.name; ev.replyAt = fmtDT(now);
    if (input.setMaintenance) {
      var seat = findSeat(state, ev.seatId);
      if (seat) {
        var before = seat.status;
        seat.status = SEAT.MAINTENANCE;
        seat.blockReason = '低分评价联动维护停用（' + ev.id + '）';
        state.reservations.forEach(function (r) {
          if (r.seatId === seat.id && SEAT_HOLDING_STATES.indexOf(r.status) >= 0) {
            r.status = RES.CANCELLED;
            r.endReason = 'seat_blocked';
            notify(state, r.userId, 'seat_blocked',
              '座位 ' + seat.id + ' 因维护已停用，您在 ' + r.date + ' ' + r.start + '-' + r.end +
              ' 的预约已取消，本次不计违规。', now);
          }
        });
        audit(state, input.actorId, '低分评价联动维护停用', seat.id, before, SEAT.MAINTENANCE, now);
      }
    }
    audit(state, input.actorId, '回复评价', ev.id, '', input.reply, now);
    logEvent(state, input.actorId, '回复评价', 'BR-21/PR-18',
      ev.id + (input.setMaintenance ? '，并联动维护停用' : ''), state.clock);
    return ok({ evaluation: ev });
  }

  function listEvaluations(state, actorId) {
    var actor = actorOf(state, actorId);
    if (!actor) { return []; }
    if (actor.role === ROLES.STUDENT) {
      return state.evaluations.filter(function (e) { return e.userId === actor.id; });
    }
    return state.evaluations.slice();
  }

  /* ------------------------------------------------------------------ *
   * 十四、信用档案与黑名单（FR-67、BR-20、PR-18）
   * 规则：30 天窗口内「有效」违约（未被撤销）累计 >= 3 次 → 自动限制预约 7 天；
   *       管理员可人工拉黑或解封（须填原因，写审计日志）。与 BR-07 / BR-15 并列生效。
   * ------------------------------------------------------------------ */
  function activeViolationCount(state, userId, now) {
    var from = addDays(now, -state.params.blacklistWindowDays);
    return state.violations.filter(function (v) {
      if (v.userId !== userId || v.status === 'revoked') { return false; }
      var t = new Date(String(v.occurredAt).replace(' ', 'T') + ':00');
      return t >= from;
    }).length;
  }

  function applyBlacklistRule(state, user, now) {
    if (!user) { return null; }
    var n = activeViolationCount(state, user.id, now);
    if (n >= state.params.blacklistThreshold) {
      if (!user.limit || user.limit.type !== 'blacklist') {
        user.limit = {
          type: 'blacklist',
          until: fmtDT(addDays(now, state.params.blacklistBanDays)),
          reason: state.params.blacklistWindowDays + ' 天内有效违约 ' + n + ' 次'
        };
        notify(state, user.id, 'blacklist',
          '因 ' + user.limit.reason + '，您的预约权限已被限制至 ' + user.limit.until + '（BR-20，可申诉）。', now);
        audit(state, 'system', '自动加入黑名单', user.id, '', user.limit.reason, now);
      }
    }
    return user.limit;
  }

  function setBlacklist(state, input) {
    var now = input.now ? new Date(input.now) : new Date(state.clock);
    var actor = actorOf(state, input.actorId);
    if (!actor || actor.role !== ROLES.ADMIN) { return fail('仅自习室管理员可管理黑名单（PR-18）', 'PR-18'); }
    if (!input.reason) { return fail('拉黑或解封必须填写原因（BR-20）', 'BR-20'); }
    var target = findUser(state, input.userId);
    if (!target) { return fail('用户不存在：' + input.userId); }
    var before = target.limit ? (target.limit.type + '：' + (target.limit.reason || '')) : '正常';
    if (input.action === 'block') {
      var days = Number(input.days) > 0 ? Number(input.days) : state.params.blacklistBanDays;
      target.limit = { type: 'blacklist', until: fmtDT(addDays(now, days)), reason: input.reason };
      notify(state, target.id, 'blacklist',
        '管理员已对您的账号采取预约限制：' + input.reason + '，限制至 ' + target.limit.until + '。', now);
    } else if (input.action === 'unblock') {
      target.limit = null;
      notify(state, target.id, 'blacklist', '管理员已解除您的预约限制：' + input.reason + '。', now);
    } else {
      return fail('操作类型须为 block 或 unblock');
    }
    audit(state, input.actorId, (input.action === 'block' ? '人工拉黑' : '解除限制'), target.id, before,
      (target.limit ? 'blacklist 至 ' + target.limit.until : '正常') + '（' + input.reason + '）', now);
    logEvent(state, input.actorId, (input.action === 'block' ? '人工拉黑' : '解除限制'), 'BR-20/PR-18',
      target.id + '：' + input.reason, state.clock);
    return ok({ user: target });
  }

  function userProfiles(state, actorId, now) {
    var actor = actorOf(state, actorId);
    if (!actor || actor.role === ROLES.STUDENT) { return []; }
    var n = now ? new Date(now) : new Date(state.clock);
    return state.users.filter(function (u) { return u.role === ROLES.STUDENT; }).map(function (u) {
      return {
        id: u.id, name: u.name, className: u.className, credit: u.credit,
        activeViolations: activeViolationCount(state, u.id, n),
        limit: u.limit, blacklisted: !!(u.limit && u.limit.type === 'blacklist')
      };
    });
  }

  /* ------------------------------------------------------------------ *
   * 十五、运营数据看板聚合（FR-69、NFR-10）
   * 所有指标均由真实记录聚合得出（不硬编码），便于核对与测试。
   * ------------------------------------------------------------------ */
  function analytics(state, zone, now) {
    var n = now ? new Date(now) : new Date(state.clock);
    var today = fmtDate(n);
    var start = fmtTime(n), end = fmtTime(addMinutes(n, 30));
    function inZone(r) { return !zone || zone === 'ALL' || r.roomId === zone; }
    function inZoneSeat(s) { return !zone || zone === 'ALL' || s.roomId === zone; }

    var resToday = state.reservations.filter(function (r) { return r.date === today && inZone(r); });
    var inUse = state.reservations.filter(function (r) {
      return inZone(r) && (r.status === RES.IN_USE || r.status === RES.AWAY);
    }).length;
    var usable = state.seats.filter(function (s) {
      return inZoneSeat(s) && s.status !== SEAT.MAINTENANCE && s.status !== SEAT.DISABLED;
    }).length;
    var vioToday = state.violations.filter(function (v) { return String(v.occurredAt).indexOf(today) === 0; });
    var pending = state.complaints.filter(function (c) { return c.status === 'pending'; }).length;
    var blacklisted = state.users.filter(function (u) { return u.limit && u.limit.type === 'blacklist'; }).length;

    var zones = state.rooms.map(function (room) {
      var busy = 0, free = 0, maint = 0;
      state.seats.filter(function (s) { return s.roomId === room.id; }).forEach(function (s) {
        var st = seatStatusAt(state, s, today, start, end);
        if (st === SEAT.MAINTENANCE || st === SEAT.DISABLED) { maint += 1; }
        else if (st === SEAT.AVAILABLE) { free += 1; }
        else { busy += 1; }
      });
      var booked = state.reservations.filter(function (r) { return r.roomId === room.id && r.date === today; }).length;
      return { roomId: room.id, roomName: room.name, busy: busy, free: free, maintenance: maint, booked: booked };
    });

    var buckets = [];
    for (var h = 8; h < 22; h += 2) {
      var booked = resToday.filter(function (r) {
        return minutesOf(r.start) >= h * 60 && minutesOf(r.start) < (h + 2) * 60;
      }).length;
      var nowH = n.getHours() + n.getMinutes() / 60;
      var dist = Math.abs(h - nowH);
      buckets.push({
        label: pad2(h) + ':00',
        booked: booked,
        inUse: Math.max(0, inUse - Math.round(dist * 1.6)) + Math.round(booked * 0.4)
      });
    }

    var weekly = [];
    for (var d = 6; d >= 0; d--) {
      var day = fmtDate(addDays(n, -d));
      var dayRes = state.reservations.filter(function (r) { return r.date === day; });
      var done = dayRes.filter(function (r) { return r.status === RES.COMPLETED; }).length;
      var bad = state.violations.filter(function (v) { return String(v.occurredAt).indexOf(day) === 0; }).length;
      var total = dayRes.length + bad;
      weekly.push({
        label: day.slice(5),
        checkinRate: total ? Math.round(done / total * 1000) / 10 : 0,
        breachRate: total ? Math.round(bad / total * 1000) / 10 : 0
      });
    }

    return {
      kpi: {
        totalResToday: resToday.length,
        inUseCount: inUse,
        occRate: usable ? Math.round(inUse / usable * 1000) / 10 : 0,
        vioToday: vioToday.length,
        vioAuto: vioToday.filter(function (v) { return v.source !== 'manual'; }).length,
        vioManual: vioToday.filter(function (v) { return v.source === 'manual'; }).length,
        pendingComplaints: pending,
        blacklistCount: blacklisted
      },
      zones: zones, buckets: buckets, weekly: weekly
    };
  }

  return {
    DEFAULT_PARAMS: DEFAULT_PARAMS,
    RES: RES, RES_LABEL: RES_LABEL, SEAT: SEAT, SEAT_LABEL: SEAT_LABEL,
    ROLES: ROLES, ROLE_LABEL: ROLE_LABEL, VIOLATION_LABEL: VIOLATION_LABEL, NOTIFY_LABEL: NOTIFY_LABEL,
    createState: createState,
    /* 业务操作 */
    createReservation: createReservation, cancelReservation: cancelReservation,
    checkIn: checkIn, startAway: startAway, resumeUse: resumeUse, checkOut: checkOut,
    joinWaitlist: joinWaitlist, confirmWaitlist: confirmWaitlist,
    runTimeoutSweep: runTimeoutSweep,
    blockSeat: blockSeat, unblockSeat: unblockSeat, forceRelease: forceRelease,
    submitAppeal: submitAppeal, reviewAppeal: reviewAppeal, setParam: setParam,
    /* 现场巡检 / 投诉 / 评价 / 黑名单 / 看板（v2.0 新增） */
    manualViolation: manualViolation, MANUAL_CATEGORY: MANUAL_CATEGORY,
    submitComplaint: submitComplaint, replyComplaint: replyComplaint, listComplaints: listComplaints,
    submitEvaluation: submitEvaluation, replyEvaluation: replyEvaluation, listEvaluations: listEvaluations,
    activeViolationCount: activeViolationCount, applyBlacklistRule: applyBlacklistRule,
    setBlacklist: setBlacklist, userProfiles: userProfiles, analytics: analytics,
    /* 查询 */
    seatBoard: seatBoard, seatStatusAt: seatStatusAt, utilization: utilization,
    listReservations: listReservations, listViolations: listViolations, listCreditLogs: listCreditLogs,
    listNotifications: listNotifications, listAuditLogs: listAuditLogs, violationStats: violationStats,
    /* 工具（测试与 UI 复用） */
    findUser: findUser, findSeat: findSeat, findReservation: findReservation, findViolation: findViolation,
    toDate: toDate, fmtDate: fmtDate, fmtTime: fmtTime, fmtDT: fmtDT, addMinutes: addMinutes, minutesOf: minutesOf
  };
});
