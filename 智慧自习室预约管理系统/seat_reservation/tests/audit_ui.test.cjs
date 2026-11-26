const test = require('node:test');
const assert = require('node:assert/strict');
const { harness } = require('./support/frontend_harness.cjs');
test('audit translates labels while retaining filter codes and escapes identifiers', async () => {
  const h = harness();
  h.a.state.auditAction = 'user_create';
  h.respond(() => ({ total: 1, items: [{ id: '1', actor_id: '<img>', actor_role: 'sys_admin', action: 'user_create', target_type: 'user', target_id: 'S1' }] }));
  const html = await h.a.auditView();
  assert.match(html, /value="user_create" selected>创建账号/);
  assert.match(html, /系统管理员/);
  assert.match(html, /账号 \/ S1/);
  assert.ok(!html.includes('<img>'));
  assert.equal(h.calls[0].options.params.action, 'user_create');
  await h.a.openAuditDetail('1');
  assert.match(h.panels[0].body, /创建账号/);
  assert.ok(!h.panels[0].body.includes('sys_admin'));
});
