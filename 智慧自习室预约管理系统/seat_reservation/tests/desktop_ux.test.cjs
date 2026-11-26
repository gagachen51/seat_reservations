// 桌面端 7 项交互/逻辑缺陷的 Node 视图测试（无第三方依赖）。
const test = require('node:test');
const assert = require('node:assert/strict');
const { harness } = require('./support/frontend_harness.cjs');

function seat(id, overrides = {}) {
  return {
    id, room_id: 'R1', room_name: '第一教学楼 A101',
    type_label: '普通位', state: 'available', state_label: '空闲', available: true,
    has_power: false, is_window: false, ...overrides,
  };
}

function meta() {
  return {
    today: '2026-11-26',
    rules: { booking_window_days: 7, min_minutes: 30, max_minutes: 240 },
    rooms: [{ id: 'R1', name: '第一教学楼 A101', location: '一楼东侧' }],
    complaint_types: { noise: '噪音', facility: '设施' },
  };
}

function filters() {
  return { date: '2026-11-26', start: '09:00', duration: 60, room: '', power: false, window: false, onlyAvailable: true };
}

test('seat room subtitle uses physical total_seats instead of filtered length', async () => {
  const h = harness();
  h.a.state.meta = meta();
  h.a.state.seatFilters = filters();
  const items = Array.from({ length: 7 }, (_, index) => seat('R1-00' + (index + 1)));
  h.respond((url) => (url === '/seats'
    ? { items, total: 7, rooms: [{ id: 'R1', name: '第一教学楼 A101', total_seats: 8 }], date: '2026-11-26' }
    : {}));
  const html = await h.a.seatsView();
  assert.match(html, /7 \/ 8 空闲/);
  assert.ok(!html.includes('7 / 7'));
  // 不可预约座位要直接显示状态标签，而不是只显示类型。
  const disabledHtml = html;
  assert.ok(disabledHtml.includes('R1-001'));
});

test('unavailable seat cards show state label and gray state data', async () => {
  const h = harness();
  h.a.state.meta = meta();
  h.a.state.seatFilters = filters();
  const items = [
    seat('R1-001', { state: 'maintenance', state_label: '维护中', available: false }),
    seat('R1-002', { state: 'disabled', state_label: '停用', available: false }),
  ];
  h.respond((url) => (url === '/seats'
    ? { items, total: 2, rooms: [{ id: 'R1', name: '第一教学楼 A101', total_seats: 8 }] }
    : {}));
  const html = await h.a.seatsView();
  assert.match(html, /data-state="maintenance"/);
  assert.match(html, /data-state="disabled"/);
  assert.match(html, /维护中/);
  assert.match(html, /停用/);
});

test('disabled seat info offers only close, occupied seat offers waitlist', () => {
  const h = harness();
  h.a.openSeatInfo(seat('R1-004', { state: 'disabled', state_label: '停用', available: false }));
  assert.equal(h.panels[0].actions.length, 1);
  assert.equal(h.panels[0].actions[0].label, '关闭');

  const h2 = harness();
  h2.a.openSeatInfo(seat('R1-003', { state: 'reserved', state_label: '已预约', available: false }));
  assert.ok(h2.panels[0].actions.some((action) => action.label === '加入候补'));
});

test('user time conflict is not presented as seat occupied and has no waitlist', async () => {
  const h = harness();
  h.a.state.meta = meta();
  h.a.state.seatFilters = filters();
  h.a.state.selectedSeat = seat('R1-002');
  h.respond(() => { const error = new Error('您在当前时段已有预约，每人同时段仅限预约 1 个座位'); error.code = 'USER_TIME_CONFLICT'; throw error; });
  await h.a.bookSelected();
  assert.equal(h.panels.length, 1);
  assert.match(h.panels[0].title, /您在当前时段已有预约/);
  assert.equal(h.panels[0].actions.length, 1);
  assert.equal(h.panels[0].actions[0].label, '知道了');
  assert.ok(!h.panels[0].actions.some((action) => action.label === '加入候补'));
});

test('disabled seat booking error has no waitlist button', async () => {
  const h = harness();
  h.a.state.meta = meta();
  h.a.state.seatFilters = filters();
  h.a.state.selectedSeat = seat('R1-004');
  h.respond(() => { const error = new Error('该座位已停用维护，暂不支持预约'); error.code = 'SEAT_DISABLED'; throw error; });
  await h.a.bookSelected();
  assert.match(h.panels[0].title, /座位不可预约/);
  assert.equal(h.panels[0].actions.length, 1);
  assert.ok(!h.panels[0].actions.some((action) => action.label === '加入候补'));
});

test('only SEAT_OCCUPIED opens the waitlist confirmation', async () => {
  const h = harness();
  h.a.state.meta = meta();
  h.a.state.seatFilters = filters();
  h.a.state.selectedSeat = seat('R1-003');
  h.respond(() => { const error = new Error('该时段已被占用'); error.code = 'SEAT_OCCUPIED'; throw error; });
  await h.a.bookSelected();
  assert.match(h.panels[0].title, /该时段已被占用/);
  assert.ok(h.panels[0].actions.some((action) => action.label === '加入候补'));
});

test('new complaint sheet loads live seat options from the latest rooms', async () => {
  const h = harness();
  h.a.state.meta = meta();
  h.respond((url) => (url === '/seats'
    ? { items: [
        { id: 'A101-001', room_id: 'A101', room_name: '第一教学楼 A101' },
        { id: 'NEW-001', room_id: 'NEW', room_name: '新建自习室' },
      ] }
    : {}));
  await h.a.newComplaintSheet();
  const body = h.panels[0].body;
  assert.match(body, /<select id="complaintSeat">/);
  assert.match(body, /不指定具体座位（选填）/);
  assert.match(body, /第一教学楼 A101 · A101-001/);
  assert.match(body, /新建自习室 · NEW-001/);
  assert.ok(!body.includes('<input id="complaintSeat"'));
  assert.equal(h.calls[0].url, '/seats');
  assert.equal(h.calls[0].options.params.date, '2026-11-26');
});

test('analytics metric guide is Chinese-only and rendered as 4x2 cards with formula pills', () => {
  const h = harness();
  h.a.state.meta = meta();
  const html = h.a.analyticsHtml({
    kpi: {}, rooms: [], daily: [], peak_hours: [],
    violations: { by_type: [] }, ratings: { distribution: [] },
    waitlist: { by_status: [] }, complaints: {}, metric_notes: {},
  });
  assert.match(html, /指标口径/);
  assert.equal((html.match(/analytics-metric-card/g) || []).length, 8);
  assert.match(html, /analytics-metric-grid/);
  assert.match(html, /analytics-formula/);
  for (const english of ['completed', 'no_show', 'pending', 'in_use', 'away', 'released', 'cancelled']) {
    assert.ok(!html.includes(english), 'should not expose ' + english);
  }
});

test('desktop export calls pywebview save bridge with BOM and reports saved path', async () => {
  const h = harness();
  let saved = null;
  h.window.pywebview = {
    api: {
      save_export_file: async (name, content) => {
        saved = { name, content };
        return { ok: true, path: 'D:/导出/预约记录.csv' };
      },
    },
  };
  h.setFetch(async () => ({ ok: true, status: 200, text: async () => 'a,b\n1,2' }));
  const messages = [];
  h.setToast((message) => messages.push(message));
  await h.a.exportAdminCsv('reservations', '预约记录');
  assert.ok(saved);
  assert.match(saved.name, /^reservations-\d{8}-\d{4}\.csv$/);
  assert.ok(saved.content.startsWith('\ufeff'));
  assert.match(messages[messages.length - 1], /已保存到 D:\/导出\/预约记录\.csv/);
  assert.equal(h.created.length, 0);
});

test('web export falls back to Blob download when no desktop bridge exists', async () => {
  const h = harness();
  h.setFetch(async () => ({ ok: true, status: 200, text: async () => 'x,y\n3,4' }));
  await h.a.exportAdminCsv('audit', '审计日志');
  const link = h.created.find((element) => element.tag === 'a');
  assert.ok(link);
  assert.match(link.download, /^audit-\d{8}-\d{4}\.csv$/);
  assert.equal(link.clicked, true);
  assert.ok(h.appended.includes(link));
});

test('demo init success sheet explains that the existing admin password is preserved', () => {
  const h = harness();
  h.a.demoInitializedSheet({
    demo_password: 'Demo-Pass-123456',
    admin_password_preserved: true,
    counts: { users: 9, rooms: 3, seats: 24 },
    accounts: [
      { username: 'S2023001', name: '张伟', role: 'student', role_label: '学生' },
      { username: 'T7001', name: '刘明', role: 'teacher', role_label: '教师' },
      { username: 'A9001', name: '王丽', role: 'room_admin', role_label: '自习室管理员' },
      { username: 'Y0001', name: '陈刚', role: 'sys_admin', role_label: '系统管理员', password_preserved: true },
    ],
  });
  const body = h.panels[0].body;
  assert.match(body, /当前系统管理员原有密码已保留/);
  assert.match(body, /原密码保留/);
  assert.match(body, /统一密码仅适用于学生、教师与自习室管理员演示账号/);
  assert.match(body, /Demo-Pass-123456/);
});
