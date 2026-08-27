(() => {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
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

  function fmtPoints(v) {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    return (n > 0 ? '+' : '') + n.toFixed(2);
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

  const els = {
    reviewStatus: $('#reviewStatus'),
    reviewDate: $('#reviewDate'),
    idxMeta: $('#idxMeta'),
    idxStats: $('#idxStats'),
    auctionBody: $('#auctionBody'),
    sectorBody: $('#sectorBody'),
    tierMeta: $('#tierMeta'),
    tierBody: $('#tierBody'),
    headMeta: $('#headMeta'),
    headList: $('#headList'),
    darkMeta: $('#darkMeta'),
    darkList: $('#darkList'),
    historyCount: $('#historyCount'),
    reviewDateList: $('#reviewDateList'),
    fetchProgress: $('#fetchProgress'),
    fetchProgressBar: $('#fetchProgressBar'),
    fetchProgressText: $('#fetchProgressText'),
    btnFetch: $('#btnFetch'),
    btnSave: $('#btnSave'),
    btnPrevDay: $('#btnPrevDay'),
    btnNextDay: $('#btnNextDay'),
    toast: $('#toast')
  };

  const state = {
    date: todayStr(),
    data: null,
    manual: {},
    pending: {},
    dates: []
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

  function setManualPath(obj, path, value) {
    const parts = path.split('.');
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      cur = cur[parts[i]] = (cur[parts[i]] && typeof cur[parts[i]] === 'object') ? cur[parts[i]] : {};
    }
    cur[parts[parts.length - 1]] = value;
  }

  function collectManual() {
    const out = JSON.parse(JSON.stringify(state.manual));
    document.querySelectorAll('[data-manual-key]').forEach(el => {
      setManualPath(out, el.getAttribute('data-manual-key'), el.value);
    });
    return out;
  }

  // ---------- 渲染 ----------

  function renderIndices() {
    const idx = state.data.indices || [];
    const sh = idx.find(i => i.name === '上证指数');
    els.idxMeta.textContent = (state.data.fetchedAt || '—') + (state.data.cached ? ' · 已缓存' : '');
    const m = state.data.breadth || {};
    els.idxStats.innerHTML = `
      <div class="stat-card"><span class="stat-label">收盘点位</span><strong class="stat-value ${sh ? colorCls(sh.changePts) : ''}">${sh && sh.close != null ? fmtNum(sh.close) : '—'}</strong><span class="stat-sub ${sh ? colorCls(sh.changePts) : ''}">${sh ? fmtPoints(sh.changePts) : ''}</span></div>
      <div class="stat-card"><span class="stat-label">涨跌幅度</span><strong class="stat-value ${sh ? colorCls(sh.changePct) : ''}">${sh ? fmtPct(sh.changePct) : '—'}</strong><span class="stat-sub">${sh && sh.open != null ? '今开 ' + fmtNum(sh.open) : ''}</span></div>
      <div class="stat-card"><span class="stat-label">沪深成交额</span><strong class="stat-value">${sh && (sh.marketAmountYi != null ? fmtNum(sh.marketAmountYi, 0) + ' 亿' : sh.amountYi != null ? fmtNum(sh.amountYi, 0) + ' 亿' : '—')}</strong><span class="stat-sub ${sh ? colorCls(sh.marketAmountChangeYi) : ''}">${sh && sh.marketAmountChangeYi != null ? fmtPoints(sh.marketAmountChangeYi) + '亿' + (sh.marketAmountChangePct != null ? ' (' + fmtPct(sh.marketAmountChangePct) + ')' : '') : '沪深两市合计'}</span></div>
      <div class="stat-card"><span class="stat-label">红/绿/平</span><strong class="stat-value">${m.up != null ? `<span class="up">${m.up}</span> / <span class="down">${m.down}</span> / <span class="flat">${m.flat}</span>` : '—'}</strong><span class="stat-sub">沪深全市场</span></div>
      <div class="stat-card"><span class="stat-label">涨停/跌停/炸板</span><strong class="stat-value"><span class="up">${(state.data.pools && state.data.pools.ztCount) || 0}</span> / <span class="down">${(state.data.pools && state.data.pools.dtCount) || 0}</span> / ${(state.data.pools && state.data.pools.zbCount) || 0}</strong><span class="stat-sub">${idx.map(i => esc(i.name) + ' <span class="' + colorCls(i.changePct) + '">' + fmtPct(i.changePct) + '</span>').join(' · ') || ''}</span></div>
    `;
  }

  function renderAuction() {
    const meta = state.data.ztMeta || {};
    const maxSeal = meta.maxSeal;
    const sealYiList = (meta.sealYi || []).map(s => `${esc(s.name)} ${fmtNum(s.sealYi)}亿 ${esc(s.industry)}`);
    const front = (meta.frontSectors || []).slice(0, 5).map(f => `${esc(f.name)} ${f.count}涨停`).join('\n') || '';
    const rows = ((state.data.manual && state.data.manual.auctionRows) || [
      { time: '09:15', maxSeal: '', wind: '', sealYi: '', front: '', three: '' },
      { time: '09:20', maxSeal: '', wind: '', sealYi: '', front: '', three: '' },
      { time: '09:25', maxSeal: '', wind: '', sealYi: '', front: '', three: '' }
    ]).filter(r => r.time !== '15:00');  // 兼容旧缓存含 15:00 行
    // 自动回填 9:25 行的最大封单/前排题材
    if (maxSeal && !rows[2].maxSeal) {
      rows[2].maxSeal = `${esc(maxSeal.name)} ${esc(maxSeal.industry)} (${fmtNum(maxSeal.sealAmount)}亿)`;
    }
    if (front && !rows[2].front) rows[2].front = front;
    if (sealYiList.length && !rows[2].sealYi) {
      // 封单亿元以上：换行分隔，便于扫描
      rows[2].sealYi = sealYiList.join('\n');
    }
    // 竞价三一票：金额/换手/涨幅三项第一命中≥2项
    const threePick = ((state.data.pools && state.data.pools.threePick) || []);
    if (threePick.length && !rows[2].three) {
      rows[2].three = threePick.map(t => `${esc(t.name)} ${t.hits}项`).join('\n');
    }

    els.auctionBody.innerHTML = rows.map((r, i) => {
      // 封单亿元以上：结构化展示，名称红暖、封单额居中、题材灰
      const sealHtml = (r.sealYi || '').split('\n').filter(Boolean).map(line => {
        const m = line.match(/^(.+?) (\d+(?:\.\d+)?)亿 (.+)$/);
        if (m) {
          return `<div class="seal-item"><span class="seal-name">${m[1]}</span><span class="seal-amount">${m[2]}亿</span><span class="seal-industry">${m[3]}</span></div>`;
        }
        return `<div class="seal-item">${line}</div>`;
      }).join('');
      // 其它列：个股名染艳红（最大封单/三一票含个股名）；前排题材为题材名保持默认
      const maxSealHtml = nameSpan(r.maxSeal);
      const threeHtml = nameSpan(r.three);
      const frontHtml = (r.front || '').split('\n').filter(Boolean).map(line =>
        `<div class="seal-item"><span class="seal-plain">${esc(line)}</span></div>`).join('');
      return `
      <tr>
        <td class="time-cell">${esc(r.time)}</td>
        <td class="seal-cell"><div class="seal-list-box">${maxSealHtml || '<span class="muted">—</span>'}</div><textarea hidden data-manual-key="auctionRows.${i}.maxSeal">${esc(r.maxSeal)}</textarea></td>
        <td><textarea class="field multi-line" data-manual-key="auctionRows.${i}.wind" rows="${rowsFor(r.wind)}">${esc(r.wind)}</textarea></td>
        <td class="seal-cell"><div class="seal-list-box">${sealHtml || '<span class="muted">—</span>'}</div><textarea hidden data-manual-key="auctionRows.${i}.sealYi">${esc(r.sealYi)}</textarea></td>
        <td class="seal-cell"><div class="seal-list-box">${frontHtml || '<span class="muted">—</span>'}</div><textarea hidden data-manual-key="auctionRows.${i}.front">${esc(r.front)}</textarea></td>
        <td class="seal-cell"><div class="seal-list-box">${threeHtml || '<span class="muted">—</span>'}</div><textarea hidden data-manual-key="auctionRows.${i}.three">${esc(r.three)}</textarea></td>
      </tr>
    `;
    }).join('');
  }

  function rowsFor(text) {
    if (!text) return 2;
    const n = String(text).split('\n').length;
    return Math.max(2, Math.min(8, n));
  }

  // 把文本行开头的个股名称包成艳红色 span，其余文字保持
  function nameSpan(text) {
    return String(text == null ? '' : text).split('\n').filter(Boolean).map(line => {
      const m = line.match(/^(\S+)(.*)$/);
      if (m && m[1]) {
        return `<div class="seal-item"><span class="seal-name">${esc(m[1])}</span><span class="seal-rest">${esc(m[2])}</span></div>`;
      }
      return `<div class="seal-item">${esc(line)}</div>`;
    }).join('');
  }

  function renderSectors() {
    const meta = state.data.ztMeta || {};
    const frontNames = (meta.frontSectors || []).map(f => f.name);
    const rows = (state.data.manual && state.data.manual.sectors) || Array.from({ length: 8 }, () => ({ name: '', point: '', press: '', support: '', note: '' }));
    rows.forEach((r, i) => {
      if (frontNames[i] && !r.name) r.name = frontNames[i];
    });
    els.sectorBody.innerHTML = rows.map((r, i) => `
      <tr>
        <td><input class="field" data-manual-key="sectors.${i}.name" value="${esc(r.name)}"></td>
        <td><input class="field" data-manual-key="sectors.${i}.point" value="${esc(r.point)}"></td>
        <td><input class="field" data-manual-key="sectors.${i}.press" value="${esc(r.press)}"></td>
        <td><input class="field" data-manual-key="sectors.${i}.support" value="${esc(r.support)}"></td>
        <td><input class="field" data-manual-key="sectors.${i}.note" value="${esc(r.note)}"></td>
      </tr>
    `).join('');
  }

  function renderTier() {
    const lb = state.data.lianban || { tier: {}, maxTier: 0 };
    const tier = lb.tier || {};
    const meta = state.data.ztMeta || {};
    const manual = (state.data.manual || {}).lianbanPlan || {};
    const levels = [];
    for (let n = 8; n >= 2; n--) {
      const stocks = tier[n] || [];
      levels.push({
        label: n + '板', stocks,
        plan: manual[n] || '',
        key: String(n),
        hot: n === lb.maxTier
      });
    }
    const first = tier.first || [];
    levels.push({ label: '首板', stocks: first, plan: manual.first || '', key: 'first', hot: false });
    els.tierMeta.textContent = lb.maxTier ? '最高 ' + lb.maxTier + ' 板' : '';
    els.tierBody.innerHTML = levels.map(lv => `
      <tr class="tier-row ${lv.hot ? 'tier-hot' : ''}">
        <td class="tier-label">${esc(lv.label)}</td>
        <td>${lv.stocks.slice(0, 5).map(s => `${esc(s.name)}<span class="muted">${esc(s.code)}</span>`).join('　') || '<span class="muted">—</span>'}</td>
        <td>${lv.stocks.slice(0, 5).map(s => esc(s.industry || '')).join('　')}</td>
        <td><input class="field" data-manual-key="lianbanPlan.${lv.key}" value="${esc(lv.plan)}"></td>
      </tr>
    `).join('');
  }

  function renderManualFields() {
    document.querySelectorAll('[data-manual-key]').forEach(el => {
      const key = el.getAttribute('data-manual-key');
      const parts = key.split('.');
      let cur = state.data.manual || {};
      for (const p of parts) cur = (cur == null ? undefined : cur[p]);
      if (cur != null) el.value = cur;
    });
    // 今日开盘点位：手动未填时自动回填上证今开
    const openInput = document.querySelector('[data-manual-key="indicesOutlook.openPoint"]');
    if (openInput && !openInput.value) {
      const sh = (state.data.indices || []).find(i => i.name === '上证指数');
      if (sh && sh.open != null) openInput.value = fmtNum(sh.open);
    }
  }

  function render() {
    if (!state.data) return;
    renderIndices();
    renderAuction();
    renderSectors();
    renderTier();
    renderManualFields();
    document.querySelectorAll('textarea.field').forEach(autoGrow);
    els.reviewDate.textContent = state.date;
    const d = state.data;
    els.reviewStatus.textContent = d.cached ? '已缓存 · ' + (d.fetchedAt || '') : (d.fetchedAt ? '抓取于 ' + (d.fetchedAt || '') : '未抓取');
    refreshIcons();
  }

  // ---------- 数据 ----------

  async function loadReview(forceRefresh) {
    if (forceRefresh) startProgressPolling();
    const url = '/api/review?date=' + encodeURIComponent(state.date) + (forceRefresh ? '&refresh=1' : '');
    try {
      const res = await fetch(url);
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) throw new Error((data && data.error) || '加载失败');
      state.data = data;
      state.manual = JSON.parse(JSON.stringify(data.manual || {}));
      render();
      if (forceRefresh) {
        stopProgressPolling();
        toast('收盘复盘数据已抓取');
      }
    } catch (err) {
      stopProgressPolling();
      toast('加载失败：' + (err.message || err));
    }
  }

  // 复盘抓取进度条：点收盘复盘后轮询进度接口
  let progressTimer = null;
  function startProgressPolling() {
    els.fetchProgress.hidden = false;
    els.fetchProgressBar.style.width = '5%';
    els.fetchProgressText.textContent = '开始抓取…';
    clearInterval(progressTimer);
    progressTimer = setInterval(async () => {
      try {
        const res = await fetch('/api/review/progress?date=' + encodeURIComponent(state.date));
        const data = await res.json();
        if (data && data.ok) {
          const pct = data.percent || 0;
          els.fetchProgressBar.style.width = pct + '%';
          els.fetchProgressText.textContent = (data.message || data.stage || '') + ' ' + pct + '%';
          if (data.done) {
            clearInterval(progressTimer);
            setTimeout(() => { els.fetchProgress.hidden = true; }, 500);
          }
        }
      } catch (e) { /* 忽略轮询错误 */ }
    }, 800);
  }
  function stopProgressPolling() {
    clearInterval(progressTimer);
    els.fetchProgress.hidden = true;
  }

  async function saveReview() {
    const manual = collectManual();
    try {
      const res = await fetch('/api/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: state.date, manual })
      });
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) throw new Error((data && data.error) || '保存失败');
      toast('已保存');
      state.manual = JSON.parse(JSON.stringify(manual));
    } catch (err) {
      toast('保存失败：' + (err.message || err));
    }
  }

  // ---------- 头等马 / 黑马 ----------
  function renderHorses(data) {
    const head = data && data.headHorses ? data.headHorses : [];
    const dark = data && data.darkHorses ? data.darkHorses : [];
    const dateLabel = (data && data.date) || (data && data.builtAt ? data.builtAt.slice(0, 10) : '');
    const stale = !!(data && data.stale);
    if (dateLabel) {
      els.headMeta.textContent = dateLabel + (stale ? ' · 未保存' : '');
      els.darkMeta.textContent = dateLabel + (stale ? ' · 未保存' : '');
    }
    els.headList.innerHTML = head.length
      ? head.map(h => `
        <div class="horse-item">
          <span class="horse-code">${esc(h.code)}</span>
          <span class="horse-name">${esc(h.name)}</span>
          <span class="horse-pct ${colorCls(h.changePct)}">${h.changePct != null ? fmtPct(h.changePct) : '—'}</span>
        </div>`).join('')
      : '<div class="horse-empty">' + (stale ? '该日未保存' : '暂无符合条件的行业') + '</div>';
    els.darkList.innerHTML = dark.length
      ? dark.map(h => `
        <div class="horse-item">
          <span class="horse-code">${esc(h.code)}</span>
          <span class="horse-name">${esc(h.name)}</span>
          <span class="horse-pct ${colorCls(h.changePct)}">${h.changePct != null ? fmtPct(h.changePct) : '—'}</span>
        </div>`).join('')
      : '<div class="horse-empty">' + (stale ? '该日未保存' : '暂无符合条件的行业') + '</div>';
  }

  async function loadHorses(date) {
    const q = date ? '?date=' + encodeURIComponent(date) : '';
    try {
      const res = await fetch('/api/review/horses' + q);
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) throw new Error((data && data.error) || '加载失败');
      renderHorses(data);
    } catch (err) {
      els.headList.innerHTML = '<div class="horse-empty">头马数据加载失败</div>';
      els.darkList.innerHTML = '<div class="horse-empty">黑马数据加载失败</div>';
    }
  }

  function bindEvents() {
    els.btnFetch.addEventListener('click', () => { loadReview(true); loadHorses(state.date); });
    els.btnSave.addEventListener('click', saveReview);
    els.btnPrevDay.addEventListener('click', () => { state.date = shiftDate(state.date, -1); loadReview(false); loadHorses(state.date); });
    els.btnNextDay.addEventListener('click', () => { state.date = shiftDate(state.date, 1); loadReview(false); loadHorses(state.date); });
    document.addEventListener('keydown', e => {
      if (e.ctrlKey && e.key === 's') { e.preventDefault(); saveReview(); }
    });
    els.reviewDateList.addEventListener('click', e => {
      const btn = e.target.closest('.date-item');
      if (btn) {
        state.date = btn.getAttribute('data-date');
        loadReview(false);
        loadHorses(state.date);
        renderReviewDates(state.dates || []);
      }
    });
    // textarea 输入时自适应增高，避免长文显示不全
    document.addEventListener('input', e => {
      if (e.target && e.target.matches('textarea.field')) autoGrow(e.target);
    });
    document.querySelectorAll('textarea.field').forEach(autoGrow);
  }

  function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
  }

  // ---------- 历史复盘日期列表 ----------
  function renderReviewDates(dates) {
    const active = state.date;
    els.historyCount.textContent = String(dates.length);
    els.reviewDateList.innerHTML = dates.length
      ? dates.map(d => `
        <button class="date-item ${d.date === active ? 'is-active' : ''}" data-date="${esc(d.date)}" type="button">
          <span>${esc(d.date)}</span>
        </button>`).join('')
      : '<div class="date-empty">暂无历史复盘</div>';
  }

  async function loadReviewDates() {
    try {
      const res = await fetch('/api/review/dates');
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!data || !data.ok) {
        els.reviewDateList.innerHTML = '<div class="date-empty">历史列表加载失败</div>';
        return;
      }
      state.dates = data.dates || [];
      renderReviewDates(state.dates);
    } catch (err) {
      els.reviewDateList.innerHTML = '<div class="date-empty">历史列表加载失败</div>';
    }
  }

  async function init() {
    bindEvents();
    refreshIcons();
    // 默认加载最近已保存的复盘，避免今天无缓存时触发全量抓取卡住页面
    try {
      const res = await fetch('/api/review/dates');
      const data = await res.json();
      const dates = (data && data.dates) || [];
      state.dates = dates;
      renderReviewDates(dates);
      if (dates.length) {
        state.date = dates[0].date;
      }
    } catch (e) { /* 忽略，保持默认今天 */ }
    loadReview(false);
    loadHorses(state.date);
  }

  init();
})();
