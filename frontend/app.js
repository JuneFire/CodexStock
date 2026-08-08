(() => {
  'use strict';

  // ---------- 工具函数 ----------
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
  const round = (v, d) => { const p = Math.pow(10, d); return Math.round((v + Number.EPSILON) * p) / p; };
  const round2 = v => (v == null ? null : round(Number(v), 2));

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

  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function isLimitUp(s) {
    const c = Number(s.changePct || 0);
    return c >= (String(s.code).startsWith('300') || String(s.code).startsWith('301') || String(s.code).startsWith('688') || String(s.code).startsWith('689') ? 19.5 : 9.8);
  }

  // ---------- 评分与标签 ----------
  function normScore(v, cap) {
    if (v == null || !Number.isFinite(Number(v))) return 0;
    return clamp(Number(v), 0, cap) / cap * 100;
  }

  function scoreOf(s) {
    const strength = normScore(s.amountStrength, 100);
    const ratio = normScore(s.ratioToYesterday, 30);
    const turnover = normScore(s.auctionTurnover, 1.5);
    const change = normScore((Number(s.changePct) || 0) + 2, 12);
    const vol = normScore(s.volumeRatio, 8);
    return round(0.30 * strength + 0.30 * ratio + 0.20 * turnover + 0.12 * change + 0.08 * vol, 1);
  }

  function tagsOf(s) {
    const tags = [];
    const ratio = Number(s.ratioToYesterday);
    const strength = Number(s.amountStrength);
    const turnover = Number(s.auctionTurnover);
    if (Number.isFinite(ratio) && Number.isFinite(strength) && ratio >= 15 && strength >= 20) tags.push('超预期');
    if (Number.isFinite(strength) && strength >= 30) tags.push('高金额强度');
    if (Number.isFinite(ratio) && ratio >= 20) tags.push('大幅放量');
    if (Number.isFinite(turnover) && turnover >= 0.8) tags.push('高换手');
    if (Number(s.changePct) >= 3) tags.push('高开');
    return tags.length ? tags : ['常规'];
  }

  const TAG_CLASS = {
    '超预期': 'tag-up',
    '高金额强度': 'tag-warn',
    '大幅放量': 'tag-up',
    '高换手': 'tag-warn',
    '高开': 'tag-accent',
    '常规': 'tag'
  };

  function computeDerived(s) {
    const amount = Number(s.auctionAmount) || 0;
    const floatCap = Number(s.floatCap) || 0;
    const price = Number(s.price) || 0;
    const volume = Number(s.auctionVolume) || 0;
    const yAmount = Number(s.yesterdayAmount) || 0;

    const ratio = yAmount > 0 ? amount / yAmount * 100 : null;
    const strength = floatCap > 0 ? amount / floatCap * 10000 : null;
    const floatShares = price > 0 ? floatCap / price : 0;
    const auctionTurnover = floatShares > 0 ? volume * 100 / floatShares * 100 : null;

    s.ratioToYesterday = round2(ratio);
    s.amountStrength = round2(strength);
    s.auctionTurnover = round2(auctionTurnover);
    s.score = scoreOf(s);
    s.tags = tagsOf(s);
    return s;
  }
  // ---------- 演示数据 ----------
  const SECTOR_DEFS = [
    ['半导体', 10], ['人工智能', 9], ['算力', 8], ['机器人', 8], ['低空经济', 6],
    ['新能源', 9], ['固态电池', 7], ['创新药', 8], ['军工', 7], ['消费', 8],
    ['汽车', 7], ['券商', 6], ['传媒', 6], ['化工', 6], ['有色', 6],
    ['电力', 6], ['通信', 6], ['银行', 5], ['地产', 5]
  ];
  const NAME_PRE = ['华', '中', '天', '国', '金', '科', '泰', '瑞', '恒', '东', '新', '联', '博', '盛', '海', '云', '星', '长', '广', '嘉', '丰', '远', '宏', '信', '创'];
  const NAME_SUF = ['科技', '智能', '股份', '控股', '新材', '数据', '装备', '电子', '光电', '生物'];
  const SECTOR_KW = {
    '半导体': '芯', '人工智能': '智', '算力': '算', '机器人': '机', '低空经济': '飞',
    '新能源': '能', '固态电池': '池', '创新药': '药', '军工': '军', '消费': '消',
    '汽车': '车', '券商': '券', '传媒': '媒', '化工': '化', '有色': '色',
    '电力': '电', '通信': '通', '银行': '银', '地产': '地'
  };

  function sampleChange(rng, board) {
    const r = rng();
    let c;
    if (r < 0.16) c = -0.5 - rng() * 5;
    else if (r < 0.72) c = 0.2 + rng() * 5;
    else if (r < 0.95) c = 5 + rng() * 3.8;
    else c = 8.5 + rng() * 1.4;
    if ((board === 'D' || board === 'E') && rng() < 0.12) c = 12 + rng() * 7.5;
    return round(clamp(c, -6, 19.9), 2);
  }

  function generateName(sector, rng, used) {
    const kw = SECTOR_KW[sector] || '科';
    for (let tries = 0; tries < 30; tries++) {
      const pre = NAME_PRE[Math.floor(rng() * NAME_PRE.length)];
      const suf = NAME_SUF[Math.floor(rng() * NAME_SUF.length)];
      const name = pre + kw + suf;
      if (!used.has(name)) {
        used.add(name);
        return name;
      }
    }
    const fallback = sector + Math.floor(rng() * 900 + 100);
    used.add(fallback);
    return fallback;
  }

  function generateDemoData() {
    const rng = mulberry32((Date.now() & 0x7fffffff) || 12345);
    const used = new Set();
    const stocks = [];
    const counters = { A: 600101, B: 101, C: 201, D: 301, E: 688101 };
    const boards = ['A', 'B', 'C', 'D', 'B', 'E'];

    SECTOR_DEFS.forEach((def, si) => {
      const sector = def[0];
      const count = def[1];
      for (let i = 0; i < count; i++) {
        const board = boards[(si + i) % boards.length];
        const code = String(counters[board]++).padStart(6, '0');
        const name = generateName(sector, rng, used);
        const price = round(4 + Math.pow(rng(), 1.8) * 76, 2);
        const changePct = sampleChange(rng, board);
        const floatCapYi = round(20 + Math.pow(rng(), 2.2) * 1600, 1);
        const floatCap = floatCapYi * 1e8;
        const turnover = round(0.03 + Math.pow(rng(), 1.4) * 2.5, 2);
        let volumeRatio = round(0.4 + Math.pow(rng(), 0.9) * 8, 2);
        if (turnover > 1) volumeRatio = round(volumeRatio * 1.5, 2);
        const floatShares = floatCap / price;
        const auctionVolume = Math.max(1, Math.round(turnover / 100 * floatShares / 100));
        const auctionAmount = Math.round(auctionVolume * 100 * price);
        let ratio = round(2 + Math.pow(rng(), 1.3) * 35, 2);
        if (turnover > 0.8) ratio = Math.min(55, round(ratio * 1.4, 2));
        const yesterdayAmount = Math.round(auctionAmount / ratio * 100);
        const prevClose = round(price / (1 + changePct / 100), 2);

        stocks.push(computeDerived({
          code,
          name,
          industry: sector,
          price,
          changePct,
          open: price,
          prevClose,
          auctionAmount,
          auctionVolume,
          turnover,
          volumeRatio,
          floatCap,
          totalCap: floatCap * (1.15 + rng() * 0.9),
          yesterdayAmount,
          yesterdayTurnover: round(0.5 + rng() * 8, 2),
          yesterdayClose: prevClose
        }));
      }
    });

    stocks.sort((a, b) => (b.score || 0) - (a.score || 0));
    stocks.forEach((s, idx) => { s.rank = idx + 1; });
    return stocks;
  }

  function demoMarket(stocks) {
    const up = stocks.filter(s => Number(s.changePct) > 0).length;
    const down = stocks.filter(s => Number(s.changePct) < 0).length;
    const flat = stocks.length - up - down;
    return {
      indices: [
        { name: '上证指数', changePct: round(0.1 + Math.random() * 0.7, 2) },
        { name: '深证成指', changePct: round(0.1 + Math.random() * 0.8, 2) },
        { name: '创业板指', changePct: round(-0.1 + Math.random() * 1.0, 2) },
        { name: '科创50', changePct: round(-0.1 + Math.random() * 1.1, 2) }
      ],
      up, down, flat,
      limitUp: stocks.filter(isLimitUp).length,
      totalAmount: round(stocks.reduce((sum, s) => sum + (Number(s.auctionAmount) || 0), 0) / 1e8, 2)
    };
  }
  // ---------- 状态 ----------
  const LS_WATCH = 'auction-screener-watchlist-v1';

  function loadWatchlist() {
    try {
      const raw = JSON.parse(localStorage.getItem(LS_WATCH) || '[]');
      return Array.isArray(raw) ? raw : [];
    } catch (e) {
      return [];
    }
  }

  function saveWatchlist() {
    try {
      localStorage.setItem(LS_WATCH, JSON.stringify(state.watchlist));
    } catch (e) { /* ignore */ }
  }

  const state = {
    stocks: [],
    market: null,
    source: '未加载',
    auto: false,
    validForAuction: true,
    lastUpdate: '',
    selectedCode: null,
    search: '',
    watchOnly: false,
    watchlist: loadWatchlist(),
    sort: { key: 'score', dir: 'desc' },
  };

  // ---------- DOM 引用 ----------
  const els = {
    layout: $('#layout'),
    phase: $('#marketPhase'),
    sourceBadge: $('#sourceBadge'),
    autoBadge: $('#autoBadge'),
    btnFetch: $('#btnFetch'),
    btnRefreshDemo: $('#btnRefreshDemo'),
    btnImport: $('#btnImport'),
    btnTemplate: $('#btnTemplate'),
    btnExport: $('#btnExport'),
    fileInput: $('#fileInput'),
    marketTime: $('#marketTime'),
    marketIndices: $('#marketIndices'),
    marketBreadth: $('#marketBreadth'),
    stats: $('#stats'),
    topMeta: $('#topMeta'),
    topList: $('#topList'),
    searchInput: $('#searchInput'),
    watchOnly: $('#watchOnly'),
    resultCount: $('#resultCount'),
    stockBody: $('#stockBody'),
    emptyState: $('#emptyState'),
    detailPanel: $('#detailPanel'),
    detailContent: $('#detailContent'),
    toast: $('#toast')
  };

  // ---------- 渲染 ----------
  function refreshIcons() {
    if (window.lucide) {
      try { window.lucide.createIcons({ attrs: { width: 16, height: 16 } }); } catch (e) { /* ignore */ }
    }
  }

  function updatePhase() {
    const now = new Date();
    const day = now.getDay();
    const hhmm = now.getHours() * 100 + now.getMinutes();
    let text = '';
    if (day === 0 || day === 6) text = '休市 · 演示';
    else if (hhmm >= 915 && hhmm < 925) text = '集合竞价中';
    else if (hhmm >= 925 && hhmm < 930) text = '竞价已结束 · 可抓取';
    else if (hhmm >= 930 && hhmm < 1500) text = '盘中交易';
    else text = '非交易时段';
    els.phase.textContent = text;
  }

  function updateSourceBadge() {
    const isEastmoney = state.source === '东方财富';
    const last = String(state.lastUpdate || '');
    const snapshotDate = last.slice(0, 10);
    const now = new Date();
    const todayStr = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
    const isToday = snapshotDate === todayStr;
    const parts = [state.source];
    if (last) parts.push(last.slice(11, 16));
    if (isEastmoney && snapshotDate && !isToday) parts.push('非今日');
    if (isEastmoney && !state.validForAuction) parts.push('非竞价快照');
    els.sourceBadge.textContent = parts.join(' · ');
    els.sourceBadge.classList.toggle('is-live', isEastmoney && state.validForAuction && isToday);
    els.sourceBadge.classList.toggle('is-stale', isEastmoney && (!state.validForAuction || !isToday));
  }

  function updateAutoBadge() {
    els.autoBadge.classList.toggle('is-live', !!state.auto);
    els.autoBadge.querySelector('span').textContent = state.auto ? '自动快照' : '9:25 自动抓取';
  }

  function renderMarket() {
    const m = state.market;
    if (!m) return;
    els.marketTime.textContent = state.lastUpdate || new Date().toLocaleTimeString('zh-CN', { hour12: false });
    els.marketIndices.innerHTML = (m.indices || []).map(idx => `
      <div class="index-item">
        <span class="index-name">${esc(idx.name)}</span>
        <span class="index-change ${colorCls(idx.changePct)}">${fmtPct(idx.changePct)}</span>
      </div>
    `).join('');
    els.marketBreadth.innerHTML = `
      <span>涨 <b class="up">${m.up || 0}</b></span>
      <span>跌 <b class="down">${m.down || 0}</b></span>
      <span>平 <b>${m.flat || 0}</b></span>
      <span>竞价涨停 <b class="up">${m.limitUp || 0}</b></span>
      <span>竞价总额 <b>${fmtYi((m.totalAmount || 0) * 1e8)}</b></span>
    `;
  }

  function renderStats() {
    const all = state.stocks;
    const filtered = getFiltered();
    const top = [...all].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 10);
    const avgChange = all.length ? all.reduce((s, x) => s + (Number(x.changePct) || 0), 0) / all.length : 0;
    const totalAmount = all.reduce((s, x) => s + (Number(x.auctionAmount) || 0), 0);
    const avgRatio = all.length ? all.reduce((s, x) => s + (Number(x.ratioToYesterday) || 0), 0) / all.length : 0;
    const topScore = top.length ? top.reduce((s, x) => s + (x.score || 0), 0) / top.length : 0;

    els.stats.innerHTML = `
      <div class="stat-card">
        <span class="stat-label">股票池</span>
        <strong class="stat-value">${all.length}</strong>
        <span class="stat-sub">筛选后 ${filtered.length}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">平均竞价涨幅</span>
        <strong class="stat-value ${colorCls(avgChange)}">${fmtPct(avgChange)}</strong>
        <span class="stat-sub">Top10 均分 ${fmtNum(topScore, 1)}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">竞价总额</span>
        <strong class="stat-value">${fmtYi(totalAmount)}</strong>
        <span class="stat-sub">平均竞价占昨日 ${fmtNum(avgRatio, 1)}%</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Top10 平均超预期分</span>
        <strong class="stat-value">${fmtNum(topScore, 1)}</strong>
        <span class="stat-sub">竞价涨停 ${state.market ? state.market.limitUp : 0}</span>
      </div>
    `;
  }

  function renderTop10() {
    const top = [...state.stocks].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 10);
    els.topMeta.textContent = state.source + (state.lastUpdate ? ' · ' + state.lastUpdate : '');
    if (!top.length) {
      els.topList.innerHTML = '<div class="top-empty">暂无数据</div>';
      return;
    }
    els.topList.innerHTML = top.map((s, idx) => {
      const rankCls = idx === 0 ? 'r1' : idx === 1 ? 'r2' : idx === 2 ? 'r3' : '';
      const isSel = s.code === state.selectedCode;
      return `
        <div class="top-row ${isSel ? 'is-selected' : ''}" data-code="${esc(s.code)}" role="button" tabindex="0">
          <span class="top-rank ${rankCls}">${idx + 1}</span>
          <div class="top-identity">
            <div class="top-name"><span>${esc(s.name)}</span><span class="stock-code">${esc(s.code)}</span></div>
            <div class="top-sub">${esc(s.industry)} · 竞价价 ${fmtNum(s.price, 2)}</div>
          </div>
          <div class="top-metric">
            <span class="top-metric-label">竞价金额</span>
            <span class="top-metric-value">${fmtAmountYuan(s.auctionAmount)}</span>
          </div>
          <div class="top-metric tm-yamount">
            <span class="top-metric-label">昨日成交额</span>
            <span class="top-metric-value">${fmtAmountYuan(s.yesterdayAmount)}</span>
          </div>
          <div class="top-metric tm-ratio">
            <span class="top-metric-label">竞价占昨日</span>
            <span class="top-metric-value ${colorCls((s.ratioToYesterday || 0) - 10)}">${fmtNum(s.ratioToYesterday, 1)}%</span>
          </div>
          <div class="top-metric tm-strength">
            <span class="top-metric-label">金额强度 bp</span>
            <span class="top-metric-value">${fmtNum(s.amountStrength, 1)}</span>
          </div>
          <div class="top-metric tm-turnover">
            <span class="top-metric-label">竞价换手</span>
            <span class="top-metric-value">${fmtNum(s.auctionTurnover, 2)}%</span>
          </div>
          <div class="top-score">
            <span class="score-track"><span class="score-fill ${s.score >= 80 ? 'high' : ''}" style="width:${clamp(s.score || 0, 0, 100)}%"></span></span>
            <strong>${fmtNum(s.score, 1)}</strong>
          </div>
        </div>
      `;
    }).join('');
  }
  function getFiltered() {
    const q = state.search.trim().toLowerCase();
    const out = state.stocks.filter(s => {
      if (state.watchOnly && !state.watchlist.includes(s.code)) return false;
      if (q) {
        const hay = (s.code + ' ' + s.name + ' ' + s.industry).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    return sortStocks(out);
  }

  function sortStocks(list) {
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

  function renderTable() {
    const rows = getFiltered();
    els.resultCount.textContent = `${rows.length} / ${state.stocks.length}`;
    els.emptyState.hidden = rows.length > 0;
    if (!rows.length) {
      els.stockBody.innerHTML = '';
      renderSortArrows();
      return;
    }

    els.stockBody.innerHTML = rows.map(s => {
      const watched = state.watchlist.includes(s.code);
      const selected = s.code === state.selectedCode;
      const tags = (s.tags || []).slice(0, 2);
      return `
        <tr class="${selected ? 'is-selected' : ''}" data-code="${esc(s.code)}">
          <td>
            <button class="star-btn ${watched ? 'is-active' : ''}" data-action="watch" data-code="${esc(s.code)}" aria-label="${watched ? '取消自选' : '加入自选'}">
              <i data-lucide="star" aria-hidden="true"></i>
            </button>
          </td>
          <td class="stock-code">${esc(s.code)}</td>
          <td>
            <span class="stock-name">${esc(s.name)}</span>
            <span class="stock-sector">${esc(s.industry)}</span>
          </td>
          <td class="num">${fmtNum(s.price, 2)}</td>
          <td class="num ${colorCls(s.changePct)}">${fmtPct(s.changePct)}</td>
          <td class="num col-amount">${fmtAmountYuan(s.auctionAmount)}</td>
          <td class="num col-yamount">${fmtAmountYuan(s.yesterdayAmount)}</td>
          <td class="num col-ratio ${colorCls((s.ratioToYesterday || 0) - 10)}">${fmtNum(s.ratioToYesterday, 1)}%</td>
          <td class="num col-strength">${fmtNum(s.amountStrength, 1)}</td>
          <td class="num col-auctionTurnover">${fmtNum(s.auctionTurnover, 2)}%</td>
          <td class="num col-vol">${fmtNum(s.volumeRatio, 2)}</td>
          <td class="num col-market">${fmtYi(s.floatCap)}</td>
          <td>
            <div class="score-cell">
              <span class="score-track"><span class="score-fill ${s.score >= 80 ? 'high' : ''}" style="width:${clamp(s.score || 0, 0, 100)}%"></span></span>
              <span class="num">${fmtNum(s.score, 1)}</span>
            </div>
          </td>
          <td>
            <div class="tag-group">${tags.map(t => `<span class="tag ${TAG_CLASS[t] || 'tag'}">${esc(t)}</span>`).join('')}</div>
          </td>
        </tr>
      `;
    }).join('');
    renderSortArrows();
    refreshIcons();
  }

  function renderSortArrows() {
    $$('th.sortable', $('#stockTable')).forEach(th => {
      const key = th.getAttribute('data-key');
      let arrow = '';
      if (key === state.sort.key) arrow = state.sort.dir === 'desc' ? '↓' : '↑';
      th.innerHTML = th.textContent.trim() + `<span class="sort-icon">${arrow}</span>`;
    });
  }



  function renderDetail() {
    const s = state.stocks.find(x => x.code === state.selectedCode);
    if (!s) {
      els.detailPanel.hidden = true;
      els.layout.classList.remove('has-detail');
      return;
    }
    els.detailPanel.hidden = false;
    els.layout.classList.add('has-detail');
    const watched = state.watchlist.includes(s.code);
    const tags = s.tags || [];
    const metrics = [
      ['竞价金额', fmtAmountYuan(s.auctionAmount)],
      ['昨日成交额', fmtAmountYuan(s.yesterdayAmount)],
      ['竞价占昨日', fmtNum(s.ratioToYesterday, 1) + '%'],
      ['金额强度', fmtNum(s.amountStrength, 1) + ' bp'],
      ['竞价换手', fmtNum(s.auctionTurnover, 2) + '%'],
      ['量比', fmtNum(s.volumeRatio, 2)]
    ];
    els.detailContent.innerHTML = `
      <div class="detail-card">
        <div class="detail-head">
          <div class="detail-title">
            <div class="detail-name">
              <h3>${esc(s.name)}</h3>
              <span class="detail-code">${esc(s.code)} · ${esc(s.industry)}</span>
            </div>
            <div class="detail-price">
              <strong class="${colorCls(s.changePct)}">${fmtNum(s.price, 2)}</strong>
              <span class="detail-change ${colorCls(s.changePct)}">${fmtPct(s.changePct)}</span>
            </div>
            <div class="detail-tags">${tags.map(t => `<span class="tag ${TAG_CLASS[t] || 'tag'}">${esc(t)}</span>`).join('')}</div>
          </div>
          <div class="detail-actions">
            <button class="star-btn ${watched ? 'is-active' : ''}" data-action="watch" data-code="${esc(s.code)}" aria-label="${watched ? '取消自选' : '加入自选'}">
              <i data-lucide="star" aria-hidden="true"></i>
            </button>
            <button class="btn btn-ghost btn-sm" data-action="close" type="button"><i data-lucide="x" aria-hidden="true"></i><span>关闭</span></button>
          </div>
        </div>
        <div class="score-block">
          <div class="score-row">
            <span class="metric-label">超预期分</span>
            <span class="score-track"><span class="score-fill ${s.score >= 80 ? 'high' : ''}" style="width:${clamp(s.score || 0, 0, 100)}%"></span></span>
            <strong class="score-num">${fmtNum(s.score, 1)}</strong>
          </div>
        </div>
        <div class="detail-metrics">
          ${metrics.map(m => `
            <div class="metric">
              <div class="metric-label">${esc(m[0])}</div>
              <div class="metric-value">${esc(m[1])}</div>
            </div>
          `).join('')}
        </div>
        <div class="detail-bars" id="compareBars"></div>
      </div>
    `;
    drawCompareBars(s, $('#compareBars'));
    refreshIcons();
  }

  function drawCompareBars(s, root) {
    if (!root) return;
    const y = Number(s.yesterdayAmount) || 0;
    const a = Number(s.auctionAmount) || 0;
    const max = Math.max(y, a, 1);
    const ratio = Number(s.ratioToYesterday) || 0;
    const ratioWidth = clamp(ratio / 50 * 100, 0, 100);
    root.innerHTML = `
      <div class="bar-row">
        <span class="bar-label">昨日成交额</span>
        <span class="bar-track"><span class="bar-fill bar-yesterday" style="width:${(y / max * 100).toFixed(1)}%"></span></span>
        <span class="bar-value">${fmtAmountYuan(y)}</span>
      </div>
      <div class="bar-row">
        <span class="bar-label">今日竞价</span>
        <span class="bar-track"><span class="bar-fill bar-auction" style="width:${(a / max * 100).toFixed(1)}%"></span></span>
        <span class="bar-value">${fmtAmountYuan(a)}</span>
      </div>
      <div class="bar-row">
        <span class="bar-label">竞价占昨日</span>
        <span class="bar-track"><span class="bar-fill bar-auction" style="width:${ratioWidth.toFixed(1)}%"></span></span>
        <span class="bar-value">${fmtNum(ratio, 1)}%</span>
      </div>
    `;
  }

  function renderAll() {
    updatePhase();
    updateSourceBadge();
    updateAutoBadge();
    renderMarket();
    renderStats();
    renderTop10();
    renderTable();
    renderDetail();
    refreshIcons();
  }

  // ---------- 数据加载 ----------
  function normalizeStock(s) {
    return computeDerived({
      code: String(s.code || ''),
      name: String(s.name || ''),
      industry: String(s.industry || '其他'),
      price: Number(s.price) || null,
      changePct: Number(s.changePct),
      open: s.open == null ? null : Number(s.open),
      prevClose: s.prevClose == null ? null : Number(s.prevClose),
      auctionAmount: Number(s.auctionAmount) || 0,
      auctionVolume: Number(s.auctionVolume) || 0,
      turnover: s.turnover == null ? null : Number(s.turnover),
      volumeRatio: s.volumeRatio == null ? null : Number(s.volumeRatio),
      floatCap: Number(s.floatCap) || 0,
      totalCap: Number(s.totalCap) || 0,
      yesterdayAmount: s.yesterdayAmount == null ? null : Number(s.yesterdayAmount),
      yesterdayTurnover: s.yesterdayTurnover == null ? null : Number(s.yesterdayTurnover),
      yesterdayClose: s.yesterdayClose == null ? null : Number(s.yesterdayClose),
      fetchedAt: s.fetchedAt || ''
    });
  }

  function isAuctionSnapshot(data) {
    if (data.validForAuction === true) return true;
    const m = String(data.fetchedAt || '').match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/);
    if (!m) return false;
    const hm = Number(m[4]) * 60 + Number(m[5]);
    return hm >= 9 * 60 + 25 && hm < 9 * 60 + 30;
  }

  function loadSnapshot(data) {
    state.stocks = (data.stocks || []).map(normalizeStock);
    state.market = data.market || demoMarket(state.stocks);
    state.source = data.source || '东方财富';
    state.auto = !!data.auto;
    state.lastUpdate = data.fetchedAt || data.date || '';
    state.validForAuction = isAuctionSnapshot(data);
    state.selectedCode = null;
    resetFilters(false);
    renderAll();
  }

  function setDemoData() {
    state.stocks = generateDemoData();
    state.market = demoMarket(state.stocks);
    state.source = '演示数据';
    state.auto = false;
    state.validForAuction = true;
    state.lastUpdate = new Date().toLocaleString('zh-CN', { hour12: false });
    state.selectedCode = null;
    resetFilters(false);
    renderAll();
  }

  function loadImportedData(stocks, sourceLabel) {
    state.stocks = stocks;
    state.market = demoMarket(stocks);
    state.source = sourceLabel;
    state.auto = false;
    state.validForAuction = true;
    state.lastUpdate = new Date().toLocaleString('zh-CN', { hour12: false });
    state.selectedCode = null;
    resetFilters(false);
    renderAll();
  }

  async function fetchFromEastmoney() {
    const btn = els.btnFetch;
    const label = $('span', btn);
    btn.disabled = true;
    label.textContent = '抓取中…';
    try {
      const res = await fetch('/api/refresh');
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) throw new Error((data && data.error) || '抓取失败');
      loadSnapshot(data);
      toast(`已抓取 ${data.stocks.length} 只，快照时间 ${data.fetchedAt}`);
    } catch (err) {
      const msg = String(err.message || err);
      toast(/Failed to fetch|fetch/i.test(msg) ? '无法连接本地服务：请先运行 python server.py' : msg);
    } finally {
      btn.disabled = false;
      label.textContent = '抓取竞价数据';
    }
  }

  async function loadLatest() {
    try {
      const res = await fetch('/api/latest');
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.ok && data.stocks && data.stocks.length) {
        loadSnapshot(data);
        toast(state.validForAuction ? '已加载最近一次竞价快照' : '已加载缓存数据（非竞价时段快照）');
      }
    } catch (e) { /* 静态打开时保持演示数据 */ }
  }
  // ---------- 筛选交互 ----------
  function resetFilters(render = true) {
    state.search = '';
    els.searchInput.value = '';
    state.watchOnly = false;
    els.watchOnly.checked = false;
    if (render) renderAll();
  }

  // ---------- 自选 ----------
  function toggleWatch(code) {
    const idx = state.watchlist.indexOf(code);
    if (idx >= 0) state.watchlist.splice(idx, 1);
    else state.watchlist.push(code);
    saveWatchlist();
    renderAll();
  }

  // ---------- CSV 导入导出 ----------
  const CSV_HEADERS = ['代码', '名称', '板块', '竞价价', '竞价涨幅', '竞价金额(元)', '竞价量(手)', '竞价换手(%)', '量比', '流通市值(元)', '昨日成交额(元)', '昨日占比(%)', '金额强度(bp)', '超预期分', '状态'];

  function normalizeKey(s) {
    return String(s).toLowerCase().replace(/[\s（）()%％]/g, '');
  }

  const RAW_ALIASES = {
    code: ['代码', '股票代码', '证券代码', 'code'],
    name: ['名称', '股票名称', '证券简称', 'name'],
    industry: ['板块', '行业', '概念', '所属板块', 'industry'],
    price: ['竞价价', '竞价价格', '竞价', 'price'],
    changePct: ['竞价涨幅', '涨幅', '竞价涨跌幅', '涨跌幅', 'changePct'],
    auctionAmount: ['竞价金额', '竞价成交额', '竞价额', 'auctionAmount'],
    auctionVolume: ['竞价量', '竞价成交量', 'auctionVolume'],
    turnover: ['竞价换手', '竞价换手率', 'auctionTurnover'],
    volumeRatio: ['量比', 'volumeRatio'],
    floatCap: ['流通市值', 'floatCap'],
    yesterdayAmount: ['昨日成交额', 'yesterdayAmount'],
    ratioToYesterday: ['昨日占比', '竞价占比', '竞价占昨日', 'ratioToYesterday'],
    amountStrength: ['金额强度', 'amountStrength'],
    yesterdayTurnover: ['昨换手', '昨日换手率', 'yesterdayTurnover']
  };
  const ALIASES = Object.fromEntries(Object.entries(RAW_ALIASES).map(([k, v]) => [k, v.map(normalizeKey)]));

  function findColumn(headers, keys) {
    return headers.findIndex(h => keys.includes(h));
  }

  function parseCSVText(text) {
    const rows = [];
    let row = [], cur = '', inQ = false;
    text = String(text).replace(/^\uFEFF/, '');
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (inQ) {
        if (ch === '"') {
          if (text[i + 1] === '"') { cur += '"'; i++; }
          else inQ = false;
        } else cur += ch;
      } else if (ch === '"') {
        inQ = true;
      } else if (ch === ',') {
        row.push(cur); cur = '';
      } else if (ch === '\n' || ch === '\r') {
        if (ch === '\r' && text[i + 1] === '\n') i++;
        row.push(cur); rows.push(row); row = []; cur = '';
      } else {
        cur += ch;
      }
    }
    if (cur.length || row.length) { row.push(cur); rows.push(row); }
    return rows.filter(r => r.some(c => String(c).trim() !== ''));
  }

  function parseNum(v, mode) {
    if (v == null) return null;
    let s = String(v).trim().replace(/,/g, '');
    if (!s || s === '-' || s === '--') return null;
    let mult = 1;
    if (s.includes('亿')) {
      mult = mode === 'yuan' ? 1e8 : mode === 'wan' ? 10000 : 1;
      s = s.replace(/亿/g, '');
    } else if (s.includes('万')) {
      mult = mode === 'yuan' ? 1e4 : mode === 'wan' ? 1 : 0.0001;
      s = s.replace(/万/g, '');
    }
    s = s.replace(/[%％]/g, '');
    const n = parseFloat(s);
    return Number.isFinite(n) ? round(n * mult, 6) : null;
  }

  function importCSV(text) {
    const rows = parseCSVText(text);
    if (rows.length < 2) throw new Error('CSV 内容为空');
    const headers = rows[0].map(normalizeKey);
    const cols = {};
    Object.keys(ALIASES).forEach(k => { cols[k] = findColumn(headers, ALIASES[k]); });
    if (cols.code < 0) throw new Error('缺少代码列');

    const stocks = [];
    for (let i = 1; i < rows.length; i++) {
      const row = rows[i];
      const get = k => (cols[k] >= 0 ? row[cols[k]] : null);
      const code = String(get('code') || '').replace(/[^0-9]/g, '').padStart(6, '0');
      if (!code || code.length !== 6) continue;
      const price = parseNum(get('price'), 'plain');
      const changePct = parseNum(get('changePct'), 'plain');
      const auctionAmount = parseNum(get('auctionAmount'), 'yuan');
      const auctionVolume = parseNum(get('auctionVolume'), 'plain');
      const turnover = parseNum(get('turnover'), 'plain');
      const volumeRatio = parseNum(get('volumeRatio'), 'plain');
      const floatCap = parseNum(get('floatCap'), 'yuan');
      const yesterdayAmount = parseNum(get('yesterdayAmount'), 'yuan');
      const ratio = parseNum(get('ratioToYesterday'), 'plain');
      const strength = parseNum(get('amountStrength'), 'plain');
      const yesterdayTurnover = parseNum(get('yesterdayTurnover'), 'plain');
      const name = String(get('name') || code).trim();
      const industry = String(get('industry') || '其他').trim();
      const prevClose = price && changePct != null ? round(price / (1 + changePct / 100), 2) : null;
      const stock = {
        code, name, industry,
        price, changePct, open: price, prevClose,
        auctionAmount: auctionAmount || 0,
        auctionVolume: auctionVolume || 0,
        turnover, volumeRatio,
        floatCap: floatCap || 0,
        totalCap: floatCap || 0,
        yesterdayAmount, yesterdayTurnover,
        yesterdayClose: prevClose,
        ratioToYesterday: ratio,
        amountStrength: strength
      };
      stocks.push(computeDerived(stock));
    }
    if (!stocks.length) throw new Error('没有解析到有效股票');
    return stocks;
  }

  function downloadCSV(filename, text) {
    const blob = new Blob(['\uFEFF' + text], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 500);
  }

  function downloadTemplate() {
    const sample = state.stocks[0] || {
      code: '600000', name: '示例股份', industry: '银行',
      price: 10.2, changePct: 3.4, auctionAmount: 52000000,
      auctionVolume: 50980, auctionTurnover: 0.6, volumeRatio: 2.8,
      floatCap: 1.8e10, yesterdayAmount: 6.5e8, ratioToYesterday: 8,
      amountStrength: 28.9, score: 78.5, tags: ['高开']
    };
    const row = [
      sample.code, sample.name, sample.industry, sample.price, sample.changePct,
      sample.auctionAmount, sample.auctionVolume, sample.auctionTurnover, sample.volumeRatio,
      sample.floatCap, sample.yesterdayAmount, sample.ratioToYesterday, sample.amountStrength,
      sample.score, (sample.tags || []).join('|')
    ].join(',');
    downloadCSV('竞价选股器模板.csv', CSV_HEADERS.join(',') + '\n' + row);
    toast('模板已下载');
  }

  async function handleFile(file) {
    try {
      const buf = await file.arrayBuffer();
      let text;
      try {
        text = new TextDecoder('utf-8', { fatal: true }).decode(buf);
      } catch (e) {
        text = new TextDecoder('gbk').decode(buf);
      }
      const stocks = importCSV(text);
      loadImportedData(stocks, '导入CSV');
      toast(`已导入 ${stocks.length} 只股票`);
    } catch (err) {
      toast('导入失败：' + (err.message || err));
    } finally {
      els.fileInput.value = '';
    }
  }

  function exportResults() {
    const rows = getFiltered();
    if (!rows.length) {
      toast('当前没有可导出的结果');
      return;
    }
    const lines = rows.map(s => [
      s.code, s.name, s.industry, s.price, s.changePct,
      s.auctionAmount, s.auctionVolume, s.auctionTurnover, s.volumeRatio,
      s.floatCap, s.yesterdayAmount, s.ratioToYesterday, s.amountStrength,
      s.score, (s.tags || []).join('|')
    ].join(','));
    const stamp = new Date().toISOString().slice(0, 10);
    downloadCSV(`竞价选股结果_${stamp}.csv`, CSV_HEADERS.join(',') + '\n' + lines.join('\n'));
    toast(`已导出 ${rows.length} 行`);
  }

  // ---------- Toast ----------
  let toastTimer = null;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { els.toast.hidden = true; }, 2800);
  }

  // ---------- 事件绑定 ----------
  function bindEvents() {
    els.btnFetch.addEventListener('click', fetchFromEastmoney);
    els.btnRefreshDemo.addEventListener('click', setDemoData);
    els.btnImport.addEventListener('click', () => els.fileInput.click());
    els.fileInput.addEventListener('change', () => {
      if (els.fileInput.files && els.fileInput.files[0]) handleFile(els.fileInput.files[0]);
    });
    els.btnTemplate.addEventListener('click', downloadTemplate);
    els.btnExport.addEventListener('click', exportResults);





    els.searchInput.addEventListener('input', () => {
      state.search = els.searchInput.value;
      renderTable();
      renderStats();
    });

    els.watchOnly.addEventListener('change', () => {
      state.watchOnly = els.watchOnly.checked;
      renderTable();
      renderStats();
    });

    const thead = $('thead', $('#stockTable'));
    thead.addEventListener('click', e => {
      const th = e.target.closest('th.sortable');
      if (!th) return;
      const key = th.getAttribute('data-key');
      if (state.sort.key === key) state.sort.dir = state.sort.dir === 'desc' ? 'asc' : 'desc';
      else state.sort = { key, dir: key === 'code' ? 'asc' : 'desc' };
      renderTable();
    });

    els.stockBody.addEventListener('click', e => {
      const star = e.target.closest('[data-action="watch"]');
      if (star) {
        e.stopPropagation();
        toggleWatch(star.getAttribute('data-code'));
        return;
      }
      const tr = e.target.closest('tr[data-code]');
      if (tr) {
        state.selectedCode = tr.getAttribute('data-code');
        renderTable();
        renderDetail();
      }
    });

    els.topList.addEventListener('click', e => {
      const row = e.target.closest('.top-row[data-code]');
      if (row) {
        state.selectedCode = row.getAttribute('data-code');
        renderTop10();
        renderTable();
        renderDetail();
      }
    });

    els.detailContent.addEventListener('click', e => {
      const actionEl = e.target.closest('[data-action]');
      if (!actionEl) return;
      const action = actionEl.getAttribute('data-action');
      if (action === 'watch') {
        toggleWatch(actionEl.getAttribute('data-code'));
      } else if (action === 'close') {
        state.selectedCode = null;
        renderTable();
        renderDetail();
      }
    });

    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        state.selectedCode = null;
        renderTable();
        renderDetail();
      }
    });

    setInterval(updatePhase, 30000);
  }

  // ---------- 初始化 ----------
  function init() {
    bindEvents();
    setDemoData();
    loadLatest();
  }

  init();
})();