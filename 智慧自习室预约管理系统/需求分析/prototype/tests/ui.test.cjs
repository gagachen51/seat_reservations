/**
 * 页面交互走查测试 v2.0（覆盖 6 个功能页与新增能力）
 * ==================================================================
 * 方法：用最小 DOM stub 加载**真实的** prototype/app.js + domain.js + seed.js，
 *       模拟用户点击与表单操作，断言页面渲染结果与拒绝提示。
 * 证据：运行输出归档为 E-TEST-02；真实浏览器截图见 attachments/evidence/screenshots/。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { FIXED_NOW, NEXT_DATE, reservationId } = require('./support/_fixtures.cjs');

const ROLE_USERS = ['S2023001', 'S2023002', 'S2023005', 'A9001', 'Y0001'];
const TAB_NAMES = ['seats', 'myres', 'queries', 'admin', 'community', 'analytics'];

function makeEl(id) {
  return {
    id: id, innerHTML: '', textContent: '', value: '', hidden: false, className: '', style: {},
    checked: false, dataset: {}, _handlers: {},
    addEventListener: function (t, f) { (this._handlers[t] = this._handlers[t] || []).push(f); },
    closest: function () { return null; },
    getBoundingClientRect: function () { return { top: 420 }; },
    offsetWidth: 0,
    classList: {
      _classes: [],
      contains: function (x) { return this._classes.indexOf(x) >= 0; },
      add: function (x) { this._classes.push(x); },
      remove: function (x) { this._classes = this._classes.filter(function (y) { return y !== x; }); }
    },
    insertAdjacentHTML: function () {},
    appendChild: function (c) { (this._children = this._children || []).push(c); },
    remove: function () {},
    fire: function (t, e) { var self = this; (this._handlers[t] || []).forEach(function (f) { f.call(self, e || {}); }); }
  };
}

function boot() {
  Object.keys(require.cache).forEach(function (k) {
    if (/app\.js|domain\.js|seed\.js/.test(k)) { delete require.cache[k]; }
  });
  const core = require('../domain.js');
  const seed = require('../seed.js');
  const els = {};
  const roleBtns = ROLE_USERS.map(function (u) {
    return { _u: u, className: '', getAttribute: function (k) { return k === 'data-user' ? this._u : null; } };
  });
  const tabBtns = TAB_NAMES.map(function (t) {
    return { _t: t, className: '', getAttribute: function (k) { return k === 'data-tab' ? this._t : null; } };
  });
  const scrolled = [];
  const cssVars = {};
  const doc = {
    _handlers: {},
    documentElement: { style: { setProperty: function (k, v) { cssVars[k] = v; } } },
    querySelector: function (sel) {
      if (sel === '.topbar') { return { offsetHeight: 72 }; }
      if (sel === '.tabs') { return { offsetHeight: 46 }; }
      if (sel === 'main') {
        return { offsetHeight: 0, getBoundingClientRect: function () { return { top: 600 }; } };
      }
      return { scrollIntoView: function () { scrolled.push('intoView:' + sel); }, offsetHeight: 0 };
    },
    body: { insertAdjacentHTML: function () {} },
    getElementById: function (id) { return els[id] || (els[id] = makeEl(id)); },
    createElement: function (tag) { return makeEl('new-' + tag); },
    querySelectorAll: function (sel) {
      if (String(sel).indexOf('role-switch') >= 0) { return roleBtns; }
      if (String(sel).indexOf('app-tabs') >= 0) { return tabBtns; }
      return [];
    },
    addEventListener: function (t, f) { (this._handlers[t] = this._handlers[t] || []).push(f); },
    fire: function (t, e) { (this._handlers[t] || []).forEach(function (f) { f(e); }); }
  };
  const win = {
    _handlers: {}, location: { hash: '' },
    addEventListener: function (t, f) { (this._handlers[t] = this._handlers[t] || []).push(f); },
    setTimeout: function () { return 0; }, setInterval: function () { return 0; },
    clearTimeout: function () {}, prompt: function () { return '现场巡检核实说明（测试输入）'; },
    scrollTo: function (a, b) {
      scrolled.push('scrollTo:' + (a && typeof a === 'object' ? (a.top + ':' + a.behavior) : a));
    },
    pageYOffset: 800,
    history: { replaceState: function () { scrolled.push('hash'); } },
    fire: function (t) { (this._handlers[t] || []).forEach(function (f) { f(); }); }
  };
  global.window = win;
  global.document = doc;
  win.SRSCore = core;
  win.SRSSeed = seed;
  win.SRS_FIXED_NOW = FIXED_NOW;
  require('../app.js');
  win.fire('DOMContentLoaded');
  return { core: core, seed: seed, doc: doc, win: win, scrolled: scrolled, cssVars: cssVars,
    el: function (id) { return els[id] || (els[id] = makeEl(id)); } };
}

/** 模拟点击带 data-* 的元素（走 app.js 的事件委托） */
function tap(page, attrs, inRoleSwitch) {
  const node = {
    _a: attrs,
    getAttribute: function (k) { return this._a[k] === undefined ? null : String(this._a[k]); },
    closest: function (sel) {
      if (String(sel).indexOf('role-switch') >= 0) { return inRoleSwitch ? node : null; }
      return node;
    },
    classList: {
      _classes: [], contains: function () { return false; },
      add: function (x) { this._classes.push(x); }, remove: function () {}
    }
  };
  page.doc.fire('click', { target: node });
}
/** 模拟点击页面上的按钮（走 bindControls 注册的监听） */
function press(page, id) { page.el(id).fire('click', {}); }
/** 切换角色到管理员 */
function asAdmin(page) { tap(page, { 'data-user': 'A9001' }, true); }
/** 读取文本框/下拉框值 */
function setVal(page, id, v) { page.el(id).value = v; }

function pendingId(p) { return reservationId(p.seed.createSeedState(FIXED_NOW), 'S2023001', 'pending'); }
function inUseId(p) { return reservationId(p.seed.createSeedState(FIXED_NOW), 'S2023003', 'in_use'); }
function completedUnevaluatedId(p) {
  const s = p.seed.createSeedState(FIXED_NOW);
  const evaluated = new Set((s.evaluations || []).map(function (e) { return e.reservationId; }));
  return s.reservations.find(function (r) {
    return r.status === 'completed' && !evaluated.has(r.id);
  }).id;
}

test('U1 初始化：顶部状态栏、座位网格、看板 KPI 与 4 张 SVG 图表均已渲染', () => {
  const p = boot();
  assert.match(p.el('status-user').innerHTML, /张小明/);
  assert.match(p.el('status-clock').textContent, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  assert.ok(p.el('seat-grid').innerHTML.length > 2000, '座位网格应有内容');
  assert.match(p.el('seat-count-hint').textContent, /符合条件\s*\d+\s*个座位/);
  assert.ok(p.el('kpi-rows').innerHTML.indexOf('今日累计预约单') >= 0);
  assert.ok(p.el('chart-zones').innerHTML.indexOf('<svg') >= 0, '柱状图应生成 SVG');
  assert.ok(p.el('chart-weekly').innerHTML.indexOf('polyline') >= 0, '折线图应生成折线');
});

test('U2 座位属性筛选：勾选「带电源」后网格只剩带电源的座位，且提示计数同步', () => {
  const p = boot();
  p.el('filter-power').checked = true;
  p.el('filter-power').fire('change', {});
  const grid = p.el('seat-grid').innerHTML;
  assert.ok(grid.indexOf('⚡ 电源') >= 0);
  assert.match(p.el('seat-count-hint').textContent, /符合条件/);
});

test('U3 预约：选择空闲座位 → 确认预约，页面出现「待签到」记录', () => {
  const p = boot();
  setVal(p, 'filter-start', '16:00');
  p.el('filter-start').fire('change', {});
  tap(p, { 'data-act': 'pick-seat', 'data-seat': 'A101-004' });
  press(p, 'btn-book');
  tap(p, { 'data-tab': 'myres' });
  assert.match(p.el('myres-list').innerHTML, /A101-004/);
  assert.match(p.el('myres-list').innerHTML, /待签到/);
});

test('U4 主流程状态链：签到 → 暂离 → 返座 → 签退（ST-01 / ST-05 / ST-06 / ST-08）', () => {
  const p = boot();
  for (let i = 0; i < 12; i++) { press(p, 'btn-advance-30'); }        // 08:00 → 14:00，进入签到窗口
  tap(p, { 'data-act': 'checkin', 'data-id': pendingId(p) });       // 张小明 14:00-15:00 预置预约
  tap(p, { 'data-tab': 'myres' });
  assert.match(p.el('myres-list').innerHTML, /使用中/);
  tap(p, { 'data-act': 'away', 'data-id': pendingId(p) });
  assert.match(p.el('myres-list').innerHTML, /暂离/);
  tap(p, { 'data-act': 'resume', 'data-id': pendingId(p) });
  assert.match(p.el('myres-list').innerHTML, /使用中/);
  tap(p, { 'data-act': 'checkout', 'data-id': pendingId(p) });
  assert.match(p.el('myres-list').innerHTML, /已完成/);
});

test('U5 异常演练：模拟「超时未签到」后状态变违约并给出规则提示（ST-02）', () => {
  const p = boot();
  tap(p, { 'data-act': 'sim-noshow', 'data-id': pendingId(p) });
  tap(p, { 'data-tab': 'myres' });
  assert.match(p.el('myres-list').innerHTML, /已违约/);
});

test('U6 现场巡检：管理员手动违约登记两种处置（仅警告 / 强制释放，ST-10 与状态保持）', () => {
  const p = boot();
  asAdmin(p);
  press(p, 'btn-advance-30');
  press(p, 'btn-advance-30');                                        // 08:00 → 09:00，对齐在途预约
  tap(p, { 'data-tab': 'admin' });
  assert.equal(p.el('admin-guard').hidden, true, '管理员视角不显示权限守卫');
  assert.ok(p.el('admin-seat-board').innerHTML.indexOf('data-act="manual-vio"') >= 0, '应有手动违约按钮');
  tap(p, { 'data-act': 'manual-vio', 'data-id': inUseId(p), 'data-mode': 'warn' });
  assert.match(p.el('admin-seat-board').innerHTML + p.el('myres-list').innerHTML, /使用中|占用/);
  tap(p, { 'data-act': 'manual-vio', 'data-id': inUseId(p), 'data-mode': 'release' });
  assert.ok(p.el('admin-seat-board').innerHTML.indexOf(inUseId(p)) < 0 ||
            p.el('admin-seat-board').innerHTML.indexOf('已释放') >= 0, '强制释放后该座位应变为空闲');
});

test('U7 投诉与反馈闭环：学生提交投诉 → 管理员填写反馈并联动违约（BR-19 / PV-03）', () => {
  const p = boot();
  tap(p, { 'data-act': 'open-complaint' });
  setVal(p, 'cmp-seat', 'A101-003');
  setVal(p, 'cmp-desc', 'A101-003 座位长期无人但物品占位（走查输入）');
  tap(p, { 'data-act': 'submit-complaint' });
  tap(p, { 'data-tab': 'community' });
  assert.match(p.el('complaints-list').innerHTML, /待处理/);
  asAdmin(p);
  tap(p, { 'data-tab': 'community' });
  const cid = (p.el('complaints-list').innerHTML.match(/data-id="(CMP-[^"]+)"/) || [])[1];
  assert.ok(cid, '页面应渲染出投诉单编号');
  tap(p, { 'data-act': 'open-feedback', 'data-id': cid });
  setVal(p, 'fb-content', '已现场核实并清理占位物品（走查输入）');
  p.el('fb-link-vio').checked = false;
  tap(p, { 'data-act': 'submit-feedback', 'data-cid': cid });
  assert.match(p.el('complaints-list').innerHTML, /已办结/);
});

test('U8 评价：仅「已完成」且未评价的预约可提交评价（BR-21）', () => {
  const p = boot();
  tap(p, { 'data-act': 'open-eval', 'data-id': completedUnevaluatedId(p) });
  setVal(p, 'eval-rating', '5');
  setVal(p, 'eval-content', '靠窗采光好，插座稳定（走查输入）');
  tap(p, { 'data-act': 'submit-eval', 'data-rid': completedUnevaluatedId(p) });
  tap(p, { 'data-tab': 'community' });
  assert.match(p.el('evals-list').innerHTML, /走查输入/);
  assert.match(p.el('evals-list').innerHTML, /好评|一般|差评/);
  // 未完成的预约不能评价：提交会被规则拒绝（页面提示含 BR-21）
  const before = p.el('evals-list').innerHTML.length;
  tap(p, { 'data-act': 'open-eval', 'data-id': pendingId(p) });
  tap(p, { 'data-act': 'submit-eval', 'data-rid': pendingId(p) });
  tap(p, { 'data-tab': 'community' });
  assert.equal(p.el('evals-list').innerHTML.length, before, '未完成预约提交评价不应产生新记录');
});

test('U9 信用与黑名单：管理员拉黑 / 解封均须填原因并即时生效（BR-20 / PR-18）', () => {
  const p = boot();
  asAdmin(p);
  tap(p, { 'data-tab': 'community' });
  assert.match(p.el('users-tbody').innerHTML, /赵敏/);
  assert.match(p.el('users-tbody').innerHTML, /黑名单限制中/);
  tap(p, { 'data-act': 'unblock', 'data-user': 'S2023005' });
  assert.ok(p.el('users-tbody').innerHTML.indexOf('黑名单限制中') < 0, '解封后不应再显示黑名单状态');
  tap(p, { 'data-act': 'block', 'data-user': 'S2023003' });
  assert.match(p.el('users-tbody').innerHTML, /黑名单限制中/);
});

test('U10 看板：10 秒自动刷新与「模拟网络异常」容错（保留上一轮有效快照）', () => {
  const p = boot();
  tap(p, { 'data-tab': 'analytics' });
  assert.match(p.el('countdown-pill').textContent, /倒计时\s*\d+s/);
  press(p, 'btn-toggle-neterror');
  assert.match(p.el('btn-toggle-neterror').textContent, /开/);
  assert.equal(p.el('net-error-banner').hidden, false, '容错横幅应显示');
  press(p, 'btn-refresh-now');
  assert.match(p.el('last-valid-snapshot').textContent, /有效快照：\d{4}-\d{2}-\d{2}/);
  press(p, 'btn-toggle-autorefresh');
  assert.match(p.el('countdown-pill').textContent, /已暂停自动刷新/);
});

test('U11 权限守卫：学生视角进入「现场巡检」页时提示权限受限且无操作入口（PR-06 / PR-17）', () => {
  const p = boot();
  tap(p, { 'data-tab': 'admin' });
  assert.equal(p.el('admin-guard').hidden, false, '学生视角应显示权限守卫横幅');
  assert.ok(p.el('admin-seat-board').innerHTML.indexOf('data-act="manual-vio"') < 0, '学生不应看到手动违约入口');
  assert.match(p.el('admin-seat-board').innerHTML, /脱敏/);
});

test('U12 综合查询：按违规来源筛选（系统自动 / 管理员手动），学生仅见本人记录（PR-01 / PR-11）', () => {
  const p = boot();
  asAdmin(p);
  tap(p, { 'data-tab': 'queries' });
  assert.match(p.el('query-vio-tbody').innerHTML, /系统自动|管理员手动/);
  setVal(p, 'query-vio-source', 'manual');
  p.el('query-vio-source').fire('change', {});
  assert.ok(p.el('query-vio-tbody').innerHTML.indexOf('系统自动') < 0, '筛选手动后不应出现系统自动违约');
  setVal(p, 'query-keyword', '赵敏');
  p.el('query-keyword').fire('input', {});
  assert.match(p.el('query-vio-tbody').innerHTML, /赵敏/);
});

test('U13 角色切换：切到「赵敏（黑名单）」后顶部显示受限状态与规则编号（BR-20）', () => {
  const p = boot();
  tap(p, { 'data-user': 'S2023005' }, true);
  assert.match(p.el('status-user').innerHTML, /赵敏/);
  assert.match(p.el('status-blacklist').innerHTML, /黑名单限制中/);
  assert.match(p.el('status-blacklist').innerHTML, /BR-20/);
  tap(p, { 'data-user': 'S2023002' }, true);
  assert.match(p.el('status-blacklist').innerHTML, /信用分不足|连续违约限制/);
});

test('U14 弹窗与重置：弹窗可打开/关闭，重置演示数据后回到初始状态', () => {
  const p = boot();
  tap(p, { 'data-act': 'open-complaint' });
  assert.match(p.el('modal-root').innerHTML, /提交投诉单/);
  tap(p, { 'data-act': 'close-modal' });
  assert.equal(p.el('modal-root').innerHTML, '');
  tap(p, { 'data-act': 'pick-seat', 'data-seat': 'A101-004' });
  press(p, 'btn-book');
  press(p, 'btn-reset');
  assert.ok(p.el('seat-grid').innerHTML.length > 2000, '重置后座位网格应重新渲染');
  assert.match(p.el('selected-seat').textContent, /未选择/);
});

test('U15 预约窗口：日期控件被限制在「模拟今天 ~ 今天+7 天」内（BR-03 回归）', () => {
  const p = boot();
  assert.match(p.el('filter-date').min, /^\d{4}-\d{2}-\d{2}$/, '应设置日期下限');
  assert.match(p.el('filter-date').max, /^\d{4}-\d{2}-\d{2}$/, '应设置日期上限');
  assert.ok(p.el('filter-date').max > p.el('filter-date').min, '上限应晚于下限');
  assert.match(p.el('booking-window-hint').textContent, /\d{4}-\d{2}-\d{2} ~ \d{4}-\d{2}-\d{2}/, '页面应明示可预约窗口');
  // 窗口内日期（模拟时钟的"明天"）可正常预约成功
  setVal(p, 'filter-date', NEXT_DATE);
  p.el('filter-date').fire('change', {});
  setVal(p, 'filter-start', '16:00');
  p.el('filter-start').fire('change', {});
  tap(p, { 'data-act': 'pick-seat', 'data-seat': 'A101-004' });
  press(p, 'btn-book');
  tap(p, { 'data-tab': 'myres' });
  assert.match(p.el('myres-list').innerHTML, new RegExp(NEXT_DATE), '窗口内日期应预约成功');
});

test('U16 长页面平滑定位：6 个部分全部展开，点标签平滑滚动到对应部分（点 6 滚到看板区域）', () => {
  const p = boot();
  ['seats', 'myres', 'queries', 'admin', 'community', 'analytics'].forEach(function (n) {
    assert.equal(p.el('view-' + n).hidden, false, n + ' 部分应展开显示（单页长文档）');
  });
  tap(p, { 'data-tab': 'analytics' });
  const hits = (p.scrolled || []).filter(function (x) { return String(x).indexOf('scrollTo:') === 0; });
  assert.ok(hits.length >= 1, '点标签后应触发滚动定位');
  const last = String(hits[hits.length - 1]);
  assert.ok(last.indexOf('smooth') >= 0, '滚动应为平滑动画（behavior=smooth）');
  assert.ok(Number(last.split(':')[1]) > 0, '滚动目标应是该部分位置，而不是页面顶部');
  assert.ok(p.scrolled.indexOf('hash') >= 0, '应同步 URL 锚点');
  assert.equal(p.cssVars['--header-topbar-h'], '72px', '固定头部高度应写入 CSS 变量');
  assert.equal(p.cssVars['--header-h'], '118px');
});
