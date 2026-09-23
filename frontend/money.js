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
    return Number(v).toFixed(d == null ? 1 : d);
  }
  function fmtPct(v, d) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    return (n > 0 ? '+' : '') + n.toFixed(d == null ? 1 : d) + '%';
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
    moneyStatus: $('#moneyStatus'),
    moneyAlert: $('#moneyAlert'),
    moneyBanner: $('#moneyBanner'),
    moneyChart: $('#moneyChart'),
    moneyHint: $('#moneyHint'),
    moneyMeta: $('#moneyMeta'),
    moneyBody: $('#moneyBody'),
    moneyEmpty: $('#moneyEmpty'),
    daysSel: $('#daysSel'),
    btnBackfill: $('#btnBackfill'),
    toast: $('#toast')
  };

  const state = { rows: [], alerts: [] };

  // 东方财富个股K线页；北交所 920xxx 也要走 bj（否则会拼成错误的 sz 链接）
  function emUrl(code) {
    const c = String(code || '').padStart(6, '0');
    const head = c[0];
    const pre = head === '6' ? 'sh' : (head === '9' || head === '4' || head === '8' ? 'bj' : 'sz');
    return 'https://quote.eastmoney.com/' + pre + c + '.html';
  }

  async function load(days) {
    try {
      const r = await fetch('/api/money?days=' + (days || 30));
      const d = await r.json();
      state.rows = (d && d.rows) || [];
      state.alerts = (d && d.alerts) || [];
      render();
    } catch (e) {
      toast('加载失败：' + (e.message || e));
    }
  }

  // 无依赖 SVG 双折线
  function renderChart(rows) {
    if (!rows.length) { els.moneyChart.innerHTML = '<div class="money-hint">暂无数据</div>'; return; }
    const W = 1000, H = 300, PL = 40, PR = 12, PT = 12, PB = 28;
    const plotW = W - PL - PR, plotH = H - PT - PB;
    const n = rows.length;
    const x = i => PL + (n === 1 ? plotW / 2 : (plotW * i / (n - 1)));
    const y = v => PT + (100 - Math.max(0, Math.min(100, v))) / 100 * plotH;

    const shortPts = [], mktPts = [], losePts = [], dots = [];
    rows.forEach((row, i) => {
      const sx = x(i);
      if (row.short != null) shortPts.push(sx + ',' + y(row.short).toFixed(1));
      if (row.market != null) mktPts.push(sx + ',' + y(row.market).toFixed(1));
      if (row.lose != null) losePts.push(sx + ',' + y(row.lose).toFixed(1));
      dots.push({ x: sx, date: row.date, short: row.short, market: row.market, lose: row.lose });
    });

    // y 轴刻度
    let grid = '';
    [0, 25, 50, 75, 100].forEach(v => {
      const yy = y(v).toFixed(1);
      grid += `<line x1="${PL}" y1="${yy}" x2="${W - PR}" y2="${yy}" class="money-grid${v === 50 ? ' money-mid' : ''}"/>`;
      grid += `<text x="${PL - 6}" y="${(+yy + 3).toFixed(1)}" class="money-ylabel">${v}</text>`;
    });
    // x 轴日期（稀疏）
    const step = Math.max(1, Math.ceil(n / 10));
    let xlabels = '';
    rows.forEach((row, i) => {
      if (i % step !== 0 && i !== n - 1) return;
      const anchor = i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle');
      const lx = i === 0 ? PL : (i === n - 1 ? W - PR : x(i));
      xlabels += `<text x="${lx.toFixed(1)}" y="${H - 8}" class="money-xlabel" text-anchor="${anchor}">${esc(row.date.slice(5))}</text>`;
    });
    // 悬停点
    const hover = dots.map(dt => {
      const anchor = dt.market != null ? dt.market : (dt.short != null ? dt.short : dt.lose);
      if (anchor == null) return '';
      const hv = v => (v == null ? '—' : v);
      return `<circle cx="${dt.x.toFixed(1)}" cy="${y(anchor).toFixed(1)}" r="6" fill="transparent"><title>${esc(dt.date)}　短线${hv(dt.short)}　大盘${hv(dt.market)}　亏钱${hv(dt.lose)}</title></circle>`;
    }).join('');

    els.moneyChart.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" class="money-svg" preserveAspectRatio="none">
        ${grid}
        ${shortPts.length > 1 ? `<polyline class="money-line money-line-short" points="${shortPts.join(' ')}"/>` : ''}
        ${mktPts.length > 1 ? `<polyline class="money-line money-line-market" points="${mktPts.join(' ')}"/>` : ''}
        ${losePts.length > 1 ? `<polyline class="money-line money-line-lose" points="${losePts.join(' ')}"/>` : ''}
        ${xlabels}
        ${hover}
      </svg>`;
  }

  const ALERT_WINDOW = 5;  // 提示只在最近 N 个交易日内展示（次新的可操作窗口很短）

  function renderAlerts(rows) {
    const alerts = state.alerts || [];
    if (!alerts.length || !rows.length) {
      els.moneyAlert.hidden = true;
      els.moneyAlert.innerHTML = '';
      return;
    }
    const winDates = new Set(rows.slice(-ALERT_WINDOW).map(r => r.date));
    const visible = alerts.filter(a => winDates.has(a.date)).slice().reverse();
    if (!visible.length) {
      els.moneyAlert.hidden = true;
      els.moneyAlert.innerHTML = '';
      return;
    }
    const lastDate = rows[rows.length - 1].date;
    els.moneyAlert.hidden = false;
    els.moneyAlert.innerHTML = visible.map(a => `
      <div class="env-banner ${a.tone === 'neg' ? 'env-neg' : 'env-pos'}">
        <span class="env-dot"></span>
        <span class="env-label"><b>${esc(a.date)}　${esc(a.label)}${a.date === lastDate ? '（最新）' : ''}</b></span>
        <span class="env-advice">${esc(a.advice)}　·　${esc(a.detail)}</span>
      </div>`).join('') + '<div id="moneyNewstocks" class="money-newstocks"></div>';
    const latest = visible[0];  // visible 已反转为最近在前
    loadNewStocks(latest.date);
  }

  async function loadNewStocks(date) {
    const box = $('#moneyNewstocks');
    if (!box) return;
    box.innerHTML = '<span class="muted">次新（N/C）名单加载中…</span>';
    try {
      const r = await fetch('/api/newstocks?date=' + encodeURIComponent(date));
      const d = await r.json();
      const items = (d && d.rows) || [];
      if (!items.length) {
        box.innerHTML = '<span class="muted">次新（N/C）：' + (d && d.stale
          ? '该日未采集快照（N/C 前缀次日会漂移，无法回溯重建）'
          : '该日无上市首日/次新个股') + '</span>';
        return;
      }
      box.innerHTML = '<span class="money-newstocks-title">次新（N/C）</span>' + items.map(s => {
        const tip = [s.stage, '换手 ' + fmtNum(s.turnover) + '%', '量比 ' + fmtNum(s.volumeRatio), '振幅 ' + fmtNum(s.amplitude) + '%'].join('　');
        return `<a class="tag money-newstock" href="${emUrl(s.code)}" target="_blank" rel="noopener" title="${esc(s.name + '　' + tip)}">${esc(s.name)}<em class="${(s.chg || 0) >= 0 ? 'up' : 'down'}">${fmtPct(s.chg)}</em></a>`;
      }).join('');
    } catch (e) {
      box.innerHTML = '<span class="muted">次新（N/C）名单加载失败</span>';
    }
  }

  function render() {
    const rows = state.rows;
    if (!rows.length) {
      els.moneyBanner.hidden = true;
      els.moneyAlert.hidden = true;
      els.moneyChart.innerHTML = '';
      els.moneyBody.innerHTML = '';
      els.moneyEmpty.hidden = false;
      els.moneyStatus.textContent = '暂无数据';
      return;
    }
    els.moneyEmpty.hidden = true;
    const last = rows[rows.length - 1];
    const tone = (v) => v == null ? 'env-warn' : (v >= 60 ? 'env-pos' : (v >= 40 ? 'env-warn' : 'env-neg'));
    els.moneyBanner.hidden = false;
    els.moneyBanner.className = 'env-banner ' + tone(last.short);
    els.moneyBanner.innerHTML = `
      <span class="env-dot"></span>
      <span class="env-label"><b>${esc(last.date)}　短线打板效应 ${last.short == null ? '—' : last.short}</b></span>
      <span class="env-advice">大盘赚钱效应 ${last.market == null ? '—' : last.market}　亏钱效应 ${last.lose == null ? '—' : last.lose}　${last.partial ? '（部分数据）' : ''}　· 50分以上为赚钱效应偏强、亏钱效应偏强</span>`;
    els.moneyStatus.textContent = rows.length + ' 个交易日 · 至 ' + last.date;
    els.moneyMeta.textContent = rows.length + ' 日';
    renderAlerts(rows);
    renderChart(rows);

    const recent = rows.slice().reverse();
    els.moneyBody.innerHTML = recent.map(r => {
      const raw = r.raw || {};
      const limitRatio = (raw.zt != null && raw.dt != null && (raw.zt + raw.dt) > 0)
        ? (raw.zt / (raw.zt + raw.dt) * 100).toFixed(0) + '%' : '—';
      const breadth = (raw.up != null && raw.down != null) ? `${raw.up}/${raw.down}` : '—';
      return `
        <tr>
          <td class="num">${esc(r.date)}</td>
          <td class="num ${(r.short || 0) >= 50 ? 'up' : 'down'}"><strong>${fmtNum(r.short)}</strong></td>
          <td class="num ${(r.market || 0) >= 50 ? 'up' : 'down'}"><strong>${fmtNum(r.market)}</strong></td>
          <td class="num ${(r.lose || 0) >= 50 ? 'up' : 'down'}"><strong>${fmtNum(r.lose)}</strong></td>
          <td class="num">${fmtPct(raw.avgPremium)}</td>
          <td class="num">${raw.promoteRate == null ? '—' : fmtNum(raw.promoteRate * 100) + '%'}</td>
          <td class="num">${raw.first2Rate == null ? '—' : fmtNum(raw.first2Rate * 100) + '%'}</td>
          <td class="num">${limitRatio}</td>
          <td class="num">${breadth}</td>
          <td class="num">${fmtPct(raw.amountChgPct)}</td>
        </tr>`;
    }).join('');
    refreshIcons();
  }

  function bindEvents() {
    els.daysSel.addEventListener('change', () => load(els.daysSel.value));
    els.btnBackfill.addEventListener('click', async () => {
      els.btnBackfill.disabled = true;
      toast('回填中（受东财约2周历史所限）…');
      try {
        const r = await fetch('/api/money/backfill');
        const d = await r.json();
        if (!d || !d.ok) throw new Error((d && d.error) || '回填失败');
        state.rows = d.rows || [];
        state.alerts = d.alerts || [];
        render();
        toast('回填完成，共 ' + state.rows.length + ' 个交易日');
      } catch (err) {
        toast('回填失败：' + (err.message || err));
      }
      els.btnBackfill.disabled = false;
    });
  }

  function init() {
    bindEvents();
    load(30);
    refreshIcons();
  }
  init();
})();
