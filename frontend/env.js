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
  function fmtPct(v, d) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    return (n > 0 ? '+' : '') + n.toFixed(d == null ? 2 : d) + '%';
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
    envStatus: $('#envStatus'),
    envDate: $('#envDate'),
    envBanner: $('#envBanner'),
    envCards: $('#envCards'),
    sigShort: $('#sigShort'),
    sigMarket: $('#sigMarket'),
    sigSector: $('#sigSector'),
    envHistory: $('#envHistory'),
    manualChoices: $('#manualChoices'),
    manualNote: $('#manualNote'),
    btnPrevDay: $('#btnPrevDay'),
    btnNextDay: $('#btnNextDay'),
    btnCalc: $('#btnCalc'),
    btnSaveManual: $('#btnSaveManual'),
    btnClearManual: $('#btnClearManual'),
    toast: $('#toast')
  };

  const state = { date: '', dates: [], data: null };

  const ENV_DEFS = [
    { key: 'trend', name: '趋势票赚钱', desc: '慢牛/有主线，趋势低吸好做' },
    { key: 'short', name: '短线赚钱', desc: '连板/打板/低吸有肉' },
    { key: 'group', name: '抱团赚钱', desc: '资金抱团少数核心' },
    { key: 'chaos', name: '混乱/轮动', desc: '电风扇，不赚钱，防守' }
  ];
  const TONE_CLS = { pos: 'env-pos', warn: 'env-warn', neg: 'env-neg', ice: 'env-ice' };
  const NAME_TO_KEY = { '趋势票赚钱': 'trend', '短线赚钱': 'short', '抱团赚钱': 'group', '混乱/轮动': 'chaos' };
  const TONE_OF_KEY = { trend: 'pos', short: 'warn', group: 'warn', chaos: 'neg' };

  async function loadDates() {
    try {
      const r = await fetch('/api/env/dates');
      const d = await r.json();
      state.dates = (d && d.dates) || [];
    } catch (e) { state.dates = []; }
  }

  async function loadEnv(date, refresh) {
    try {
      const url = '/api/env?date=' + encodeURIComponent(date) + (refresh ? '&refresh=1' : '');
      const r = await fetch(url);
      const d = await r.json();
      if (!d || !d.ok) throw new Error((d && d.error) || '加载失败');
      state.data = d;
      state.date = d.date;
      render();
    } catch (err) {
      state.data = null;
      els.envBanner.hidden = false;
      els.envBanner.className = 'env-banner env-ice';
      els.envBanner.innerHTML = '<span class="env-dot"></span><span class="env-label"><b>暂无环境数据</b></span><span class="env-advice">' + esc(err.message || '') + '</span>';
      els.envCards.innerHTML = '';
      els.envStatus.textContent = date;
      els.envDate.textContent = date;
      toast('加载失败：' + (err.message || err));
    }
  }

  function effectiveConclusion() {
    const c = (state.data && state.data.conclusion) || {};
    const m = state.data && state.data.manual;
    if (m && m.state) {
      const key = NAME_TO_KEY[m.state] || m.state;
      return { state: m.state, key, label: m.label || m.state, tone: m.tone || c.tone, advice: m.advice || '', manual: true };
    }
    return { state: c.state, key: c.top || '', label: c.label, tone: c.tone, advice: c.advice, manual: false };
  }

  function render() {
    const d = state.data;
    if (!d) return;
    els.envDate.textContent = d.date;
    const scores = d.scores || {};
    const concl = effectiveConclusion();
    els.envStatus.textContent = (concl.manual ? '人工判定 · ' : '') + (concl.label || '—');

    // 结论横幅
    els.envBanner.hidden = false;
    els.envBanner.className = 'env-banner ' + (TONE_CLS[concl.tone] || 'env-warn');
    els.envBanner.innerHTML = `
      <span class="env-dot"></span>
      <span class="env-label"><b>${esc(concl.label || '—')}</b>${concl.manual ? '（人工）' : ''}</span>
      <span class="env-advice">${esc(concl.advice || '')}　·　saved ${esc((d.savedAt || '').slice(11, 16))}</span>`;

    // 四张卡
    els.envCards.innerHTML = ENV_DEFS.map(def => {
      const sc = Number(scores[def.key]) || 0;
      const isTop = concl.key === def.key;
      return `
        <div class="env-card ${isTop ? 'env-card-top' : ''}">
          <div class="env-card-head"><span class="env-card-name">${def.name}</span><strong>${fmtNum(sc, 0)}</strong></div>
          <div class="env-card-bar"><span style="width:${Math.max(0, Math.min(100, sc))}%"></span></div>
          <div class="env-card-desc">${def.desc}</div>
        </div>`;
    }).join('');

    renderSignals(d.signals || {});
    renderManual(concl);
    refreshIcons();
  }

  function kv(label, value, cls) {
    return `<div class="env-kv-item"><span class="env-kv-label">${label}</span><span class="env-kv-value ${cls || ''}">${value}</span></div>`;
  }

  function renderSignals(sig) {
    const s1 = sig.S1 || {}, s2 = sig.S2 || {}, s3 = sig.S3 || {}, s4 = sig.S4 || {}, s5 = sig.S5 || {}, s6 = sig.S6 || {};
    els.sigShort.innerHTML =
      kv('涨停/昨', `${s1.zt == null ? '—' : s1.zt} ${s1.ztChangePct == null ? '' : fmtPct(s1.ztChangePct, 0)}`) +
      kv('非一字涨停', s1.ztReal == null ? '—' : s1.ztReal) +
      kv('跌停/炸板', `${s1.dt == null ? '—' : s1.dt} / ${s1.zb == null ? '—' : s1.zb}`) +
      kv('炸板率', s1.zbRate == null ? '—' : fmtNum(s1.zbRate, 1) + '%') +
      kv('连板(2板/3板/3板+)', `${s1.lb2 || 0} / ${s1.lb3 || 0} / ${s1.lb3p || 0}`) +
      kv('最高板', s1.maxTier == null ? '—' : s1.maxTier + '板') +
      kv('连板晋级率', s3.promoteRate == null ? '—' : fmtNum(s3.promoteRate * 100, 1) + '%') +
      kv('首板→2板率', s3.first2Rate == null ? '—' : fmtNum(s3.first2Rate * 100, 1) + '%') +
      kv('昨涨停均溢价', fmtPct(s3.avgPremium)) +
      kv('昨连板均溢价', fmtPct(s3.lbAvgPremium));

    els.sigMarket.innerHTML =
      kv('红/绿盘', `${s2.red == null ? '—' : s2.red} / ${s2.green == null ? '—' : s2.green}`) +
      kv('红盘占比', s2.redRatio == null ? '—' : fmtNum(s2.redRatio * 100, 1) + '%') +
      kv('涨跌中位', fmtPct(s5.medChangePct)) +
      kv('两市量能', s2.amountYi == null ? '—' : fmtNum(s2.amountYi / 10000, 2) + '万亿') +
      kv('量能较昨', fmtPct(s2.amountChgPct, 1)) +
      kv('指数>MA20', `${s6.idxAbove20 == null ? '—' : s6.idxAbove20}/${s6.idxChecked == null ? '—' : s6.idxChecked}`) +
      kv('MA20方向', s6.ma20Up ? '上行' : '走平/下行') +
      kv('头马数', s6.headN == null ? '—' : s6.headN);

    const jac = s4.jaccard == null ? '—' : fmtNum(s4.jaccard, 2);
    els.sigSector.innerHTML =
      kv('今领涨', (s4.topToday || []).join('、') || '—') +
      kv('昨领涨', (s4.topYesterday || []).join('、') || '—') +
      kv('板块重合度(Jaccard)', jac) +
      kv('昨领涨今涨停', s4.prevLeaderTodayZt == null ? '—' : s4.prevLeaderTodayZt + '家') +
      kv('Top10成交占比', s5.top10Share == null ? '—' : fmtNum(s5.top10Share, 1) + '%') +
      kv('Top20成交占比', s5.top20Share == null ? '—' : fmtNum(s5.top20Share, 1) + '%') +
      kv('覆盖行业数', s4.sectorCount == null ? '—' : s4.sectorCount);
  }

  function renderManual(concl) {
    els.manualChoices.innerHTML = ENV_DEFS.map(def => {
      const checked = concl.manual && concl.key === def.key ? 'checked' : '';
      return `<label class="env-radio-item"><input type="radio" name="envmanual" value="${def.name}" ${checked}> ${def.name}</label>`;
    }).join('');
    if (state.data && state.data.manual && state.data.manual.note) {
      els.manualNote.value = state.data.manual.note;
    } else {
      els.manualNote.value = '';
    }
  }

  async function loadHistory() {
    try {
      const r = await fetch('/api/env/history?days=12');
      const d = await r.json();
      const rows = (d && d.rows) || [];
      if (!rows.length) { els.envHistory.innerHTML = '<div class="env-empty">暂无历史</div>'; return; }
      els.envHistory.innerHTML = rows.map(row => {
        const sc = row.scores || {};
        const bar = (k) => `<div class="env-hbar env-hbar-${k}"><span style="width:${Math.max(0, Math.min(100, Number(sc[k]) || 0))}%"></span></div>`;
        const label = (row.manual && row.manual.state) || row.state || '';
        return `<div class="env-hrow" title="${esc(row.date)} ${esc(label)}">
          <span class="env-hdate">${esc(row.date.slice(5))}</span>
          <div class="env-hbars">${bar('trend')}${bar('short')}${bar('group')}${bar('chaos')}</div>
          <span class="env-hlabel">${esc(label)}</span>
        </div>`;
      }).join('');
    } catch (e) {
      els.envHistory.innerHTML = '<div class="env-empty">历史加载失败</div>';
    }
  }

  async function saveManual() {
    const picked = document.querySelector('input[name="envmanual"]:checked');
    if (!picked) { toast('请先选择环境'); return; }
    const stateName = picked.value;
    const def = ENV_DEFS.find(d => d.name === stateName) || {};
    const manual = {
      state: stateName, label: stateName, tone: TONE_OF_KEY[def.key] || 'warn',
      advice: def.desc || '', note: els.manualNote.value || ''
    };
    try {
      const r = await fetch('/api/env', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: state.date, manual })
      });
      const d = await r.json();
      if (!d || !d.ok) throw new Error((d && d.error) || '保存失败');
      toast('已保存人工判定');
      await loadEnv(state.date, false);
    } catch (err) { toast('保存失败：' + (err.message || err)); }
  }

  async function clearManual() {
    try {
      const r = await fetch('/api/env', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: state.date, manual: null })
      });
      const d = await r.json();
      if (!d || !d.ok) throw new Error((d && d.error) || '清除失败');
      toast('已清除覆盖');
      await loadEnv(state.date, false);
    } catch (err) { toast('清除失败：' + (err.message || err)); }
  }

  function bindEvents() {
    els.btnPrevDay.addEventListener('click', () => {
      const i = state.dates.indexOf(state.date);
      if (i >= 0 && i < state.dates.length - 1) loadEnv(state.dates[i + 1], false);
    });
    els.btnNextDay.addEventListener('click', () => {
      const i = state.dates.indexOf(state.date);
      if (i > 0) loadEnv(state.dates[i - 1], false);
    });
    els.btnCalc.addEventListener('click', async () => {
      els.btnCalc.disabled = true;
      toast('计算中…');
      await loadEnv(state.date, true);
      await loadHistory();
      els.btnCalc.disabled = false;
    });
    els.btnSaveManual.addEventListener('click', saveManual);
    els.btnClearManual.addEventListener('click', clearManual);
  }

  async function init() {
    bindEvents();
    await loadDates();
    const latest = state.dates[0] || new Date().toISOString().slice(0, 10);
    state.date = latest;
    await loadEnv(latest, false);
    await loadHistory();
    refreshIcons();
  }

  init();
})();
