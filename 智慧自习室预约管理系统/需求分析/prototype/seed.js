/**
 * 原型模拟数据（教学模拟）
 * =====================
 * 说明：
 *  1. createSeedState(now) 显式接收基准时间；不传时使用本机当前时间。
 *  2. 所有日期与预约 ID 都从该基准派生：演示时随本机日期平移，测试时用 FIXED_NOW 固定。
 *  3. 传入固定时钟时使用标准演示场景（09:00—10:00 使用中、14:00—15:00 待签到），
 *     保证 A.5/A.6 的走查场景与自动化测试可重复；不传时使用智能时段，避免演示数据一打开就超时。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) { module.exports = factory(require('./domain.js')); }
  else { root.SRSSeed = factory(root.SRSCore); }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (core) {
  'use strict';

  function pad2(n) { return n < 10 ? '0' + n : '' + n; }

  // 纯函数：在给定日期上加减天数，返回新的 Date（不修改入参）
  function addDays(baseDate, deltaDays) {
    var d = new Date(baseDate.getTime());
    d.setDate(d.getDate() + (deltaDays || 0));
    return d;
  }

  // 纯函数：格式化为 YYYY-MM-DD
  function formatDate(d) { return core.fmtDate(d); }

  // 获取给定时间的 ISO 形式 YYYY-MM-DDTHH:mm:00（不传时由调用方显式提供）
  function getLocalISO(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
           'T' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':00';
  }

  // 获取相对于基准日偏移 deltaDays 天的日期字符串 (YYYY-MM-DD)
  function dayStr(deltaDays, baseDate) {
    return formatDate(addDays(baseDate, deltaDays));
  }

  // 根据本机当前时间，计算适合演示的今日时段（确保不会一上来就判定超时违约）
  function getSmartSlot(now, offsetHalfHours, durationMinutes) {
    var totalMin = now.getHours() * 60 + now.getMinutes();
    var rounded = Math.ceil((totalMin + 5) / 30) * 30 + (offsetHalfHours || 0) * 30;
    // 限制在开放时段 08:00 ~ 21:00 内
    var startMin = Math.max(8 * 60, Math.min(21 * 60, rounded));
    var endMin = Math.min(22 * 60 + 30, startMin + (durationMinutes || 60));
    if (endMin - startMin < 30) { startMin = endMin - 30; }
    var hm = function (m) { return pad2(Math.floor(m / 60)) + ':' + pad2(m % 60); };
    return { start: hm(startMin), end: hm(endMin), startMin: startMin };
  }

  function seat(roomId, no, type, capacity) {
    return {
      id: roomId + '-' + no, roomId: roomId, type: type, capacity: capacity || 1,
      status: core.SEAT.AVAILABLE, blockReason: '', blockFrom: '', blockTo: ''
    };
  }

  function createSeedState(customNow) {
    var pinned = !!customNow;
    var now = customNow ? new Date(customNow) : new Date();
    var today = dayStr(0, now);
    var tomorrow = dayStr(1, now);
    var clockStart = getLocalISO(now);
    var todayCompact = today.replace(/-/g, '');

    var rooms = [
      { id: 'A101', name: 'A101 静音自习室', location: '第一教学楼 1F', seatCount: 9, status: 'open' },
      { id: 'B201', name: 'B201 综合自习室', location: '第二教学楼 2F', seatCount: 8, status: 'open' },
      { id: 'C301', name: 'C301 讨论区', location: '第三教学楼 3F', seatCount: 4, status: 'open' }
    ];

    var seats = [];
    ['001', '002', '003', '004'].forEach(function (n) { seats.push(seat('A101', n, '静音区', 1)); });
    ['005', '006', '007', '008'].forEach(function (n) { seats.push(seat('A101', n, '电源位', 1)); });
    ['001', '002', '003', '004', '005', '006'].forEach(function (n) { seats.push(seat('B201', n, '普通位', 1)); });
    ['007', '008'].forEach(function (n) { seats.push(seat('B201', n, '电源位', 1)); });
    ['D01', 'D02', 'D03', 'D04'].forEach(function (n) { seats.push(seat('C301', n, '讨论间', 6)); });
    seats.push(seat('A101', '009', '电源位', 1));

    seats.forEach(function (s) {
      var n = parseInt(String(s.id).slice(-3), 10) || 0;
      s.hasPower = s.type === '电源位' || n % 2 === 0;
      s.isWindow = n % 3 === 0;
      s.hasLamp = s.type === '静音区' || n % 2 === 1;
      s.zoneType = s.type;
    });

    /* A101-009 预置为维护中（相对于本机今天 ~ 未来2天） */
    var maintenanceSeat = seats.filter(function (s) { return s.id === 'A101-009'; })[0];
    maintenanceSeat.status = core.SEAT.MAINTENANCE;
    maintenanceSeat.blockReason = '座椅气压杆损坏待更换（预置数据）';
    maintenanceSeat.blockFrom = today;
    maintenanceSeat.blockTo = dayStr(2, now);

    var users = [
      { id: 'S2023001', name: '张小明', role: 'student', className: '计算机 2301', credit: 100, limit: null, consecutiveNoShow: 0, status: '正常' },
      { id: 'S2023002', name: '李华', role: 'student', className: '计算机 2301', credit: 55, limit: { type: 'no_show', until: dayStr(-2, now) + ' 00:00' }, consecutiveNoShow: 3, status: '正常' },
      { id: 'S2023003', name: '王芳', role: 'student', className: '计算机 2302', credit: 100, limit: null, consecutiveNoShow: 0, status: '正常' },
      { id: 'A9001', name: '王老师', role: 'admin', className: '自习室管理组', credit: 100, limit: null, consecutiveNoShow: 0, status: '正常' },
      { id: 'Y0001', name: '赵工', role: 'sysadmin', className: '信息中心', credit: 100, limit: null, consecutiveNoShow: 0, status: '正常' },
      { id: 'T7001', name: '陈老师', role: 'teacher', className: '计算机 2301', credit: 100, limit: null, consecutiveNoShow: 0, status: '正常' },
      { id: 'S2023005', name: '赵敏', role: 'student', className: '计算机 2302', credit: 82, limit: { type: 'blacklist', until: dayStr(4, now) + ' 10:00', reason: '30 天内有效违约 3 次' }, consecutiveNoShow: 1, status: '正常' }
    ];

    // 固定时钟（测试/证据复现）使用标准演示场景；真实时钟使用智能时段，避免演示数据一打开就超时
    var activeSlot = pinned
      ? { start: '09:00', end: '10:00' }
      : getSmartSlot(now, -1, 120); // 当前正在使用中的时段
    var pendingSlot = pinned
      ? { start: '14:00', end: '15:00' }
      : getSmartSlot(now, 2, 60);   // 稍后待签到的时段

    var reservations = [
      {
        id: 'R' + todayCompact + '-01', userId: 'S2023003', seatId: 'B201-002', roomId: 'B201',
        date: today, start: activeSlot.start, end: activeSlot.end, status: core.RES.IN_USE,
        createdAt: dayStr(-1, now) + ' 20:12', checkInAt: today + ' ' + activeSlot.start, awayAt: '', awayDeadlineAt: '',
        checkoutAt: '', credential: 'CRED-SEED-1', endReason: '', source: 'self'
      },
      {
        id: 'R' + todayCompact + '-02', userId: 'S2023001', seatId: 'A101-003', roomId: 'A101',
        date: today, start: pendingSlot.start, end: pendingSlot.end, status: core.RES.PENDING,
        createdAt: dayStr(-1, now) + ' 21:03', checkInAt: '', awayAt: '', awayDeadlineAt: '',
        checkoutAt: '', credential: '', endReason: '', source: 'self'
      },
      {
        id: 'R' + todayCompact + '-03', userId: 'S2023001', seatId: 'A101-001', roomId: 'A101',
        date: dayStr(-1, now), start: '14:00', end: '15:00', status: core.RES.COMPLETED,
        createdAt: dayStr(-2, now) + ' 19:40', checkInAt: dayStr(-1, now) + ' 13:52', awayAt: '', awayDeadlineAt: '',
        checkoutAt: dayStr(-1, now) + ' 14:52', credential: 'CRED-SEED-0', endReason: 'normal', source: 'self'
      }
    ];

    /* 李华的 6 条历史违规（自动偏移到近 20 天内） */
    var huaViolations = [];
    var creditLogs = [];
    var history = [
      { type: 'no_show', at: dayStr(-19, now) + ' 14:20', points: -10 },
      { type: 'no_show', at: dayStr(-15, now) + ' 09:20', points: -10 },
      { type: 'no_show', at: dayStr(-11, now) + ' 10:20', points: -10 },
      { type: 'away_timeout', at: dayStr(-8, now) + ' 15:40', points: -5 },
      { type: 'overstay', at: dayStr(-6, now) + ' 17:05', points: -5 },
      { type: 'late_cancel', at: dayStr(-4, now) + ' 13:55', points: -5 }
    ];
    var running = 100;
    history.forEach(function (h, idx) {
      var vid = 'VIO-S' + (idx + 1);
      huaViolations.push({
        id: vid, userId: 'S2023002', reservationId: 'RHIST-' + (idx + 1),
        type: h.type, typeLabel: core.VIOLATION_LABEL[h.type], points: h.points,
        occurredAt: h.at, status: 'active', appealReason: '', appealAt: '', reviewNote: ''
      });
      running += h.points;
      creditLogs.push({
        id: 'CL-S' + (idx + 1), userId: 'S2023002', delta: h.points, after: running,
        reason: '违规：' + core.VIOLATION_LABEL[h.type] + '（' + vid + '）', violationId: vid,
        operator: '系统', at: h.at
      });
    });

    /* 近 6 天历史数据（用于运营看板近 7 日折线图） */
    var hist = [
      ['R-H011', 'S2023001', 'A101-001', dayStr(-6, now), '09:00', '11:00', 'completed'],
      ['R-H018', 'S2023003', 'B201-003', dayStr(-5, now), '14:00', '16:00', 'completed'],
      ['R-H019', 'S2023005', 'A101-006', dayStr(-5, now), '19:00', '21:00', 'no_show'],
      ['R-H024', 'S2023001', 'A101-002', dayStr(-4, now), '10:00', '12:00', 'completed'],
      ['R-H041', 'S2023005', 'A101-007', dayStr(-4, now), '09:00', '11:00', 'no_show'],
      ['R-H033', 'S2023003', 'B201-001', dayStr(-3, now), '15:00', '17:00', 'completed'],
      ['R-H052', 'S2023005', 'C301-D01', dayStr(-2, now), '16:00', '18:00', 'completed'],
      ['R-H055', 'S2023002', 'A101-005', dayStr(-2, now), '10:00', '12:00', 'cancelled'],
      ['R-H061', 'S2023002', 'A101-005', dayStr(-1, now), '10:00', '12:00', 'no_show'],
      ['R-H064', 'S2023003', 'B201-004', dayStr(-1, now), '14:00', '16:00', 'completed']
    ];
    hist.forEach(function (h) {
      var done = h[6] === 'completed';
      reservations.push({
        id: h[0], userId: h[1], seatId: h[2], roomId: String(h[2]).split('-')[0],
        date: h[3], start: h[4], end: h[5], status: h[6],
        createdAt: h[3] + ' 08:00',
        checkInAt: done ? h[3] + ' ' + h[4] : '',
        awayAt: '', awayDeadlineAt: '',
        checkoutAt: done ? h[3] + ' ' + h[5] : '',
        credential: '', endReason: done ? 'normal' : (h[6] === 'no_show' ? 'no_show' : 'cancel_free'),
        source: 'self'
      });
    });

    /* 赵敏（黑名单示例）：近 5 天内 3 次有效违约 */
    var zhaoViolations = [
      { id: 'VIO-Z01', userId: 'S2023005', reservationId: 'R-H019', type: 'no_show',
        typeLabel: core.VIOLATION_LABEL.no_show, points: -10, occurredAt: dayStr(-5, now) + ' 19:20',
        status: 'active', source: 'auto', appealReason: '', reviewNote: '' },
      { id: 'VIO-Z02', userId: 'S2023005', reservationId: 'R-H041', type: 'no_show',
        typeLabel: core.VIOLATION_LABEL.no_show, points: -10, occurredAt: dayStr(-4, now) + ' 09:20',
        status: 'active', source: 'auto', appealReason: '', reviewNote: '' },
      { id: 'VIO-Z03', userId: 'S2023005', reservationId: 'R-H052', type: 'overstay',
        typeLabel: core.VIOLATION_LABEL.overstay, points: -5, occurredAt: dayStr(-2, now) + ' 18:05',
        status: 'active', source: 'manual', appealReason: '',
        reviewNote: '管理员巡检：到点未签退且人已离开，现场登记', operator: '王老师' }
    ];

    var historyResViolations = [
      { id: 'VIO-H01', userId: 'S2023002', reservationId: 'R-H061', type: 'no_show',
        typeLabel: core.VIOLATION_LABEL.no_show, points: -10, occurredAt: dayStr(-1, now) + ' 10:20',
        status: 'active', source: 'auto', appealReason: '', reviewNote: '' },
      { id: 'VIO-H02', userId: 'S2023003', reservationId: 'R-H033', type: 'manual',
        typeLabel: '手动登记 — 人走留物恶意占用座位', points: -5, occurredAt: dayStr(-3, now) + ' 16:40',
        status: 'active', source: 'manual', appealReason: '',
        reviewNote: '巡检发现有物品占座超 1 小时，现场清理并登记', operator: '王老师' }
    ];

    var seedComplaints = [
      { id: 'CMP-01', userId: 'S2023001', userName: '张小明',
        seatId: 'A101-003', seatCode: 'A101-003', type: '他人恶意占座',
        description: 'A101-003 座位上只有一本书，使用人已离开超过 1 小时未办理暂离，希望管理员核实清理。',
        status: 'pending', feedback: '', feedbackBy: '', feedbackAt: '', linkedViolationId: null,
        createdAt: dayStr(-1, now) + ' 15:10' },
      { id: 'CMP-02', userId: 'S2023003', userName: '王芳',
        seatId: 'B201-008', seatCode: 'B201-008', type: '座位设施损坏',
        description: 'B201-008 的电源插座松动断电，台灯无法点亮。',
        status: 'closed',
        feedback: '管理员已现场核查确认插座模块故障，已将该座位设为维护停用并报修，感谢反馈！',
        feedbackBy: '王老师', feedbackAt: dayStr(-2, now) + ' 16:30', linkedViolationId: null,
        createdAt: dayStr(-2, now) + ' 11:20' }
    ];

    var seedEvaluations = [
      { id: 'EVAL-01', reservationId: 'R' + todayCompact + '-03', userId: 'S2023001', userName: '张小明',
        seatId: 'A101-001', rating: 5, tags: '插座供电稳定, 靠窗采光好, 环境安静',
        content: 'A101 静音自习室氛围很好，靠窗位置视野开阔，签到后台灯自动通电很方便。',
        reply: '感谢同学的肯定，我们会继续维护 A101 静音区环境。',
        replyBy: '王老师', replyAt: dayStr(-1, now) + ' 17:10', createdAt: dayStr(-1, now) + ' 16:58' }
    ];

    var allViolations = huaViolations.concat(zhaoViolations).concat(historyResViolations);
    var state = core.createState({
      now: clockStart,
      rooms: rooms, seats: seats, users: users,
      reservations: reservations,
      violations: allViolations,
      creditLogs: creditLogs,
      complaints: seedComplaints, evaluations: seedEvaluations
    });
    state.seq = {
      reservation: reservations.length, violation: allViolations.length,
      waitlist: 0, notification: 0, credit: creditLogs.length, audit: 0, event: 0,
      complaint: seedComplaints.length, evaluation: seedEvaluations.length
    };
    state.usedCredentials = ['CRED-SEED-1', 'CRED-SEED-0'];
    // 供页面默认值、测试断言与证据记录使用；随本次种子基准固定，不再读真实时间
    state.TODAY = today;
    state.TOMORROW = tomorrow;
    state.CLOCK_START = clockStart;
    return state;
  }

  return {
    createSeedState: createSeedState,
    getLocalISO: getLocalISO,
    formatDate: formatDate,
    addDays: addDays
  };
});