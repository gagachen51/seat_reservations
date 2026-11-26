// 共享的无第三方依赖前端测试夹具：从 app.js 截取到事件绑定之前，
// 在 vm 沙箱里暴露视图函数与可控的 api/openSheet 替身。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS = path.join(__dirname, '../../src/seat_reservation/web/app.js');

function harness() {
  const source = fs.readFileSync(APP_JS, 'utf8');
  const end = source.indexOf('  // 事件绑定');
  assert.ok(end > 0);
  const elements = {
    '#app': { hidden: false },
    '#login': { hidden: true },
    '#boot': { hidden: false },
    '#loginBtn': { disabled: false, textContent: '登录' },
    '#loginError': { hidden: true, textContent: '' },
    '#username': { value: '', focus() {} },
    '#password': { value: '', focus() {} },
    '#demoBox': { hidden: true, innerHTML: '' },
  };
  const panels = [];
  const calls = [];
  const context = vm.createContext({
    document: {
      querySelector: (selector) => elements[selector],
      querySelectorAll: () => [],
      body: { dataset: {} },
    },
    setTimeout: (fn) => fn(), clearTimeout() {}, setInterval() {}, clearInterval() {},
    console, URL, Date,
  });
  vm.runInContext(source.slice(0, end) + `
    globalThis.account = {
      state, usersView, accountSheet, accountStatusSheet, accountResetSheet,
      sessionSheet, profileSheet, waitlistView, accountConfirmWaitlist, accountCancelWaitlist,
      showLogin, submitLogin, logout, changePasswordSheet, restoreSession,
      systemView, initDemoDataSheet, demoInitializedSheet, ACTIONS,
      mergeOptions, datalistOptions, loadDemoAccounts,
      setApi(fn) { api = fn; }, setPanel(fn) { openSheet = fn; },
      setEnterApp(fn) { enterApp = fn; },
      setRenderView(fn) { renderView = fn; }
    };
    renderView = () => {};
    renderUser = () => {};
    refreshBadge = () => {};
    enterApp = () => {};
    toast = () => {};
    closeSheet = () => { state.sheet = null; };
  })();`, context);
  const api = context.account;
  api.state.user = { id: 'Y1', name: '管理员', role: 'sys_admin', department: '计算机' };
  api.setPanel((config) => panels.push(config));
  return {
    a: api, panels, calls, elements,
    field(id, value, valid = true) {
      elements[id] = { value, reportValidity: () => valid, focus() {} };
    },
    respond(fn) {
      api.setApi(async (url, options = {}) => {
        calls.push(JSON.parse(JSON.stringify({ url, options })));
        return fn(url, options);
      });
    },
  };
}

module.exports = { harness };
