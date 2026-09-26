(() => {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);

  const esc = RichText.esc;

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
    RichText.renderInto(d.text, els.planContent);
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
