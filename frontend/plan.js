(() => {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
  }

  let toastTimer = null;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { els.toast.hidden = true; }, 2600);
  }

  function refreshIcons() {
    if (window.lucide) {
      try { window.lucide.createIcons({ attrs: { width: 16, height: 16 } }); } catch (e) { /* ignore */ }
    }
  }

  const els = {
    planStatus: $('#planStatus'),
    planDate: $('#planDate'),
    planContent: $('#planContent'),
    btnPrevDay: $('#btnPrevDay'),
    btnNextDay: $('#btnNextDay'),
    toast: $('#toast')
  };

  const state = {
    date: todayStr(),
    dates: [],
    data: null
  };

  function todayStr() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function shiftDate(dateStr, delta) {
    const d = new Date(dateStr + 'T00:00:00');
    d.setDate(d.getDate() + delta);
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  async function loadDates() {
    try {
      const res = await fetch('/api/plan/dates');
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) throw new Error((data && data.error) || '加载失败');
      state.dates = data.dates || [];
    } catch (err) {
      toast('日期列表加载失败：' + (err.message || err));
    }
  }

  async function loadPlan(date) {
    els.planContent.innerHTML = '<div class="plan-empty">加载中…</div>';
    try {
      const res = await fetch('/api/plan?date=' + encodeURIComponent(date));
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) throw new Error((data && data.error) || '未找到该日预案');
      state.data = data;
      render();
    } catch (err) {
      state.data = null;
      els.planContent.innerHTML = '<div class="plan-empty">' + esc(err.message || '加载失败') + '</div>';
      els.planStatus.textContent = date;
      els.planDate.textContent = date;
      toast('加载失败：' + (err.message || err));
    }
  }

  function render() {
    const d = state.data;
    els.planDate.textContent = d.date;
    els.planStatus.textContent = d.date + ' 早盘预案';
    const lines = (d.text || '').split('\n');
    // 保留空行，逐行转义（不折叠换行，保持手写格式）
    els.planContent.innerHTML = lines.map(line => {
      if (!line.trim()) return '<div class="plan-blank"></div>';
      // 一级标题（早盘预案）
      if (/^\d{4}年.*早盘预案$/.test(line.trim())) {
        return '<h1 class="plan-title">' + esc(line.trim()) + '</h1>';
      }
      // 二级标题（大局观/具体机会解析/总结）
      const section = line.trim();
      if (section === '大局观' || section === '具体机会解析' || section === '总结') {
        return '<h2 class="plan-section">' + esc(section) + '</h2>';
      }
      // 三级标题（短线方面/题材方面/其他对流/指数/情绪/题材/消息/短线/机器人等）
      if (/^(短线方面|题材方面|其他对流)$/.test(section)) {
        return '<h3 class="plan-sub">' + esc(section) + '</h3>';
      }
      return '<div class="plan-line">' + highlight(line) + '</div>';
    }).join('');
  }

  // 对一行预案文本做关键信息高亮：股票名/连板数/大盘点位/涨跌/关键位
  // 用控制字符 token 占位，避免多遍正则互相覆盖，最后统一还原为 span
  function highlight(text) {
    const safe = esc(text);
    const tokens = [];
    const S = '', E = '';
    function push(type, cls, content) {
      tokens.push('<span class="' + cls + '">' + content + '</span>');
      return S + (tokens.length - 1) + E;
    }
    let work = safe;
    // 1. 连板数 N板/N连板（高亮晋级），如 "6板"、"11板"
    work = work.replace(/(\d+)板/g, (m, n) => push('lb', 'pl-lb', n + '板'));
    // 2. 大盘点位：独立的 4 位数字（3934/3927/3943/3902），用于指数与关键位
    work = work.replace(/(?<!\d)(\d{4})(?!\d)/g, (m, n) => push('pt', 'pl-pt', n));
    // 3. 涨跌幅：带 +/- 或 % 的数字（-0.82%、+0.34%、11%）
    work = work.replace(/([+-]\d+(?:\.\d+)?%)/g, (m, n) => push('pct', n.startsWith('-') ? 'pl-down' : 'pl-up', n));
    work = work.replace(/(?<![A-Za-z])(\d+(?:\.\d+)?%)/g, (m, n) => push('pct2', 'pl-pct', n));
    // 4. 价格/关键位：带小数的数字（157.93、26.83、7.82），用于支撑/低吸位
    work = work.replace(/(?<!\d)(\d+\.\d{1,2})(?!\d)/g, (m, n) => push('key', 'pl-key', n));
    // 5. 股票名：常见后缀组合，如 百花医药/秦安股份/哈药股份
    work = work.replace(/([一-龥]{2,8}?(?:股份|医药|药业|科技|实业|食品|发展|集团|新材|电子|生物|能源|银行|证券|传媒|地产|文化|航空|重机|智能|环保|通信|电气|数据|软件|光电|材料|体育|传媒))/g,
      (m, name) => push('name', 'pl-name', name));
    // 还原 token
    return work.replace(new RegExp(S + '(\\d+)' + E, 'g'), (m, i) => tokens[+i]);
  }

  async function load(date) {
    if (!state.dates.length) await loadDates();
    const idx = state.dates.indexOf(date);
    // dates 为倒序（最新在前）：prev=更早(index+1)，next=更新(index-1)
    els.btnPrevDay.disabled = idx < 0 || idx >= state.dates.length - 1;
    els.btnNextDay.disabled = idx <= 0;
    await loadPlan(date);
  }

  function bindEvents() {
    els.btnPrevDay.addEventListener('click', () => {
      const idx = state.dates.indexOf(state.date);
      if (idx >= 0 && idx < state.dates.length - 1) { state.date = state.dates[idx + 1]; load(state.date); }
    });
    els.btnNextDay.addEventListener('click', () => {
      const idx = state.dates.indexOf(state.date);
      if (idx > 0) { state.date = state.dates[idx - 1]; load(state.date); }
    });
  }

  async function init() {
    bindEvents();
    await loadDates();
    // 默认展示最新一份预案；无则显示今天
    const latest = state.dates.length ? state.dates[0] : state.date;
    state.date = latest;
    load(latest);
    refreshIcons();
  }

  init();
})();
