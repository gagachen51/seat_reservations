// 共享的无第三方依赖前端测试夹具：从 app.js 截取到事件绑定之前，
// 在 vm 沙箱里暴露视图函数与可控的 api/openSheet/浏览器替身。
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
  const created = [];
  const appended = [];
  const windowObj = {};
  let fetchImpl = async () => { throw new Error('fetch not mocked'); };
  let toastImpl = () => {};

  class URLCtor extends URL {
    static createObjectURL(blob) { return 'blob:mock-' + (blob.parts ? blob.parts[0].length : 0); }
    static revokeObjectURL() {}
  }
  class BlobMock {
    constructor(parts, options = {}) { this.parts = parts; this.options = options; }
  }
  const documentObj = {
    querySelector: (selector) => elements[selector],
    querySelectorAll: () => [],
    createElement: (tag) => {
      const element = {
        tag, style: {}, href: '', download: '', clicked: false,
        click() { element.clicked = true; },
        remove() {},
        setAttribute(name, value) { element[name] = value; },
        focus() {}, select() {},
      };
      created.push(element);
      return element;
    },
    body: {
      dataset: {},
      style: {},
      appendChild: (element) => { appended.push(element); },
      removeChild: () => {},
    },
    addEventListener: () => {},
  };

  const context = vm.createContext({
    document: documentObj,
    window: windowObj,
    location: { hash: '', origin: 'http://127.0.0.1:8010', search: '' },
    fetch: (...args) => fetchImpl(...args),
    Blob: BlobMock,
    URL: URLCtor,
    navigator: { clipboard: { writeText: async () => {} } },
    setTimeout: (fn) => fn(), clearTimeout() {}, setInterval() {}, clearInterval() {},
    console, Date,
  });
  vm.runInContext(source.slice(0, end) + `
    globalThis.account = {
      state, usersView, accountSheet, accountStatusSheet, accountResetSheet,
      sessionSheet, profileSheet, waitlistView, accountConfirmWaitlist, accountCancelWaitlist,
      showLogin, submitLogin, logout, changePasswordSheet, restoreSession,
      systemView, auditView, openAuditDetail, ACTIONS,
      mergeOptions, datalistOptions,
      seatsView, seatCard, openSeatInfo, openNoticeSheet, bookSelected, joinWaitlist,
      newComplaintSheet, analyticsHtml, ANALYTICS_METRIC_GUIDE, exportAdminCsv,
      setApi(fn) { api = fn; }, setPanel(fn) { openSheet = fn; },
      setEnterApp(fn) { enterApp = fn; },
      setRenderView(fn) { renderView = fn; },
      setToast(fn) { toast = fn; }
    };
    renderView = () => {};
    renderUser = () => {};
    refreshBadge = () => {};
    enterApp = () => {};
    toast = () => {};
    go = () => {};
    closeSheet = () => { state.sheet = null; };
  })();`, context);
  const api = context.account;
  api.state.user = { id: 'Y1', name: '管理员', role: 'sys_admin', department: '计算机' };
  api.setPanel((config) => panels.push(config));
  api.setToast((message, options) => { toastImpl(message, options); });
  return {
    a: api, panels, calls, elements, created, appended, context, window: windowObj,
    field(id, value, valid = true) {
      elements[id] = { value, reportValidity: () => valid, focus() {} };
    },
    respond(fn) {
      api.setApi(async (url, options = {}) => {
        calls.push(JSON.parse(JSON.stringify({ url, options })));
        return fn(url, options);
      });
    },
    setFetch(fn) { fetchImpl = fn; },
    setToast(fn) { toastImpl = fn; },
  };
}

module.exports = { harness };
