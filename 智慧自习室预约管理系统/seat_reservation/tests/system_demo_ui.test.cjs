// 系统管理页 / 新建账号联想的 Node 视图测试；不替代真实浏览器视觉验收。
const test = require('node:test');
const assert = require('node:assert/strict');
const { harness } = require('./support/frontend_harness.cjs');

const SYSTEM_PAYLOAD = {
  status: 'ok',
  version: '2.0.0',
  env: 'development',
  demo_mode: false,
  demo_data: false,
  db: { schema_version: 4, size_bytes: 204_800, users: 9 },
  uptime_seconds: 125,
  scheduler: { enabled: true, running: true, interval_seconds: 60 },
};

test('system view renders schema v4, runtime state, scheduler and export panel', async () => {
  const h = harness();
  h.respond((url) => (url === '/admin/system'
    ? SYSTEM_PAYLOAD
    : { enabled: true, running: true, interval_seconds: 60, last_run_at: '2026-11-26 12:00:00', last_summary: { no_show: 1 } }));
  const html = await h.a.systemView();
  assert.match(html, /数据库版本/);
  assert.match(html, /v4/);
  assert.match(html, /运行状态/);
  assert.match(html, /正常/);
  assert.match(html, /运行中/);
  assert.match(html, /数据导出/);
  assert.match(html, /演示数据（课程演示与测试）/);
  assert.match(html, /初始化演示数据/);
  assert.ok(!html.includes('undefined'));
  assert.ok(!html.includes('Cannot read properties'));
});

test('system view survives missing db/scheduler fields instead of throwing', async () => {
  const h = harness();
  h.respond((url) => {
    if (url === '/admin/system') return { status: 'ok' };
    throw new Error('scheduler unavailable');
  });
  const html = await h.a.systemView();
  // 空库/旧后端/字段缺失时仍要完整渲染，数据库版本兜底为 v4。
  assert.match(html, /v4/);
  assert.match(html, /已禁用/);
  assert.match(html, /数据导出/);
  assert.ok(!html.includes('undefined'));
  assert.ok(!html.includes('NaN'));
});

test('system view shows a banner when the status endpoint fails entirely', async () => {
  const h = harness();
  h.respond(() => { throw new Error('boom'); });
  const html = await h.a.systemView();
  assert.match(html, /运行状态暂时无法获取/);
  assert.match(html, /v4/);
  assert.match(html, /数据导出/);
});

test('system view marks demo data as initialized after seeding', async () => {
  const h = harness();
  h.respond((url) => (url === '/admin/system'
    ? { ...SYSTEM_PAYLOAD, demo_data: true, db: { ...SYSTEM_PAYLOAD.db, users: 9 } }
    : { enabled: false, running: false }));
  const html = await h.a.systemView();
  assert.match(html, /演示数据已初始化/);
  assert.match(html, /重新初始化演示数据/);
});

test('new account sheet merges history with presets and keeps free input', async () => {
  const h = harness();
  h.respond(() => ({
    total: 3,
    items: [
      { id: 'S1', department: '信息工程学院', class_name: '计科2301' },
      { id: 'S2', department: ' 信息工程学院 ', class_name: '计科2301' },
      { id: 'S3', department: '人工智能学院', class_name: '' },
    ],
  }));
  await h.a.accountSheet();
  const body = h.panels[0].body;
  assert.match(body, /院系（选填，可直接输入）/);
  assert.match(body, /班级（选填，可直接输入）/);
  assert.match(body, /placeholder="例如：计算机科学与技术学院"/);
  assert.match(body, /placeholder="例如：计科2301"/);
  assert.match(body, /id="accountDepartment" maxlength="100" list="accountDepartmentOptions"/);
  assert.match(body, /id="accountClass" maxlength="100" list="accountClassOptions"/);
  // 历史值与预设都在，且重复值只出现一次。
  assert.match(body, /人工智能学院/);
  assert.match(body, /软件学院/);
  assert.match(body, /软工2301/);
  assert.equal((body.match(/<option value="信息工程学院"><\/option>/g) || []).length, 1);
  assert.ok(!/id="accountDepartment"[^>]*readonly/.test(body));
});

test('new account sheet still offers presets when the directory is empty', async () => {
  const h = harness();
  h.respond(() => ({ total: 0, items: [] }));
  await h.a.accountSheet();
  const body = h.panels[0].body;
  assert.match(body, /计算机科学与技术学院/);
  assert.match(body, /信息工程学院/);
  assert.match(body, /计科2301/);
  assert.match(body, /软件 2301/);
});

test('demo initialization opens a destructive confirmation and sends nothing yet', () => {
  const h = harness();
  h.a.ACTIONS['system-init-demo']();
  assert.equal(h.panels.length, 1);
  assert.equal(h.calls.length, 0);
  const panel = h.panels[0];
  assert.match(panel.title, /初始化演示数据/);
  assert.match(panel.subtitle, /仅用于课程演示与测试/);
  assert.match(panel.body, /清空当前库/);
  assert.match(panel.body, /统一初始密码/);
  assert.equal(panel.actions[1].label, '确认初始化');
});

test('confirming demo initialization posts the confirm phrase and shows credentials once', async () => {
  const h = harness();
  let rendered = 0;
  h.a.setRenderView(() => { rendered += 1; });
  h.respond((url) => {
    assert.equal(url, '/admin/system/demo-data');
    return {
      counts: { users: 9, rooms: 3, seats: 24 },
      accounts: [
        { username: 'S2023001', name: '张伟', role: 'student', role_label: '学生' },
        { username: 'T7001', name: '刘明', role: 'teacher', role_label: '教师' },
        { username: 'A9001', name: '王丽', role: 'room_admin', role_label: '自习室管理员' },
        { username: 'Y0001', name: '陈刚', role: 'sys_admin', role_label: '系统管理员' },
      ],
      demo_password: 'Demo-Pass-123456',
      must_relogin: true,
    };
  });

  h.a.ACTIONS['system-init-demo']();
  await h.panels[0].actions[1].onClick();

  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.calls[0], {
    url: '/admin/system/demo-data',
    options: { method: 'POST', body: { confirm: 'INITIALIZE_DEMO_DATA' } },
  });
  assert.equal(h.panels.length, 2);
  const success = h.panels[1];
  assert.match(success.title, /演示数据已初始化/);
  assert.match(success.body, /Demo-Pass-123456/);
  assert.match(success.body, /S2023001/);
  assert.match(success.body, /T7001/);
  assert.match(success.body, /A9001/);
  assert.match(success.body, /当前登录会话已失效/);
  assert.equal(success.actions[1].label, '重新登录');
  // 成功后不能立即 renderView（会话已失效），也不能把密码放进客户端状态。
  assert.equal(rendered, 0);
  assert.ok(!JSON.stringify(h.a.state).includes('Demo-Pass-123456'));
});

test('cancelling demo initialization sends no request', async () => {
  const h = harness();
  h.a.initDemoDataSheet();
  await h.panels[0].actions[0].onClick();
  assert.equal(h.calls.length, 0);
  assert.equal(h.panels.length, 1);
});

test('login page demo account shortcuts no longer depend on /health.demo', async () => {
  const h = harness();
  h.respond(() => ([
    { username: 'S2023001', name: '张伟', role: 'student' },
    { username: 'Y0001', name: '陈刚', role: 'sys_admin' },
  ]));
  await h.a.loadDemoAccounts();
  assert.deepEqual(h.calls.map((call) => call.url), ['/auth/demo-accounts']);
  assert.equal(h.elements['#demoBox'].hidden, false);
  assert.match(h.elements['#demoBox'].innerHTML, /S2023001/);
  assert.match(h.elements['#demoBox'].innerHTML, /不通过接口下发/);
});

test('returning to login refreshes demo shortcuts after in-app initialization', async () => {
  const h = harness();
  h.respond(() => ([
    { username: 'S2023001', name: '张伟', role: 'student' },
    { username: 'Y0001', name: '陈刚', role: 'sys_admin' },
  ]));
  h.a.showLogin('演示数据已初始化，请使用演示账号重新登录');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(h.calls.map((call) => call.url), ['/auth/demo-accounts']);
  assert.equal(h.elements['#demoBox'].hidden, false);
  assert.match(h.elements['#demoBox'].innerHTML, /S2023001/);
  assert.match(h.elements['#loginError'].textContent, /演示数据已初始化/);
});

test('mergeOptions trims, dedupes and keeps history before presets', () => {
  const h = harness();
  assert.deepEqual(
    [...h.a.mergeOptions([' B ', '', null, 'A', 'B'], ['A', 'C'])],
    ['B', 'A', 'C'],
  );
});

test('datalistOptions escapes user-controlled history values', () => {
  const h = harness();
  const html = h.a.datalistOptions(['"><img src=x onerror=alert(1)>']);
  assert.match(html, /&quot;&gt;&lt;img/);
  assert.ok(!html.includes('<img'));
});
