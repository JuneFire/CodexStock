// 结构化文本渲染：把「标题 + 分行正文」的纯文本渲染成带层级的 HTML。
// 供 plan.html（早盘预案）与 review.html（四层复盘）共用。
(() => {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
  }

  // 对一行文本做关键信息高亮：股票名/连板数/大盘点位/涨跌/关键位
  // 用控制字符 token 占位，避免多遍正则互相覆盖，最后统一还原为 span
  function highlight(text) {
    const safe = esc(text);
    const tokens = [];
    const S = '\x01', E = '\x02';
    function push(cls, content) {
      tokens.push('<span class="' + cls + '">' + content + '</span>');
      return S + (tokens.length - 1) + E;
    }
    let work = safe;
    // 1. 连板数 N板/N连板（高亮晋级），如 "6板"、"11板"
    work = work.replace(/(\d+)板/g, (m, n) => push('pl-lb', n + '板'));
    // 2. 大盘点位：独立的 4 位数字（3934/3927/3943/3902），用于指数与关键位
    work = work.replace(/(?<!\d)(\d{4})(?!\d)/g, (m, n) => push('pl-pt', n));
    // 3. 涨跌幅：带 +/- 或 % 的数字（-0.82%、+0.34%、11%）
    work = work.replace(/([+-]\d+(?:\.\d+)?%)/g, (m, n) => push(n.startsWith('-') ? 'pl-down' : 'pl-up', n));
    work = work.replace(/(?<![A-Za-z])(\d+(?:\.\d+)?%)/g, (m, n) => push('pl-pct', n));
    // 4. 价格/关键位：带小数的数字（157.93、26.83、7.82），用于支撑/低吸位
    work = work.replace(/(?<!\d)(\d+\.\d{1,2})(?!\d)/g, (m, n) => push('pl-key', n));
    // 5. 股票名：常见后缀组合，如 百花医药/秦安股份/哈药股份
    work = work.replace(/([一-龥]{2,8}?(?:股份|医药|药业|科技|实业|食品|发展|集团|新材|电子|生物|能源|银行|证券|传媒|地产|文化|航空|重机|智能|环保|通信|电气|数据|软件|光电|材料|体育|传媒))/g,
      (m, name) => push('pl-name', name));
    // 还原 token
    return work.replace(new RegExp(S + '(\\d+)' + E, 'g'), (m, i) => tokens[+i]);
  }

  // 渲染规则（两套并存）：
  //   1) markdown 前缀：# 标题 / ## 章节 / ### 子节
  //   2) 预案裸标题：YYYY年…早盘预案 / 大局观|具体机会解析|总结 / 短线方面|题材方面|其他对流
  // 其余行 → .plan-line + 高亮；空行 → .plan-blank
  function renderInto(text, container) {
    if (!container) return;
    const lines = (text || '').split('\n');
    container.innerHTML = lines.map(line => {
      const t = line.trim();
      if (!t) return '<div class="plan-blank"></div>';
      let m;
      if ((m = t.match(/^###\s+(.*)$/))) return '<h3 class="plan-sub">' + esc(m[1]) + '</h3>';
      if ((m = t.match(/^##\s+(.*)$/))) return '<h2 class="plan-section">' + esc(m[1]) + '</h2>';
      if ((m = t.match(/^#\s+(.*)$/))) return '<h1 class="plan-title">' + esc(m[1]) + '</h1>';
      if (/^\d{4}年.*早盘预案$/.test(t)) return '<h1 class="plan-title">' + esc(t) + '</h1>';
      if (t === '大局观' || t === '具体机会解析' || t === '总结') return '<h2 class="plan-section">' + esc(t) + '</h2>';
      if (/^(短线方面|题材方面|其他对流)$/.test(t)) return '<h3 class="plan-sub">' + esc(t) + '</h3>';
      return '<div class="plan-line">' + highlight(line) + '</div>';
    }).join('');
  }

  window.RichText = { esc, highlight, renderInto };
})();
