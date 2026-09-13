(() => {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
  }
  function fmtNum(v, d) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    return Number(v).toFixed(d == null ? 2 : d);
  }
  function fmtAmount(v) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    if (n >= 1e8) return (n / 1e8).toFixed(1) + '亿';
    if (n >= 1e4) return Math.round(n / 1e4) + '万';
    return Math.round(n) + '';
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
    tacticsStatus: $('#tacticsStatus'),
    tacticsDate: $('#tacticsDate'),
    tacticsBanner: $('#tacticsBanner'),
    tacticsMeta: $('#tacticsMeta'),
    tacticsBody: $('#tacticsBody'),
    tacticsEmpty: $('#tacticsEmpty'),
    btnPrevDay: $('#btnPrevDay'),
    btnNextDay: $('#btnNextDay'),
    toast: $('#toast')
  };

  const state = { date: '', dates: [], data: null };

  const STAGE_CLS = {
    '首板健康': 'tag-up', '抬升': 'tag-warn', '缩量加速': 'tag-accent', '放量兑现': 'tag-down'
  };

  async function loadDates() {
    try {
      const r = await fetch('/api/tactics/dates');
      const d = await r.json();
      state.dates = (d && d.dates) || [];
    } catch (e) { state.dates = []; }
  }

  async function loadTactics(date) {
    try {
      const r = await fetch('/api/tactics?date=' + encodeURIComponent(date));
      const d = await r.json();
      if (!d || !d.ok) throw new Error((d && d.error) || '加载失败');
      state.data = d;
      state.date = d.date;
      render();
    } catch (err) {
      state.data = null;
      els.tacticsBanner.hidden = false;
      els.tacticsBanner.className = 'env-banner env-ice';
      els.tacticsBanner.innerHTML = '<span class="env-dot"></span><span class="env-label"><b>暂无战法数据</b></span><span class="env-advice">' + esc(err.message || '') + '</span>';
      els.tacticsBody.innerHTML = '';
      els.tacticsEmpty.hidden = false;
      els.tacticsDate.textContent = date;
      els.tacticsStatus.textContent = date;
    }
  }

  function render() {
    const d = state.data;
    if (!d) return;
    els.tacticsDate.textContent = d.date;
    const mkt = d.market || {};
    els.tacticsStatus.textContent = d.date + ' · 扫描' + (d.scanned || 0) + '只';
    // 顶部市场环境
    els.tacticsBanner.hidden = false;
    const toneMap = { '上升': 'env-pos', '退潮': 'env-neg', '冰点': 'env-ice', '分歧': 'env-warn' };
    const cls = toneMap[mkt.envState] || 'env-warn';
    els.tacticsBanner.className = 'env-banner ' + cls;
    els.tacticsBanner.innerHTML = `
      <span class="env-dot"></span>
      <span class="env-label"><b>市场环境：${esc(mkt.envLabel || mkt.envState || '—')}</b></span>
      <span class="env-advice">扫描${d.scanned || 0}只首板/2板 · 入选${(d.candidates || []).length}只 · ${esc((d.savedAt || '').slice(11, 16))}</span>`;

    const cands = d.candidates || [];
    els.tacticsMeta.textContent = (d.scanned || 0) + ' 只候选 → 前 ' + cands.length + ' 名';
    els.tacticsEmpty.hidden = cands.length > 0;
    els.tacticsBody.innerHTML = cands.map((c, i) => {
      const stageCls = STAGE_CLS[c.stage] || 'tag';
      const hits = (c.hits || []).map(h => `<span class="tag">${esc(h)}</span>`).join('');
      const warns = (c.warns || []).map(w => `<span class="tag tag-down">${esc(w)}</span>`).join('');
      return `
        <tr>
          <td class="num">${i + 1}</td>
          <td class="stock-code">${esc(c.code)}</td>
          <td><span class="stock-name">${esc(c.name)}</span> <span class="stock-sector">${esc(c.industry)}</span></td>
          <td class="num">${c.lb || 0}板</td>
          <td class="num">${c.sectorZt || 0}只</td>
          <td class="num">${c.latestTurnover != null ? fmtNum(c.latestTurnover, 1) + '%' : '—'}</td>
          <td><span class="tag ${stageCls}">${esc(c.stage)}</span></td>
          <td class="num">${c.prevVolRatio != null ? fmtNum(c.prevVolRatio, 1) + 'x' : '—'}</td>
          <td class="num">${c.floatCapYi != null ? fmtNum(c.floatCapYi, 0) + '亿' : '—'}</td>
          <td class="num">${c.upside != null ? fmtNum(c.upside * 100, 1) + '%' : '—'}</td>
          <td>${hits}${warns}</td>
          <td class="num"><strong>${c.score || 0}</strong></td>
        </tr>`;
    }).join('');
    refreshIcons();
  }

  function bindEvents() {
    els.btnPrevDay.addEventListener('click', () => {
      const i = state.dates.indexOf(state.date);
      if (i >= 0 && i < state.dates.length - 1) loadTactics(state.dates[i + 1]);
    });
    els.btnNextDay.addEventListener('click', () => {
      const i = state.dates.indexOf(state.date);
      if (i > 0) loadTactics(state.dates[i - 1]);
    });
  }

  async function init() {
    bindEvents();
    await loadDates();
    const latest = state.dates[0] || new Date().toISOString().slice(0, 10);
    state.date = latest;
    await loadTactics(latest);
    refreshIcons();
  }

  init();
})();
