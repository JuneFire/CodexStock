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
  // 东方财富个股K线页（外部链接）
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
    tacticsStatus: $('#tacticsStatus'),
    tacticsDate: $('#tacticsDate'),
    tacticsBanner: $('#tacticsBanner'),
    tacticsMeta: $('#tacticsMeta'),
    tacticsBody: $('#tacticsBody'),
    tacticsEmpty: $('#tacticsEmpty'),
    otherPanel: $('#otherPanel'),
    otherMeta: $('#otherMeta'),
    otherBody: $('#otherBody'),
    leadMeta: $('#leadMeta'),
    leadBody: $('#leadBody'),
    leadEmpty: $('#leadEmpty'),
    lostWrap: $('#lostWrap'),
    lostBody: $('#lostBody'),
    congMeta: $('#congMeta'),
    congBody: $('#congBody'),
    congEmpty: $('#congEmpty'),
    trackMeta: $('#trackMeta'),
    trackBody: $('#trackBody'),
    trackEmpty: $('#trackEmpty'),
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

  function rowHtml(c, i, showReso) {
    const stageCls = STAGE_CLS[c.stage] || 'tag';
    const reso = c.resonance || {};
    const st = c.strategy || {};
    const lad = st.ladder || {};
    const four = st.fourGate || {};
    let badge = '';
    if (showReso !== false && reso.resonant) {
      badge = '<span class="tag tag-up" title="消息+板块+个股三方共振">三方共振</span>';
    } else if (showReso === false) {
      badge = '<span class="tag" title="非三方共振（板块热度或个股技术面不足）">孤军</span>';
    }
    const hits = (c.hits || []).map(h => `<span class="tag">${esc(h)}</span>`).join('');
    const warns = (c.warns || []).map(w => `<span class="tag tag-down">${esc(w)}</span>`).join('');
    // 手册·换手率阶梯档位
    const ladCls = lad.tone === 'up' ? 'tag-up' : (lad.tone === 'down' ? 'tag-down' : 'tag-warn');
    const ladTxt = lad.grade
      ? `<span class="tag ${ladCls}" title="${esc(lad.action || '')}">${esc(lad.grade)}${lad.ladder ? ' ' + lad.ladder + '档' : ''}</span>`
      : '<span class="tag">—</span>';
    const shrinkTag = lad.shrink
      ? '<span class="tag tag-accent" title="当日换手低于前一日（手册：三板缩量优先）">缩量</span>' : '';
    // 手册·四关结论 + 仓位（结论由后端按阶梯档位分层给出：候选/观察）
    const v = c.verdict || four.verdict || '';
    const vCls = v === '候选' ? 'tag-up' : (v === '回避' ? 'tag-down' : 'tag-warn');
    const blocked = c.blocked || four.blocked || [];
    const manual = four.manualChecks || [];
    const vTip = c.verdictNote || c.riskNote
      || (blocked.length ? '风险提示：' + blocked.join('、')
        : (manual.length ? '需人工复核：' + manual.join('/') : '硬项全过（200日回测中硬项无超额，仅供参考）'));
    const heatTag = c.heat
      ? `<span class="tag ${c.heat === '过热' ? 'tag-down' : 'tag-accent'}" title="池内技术面分位${c.heat === '过热' ? '≥80，回测 T+3 偏负' : '≤40，回测 T+3 偏正'}">${esc(c.heat)}</span>`
      : '';
    const verdictCell = v
      ? `<span class="tag ${vCls}" title="${esc(vTip)}">${esc(v)}</span>
         <span class="dim">${esc(c.position || four.position || '')}</span>${heatTag}`
      : '<span class="tag">—</span>';
    const exitCell = c.exitRule
      ? `<span class="tag tag-accent" title="路线2：盘后复盘选股 → 次日开盘买入 → 最早 D+2 卖出（A股 T+1）。200交易日/15337样本实测 D+2收盘 -0.07%、D+2开盘 -0.30%，无统计 alpha。有 alpha 的是打板口径：D 日涨停价成交 → D+1 开盘卖 +1.37%（换手板）/+2.31%（早板）。">${esc(c.exitRule)}</span>`
      : '<span class="tag">—</span>';
    return `
      <tr>
        <td class="num">${i + 1}</td>
        <td class="stock-code"><a class="stock-link" href="${emUrl(c.code)}" target="_blank" rel="noopener">${esc(c.code)}</a></td>
        <td>${codeLink(c.code, c.name)} <span class="stock-sector">${esc(c.industry)}</span></td>
        <td class="num">${c.lb || 0}板</td>
        <td class="num">${c.sectorZt || 0}只</td>
        <td class="num">${c.latestTurnover != null ? fmtNum(c.latestTurnover, 1) + '%' : '—'}</td>
        <td class="hits-cell">${ladTxt}${shrinkTag}</td>
        <td class="hits-cell">${verdictCell}</td>
        <td class="hits-cell">${exitCell}</td>
        <td><span class="tag ${stageCls}">${esc(c.stage)}</span></td>
        <td class="num">${c.prevVolRatio != null ? fmtNum(c.prevVolRatio, 1) + 'x' : '—'}</td>
        <td class="num">${c.floatCapYi != null ? fmtNum(c.floatCapYi, 0) + '亿' : '—'}</td>
        <td class="num">${c.upside != null ? fmtNum(c.upside * 100, 1) + '%' : '—'}</td>
        <td class="hits-cell">${badge}${hits}${warns}</td>
        <td class="num"><strong>${c.score || 0}</strong></td>
      </tr>`;
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
      <span class="env-advice">扫描${d.scanned || 0}只首板~${d.candLbMax || 3}板 · 剔除超标${d.excludedN || 0}只 · 三方共振${d.resonantN || 0}只 · 入池${(d.candidates || []).length}只 · 候选${(d.verdictN || {}).候选 || 0}/观察${(d.verdictN || {}).观察 || 0} · ${esc((d.savedAt || '').slice(11, 16))}${d.expect != null ? '　·　<b>T+1期望 ' + (d.expect > 0 ? '+' : '') + d.expect + '%</b>（隔日开盘买→D+2收盘卖，无alpha，仅观察）' : ''}</span>`;

    const cands = d.candidates || [];
    els.tacticsMeta.textContent = (d.scanned || 0) + ' 只候选 · 三方共振 ' + (d.resonantN || 0) + ' 只 → 前 ' + cands.length;
    els.tacticsEmpty.hidden = cands.length > 0;
    els.tacticsBody.innerHTML = cands.map((c, i) => rowHtml(c, i)).join('');

    // 孤军（非三方共振）折叠区
    const others = d.others || [];
    els.otherMeta.textContent = others.length + ' 只';
    els.otherPanel.hidden = others.length === 0;
    els.otherBody.innerHTML = others.map((c, i) => rowHtml(c, i, false)).join('');
    renderTracking(d.tracking || []);
    renderLeadership(d.leadership);
    renderCongestion(d.congestion);
    refreshIcons();
  }

  function renderLeadership(data) {
    const promos = (data && data.promotions) || [];
    const lost = (data && data.lostSlots) || [];
    els.leadMeta.textContent = data ? (promos.length + ' 只晋级（卡位 ' + promos.filter(p => p.type === '卡位晋级').length + '）') : '—';
    els.leadEmpty.hidden = promos.length > 0;
    els.leadBody.innerHTML = promos.map(p => `
      <tr>
        <td class="stock-code"><a class="stock-link" href="${emUrl(p.code)}" target="_blank" rel="noopener">${esc(p.code)}</a></td>
        <td>${codeLink(p.code, p.name)} <span class="stock-sector">${esc(p.industry || '')}</span></td>
        <td class="num">${p.prevLb}板</td>
        <td class="num"><strong>${p.todayLb}板</strong></td>
        <td><span class="tag ${p.type === '卡位晋级' ? 'tag-up' : 'tag'}">${esc(p.type)}</span></td>
      </tr>`).join('');
    els.lostWrap.hidden = lost.length === 0;
    els.lostBody.innerHTML = lost.map(l => `
      <tr>
        <td class="stock-code"><a class="stock-link" href="${emUrl(l.code)}" target="_blank" rel="noopener">${esc(l.code)}</a></td>
        <td>${codeLink(l.code, l.name)} <span class="stock-sector">${esc(l.industry || '')}</span></td>
        <td class="num">昨${l.prevLb}板</td>
        <td><span class="tag tag-down">${esc(l.note || '被卡位')}</span></td>
      </tr>`).join('');
  }

  function renderCongestion(data) {
    const cands = (data && data.candidates) || [];
    els.congMeta.textContent = data ? ('扫描 ' + (data.scanned || 0) + ' 只 · 命中 ' + cands.length) : '（待收盘计算）';
    els.congEmpty.hidden = cands.length > 0;
    els.congBody.innerHTML = cands.map(c => `
      <tr>
        <td class="stock-code"><a class="stock-link" href="${emUrl(c.code)}" target="_blank" rel="noopener">${esc(c.code)}</a></td>
        <td>${codeLink(c.code, c.name)} <span class="stock-sector">${esc(c.industry || '')}</span></td>
        <td class="num">${fmtNum(c.volShrink, 2)}</td>
        <td class="num">${fmtNum((c.amplitude || 0) * 100, 1)}%</td>
        <td class="num">${c.nearMa != null ? fmtNum(c.nearMa * 100, 1) + '%' : '—'}</td>
        <td class="num">${c.red ? '红' : '—'}</td>
        <td>${(c.hits || []).map(h => `<span class="tag">${esc(h)}</span>`).join('')}</td>
        <td class="num"><strong>${c.score || 0}</strong></td>
      </tr>`).join('');
  }

  const TRACK_STAGE_CLS = {
    '首板健康': 'tag-up', '抬升': 'tag-warn', '缩量加速': 'tag-accent', '放量兑现': 'tag-down'
  };
  const TRACK_STATUS_CLS = { '蓄势': 'tag', '启动': 'tag-up', '兑现': 'tag-down', '转弱': 'tag-down' };

  function renderTracking(list) {
    els.trackMeta.textContent = list.length + ' 只在跟踪';
    els.trackEmpty.hidden = list.length > 0;
    els.trackBody.innerHTML = list.map(e => {
      const days = e.days || [];
      const last = days[days.length - 1] || {};
      const seq = days.map(d => `<span class="tag ${TRACK_STAGE_CLS[d.stage] || 'tag'}">${esc(d.stage || '—')}</span>`).join('<span class="track-arrow">→</span>');
      const stCls = TRACK_STATUS_CLS[e.status] || 'tag';
      return `
        <tr>
          <td class="stock-code"><a class="stock-link" href="${emUrl(e.code)}" target="_blank" rel="noopener">${esc(e.code)}</a></td>
          <td>${codeLink(e.code, e.name)} <span class="stock-sector">${esc(e.industry || '')}</span></td>
          <td class="num">${esc(e.entryDate || '')}</td>
          <td class="num">${days.length} 天</td>
          <td><span class="tag ${stCls}">${esc(e.status || '')}</span></td>
          <td class="track-seq">${seq || '—'}</td>
          <td class="num">${last.turnover != null ? fmtNum(last.turnover, 1) + '%' : '—'}</td>
        </tr>`;
    }).join('');
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
