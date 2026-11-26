// 无第三方依赖的视图/控制器测试；不替代真实浏览器视觉验收。
const test = require('node:test');
const assert = require('node:assert/strict');
const { harness } = require('./support/frontend_harness.cjs');

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
  h.respond((url, options) => {
    if (url === '/admin/users' && (options.method || 'GET') === 'GET') {
      return { total: 2, items: [
        { id: 'S1', name: '甲', department: '信息工程学院', class_name: '计科2301' },
        { id: 'S2', name: '乙', department: '软件学院', class_name: '软工2302' },
      ] };
    }
    return { user: { id: 'S9' }, initial_password: '12345678' };
  });
  h.field('#accountForm', '', true);
  h.field('#accountId', ' S9 '); h.field('#accountName', ' 新姓名 ');
  h.field('#accountRole', 'student'); h.field('#accountDepartment', ' 院系 ');
  h.field('#accountClass', ' 班级 '); h.field('#accountPassword', '');
  await h.a.accountSheet();
  assert.match(h.panels[0].body, /院系（选填，可直接输入）/);
  assert.match(h.panels[0].body, /初始密码（留空则使用临时密码 12345678）/);
  assert.match(h.panels[0].body, /例如：计算机科学与技术学院/);
  assert.match(h.panels[0].body, /例如：计科2301/);
  assert.match(h.panels[0].body, /信息工程学院/);
  assert.match(h.panels[0].body, /计科2301/);
  await h.panels[0].actions[1].onClick();
  const post = h.calls.find((call) => call.options.method === 'POST');
  assert.deepEqual(post, { url: '/admin/users', options: { method: 'POST',
    body: { id: 'S9', name: '新姓名', role: 'student', department: '院系', class_name: '班级' } } });
  assert.match(h.panels[1].body, /已将临时密码重置为 12345678，该账号下次登录时必须修改为新密码。/);
  assert.match(h.panels[1].body, /12345678/);
  assert.ok(!JSON.stringify(h.a.state).includes('12345678'));
});

test('invalid create form never sends a request', async () => {
  const h = harness();
  h.respond(() => { throw new Error('should not call'); });
  h.field('#accountForm', '', false);
  await h.a.accountSheet();
  await h.panels[0].actions[1].onClick();
  assert.ok(!h.calls.some((call) => call.options.method === 'POST'));
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

test('password reset calls the correct endpoint and shows the 12345678 temp password', async () => {
  const h = harness();
  h.respond(() => ({ initial_password: '12345678' }));
  h.a.accountResetSheet({ id: 'S1', name: '同学' });
  assert.match(h.panels[0].body, /重置后临时密码固定为 12345678/);
  await h.panels[0].actions[1].onClick();
  assert.equal(h.calls[0].url, '/admin/users/S1/reset-password');
  assert.match(h.panels[1].body, /已将临时密码重置为 12345678，该账号下次登录时必须修改为新密码。/);
  assert.match(h.panels[1].body, /12345678/);
  assert.ok(!JSON.stringify(h.a.state).includes('12345678'));
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

test('login success clears password input and never stores password in client state', async () => {
  const h = harness();
  h.respond(url => url === '/auth/login'
    ? { csrf_token: 'csrf-1', user: { id: 'S9', name: '学生', role: 'student' } }
    : {});
  h.field('#username', 'S9');
  h.field('#password', 'Secret-Login-123');
  await h.a.submitLogin({ preventDefault() {} });
  assert.equal(h.a.state.user.id, 'S9');
  assert.equal(h.elements['#password'].value, '');
  assert.ok(!('lastLoginPassword' in h.a.state));
  assert.ok(!JSON.stringify(h.a.state).includes('Secret-Login-123'));
});

test('login failure clears password input and stale account context', async () => {
  const h = harness();
  h.respond(() => { throw new Error('账号或密码不正确'); });
  h.field('#username', 'S9');
  h.field('#password', 'Wrong-Password-123');
  await h.a.submitLogin({ preventDefault() {} });
  assert.equal(h.elements['#password'].value, '');
  assert.equal(h.a.state.user, null);
  assert.equal(h.a.state.meta, null);
  assert.equal(h.a.state.csrf, null);
  assert.ok(!JSON.stringify(h.a.state).includes('Wrong-Password-123'));
});

test('logout/showLogin clears password, view and cached account data', async () => {
  const h = harness();
  h.respond(() => ({}));
  h.elements['#password'].value = 'Previous-Password-123';
  h.a.state.view = 'users';
  h.a.state.page = 3;
  h.a.state.seatCache = [{ id: 'R1-001' }];
  h.a.state.roomsAdmin = [{ id: 'R1' }];
  await h.a.logout();
  assert.equal(h.elements['#password'].value, '');
  assert.equal(h.a.state.user, null);
  assert.equal(h.a.state.view, null);
  assert.equal(h.a.state.page, 1);
  assert.equal(h.a.state.seatCache.length, 0);
  assert.equal(h.a.state.roomsAdmin.length, 0);
  assert.ok(!('lastLoginPassword' in h.a.state));
});

test('change-password form never pre-fills a previous password', () => {
  const h = harness();
  h.a.changePasswordSheet(true);
  const body = h.panels[0].body;
  assert.ok(!body.includes('value="'));
  assert.match(body, /id="oldPassword"/);
  assert.match(body, /id="newPassword"/);
  assert.match(body, /不能使用临时密码 12345678/);
  assert.match(body, /至少 3 类/);
});

test('first login with must_change_password skips /meta and opens forced change sheet', async () => {
  const h = harness();
  let entered = 0;
  h.a.setEnterApp(() => { entered += 1; });
  h.respond(url => {
    if (url === '/auth/login') {
      return { csrf_token: 'csrf-1', user: { id: 'Y0001', name: '管理员', role: 'sys_admin', must_change_password: true } };
    }
    throw new Error('unexpected request ' + url);
  });
  h.field('#username', 'Y0001');
  h.field('#password', 'Init-Pass-123');
  await h.a.submitLogin({ preventDefault() {} });
  // /meta 在强制改密期间会被后端 403，必须完全跳过，否则 catch 会清空登录态。
  assert.deepEqual(h.calls.map(c => c.url), ['/auth/login']);
  assert.equal(h.a.state.user.id, 'Y0001');
  assert.equal(h.a.state.csrf, 'csrf-1');
  assert.equal(h.a.state.meta, null);
  assert.equal(entered, 0);
  assert.equal(h.panels.length, 1);
  assert.match(h.panels[0].title, /首次登录需修改密码/);
  assert.equal(h.elements['#loginError'].hidden, true);
  assert.equal(h.elements['#password'].value, '');
  assert.ok(!JSON.stringify(h.a.state).includes('Init-Pass-123'));
});

test('forced password change completes and returns to login with new-password hint', async () => {
  const h = harness();
  h.respond(url => {
    if (url === '/auth/login') {
      return { csrf_token: 'csrf-1', user: { id: 'Y0001', name: '管理员', role: 'sys_admin', must_change_password: true } };
    }
    if (url === '/auth/change-password') return null;
    return {};
  });
  h.field('#username', 'Y0001');
  h.field('#password', 'Init-Pass-123');
  await h.a.submitLogin({ preventDefault() {} });
  h.field('#oldPassword', 'Init-Pass-123');
  h.field('#newPassword', 'Changed-Pass-123');
  await h.panels[0].actions[0].onClick();
  assert.deepEqual(h.calls[1], {
    url: '/auth/change-password',
    options: { method: 'POST', body: { old_password: 'Init-Pass-123', new_password: 'Changed-Pass-123' } },
  });
  // 改密成功后回到登录页，提示用新密码重新登录，客户端不保留密码。
  assert.equal(h.a.state.user, null);
  assert.match(h.elements['#loginError'].textContent, /密码已修改，请重新登录/);
  assert.ok(!JSON.stringify(h.a.state).includes('Changed-Pass-123'));
});

test('session restore with must_change_password keeps session and opens forced sheet without /meta', async () => {
  const h = harness();
  let entered = 0;
  h.a.setEnterApp(() => { entered += 1; });
  h.respond(url => {
    if (url === '/auth/me') {
      return { csrf_token: 'csrf-2', user: { id: 'Y0001', name: '管理员', role: 'sys_admin', must_change_password: true } };
    }
    throw new Error('unexpected request ' + url);
  });
  await h.a.restoreSession();
  assert.deepEqual(h.calls.map(c => c.url), ['/auth/me']);
  assert.equal(h.a.state.user.id, 'Y0001');
  assert.equal(h.a.state.csrf, 'csrf-2');
  assert.equal(h.a.state.meta, null);
  assert.equal(entered, 0);
  assert.equal(h.panels.length, 1);
  assert.match(h.panels[0].title, /首次登录需修改密码/);
});

test('normal login and session restore still load /meta and enter the app', async () => {
  const h = harness();
  let entered = 0;
  h.a.setEnterApp(() => { entered += 1; });
  h.respond(url => {
    if (url === '/auth/login') {
      return { csrf_token: 'csrf-3', user: { id: 'S9', name: '学生', role: 'student', must_change_password: false } };
    }
    if (url === '/auth/me') {
      return { csrf_token: 'csrf-4', user: { id: 'S9', name: '学生', role: 'student', must_change_password: false } };
    }
    return { server_time: '2026-11-26 12:00:00' };
  });
  h.field('#username', 'S9');
  h.field('#password', 'Normal-Pass-123');
  await h.a.submitLogin({ preventDefault() {} });
  assert.deepEqual(h.calls.map(c => c.url), ['/auth/login', '/meta']);
  assert.equal(entered, 1);
  assert.equal(h.panels.length, 0);
  await h.a.restoreSession();
  assert.deepEqual(h.calls.map(c => c.url), ['/auth/login', '/meta', '/auth/me', '/meta']);
  assert.equal(entered, 2);
  assert.equal(h.panels.length, 0);
});
