/* global window, document, fetch, location, URL, setTimeout, clearTimeout, setInterval, clearInterval */
(() => {
  'use strict';

  // ---------------------------------------------------------------------------
  // 常量
  // ---------------------------------------------------------------------------
  const ROLE_TEXT = { student: '学生', teacher: '教师', room_admin: '自习室管理员', sys_admin: '系统管理员' };
  const RES_STATUS = {
    pending: ['待签到', 'orange'],
    in_use: ['使用中', 'blue'],
    away: ['暂离', 'orange'],
    completed: ['已完成', 'green'],
    cancelled: ['已取消', 'gray'],
    no_show: ['已违约', 'red'],
    released: ['已释放', 'gray'],
  };
  const VIOLATION_STATUS = { active: ['生效', 'red'], appealing: ['申诉中', 'orange'], revoked: ['已撤销', 'gray'] };
  const COMPLAINT_STATUS = { pending: ['待处理', 'orange'], resolved: ['已办结', 'green'], rated: ['已评价', 'gray'] };
  const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const VIEW_META = {
    seats: ['找座位', '按日期与时段查看空闲座位'],
    reservations: ['我的预约', '凭证、签到、暂离与签退'],
    credit: ['信用', '违规记录与信用分明细'],
    feedback: ['反馈', '投诉建议与处理结果'],
    messages: ['消息', '预约提醒与处理通知'],
    overview: ['概览', '实时使用率与近 7 日趋势'],
    floor: ['现场', '座位状态与现场处置'],
    resources: ['资源', '自习室与座位的增改、启用/停用'],
    search: ['预约查询', '按状态、日期与关键词检索'],
    complaints: ['投诉', '处理学生反馈并留痕'],
    reviews: ['评价', '查看座位与环境评价并回复'],
    appeals: ['违规申诉', '审核学生申诉，成立则撤销并回退信用'],
    students: ['学生', '信用档案与黑名单'],
    class: ['班级看板', '仅统计本班汇总'],
    params: ['参数', '业务规则数值，仅对新预约生效'],
    users: ['账号', '角色与权限分配'],
    audit: ['审计', '关键操作留痕，不可修改'],
    system: ['系统', '运行状态、调度器与导出'],
  };

  const ICONS = {
    seats: '<path d="M5 10V7.5A2.5 2.5 0 0 1 7.5 5h9A2.5 2.5 0 0 1 19 7.5V10"/><path d="M4 12.5A1.5 1.5 0 0 1 5.5 11h13A1.5 1.5 0 0 1 20 12.5V16H4z"/><path d="M6 16v3M18 16v3"/>',
    calendar: '<rect x="4" y="5" width="16" height="15" rx="2.5"/><path d="M8 3v4M16 3v4M4 10h16"/><path d="M8 14h3"/>',
    credit: '<path d="M12 3a9 9 0 1 0 9 9"/><path d="M12 7v5l3 2"/><path d="M17 3l4 4M21 3l-4 4"/>',
    feedback: '<path d="M21 12a8 8 0 0 1-8 8H6l-3 2 1.4-4.2A8 8 0 1 1 21 12z"/><path d="M8 11h8M8 15h5"/>',
    bell: '<path d="M18 8a6 6 0 1 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10.3 21a2 2 0 0 0 3.4 0"/>',
    overview: '<path d="M4 20V11M10 20V4M16 20v-7M22 20H2"/>',
    floor: '<rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 5.2a3.5 3.5 0 0 1 0 5.6M18 20a6 6 0 0 0-2-4.4"/>',
    params: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h10M18 18h2"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
    audit: '<path d="M7 3h10a2 2 0 0 1 2 2v16l-3-2-3 2-3-2-3 2V5a2 2 0 0 1 2-2z"/><path d="M9 8h6M9 12h6M9 16h4"/>',
    system: '<circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3 1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8 1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
    class: '<path d="M3 6h18M3 12h18M3 18h18"/><path d="M7 6v12M17 6v12"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    power: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    window: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 4v16M4 12h16"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    alert: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 9v5M12 17h.01"/>',
    check: '<path d="m5 12 5 5L19 7"/>',
    empty: '<path d="M4 7h16v12H4z"/><path d="M4 11h16M8 7V5h8v2"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.6"/><path d="M20 5v6h-6"/>',
    logout: '<path d="M9 4H5v16h4"/><path d="M14 8l4 4-4 4M8 12h10"/>',
    block: '<circle cx="12" cy="12" r="8.5"/><path d="m6 6 12 12"/>',
    star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  };

  function icon(name, cls = '') {
    return '<svg class="' + cls + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || '') + '</svg>';
  }

  // ---------------------------------------------------------------------------
  // 状态与工具
  // ---------------------------------------------------------------------------
  const state = {
    user: null,
    meta: null,
    csrf: null,
    view: null,
    seatFilters: { date: '', start: '09:00', duration: 60, room: '', power: false, window: false, onlyAvailable: true },
    selectedSeat: null,
    seatCache: [],
    reservationTab: 'active',
    overviewRoom: '',
    complaintTab: 'pending',
    reviewTab: 'pending',
    feedbackTab: 'complaints',
    appealTab: 'pending',
    creditTab: 'violations',
    floorDate: '',
    floorStart: '',
    resourceRoom: '',
    roomsAdmin: [],
    seatsAdmin: [],
    page: 1,
    sheet: null,
    sheetTimer: null,
    refreshTimer: null,
    lastSnapshot: null,
    lastLoginPassword: '',
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  function parseDay(iso) {
    const [y, m, d] = String(iso).split('-').map(Number);
    return new Date(y, m - 1, d);
  }

  function fmtDay(iso) {
    const d = parseDay(iso);
    return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + WEEKDAYS[d.getDay()];
  }

  function shiftDay(iso, days) {
    const d = parseDay(iso);
    d.setDate(d.getDate() + days);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function toMinutes(hhmm) {
    const [h, m] = String(hhmm).split(':').map(Number);
    return h * 60 + m;
  }

  function fromMinutes(total) {
    return pad(Math.floor(total / 60)) + ':' + pad(total % 60);
  }

  function addMinutes(hhmm, minutes) { return fromMinutes(toMinutes(hhmm) + minutes); }

  function fmtDateTime(value) {
    if (!value) return '—';
    return String(value).replace('T', ' ').slice(0, 16);
  }

  function fmtClock(value) {
    return value ? String(value).slice(11, 16) : '—';
  }

  function nowHHMM() {
    const d = new Date();
    return pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function todayISO() {
    const d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function localDateTimeValue(date) {
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }

  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : '';
  }

  function statusPill(status) {
    const meta = RES_STATUS[status] || [status, 'gray'];
    return '<span class="pill ' + meta[1] + '">' + esc(meta[0]) + '</span>';
  }

  function roleOf() { return state.user ? state.user.role : 'student'; }

  // ---------------------------------------------------------------------------
  // HTTP
  // ---------------------------------------------------------------------------
  class ApiError extends Error {
    constructor(message, code, rule, details) {
      super(message);
      this.code = code;
      this.rule = rule;
      this.details = details;
    }
  }

  async function api(path, options = {}) {
    const method = options.method || 'GET';
    const url = new URL('/api' + path, location.origin);
    if (options.params) {
      Object.entries(options.params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
      });
    }
    const headers = { Accept: 'application/json' };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET') headers['X-CSRF-Token'] = state.csrf || getCookie('seat_csrf');
    const response = await fetch(url, {
      method,
      headers,
      credentials: 'same-origin',
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    let payload = null;
    try { payload = await response.json(); } catch (e) { payload = null; }
    if (response.status === 401) {
      // 登录接口的 401 是"账号或密码不正确"，不是会话失效，不能统一覆盖文案。
      if (path === '/auth/login') {
        const error = (payload && payload.error) || {};
        throw new ApiError(error.message || '账号或密码不正确', error.code || 'UNAUTHENTICATED');
      }
      showLogin();
      throw new ApiError('登录状态已失效', 'UNAUTHENTICATED');
    }
    if (!response.ok || (payload && payload.ok === false)) {
      const error = (payload && payload.error) || {};
      throw new ApiError(error.message || '请求失败，请稍后重试', error.code || 'HTTP_' + response.status, error.rule, error.details);
    }
    return payload ? payload.data : null;
  }

  // ---------------------------------------------------------------------------
  // 提示与浮层
  // ---------------------------------------------------------------------------
  let toastTimer = null;
  function toast(message, options = {}) {
    const el = $('#toast');
    el.innerHTML = esc(message) + (options.rule ? '<span class="rule">' + esc(options.rule) + '</span>' : '');
    el.classList.toggle('error', Boolean(options.error));
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), options.duration || 3400);
  }

  function handleError(error) {
    if (error && error.code === 'UNAUTHENTICATED') return;
    console.error(error);
    toast((error && error.message) || '操作失败', { error: true, rule: error && error.rule });
  }

  function openSheet(config) {
    closeSheet();
    const wrap = document.createElement('div');
    wrap.className = 'sheet';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    const actions = config.actions || [];
    wrap.innerHTML =
      '<div class="sheet-grabber"></div>' +
      '<div class="sheet-head"><div><h2>' + esc(config.title) + '</h2>' +
      (config.subtitle ? '<p>' + esc(config.subtitle) + '</p>' : '') +
      '</div><button class="sheet-close" type="button" aria-label="关闭">' + icon('close') + '</button></div>' +
      '<div class="sheet-body">' + (config.body || '') + '</div>' +
      (actions.length
        ? '<div class="sheet-actions">' + actions.map((action, index) =>
            '<button class="btn ' + (action.kind === 'primary' ? 'btn-primary' : action.kind === 'danger' ? 'btn-danger' : 'btn-secondary') + '" data-sheet-action="' + index + '"' + (action.disabled ? ' disabled' : '') + '>' + esc(action.label) + '</button>').join('') + '</div>'
        : '');

    document.body.appendChild(wrap);
    state.sheet = wrap;
    const overlay = $('#overlay');
    overlay.hidden = false;
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => {
      overlay.classList.add('show');
      wrap.classList.add('open');
    });

    wrap.querySelector('.sheet-close').addEventListener('click', closeSheet);
    overlay.onclick = closeSheet;

    actions.forEach((action, index) => {
      const button = wrap.querySelector('[data-sheet-action="' + index + '"]');
      if (!button) return;
      button.addEventListener('click', async () => {
        if (button.disabled) return;
        button.disabled = true;
        try {
          await action.onClick(button);
        } catch (error) {
          handleError(error);
        } finally {
          button.disabled = false;
        }
      });
    });

    if (config.onMount) config.onMount(wrap);
    const focusable = wrap.querySelector('input, select, textarea, button.btn-primary');
    if (focusable) setTimeout(() => focusable.focus({ preventScroll: true }), 220);
    document.addEventListener('keydown', escToClose);
    return wrap;
  }

  function escToClose(event) {
    if (event.key === 'Escape') closeSheet();
  }

  function closeSheet() {
    const wrap = state.sheet;
    if (!wrap) return;
    state.sheet = null;
    document.removeEventListener('keydown', escToClose);
    if (state.sheetTimer) { clearInterval(state.sheetTimer); state.sheetTimer = null; }
    const overlay = $('#overlay');
    overlay.classList.remove('show');
    wrap.classList.remove('open');
    document.body.style.overflow = '';
    setTimeout(() => {
      overlay.hidden = true;
      overlay.onclick = null;
      wrap.remove();
    }, 220);
  }

  function confirmSheet(config) {
    openSheet({
      title: config.title,
      subtitle: config.subtitle || '',
      body: '<p class="small muted" style="margin:2px 0 6px">' + esc(config.message || '') + '</p>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        {
          label: config.confirmLabel || '确认',
          kind: config.danger ? 'danger' : 'primary',
          onClick: async (button) => {
            await config.onConfirm(button);
          },
        },
      ],
    });
  }

  // ---------------------------------------------------------------------------
  // 登录
  // ---------------------------------------------------------------------------
  async function loadDemoAccounts() {
    const box = $('#demoBox');
    try {
      const health = await api('/health');
      if (!health || !health.demo) { box.hidden = true; return; }
      const accounts = await api('/auth/demo-accounts');
      if (!Array.isArray(accounts) || !accounts.length) { box.hidden = true; return; }
      box.hidden = false;
      box.innerHTML = '<h3>演示账号</h3><div class="demo-grid">' + accounts.map((account) =>
        '<button class="demo-btn" type="button" data-demo-user="' + esc(account.username) + '" data-demo-pass="' + esc(account.password) + '">' +
        '<strong>' + esc(account.name) + '</strong><small>' + esc(ROLE_TEXT[account.role] || account.role) + ' · ' + esc(account.username) + '</small></button>').join('') + '</div>';
    } catch (error) {
      box.hidden = true;
    }
  }

  function showLogin(message) {
    clearInterval(state.refreshTimer);
    state.user = null; state.meta = null; state.csrf = null; state.sheet = null;
    $('#app').hidden = true;
    $('#login').hidden = false;
    $('#boot').hidden = true;
    const errorBox = $('#loginError');
    if (message) { errorBox.hidden = false; errorBox.textContent = message; } else { errorBox.hidden = true; }
    if (!document.body.dataset.demoLoaded) {
      document.body.dataset.demoLoaded = '1';
      loadDemoAccounts();
    }
    setTimeout(() => $('#username').focus(), 50);
  }

  async function submitLogin(event) {
    event.preventDefault();
    const button = $('#loginBtn');
    const errorBox = $('#loginError');
    const username = $('#username').value.trim();
    const password = $('#password').value;
    if (!username || !password) {
      errorBox.hidden = false;
      errorBox.textContent = '请输入账号与密码';
      return;
    }
    button.disabled = true;
    button.textContent = '正在登录…';
    errorBox.hidden = true;
    try {
      const data = await api('/auth/login', { method: 'POST', body: { username, password } });
      state.csrf = data.csrf_token;
      state.user = data.user;
      // 记在内存里，改密码时预填原密码；登出即清空，不落盘
      state.lastLoginPassword = password;
      state.meta = await api('/meta');
      enterApp();
      // 需求 §7.4：带 must_change_password 标记的账号（CLI 新建 / 重置过密码）登录后必须先改密
      if (data.user && data.user.must_change_password) {
        setTimeout(() => changePasswordSheet(true), 150);
      }
    } catch (error) {
      errorBox.hidden = false;
      errorBox.textContent = error.message || '登录失败';
    } finally {
      button.disabled = false;
      button.textContent = '登录';
    }
  }

  async function logout() {
    state.lastLoginPassword = '';
    try { await api('/auth/logout', { method: 'POST' }); } catch (error) { /* 会话已失效 */ }
    closeSheet();
    showLogin();
    toast('已退出登录');
  }

  // ---------------------------------------------------------------------------
  // 外壳、导航与路由
  // ---------------------------------------------------------------------------
  const NAV = {
    student: [
      { title: '预约', items: [['seats', '找座位', 'seats'], ['reservations', '我的预约', 'calendar']] },
      { title: '账户', items: [['credit', '信用', 'credit'], ['feedback', '反馈', 'feedback'], ['messages', '消息', 'bell']] },
    ],
    room_admin: [
      { title: '运营', items: [['overview', '概览', 'overview'], ['floor', '现场', 'floor'], ['resources', '资源', 'floor'], ['search', '预约查询', 'search']] },
      { title: '服务', items: [['complaints', '投诉', 'feedback'], ['reviews', '评价', 'star'], ['appeals', '违规申诉', 'alert'], ['students', '学生', 'users']] },
    ],
    teacher: [
      { title: '教学', items: [['class', '班级看板', 'class']] },
    ],
    sys_admin: [
      { title: '系统', items: [['params', '参数', 'params'], ['resources', '资源', 'floor'], ['users', '账号', 'users'], ['audit', '审计', 'audit'], ['system', '系统', 'system']] },
    ],
  };

  function homeView() {
    return { student: 'seats', room_admin: 'overview', teacher: 'class', sys_admin: 'params' }[roleOf()] || 'seats';
  }

  function allowedViews() {
    return (NAV[roleOf()] || []).flatMap((group) => group.items.map((item) => item[0]));
  }

  function enterApp() {
    $('#login').hidden = true;
    $('#app').hidden = false;
    $('#boot').hidden = true;
    renderUser();
    renderNav();
    const initial = location.hash.replace(/^#\//, '') || homeView();
    go(initial);
    refreshBadge();
  }

  function renderUser() {
    const user = state.user;
    $('#userName').textContent = user.name;
    $('#userRole').textContent = ROLE_TEXT[user.role] || user.role;
    $('#avatar').textContent = (user.name || '?').slice(0, 1);
    $('#userCard').innerHTML =
      '<div class="user-mini"><span class="avatar">' + esc((user.name || '?').slice(0, 1)) + '</span>' +
      '<div class="user-mini-text"><strong>' + esc(user.name) + '</strong><small>' + esc(user.id) + ' · ' + esc(ROLE_TEXT[user.role] || user.role) + '</small></div></div>';
  }

  function renderNav() {
    const groups = NAV[roleOf()] || [];
    $('#nav').innerHTML = groups.map((group) =>
      '<div class="nav-group"><div class="nav-group-title">' + esc(group.title) + '</div>' +
      group.items.map(([id, label, iconName]) =>
        '<button class="nav-item' + (state.view === id ? ' active' : '') + '" type="button" data-action="nav" data-view="' + id + '">' +
        icon(iconName) + '<span>' + esc(label) + '</span></button>').join('') + '</div>').join('');
  }

  function go(view) {
    if (!allowedViews().includes(view)) view = homeView();
    const hash = '#/' + view;
    if (location.hash === hash) activate(view);
    else location.hash = hash;
  }

  function activate(view) {
    if (!allowedViews().includes(view)) view = homeView();
    clearInterval(state.refreshTimer);
    state.refreshTimer = null;
    state.view = view;
    state.page = 1;
    renderNav();
    const meta = VIEW_META[view] || ['', ''];
    $('#pageTitle').textContent = meta[0];
    $('#pageSubtitle').textContent = meta[1];
    renderView();
  }

  async function renderView() {
    const view = state.view;
    const root = $('#view');
    root.innerHTML = skeletonView();
    try {
      const html = await VIEWS[view]();
      if (state.view !== view) return;
      root.innerHTML = html;
      if (AFTER[view]) AFTER[view]();
    } catch (error) {
      if (state.view !== view) return;
      if (error.code === 'UNAUTHENTICATED') return;
      root.innerHTML = errorView(error);
    }
  }

  function skeletonView() {
    return '<div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div>';
  }

  function errorView(error) {
    return '<div class="card empty">' + icon('alert') + '<strong>加载失败</strong><p>' + esc(error.message || '请稍后重试') + '</p>' +
      '<button class="btn btn-secondary" type="button" data-action="refresh">重新加载</button></div>';
  }

  async function refreshBadge() {
    try {
      const data = await api('/notifications/unread');
      const badge = $('#bellBadge');
      if (data.count > 0) { badge.hidden = false; badge.textContent = data.count > 99 ? '99+' : String(data.count); }
      else badge.hidden = true;
    } catch (error) { /* 静默 */ }
  }

  // ---------------------------------------------------------------------------
  // 视图：学生 · 找座位
  // ---------------------------------------------------------------------------
  function startOptions() {
    const rules = state.meta.rules;
    const open = toMinutes('07:00');
    const close = toMinutes('23:00');
    const options = [];
    for (let m = open; m + rules.min_minutes <= close; m += 30) {
      if (m + state.seatFilters.duration <= close) options.push(fromMinutes(m));
    }
    return options;
  }

  function endTime() {
    return addMinutes(state.seatFilters.start, state.seatFilters.duration);
  }

  function dateStrip() {
    const days = Math.max(1, state.meta.rules.booking_window_days);
    const today = state.meta.today;
    let html = '<div class="date-strip">';
    for (let i = 0; i < days; i += 1) {
      const iso = shiftDay(today, i);
      const d = parseDay(iso);
      html += '<button class="date-pill' + (state.seatFilters.date === iso ? ' active' : '') + '" type="button" data-action="pick-date" data-date="' + iso + '">' +
        '<small>' + (i === 0 ? '今天' : WEEKDAYS[d.getDay()]) + '</small><strong>' + d.getDate() + '</strong></button>';
    }
    return html + '</div>';
  }

  async function seatsView() {
    const filters = state.seatFilters;
    if (!filters.date) filters.date = state.meta.today;
    if (!startOptions().includes(filters.start)) filters.start = startOptions()[0] || '09:00';
    filters.duration = Math.min(filters.duration, state.meta.rules.max_minutes);

    const params = {
      date: filters.date,
      start: filters.start,
      end: endTime(),
      room_id: filters.room || undefined,
      only_available: filters.onlyAvailable ? 'true' : 'false',
      has_power: filters.power ? 'true' : undefined,
      is_window: filters.window ? 'true' : undefined,
    };
    const data = await api('/seats', { params });
    state.seatCache = data.items || [];
    if (state.selectedSeat && !state.seatCache.some((seat) => seat.id === state.selectedSeat.id && seat.available)) {
      state.selectedSeat = null;
    }

    const rooms = state.meta.rooms.filter((room) => !filters.room || room.id === filters.room);
    const roomMap = Object.fromEntries(state.meta.rooms.map((room) => [room.id, room]));

    const grouped = rooms.map((room) => {
      const seats = state.seatCache.filter((seat) => seat.room_id === room.id);
      return { room, seats };
    }).filter((group) => group.seats.length > 0);

    const availableCount = state.seatCache.filter((seat) => seat.available).length;
    const durationChoices = [30, 60, 90, 120, 180, 240].filter((minutes) => minutes <= state.meta.rules.max_minutes);

    const toolbar =
      '<div class="card toolbar">' +
        dateStrip() +
        '<div class="toolbar-row">' +
          '<label class="row small muted" style="gap:7px">开始' +
            '<select class="select-compact" data-filter="start">' + startOptions().map((value) =>
              '<option value="' + value + '"' + (value === filters.start ? ' selected' : '') + '>' + value + '</option>').join('') + '</select>' +
          '</label>' +
          '<label class="row small muted" style="gap:7px">时长' +
            '<select class="select-compact" data-filter="duration">' + durationChoices.map((value) =>
              '<option value="' + value + '"' + (Number(filters.duration) === value ? ' selected' : '') + '>' + (value < 60 ? value + ' 分钟' : (value / 60) + ' 小时') + '</option>').join('') + '</select>' +
          '</label>' +
        '</div>' +
        '<div class="toolbar-row">' +
          '<div class="segmented">' +
            '<button type="button" data-action="filter-room" data-room="" class="' + (!filters.room ? 'active' : '') + '">全部</button>' +
            state.meta.rooms.map((room) => '<button type="button" data-action="filter-room" data-room="' + esc(room.id) + '" class="' + (filters.room === room.id ? 'active' : '') + '">' + esc(room.name.split(' ').pop()) + '</button>').join('') +
          '</div>' +
        '</div>' +
        '<div class="toolbar-row">' +
          '<span class="small muted">仅看空闲</span>' +
          '<label class="switch"><input type="checkbox" data-filter="onlyAvailable"' + (filters.onlyAvailable ? ' checked' : '') + '><span></span></label>' +
          '<div class="grow"></div>' +
          '<span class="pill ' + (availableCount ? 'green' : 'gray') + ' plain">' + availableCount + ' 个空闲</span>' +
        '</div>' +
        '<div class="toolbar-row">' +
          '<button class="chip' + (filters.power ? ' active' : '') + '" type="button" data-action="toggle-filter" data-key="power">' + icon('power') + '电源</button>' +
          '<button class="chip' + (filters.window ? ' active' : '') + '" type="button" data-action="toggle-filter" data-key="window">' + icon('window') + '靠窗</button>' +
        '</div>' +
        '<div class="toolbar-row">' +
          '<div class="legend"><span><i style="background:#34C759"></i>空闲</span><span><i style="background:#FF9500"></i>已预约 / 暂离</span><span><i style="background:#007AFF"></i>使用中</span><span class="tertiary" style="margin-left:auto">点击座位选择</span></div>' +
        '</div>' +
      '</div>';

    let body = '';
    if (!grouped.length) {
      body = '<div class="card empty" style="margin-top:18px">' + icon('empty') + '<strong>没有符合条件的座位</strong><p>调整时间、区域或筛选条件后再试。</p></div>';
    } else {
      body = grouped.map(({ room, seats }) => {
        const free = seats.filter((seat) => seat.available).length;
        return '<section class="seat-room"><div class="seat-room-head"><div><h3>' + esc(room.name) + '</h3><p>' + esc(room.location || '') + ' · ' + free + ' / ' + seats.length + ' 空闲</p></div></div>' +
          '<div class="seat-grid">' + seats.map(seatCard).join('') + '</div></section>';
      }).join('');
    }

    const selected = state.selectedSeat;
    const selectedRoom = selected ? roomMap[selected.room_id] : null;
    const actionBar =
      '<div class="action-bar' + (selected ? '' : ' is-empty') + '">' +
        '<div>' + (selected
          ? '<strong>' + esc(selected.id) + '</strong><small><span class="bar-room">' + esc(selectedRoom ? selectedRoom.name : '') + ' · </span>' + fmtDay(filters.date) + ' ' + filters.start + '–' + endTime() + '</small>'
          : '<strong>选择一个座位</strong><small>' + fmtDay(filters.date) + ' ' + filters.start + '–' + endTime() + '</small>') +
        '</div>' +
        '<button class="btn btn-primary" type="button" data-action="book"' + (selected ? '' : ' disabled') + '>预约</button>' +
      '</div>';

    return toolbar + body + actionBar;
  }

  function seatCard(seat, options = {}) {
    const selected = state.selectedSeat && state.selectedSeat.id === seat.id;
    const attr = (seat.has_power ? icon('power') : '') + (seat.is_window ? icon('window') : '');
    const title = seat.state === 'available' ? '空闲' : seat.state_label;
    const sub = options.showState && seat.state !== 'available' ? seat.state_label : seat.type_label;
    return '<button class="seat' + (selected ? ' selected' : '') + '" type="button" data-action="select-seat" data-id="' + esc(seat.id) + '" data-state="' + esc(seat.state) + '" aria-disabled="' + (seat.available ? 'false' : 'true') + '" title="' + esc(title) + '">' +
      '<span class="seat-dot"></span><strong>' + esc(seat.id) + '</strong><small>' + esc(sub) + '</small>' +
      (attr ? '<span class="seat-icons">' + attr + '</span>' : '') + '</button>';
  }

  // ---------------------------------------------------------------------------
  // 视图：学生 · 我的预约
  // ---------------------------------------------------------------------------
  async function reservationsView() {
    const data = await api('/reservations', { params: { page_size: 50 } });
    const items = data.items || [];
    const active = items.filter((item) => ['pending', 'in_use', 'away'].includes(item.status));
    const history = items.filter((item) => !['pending', 'in_use', 'away'].includes(item.status));
    const list = state.reservationTab === 'active' ? active : history;

    const tabs = '<div class="segmented" style="margin-bottom:14px">' +
      '<button type="button" data-action="reservation-tab" data-tab="active" class="' + (state.reservationTab === 'active' ? 'active' : '') + '">进行中 ' + active.length + '</button>' +
      '<button type="button" data-action="reservation-tab" data-tab="history" class="' + (state.reservationTab === 'history' ? 'active' : '') + '">历史 ' + history.length + '</button>' +
      '</div>';

    if (!list.length) {
      return tabs + '<div class="card empty">' + icon('empty') + '<strong>' + (state.reservationTab === 'active' ? '没有进行中的预约' : '还没有历史预约') + '</strong><p>去“找座位”选择一个时段，预约会出现在这里。</p>' +
        '<button class="btn btn-primary" type="button" data-action="nav" data-view="seats">去找座位</button></div>';
    }

    return tabs + '<div class="list">' + list.map((item) => {
      const status = RES_STATUS[item.status] || [item.status, 'gray'];
      return '<button class="list-row clickable" type="button" data-action="open-reservation" data-id="' + esc(item.id) + '">' +
        '<span class="dot ' + status[1] + '"></span>' +
        '<div class="row-main"><div class="row-title">' + esc(item.seat_id) + ' <span class="tertiary small">' + esc(item.room_name) + '</span></div>' +
        '<div class="row-sub">' + fmtDay(item.date) + ' · ' + esc(item.start) + '–' + esc(item.end) + '</div></div>' +
        '<div class="row-side">' + statusPill(item.status) + icon('chevron', 'row-chevron') + '</div></button>';
    }).join('') + '</div>';
  }

  async function openReservation(id) {
    const item = await api('/reservations/' + encodeURIComponent(id));
    const statusMeta = RES_STATUS[item.status] || [item.status, 'gray'];
    const rows = [
      ['来源', item.source === 'waitlist' ? '候补补位' : '本人预约'],
      ['签到窗口', item.checkin_opens_at ? fmtClock(item.checkin_opens_at) + '–' + fmtClock(item.checkin_deadline) : '—'],
      ['签到时间', item.checked_in_at ? fmtDateTime(item.checked_in_at) : '—'],
      ['签退时间', item.checked_out_at ? fmtDateTime(item.checked_out_at) : '—'],
      ['结束原因', item.end_reason_label || '—'],
    ];
    const body =
      '<div class="ticket">' +
        '<div class="ticket-main">' +
          '<div class="ticket-kicker">RESERVATION</div>' +
          '<div class="ticket-seat">' + esc(item.seat_id) + '</div>' +
          '<div class="ticket-room">' + esc(item.room_name) + '</div>' +
          '<div class="ticket-time">' + fmtDay(item.date) + ' · ' + esc(item.start) + '–' + esc(item.end) + '</div>' +
        '</div>' +
        '<div class="ticket-stub">' +
          '<div class="ticket-code">' + esc(item.id) + '</div>' +
          '<div class="ticket-status"><span class="pill ' + statusMeta[1] + '">' + statusMeta[0] + '</span></div>' +
        '</div>' +
      '</div>' +
      '<dl class="detail-list">' + rows.map(([label, value]) =>
        '<div class="detail-row"><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>').join('') + '</dl>';

    const actions = [];
    if (item.status === 'pending') {
      actions.push({ label: '出示凭证', kind: 'secondary', onClick: () => openCredential(item.id) });
      actions.push({ label: '签到', kind: 'primary', onClick: async () => { await quickCheckIn(item.id); } });
      actions.push({ label: '取消', kind: 'secondary', onClick: () => cancelReservation(item.id) });
    } else if (item.status === 'in_use') {
      actions.push({ label: '暂离', kind: 'secondary', onClick: async () => { await postAction('/reservations/' + item.id + '/away', '已进入暂离'); } });
      actions.push({ label: '签退', kind: 'primary', onClick: async () => { await postAction('/reservations/' + item.id + '/check-out', '已签退'); } });
    } else if (item.status === 'away') {
      actions.push({ label: '返座', kind: 'primary', onClick: async () => { await postAction('/reservations/' + item.id + '/resume', '已返座'); } });
      actions.push({ label: '签退', kind: 'secondary', onClick: async () => { await postAction('/reservations/' + item.id + '/check-out', '已签退'); } });
    } else if (item.status === 'completed' && !item.has_review) {
      actions.push({ label: '评价座位', kind: 'primary', onClick: () => openReview(item.id) });
    }
    if (!actions.length) actions.push({ label: '关闭', kind: 'secondary', onClick: closeSheet });
    openSheet({ title: '预约详情', subtitle: item.id, body, actions });
  }

  async function quickCheckIn(id) {
    const credential = await api('/reservations/' + encodeURIComponent(id) + '/credential', { method: 'POST' });
    await api('/reservations/' + encodeURIComponent(id) + '/check-in', { method: 'POST', body: { code: credential.code } });
    closeSheet();
    toast('签到成功');
    refreshBadge();
    renderView();
  }

  async function openCredential(id) {
    const credential = await api('/reservations/' + encodeURIComponent(id) + '/credential', { method: 'POST' });
    let remaining = credential.ttl_seconds;
    const body =
      '<p class="small muted" style="margin-bottom:10px">向座位旁的扫码设备出示此码，或用闸机刷卡。动态码一次有效。</p>' +
      '<div class="code-box"><code>' + esc(credential.code) + '</code><span class="countdown" id="credCountdown">' + remaining + 's</span></div>' +
      '<p class="tiny tertiary">有效期至 ' + esc(fmtDateTime(credential.expires_at)) + '。过期后可重新签发。</p>';

    const wrap = openSheet({
      title: '签到凭证',
      subtitle: '一次性动态码',
      body,
      actions: [
        { label: '复制', kind: 'secondary', onClick: async () => {
            try {
              await navigator.clipboard.writeText(credential.code);
              toast('已复制');
            } catch (error) {
              toast('复制失败，请手动选择', { error: true });
            }
          } },
        { label: '立即签到', kind: 'primary', onClick: async () => {
            await api('/reservations/' + encodeURIComponent(id) + '/check-in', { method: 'POST', body: { code: credential.code } });
            closeSheet();
            toast('签到成功');
            refreshBadge();
            renderView();
          } },
      ],
    });
    const label = wrap.querySelector('#credCountdown');
    state.sheetTimer = setInterval(() => {
      remaining -= 1;
      if (label) label.textContent = remaining > 0 ? remaining + 's' : '已过期';
      if (remaining <= 0) { clearInterval(state.sheetTimer); state.sheetTimer = null; }
    }, 1000);
  }

  function cancelReservation(id) {
    confirmSheet({
      title: '取消预约',
      message: '距开始 30 分钟以上取消不记违规；不足 30 分钟将扣 5 分。开始后无法自助取消。',
      confirmLabel: '取消预约',
      danger: true,
      onConfirm: async () => {
        await api('/reservations/' + encodeURIComponent(id) + '/cancel', { method: 'POST' });
        closeSheet();
        toast('预约已取消');
        renderView();
      },
    });
  }

  async function postAction(path, message) {
    const result = await api(path, { method: 'POST' });
    closeSheet();
    toast((result && result.message) || message);
    renderView();
  }

  function openReview(reservationId) {
    let rating = 5;
    const tags = ['安静', '插座稳定', '光线好', '桌面干净', '网络稳定'];
    const body =
      '<div class="row" style="justify-content:center;margin:6px 0 16px"><div class="stars" id="stars">' +
        [1, 2, 3, 4, 5].map((value) => '<button class="star-btn on" type="button" data-star="' + value + '" aria-label="' + value + ' 星">' + icon('star') + '</button>').join('') +
      '</div></div>' +
      '<div class="chip-row" id="reviewTags" style="margin-bottom:14px">' + tags.map((tag) =>
        '<button class="chip" type="button" data-tag="' + esc(tag) + '">' + esc(tag) + '</button>').join('') + '</div>' +
      '<label class="field"><span>补充说明（可选）</span><textarea id="reviewContent" maxlength="500" placeholder="座位、环境、设备的实际情况"></textarea></label>' +
      '<input type="hidden" id="reviewRating" value="5">';

    openSheet({
      title: '评价座位',
      subtitle: '仅本人已完成的预约可评价一次',
      body,
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '提交评价', kind: 'primary', onClick: async () => {
            const selectedTags = $$('#reviewTags .chip.active').map((el) => el.dataset.tag);
            await api('/reviews', { method: 'POST', body: {
              reservation_id: reservationId,
              rating: Number($('#reviewRating').value),
              tags: selectedTags,
              content: $('#reviewContent').value.trim(),
            } });
            closeSheet();
            toast('评价已提交');
            renderView();
          } },
      ],
      onMount: (wrap) => {
        const stars = wrap.querySelector('#stars');
        stars.addEventListener('click', (event) => {
          const button = event.target.closest('[data-star]');
          if (!button) return;
          rating = Number(button.dataset.star);
          wrap.querySelector('#reviewRating').value = String(rating);
          $$('.star-btn', stars).forEach((el) => el.classList.toggle('on', Number(el.dataset.star) <= rating));
        });
        wrap.querySelector('#reviewTags').addEventListener('click', (event) => {
          const button = event.target.closest('[data-tag]');
          if (button) button.classList.toggle('active');
        });
      },
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：学生 · 信用
  // ---------------------------------------------------------------------------
  async function creditView() {
    const dashboard = await api('/me/dashboard');
    const user = dashboard.user;
    const stats = dashboard.stats;
    const limit = dashboard.limit;
    const credit = user.credit;

    const ring = ringSvg(credit);
    const banner = limit.type !== 'none'
      ? '<div class="banner warning" style="margin-top:14px">' + icon('alert') + '<div><strong>当前受限：' + esc(limit.label || limit.type) + '</strong><br>' + esc(limit.reason || '') + (limit.until ? '（至 ' + esc(fmtDateTime(limit.until)) + '）' : '') + '</div></div>'
      : '';

    const summary =
      '<div class="card ring-card">' + ring +
        '<div class="grow"><div class="row-between"><div><div class="kpi-label">信用状态</div><div style="font-size:22px;font-weight:700;letter-spacing:-.02em;margin-top:3px">' + (credit >= 80 ? '良好' : credit >= 60 ? '注意' : '受限') + '</div></div>' +
        '<span class="pill ' + (credit >= 80 ? 'green' : credit >= 60 ? 'orange' : 'red') + ' plain">' + credit + ' 分</span></div>' +
        '<p class="small muted" style="margin-top:8px">本月预约 ' + stats.month_total + ' 次 · 完成 ' + stats.month_completed + ' 次 · 违约 ' + stats.month_no_show + ' 次</p></div></div>' +
      banner;

    const tabs =
      '<div class="segmented" style="margin:16px 0 12px">' +
        '<button type="button" data-action="credit-tab" data-tab="violations" class="' + (state.creditTab === 'violations' ? 'active' : '') + '">违规记录</button>' +
        '<button type="button" data-action="credit-tab" data-tab="logs" class="' + (state.creditTab === 'logs' ? 'active' : '') + '">信用流水</button>' +
      '</div>';

    let list = '';
    if (state.creditTab === 'violations') {
      const data = await api('/violations', { params: { page_size: 50 } });
      const items = data.items || [];
      list = items.length
        ? '<div class="list">' + items.map((item) => {
            const status = VIOLATION_STATUS[item.status] || [item.status, 'gray'];
            const appealable = item.status === 'active';
            return '<div class="list-row">' +
              '<span class="dot ' + (item.status === 'revoked' ? 'gray' : 'red') + '"></span>' +
              '<div class="row-main"><div class="row-title">' + esc(item.type_label) + ' <span class="tertiary small">−' + item.points + ' 分</span></div>' +
              '<div class="row-sub">' + fmtDateTime(item.occurred_at) + ' · ' + esc(item.source === 'manual' ? '管理员登记' : '系统判定') + '</div></div>' +
              '<div class="row-side"><span class="pill ' + status[1] + '">' + status[0] + '</span>' +
              (appealable ? '<button class="btn btn-tinted btn-sm" type="button" data-action="appeal" data-id="' + esc(item.id) + '">申诉</button>' : '') +
              '</div></div>';
          }).join('') + '</div>'
        : emptyBlock('没有违规记录', '保持按时签到与签退，信用分会保持良好。');
    } else {
      const data = await api('/me/credit-logs', { params: { page_size: 50 } });
      const items = data.items || [];
      list = items.length
        ? '<div class="list">' + items.map((item) =>
            '<div class="list-row"><div class="row-main"><div class="row-title">' + esc(item.reason) + '</div>' +
            '<div class="row-sub">' + fmtDateTime(item.created_at) + ' · 余额 ' + item.balance_after + '</div></div>' +
            '<div class="row-side"><span class="pill ' + (item.delta > 0 ? 'green' : 'red') + ' plain">' + (item.delta > 0 ? '+' : '') + item.delta + '</span></div></div>').join('') + '</div>'
        : emptyBlock('还没有信用流水', '签到、违约与申诉都会在这里留下记录。');
    }

    return summary + tabs + list;
  }

  function ringSvg(value) {
    const radius = 38;
    const circumference = 2 * Math.PI * radius;
    const pct = Math.max(0, Math.min(100, value)) / 100;
    const color = value >= 80 ? '#34C759' : value >= 60 ? '#FF9500' : '#FF3B30';
    return '<svg class="ring" viewBox="0 0 92 92" role="img" aria-label="信用分 ' + value + '">' +
      '<circle cx="46" cy="46" r="' + radius + '" fill="none" stroke="#EDEDF2" stroke-width="8"/>' +
      '<circle cx="46" cy="46" r="' + radius + '" fill="none" stroke="' + color + '" stroke-width="8" stroke-linecap="round" stroke-dasharray="' + circumference + '" stroke-dashoffset="' + (circumference * (1 - pct)) + '" transform="rotate(-90 46 46)"/>' +
      '<text x="46" y="50" text-anchor="middle" font-size="21" font-weight="700" fill="#1C1C1E">' + value + '</text>' +
      '</svg>';
  }

  function appealSheet(id) {
    openSheet({
      title: '提交申诉',
      subtitle: '仅限违规发生后 3 日内，且只能申诉本人记录',
      body: '<label class="field"><span>申诉理由</span><textarea id="appealReason" maxlength="500" placeholder="例如：扫码设备故障、现场网络中断、已在现场但未记录"></textarea></label>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '提交', kind: 'primary', onClick: async () => {
            const reason = $('#appealReason').value.trim();
            if (reason.length < 4) { toast('请填写至少 4 个字的理由', { error: true }); return; }
            await api('/violations/' + encodeURIComponent(id) + '/appeal', { method: 'POST', body: { reason } });
            closeSheet();
            toast('申诉已提交');
            renderView();
          } },
      ],
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：学生 · 反馈
  // ---------------------------------------------------------------------------
  async function feedbackView() {
    const tab = state.feedbackTab;
    const tabs = '<div class="segmented" style="margin-bottom:14px">' +
      '<button type="button" data-action="feedback-tab" data-tab="complaints" class="' + (tab === 'complaints' ? 'active' : '') + '">我的投诉</button>' +
      '<button type="button" data-action="feedback-tab" data-tab="reviews" class="' + (tab === 'reviews' ? 'active' : '') + '">我的评价</button></div>';
    if (tab === 'reviews') return tabs + await myReviewsView();

    const data = await api('/complaints', { params: { page_size: 50 } });
    const items = data.items || [];
    const header = '<div class="row-between" style="margin-bottom:12px"><p class="small muted">投诉办结后可以评价处理结果。</p>' +
      '<button class="btn btn-primary btn-sm" type="button" data-action="new-complaint">新建投诉</button></div>';

    if (!items.length) return header + emptyBlock('还没有提交过反馈', '座位被占、环境嘈杂或设施损坏都可以在这里反馈。');
    return header + '<div class="list">' + items.map((item) => {
      const status = COMPLAINT_STATUS[item.status] || [item.status, 'gray'];
      return '<div class="list-row"><div class="row-main"><div class="row-title">' + esc(item.type_label) +
        (item.seat_id ? ' <span class="tertiary small">' + esc(item.seat_id) + '</span>' : '') + '</div>' +
        '<div class="row-sub">' + fmtDateTime(item.created_at) + ' · ' + esc(item.content) + '</div>' +
        (item.feedback ? '<div class="row-sub" style="color:#1F8F43">反馈：' + esc(item.feedback) + '</div>' : '') +
        '</div><div class="row-side"><span class="pill ' + status[1] + '">' + status[0] + '</span>' +
        (item.status === 'resolved' ? '<button class="btn btn-tinted btn-sm" type="button" data-action="rate-complaint" data-id="' + esc(item.id) + '">评价</button>' : '') +
        (item.rating ? '<span class="small muted">' + '★'.repeat(item.rating) + '</span>' : '') +
        '</div></div>';
    }).join('') + '</div>';
  }

  // ---------------------------------------------------------------------------
  // 视图：学生 · 我的评价
  // GET /reviews 由后端按角色过滤（学生只能看到自己的），返回项含 reply/replied_at，
  // 管理员在「评价」页回复后这里会绿字回显，反馈链路至此闭合。
  // ---------------------------------------------------------------------------
  async function myReviewsView() {
    const data = await api('/reviews', { params: { page: 1, page_size: 50 } });
    const items = data.items || [];
    if (!items.length) return emptyBlock('还没有提交过评价', '签退后可以对座位打分，管理员回复会显示在这里。');
    return '<div class="list">' + items.map((item) => {
      const stars = item.rating ? '★'.repeat(item.rating) : '';
      const tags = Array.isArray(item.tags) && item.tags.length ? item.tags.join(' · ') : '';
      return '<div class="list-row"><div class="row-main"><div class="row-title">' +
        esc(item.seat_id || '自习室') + (item.room_name ? ' <span class="tertiary small">' + esc(item.room_name) + '</span>' : '') + '</div>' +
        '<div class="row-sub">' + fmtDateTime(item.created_at) +
        (stars ? ' · <span class="small">' + stars + '</span>' : '') +
        (tags ? ' · ' + esc(tags) : '') + '</div>' +
        '<div class="row-sub">' + esc(item.content || '') + '</div>' +
        (item.reply ? '<div class="row-sub" style="color:#1F8F43">管理员回复：' + esc(item.reply) +
          (item.replied_at ? '（' + fmtDateTime(item.replied_at) + '）' : '') + '</div>' : '') +
        '</div><div class="row-side">' +
        (item.reply ? '<span class="pill green plain">已回复</span>' : '<span class="pill gray plain">待回复</span>') +
        '</div></div>';
    }).join('') + '</div>';
  }

  function newComplaintSheet() {
    const types = Object.entries(state.meta.complaint_types || {});
    openSheet({
      title: '新建投诉',
      subtitle: '管理员办结时必须填写反馈',
      body:
        '<label class="field"><span>类型</span><select id="complaintType">' + types.map(([value, label]) =>
          '<option value="' + esc(value) + '">' + esc(label) + '</option>').join('') + '</select></label>' +
        '<label class="field"><span>座位（可选）</span><input id="complaintSeat" placeholder="例如 B201-003" maxlength="20"></label>' +
        '<label class="field"><span>具体情况</span><textarea id="complaintContent" maxlength="1000" placeholder="说明时间、位置和现象，便于现场核查"></textarea></label>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '提交', kind: 'primary', onClick: async () => {
            const content = $('#complaintContent').value.trim();
            if (content.length < 5) { toast('请填写至少 5 个字', { error: true }); return; }
            await api('/complaints', { method: 'POST', body: {
              type: $('#complaintType').value,
              content,
              seat_id: $('#complaintSeat').value.trim() || null,
            } });
            closeSheet();
            toast('投诉已提交');
            renderView();
          } },
      ],
    });
  }

  function rateComplaintSheet(id) {
    let rating = 5;
    openSheet({
      title: '评价处理结果',
      body: '<div class="row" style="justify-content:center;margin:6px 0 14px"><div class="stars" id="rateStars">' +
        [1, 2, 3, 4, 5].map((value) => '<button class="star-btn on" type="button" data-star="' + value + '">' + icon('star') + '</button>').join('') +
        '</div></div><label class="field"><span>补充说明（可选）</span><textarea id="rateComment" maxlength="500"></textarea></label>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '提交', kind: 'primary', onClick: async () => {
            await api('/complaints/' + encodeURIComponent(id) + '/rate', { method: 'POST', body: { rating, comment: $('#rateComment').value.trim() } });
            closeSheet();
            toast('评价已提交');
            renderView();
          } },
      ],
      onMount: (wrap) => {
        wrap.querySelector('#rateStars').addEventListener('click', (event) => {
          const button = event.target.closest('[data-star]');
          if (!button) return;
          rating = Number(button.dataset.star);
          $$('.star-btn', wrap).forEach((el) => el.classList.toggle('on', Number(el.dataset.star) <= rating));
        });
      },
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：学生 · 消息
  // ---------------------------------------------------------------------------
  async function messagesView() {
    const data = await api('/notifications', { params: { page_size: 50 } });
    const items = data.items || [];
    const header = '<div class="row-between" style="margin-bottom:12px"><p class="small muted">共 ' + data.total + ' 条</p>' +
      (items.some((item) => !item.read_at) ? '<button class="btn btn-secondary btn-sm" type="button" data-action="mark-all-read">全部已读</button>' : '') + '</div>';
    if (!items.length) return header + emptyBlock('没有消息', '预约提醒、违约与候补通知会出现在这里。');
    return header + '<div class="list">' + items.map((item) =>
      '<div class="list-row"><span class="dot ' + (item.read_at ? 'gray' : 'blue') + '"></span>' +
      '<div class="row-main"><div class="row-title" style="font-weight:' + (item.read_at ? '500' : '600') + '">' + esc(item.content) + '</div>' +
      '<div class="row-sub">' + fmtDateTime(item.created_at) + '</div></div></div>').join('') + '</div>';
  }

  // ---------------------------------------------------------------------------
  // 视图：管理员 · 概览
  // ---------------------------------------------------------------------------
  async function overviewView() {
    const [stats, recent] = await Promise.all([
      api('/admin/stats', { params: { room_id: state.overviewRoom || undefined } }),
      api('/admin/reservations', { params: { page_size: 6 } }),
    ]);
    state.lastSnapshot = stats;
    state.recentReservations = recent.items || [];
    return '<div id="overviewRoot">' + overviewHtml(stats, state.recentReservations) + '</div>';
  }

  function overviewHtml(stats, recent = []) {
    const kpi = stats.kpi;
    const rooms = stats.rooms || [];
    const roomFilter = '<div class="segmented" style="margin-bottom:14px">' +
      '<button type="button" data-action="overview-room" data-room="" class="' + (!state.overviewRoom ? 'active' : '') + '">全部</button>' +
      state.meta.rooms.map((room) => '<button type="button" data-action="overview-room" data-room="' + esc(room.id) + '" class="' + (state.overviewRoom === room.id ? 'active' : '') + '">' + esc(room.name.split(' ').pop()) + '</button>').join('') +
      '</div>';

    const kpis =
      '<div class="kpi-grid">' +
        '<div class="kpi blue"><div class="kpi-label">在馆人数</div><div class="kpi-value">' + (kpi.occupied + kpi.held) + '<small>人</small></div><div class="kpi-foot">使用中 ' + kpi.occupied + ' · 暂离 ' + kpi.held + '</div></div>' +
        '<div class="kpi"><div class="kpi-label">今日预约</div><div class="kpi-value">' + kpi.today_reservations + '<small>单</small></div><div class="kpi-foot">已签到 ' + kpi.today_checkins + ' · 待签到 ' + kpi.pending + '</div></div>' +
        '<div class="kpi"><div class="kpi-label">实时使用率</div><div class="kpi-value">' + kpi.utilization + '<small>%</small></div><div class="kpi-foot">空闲 ' + kpi.available + ' / 总座位 ' + kpi.total_seats + '</div></div>' +
        '<div class="kpi"><div class="kpi-label">待处理投诉</div><div class="kpi-value">' + kpi.open_complaints + '<small>条</small></div><div class="kpi-foot">候补等待 ' + kpi.waitlist + ' · 评分 ' + (kpi.average_rating == null ? '—' : kpi.average_rating) + '</div></div>' +
      '</div>';

    const roomBars = rooms.length
      ? '<div class="card chart-card" style="margin-top:12px"><div class="chart-head"><h3>区域使用率</h3><span class="tiny tertiary">' + esc(stats.generated_at.slice(11, 16)) + ' 更新</span></div>' +
        '<div class="bar-list">' + rooms.map((room) =>
          '<div class="bar-item"><span class="bar-label">' + esc(room.id) + '</span><span class="bar-track"><span class="bar-fill' + (room.utilization >= 85 ? ' orange' : '') + '" style="width:' + Math.min(100, room.utilization) + '%"></span></span><span class="bar-value">' + room.utilization + '%</span></div>').join('') + '</div></div>'
      : '';

    const trend = '<div class="card chart-card" style="margin-top:12px"><div class="chart-head"><h3>近 7 日预约与签到</h3>' +
      '<div class="legend"><span><i style="background:#007AFF"></i>预约</span><span><i style="background:#FF9500"></i>签到</span></div></div>' +
      '<div class="chart">' + trendChart(stats.trend || []) + '</div></div>';

    const hourly = '<div class="card chart-card" style="margin-top:12px"><div class="chart-head"><h3>今日时段分布</h3><span class="tiny tertiary">按开始时间统计</span></div>' +
      '<div class="chart">' + hourlyChart(stats.hourly || []) + '</div></div>';

    const violations = '<div class="card chart-card" style="margin-top:12px"><div class="chart-head"><h3>近 30 日违规构成</h3></div>' +
      (stats.violations && stats.violations.length
        ? '<div class="bar-list">' + stats.violations.map((item) =>
            '<div class="bar-item"><span class="bar-label">' + esc((state.meta.violation_types || {})[item.type] || item.type) + '</span><span class="bar-track"><span class="bar-fill red" style="width:' + Math.max(6, Math.round(item.count / Math.max(...stats.violations.map((v) => v.count)) * 100)) + '%"></span></span><span class="bar-value">' + item.count + '</span></div>').join('') + '</div>'
        : '<div class="chart-empty">近 30 日没有违规记录</div>') + '</div>';

    return roomFilter + kpis + '<div class="dash-grid">' + trend + roomBars + hourly + violations + '</div>' + recentReservations(recent);
  }

  function recentReservations(items) {
    if (!items.length) return '';
    return '<div class="card" style="margin-top:12px">' +
      '<div class="section-head" style="padding:14px 16px 4px"><h2>最近预约</h2><a class="link" href="#/search">查看全部</a></div>' +
      '<div class="list" style="border:none;box-shadow:none;border-radius:0">' + items.map((item) => {
        const status = RES_STATUS[item.status] || [item.status, 'gray'];
        return '<button class="list-row clickable" type="button" data-action="open-reservation" data-id="' + esc(item.id) + '">' +
          '<span class="dot ' + status[1] + '"></span>' +
          '<div class="row-main"><div class="row-title">' + esc(item.seat_id) + ' <span class="tertiary small">' + esc(item.user_name) + '</span></div>' +
          '<div class="row-sub">' + fmtDay(item.date) + ' · ' + esc(item.start) + '–' + esc(item.end) + '</div></div>' +
          '<div class="row-side">' + statusPill(item.status) + icon('chevron', 'row-chevron') + '</div></button>';
      }).join('') + '</div></div>';
  }

  function trendChart(items, options = {}) {
    if (!items.length) return '<div class="chart-empty">暂无数据</div>';
    const withCheckins = options.withCheckins !== false;
    const compact = window.innerWidth <= 640;
    const W = compact ? 380 : 920, H = compact ? 172 : 210;
    const left = compact ? 26 : 34, right = 8, top = 14, bottom = compact ? 26 : 30;
    const innerW = W - left - right, innerH = H - top - bottom;
    const max = Math.max(1, ...items.map((item) => Math.max(Number(item.reservations || 0), withCheckins ? Number(item.checkins || 0) : 0)));
    const group = innerW / items.length;
    const barW = Math.min(16, group * 0.26);
    let content = '';
    [0, 0.5, 1].forEach((ratio) => {
      const y = top + innerH * (1 - ratio);
      content += '<line x1="' + left + '" x2="' + (W - right) + '" y1="' + y + '" y2="' + y + '" stroke="rgba(60,60,67,0.08)"/>';
      content += '<text x="' + (left - 6) + '" y="' + (y + 3) + '" text-anchor="end" font-size="' + (compact ? 9 : 10) + '" fill="#A1A1A6">' + Math.round(max * ratio) + '</text>';
    });
    items.forEach((item, index) => {
      const x = left + index * group + group / 2;
      const reservations = Number(item.reservations || 0);
      const checkins = Number(item.checkins || 0);
      const h1 = (reservations / max) * innerH;
      const h2 = (checkins / max) * innerH;
      content += '<rect x="' + (x - (withCheckins ? barW + 2 : barW / 2)) + '" y="' + (top + innerH - h1) + '" width="' + barW + '" height="' + Math.max(1, h1) + '" rx="3" fill="#007AFF"/>';
      if (withCheckins) {
        content += '<rect x="' + (x + 2) + '" y="' + (top + innerH - h2) + '" width="' + barW + '" height="' + Math.max(1, h2) + '" rx="3" fill="#34C759" opacity="0.9"/>';
      }
      content += '<text x="' + x + '" y="' + (H - 8) + '" text-anchor="middle" font-size="' + (compact ? 10 : 10.5) + '" fill="#8E8E93">' + esc(item.label) + '</text>';
    });
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="预约趋势">' + content + '</svg>';
  }

  function hourlyChart(items) {
    if (!items.length) return '<div class="chart-empty">今天还没有预约</div>';
    const compact = window.innerWidth <= 640;
    const W = compact ? 380 : 920, H = compact ? 138 : 150;
    const left = compact ? 18 : 24, right = 8, top = 12, bottom = compact ? 24 : 26;
    const innerW = W - left - right, innerH = H - top - bottom;
    const max = Math.max(1, ...items.map((item) => item.count));
    const step = innerW / Math.max(1, items.length);
    const barW = Math.min(30, step * 0.55);
    let content = '';
    items.forEach((item, index) => {
      const x = left + index * step + step / 2;
      const count = Number(item.count || 0);
      const h = (count / max) * innerH;
      content += '<rect x="' + (x - barW / 2) + '" y="' + (top + innerH - h) + '" width="' + barW + '" height="' + Math.max(2, h) + '" rx="4" fill="#007AFF" opacity="' + (0.35 + 0.65 * (count / max)) + '"/>';
      content += '<text x="' + x + '" y="' + (H - 7) + '" text-anchor="middle" font-size="' + (compact ? 9.5 : 10) + '" fill="#8E8E93">' + esc(item.hour) + '</text>';
    });
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="今日时段分布">' + content + '</svg>';
  }

  // ---------------------------------------------------------------------------
  // 视图：管理员 · 现场
  // ---------------------------------------------------------------------------

  // 现场页可选的半小时时段：取自各自习室开放时间的最早 / 最晚
  function floorSlotOptions() {
    const rooms = state.meta.rooms || [];
    let open = toMinutes('07:00');
    let close = toMinutes('23:00');
    rooms.forEach((room) => {
      if (room.open_time) open = Math.min(open, toMinutes(room.open_time));
      if (room.close_time) close = Math.max(close, toMinutes(room.close_time));
    });
    const options = [];
    for (let m = open; m + 30 <= close; m += 30) options.push(fromMinutes(m));
    return options;
  }

  // 现场页当前所选时段（默认跟随真实时间的半小时档）
  function floorSlot() {
    const options = floorSlotOptions();
    const nowSlot = fromMinutes(Math.floor(toMinutes(nowHHMM()) / 30) * 30);
    if (!state.floorStart || !options.includes(state.floorStart)) {
      state.floorStart = options.includes(nowSlot) ? nowSlot : (options[0] || '07:00');
    }
    return state.floorStart;
  }

  function floorDate() {
    if (!state.floorDate) state.floorDate = state.meta.today;
    return state.floorDate;
  }

  function floorDateStrip() {
    const today = state.meta.today;
    const days = Math.max(1, state.meta.rules.booking_window_days);
    let html = '<div class="date-strip">';
    for (let i = 0; i < days; i += 1) {
      const iso = shiftDay(today, i);
      const d = parseDay(iso);
      html += '<button class="date-pill' + (floorDate() === iso ? ' active' : '') + '" type="button" data-action="pick-floor-date" data-date="' + iso + '">' +
        '<small>' + (i === 0 ? '今天' : WEEKDAYS[d.getDay()]) + '</small><strong>' + d.getDate() + '</strong></button>';
    }
    return html + '</div>';
  }

  async function floorView() {
    const date = floorDate();
    const start = floorSlot();
    const end = addMinutes(start, 30);
    const data = await api('/seats', { params: { date, start, end } });
    const seats = data.items || [];
    // 现场页也要填充 seatCache，否则 selectSeat() 找不到座位会静默返回，点击座位无任何反应
    state.seatCache = seats;
    const groups = state.meta.rooms.map((room) => ({ room, seats: seats.filter((seat) => seat.room_id === room.id) })).filter((group) => group.seats.length);

    const isToday = date === state.meta.today;
    const freeCount = seats.filter((seat) => seat.available).length;
    const toolbar =
      '<div class="card toolbar">' +
        floorDateStrip() +
        '<div class="toolbar-row">' +
          '<label class="row small muted" style="gap:7px">时段' +
            '<select class="select-compact" data-action-select="floor-slot">' + floorSlotOptions().map((value) =>
              '<option value="' + value + '"' + (value === start ? ' selected' : '') + '>' + value + '–' + addMinutes(value, 30) + '</option>').join('') + '</select>' +
          '</label>' +
          '<button class="btn btn-sm" type="button" data-action="floor-now">回到当前时段</button>' +
          '<div class="grow"></div>' +
          '<span class="pill ' + (freeCount ? 'green' : 'gray') + ' plain">' + freeCount + ' / ' + seats.length + ' 空闲</span>' +
        '</div>' +
      '</div>';

    const banner = '<div class="banner info" style="margin:14px 0">' + icon('alert') +
      '<div>' + (isToday ? '' : fmtDay(date) + ' · ') + start + '–' + end +
      (isToday && start !== fromMinutes(Math.floor(toMinutes(nowHHMM()) / 30) * 30) ? '（非当前时段）' : '') +
      '。点击座位可查看占用详情、登记违约或强制释放；点击空闲座位可封禁。</div></div>';

    const legend = '<div class="legend" style="margin:0 2px 12px"><span><i style="background:#34C759"></i>空闲</span><span><i style="background:#FF9500"></i>已预约</span><span><i style="background:#007AFF"></i>使用中</span><span><i style="background:#C77D00"></i>暂离</span><span><i style="background:#8E8E93"></i>维护</span></div>';

    const board = groups.map(({ room, seats: roomSeats }) =>
      '<section class="seat-room"><div class="seat-room-head"><div><h3>' + esc(room.name) + '</h3><p>' + esc(room.location || '') + '</p></div>' +
      '<span class="tiny tertiary">' + roomSeats.filter((seat) => seat.available).length + ' 空闲</span></div>' +
      '<div class="seat-grid">' + roomSeats.map((seat) => seatCard(seat, { showState: true })).join('') + '</div></section>').join('');

    return toolbar + banner + legend + (board || '<div class="card empty">' + icon('empty') + '<strong>该时段没有座位数据</strong><p>换一个日期或时段再试。</p></div>');
  }

  async function openSeatAdmin(seatId) {
    // 与现场页所选日期/时段保持一致，否则查不到该座位会静默返回、点了没反应
    const start = floorSlot();
    const end = addMinutes(start, 30);
    const data = await api('/seats', { params: { date: floorDate(), start, end } });
    const seat = (data.items || []).find((item) => item.id === seatId);
    if (!seat) { toast('该座位在 ' + start + '–' + end + ' 查不到，请切换时段', { error: true }); return; }

    let reservation = null;
    if (seat.holder_reservation_id) {
      try { reservation = await api('/reservations/' + encodeURIComponent(seat.holder_reservation_id)); } catch (error) { reservation = null; }
    }

    const details = [
      ['座位', seat.id + ' · ' + seat.room_name],
      ['类型', seat.type_label + (seat.capacity > 1 ? ' · ' + seat.capacity + ' 人' : '')],
      ['状态', seat.state_label],
      ['属性', [seat.has_power ? '电源' : '', seat.is_window ? '靠窗' : ''].filter(Boolean).join(' / ') || '—'],
    ];
    if (reservation) {
      details.push(['预约人', reservation.user_name + '（' + reservation.user_id + '）']);
      details.push(['时段', reservation.start + '–' + reservation.end]);
      details.push(['状态', reservation.status_label]);
    }
    const body = '<dl class="detail-list">' + details.map(([label, value]) =>
      '<div class="detail-row"><dt>' + label + '</dt><dd>' + (label === '状态' && reservation ? statusPill(reservation.status) : esc(value)) + '</dd></div>').join('') + '</dl>';

    const actions = [];
    if (reservation) {
      actions.push({ label: '登记违约', kind: 'secondary', onClick: () => manualViolationSheet(reservation.id) });
      actions.push({ label: '强制释放', kind: 'danger', onClick: () => forceReleaseSheet(reservation.id) });
    } else if (seat.state === 'maintenance') {
      actions.push({ label: '解除封禁', kind: 'primary', onClick: () => unblockSeat(seat.id) });
    } else if (seat.state === 'available' || seat.state === 'disabled') {
      actions.push({ label: '封禁座位', kind: 'danger', onClick: () => blockSeatSheet(seat.id) });
    }
    if (!actions.length) actions.push({ label: '关闭', kind: 'secondary', onClick: closeSheet });
    openSheet({ title: '座位 ' + seat.id, subtitle: seat.room_name, body, actions });
  }

  function manualViolationSheet(reservationId) {
    openSheet({
      title: '登记现场违约',
      subtitle: '必须填写现场核实说明，扣 5 分',
      body:
        '<label class="field"><span>处置方式</span><select id="violationMode">' +
          '<option value="warn">仅警告（预约与座位保持）</option>' +
          '<option value="release">强制释放座位</option></select></label>' +
        '<label class="field"><span>现场核实说明</span><textarea id="violationNote" maxlength="500" placeholder="说明到场时间、座位实际使用情况、物品状况"></textarea></label>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '提交登记', kind: 'danger', onClick: async () => {
            const note = $('#violationNote').value.trim();
            if (note.length < 4) { toast('请填写现场核实说明', { error: true }); return; }
            await api('/admin/manual-violation', { method: 'POST', body: {
              reservation_id: reservationId, mode: $('#violationMode').value, note,
            } });
            closeSheet();
            toast('已登记违约');
            renderView();
          } },
      ],
    });
  }

  function forceReleaseSheet(reservationId) {
    openSheet({
      title: '强制释放座位',
      subtitle: '不记违规，但会写入审计日志',
      body: '<label class="field"><span>原因</span><textarea id="releaseReason" maxlength="500" placeholder="例如：现场核查无人使用，清理个人物品"></textarea></label>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '确认释放', kind: 'danger', onClick: async () => {
            const reason = $('#releaseReason').value.trim();
            if (reason.length < 2) { toast('请填写原因', { error: true }); return; }
            await api('/admin/reservations/' + encodeURIComponent(reservationId) + '/force-release', { method: 'POST', body: { reason } });
            closeSheet();
            toast('已释放座位');
            renderView();
          } },
      ],
    });
  }

  function blockSeatSheet(seatId) {
    const now = new Date();
    const later = new Date(now.getTime() + 2 * 3600 * 1000);
    openSheet({
      title: '封禁座位',
      subtitle: '受影响预约将自动取消，不记违规',
      body:
        '<label class="field"><span>原因</span><input id="blockReason" maxlength="200" placeholder="例如：桌面损坏，等待维修"></label>' +
        '<div class="row" style="gap:10px"><label class="field grow"><span>开始</span><input id="blockStart" type="datetime-local" value="' + localDateTimeValue(now) + '"></label>' +
        '<label class="field grow"><span>结束</span><input id="blockEnd" type="datetime-local" value="' + localDateTimeValue(later) + '"></label></div>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '确认封禁', kind: 'danger', onClick: async () => {
            const reason = $('#blockReason').value.trim();
            if (reason.length < 2) { toast('请填写原因', { error: true }); return; }
            await api('/admin/seat-blocks', { method: 'POST', body: {
              seat_id: seatId,
              reason,
              start_at: $('#blockStart').value,
              end_at: $('#blockEnd').value,
            } });
            closeSheet();
            toast('已封禁座位');
            renderView();
          } },
      ],
    });
  }

  function unblockSeat(seatId) {
    confirmSheet({
      title: '解除封禁',
      message: '解封后座位恢复可预约，历史封禁记录保留。',
      confirmLabel: '解封',
      onConfirm: async () => {
        await api('/admin/seat-blocks/' + encodeURIComponent(seatId), { method: 'DELETE' });
        closeSheet();
        toast('已解封');
        renderView();
      },
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：管理员 · 预约查询
  // ---------------------------------------------------------------------------
  async function searchView() {
    const filters = state.searchFilters || (state.searchFilters = {
      keyword: '', status: '', date: '', mode: 'reservations', source: '', violationType: '',
    });
    if (!filters.mode) filters.mode = 'reservations';
    const isViolation = filters.mode === 'violations';
    const data = await api(isViolation ? '/violations' : '/admin/reservations', { params: isViolation ? {
      page: state.page, page_size: 20, keyword: filters.keyword || undefined,
      status: filters.status || undefined, source: filters.source || undefined,
      violation_type: filters.violationType || undefined,
    } : {
      page: state.page, page_size: 20, keyword: filters.keyword || undefined,
      status: filters.status || undefined, date: filters.date || undefined,
    } });
    const items = data.items || [];

    const modeTabs = '<div class="segmented" style="margin-bottom:14px">' +
      '<button type="button" data-action="search-mode" data-mode="reservations" class="' + (!isViolation ? 'active' : '') + '">预约记录</button>' +
      '<button type="button" data-action="search-mode" data-mode="violations" class="' + (isViolation ? 'active' : '') + '">违规记录</button>' +
      '</div>';
    const statusOptions = isViolation
      ? ['active', 'appealing', 'revoked'].map((value) =>
          '<option value="' + value + '"' + (filters.status === value ? ' selected' : '') + '>' + (VIOLATION_STATUS[value] ? VIOLATION_STATUS[value][0] : value) + '</option>').join('')
      : Object.entries(RES_STATUS).map(([value, meta]) =>
          '<option value="' + value + '"' + (filters.status === value ? ' selected' : '') + '>' + meta[0] + '</option>').join('');
    const filtersHtml =
      '<div class="card toolbar" style="margin-bottom:14px"><div class="toolbar-row">' +
        '<input class="field grow" id="searchKeyword" placeholder="' + (isViolation ? '违规号 / 学号 / 姓名 / 预约号' : '预约号 / 学号 / 姓名 / 座位号') + '" value="' + esc(filters.keyword) + '" style="height:38px;border:1px solid var(--separator);border-radius:9px;padding:0 11px;background:var(--card-muted)">' +
        '<select class="select-compact" data-search="status"><option value="">全部状态</option>' + statusOptions + '</select>' +
        (isViolation
          ? '<select class="select-compact" data-search="source"><option value="">全部来源</option><option value="system"' + (filters.source === 'system' ? ' selected' : '') + '>系统自动</option><option value="manual"' + (filters.source === 'manual' ? ' selected' : '') + '>管理员手动</option></select>' +
            '<select class="select-compact" data-search="violationType"><option value="">全部类型</option>' + Object.entries(state.meta.violation_types || {}).map(([value, label]) =>
              '<option value="' + esc(value) + '"' + (filters.violationType === value ? ' selected' : '') + '>' + esc(label) + '</option>').join('') + '</select>'
          : '<input class="select-compact" type="date" data-search="date" value="' + esc(filters.date) + '">') +
        '<button class="btn btn-primary btn-sm" type="button" data-action="do-search">查询</button>' +
      '</div></div>';

    if (!items.length) {
      return modeTabs + filtersHtml + emptyBlock(isViolation ? '没有匹配的违规' : '没有匹配的预约', '调整关键词或筛选条件后再试。');
    }

    const rows = isViolation ? items.map((item) => {
      const status = VIOLATION_STATUS[item.status] || [item.status, 'gray'];
      return '<tr>' +
        '<td class="mono">' + esc(item.id) + '</td>' +
        '<td>' + esc(item.user_id) + '</td>' +
        '<td>' + esc(item.type_label || item.type) + '</td>' +
        '<td>-' + esc(item.points) + '</td>' +
        '<td>' + (item.source === 'manual' ? '管理员手动' : '系统自动') + '</td>' +
        '<td><span class="pill ' + status[1] + '">' + status[0] + '</span></td>' +
        '<td class="nowrap">' + esc(item.occurred_at || '—') + '</td></tr>';
    }).join('') : items.map((item) => {
      const status = RES_STATUS[item.status] || [item.status, 'gray'];
      return '<tr class="clickable" data-action="open-reservation" data-id="' + esc(item.id) + '">' +
        '<td class="mono">' + esc(item.id) + '</td><td>' + esc(item.user_name) + '<br><span class="tiny tertiary">' + esc(item.user_id) + '</span></td>' +
        '<td>' + esc(item.seat_id) + '<br><span class="tiny tertiary">' + esc(item.room_name) + '</span></td>' +
        '<td class="nowrap">' + esc(item.date) + '<br><span class="tiny tertiary">' + esc(item.start) + '–' + esc(item.end) + '</span></td>' +
        '<td><span class="pill ' + status[1] + '">' + status[0] + '</span></td></tr>';
    }).join('');

    const table = isViolation
      ? '<div class="table-wrap"><table class="data"><thead><tr><th>违规号</th><th>学号</th><th>类型</th><th>扣分</th><th>来源</th><th>状态</th><th>时间</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
      : '<div class="table-wrap"><table class="data"><thead><tr><th>预约号</th><th>学生</th><th>座位</th><th>时段</th><th>状态</th></tr></thead><tbody>' + rows + '</tbody></table></div>';

    return modeTabs + filtersHtml + table +
      '<div class="pager"><span>共 ' + data.total + ' 条</span><div class="row"><button class="btn btn-secondary btn-sm" type="button" data-action="page-prev"' + (state.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<button class="btn btn-secondary btn-sm" type="button" data-action="page-next"' + (state.page * 20 >= data.total ? ' disabled' : '') + '>下一页</button></div></div>';
  }

  // ---------------------------------------------------------------------------
  // 视图：管理员 · 投诉
  // ---------------------------------------------------------------------------
  async function complaintsView() {
    const tab = state.complaintTab;
    const data = await api('/admin/complaints', { params: { status: tab === 'pending' ? 'pending' : undefined, page_size: 50 } });
    const items = (data.items || []).filter((item) => tab === 'pending' ? item.status === 'pending' : item.status !== 'pending');

    const tabs = '<div class="segmented" style="margin-bottom:14px">' +
      '<button type="button" data-action="complaint-tab" data-tab="pending" class="' + (tab === 'pending' ? 'active' : '') + '">待处理</button>' +
      '<button type="button" data-action="complaint-tab" data-tab="done" class="' + (tab === 'done' ? 'active' : '') + '">已办结</button></div>';

    if (!items.length) return tabs + emptyBlock(tab === 'pending' ? '没有待处理投诉' : '还没有已办结记录', '学生提交的投诉会出现在这里。');

    return tabs + '<div class="list">' + items.map((item) => {
      const status = COMPLAINT_STATUS[item.status] || [item.status, 'gray'];
      return '<div class="list-row"><div class="row-main"><div class="row-title">' + esc(item.type_label) + ' <span class="tertiary small">' + esc(item.user_name) + (item.seat_id ? ' · ' + esc(item.seat_id) : '') + '</span></div>' +
        '<div class="row-sub">' + fmtDateTime(item.created_at) + ' · ' + esc(item.content) + '</div>' +
        (item.feedback ? '<div class="row-sub" style="color:#1F8F43">反馈：' + esc(item.feedback) + '</div>' : '') +
        '</div><div class="row-side"><span class="pill ' + status[1] + '">' + status[0] + '</span>' +
        (item.status === 'pending' ? '<button class="btn btn-primary btn-sm" type="button" data-action="resolve-complaint" data-id="' + esc(item.id) + '">处理</button>' : '') +
        '</div></div>';
    }).join('') + '</div>';
  }

  function resolveComplaintSheet(id) {
    openSheet({
      title: '办结投诉',
      subtitle: '反馈内容学生端可见，办结前必填',
      body:
        '<label class="field"><span>反馈信息</span><textarea id="complaintFeedback" maxlength="1000" placeholder="说明核查结果与处理方式"></textarea></label>' +
        '<div class="switch-row" style="margin:2px 0 10px"><span class="small">同时登记现场违约</span><label class="switch"><input type="checkbox" id="linkedViolation"><span></span></label></div>' +
        '<div id="linkedFields" hidden><label class="field"><span>关联预约号</span><input id="linkedReservation" placeholder="R20261031-0001"></label>' +
        '<label class="field"><span>处置方式</span><select id="linkedMode"><option value="warn">仅警告</option><option value="release">强制释放</option></select></label></div>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '办结', kind: 'primary', onClick: async () => {
            const feedback = $('#complaintFeedback').value.trim();
            if (feedback.length < 2) { toast('请填写反馈信息', { error: true }); return; }
            const linked = $('#linkedViolation').checked;
            const linkedId = linked ? $('#linkedReservation').value.trim() : null;
            if (linked && !linkedId) { toast('请填写关联预约号', { error: true }); return; }
            await api('/admin/complaints/' + encodeURIComponent(id) + '/resolve', { method: 'POST', body: {
              feedback, linked_reservation_id: linkedId, linked_mode: linked ? $('#linkedMode').value : null,
            } });
            closeSheet();
            toast('投诉已办结');
            renderView();
          } },
      ],
      onMount: (wrap) => {
        wrap.querySelector('#linkedViolation').addEventListener('change', (event) => {
          wrap.querySelector('#linkedFields').hidden = !event.target.checked;
        });
      },
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：管理员 · 评价回复
  // 后端接口 /admin/reviews 与 /admin/reviews/{id}/reply 一直存在，
  // 但出厂前端没有接入口，学生评价提交后无人可回。这里补上列表与回复。
  // ---------------------------------------------------------------------------
  async function reviewsView() {
    const tab = state.reviewTab;
    const data = await api('/admin/reviews', { params: { page: 1, page_size: 50 } });
    const all = data.items || [];
    const items = all.filter((item) => (tab === 'pending' ? !item.reply : Boolean(item.reply)));
    const pending = all.filter((item) => !item.reply).length;

    const tabs = '<div class="segmented" style="margin-bottom:14px">' +
      '<button type="button" data-action="review-tab" data-tab="pending" class="' + (tab === 'pending' ? 'active' : '') + '">待回复 ' + pending + '</button>' +
      '<button type="button" data-action="review-tab" data-tab="done" class="' + (tab === 'done' ? 'active' : '') + '">已回复</button></div>';

    if (!items.length) return tabs + emptyBlock(tab === 'pending' ? '没有待回复评价' : '还没有回复记录', '学生签退后提交的评价会出现在这里。');

    return tabs + '<div class="list">' + items.map((item) => {
      const stars = item.rating ? '★'.repeat(item.rating) : '';
      const tags = Array.isArray(item.tags) && item.tags.length ? item.tags.join(' · ') : '';
      const who = item.user_name || item.user_id || '';
      return '<div class="list-row"><div class="row-main"><div class="row-title">' +
        esc(item.seat_id || '自习室整体') + ' <span class="tertiary small">' + esc(who) + '</span></div>' +
        '<div class="row-sub">' + fmtDateTime(item.created_at) +
        (stars ? ' · <span class="small">' + stars + '</span>' : '') +
        (tags ? ' · ' + esc(tags) : '') + '</div>' +
        '<div class="row-sub">' + esc(item.content || '') + '</div>' +
        (item.reply ? '<div class="row-sub" style="color:#1F8F43">回复：' + esc(item.reply) +
          (item.replied_at ? '（' + fmtDateTime(item.replied_at) + '）' : '') + '</div>' : '') +
        '</div><div class="row-side">' +
        (item.reply ? '<span class="pill green plain">已回复</span>' : '<button class="btn btn-primary btn-sm" type="button" data-action="reply-review" data-id="' + esc(item.id) + '">回复</button>') +
        '</div></div>';
    }).join('') + '</div>';
  }

  function replyReviewSheet(id) {
    openSheet({
      title: '回复评价',
      subtitle: '回复内容学生端可见，2–500 字',
      body:
        '<label class="field"><span>回复内容</span><textarea id="reviewReply" maxlength="500" placeholder="说明改进措施或处理结果"></textarea></label>' +
        '<div class="switch-row" style="margin:2px 0 10px"><span class="small">同时封禁该座位</span><label class="switch"><input type="checkbox" id="reviewBlockSeat"><span></span></label></div>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '提交', kind: 'primary', onClick: async () => {
            const reply = $('#reviewReply').value.trim();
            if (reply.length < 2) { toast('请填写至少 2 个字的回复', { error: true }); return; }
            await api('/admin/reviews/' + encodeURIComponent(id) + '/reply', { method: 'POST', body: {
              reply,
              block_seat: Boolean($('#reviewBlockSeat') && $('#reviewBlockSeat').checked),
            } });
            closeSheet();
            toast('回复已提交');
            renderView();
          } },
      ],
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：管理员 · 违规申诉审核
  // 后端 DomainEngine.review_appeal 一直存在（PR-10：仅自习室管理员可审核，
  // 成立则撤销违规并恢复信用分），但出厂包既没挂 HTTP 接口也没给前端入口，
  // 学生提交申诉后会一直卡在「申诉中」。接口由本地补丁补上，这里补列表与审核入口。
  // ---------------------------------------------------------------------------
  async function appealsView() {
    const tab = state.appealTab;
    const data = await api('/violations', { params: { page: 1, page_size: 50 } });
    const all = data.items || [];
    const appealing = all.filter((item) => item.status === 'appealing');
    const handled = all.filter((item) => item.status !== 'appealing' && (item.review_note || item.reviewed_by));
    const items = tab === 'pending' ? appealing : handled;

    const tabs = '<div class="segmented" style="margin-bottom:14px">' +
      '<button type="button" data-action="appeal-tab" data-tab="pending" class="' + (tab === 'pending' ? 'active' : '') + '">待审核 ' + appealing.length + '</button>' +
      '<button type="button" data-action="appeal-tab" data-tab="done" class="' + (tab === 'done' ? 'active' : '') + '">已处理</button></div>';

    if (!items.length) {
      return tabs + emptyBlock(tab === 'pending' ? '没有待审核的申诉' : '还没有已处理的申诉',
        '学生提交申诉后会出现在这里；审核成立将撤销违规并恢复信用分。');
    }

    return tabs + '<div class="list">' + items.map((item) => {
      const revoked = item.status === 'revoked';
      return '<div class="list-row"><div class="row-main"><div class="row-title">' +
        esc(item.id) + ' <span class="tertiary small">' + esc(item.user_id || '') + '</span></div>' +
        '<div class="row-sub">' + esc(item.type_label || item.type || '') +
        (item.points ? ' · 扣 ' + item.points + ' 分' : '') + ' · ' + fmtDateTime(item.occurred_at) + '</div>' +
        '<div class="row-sub">申诉理由：' + esc(item.appeal_reason || '—') + '</div>' +
        (item.review_note ? '<div class="row-sub" style="color:#1F8F43">审核：' + (revoked ? '成立 · ' : '驳回 · ') +
          esc(item.review_note) + (item.reviewed_by ? '（' + esc(item.reviewed_by) + '）' : '') + '</div>' : '') +
        '</div><div class="row-side">' +
        (item.status === 'appealing'
          ? '<button class="btn btn-primary btn-sm" type="button" data-action="review-appeal" data-id="' + esc(item.id) + '">审核</button>'
          : '<span class="pill ' + (revoked ? 'green' : 'gray') + ' plain">' + (revoked ? '已撤销' : '已维持') + '</span>') +
        '</div></div>';
    }).join('') + '</div>';
  }

  function reviewAppealSheet(id) {
    openSheet({
      title: '审核申诉 ' + id,
      subtitle: '仅自习室管理员可审核（PR-10）',
      body:
        '<label class="field"><span>审核结论</span><select id="appealDecision">' +
        '<option value="approve">成立 —— 撤销违规并恢复信用分</option>' +
        '<option value="reject">不成立 —— 维持原判</option></select></label>' +
        '<label class="field"><span>审核说明（学生可见，至少 2 字）</span><textarea id="appealNote" maxlength="500" placeholder="说明核实依据"></textarea></label>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '提交', kind: 'primary', onClick: async () => {
            const note = $('#appealNote').value.trim();
            if (note.length < 2) { toast('请填写至少 2 个字的审核说明', { error: true }); return; }
            const decision = $('#appealDecision').value === 'reject' ? 'reject' : 'approve';
            await api('/violations/' + encodeURIComponent(id) + '/review', { method: 'POST', body: { decision, note } });
            closeSheet();
            toast(decision === 'approve' ? '申诉成立，已撤销违规并恢复信用分' : '申诉不成立，维持原判');
            renderView();
          } },
      ],
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：管理员 · 学生与黑名单
  // ---------------------------------------------------------------------------
  async function studentsView() {
    const keyword = state.studentKeyword || '';
    const [data, blacklist] = await Promise.all([
      api('/admin/students', { params: { keyword: keyword || undefined, page_size: 50 } }),
      api('/admin/blacklist', { params: { active_only: true, page_size: 100 } }),
    ]);
    const active = new Map((blacklist.items || []).map((item) => [item.user_id, item]));
    const items = data.items || [];

    const header = '<div class="card toolbar" style="margin-bottom:14px"><div class="toolbar-row">' +
      '<input id="studentKeyword" placeholder="姓名 / 学号" value="' + esc(keyword) + '" style="flex:1;height:38px;border:1px solid var(--separator);border-radius:9px;padding:0 11px;background:var(--card-muted)">' +
      '<button class="btn btn-primary btn-sm" type="button" data-action="do-student-search">搜索</button></div></div>';

    if (!items.length) return header + emptyBlock('没有学生账号', '账号由系统管理员创建。');

    return header + '<div class="list">' + items.map((user) => {
      const record = active.get(user.id);
      const limited = user.limit_type !== 'none';
      return '<div class="list-row"><span class="avatar-sm">' + esc(user.name.slice(0, 1)) + '</span>' +
        '<div class="row-main"><div class="row-title">' + esc(user.name) + ' <span class="tertiary small">' + esc(user.id) + '</span></div>' +
        '<div class="row-sub">' + esc(user.class_name || '未分班') + ' · 信用 ' + user.credit + ' · ' + esc(user.limit_label) + '</div></div>' +
        '<div class="row-side">' + (record ? '<span class="pill red">黑名单</span>' : limited ? '<span class="pill orange">受限</span>' : '<span class="pill green">正常</span>') +
        (record
          ? '<button class="btn btn-secondary btn-sm" type="button" data-action="blacklist-release" data-id="' + esc(user.id) + '">解封</button>'
          : '<button class="btn btn-danger btn-sm" type="button" data-action="blacklist-add" data-id="' + esc(user.id) + '">拉黑</button>') +
        '</div></div>';
    }).join('') + '</div>';
  }

  function blacklistAddSheet(userId) {
    openSheet({
      title: '加入黑名单',
      subtitle: '限制期间无法创建预约',
      body:
        '<label class="field"><span>原因</span><textarea id="blacklistReason" maxlength="500" placeholder="例如：多次恶意占座，现场劝阻无效"></textarea></label>' +
        '<label class="field"><span>限制天数</span><input id="blacklistDays" type="number" min="1" max="365" value="7"></label>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '确认拉黑', kind: 'danger', onClick: async () => {
            const reason = $('#blacklistReason').value.trim();
            if (reason.length < 2) { toast('请填写原因', { error: true }); return; }
            await api('/admin/blacklist', { method: 'POST', body: { user_id: userId, reason, days: Number($('#blacklistDays').value) } });
            closeSheet();
            toast('已加入黑名单');
            renderView();
          } },
      ],
    });
  }

  function blacklistReleaseSheet(userId) {
    openSheet({
      title: '解除黑名单',
      body: '<label class="field"><span>解封原因</span><textarea id="releaseBlacklistReason" maxlength="500" placeholder="例如：申诉复核通过，提前解除"></textarea></label>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '确认解封', kind: 'primary', onClick: async () => {
            const reason = $('#releaseBlacklistReason').value.trim();
            if (reason.length < 2) { toast('请填写原因', { error: true }); return; }
            await api('/admin/blacklist/' + encodeURIComponent(userId) + '/release', { method: 'POST', body: { reason } });
            closeSheet();
            toast('已解除黑名单');
            renderView();
          } },
      ],
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：教师
  // ---------------------------------------------------------------------------
  async function classView() {
    const data = await api('/teacher/dashboard');
    const kpi = data.kpi;
    const trend = data.trend || [];
    return '<div class="banner info" style="margin-bottom:14px">' + icon('alert') +
      '<div>仅展示 ' + esc(data.class_name) + ' 的汇总数据，不含学生个人明细（PR-12）。</div></div>' +
      '<div class="kpi-grid">' +
        '<div class="kpi blue"><div class="kpi-label">本班学生</div><div class="kpi-value">' + kpi.students + '<small>人</small></div><div class="kpi-foot">' + esc(data.class_name) + '</div></div>' +
        '<div class="kpi"><div class="kpi-label">今日预约</div><div class="kpi-value">' + kpi.today_reservations + '<small>单</small></div><div class="kpi-foot">已签到 ' + kpi.today_checkins + '</div></div>' +
        '<div class="kpi"><div class="kpi-label">本月完成</div><div class="kpi-value">' + kpi.month_completed + '<small>次</small></div><div class="kpi-foot">近 30 日出勤率 ' + (kpi.attendance_rate == null ? '—' : kpi.attendance_rate + '%') + '</div></div>' +
        '<div class="kpi"><div class="kpi-label">30 日违规</div><div class="kpi-value">' + kpi.violations_30 + '<small>条</small></div><div class="kpi-foot">仅统计数量</div></div>' +
      '</div>' +
      '<div class="card chart-card" style="margin-top:12px"><div class="chart-head"><h3>近 7 日班级预约</h3><div class="legend"><span><i style="background:#007AFF"></i>预约</span></div></div>' +
      '<div class="chart">' + trendChart(trend, { withCheckins: false }) + '</div></div>';
  }

  // ---------------------------------------------------------------------------
  // 视图：系统管理员 · 参数
  // ---------------------------------------------------------------------------
  async function paramsView() {
    const data = await api('/admin/params');
    const groups = {};
    (data.items || []).forEach((item) => {
      const prefix = item.code.split('.')[0];
      if (!groups[prefix]) groups[prefix] = [];
      groups[prefix].push(item);
    });
    const labels = { booking: '预约', checkin: '签到', away: '暂离', credit: '信用分', cancel: '取消', consecutive: '连续违约', waitlist: '候补', blacklist: '黑名单', appeal: '申诉', reminder: '提醒', room: '开放时间' };

    return Object.entries(groups).map(([prefix, items]) =>
      '<div class="section" style="margin-top:16px"><div class="section-head"><h2>' + esc(labels[prefix] || prefix) + '</h2></div>' +
      '<div class="list">' + items.map((item) =>
        '<button class="list-row clickable" type="button" data-action="edit-param" data-code="' + esc(item.code) + '" data-label="' + esc(item.label) + '" data-value="' + esc(item.value) + '" data-kind="' + esc(item.kind) + '" data-unit="' + esc(item.unit || '') + '">' +
        '<div class="row-main"><div class="row-title">' + esc(item.label) + '</div><div class="row-sub mono">' + esc(item.code) + '</div></div>' +
        '<div class="row-side"><span class="mono" style="font-weight:600">' + esc(item.value) + '</span><span class="tiny tertiary">' + esc(item.unit || '') + '</span>' + icon('chevron', 'row-chevron') + '</div></button>').join('') + '</div></div>'
    ).join('');
  }

  function editParamSheet(config) {
    openSheet({
      title: config.label,
      subtitle: config.code,
      body: '<label class="field"><span>当前值' + (config.unit ? '（' + esc(config.unit) + '）' : '') + '</span><input id="paramValue" value="' + esc(config.value) + '"></label>' +
        '<p class="tiny tertiary">修改只对之后创建的预约生效；已有预约按创建时的参数快照执行。</p>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '保存', kind: 'primary', onClick: async () => {
            await api('/admin/params/' + encodeURIComponent(config.code), { method: 'PUT', body: { value: $('#paramValue').value.trim() } });
            closeSheet();
            toast('参数已更新');
            renderView();
          } },
      ],
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：系统管理员 · 账号
  // ---------------------------------------------------------------------------
  async function usersView() {
    const data = await api('/admin/users', { params: { page_size: 100 } });
    const items = data.items || [];
    return '<p class="small muted" style="margin-bottom:12px">点击账号调整角色，变更会写入审计日志。</p>' +
      '<div class="list">' + items.map((user) =>
        '<button class="list-row clickable" type="button" data-action="edit-role" data-id="' + esc(user.id) + '" data-name="' + esc(user.name) + '" data-role="' + esc(user.role) + '">' +
        '<span class="avatar-sm">' + esc(user.name.slice(0, 1)) + '</span>' +
        '<div class="row-main"><div class="row-title">' + esc(user.name) + ' <span class="tertiary small">' + esc(user.id) + '</span></div>' +
        '<div class="row-sub">' + esc(user.department || '') + (user.class_name ? ' · ' + esc(user.class_name) : '') + '</div></div>' +
        '<div class="row-side"><span class="pill gray plain">' + esc(ROLE_TEXT[user.role] || user.role) + '</span>' + icon('chevron', 'row-chevron') + '</div></button>').join('') + '</div>';
  }

  function editRoleSheet(config) {
    let selected = config.role;
    const options = Object.entries(ROLE_TEXT);
    openSheet({
      title: '调整角色',
      subtitle: config.name + ' · ' + config.id,
      body: '<div class="segmented block" id="roleOptions">' + options.map(([value, label]) =>
        '<button type="button" data-role="' + value + '" class="' + (value === selected ? 'active' : '') + '">' + label + '</button>').join('') + '</div>' +
        '<p class="tiny tertiary" style="margin-top:12px">不能移除最后一名系统管理员；变更即时生效。</p>',
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '保存', kind: 'primary', onClick: async () => {
            await api('/admin/users/' + encodeURIComponent(config.id) + '/role', { method: 'PUT', body: { role: selected } });
            closeSheet();
            toast('角色已调整');
            renderView();
          } },
      ],
      onMount: (wrap) => {
        wrap.querySelector('#roleOptions').addEventListener('click', (event) => {
          const button = event.target.closest('[data-role]');
          if (!button) return;
          selected = button.dataset.role;
          $$('#roleOptions button', wrap).forEach((el) => el.classList.toggle('active', el === button));
        });
      },
    });
  }

  // ---------------------------------------------------------------------------
  // 视图：系统管理员 · 审计
  // ---------------------------------------------------------------------------
  async function auditView() {
    const action = state.auditAction || '';
    const data = await api('/admin/audit', { params: { action: action || undefined, page: state.page, page_size: 30 } });
    const items = data.items || [];
    const filters = '<div class="card toolbar" style="margin-bottom:14px"><div class="toolbar-row">' +
      '<select class="select-compact" data-audit="action"><option value="">全部动作</option>' +
        ['seat_block', 'seat_unblock', 'force_release', 'manual_violation', 'param_change', 'role_change', 'appeal_review', 'blacklist_add', 'blacklist_release', 'complaint_resolve', 'review_reply', 'auto_blacklist'].map((value) =>
          '<option value="' + value + '"' + (action === value ? ' selected' : '') + '>' + value + '</option>').join('') + '</select>' +
      '<div class="grow"></div><a class="btn btn-secondary btn-sm" href="/api/admin/export/audit.csv">导出 CSV</a></div></div>';

    if (!items.length) return filters + emptyBlock('没有审计记录', '封禁、释放、参数与角色变更会记录在这里。');

    return filters + '<div class="table-wrap"><table class="data"><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>IP</th></tr></thead><tbody>' +
      items.map((item) => '<tr class="clickable" data-action="audit-detail" data-id="' + esc(item.id) + '">' +
        '<td class="nowrap">' + fmtDateTime(item.created_at) + '</td><td>' + esc(item.actor_id) + '<br><span class="tiny tertiary">' + esc(item.actor_role) + '</span></td>' +
        '<td class="mono">' + esc(item.action) + '</td><td class="mono">' + esc(item.target_type) + ' / ' + esc(item.target_id) + '</td><td class="tiny tertiary">' + esc(item.ip || '—') + '</td></tr>').join('') +
      '</tbody></table></div><div class="pager"><span>共 ' + data.total + ' 条</span><div class="row">' +
      '<button class="btn btn-secondary btn-sm" type="button" data-action="page-prev"' + (state.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<button class="btn btn-secondary btn-sm" type="button" data-action="page-next"' + (state.page * 30 >= data.total ? ' disabled' : '') + '>下一页</button></div></div>';
  }

  async function openAuditDetail(id) {
    const data = await api('/admin/audit', { params: { page_size: 100 } });
    const item = (data.items || []).find((row) => row.id === id);
    if (!item) return;
    const body = '<dl class="detail-list">' +
      [['日志号', item.id], ['操作人', item.actor_id + '（' + item.actor_role + '）'], ['动作', item.action], ['对象', item.target_type + ' / ' + item.target_id], ['时间', fmtDateTime(item.created_at)], ['IP', item.ip || '—']]
        .map(([label, value]) => '<div class="detail-row"><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>').join('') + '</dl>' +
      '<div class="section-head" style="margin-top:16px"><h2>变更前</h2></div><pre class="mono small" style="white-space:pre-wrap;background:var(--card-muted);padding:12px;border-radius:10px;overflow:auto">' + esc(JSON.stringify(item.before, null, 2) || '—') + '</pre>' +
      '<div class="section-head" style="margin-top:14px"><h2>变更后</h2></div><pre class="mono small" style="white-space:pre-wrap;background:var(--card-muted);padding:12px;border-radius:10px;overflow:auto">' + esc(JSON.stringify(item.after, null, 2) || '—') + '</pre>';
    openSheet({ title: '审计详情', subtitle: item.id, body, actions: [{ label: '关闭', kind: 'secondary', onClick: closeSheet }] });
  }

  // ---------------------------------------------------------------------------
  // 视图：系统管理员 · 系统
  // ---------------------------------------------------------------------------
  async function systemView() {
    const [health, scheduler] = await Promise.all([api('/health'), api('/admin/scheduler')]);
    const summary = scheduler.last_summary || {};
    const rows = [
      ['版本', health.version],
      ['环境', health.env],
      ['Schema', 'v' + health.db.schema_version],
      ['数据库大小', (health.db.size_bytes / 1024).toFixed(1) + ' KB'],
      ['运行时长', Math.floor(health.uptime_seconds / 60) + ' 分钟'],
      ['调度器', scheduler.enabled ? (scheduler.running ? '运行中' : '未启动') : '已禁用'],
      ['扫描间隔', (scheduler.interval_seconds || 0) + ' 秒'],
      ['最近扫描', scheduler.last_run_at ? fmtDateTime(scheduler.last_run_at) : '—'],
      ['最近结果', '未签到 ' + (summary.no_show || 0) + ' · 暂离超时 ' + (summary.away_timeout || 0) + ' · 超时未签退 ' + (summary.late_checkout || 0) + ' · 提醒 ' + (summary.reminders || 0)],
    ];
    return '<div class="card"><dl class="detail-list" style="padding:4px 16px">' + rows.map(([label, value]) =>
      '<div class="detail-row"><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>').join('') + '</dl></div>' +
      (scheduler.last_error ? '<div class="banner danger" style="margin-top:12px">' + icon('alert') + '<div>最近一次任务失败：' + esc(scheduler.last_error) + '</div></div>' : '') +
      '<div class="section"><div class="section-head"><h2>数据导出</h2></div><div class="list">' +
        [['reservations', '预约记录'], ['violations', '违规记录'], ['audit', '审计日志']].map(([kind, label]) =>
          '<a class="list-row clickable" href="/api/admin/export/' + kind + '.csv"><div class="row-main"><div class="row-title">' + label + '</div><div class="row-sub">CSV · UTF-8 BOM · 可用 Excel 打开</div></div><div class="row-side">' + icon('download', 'row-chevron') + '</div></a>').join('') +
      '</div></div>' +
      '<div class="section"><div class="section-head"><h2>运维提示</h2></div><div class="card card-pad small muted">' +
        '<p>· 数据库为单机 SQLite（WAL），备份时请同时复制 <span class="mono">.db</span> 与 <span class="mono">-wal</span> 文件，或先停止服务再复制。</p>' +
        '<p style="margin-top:6px">· 对外部署请置于 HTTPS 反向代理之后，并设置 <span class="mono">SEAT_COOKIE_SECURE=true</span>。</p>' +
        '<p style="margin-top:6px">· 定时任务由服务内调度线程执行；多实例部署时应只保留一个调度器。</p>' +
      '</div></div>';
  }

  // ---------------------------------------------------------------------------
  // 视图：管理员 · 资源管理（FR-21）
  // ---------------------------------------------------------------------------
  async function resourcesView() {
    const [roomData, seatData] = await Promise.all([
      api('/admin/rooms'),
      api('/admin/seats', { params: { room_id: state.resourceRoom || undefined } }),
    ]);
    state.roomsAdmin = roomData.items || [];
    state.seatsAdmin = seatData.items || [];
    const rooms = state.roomsAdmin;
    const seats = state.seatsAdmin;
    const roomOptions = '<option value="">全部自习室</option>' + rooms.map((room) =>
      '<option value="' + esc(room.id) + '"' + (state.resourceRoom === room.id ? ' selected' : '') + '>' + esc(room.name) + '</option>').join('');
    const roomRows = rooms.length ? rooms.map((room) =>
      '<tr>' +
        '<td class="mono">' + esc(room.id) + '</td>' +
        '<td>' + esc(room.name) + '<br><span class="tiny tertiary">' + esc(room.location || '—') + '</span></td>' +
        '<td class="nowrap">' + esc(room.open_time) + '–' + esc(room.close_time) + '</td>' +
        '<td><span class="pill ' + (room.status === 'open' ? 'green' : 'gray') + '">' + (room.status === 'open' ? '开放' : '停用') + '</span></td>' +
        '<td>' + (room.seat_count || 0) + '</td>' +
        '<td><button class="btn btn-secondary btn-sm" type="button" data-action="edit-room" data-id="' + esc(room.id) + '">编辑</button></td>' +
      '</tr>').join('') : '<tr><td colspan="6" class="tiny tertiary">暂无自习室</td></tr>';
    const seatRows = seats.length ? seats.map((seat) => {
      const status = seat.admin_status === 'available'
        ? ['green', '启用']
        : seat.admin_status === 'maintenance' ? ['orange', '维护中'] : ['gray', '停用'];
      const attrs = [seat.has_power ? '电源' : '', seat.is_window ? '靠窗' : ''].filter(Boolean).join(' / ') || '—';
      return '<tr>' +
        '<td class="mono">' + esc(seat.id) + '</td>' +
        '<td>' + esc(seat.room_name || seat.room_id) + '</td>' +
        '<td>' + esc(seat.type_label || seat.type) + (seat.capacity > 1 ? ' · ' + seat.capacity + ' 人' : '') + '</td>' +
        '<td><span class="pill ' + status[0] + '">' + status[1] + '</span></td>' +
        '<td class="tiny tertiary">' + attrs + '</td>' +
        '<td><button class="btn btn-secondary btn-sm" type="button" data-action="edit-seat" data-id="' + esc(seat.id) + '">编辑</button></td>' +
      '</tr>';
    }).join('') : '<tr><td colspan="6" class="tiny tertiary">暂无座位</td></tr>';
    return '<div class="section">' +
        '<div class="section-head"><h2>自习室</h2><button class="btn btn-primary btn-sm" type="button" data-action="new-room">新增自习室</button></div>' +
        '<div class="card table-wrap"><table class="data"><thead><tr><th>编号</th><th>名称 / 位置</th><th>开放时间</th><th>状态</th><th>座位数</th><th>操作</th></tr></thead><tbody>' + roomRows + '</tbody></table></div>' +
      '</div>' +
      '<div class="section">' +
        '<div class="section-head"><h2>座位</h2><div class="row"><select class="select-compact" data-action-select="resource-room">' + roomOptions + '</select><button class="btn btn-primary btn-sm" type="button" data-action="new-seat">新增座位</button></div></div>' +
        '<p class="small muted" style="margin:0 0 10px">停用/维护只影响新预约；已有预约请到「现场」页处理。封禁/解封仍走现场页。</p>' +
        '<div class="card table-wrap"><table class="data"><thead><tr><th>编号</th><th>自习室</th><th>类型 / 容量</th><th>状态</th><th>属性</th><th>操作</th></tr></thead><tbody>' + seatRows + '</tbody></table></div>' +
      '</div>';
  }

  function roomSheet(room) {
    const editing = Boolean(room);
    const body =
      '<label class="field"><span>自习室编号</span><input id="roomId" value="' + esc(room ? room.id : '') + '"' + (editing ? ' disabled' : '') + '></label>' +
      '<label class="field"><span>名称</span><input id="roomName" value="' + esc(room ? room.name : '') + '"></label>' +
      '<label class="field"><span>位置</span><input id="roomLocation" value="' + esc(room ? room.location : '') + '"></label>' +
      '<div class="row" style="gap:10px">' +
        '<label class="field grow"><span>开放时间</span><input id="roomOpen" type="time" value="' + esc(room ? room.open_time : '07:00') + '"></label>' +
        '<label class="field grow"><span>关闭时间</span><input id="roomClose" type="time" value="' + esc(room ? room.close_time : '23:00') + '"></label>' +
      '</div>' +
      '<label class="field"><span>状态</span><select id="roomStatus"><option value="open"' + (!room || room.status === 'open' ? ' selected' : '') + '>开放</option><option value="closed"' + (room && room.status === 'closed' ? ' selected' : '') + '>停用</option></select></label>';
    openSheet({
      title: editing ? '编辑自习室' : '新增自习室',
      subtitle: editing ? room.id : '停用不会物理删除，已有预约会保留',
      body,
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '保存', kind: 'primary', onClick: async () => {
            const payload = {
              name: $('#roomName').value.trim(),
              location: $('#roomLocation').value.trim(),
              open_time: $('#roomOpen').value,
              close_time: $('#roomClose').value,
              status: $('#roomStatus').value,
            };
            try {
              if (editing) await api('/admin/rooms/' + encodeURIComponent(room.id), { method: 'PUT', body: payload });
              else await api('/admin/rooms', { method: 'POST', body: { id: $('#roomId').value.trim(), ...payload } });
              state.meta = await api('/meta');
              closeSheet();
              toast(editing ? '自习室已更新' : '自习室已创建');
              renderView();
            } catch (error) { handleError(error); }
          } },
      ],
    });
  }

  function seatSheet(seat) {
    const editing = Boolean(seat);
    const rooms = state.roomsAdmin.length ? state.roomsAdmin : (state.meta.rooms || []);
    const roomOptions = rooms.map((room) =>
      '<option value="' + esc(room.id) + '"' + (seat && seat.room_id === room.id ? ' selected' : '') + '>' + esc(room.name) + '</option>').join('');
    const typeOptions = Object.entries(state.meta.seat_types || {}).map(([value, label]) =>
      '<option value="' + esc(value) + '"' + ((seat ? seat.type : 'standard') === value ? ' selected' : '') + '>' + esc(label) + '</option>').join('');
    const statusOptions = [['available', '启用'], ['maintenance', '维护中'], ['disabled', '停用']].map(([value, label]) =>
      '<option value="' + value + '"' + ((seat ? seat.admin_status : 'available') === value ? ' selected' : '') + '>' + label + '</option>').join('');
    const body =
      '<label class="field"><span>座位编号</span><input id="seatId" value="' + esc(seat ? seat.id : '') + '"' + (editing ? ' disabled' : '') + '></label>' +
      '<label class="field"><span>所属自习室</span><select id="seatRoom">' + roomOptions + '</select></label>' +
      '<label class="field"><span>类型</span><select id="seatType">' + typeOptions + '</select></label>' +
      '<label class="field"><span>容量（讨论间 4~8，普通座位 1）</span><input id="seatCapacity" type="number" min="1" max="20" value="' + (seat ? seat.capacity : 1) + '"></label>' +
      '<div class="row" style="gap:16px"><label class="row small"><input id="seatPower" type="checkbox"' + (seat && seat.has_power ? ' checked' : '') + '> 电源</label><label class="row small"><input id="seatWindow" type="checkbox"' + (seat && seat.is_window ? ' checked' : '') + '> 靠窗</label></div>' +
      '<label class="field"><span>备注</span><input id="seatNote" value="' + esc(seat ? seat.note : '') + '"></label>' +
      (editing ? '<label class="field"><span>状态</span><select id="seatStatus">' + statusOptions + '</select></label>' : '');
    openSheet({
      title: editing ? '编辑座位' : '新增座位',
      subtitle: editing ? seat.id : '普通座位容量填 1；讨论间填 4~8',
      body,
      actions: [
        { label: '取消', kind: 'secondary', onClick: closeSheet },
        { label: '保存', kind: 'primary', onClick: async () => {
            const payload = {
              room_id: $('#seatRoom').value,
              type: $('#seatType').value,
              capacity: Number($('#seatCapacity').value),
              has_power: $('#seatPower').checked,
              is_window: $('#seatWindow').checked,
              note: $('#seatNote').value.trim(),
            };
            try {
              if (editing) {
                payload.admin_status = $('#seatStatus').value;
                await api('/admin/seats/' + encodeURIComponent(seat.id), { method: 'PUT', body: payload });
              } else {
                await api('/admin/seats', { method: 'POST', body: { id: $('#seatId').value.trim(), ...payload } });
              }
              state.meta = await api('/meta');
              closeSheet();
              toast(editing ? '座位已更新' : '座位已创建');
              renderView();
            } catch (error) { handleError(error); }
          } },
      ],
    });
  }

  // ---------------------------------------------------------------------------
  // 视图注册
  // ---------------------------------------------------------------------------
  const VIEWS = {
    seats: seatsView,
    reservations: reservationsView,
    credit: creditView,
    feedback: feedbackView,
    messages: messagesView,
    overview: overviewView,
    floor: floorView,
    resources: resourcesView,
    search: searchView,
    complaints: complaintsView,
    reviews: reviewsView,
    appeals: appealsView,
    students: studentsView,
    class: classView,
    params: paramsView,
    users: usersView,
    audit: auditView,
    system: systemView,
  };

  const AFTER = {
    overview: () => {
      state.refreshTimer = setInterval(async () => {
        if (state.view !== 'overview' || document.visibilityState !== 'visible') return;
        try {
          const [stats, recent] = await Promise.all([
            api('/admin/stats', { params: { room_id: state.overviewRoom || undefined } }),
            api('/admin/reservations', { params: { page_size: 6 } }),
          ]);
          state.lastSnapshot = stats;
          state.recentReservations = recent.items || [];
          const root = $('#overviewRoot');
          if (root) root.innerHTML = overviewHtml(stats, state.recentReservations);
        } catch (error) {
          const root = $('#overviewRoot');
          if (root && !root.querySelector('.banner.danger')) {
            root.insertAdjacentHTML('afterbegin', '<div class="banner danger" style="margin-bottom:12px">' + icon('alert') + '<div>刷新失败，当前显示 ' + esc((state.lastSnapshot && state.lastSnapshot.generated_at) || '上次') + ' 的快照。</div></div>');
          }
        }
      }, 10000);
    },
  };

  function emptyBlock(title, text) {
    return '<div class="card empty">' + icon('empty') + '<strong>' + esc(title) + '</strong><p>' + esc(text) + '</p></div>';
  }

  // ---------------------------------------------------------------------------
  // 动作
  // ---------------------------------------------------------------------------
  const ACTIONS = {
    nav: (el) => go(el.dataset.view),
    refresh: () => renderView(),
    'close-sheet': () => closeSheet(),
    logout: () => logout(),
    'user-menu': () => openUserMenu(),
    'pick-date': (el) => { state.seatFilters.date = el.dataset.date; state.selectedSeat = null; renderView(); },
    // 现场页：切换日期 / 时段
    'pick-floor-date': (el) => { state.floorDate = el.dataset.date; renderView(); },
    'floor-slot': (el) => { state.floorStart = el.value; renderView(); },
    'floor-now': () => {
      state.floorDate = state.meta.today;
      state.floorStart = fromMinutes(Math.floor(toMinutes(nowHHMM()) / 30) * 30);
      renderView();
    },
    'filter-room': (el) => { state.seatFilters.room = el.dataset.room; state.selectedSeat = null; renderView(); },
    'toggle-filter': (el) => { const key = el.dataset.key; state.seatFilters[key] = !state.seatFilters[key]; state.selectedSeat = null; renderView(); },
    'select-seat': (el) => selectSeat(el.dataset.id),
    book: () => bookSelected(),
    'reservation-tab': (el) => { state.reservationTab = el.dataset.tab; renderView(); },
    'credit-tab': (el) => { state.creditTab = el.dataset.tab; renderView(); },
    'complaint-tab': (el) => { state.complaintTab = el.dataset.tab; renderView(); },
    'review-tab': (el) => { state.reviewTab = el.dataset.tab; renderView(); },
    'feedback-tab': (el) => { state.feedbackTab = el.dataset.tab; renderView(); },
    'reply-review': (el) => replyReviewSheet(el.dataset.id),
    'appeal-tab': (el) => { state.appealTab = el.dataset.tab; renderView(); },
    'review-appeal': (el) => reviewAppealSheet(el.dataset.id),
    'overview-room': (el) => { state.overviewRoom = el.dataset.room; renderView(); },
    'open-reservation': (el) => openReservation(el.dataset.id).catch(handleError),
    appeal: (el) => appealSheet(el.dataset.id),
    'new-complaint': () => newComplaintSheet(),
    'rate-complaint': (el) => rateComplaintSheet(el.dataset.id),
    'mark-all-read': async () => { await api('/notifications/read', { method: 'POST', body: { ids: null } }); toast('已全部标记为已读'); refreshBadge(); renderView(); },
    'block-seat': (el) => blockSeatSheet(el.dataset.id),
    'unblock-seat': (el) => unblockSeat(el.dataset.id),
    'manual-violation': (el) => manualViolationSheet(el.dataset.id),
    'force-release': (el) => forceReleaseSheet(el.dataset.id),
    'resolve-complaint': (el) => resolveComplaintSheet(el.dataset.id),
    'blacklist-add': (el) => blacklistAddSheet(el.dataset.id),
    'blacklist-release': (el) => blacklistReleaseSheet(el.dataset.id),
    'edit-param': (el) => editParamSheet({ code: el.dataset.code, label: el.dataset.label, value: el.dataset.value, kind: el.dataset.kind, unit: el.dataset.unit }),
    'edit-role': (el) => editRoleSheet({ id: el.dataset.id, name: el.dataset.name, role: el.dataset.role }),
    'new-room': () => roomSheet(null),
    'edit-room': (el) => {
      const room = (state.roomsAdmin || []).find((item) => item.id === el.dataset.id);
      if (room) roomSheet(room);
    },
    'new-seat': () => seatSheet(null),
    'edit-seat': (el) => {
      const seat = (state.seatsAdmin || []).find((item) => item.id === el.dataset.id);
      if (seat) seatSheet(seat);
    },
    'resource-room': (el) => { state.resourceRoom = el.value; renderView(); },
    'audit-detail': (el) => openAuditDetail(el.dataset.id).catch(handleError),
    'do-search': () => {
      const input = $('#searchKeyword');
      state.searchFilters.keyword = input ? input.value.trim() : '';
      state.page = 1;
      renderView();
    },
    'search-mode': (el) => {
      state.searchFilters.mode = el.dataset.mode;
      state.searchFilters.status = '';
      state.searchFilters.source = '';
      state.searchFilters.violationType = '';
      state.page = 1;
      renderView();
    },
    'do-student-search': () => {
      const input = $('#studentKeyword');
      state.studentKeyword = input ? input.value.trim() : '';
      renderView();
    },
    'page-prev': () => { state.page = Math.max(1, state.page - 1); renderView(); },
    'page-next': () => { state.page += 1; renderView(); },
  };

  async function selectSeat(seatId) {
    const seat = state.seatCache.find((item) => item.id === seatId);
    if (!seat) return;
    // 管理员视角一律进入管理处置：空闲座位给封禁入口，占用座位给登记违约 / 强制释放
    if (roleOf() === 'room_admin' || roleOf() === 'sys_admin') {
      openSeatAdmin(seatId);
      return;
    }
    if (seat.available) {
      state.selectedSeat = state.selectedSeat && state.selectedSeat.id === seatId ? null : seat;
      renderView();
      return;
    }
    // 学生视角：不可预约的座位提示候补
    openSeatInfo(seat);
  }

  function openSeatInfo(seat) {
    const body = '<dl class="detail-list">' +
      [['座位', seat.id], ['自习室', seat.room_name], ['类型', seat.type_label], ['状态', seat.state_label], ['属性', [seat.has_power ? '电源' : '', seat.is_window ? '靠窗' : ''].filter(Boolean).join(' / ') || '—']]
        .map(([label, value]) => '<div class="detail-row"><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>').join('') + '</dl>';
    openSheet({
      title: seat.id,
      subtitle: seat.room_name,
      body,
      actions: [
        { label: '关闭', kind: 'secondary', onClick: closeSheet },
        { label: '加入候补', kind: 'primary', onClick: async () => { await joinWaitlist(seat.id); } },
      ],
    });
  }

  async function bookSelected() {
    const seat = state.selectedSeat;
    if (!seat) return;
    const filters = state.seatFilters;
    const body = { seat_id: seat.id, date: filters.date, start: filters.start, end: endTime() };
    try {
      await api('/reservations', { method: 'POST', body });
      toast('预约成功，请在开始前 15 分钟内签到');
      state.selectedSeat = null;
      go('reservations');
    } catch (error) {
      if (error.rule === 'BR-11') {
        confirmSheet({
          title: '该时段已被占用',
          message: '可以加入候补队列，有人取消时按顺序通知你。',
          confirmLabel: '加入候补',
          onConfirm: async () => { await joinWaitlist(seat.id); },
        });
      } else {
        handleError(error);
      }
    }
  }

  async function joinWaitlist(seatId) {
    const filters = state.seatFilters;
    try {
      await api('/waitlist', { method: 'POST', body: { seat_id: seatId, date: filters.date, start: filters.start, end: endTime() } });
      closeSheet();
      toast('已加入候补，座位释放后会通知你');
    } catch (error) {
      handleError(error);
    }
  }

  function openUserMenu() {
    const user = state.user;
    const body = '<dl class="detail-list">' +
      [['姓名', user.name], ['账号', user.id], ['角色', ROLE_TEXT[user.role] || user.role], ['院系', user.department || '—'], ['班级', user.class_name || '—']]
        .map(([label, value]) => '<div class="detail-row"><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>').join('') + '</dl>';
    openSheet({
      title: '账户',
      subtitle: user.id,
      body,
      actions: [
        { label: '修改密码', kind: 'secondary', onClick: () => changePasswordSheet() },
        { label: '退出登录', kind: 'danger', onClick: () => logout() },
      ],
    });
  }

  function changePasswordSheet(forced) {
    // 原密码框预填本次登录用的密码：初始密码是系统生成的，
    // 用户多半没记住，让他手输只会卡在这一步。
    const known = state.lastLoginPassword || '';
    let hint = '';
    if (forced) {
      hint = '<p class="tiny tertiary" style="margin-bottom:12px">该账号要求首次登录修改密码，完成后需要用新密码重新登录。</p>';
    } else if (known) {
      hint = '<p class="tiny tertiary" style="margin-bottom:12px">已将本次登录的密码填入下方，直接设置新密码即可。</p>';
    }

    openSheet({
      title: forced ? '首次登录需修改密码' : '修改密码',
      body:
        hint +
        '<label class="field"><span>原密码</span><input id="oldPassword" type="password" value="' + esc(known) + '" autocomplete="current-password"></label>' +
        '<label class="field"><span>新密码（至少 8 位）</span><input id="newPassword" type="password" autocomplete="new-password"></label>' +
        '<p class="tiny tertiary">修改成功后所有设备都需要重新登录。</p>',
      actions: [
        ...(forced ? [] : [{ label: '取消', kind: 'secondary', onClick: closeSheet }]),
        { label: '保存', kind: 'primary', onClick: async () => {
            await api('/auth/change-password', {
              method: 'POST',
              body: {
                old_password: $('#oldPassword').value,
                new_password: $('#newPassword').value,
              },
            });
            state.lastLoginPassword = '';
            closeSheet();
            showLogin('密码已修改，请重新登录');
            toast('密码已修改');
          } },
      ],
    });
  }

  async function openNotifications() {
    const data = await api('/notifications', { params: { page_size: 30 } });
    const items = data.items || [];
    openSheet({
      title: '消息',
      subtitle: '共 ' + data.total + ' 条',
      body: items.length
        ? '<div class="list" style="border:none">' + items.map((item) =>
            '<div class="list-row" style="padding-left:0;padding-right:0"><span class="dot ' + (item.read_at ? 'gray' : 'blue') + '"></span>' +
            '<div class="row-main"><div class="row-title" style="font-size:13.5px;font-weight:' + (item.read_at ? '500' : '600') + '">' + esc(item.content) + '</div>' +
            '<div class="row-sub">' + fmtDateTime(item.created_at) + '</div></div></div>').join('') + '</div>'
        : '<div class="empty" style="padding:24px 0">' + icon('empty') + '<strong>没有消息</strong></div>',
      actions: [
        { label: '全部已读', kind: 'secondary', onClick: async () => {
            await api('/notifications/read', { method: 'POST', body: { ids: null } });
            refreshBadge();
            closeSheet();
            toast('已全部标记为已读');
          } },
        { label: '关闭', kind: 'primary', onClick: closeSheet },
      ],
    });
  }

  // ---------------------------------------------------------------------------
  // 事件绑定
  // ---------------------------------------------------------------------------
  document.addEventListener('click', (event) => {
    const demo = event.target.closest('[data-demo-user]');
    if (demo) {
      $('#username').value = demo.dataset.demoUser;
      $('#password').value = demo.dataset.demoPass;
      $('#loginBtn').focus();
      return;
    }
    const actionEl = event.target.closest('[data-action]');
    if (!actionEl) return;
    const handler = ACTIONS[actionEl.dataset.action];
    if (!handler) return;
    event.preventDefault();
    try {
      const result = handler(actionEl);
      if (result && typeof result.catch === 'function') result.catch(handleError);
    } catch (error) {
      handleError(error);
    }
  });

  document.addEventListener('change', (event) => {
    const filter = event.target.closest('[data-filter]');
    if (filter) {
      const key = filter.dataset.filter;
      state.seatFilters[key] = filter.type === 'checkbox' ? filter.checked : filter.value;
      state.selectedSeat = null;
      renderView();
      return;
    }
    const search = event.target.closest('[data-search]');
    if (search) {
      state.searchFilters[search.dataset.search] = search.value;
      state.page = 1;
      renderView();
      return;
    }
    // 通用：下拉框直接触发指定 action（如现场页的时段切换）
    const actionSelect = event.target.closest('[data-action-select]');
    if (actionSelect) {
      const handler = ACTIONS[actionSelect.dataset.actionSelect];
      if (handler) { handler(actionSelect); return; }
    }
    const audit = event.target.closest('[data-audit]');
    if (audit) {
      state.auditAction = audit.value;
      state.page = 1;
      renderView();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.id === 'searchKeyword') {
      event.preventDefault();
      ACTIONS['do-search']();
    }
    if (event.key === 'Enter' && event.target.id === 'studentKeyword') {
      event.preventDefault();
      ACTIONS['do-student-search']();
    }
  });

  $('#loginForm').addEventListener('submit', submitLogin);
  $('#bellBtn').addEventListener('click', () => {
    if (state.view === 'messages') return;
    if (roleOf() === 'student') go('messages');
    else openNotifications().catch(handleError);
  });
  $('#userChip').addEventListener('click', openUserMenu);

  window.addEventListener('hashchange', () => {
    const view = location.hash.replace(/^#\//, '') || homeView();
    activate(view);
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshBadge();
  });

  // ---------------------------------------------------------------------------
  // 启动
  // ---------------------------------------------------------------------------
  async function boot() {
    try {
      const me = await api('/auth/me');
      state.csrf = me.csrf_token;
      state.user = me.user;
      state.meta = await api('/meta');
      enterApp();
    } catch (error) {
      showLogin();
    } finally {
      $('#boot').hidden = true;
    }
  }

  // 桌面壳：自绘标题栏（仅 ?shell=1 时启用，浏览器访问不受影响）
  function initTitlebar() {
    if (new URLSearchParams(location.search).get('shell') !== '1') return;
    document.body.classList.add('shell');

    const call = (method) => {
      const api = window.pywebview && window.pywebview.api;
      if (api && typeof api[method] === 'function') api[method]();
    };

    // 拖动标题栏：按下时让外壳记住窗口起点，移动时按鼠标位移平移窗口。
    // 早期版本直接调 SendMessage(WM_NCLBUTTONDOWN)，跨线程下会静默失效。
    const drag = $('#titlebarDrag');
    if (drag) {
      drag.addEventListener('mousedown', (event) => {
        if (event.button !== 0) return;
        const api = window.pywebview && window.pywebview.api;
        if (!api || typeof api.drag_move !== 'function') return;

        const startX = event.screenX;
        const startY = event.screenY;
        let last = 0;
        api.begin_drag();

        const onMove = (ev) => {
          const now = performance.now();
          if (now - last < 16) return;
          last = now;
          api.drag_move(ev.screenX - startX, ev.screenY - startY);
        };
        const onUp = () => {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });
    }

    const bar = $('#titlebar');
    if (bar) bar.addEventListener('dblclick', () => call('toggle_max'));

    document.querySelectorAll('[data-win]').forEach((button) => {
      button.addEventListener('click', () => {
        const action = button.dataset.win;
        if (action === 'min') call('minimize');
        else if (action === 'max') call('toggle_max');
        else call('close_window');
      });
    });
  }

  initTitlebar();
  boot();
})();
