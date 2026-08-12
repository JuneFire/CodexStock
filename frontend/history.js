(() => {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);

  const els = {
    btnBack: $('#btnBack'),
    btnExport: $('#btnExport'),
    historyStatus: $('#historyStatus'),
    dateCount: $('#dateCount'),
    dateList: $('#dateList'),
    dayStats: $('#dayStats'),
    resultCount: $('#resultCount'),
    stockBody: $('#stockBody'),
    emptyState: $('#emptyState'),
    detailPanel: $('#detailPanel'),
    detailContent: $('#detailContent'),
    btnPagePrev: $('#btnPagePrev'),
    btnPageNext: $('#btnPageNext'),
    pageInfo: $('#pageInfo'),
    toast: $('#toast')
  };

  const state = {
    dates: [],
    currentDate: '',
    day: null,
    selectedCode: null,
    stockRows: [],
    page: 1,
    pageSize: 30,
    total: 0,
    hasMore: false
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
  }

  function fmtAmountYuan(v) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (n >= 1e4) return Math.round(n / 1e4) + '万';
    return Math.round(n) + '元';
  }

  function fmtYi(v) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    return (Number(v) / 1e8).toFixed(1) + '亿';
  }

  function fmtPct(v) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  function fmtNum(v, d) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    return Number(v).toFixed(d == null ? 2 : d);
  }

  function colorCls(v) {
    const n = Number(v);
    if (n > 0) return 'up';
    if (n < 0) return 'down';
    return 'flat';
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

  async function loadDates(page) {
    const p = Math.max(page || 1, 1);
    try {
      const res = await fetch('/api/history/dates?page=' + p + '&page_size=' + state.pageSize);
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) {
        state.dates = [];
        state.total = 0;
        state.hasMore = false;
        state.page = 1;
        state.currentDate = '';
        renderPager();
        els.historyStatus.textContent = 'MySQL 未连接';
        els.historyStatus.classList.add('is-stale');
        els.dateList.innerHTML = '<div class="date-empty">数据库未连接，无法读取历史数据</div>';
        els.dateCount.textContent = '0';
        return;
      }
      state.dates = data.dates || [];
      state.total = data.total || 0;
      state.hasMore = !!data.has_more;
      state.page = data.page || p;
      state.pageSize = data.page_size || state.pageSize;
      els.historyStatus.textContent = 'MySQL 已连接';
      els.historyStatus.classList.remove('is-stale');
      els.dateCount.textContent = state.total ? '共 ' + state.total + ' 天' : '0';
      renderDates();
      renderPager();
      if (state.dates.some(d => d.date === state.currentDate)) {
        renderDates();
      } else if (state.dates.length) {
        await selectDate(state.dates[0].date);
      } else {
        state.currentDate = '';
        els.dayStats.innerHTML = '';
        els.stockBody.innerHTML = '';
        els.resultCount.textContent = '0 / 0';
      }
    } catch (err) {
      state.dates = [];
      state.total = 0;
      state.hasMore = false;
      state.page = 1;
      state.currentDate = '';
      renderPager();
      els.historyStatus.textContent = 'MySQL 未连接';
      els.historyStatus.classList.add('is-stale');
      els.dateList.innerHTML = '<div class="date-empty">数据库未连接，无法读取历史数据</div>';
    }
  }

  function renderPager() {
    const totalPages = state.total > 0 ? Math.ceil(state.total / state.pageSize) : 0;
    els.btnPagePrev.disabled = state.page <= 1;
    els.btnPageNext.disabled = !state.hasMore;
    els.pageInfo.textContent = state.total > 0 ? state.page + ' / ' + totalPages : '—';
  }

  function renderDates() {
    els.dateList.innerHTML = state.dates.map(item => {
      const isActive = item.date === state.currentDate;
      return `
        <button class="date-item ${isActive ? 'is-active' : ''}" data-date="${esc(item.date)}" type="button">
          <span>${esc(item.date)}</span>
          <span class="muted">${item.stockCount} 只</span>
        </button>
      `;
    }).join('');
  }

  async function selectDate(date) {
    state.currentDate = date;
    state.selectedCode = null;
    renderDates();
    try {
      const res = await fetch('/api/history/date?date=' + encodeURIComponent(date));
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) throw new Error((data && data.error) || '加载失败');
      state.day = data;
      renderStats();
      renderTable();
      hideDetail();
    } catch (err) {
      toast('历史数据加载失败：' + (err.message || err));
    }
  }

  function renderStats() {
    const day = state.day;
    if (!day) return;
    const m = day.market || {};
    const indices = (m.indices || []).map(idx => `${esc(idx.name)} ${fmtPct(idx.changePct)}`).join(' · ');
    els.dayStats.innerHTML = `
      <div class="stat-card">
        <span class="stat-label">日期</span>
        <strong class="stat-value">${esc(day.date)}</strong>
        <span class="stat-sub">${esc(day.fetchedAt || '')}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">股票池</span>
        <strong class="stat-value">${(day.stocks || []).length}</strong>
        <span class="stat-sub">涨 ${m.up || 0} · 跌 ${m.down || 0} · 平 ${m.flat || 0}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">竞价总额</span>
        <strong class="stat-value">${fmtYi((m.totalAmount || 0) * 1e8)}</strong>
        <span class="stat-sub">竞价涨停 ${m.limitUp || 0}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">指数</span>
        <strong class="stat-value stat-value-sm">${indices || '—'}</strong>
        <span class="stat-sub">${day.auto ? '自动抓取' : '手动抓取'}</span>
      </div>
    `;
  }

  function getFiltered() {
    return state.day ? state.day.stocks : [];
  }

  function renderTable() {
    const rows = getFiltered();
    els.resultCount.textContent = `${rows.length} / ${state.day ? state.day.stocks.length : 0}`;
    els.emptyState.hidden = rows.length > 0;
    if (!rows.length) {
      els.stockBody.innerHTML = '';
      return;
    }
    els.stockBody.innerHTML = rows.map(s => `
      <tr data-code="${esc(s.code)}">
        <td class="num">${s.rank == null ? '—' : s.rank}</td>
        <td><button class="kline-link btn-link" data-code="${esc(s.code)}" type="button">${esc(s.code)}</button></td>
        <td><span class="stock-name">${esc(s.name)}</span><span class="stock-sector">${esc(s.industry)}</span></td>
        <td class="num">${fmtNum(s.price, 2)}</td>
        <td class="num ${colorCls(s.changePct)}">${fmtPct(s.changePct)}</td>
        <td class="num ${s.closePct != null ? colorCls(s.closePct) : ''}">${s.closePct != null ? fmtPct(s.closePct) : '—'}</td>
        <td class="num col-amount">${fmtAmountYuan(s.auctionAmount)}</td>
        <td class="num col-yamount">${fmtAmountYuan(s.yesterdayAmount)}</td>
        <td class="num col-ratio ${colorCls((s.ratioToYesterday || 0) - 10)}">${fmtNum(s.ratioToYesterday, 1)}%</td>
        <td class="num col-strength">${fmtNum(s.amountStrength, 1)}</td>
        <td class="num col-auctionTurnover">${fmtNum(s.auctionTurnover, 2)}%</td>
        <td class="num col-market">${fmtYi(s.floatCap)}</td>
        <td class="num">${fmtNum(s.score, 1)}</td>
        <td><div class="tag-group">${(s.tags || []).slice(0, 2).map(t => `<span class="tag tag">${esc(t)}</span>`).join('')}</div></td>
      </tr>
    `).join('');
  }

  async function loadStockHistory(code) {
    state.selectedCode = code;
    try {
      const res = await fetch('/api/history/stock?code=' + encodeURIComponent(code));
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) throw new Error((data && data.error) || '加载失败');
      state.stockRows = data.rows || [];
      renderDetail();
    } catch (err) {
      toast('个股历史加载失败：' + (err.message || err));
    }
  }

  function renderDetail() {
    if (!state.selectedCode) {
      hideDetail();
      return;
    }
    const rows = state.stockRows;
    const first = rows[0] || {};
    els.detailPanel.hidden = false;
    els.detailContent.innerHTML = `
      <div class="detail-card">
        <div class="detail-head">
          <div class="detail-title">
            <div class="detail-name">
              <h3>${esc(first.name || state.selectedCode)}</h3>
              <span class="detail-code">${esc(state.selectedCode)} · 个股历史</span>
            </div>
            <div class="detail-actions">
              <button class="btn btn-ghost btn-sm" data-action="close" type="button"><i data-lucide="x" aria-hidden="true"></i><span>关闭</span></button>
            </div>
          </div>
        </div>
        <div class="history-stock-table">
          <table class="table table-sm">
            <thead>
              <tr>
                <th>日期</th>
                <th>竞价价</th>
                <th>竞价涨幅</th>
                <th>收盘涨幅</th>
                <th>竞价金额</th>
                <th>占比</th>
                <th>分数</th>
                <th>排名</th>
              </tr>
            </thead>
            <tbody>
              ${rows.map(r => `
                <tr>
                  <td>${esc(r.date)}</td>
                  <td class="num">${fmtNum(r.price, 2)}</td>
                  <td class="num ${colorCls(r.changePct)}">${fmtPct(r.changePct)}</td>
                  <td class="num ${r.closePct != null ? colorCls(r.closePct) : ''}">${r.closePct != null ? fmtPct(r.closePct) : '—'}</td>
                  <td class="num">${fmtAmountYuan(r.auctionAmount)}</td>
                  <td class="num">${fmtNum(r.ratioToYesterday, 1)}%</td>
                  <td class="num">${fmtNum(r.score, 1)}</td>
                  <td class="num">${r.rank == null ? '—' : r.rank}</td>
                </tr>
              `).join('') || '<tr><td colspan="8" class="muted">暂无历史记录</td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    `;
    refreshIcons();
  }

  function hideDetail() {
    els.detailPanel.hidden = true;
    els.detailContent.innerHTML = '';
  }

  async function exportCsv() {
    if (!state.currentDate) {
      toast('请先选择日期');
      return;
    }
    try {
      const res = await fetch('/api/history/export.csv?date=' + encodeURIComponent(state.currentDate));
      if (!res.ok) {
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        throw new Error((data && data.error) || '导出失败');
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'auction_history_' + state.currentDate + '.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 500);
    } catch (err) {
      toast('导出失败：' + (err.message || err));
    }
  }

  function bindEvents() {
    els.btnBack.addEventListener('click', () => { location.href = 'index.html'; });
    els.btnExport.addEventListener('click', exportCsv);
    els.btnPagePrev.addEventListener('click', () => { if (state.page > 1) loadDates(state.page - 1); });
    els.btnPageNext.addEventListener('click', () => { if (state.hasMore) loadDates(state.page + 1); });
    els.dateList.addEventListener('click', e => {
      const btn = e.target.closest('.date-item');
      if (btn) selectDate(btn.getAttribute('data-date'));
    });

    els.stockBody.addEventListener('click', e => {
      const btn = e.target.closest('.btn-link[data-code]');
      if (btn) loadStockHistory(btn.getAttribute('data-code'));
    });
    els.detailContent.addEventListener('click', e => {
      const actionEl = e.target.closest('[data-action]');
      if (!actionEl) return;
      if (actionEl.getAttribute('data-action') === 'close') {
        state.selectedCode = null;
        hideDetail();
      }
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        state.selectedCode = null;
        hideDetail();
      }
    });
  }

  function init() {
    bindEvents();
    loadDates(1);
    refreshIcons();
  }

  init();
})();