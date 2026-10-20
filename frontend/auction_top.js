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

  const state = { date: '', dates: [], rows: [], sort: { key: 'auctionAmount', dir: 'desc' } };

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

  function rowHtml(r, i) {
    const pct = r.changePct;
    return `
      <tr>
        <td class="num">${r.rank || i + 1}</td>
        <td class="stock-code"><a class="stock-link" href="${emUrl(r.code)}" target="_blank" rel="noopener">${esc(r.code)}</a></td>
        <td>${codeLink(r.code, r.name)} <span class="stock-sector">${esc(r.industry || '')}</span></td>
        <td class="num">${fmtNum(r.price, 2)}</td>
        <td class="num ${pctCls(pct)}">${fmtNum(pct, 2)}%</td>
        <td class="num">${fmtAmount(r.auctionAmount)}</td>
        <td class="num">${fmtNum(r.auctionVolume, 0)}</td>
        <td class="num">${r.volumeRatio != null ? fmtNum(r.volumeRatio, 1) + '×' : '—'}</td>
        <td class="num">${r.turnover != null ? fmtNum(r.turnover, 2) + '%' : '—'}</td>
        <td class="num">${fmtAmount(r.floatCap)}</td>
      </tr>`;
  }

  const SORT_LABELS = {
    code: '代码', name: '名称', price: '竞价价', changePct: '竞价涨幅',
    auctionAmount: '竞价成交额', auctionVolume: '竞价量', volumeRatio: '竞价量比',
    turnover: '竞价换手', floatCap: '流通市值'
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

  function render() {
    const rows = sortRows(state.rows);
    const date = state.date;
    els.topDate.textContent = date;
    els.topStatus.textContent = date + ' · ' + rows.length + ' 只';
    els.topMeta.textContent = '前 ' + rows.length + ' 名 · 按' + (SORT_LABELS[state.sort.key] || '') + (state.sort.dir === 'desc' ? '降序' : '升序');
    els.topEmpty.hidden = rows.length > 0;
    els.topBody.innerHTML = rows.map(rowHtml).join('');
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
    await loadDates();
    const latest = state.dates[0] || new Date().toISOString().slice(0, 10);
    state.date = latest;
    await loadTop(latest);
    refreshIcons();
  }

  init();
})();
