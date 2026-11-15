// 无第三方依赖的视图/控制器测试；不替代真实浏览器视觉验收。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness() {
  const source = fs.readFileSync(path.join(__dirname, '../src/seat_reservation/web/app.js'), 'utf8');
  const end = source.indexOf('  // 事件绑定');
  assert.ok(end > 0);
  const elements = {};
  const panels = [];
  const calls = [];
  const context = vm.createContext({
    document: { querySelector: (selector) => elements[selector] },
    setTimeout: (fn) => fn(), clearTimeout() {}, setInterval() {}, clearInterval() {},
    console, URL, Date,
  });
  vm.runInContext(source.slice(0, end) + `
    globalThis.account = {
      state, usersView, accountSheet, accountStatusSheet, accountResetSheet,
      sessionSheet, profileSheet, waitlistView, accountConfirmWaitlist, accountCancelWaitlist,
      setApi(fn) { api = fn; }, setPanel(fn) { openSheet = fn; }
    };
    renderView = () => {}; renderUser = () => {}; refreshBadge = () => {};
    toast = () => {}; closeSheet = () => { state.sheet = null; };
  })();`, context);
  const a = context.account;
  a.state.user = { id: 'Y1', name: '管理员', role: 'sys_admin', department: '计算机' };
  a.setPanel(config => panels.push(config));
  return {
    a, panels, calls,
    field(id, value, valid = true) { elements[id] = { value, reportValidity: () => valid }; },
    respond(fn) { a.setApi(async (url, options = {}) => {
      calls.push(JSON.parse(JSON.stringify({ url, options })));
      return fn(url, options);
    }); },
  };
}

test('account list escapes user data and shows disabled state and paging', async () => {
  const h = harness();
  h.a.state.page = 2;
  h.respond(() => ({ total: 41, items: [{ id: 'S1', name: '<img onerror=alert(1)>',
    role: 'student', department: '<script>', status: 'disabled', must_change_password: true }] }));
  const html = await h.a.usersView();
  assert.ok(html.includes('&lt;img onerror=alert(1)&gt;'));
  assert.ok(!html.includes('<img') && !html.includes('<script>'));
  assert.match(html, /已停用/);
  assert.match(html, /需修改密码/);
  assert.match(html, /第 2 页/);
  assert.equal(h.calls[0].options.params.page, 2);
  assert.equal(h.calls[0].options.params.page_size, 20);
});

test('current administrator cannot click disable-self button', async () => {
  const h = harness();
  h.respond(() => ({ total: 1, items: [{ ...h.a.state.user, status: 'active' }] }));
  assert.match(await h.a.usersView(), /disabled title="不能停用自己的账号"/);
});

test('create form sends trimmed fields and requests generated password only when blank', async () => {
  const h = harness();
  h.respond(() => ({ user: { id: 'S9' }, initial_password: 'Generated-123' }));
  h.field('#accountForm', '', true);
  h.field('#accountId', ' S9 '); h.field('#accountName', ' 新姓名 ');
  h.field('#accountRole', 'student'); h.field('#accountDepartment', ' 院系 ');
  h.field('#accountClass', ' 班级 '); h.field('#accountPassword', '');
  h.a.accountSheet();
  await h.panels[0].actions[1].onClick();
  assert.deepEqual(h.calls[0], { url: '/admin/users', options: { method: 'POST',
    body: { id: 'S9', name: '新姓名', role: 'student', department: '院系', class_name: '班级' } } });
  assert.match(h.panels[1].body, /Generated-123/);
  assert.ok(!JSON.stringify(h.a.state).includes('Generated-123'));
});

test('invalid create form never sends a request', async () => {
  const h = harness();
  h.respond(() => { throw new Error('should not call'); });
  h.field('#accountForm', '', false);
  h.a.accountSheet();
  await h.panels[0].actions[1].onClick();
  assert.equal(h.calls.length, 0);
});

test('status change requires a reason and targets only the selected account', async () => {
  const h = harness();
  h.respond(() => ({}));
  h.field('#accountStatusForm', '', true); h.field('#accountReason', ' 暂停使用 ');
  h.a.accountStatusSheet({ id: 'S@9', name: '同学', status: 'active' });
  await h.panels[0].actions[1].onClick();
  assert.deepEqual(h.calls[0], { url: '/admin/users/S%409/status',
    options: { method: 'PUT', body: { status: 'disabled', reason: '暂停使用' } } });
});

test('password reset calls the correct endpoint and displays the new password once', async () => {
  const h = harness();
  h.respond(() => ({ initial_password: 'Reset-12345678' }));
  h.a.accountResetSheet({ id: 'S1', name: '同学' });
  await h.panels[0].actions[1].onClick();
  assert.equal(h.calls[0].url, '/admin/users/S1/reset-password');
  assert.match(h.panels[1].body, /Reset-12345678/);
  assert.ok(!JSON.stringify(h.a.state).includes('Reset-12345678'));
});

test('session view escapes untrusted user agent and revokes only other devices', async () => {
  const h = harness();
  const data = { items: [
    { ip: '127.0.0.1', user_agent: '<img src=x>', current: true },
    { ip: '127.0.0.2', user_agent: 'Other', current: false },
  ] };
  h.respond(url => url.endsWith('revoke-others') ? { revoked: 1 } : data);
  await h.a.sessionSheet();
  assert.match(h.panels[0].body, /当前设备/);
  assert.match(h.panels[0].body, /&lt;img src=x&gt;/);
  assert.ok(!h.panels[0].body.includes('<img'));
  await h.panels[0].actions[1].onClick();
  assert.ok(h.calls.some(c => c.url === '/me/sessions/revoke-others' && c.options.method === 'POST'));
});

test('profile updates only name and department and refreshes user state', async () => {
  const h = harness();
  h.respond(() => ({ user: { ...h.a.state.user, name: '新姓名', department: '' } }));
  h.field('#accountProfileForm', '', true); h.field('#profileName', ' 新姓名 '); h.field('#profileDepartment', '');
  h.a.profileSheet();
  await h.panels[0].actions[1].onClick();
  assert.deepEqual(h.calls[0].options.body, { name: '新姓名', department: '' });
  assert.equal(h.a.state.user.name, '新姓名');
});

test('waitlist buttons follow status and deadline and escape seat text', async () => {
  const h = harness();
  const future = new Date(Date.now() + 3600000).toISOString();
  const past = new Date(Date.now() - 3600000).toISOString();
  h.respond(() => ({ total: 5, items: [
    { id: 'Q1', seat_id: '<img>', status: 'queued', seq: 1 },
    { id: 'N1', seat_id: '座位1', status: 'notified', seq: 2, confirm_deadline: future },
    { id: 'N2', seat_id: '座位2', status: 'notified', seq: 3, confirm_deadline: past },
    { id: 'C1', seat_id: '座位3', status: 'confirmed', seq: 4 },
    { id: 'E1', seat_id: '座位4', status: 'expired', seq: 5 },
  ] }));
  const html = await h.a.waitlistView();
  assert.ok(html.includes('&lt;img&gt;') && !html.includes('<img>'));
  assert.match(html, /account-waitlist-confirm" data-id="N1">/);
  assert.match(html, /account-waitlist-confirm" data-id="N2" disabled>/);
  assert.equal((html.match(/data-action="account-waitlist-cancel"/g) || []).length, 3);
  assert.ok(!/account-waitlist-cancel" data-id="[CE]1"/.test(html));
});

test('waitlist confirmation blocks duplicate clicks and enables retry after failure', async () => {
  const h = harness();
  h.respond(() => { throw new Error('名额已被占用'); });
  const button = { dataset: { id: 'N@1' }, disabled: true };
  await h.a.accountConfirmWaitlist(button);
  assert.equal(h.calls.length, 0);
  button.disabled = false;
  await assert.rejects(h.a.accountConfirmWaitlist(button), /名额已被占用/);
  assert.equal(button.disabled, false);
  assert.equal(h.calls[0].url, '/waitlist/N%401/confirm');
});

test('waitlist cancellation uses C endpoint only after confirmation', async () => {
  const h = harness();
  h.respond(() => ({}));
  h.a.accountCancelWaitlist('Q@1');
  assert.equal(h.calls.length, 0);
  await h.panels[0].actions[1].onClick();
  assert.deepEqual(h.calls[0], { url: '/waitlist/Q%401/cancel', options: { method: 'POST', body: {} } });
});
