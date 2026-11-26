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
  db: { schema_version: 5, size_bytes: 204_800, users: 9 },
  uptime_seconds: 125,
  scheduler: { enabled: true, running: true, interval_seconds: 60 },
};

test('system view renders schema v5, runtime state, scheduler and export panel', async () => {
  const h = harness();
  h.respond((url) => (url === '/admin/system'
    ? SYSTEM_PAYLOAD
    : { enabled: true, running: true, interval_seconds: 60, last_run_at: '2026-11-26 12:00:00', last_summary: { no_show: 1 } }));
  const html = await h.a.systemView();
  assert.match(html, /数据库版本/);
  assert.match(html, /v5/);
  assert.match(html, /运行状态/);
  assert.match(html, /正常/);
  assert.match(html, /运行中/);
  assert.match(html, /数据导出/);
  assert.ok(!html.includes("演示数据（课程演示与测试）"));
  assert.ok(!html.includes("初始化演示数据"));
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
  // 空库/旧后端/字段缺失时仍要完整渲染，数据库版本兜底为 v5。
  assert.match(html, /v5/);
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
  assert.match(html, /v5/);
  assert.match(html, /数据导出/);
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
