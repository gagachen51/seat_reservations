/**
 * 智慧自习室预约管理系统 · 交互验证原型 v2.0 —— 界面逻辑
 * ==================================================================
 * 设计目标（针对上一版的不足）：
 *   1) 功能对齐课程参考原型：座位属性筛选 / 综合查询 / 现场巡检与手动违约 /
 *      评价 / 投诉反馈 / 信用与黑名单 / 运营看板（10 秒自动刷新 + 容错）
 *   2) 易用性优先：每页顶部有“这一页做什么 / 怎么用”的引导条，按钮标注对应规则编号
 *   3) 视觉遵循 Apple 风格：清晰层级、克制色彩、充裕留白、克制动效
 * 说明：界面只负责展示与调用，**全部业务判断仍在 domain.js**（页面不重复实现规则）。
 */
(function () {
  'use strict';

  var core = window.SRSCore;
  var seed = window.SRSSeed;
  // 测试/证据复现时由 tests/_fixtures.cjs 注入固定时钟；正常打开页面时走真实时间
  var fixedNow = window.SRS_FIXED_NOW || '';
  var state = seed.createSeedState(fixedNow || undefined);
  var credSeq = 0;
  var clockOffsetMs = 0; // 用于支持 "+30分钟 / +1小时" 调试偏移，默认 0 即完全等于本机时间

  function pad2(n) { return n < 10 ? '0' + n : '' + n; }
  function minOf(t) { var p = String(t).split(':'); return parseInt(p[0], 10) * 60 + parseInt(p[1], 10); }
  function hm(m) { return pad2(Math.floor(m / 60)) + ':' + pad2(m % 60); }
  function endOf(start, minutes) { return hm(minOf(start) + Number(minutes)); }

  // 同步基准时间（测试用固定时钟；演示用本机时间）并叠加手动快进偏移量
  function syncClockToLocal() {
    var base = fixedNow ? new Date(fixedNow).getTime() : Date.now();
    var d = new Date(base + clockOffsetMs);
    state.clock = d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + 'T' +
                  pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
    return d;
  }

  // 根据当前系统时间，智能计算默认的预约日期和下一个半点/整点开始时间
  function getDefaultQuerySlot() {
    var d = syncClockToLocal();
    var nextSlotMin = Math.ceil((d.getHours() * 60 + d.getMinutes() + 1) / 30) * 30;
    var dateStr = core.fmtDate(d);
    if (nextSlotMin < 7 * 60) {
      nextSlotMin = 7 * 60; // 早于07:00，默认选今天07:00
    } else if (nextSlotMin > 21 * 60 + 30) {
      // 晚于21:30（加1小时超过22:30闭馆），自动顺延到明天08:00
      var tomorrow = new Date(d.getTime() + 86400000);
      dateStr = core.fmtDate(tomorrow);
      nextSlotMin = 8 * 60;
    }
    return { date: dateStr, start: hm(nextSlotMin) };
  }

  var initSlot = getDefaultQuerySlot();

  var ui = {
    userId: 'S2023001',
    tab: 'seats',
    selectedSeat: null,
    query: { date: initSlot.date, start: initSlot.start, minutes: 60, roomId: '', status: '', hasPower: false, isWindow: false, hasLamp: false },
    resStatus: '', vioSource: '', vioStatus: '', keyword: '',
    analyticsZone: 'ALL',
    autoRefresh: true, simulateNetError: false,
    countdown: 10, lastSync: '—', lastSnapshot: null, snapshotAt: '—'
  };

  function $(id) { return document.getElementById(id); }
  function clockText() {
    syncClockToLocal();
    return String(state.clock).replace('T', ' ').slice(0, 16);
  }
  function advance(minutes) {
    clockOffsetMs += minutes * 60000;
    syncClockToLocal();
  }
  function user() { return core.findUser(state, ui.userId); }
  function isAdminView() { var u = user(); return !!u && (u.role === 'admin' || u.role === 'sysadmin'); }
  function canOperate() { var u = user(); return !!u && u.role === 'admin'; }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }
  function badge(text, cls) { return '<span class="badge ' + (cls || '') + '">' + esc(text) + '</span>'; }
  function rule(code) { return '<span class="kbd">' + esc(code) + '</span>'; }

  function toast(msg, type) {
    var box = $('toast-wrap');
    var el = document.createElement('div');
    el.className = 'toast' + (type === 'ok' ? ' ok' : type === 'err' ? ' err' : '');
    el.innerHTML = esc(msg);
    box.appendChild(el);
    window.setTimeout(function () {
      el.style.opacity = '0';
      window.setTimeout(function () { el.remove(); }, 250);
    }, 4200);
  }

  /** 统一处理领域操作返回值：成功提示 / 失败提示（带规则编号） */
  function consume(res, okMsg) {
    if (res && res.ok) { toast(okMsg || '操作成功', 'ok'); render(); return true; }
    toast((res && res.message ? res.message : '操作失败') +
          (res && res.rule ? '（规则 ' + res.rule + '）' : ''), 'err');
    render();
    return false;
  }
  function cred() { credSeq += 1; return { code: 'CRED-UI-' + credSeq + '-' + Date.now(), issuedAt: state.clock }; }
  function prompt2(title, value) { return window.prompt(title, value || '') || ''; }

  var STATUS_BADGE = {
    pending: ['待签到', 'blue'], in_use: ['使用中', 'purple'], away: ['暂离中', 'orange'],
    completed: ['已完成', 'green'], cancelled: ['已取消', ''], no_show: ['已违约', 'red'], released: ['已释放', 'red']
  };
  function resBadge(st) {
    var b = STATUS_BADGE[st] || [st, ''];
    return badge(b[0], b[1]);
  }
  var SEAT_BADGE = {
    available: ['空闲', 'green'], reserved: ['已预约', 'blue'], occupied: ['使用中', 'purple'],
    held: ['暂离保留', 'orange'], maintenance: ['维护停用', 'red'], disabled: ['停用', '']
  };
  function seatBadge(st) {
    var b = SEAT_BADGE[st] || [st, ''];
    return badge(b[0], b[1]);
  }

  /* ==================== 页面 1：座位资源与预约 ==================== */
  function renderSeats() {
    var q = ui.query, end = endOf(q.start, q.minutes), u = user();
    var board = core.seatBoard(state, q.date, q.start, end).filter(function (b) {
      if (q.roomId && b.roomId !== q.roomId) { return false; }
      if (q.status && b.status !== q.status) { return false; }
      var s = core.findSeat(state, b.id) || {};
      if (q.hasPower && !s.hasPower) { return false; }
      if (q.isWindow && !s.isWindow) { return false; }
      if (q.hasLamp && !s.hasLamp) { return false; }
      return true;
    });
    $('seat-count-hint').textContent = '符合条件 ' + board.length + ' 个座位 · ' + q.date + ' ' + q.start + '–' + end;
    $('selected-seat').textContent = ui.selectedSeat || '（未选择）';
    var host = $('seat-grid');
    if (!board.length) {
      host.innerHTML = '<div class="card empty"><span class="icon">🔍</span>没有符合当前筛选条件的座位。<br>建议减少属性条件，或换一个时段再试。</div>';
      return;
    }
    host.innerHTML = board.map(function (b) {
      var s = core.findSeat(state, b.id) || {};
      var attrs = [];
      if (s.hasPower) { attrs.push('⚡ 电源'); }
      if (s.isWindow) { attrs.push('🪟 靠窗'); }
      if (s.hasLamp) { attrs.push('💡 台灯'); }
      var occ = state.reservations.filter(function (r) {
        return r.seatId === b.id && ['pending', 'in_use', 'away'].indexOf(r.status) >= 0;
      })[0];
      var who = occ ? (isAdminView() ? ((core.findUser(state, occ.userId) || {}).name || '') : '（已占用）') : '';
      var actions;
      if (b.status === 'available' && u.role === 'student') {
        actions = '<button class="btn sm primary block" data-act="pick-seat" data-seat="' + b.id + '">选择该座位</button>';
      } else if (b.status === 'maintenance') {
        actions = '<button class="btn sm block" disabled>维护停用不可预约</button>';
      } else {
        actions = '<button class="btn sm block" disabled>该时段不可用</button>';
      }
      var adminTools = canOperate()
        ? '<div class="row" style="margin-top:8px"><button class="btn sm ghost" data-act="toggle-maint" data-seat="' + b.id + '">' +
          (b.status === 'maintenance' ? '解除维护' : '设为维护') + '</button></div>'
        : '';
      return '<div class="seat' + (b.status === 'available' ? '' : ' busy') + (ui.selectedSeat === b.id ? ' selected' : '') + '">' +
        '<div class="row" style="justify-content:space-between"><span class="code">' + esc(b.id) + '</span>' + seatBadge(b.status) + '</div>' +
        '<div class="meta">' + esc(b.roomName || b.roomId) + ' · ' + esc(b.type) + (who ? ' · 使用人 ' + esc(who) : '') + '</div>' +
        '<div class="attrs">' + (attrs.length ? attrs.map(function (a) { return '<span class="chip">' + a + '</span>'; }).join('') : '<span class="chip">标准座位</span>') + '</div>' +
        actions + adminTools + '</div>';
    }).join('');
  }

  /* ==================== 页面 2：我的预约与签到 ==================== */
  function renderMyRes() {
    var u = user();
    var list = core.listReservations(state, ui.userId).slice().sort(function (a, b) {
      return (a.date + a.start) < (b.date + b.start) ? 1 : -1;
    });
    var host = $('myres-list');
    if (!list.length) {
      host.innerHTML = '<div class="card empty"><span class="icon">📅</span>暂无预约记录。请到「1 · 座位资源与预约」选择座位并确认预约。</div>';
      return;
    }
    host.innerHTML = list.map(function (r) {
      var acts = [], student = u.role === 'student';
      if (r.status === 'pending' && student) {
        acts.push('<button class="btn sm green" data-act="checkin" data-id="' + r.id + '">签到</button>');
        acts.push('<button class="btn sm" data-act="cancel" data-id="' + r.id + '">取消预约</button>');
        acts.push('<button class="btn sm danger" data-act="sim-noshow" data-id="' + r.id + '">模拟超时未签到 ' + rule('ST-02') + '</button>');
      } else if (r.status === 'in_use' && student) {
        acts.push('<button class="btn sm warn" data-act="away" data-id="' + r.id + '">暂时离开 ' + rule('ST-05') + '</button>');
        acts.push('<button class="btn sm primary" data-act="checkout" data-id="' + r.id + '">签退 ' + rule('ST-08') + '</button>');
      } else if (r.status === 'away' && student) {
        acts.push('<button class="btn sm green" data-act="resume" data-id="' + r.id + '">返座确认 ' + rule('ST-06') + '</button>');
        acts.push('<button class="btn sm danger" data-act="sim-away-timeout" data-id="' + r.id + '">模拟暂离超时 ' + rule('ST-07') + '</button>');
      } else if (r.status === 'completed' && student) {
        var done = state.evaluations.some(function (e) { return e.reservationId === r.id; });
        acts.push(done ? badge('已评价', 'green')
          : '<button class="btn sm primary" data-act="open-eval" data-id="' + r.id + '">提交评价 ' + rule('BR-21') + '</button>');
      }
      var owner = core.findUser(state, r.userId) || {};
      return '<div class="item">' +
        '<div><div class="title">' + esc(r.seatId) + ' · ' + esc(r.date + ' ' + r.start + '-' + r.end) + ' ' + resBadge(r.status) +
        '<span class="kbd">' + esc(r.id) + '</span></div>' +
        '<div class="sub">预约人 ' + esc(owner.name || r.userId) + ' ｜ 创建 ' + esc(r.createdAt) +
        (r.checkInAt ? ' ｜ 签到 ' + esc(r.checkInAt) : '') +
        (r.awayDeadlineAt ? ' ｜ 暂离保留至 ' + esc(r.awayDeadlineAt) : '') +
        (r.checkoutAt ? ' ｜ 签退 ' + esc(r.checkoutAt) : '') +
        (r.endReason ? ' ｜ 结束原因 ' + esc(r.endReason) : '') + '</div></div>' +
        '<div class="btn-row">' + acts.join('') + '</div></div>';
    }).join('');
  }

  /* ==================== 页面 3：预约与违规综合查询 ==================== */
  function renderQueries() {
    var kw = (ui.keyword || '').toLowerCase(), u = user();
    var resList = core.listReservations(state, ui.userId).filter(function (r) {
      if (ui.resStatus && r.status !== ui.resStatus) { return false; }
      var o = core.findUser(state, r.userId) || {};
      var hay = (r.id + r.seatId + (o.name || '') + (o.id || '')).toLowerCase();
      return !kw || hay.indexOf(kw) >= 0;
    }).sort(function (a, b) { return (a.date + a.start) < (b.date + b.start) ? 1 : -1; });
    $('query-res-tbody').innerHTML = resList.length ? resList.map(function (r) {
      var o = core.findUser(state, r.userId) || {};
      var who = u.role === 'student' ? '本人' : (esc(o.name || '') + '（' + esc(o.id || '') + '）');
      return '<tr><td class="mono">' + esc(r.id) + '</td><td>' + who + '</td><td>' + esc(r.seatId) + '</td>' +
        '<td>' + esc(r.date + ' ' + r.start + '-' + r.end) + '</td>' +
        '<td>' + esc(r.checkInAt || '—') + ' / ' + esc(r.checkoutAt || '—') + '</td>' +
        '<td>' + resBadge(r.status) + '</td></tr>';
    }).join('') : '<tr><td colspan="6" class="empty">没有匹配的预约记录</td></tr>';

    var vioList = core.listViolations(state, ui.userId).filter(function (v) {
      if (ui.vioSource === 'manual' && v.source !== 'manual') { return false; }
      if (ui.vioSource === 'auto' && v.source === 'manual') { return false; }
      if (ui.vioStatus && v.status !== ui.vioStatus) { return false; }
      var o = core.findUser(state, v.userId) || {};
      var hay = (v.id + (v.reservationId || '') + (v.typeLabel || '') + (v.reviewNote || '') + (o.name || '')).toLowerCase();
      return !kw || hay.indexOf(kw) >= 0;
    }).sort(function (a, b) { return a.occurredAt < b.occurredAt ? 1 : -1; });
    $('query-vio-tbody').innerHTML = vioList.length ? vioList.map(function (v) {
      var o = core.findUser(state, v.userId) || {};
      var src = v.source === 'manual' ? badge('管理员手动', 'orange') : badge('系统自动', 'red');
      var st = v.status === 'revoked' ? badge('已撤销', 'green')
        : v.status === 'appealing' ? badge('申诉中', 'orange') : badge('生效', 'red');
      return '<tr><td class="mono">' + esc(v.id) + '</td><td>' + esc(o.name || v.userId) + '</td>' +
        '<td>' + esc(v.reservationId || '—') + '</td><td>' + src + '</td>' +
        '<td>' + esc(v.typeLabel || v.type) + '（' + esc(v.points) + ' 分）<br>' + st + '</td>' +
        '<td>' + esc(v.reviewNote || '（系统按规则判定）') + '</td><td class="mono">' + esc(v.occurredAt) + '</td></tr>';
    }).join('') : '<tr><td colspan="7" class="empty">没有匹配的违规记录</td></tr>';
  }

  /* ==================== 页面 4：现场巡检与手动违约登记 ==================== */
  function renderAdmin() {
    $('admin-guard').hidden = canOperate();
    var kpi = core.analytics(state, ui.analyticsZone, new Date(state.clock)).kpi;
    $('admin-stats').innerHTML = [
      ['今日手动违约登记', kpi.vioManual, 'BR-18 / PR-17'],
      ['今日系统自动违约', kpi.vioAuto, 'BR-04 / BR-05 / BR-06'],
      ['待处理投诉单', kpi.pendingComplaints, 'FR-66 / BR-19'],
      ['黑名单受限人数', kpi.blacklistCount, 'BR-20（候选规则）']
    ].map(function (x) {
      return '<div class="kpi"><div class="label">' + x[0] + '</div><div class="value">' + x[1] + '</div><div class="foot">' + x[2] + '</div></div>';
    }).join('');

    var now = clockText();
    var board = core.seatBoard(state, now.slice(0, 10), now.slice(11, 16), endOf(now.slice(11, 16), 30));
    $('admin-seat-board').innerHTML = board.map(function (b) {
      var occ = state.reservations.filter(function (r) {
        return r.seatId === b.id && ['pending', 'in_use', 'away'].indexOf(r.status) >= 0;
      })[0];
      var o = occ ? core.findUser(state, occ.userId) : null;
      var acts = '';
      if (occ && canOperate()) {
        acts = '<div class="btn-row" style="margin-top:8px">' +
          '<button class="btn sm danger" data-act="manual-vio" data-id="' + occ.id + '" data-mode="release">违约并释放座位</button>' +
          '<button class="btn sm warn" data-act="manual-vio" data-id="' + occ.id + '" data-mode="warn">仅登记警告</button></div>';
      } else if (occ) {
        acts = '<div class="sub" style="margin-top:6px">（学生视角仅显示占用状态，不含实名）</div>';
      }
      var who = occ ? (isAdminView() && o ? '使用人 ' + esc(o.name) + '（' + esc(o.id) + '）' : '已占用（脱敏）') : '当前无人使用';
      return '<div class="seat' + (b.status === 'available' ? '' : ' busy') + '">' +
        '<div class="row" style="justify-content:space-between"><span class="code">' + esc(b.id) + '</span>' + seatBadge(b.status) + '</div>' +
        '<div class="meta">' + esc(b.roomName || '') + ' · ' + esc(b.type) + '</div>' +
        '<div class="sub">' + who + '</div>' + acts + '</div>';
    }).join('');
  }

  /* ==================== 页面 5：投诉 · 信用与黑名单 · 评价 ==================== */
  function renderCommunity() {
    var admin = canOperate();

    var cps = core.listComplaints(state, ui.userId).slice().sort(function (a, b) { return a.createdAt < b.createdAt ? 1 : -1; });
    $('complaints-list').innerHTML = cps.length ? cps.map(function (c) {
      var st = c.status === 'closed' ? badge('已办结', 'green') : badge('待处理', 'orange');
      var fb = c.feedback
        ? '<div class="sub" style="margin-top:8px;padding:8px 10px;background:var(--blue-soft);border-radius:10px">' +
          '<b>管理员反馈（' + esc(c.feedbackBy) + ' ' + esc(c.feedbackAt) + '）：</b><br>' + esc(c.feedback) +
          (c.linkedViolationId ? '<br>' + rule(c.linkedViolationId) + ' 已联动违约登记' : '') + '</div>'
        : '';
      var act = (admin && c.status !== 'closed')
        ? '<button class="btn sm primary" data-act="open-feedback" data-id="' + c.id + '">填写反馈并办结</button>' : '';
      return '<div class="item' + (c.status === 'pending' ? ' warn' : '') + '">' +
        '<div><div class="title">' + esc(c.type) + ' ' + st + '<span class="kbd">' + esc(c.id) + '</span></div>' +
        '<div class="sub">' + (admin ? esc(c.userName) + ' ｜ ' : '') + '座位 ' + esc(c.seatCode || '—') + ' ｜ ' + esc(c.createdAt) + '</div>' +
        '<div class="sub" style="margin-top:6px">' + esc(c.description) + '</div>' + fb + '</div>' +
        '<div class="btn-row">' + act + '</div></div>';
    }).join('') : '<div class="card empty"><span class="icon">📮</span>暂无投诉单</div>';

    var rows = core.userProfiles(state, ui.userId, new Date(state.clock));
    $('users-tbody').innerHTML = rows.length ? rows.map(function (r) {
      var st = r.blacklisted ? badge('黑名单限制中', 'red') : (r.limit ? badge('受限', 'orange') : badge('正常', 'green'));
      var reason = r.limit
        ? esc(r.limit.reason || '') + (r.limit.until ? '，至 ' + esc(r.limit.until) : '')
        : '无';
      var act = admin
        ? (r.limit
            ? '<button class="btn sm" data-act="unblock" data-user="' + r.id + '">解封</button>'
            : '<button class="btn sm danger" data-act="block" data-user="' + r.id + '">拉黑</button>')
        : '—';
      return '<tr><td class="mono">' + esc(r.id) + '<br>' + esc(r.name) + '</td><td>' + esc(r.className) + '</td>' +
        '<td>' + r.activeViolations + ' 次</td><td>' + st + '<br>信用 ' + r.credit + ' 分</td>' +
        '<td>' + reason + '</td><td>' + act + '</td></tr>';
    }).join('') : '<tr><td colspan="6" class="empty">按角色权限矩阵（' + 'PR-11' + '），学生不可查看他人信用档案</td></tr>';

    var evs = core.listEvaluations(state, ui.userId).slice().sort(function (a, b) { return a.createdAt < b.createdAt ? 1 : -1; });
    $('evals-list').innerHTML = evs.length ? evs.map(function (e) {
      var stars = '★★★★★'.slice(0, e.rating) + '☆☆☆☆☆'.slice(0, 5 - e.rating);
      var kind = e.rating <= 2 ? ['差评', 'red'] : e.rating >= 4 ? ['好评', 'green'] : ['一般', ''];
      var reply = e.reply
        ? '<div class="sub" style="margin-top:8px;padding:8px 10px;background:var(--green-soft);border-radius:10px">' +
          '<b>管理员回复（' + esc(e.replyBy) + '）：</b><br>' + esc(e.reply) + '</div>'
        : '';
      var act = (admin && !e.reply)
        ? '<button class="btn sm primary" data-act="reply-eval" data-id="' + e.id + '">回复评价</button>' : '';
      return '<div class="item"><div><div class="title">' + esc(e.seatId) +
        ' <span style="color:#b25000;letter-spacing:2px">' + stars + '</span> ' + badge(kind[0], kind[1]) + '</div>' +
        '<div class="sub">' + (admin ? esc(e.userName) + ' ｜ ' : '') + esc(e.createdAt) + (e.tags ? ' ｜ ' + esc(e.tags) : '') + '</div>' +
        '<div class="sub" style="margin-top:6px">' + esc(e.content) + '</div>' + reply + '</div>' +
        '<div class="btn-row">' + act + '</div></div>';
    }).join('') : '<div class="card empty"><span class="icon">⭐</span>暂无评价。预约完成后可在「2 · 我的预约」提交评价。</div>';
  }

  /* ==================== 页面 6：运营数据看板（纯 SVG 图表，离线可用） ==================== */
  var PALETTE = ['#0071e3', '#5b3fd6', '#b25000', '#1d8348', '#c7362f'];

  function svgBar(items, series, H) {
    var W = 520; H = H || 190;
    var pad = { l: 34, r: 8, t: 12, b: 26 }, max = 1;
    items.forEach(function (it) { series.forEach(function (s) { max = Math.max(max, it.values[s.key] || 0); }); });
    var iw = (W - pad.l - pad.r) / Math.max(1, items.length);
    var bw = Math.max(6, (iw - 10) / series.length);
    var g = '', b = '';
    for (var t = 0; t <= 4; t++) {
      var y = pad.t + (H - pad.t - pad.b) * t / 4;
      g += '<line x1="' + pad.l + '" x2="' + (W - pad.r) + '" y1="' + y + '" y2="' + y + '" stroke="#e8e8ed"/>';
      g += '<text x="' + (pad.l - 6) + '" y="' + (y + 4) + '" font-size="10" fill="#86868b" text-anchor="end">' +
           Math.round(max * (4 - t) / 4) + '</text>';
    }
    items.forEach(function (it, i) {
      var x0 = pad.l + i * iw + 5;
      series.forEach(function (s, j) {
        var v = it.values[s.key] || 0;
        var h = (H - pad.t - pad.b) * v / max;
        b += '<rect x="' + (x0 + j * bw).toFixed(1) + '" y="' + (H - pad.b - h).toFixed(1) + '" width="' + (bw - 3).toFixed(1) +
             '" height="' + h.toFixed(1) + '" rx="3" fill="' + (s.color || PALETTE[j % PALETTE.length]) + '"/>';
      });
      b += '<text x="' + (pad.l + i * iw + iw / 2).toFixed(1) + '" y="' + (H - 8) + '" font-size="10" fill="#6e6e73" text-anchor="middle">' + it.label + '</text>';
    });
    return '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '">' + g + b + '</svg>';
  }

  function svgLine(items, series, H) {
    var W = 520; H = H || 190;
    var pad = { l: 34, r: 12, t: 12, b: 26 }, max = 1;
    items.forEach(function (it) { series.forEach(function (s) { max = Math.max(max, it.values[s.key] || 0); }); });
    max = Math.max(10, Math.ceil(max / 10) * 10);
    var step = items.length > 1 ? (W - pad.l - pad.r) / (items.length - 1) : 0;
    var g = '', b = '';
    for (var t = 0; t <= 4; t++) {
      var y = pad.t + (H - pad.t - pad.b) * t / 4;
      g += '<line x1="' + pad.l + '" x2="' + (W - pad.r) + '" y1="' + y + '" y2="' + y + '" stroke="#e8e8ed"/>';
      g += '<text x="' + (pad.l - 6) + '" y="' + (y + 4) + '" font-size="10" fill="#86868b" text-anchor="end">' +
           Math.round(max * (4 - t) / 4) + '</text>';
    }
    series.forEach(function (s, j) {
      var color = s.color || PALETTE[j % PALETTE.length];
      var pts = items.map(function (it, i) {
        var v = it.values[s.key] || 0;
        return [pad.l + i * step, H - pad.b - (H - pad.t - pad.b) * v / max];
      });
      b += '<polyline fill="none" stroke="' + color + '" stroke-width="2" points="' +
           pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ') + '"/>';
      pts.forEach(function (p) {
        b += '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="2.6" fill="#fff" stroke="' + color + '" stroke-width="2"/>';
      });
    });
    items.forEach(function (it, i) {
      b += '<text x="' + (pad.l + i * step).toFixed(1) + '" y="' + (H - 8) + '" font-size="10" fill="#6e6e73" text-anchor="middle">' + it.label + '</text>';
    });
    return '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '">' + g + b + '</svg>';
  }

  function renderAnalytics() {
    var data = core.analytics(state, ui.analyticsZone, new Date(state.clock));
    var k = data.kpi;
    $('kpi-rows').innerHTML = [
      ['今日累计预约单', k.totalResToday, '区域维度：' + ui.analyticsZone],
      ['当前在馆学习人数', k.inUseCount, '含暂离保留座位'],
      ['实时座位使用率', k.occRate + '%', '可预约座位为分母'],
      ['今日违约记录', k.vioToday, '自动 ' + k.vioAuto + ' / 手动 ' + k.vioManual],
      ['待处理投诉单', k.pendingComplaints, '建议 12 分钟内响应'],
      ['黑名单受限人数', k.blacklistCount, 'BR-20（候选规则）']
    ].map(function (x) {
      return '<div class="kpi"><div class="label">' + x[0] + '</div><div class="value">' + x[1] + '</div><div class="foot">' + x[2] + '</div></div>';
    }).join('');

    $('chart-zones').innerHTML = svgBar(data.zones.map(function (z) {
      return { label: z.roomId, values: { busy: z.busy, free: z.free, maint: z.maintenance, booked: z.booked } };
    }), [
      { key: 'busy', color: '#0071e3' }, { key: 'free', color: '#c7c7cc' },
      { key: 'maint', color: '#b25000' }, { key: 'booked', color: '#5b3fd6' }
    ]);

    $('chart-vio').innerHTML = svgBar([
      { label: '系统自动违约', values: { a: k.vioAuto } },
      { label: '管理员手动登记', values: { b: k.vioManual } }
    ], [{ key: 'a', color: '#c7362f' }, { key: 'b', color: '#b25000' }]);

    $('chart-hourly').innerHTML = svgLine(data.buckets.map(function (x) {
      return { label: x.label, values: { inUse: x.inUse, booked: x.booked } };
    }), [{ key: 'inUse', color: '#0071e3' }, { key: 'booked', color: '#5b3fd6' }]);

    $('chart-weekly').innerHTML = svgLine(data.weekly.map(function (x) {
      return { label: x.label, values: { checkin: x.checkinRate, breach: x.breachRate } };
    }), [{ key: 'checkin', color: '#1d8348' }, { key: 'breach', color: '#c7362f' }]);

    $('countdown-pill').textContent = ui.autoRefresh ? ('倒计时 ' + ui.countdown + 's') : '已暂停自动刷新';
    $('last-sync').textContent = '最近同步 ' + ui.lastSync;
    $('net-error-banner').hidden = !ui.simulateNetError;
    $('last-valid-snapshot').textContent = '有效快照：' + ui.snapshotAt;
    $('btn-toggle-autorefresh').textContent = ui.autoRefresh ? '暂停自动刷新' : '恢复自动刷新';
    $('btn-toggle-neterror').textContent = '模拟网络异常：' + (ui.simulateNetError ? '开' : '关');
  }

  /** 测量固定头部（顶栏 + 标签栏）的实际高度，写入 CSS 变量
   *  顶栏中的状态条会随换行变化，因此必须动态测量，避免内容被遮挡。
   *  同时保证标签栏 1~6 在任何滚动位置都固定在顶部可见（修复"滚上去才看得到标签"）。 */
  function syncHeaderHeight() {
    var bar = (typeof document.querySelector === 'function') ? document.querySelector('.topbar') : null;
    var tabs = (typeof document.querySelector === 'function') ? document.querySelector('.tabs') : null;
    var h1 = (bar && bar.offsetHeight) ? bar.offsetHeight : 64;
    var h2 = (tabs && tabs.offsetHeight) ? tabs.offsetHeight : 46;
    var root = document.documentElement;
    if (root && root.style && root.style.setProperty) {
      root.style.setProperty('--header-topbar-h', h1 + 'px');
      root.style.setProperty('--header-h', (h1 + h2) + 'px');
    }
  }

  /** 切换功能页：渲染 + 把视口带到内容区顶部 + 同步 URL 锚点
   *  修复"点了顶部 1~6 标签像没反应"：标签栏是吸顶的，切页后若视口仍停在下方，
   *  使用者看不到新内容；同时把当前页写进 URL hash，刷新/分享后仍停在原页。 */
  function switchTab(name) {
    ui.tab = name;
    render();
    try {
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, '', '#' + name);
      }
    } catch (e) { /* file:// 协议下忽略 */ }
    /* 单页长文档：点击 1~6 立即滚动到对应部分（点"6·运营数据看板"即滚到页面下方的看板区域），
       目标位置 = 该部分顶边 − 固定头部高度 − 间距；使用**平滑滚动**，让使用者看到页面移动的过程。 */
    var sec = $('view-' + name);
    if (sec && typeof sec.getBoundingClientRect === 'function' && typeof window.scrollTo === 'function') {
      var bar = (typeof document.querySelector === 'function') ? document.querySelector('.topbar') : null;
      var tabbar = (typeof document.querySelector === 'function') ? document.querySelector('.tabs') : null;
      var offset = ((bar && bar.offsetHeight) || 0) + ((tabbar && tabbar.offsetHeight) || 0) + 12;
      var top = sec.getBoundingClientRect().top + (window.pageYOffset || 0) - offset;
      top = Math.max(0, Math.round(top));
      try { window.scrollTo({ top: top, behavior: 'smooth' }); }
      catch (e) { window.scrollTo(0, top); }
    }

    /* 切页反馈：新页面轻微淡入，让"已切换"这件事看得见 */
    var sec = $('view-' + name);
    if (sec && sec.classList && sec.classList.add) {
      sec.classList.remove('page-in');
      void sec.offsetWidth;
      sec.classList.add('page-in');
    }
  }

  /* ==================== 渲染分发 ==================== */
  function render() {
    var u = user();
    var roleName = u.role === 'student' ? '学生' : u.role === 'admin' ? '自习室管理员'
      : u.role === 'sysadmin' ? '系统管理员' : '教师';
    $('status-user').innerHTML = '当前登录 <b>' + esc(u.name) + '</b>（' + esc(u.id) + ' · ' + esc(roleName) + '）';
    if (u.limit) {
      var kind = u.limit.type === 'blacklist' ? '黑名单限制中' : u.limit.type === 'credit' ? '信用分不足' : '连续违约限制';
      var code = u.limit.type === 'blacklist' ? 'BR-20' : u.limit.type === 'credit' ? 'BR-07' : 'BR-15';
      $('status-blacklist').innerHTML = '<span class="pill red">' + kind + '（' + code + '）：' +
        esc(u.limit.reason || '') + (u.limit.until ? '，至 ' + esc(u.limit.until) : '') + '</span>';
    } else if (u.role === 'student') {
      $('status-blacklist').innerHTML = '<span class="pill green">信用正常 · ' + u.credit + ' 分 · 30 天内有效违约 ' +
        core.activeViolationCount(state, u.id, new Date(state.clock)) + ' 次</span>';
    } else {
      $('status-blacklist').innerHTML = '';
    }
    $('status-clock').textContent = clockText();

    var btns = document.querySelectorAll('#role-switch button');
    for (var i = 0; i < btns.length; i++) {
      var uid = btns[i].getAttribute('data-user');
      var risky = uid === 'S2023002' || uid === 'S2023005';
      btns[i].className = uid === ui.userId ? (risky ? 'active danger' : 'active') : '';
    }
    var tabs = document.querySelectorAll('#app-tabs .tab');
    for (var j = 0; j < tabs.length; j++) {
      var name = tabs[j].getAttribute('data-tab');
      var on = name === ui.tab;
      tabs[j].className = 'tab' + (on ? ' active' : '');
      var sec = $('view-' + name);
      if (sec) { sec.hidden = false; }   /* 单页长文档：6 个部分全部展开，点标签只做滚动定位 */
    }
    if (ui.tab === 'seats') { renderSeats(); }
    else if (ui.tab === 'myres') { renderMyRes(); }
    else if (ui.tab === 'queries') { renderQueries(); }
    else if (ui.tab === 'admin') { renderAdmin(); }
    else if (ui.tab === 'community') { renderCommunity(); }
    else if (ui.tab === 'analytics') { renderAnalytics(); }
  }

  /* ==================== 事件委托（点击） ==================== */
  document.addEventListener('click', function (ev) {
    var el = ev.target && ev.target.closest ? ev.target.closest('[data-act],[data-tab],[data-user]') : null;
    if (!el) { return; }
    var tab = el.getAttribute('data-tab');
    if (tab) { switchTab(tab); return; }
    if (el.closest('#role-switch')) {
      ui.userId = el.getAttribute('data-user');
      ui.selectedSeat = null;
      switchTab('seats');
      toast('已切换视角：' + (user().name) + '（' + (user().role === 'student' ? '学生' : user().role === 'admin' ? '管理员' : '系统管理员') + '）');
      return;
    }
    handleAction(el.getAttribute('data-act'), el);
  });

  /* ==================== 操作分发（失败一律显示规则编号） ==================== */
  function handleAction(act, el) {
    if (!act) { return; }
    var id = el.getAttribute('data-id');
    var seat = el.getAttribute('data-seat');
    var mode = el.getAttribute('data-mode');
    var res;
    switch (act) {
      case 'pick-seat':
        ui.selectedSeat = seat;
        renderSeats();
        toast('已选择座位 ' + seat + '，请点击「确认预约」');
        return;
      case 'book':
        if (!ui.selectedSeat) { toast('请先在下方选择一个空闲座位', 'err'); return; }
        res = core.createReservation(state, { actorId: ui.userId, seatId: ui.selectedSeat, date: ui.query.date,
          start: ui.query.start, end: endOf(ui.query.start, ui.query.minutes), now: state.clock });
        if (consume(res, '预约成功：' + ui.selectedSeat + ' ' + ui.query.date + ' ' + ui.query.start + '–' +
            endOf(ui.query.start, ui.query.minutes) + '（请按时签到）')) { ui.selectedSeat = null; }
        return;
      case 'join-waitlist':
        if (!ui.selectedSeat) { toast('请先选择一个座位，再加入候补', 'err'); return; }
        consume(core.joinWaitlist(state, { actorId: ui.userId, seatId: ui.selectedSeat, date: ui.query.date,
          start: ui.query.start, end: endOf(ui.query.start, ui.query.minutes), now: state.clock }), '已加入候补队列（BR-08）');
        return;
      case 'checkin':
        consume(core.checkIn(state, { actorId: ui.userId, reservationId: id, now: state.clock, credential: cred() }),
          '签到成功，状态转为「使用中」（ST-01）');
        return;
      case 'cancel':
        res = core.cancelReservation(state, { actorId: ui.userId, reservationId: id, now: state.clock });
        consume(res, res && res.outcome === 'cancelled_late'
          ? '已取消；距开始不足 30 分钟，记违规 −5（ST-04）' : '已取消，不记违规（ST-03）');
        return;
      case 'away':
        consume(core.startAway(state, { actorId: ui.userId, reservationId: id, now: state.clock }),
          '已进入暂离，座位保留 30 分钟（ST-05）');
        return;
      case 'resume':
        consume(core.resumeUse(state, { actorId: ui.userId, reservationId: id, now: state.clock }),
          '已返座，恢复使用（ST-06）');
        return;
      case 'checkout':
        res = core.checkOut(state, { actorId: ui.userId, reservationId: id, now: state.clock });
        consume(res, res && res.outcome === 'overstay'
          ? '已签退；超过预约结束时间，记违规 −5（ST-09）' : '签退成功，座位已释放（ST-08）');
        return;
      case 'sim-noshow': {
        var r1 = core.findReservation(state, id);
        var t1 = core.addMinutes(core.toDate(r1.date, r1.start), 16);
        var out1 = core.runTimeoutSweep(state, t1);
        toast(out1.count ? '已模拟「开始后 16 分钟仍未签到」→ 定时任务判定违约并释放座位（ST-02）'
                         : '该预约已不在超时范围内，无需处理', out1.count ? 'ok' : 'err');
        render();
        return;
      }
      case 'sim-away-timeout': {
        var r2 = core.findReservation(state, id);
        var t2 = new Date(String(r2.awayDeadlineAt).replace(' ', 'T') + ':00').getTime() + 60000;
        var out2 = core.runTimeoutSweep(state, new Date(t2));
        toast(out2.count ? '已模拟「暂离超 30 分钟未归」→ 座位被释放并记违约（ST-07）'
                         : '该预约当前不处于可超时的暂离状态', out2.count ? 'ok' : 'err');
        render();
        return;
      }
      case 'manual-vio': {
        var remark = prompt2('请填写现场核实说明（必填）', '巡检发现该座位无人使用且未办理暂离');
        if (!remark) { toast('已取消：手动违约登记必须填写核实说明（BR-18）', 'err'); return; }
        res = core.manualViolation(state, { actorId: ui.userId, reservationId: id, category: 'occupy',
          remark: remark, mode: mode, now: state.clock });
        consume(res, mode === 'release'
          ? '已登记违约并释放座位（ST-10）；操作已写入审计日志（NFR-09）'
          : '已登记违约警告；座位保持使用中（状态保持）');
        return;
      }
      case 'toggle-maint': {
        var s = core.findSeat(state, seat);
        if (s.status === 'maintenance') {
          consume(core.unblockSeat(state, { actorId: ui.userId, seatId: seat, now: state.clock }), '已解除维护（BR-09）');
        } else {
          var reason = prompt2('封禁原因（必填）', '座椅损坏待维修');
          if (!reason) { return; }
          res = core.blockSeat(state, { actorId: ui.userId, seatId: seat, reason: reason,
            from: ui.query.date, to: ui.query.date, now: state.clock });
          consume(res, res && res.affectedReservations && res.affectedReservations.length
            ? '已封禁；受影响预约 ' + res.affectedReservations.join('、') + ' 已自动取消且不记违规（BR-09）'
            : '已封禁该座位（BR-09）');
        }
        return;
      }
      case 'open-complaint': openComplaintModal(); return;
      case 'open-feedback': openFeedbackModal(id); return;
      case 'open-eval': openEvalModal(id); return;
      case 'reply-eval': openReplyEvalModal(id); return;
      case 'block':
        consume(core.setBlacklist(state, { actorId: ui.userId, userId: el.getAttribute('data-user'),
          action: 'block', reason: prompt2('拉黑原因（必填）', '多次违约且未整改'), now: state.clock }),
          '已人工拉黑（BR-20 / PR-18）');
        return;
      case 'unblock':
        consume(core.setBlacklist(state, { actorId: ui.userId, userId: el.getAttribute('data-user'),
          action: 'unblock', reason: prompt2('解封原因（必填）', '申诉成立，已完成整改'), now: state.clock }),
          '已解除预约限制（BR-20）');
        return;
      case 'switch-admin':
        ui.userId = 'A9001';
        render();
        return;
      case 'close-modal': closeModal(); return;
      default: return;
    }
  }

  /* ==================== 弹窗（表单） ==================== */
  function openModal(html) {
    $('modal-root').innerHTML = '<div class="modal-mask" data-act="close-modal"><div class="modal">' + html + '</div></div>';
  }
  function closeModal() { $('modal-root').innerHTML = ''; }
  function field(label, inner) { return '<div class="field" style="margin-bottom:12px"><label>' + label + '</label>' + inner + '</div>'; }

  function openComplaintModal() {
    var opts = state.seats.map(function (s) {
      return '<option value="' + s.id + '">' + esc(s.id) + '（' + esc(s.type) + '）</option>';
    }).join('');
    openModal('<h3>提交投诉单</h3><div class="sub">管理员处理后会填写反馈信息，学生端立即可见（BR-19）</div>' +
      field('涉及座位', '<select id="cmp-seat">' + opts + '</select>') +
      field('投诉类别', '<select id="cmp-type"><option>他人恶意占座</option><option>周围环境喧哗</option>' +
        '<option>座位设施损坏</option><option>违约误判申诉</option></select>') +
      field('具体情况描述', '<textarea id="cmp-desc" rows="3">该座位放了书本但已超过 1 小时无人，也没有办理暂离，影响其他同学使用。</textarea>') +
      '<div class="modal-foot"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn primary" data-act="submit-complaint">提交投诉</button></div>');
  }

  function openFeedbackModal(cid) {
    var c = null;
    state.complaints.forEach(function (x) { if (x.id === cid) { c = x; } });
    if (!c) { return; }
    var target = core.findReservation;
    openModal('<h3>填写投诉反馈并办结</h3><div class="sub">反馈内容学生端可见；可勾选联动处理现场违约（BR-19 / BR-18）</div>' +
      '<div class="banner info" style="margin-bottom:12px">' + esc(c.id) + ' · ' + esc(c.type) + ' · 座位 ' + esc(c.seatCode || '—') +
      '<br>' + esc(c.description) + '</div>' +
      '<label class="check" style="margin-bottom:10px"><input type="checkbox" id="fb-link-vio" checked>' +
      ' 同步对该座位当前使用人执行手动违约登记并强制释放座位（ST-10）</label>' +
      field('投诉反馈信息（必填）', '<textarea id="fb-content" rows="3">管理员已到现场核实并清理占座物品，已对该座位使用人执行手动违约登记，座位现已释放为空闲状态。感谢您的监督反馈！</textarea>') +
      '<div class="modal-foot"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn green" data-act="submit-feedback" data-cid="' + esc(c.id) + '">提交反馈并办结</button></div>');
  }

  function openEvalModal(rid) {
    var r = core.findReservation(state, rid);
    openModal('<h3>提交座位使用评价</h3><div class="sub">仅「已完成」的预约可评价，每单限一次（BR-21）</div>' +
      '<div class="banner info" style="margin-bottom:12px">' + esc(r.seatId) + ' · ' + esc(r.date + ' ' + r.start + '-' + r.end) + '</div>' +
      field('星级评分', '<select id="eval-rating"><option value="5">5 星 · 非常满意</option><option value="4">4 星 · 比较满意</option>' +
        '<option value="3">3 星 · 一般</option><option value="2">2 星 · 设施存在小问题</option><option value="1">1 星 · 设施故障 / 环境嘈杂</option></select>') +
      field('快捷标签', '<input type="text" id="eval-tags" value="插座供电稳定, 靠窗采光好, 环境安静">') +
      field('评价内容', '<textarea id="eval-content" rows="3">插座供电很稳，护眼台灯亮度舒适，周围同学都很安静。</textarea>') +
      '<div class="modal-foot"><button class="btn" data-act="close-modal">暂不评价</button>' +
      '<button class="btn primary" data-act="submit-eval" data-rid="' + esc(rid) + '">提交评价</button></div>');
  }

  function openReplyEvalModal(eid) {
    openModal('<h3>回复学生评价</h3><div class="sub">若为差评，可勾选联动将该座位设为维护停用（PR-18）</div>' +
      field('回复内容', '<textarea id="reply-content" rows="3">感谢反馈！我们已安排现场核查，并会持续改善该区域环境。</textarea>') +
      '<label class="check"><input type="checkbox" id="reply-maint"> 同时将该座位设为「维护停用」并取消受影响预约（不记违规）</label>' +
      '<div class="modal-foot"><button class="btn" data-act="close-modal">取消</button>' +
      '<button class="btn primary" data-act="submit-reply" data-eid="' + esc(eid) + '">提交回复</button></div>');
  }

  /* 弹窗内表单提交（与主分发解耦，避免误触） */
  document.addEventListener('click', function (ev) {
    var el = ev.target && ev.target.closest ? ev.target.closest('[data-act]') : null;
    if (!el) { return; }
    var act = el.getAttribute('data-act'), out;
    if (act === 'close-modal' && el.classList.contains('modal-mask') && ev.target !== el) { return; }
    if (act === 'submit-complaint') {
      out = core.submitComplaint(state, { actorId: ui.userId, seatId: $('cmp-seat').value, type: $('cmp-type').value,
        description: $('cmp-desc').value, now: state.clock });
      if (consume(out, '投诉已提交，管理员处理后会显示反馈信息')) { closeModal(); }
    } else if (act === 'submit-feedback') {
      out = core.replyComplaint(state, { actorId: ui.userId, complaintId: el.getAttribute('data-cid'),
        feedback: $('fb-content').value, linkViolation: $('fb-link-vio').checked, now: state.clock });
      if (consume(out, '反馈已提交并办结' + (out && out.linkedViolationId ? '，联动违约 ' + out.linkedViolationId : ''))) { closeModal(); }
    } else if (act === 'submit-eval') {
      out = core.submitEvaluation(state, { actorId: ui.userId, reservationId: el.getAttribute('data-rid'),
        rating: $('eval-rating').value, tags: $('eval-tags').value, content: $('eval-content').value, now: state.clock });
      if (consume(out, '评价已提交，感谢反馈')) { closeModal(); }
    } else if (act === 'submit-reply') {
      out = core.replyEvaluation(state, { actorId: ui.userId, evaluationId: el.getAttribute('data-eid'),
        reply: $('reply-content').value, setMaintenance: $('reply-maint').checked, now: state.clock });
      if (consume(out, '已回复学生评价')) { closeModal(); }
    }
  });

  /* ==================== 看板自动刷新（10 秒）与网络异常容错 ==================== */
  function refreshDashboard(manual) {
    if (ui.simulateNetError) {
      if (ui.snapshotAt === '—') { ui.snapshotAt = clockText(); }
      ui.countdown = 10;
      if (manual) { toast('模拟网络超时：已保留上一轮有效快照，看板不中断（NFR-10 容错）', 'err'); }
      renderAnalytics();
      return;
    }
    ui.lastSnapshot = core.analytics(state, ui.analyticsZone, new Date(state.clock));
    ui.lastSync = clockText().slice(11);
    ui.snapshotAt = clockText();
    ui.countdown = 10;
    renderAnalytics();
    if (manual) { toast('看板已刷新（每 10 秒自动刷新一次）', 'ok'); }
  }
  window.setInterval(function () {
    if (!ui.autoRefresh) { return; }
    ui.countdown -= 1;
    if (ui.countdown <= 0) { refreshDashboard(false); }
    else { $('countdown-pill').textContent = '倒计时 ' + ui.countdown + 's'; }
  }, 1000);

  /* ==================== 初始化与控件绑定 ==================== */
  function bindControls() {
    var mk = function (el, ev, fn) { el.addEventListener(ev, fn); };

    mk($('filter-date'), 'change', function () { ui.query.date = this.value; renderSeats(); });
    mk($('filter-start'), 'change', function () { ui.query.start = this.value; renderSeats(); });
    mk($('filter-duration'), 'change', function () { ui.query.minutes = this.value; renderSeats(); });
    mk($('filter-room'), 'change', function () { ui.query.roomId = this.value; renderSeats(); });
    mk($('filter-status'), 'change', function () { ui.query.status = this.value; renderSeats(); });
    ['power', 'window', 'lamp'].forEach(function (k) {
      mk($('filter-' + k), 'change', function () {
        ui.query['has' + k.charAt(0).toUpperCase() + k.slice(1)] = this.checked;
        renderSeats();
      });
    });
    mk($('btn-seat-filter-reset'), 'click', function () {
      ui.query.status = ''; ui.query.roomId = ''; ui.query.start = '14:00'; ui.query.minutes = 60;
      ui.query.hasPower = false; ui.query.hasWindow = false; ui.query.hasLamp = false; ui.selectedSeat = null;
      $('filter-room').value = ''; $('filter-status').value = ''; $('filter-start').value = '14:00';
      $('filter-duration').value = '60';
      $('filter-power').checked = false; $('filter-window').checked = false; $('filter-lamp').checked = false;
      renderSeats();
      toast('筛选条件已重置');
    });
    mk($('btn-book'), 'click', function () { doBook(); });
    mk($('btn-join-waitlist'), 'click', function () {
      if (!ui.selectedSeat) { toast('请先选择一个座位，再加入候补', 'err'); return; }
      consume(core.joinWaitlist(state, { actorId: ui.userId, seatId: ui.selectedSeat, date: ui.query.date,
        start: ui.query.start, end: endOf(ui.query.start, ui.query.minutes), now: state.clock }), '已加入候补队列（BR-08）');
    });

    mk($('btn-advance-30'), 'click', function () { advance(30); render(); toast('模拟时钟已推进 30 分钟：' + clockText()); });
    mk($('btn-advance-60'), 'click', function () { advance(60); render(); toast('模拟时钟已推进 1 小时：' + clockText()); });
    mk($('btn-run-sweep'), 'click', function () {
      var out = core.runTimeoutSweep(state, state.clock);
      toast(out.count ? '定时任务已处理 ' + out.count + ' 条超时记录（FR-44 / ST-02·ST-07·ST-09）'
                      : '定时任务执行完成：当前没有需要处理的超时记录（幂等，AC-15）', out.count ? 'ok' : '');
      render();
    });
    mk($('btn-reset'), 'click', function () {
      state = seed.createSeedState(fixedNow || undefined);
      ui.selectedSeat = null;
      render();
      toast('已重置为初始演示数据');
    });

    ['res-status', 'vio-source', 'vio-status'].forEach(function (k) {
      mk($('query-' + k), 'change', function () {
        if (k === 'res-status') { ui.resStatus = this.value; }
        if (k === 'vio-source') { ui.vioSource = this.value; }
        if (k === 'vio-status') { ui.vioStatus = this.value; }
        renderQueries();
      });
    });
    mk($('query-keyword'), 'input', function () { ui.keyword = this.value; renderQueries(); });

    mk($('analytics-zone'), 'change', function () { ui.analyticsZone = this.value; refreshDashboard(false); toast('看板已按区域筛选刷新'); });
    mk($('btn-refresh-now'), 'click', function () { refreshDashboard(true); });
    mk($('btn-toggle-autorefresh'), 'click', function () {
      ui.autoRefresh = !ui.autoRefresh;
      ui.countdown = 10;
      renderAnalytics();
      toast(ui.autoRefresh ? '已恢复自动刷新（每 10 秒）' : '已暂停自动刷新');
    });
    mk($('btn-toggle-neterror'), 'click', function () {
      ui.simulateNetError = !ui.simulateNetError;
      renderAnalytics();
      toast(ui.simulateNetError ? '已开启「模拟网络异常」：下一次轮询将保留上一轮快照（容错演示）'
                                : '已关闭「模拟网络异常」，看板恢复正常刷新', ui.simulateNetError ? 'err' : 'ok');
    });
  }

  function doBook() {
    if (!ui.selectedSeat) { toast('请先在下方选择一个空闲座位', 'err'); return; }
    var out = core.createReservation(state, { actorId: ui.userId, seatId: ui.selectedSeat, date: ui.query.date,
      start: ui.query.start, end: endOf(ui.query.start, ui.query.minutes), now: state.clock });
    var simToday = core.fmtDate(new Date(state.clock));
    var future = out && out.ok && out.reservation.date > simToday;
    var msg = out && out.ok
      ? '预约成功：' + ui.selectedSeat + ' ' + ui.query.date + ' ' + ui.query.start + '–' +
        endOf(ui.query.start, ui.query.minutes) +
        (future ? '；该时段在未来，可点「+1 小时」把模拟时钟推进到签到窗口后再签到（BR-04）' : '（请按时签到）')
      : null;
    if (consume(out, msg)) { ui.selectedSeat = null; }
  }

  /* ==================== 启动 ==================== */
  function boot() {
    /* 支持深层链接（便于截图取证与演示）：index.html?u=A9001#analytics */
    var qs = String((window.location && window.location.search) || '');
    var mu = qs.match(/u=([A-Za-z0-9]+)/);
    if (mu && core.findUser(state, mu[1])) { ui.userId = mu[1]; }
    var hash = String((window.location && window.location.hash) || '').replace('#', '');
    if (['seats', 'myres', 'queries', 'admin', 'community', 'analytics'].indexOf(hash) >= 0) { ui.tab = hash; }

    /* BR-03：把日期控件限制在「模拟时钟今天 ~ 今天+7 天」的可预约窗口内，
       避免使用者按真实世界的日期选择而超出模拟窗口（原表现为误报"只能预约 7 天内"）。 */
    var maxD = new Date(state.TODAY + 'T00:00:00');
    maxD.setDate(maxD.getDate() + state.params.bookingWindowDays);
    var maxDay = core.fmtDate(maxD);
    $('filter-date').min = state.TODAY;
    $('filter-date').max = maxDay;
    $('filter-date').value = state.TODAY;
    if ($('booking-window-hint')) {
      $('booking-window-hint').textContent = state.TODAY + ' ~ ' + maxDay + '（模拟时钟基准 ' + state.TODAY + '）';
    }
    var startOpts = '';
    for (var m = 7 * 60; m <= 22 * 60 + 30; m += 30) {
      startOpts += '<option value="' + hm(m) + '">' + hm(m) + '</option>';
    }
    $('filter-start').innerHTML = startOpts;
    $('filter-start').value = ui.query.start;
    $('filter-room').innerHTML = '<option value="">全部自习室</option>' + state.rooms.map(function (r) {
      return '<option value="' + r.id + '">' + esc(r.name) + '</option>';
    }).join('');
    $('analytics-zone').innerHTML = '<option value="ALL">全馆汇总视图</option>' + state.rooms.map(function (r) {
      return '<option value="' + r.id + '">' + esc(r.name) + '</option>';
    }).join('');
    bindControls();
    render();
    syncHeaderHeight();
    if (window.addEventListener) { window.addEventListener('resize', syncHeaderHeight); }
    ui.lastSync = clockText().slice(11);
    refreshDashboard(false);
    toast('原型已就绪：先用顶部的「当前模拟视角」切换角色，再按每页顶部的引导条操作', 'ok');
  }

  window.addEventListener('DOMContentLoaded', function () {
    try {
      boot();
    } catch (err) {
      document.body.insertAdjacentHTML('afterbegin',
        '<div class="banner warn" style="margin:16px">原型初始化失败：' + esc(err && err.message) + '</div>');
    }
  });
})();
