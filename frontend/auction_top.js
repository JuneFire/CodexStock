(() => {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

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
  function emUrl(code) {
    const c = String(code || '').padStart(6, '0');
    const pre = c.startsWith('6') ? 'sh' : (c.startsWith('4') || c.startsWith('8') ? 'bj' : 'sz');
    return 'https://quote.eastmoney.com/' + pre + c + '.html';
  }
  function codeLink(code, name) {
    return `<a class="stock-link" href="${emUrl(code)}" target="_blank" rel="noopener">${esc(name || code)}</a>`;
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
    topStatus: $('#topStatus'),
    topDate: $('#topDate'),
    topBanner: $('#topBanner'),
    topMeta: $('#topMeta'),
    topBody: $('#topBody'),
    topTable: $('#topTable'),
    topEmpty: $('#topEmpty'),
    btnPrevDay: $('#btnPrevDay'),
    btnNextDay: $('#btnNextDay'),
    toast: $('#toast')
  };

  const LS_COLS = 'auction-top-cols-v1';
  const DEFAULT_COL_ORDER = ['rank', 'code', 'name', 'price', 'changePct', 'auctionAmount', 'auctionVolume', 'sealAmount', 'volumeRatio', 'turnover', 'floatCap'];

  function loadColOrder() {
    try {
      const arr = JSON.parse(localStorage.getItem(LS_COLS) || '[]');
      if (Array.isArray(arr) && arr.length === DEFAULT_COL_ORDER.length && DEFAULT_COL_ORDER.every(k => arr.includes(k))) {
        return arr;
      }
    } catch (e) { /* ignore */ }
    return DEFAULT_COL_ORDER.slice();
  }

  function saveColOrder(order) {
    try { localStorage.setItem(LS_COLS, JSON.stringify(order)); } catch (e) { /* ignore */ }
  }

  const state = { date: '', dates: [], rows: [], colOrder: loadColOrder(), sort: { key: 'auctionAmount', dir: 'desc' } };

  async function loadDates() {
    try {
      const r = await fetch('/api/auction_top/dates');
      const d = await r.json();
      state.dates = (d && d.dates || []).map(x => x.date);
    } catch (e) { state.dates = []; }
  }

  async function loadTop(date) {
    els.topBanner.hidden = true;
    try {
      const r = await fetch('/api/auction_top?date=' + encodeURIComponent(date));
      const d = await r.json();
      if (!d || !d.ok) throw new Error((d && d.error) || '加载失败');
      state.rows = d.rows || [];
      state.date = d.date;
      render();
    } catch (err) {
      state.rows = [];
      state.date = date;
      els.topDate.textContent = date;
      els.topStatus.textContent = date;
      els.topMeta.textContent = '—';
      els.topBody.innerHTML = '';
      els.topEmpty.hidden = false;
      els.topBanner.hidden = false;
      els.topBanner.className = 'env-banner env-ice';
      els.topBanner.innerHTML = '<span class="env-dot"></span><span class="env-label"><b>暂无竞价榜数据</b></span><span class="env-advice">' + esc(err.message || '') + '</span>';
    }
  }

  function pctCls(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '';
    if (n > 0) return 'up';
    if (n < 0) return 'down';
    return '';
  }

  function cellHtml(r, key, i) {
    switch (key) {
      case 'rank':
        return `<td class="num">${r.rank || i + 1}</td>`;
      case 'code':
        return `<td class="stock-code"><a class="stock-link" href="${emUrl(r.code)}" target="_blank" rel="noopener">${esc(r.code)}</a></td>`;
      case 'name':
        return `<td>${codeLink(r.code, r.name)} <span class="stock-sector">${esc(r.industry || '')}</span></td>`;
      case 'price':
        return `<td class="num">${fmtNum(r.price, 2)}</td>`;
      case 'changePct':
        return `<td class="num ${pctCls(r.changePct)}">${fmtNum(r.changePct, 2)}%</td>`;
      case 'auctionAmount':
        return `<td class="num">${fmtAmount(r.auctionAmount)}</td>`;
      case 'auctionVolume':
        return `<td class="num">${fmtNum(r.auctionVolume, 0)}</td>`;
      case 'sealAmount':
        return `<td class="num">${r.sealAmount != null ? fmtAmount(r.sealAmount) : '—'}</td>`;
      case 'volumeRatio':
        return `<td class="num">${r.volumeRatio != null ? fmtNum(r.volumeRatio, 1) + '×' : '—'}</td>`;
      case 'turnover':
        return `<td class="num">${r.turnover != null ? fmtNum(r.turnover, 2) + '%' : '—'}</td>`;
      case 'floatCap':
        return `<td class="num">${fmtAmount(r.floatCap)}</td>`;
      default:
        return '<td></td>';
    }
  }

  const SORT_LABELS = {
    code: '代码', name: '名称', price: '竞价价', changePct: '竞价涨幅',
    auctionAmount: '竞价成交额', auctionVolume: '竞价量', sealAmount: '封单额',
    volumeRatio: '竞价量比', turnover: '竞价换手', floatCap: '流通市值'
  };

  function sortRows(list) {
    const { key, dir } = state.sort;
    const sign = dir === 'desc' ? -1 : 1;
    return [...list].sort((a, b) => {
      let av = a[key];
      let bv = b[key];
      if (Array.isArray(av)) av = av.join('|');
      if (Array.isArray(bv)) bv = bv.join('|');
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av || '').localeCompare(String(bv || ''), 'zh') * sign;
      }
      const an = Number(av);
      const bn = Number(bv);
      const avv = Number.isFinite(an) ? an : -Infinity;
      const bvv = Number.isFinite(bn) ? bn : -Infinity;
      return (avv - bvv) * sign;
    });
  }

  function updateSortIcons() {
    els.topTable.querySelectorAll('th.sortable').forEach(th => {
      const key = th.getAttribute('data-key');
      const arrow = (key === state.sort.key) ? (state.sort.dir === 'desc' ? '↓' : '↑') : '';
      const icon = th.querySelector('.sort-icon');
      if (icon) icon.textContent = arrow;
    });
  }

  function applyColOrder() {
    const theadRow = $('thead tr', els.topTable);
    const map = {};
    $$('th[data-key]', theadRow).forEach(th => { map[th.getAttribute('data-key')] = th; });
    state.colOrder.forEach(key => {
      const th = map[key];
      if (th) theadRow.appendChild(th);
    });
  }

  function initColumnDrag() {
    const theadRow = $('thead tr', els.topTable);
    let dragKey = null;

    $$('th[data-key]', theadRow).forEach(th => th.setAttribute('draggable', 'true'));

    theadRow.addEventListener('dragstart', e => {
      const th = e.target.closest('th[data-key]');
      if (!th) return;
      dragKey = th.getAttribute('data-key');
      th.classList.add('is-dragging');
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', dragKey); } catch (err) { /* ignore */ }
      }
    });

    theadRow.addEventListener('dragend', () => {
      $$('th[data-key]', theadRow).forEach(t => t.classList.remove('is-dragging', 'is-drop-target'));
      dragKey = null;
    });

    theadRow.addEventListener('dragover', e => {
      const th = e.target.closest('th[data-key]');
      if (!th || !dragKey) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
      $$('th[data-key]', theadRow).forEach(t => t.classList.toggle('is-drop-target', t === th));
    });

    theadRow.addEventListener('drop', e => {
      const th = e.target.closest('th[data-key]');
      if (!th || !dragKey || th.getAttribute('data-key') === dragKey) return;
      e.preventDefault();
      const dragged = $('th[data-key="' + dragKey + '"]', theadRow);
      if (!dragged) return;
      const rect = th.getBoundingClientRect();
      if (e.clientX > rect.left + rect.width / 2) th.after(dragged);
      else th.before(dragged);
      state.colOrder = $$('th[data-key]', theadRow).map(t => t.getAttribute('data-key'));
      saveColOrder(state.colOrder);
      render();
    });
  }

  function render() {
    const rows = sortRows(state.rows);
    const date = state.date;
    els.topDate.textContent = date;
    els.topStatus.textContent = date + ' · ' + rows.length + ' 只';
    els.topMeta.textContent = '前 ' + rows.length + ' 名 · 按' + (SORT_LABELS[state.sort.key] || '') + (state.sort.dir === 'desc' ? '降序' : '升序');
    els.topEmpty.hidden = rows.length > 0;
    els.topBody.innerHTML = rows.map((r, i) => '<tr>' + state.colOrder.map(k => cellHtml(r, k, i)).join('') + '</tr>').join('');
    updateSortIcons();
    refreshIcons();
  }

  function bindEvents() {
    els.topTable.addEventListener('click', (e) => {
      const th = e.target.closest('th.sortable');
      if (!th) return;
      const key = th.getAttribute('data-key');
      if (state.sort.key === key) state.sort.dir = state.sort.dir === 'desc' ? 'asc' : 'desc';
      else state.sort = { key, dir: key === 'code' ? 'asc' : 'desc' };
      render();
    });
    els.btnPrevDay.addEventListener('click', () => {
      const i = state.dates.indexOf(state.date);
      if (i >= 0 && i < state.dates.length - 1) loadTop(state.dates[i + 1]);
    });
    els.btnNextDay.addEventListener('click', () => {
      const i = state.dates.indexOf(state.date);
      if (i > 0) loadTop(state.dates[i - 1]);
    });
  }

  async function init() {
    bindEvents();
    applyColOrder();
    initColumnDrag();
    await loadDates();
    const latest = state.dates[0] || new Date().toISOString().slice(0, 10);
    state.date = latest;
    await loadTop(latest);
    refreshIcons();
  }

  init();
})();
