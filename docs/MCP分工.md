# MCP 分工约定（悟道 A股 vs 财搭子）

> 适用：本项目（D:/A股量化空间）所有会话。
> 三个 MCP 均已配置在 pi 客户端 `C:\Users\zkCheng\.pi\agent\mcp.json`：悟道 A 股、财搭子、stock-sdk（2026-10-25 接入，见 §6）；另有第四个 `tdx` 通达信（见 §7）。
> 本文结论全部来自 2026-10-23 / 2026-10-25 的实测，不是文档推测。

---

## 1. 一句话原则

**数字以悟道为准，财搭子只贡献「悟道不给的判断层」。**

原因：两者同一指标的口径与时间基准不同，混用会让同一份报告自相矛盾。
实测例（300750 宁德时代）：悟道 `valuation_snapshot` 给 PE(TTM) **12.54**（基准 2026-10-23 收盘），
财搭子 `analyze_caidazi_valuation` 给 PE **14.54**（基准 **2026-10-19** 收盘，滞后 4 天）。
PE 差 2 个数，但两个都不算错——只是基准日不同。

---

## 2. 场景分工表

| 场景 | 用谁 | 说明 |
|---|---|---|
| 竞价复盘 / 竞价额 / 封单 / 尾秒 | **只悟道** | 财搭子零竞价指标 |
| 涨停梯队、炸板、跌停、连板溢价、题材归因 | **只悟道** | 财搭子零涨停生态 |
| 盘后市场复盘（温度/宽度/梯队/简报） | 悟道 `market_replay_workflow` 打底 + 财搭子 `get_market_analysis` 补定性 | 别让财搭子出数字 |
| 涨停板专项复盘 | 悟道 `limitup_review_workflow` | `detailLevel` 可选 summary/standard/raw |
| 个股研究 | 悟道 `stock_research_workflow` 取数 → 财搭子四维补分位/行业/反证 | 短线用 `includeFundamental:false` |
| 只看价格涨跌、是否涨停 | 财搭子 `get_real_time_record` | 自带涨停判定（是否涨停/距涨停/板类型），省一次计算 |
| 历史竞价价（开盘价=09:25撮合价） | `data/kline_cache/` 或 akshare 日K `open` | 财搭子分钟K 不含竞价，见 §4 |
| 自选 / 持仓 / 组合快照 / 监控任务 | **只财搭子** | 悟道 `watchlist_*` 是另一套池子，别混 |
| 财经新闻 / 研报 / 公告 | 悟道 `research_reports` / `official_announcements` / `cls_news`；深度检索用财搭子 `investment_search_pro` | |
| 事件影响、业绩前瞻/点评、相对估值、DCF | **只财搭子** | 悟道无对应工具 |
| 龙虎榜、解禁、两融、北向、股东结构、可转债/ETF/指数行情、美股 SEC | **只悟道** | 财搭子无对应工具 |

---

## 3. 工具清单对照

### 悟道 63 个工具（`mcp__wudao_astock__*`）

```
stock_search, kline, minute_data, stock_rank, market_overview, trading_calendar,
limit_up_ladder, limit_up_filter, limit_up_premium, broken_limit_up, limit_down,
approaching_limit_up, limit_stats, limit_events, limit_event_summary,
board_break_analysis, anomaly_detection, capital_flow, sector_analysis,
stock_screener, smart_hotlist, research_reports, auction_data, briefings,
dragon_tiger, valuation_snapshot, financial_summary, shareholder_structure,
stock_event_calendar, unlock_events, margin_trading, northbound_holdings,
macro_calendar, market_catalyst_calendar,
auction_market_scan, auction_opening_snapshot, auction_theme_strength,
market_replay_workflow, stock_research_workflow, limitup_review_workflow,
official_announcements, official_interactions, official_disclosure_evidence,
news_hotlist, cls_news, index_market, etf_market, convertible_bond_market,
intraday_main_flow, short_term_emotion, theme_intraday_capital, theme_stocks,
sec_company_search, sec_filings, sec_company_facts,
watchlist_list, watchlist_group_list, watchlist_group_create,
watchlist_group_rename, watchlist_group_delete, watchlist_add,
watchlist_remove, watchlist_update_stock
```

**写操作警告**：`watchlist_group_*` / `watchlist_add` / `watchlist_remove` / `watchlist_update_stock`
只在用户明确要求时才调用。上限 300 只 / 20 组 / 每组 50 只；`groupMode=add` 追加、`replace` 移动。

### 财搭子 33 个工具（`mcp__caidazi__*`）

| 组 | 工具 |
|---|---|
| 行情快照 (5) | `get_real_time_record`、`get_real_time_market_summary`、`get_a_share_realtime_1m_price`、`get_a_share_history_1m_price`、`compare_assets` |
| 个股四维 (7) | `analyze_caidazi_technical`、`analyze_caidazi_capital_flow`、`analyze_caidazi_financial`、`analyze_caidazi_valuation`、`relative_valuation`、`intrinsic_valuation`、`get_asset_overview` |
| 事件财报 (3) | `analyze_event_impact`、`analyze_earnings_result`、`preview_earnings` |
| 板块宏观 (5) | `get_market_analysis`、`get_hot_report`、`get_macro_analysis`、`get_sector_radar`、`sector_deep_dive` |
| 搜索选股 (4) | `investment_search_pro`、`screen_stocks`、`extract_assets`、`generate_asset_report` |
| ETF/归属 (3) | `get_etf_constituents`、`get_index_related_etfs`、`get_stock_belongings` |
| 账户 (6) | 只读 `get_caidazi_user_watchlist`、`get_caidazi_positions_summary`、`get_caidazi_portfolio_snapshot`、`get_caidazi_monitor_tasks`；写 `add_caidazi_watchlist`、`remove_caidazi_watchlist` |

**写操作警告**：`add_caidazi_watchlist` / `remove_caidazi_watchlist` 仅在用户明确要求时调用。

### 重叠项（两个都有，按 §1 原则取悟道）

选股（`stock_screener` vs `screen_stocks`）、估值（`valuation_snapshot` vs `analyze_caidazi_valuation`）、
财务（`financial_summary` vs `analyze_caidazi_financial`）、
资金（`capital_flow`/`intraday_main_flow` vs `analyze_caidazi_capital_flow`）、
市场复盘（`market_replay_workflow`/`briefings` vs `get_market_analysis`/`get_hot_report`）。

---

## 4. 财搭子拿不到竞价数据（实测记录，2026-10-23）

三个层次：

1. **完全拿不到**：竞价成交额、竞价换手率、竞价量比、涨停委买额、封单额、
   尾秒抢筹/撤退（`openBidPct`/`openBidSignal`）、几天几板、竞价一致性。33 个工具里无任何 auction 工具。
2. **只能拿“当天”的竞价撮合价**：`get_real_time_record` 的**「开盘价」字段 = 09:25 竞价撮合价**。
   实测 300750：财搭子开盘价 220.00，悟道竞价涨幅 -0.19%（prevClose 220.41 → 219.99），完全对上。
   但悟道的竞价额 4.1067亿 / 竞价量比 6.4 / 涨停委买额 0.4869亿，财搭子一个都没有。
   注意财搭子的 `量比` 是**全天量比**（1.59），不是竞价量比（6.4），别混用。
3. **分钟K 不含竞价**：`get_a_share_history_1m_price(['300750.SZ'], trading_days=2)`
   实测 20261023 共 240 根，**09:30 之前 0 根**（从 09:31 开始）；
   首根 09:31 是 `is_synthetic:true / open = prev_close / volume = 0` 的假 bar，
   用它算竞价价会得到 0% 涨幅——错误。另有大量 `volume: 0` 的 bar，数据质量偏弱。
   `trading_days` **参数只支持 2**，传 1 会直接报 `获取 A 股历史 1 分钟行情失败: trading_days 目前只支持 2`。

**一个待验证的可能**：`get_real_time_record` 带「委托买盘价 / 委托买盘量」字段。
收盘后实测为 `0.00 / 0.00股`（盘口已清空）。若盘中 09:15–09:25 该字段是实时的，
则可作为 09:15/09:20 封单的独立兜底源——正好补上沪市涨停委买额 **18.9%（83/439）** 的缺口
（深市缺口 0%、北交所 100% 缺）。验证方法：交易日 09:20 调一次 `get_real_time_record`。

---

## 5. 相关本地数据与脚本

- `data/auction_hist/<date>.json` — 悟道 `auction_opening_snapshot` 原始缓存（133 天，含 8 个 bucket）
- `data/auction_top/<date>.json` — 后端标准命名归档，由 `scripts/wudao_hist_to_auction_top.py` 生成
- `scripts/auction_hist_pull.py` — 拉悟道竞价历史（读 `~/.claude.json` 的 Authorization，免费额度 50/天）
- 转换要点：`sealAmount` 取 `limitBuyAmount`（09:25 终值），**不可取 `finalSealAmount`**（盘后截面，开过板的票会塌成 0.01 亿）

---

## 6. 第三个数据源：stock-sdk（2026-10-25 接入，实测通过）

配置：`C:\Users\zkCheng\.pi\agent\mcp.json` → `"stock-sdk": {"command":"npx","args":["-y","stock-sdk-mcp"]}`。
后端 npm 包 `stock-sdk-mcp@0.2.1`（bin `stock-mcp`，node≥18），stdio 起服，**69 个工具**，无需 token/额度。

### 实测基线（2026-10-25 11:40）
- `initialize` → `serverInfo: {name:"stock-sdk-mcp", version:"0.2.1"}`，`tools/list` 返回 69 个工具。
- `get_a_share_quotes {codes:["sh600519","sz000001"]}` → 贵州茅台 price 1335 / prevClose 1318.49，五档盘口完整。
- `get_quotes_by_query {queries:["联特科技"]}` → 301205 price 279.5 / prevClose 268（+4.3%），与群内 10-23「联特最强光」口径一致。
- `get_zt_pool {}` → total 82，字段含 `continuousBoardCount`/`firstBoardTime`/`sealAmount`/`industry`/`ztStatistics`。
- **A 股代码必须带市场前缀**（`sh600519` / `sz000001`）；不带前缀传 `["600519","000001"]` 返回 **`[]` 且不报错**（静默空结果，最容易踩的坑）。
  注意**前缀要求因工具而异**：`get_kline_with_indicators` / `analyze_stock` 的 `symbol` 示例是 `600519` / `sh600519` 两种写法都出现过；遇到空结果先去前缀 / 再加前缀各试一次。
- 不知道代码时用 `get_quotes_by_query`（支持名称/拼音/代码，自动识别 A/H/美股）。

### 工具分组（69）
| 组 | 工具 |
|---|---|
| 行情快照 (5) | `get_a_share_quotes`、`get_hk_quotes`、`get_us_quotes`、`get_fund_quotes`、`get_quotes_by_query` |
| 全市场批量 (3) | `get_all_a_share_quotes`、`get_all_hk_quotes`、`get_all_us_quotes` |
| K线 (6) | `get_history_kline`、`get_hk_history_kline`、`get_us_history_kline`、`get_minute_kline`、`get_today_timeline`、`get_kline_with_indicators` |
| 代码表/搜索 (5) | `search_stock`、`get_a_share_code_list`、`get_hk_code_list`、`get_us_code_list`、`get_fund_code_list` |
| 行业板块 (5) | `get_industry_list`、`get_industry_spot`、`get_industry_constituents`、`get_industry_kline`、`get_industry_minute_kline` |
| 概念板块 (5) | `get_concept_list`、`get_concept_spot`、`get_concept_constituents`、`get_concept_kline`、`get_concept_minute_kline` |
| 资金 (6) | `get_fund_flow`、`get_panel_large_order`、`get_stock_fund_flow_history`、`get_market_fund_flow`、`get_fund_flow_rank`、`get_sector_fund_flow_history` |
| 北向 (3) | `get_northbound_realtime`、`get_northbound_history`、`get_northbound_holding_rank` |
| 龙虎/大宗/两融 (6) | `get_dragon_tiger_list`、`get_dragon_tiger_stats`、`get_dragon_tiger_seat_detail`、`get_option_lhb`、`get_block_trade`、`get_margin_data` |
| 涨停/异动 (3) | `get_zt_pool`、`get_stock_changes`、`get_board_changes` |
| 期货/商品 (6) | `get_futures_kline`、`get_global_futures_spot`、`get_global_futures_kline`、`get_futures_inventory_symbols`、`get_futures_inventory`、`get_comex_inventory` |
| 期权 (9) | `get_index_option_spot`、`get_index_option_kline`、`get_cffex_option_quotes`、`get_etf_option_months`、`get_etf_option_expire_day`、`get_etf_option_minute`、`get_etf_option_daily_kline`、`get_commodity_option_spot`、`get_commodity_option_kline` |
| 其他 (2) | `get_trading_calendar`、`get_dividend_detail` |
| 复合 (5) | `analyze_stock`、`compare_stocks`、`scan_market`、`get_market_overview`、`get_sector_analysis` |

### 它补上了什么（悟道/财搭子都不给的）
- **港股/美股/基金**的实时行情 + 历史 K 线（悟道只有 A 股生态 + 部分 sec/index/etf）。
- **五档盘口 bid/ask 数组**（内外盘 `outerVolume`/`innerVolume`），财搭子只有「委托买盘」两个字段。
- **期货 / 全球商品（含 COMEX 库存）/ 期权（股指、ETF、商品、中金所）** —— 两个现有 MCP 完全没有。
- **一次性带全套技术指标的 K 线**（`get_kline_with_indicators`，自动补算所需历史），省掉本地 ma/macd 计算。
- **全市场批量快照**（A 股 5000+ / H 股 / 美股 8000+），适合做全市场排序、自定义筛选。
- 大宗交易 `get_block_trade`、两融 `get_margin_data`、北向持股排名。

### Prompts / Resources / Skills（非工具）
- **MCP Prompts 5 个**：`stock-analyst`、`stock-screener`、`market-overview`、`realtime-monitor`、`futures-overview`。
- **静态 Resources 7 个**：`stock://calendar/trading`、`stock://market/{a-share,hk,us,fund}/codes`、`stock://board/{industry,concept}/list`。
- **Resource Templates 4 个**：`stock://quotes/{code}`、`stock://kline/{code}/{period}`、`stock://board/industry/{code}`、`stock://board/concept/{code}`。
- **Skills 4 个**（仅 OpenClaw 格式）：stock-analyst、stock-screener、market-overview、realtime-monitor。

### 注意：README 比实际少 15 个工具
官方 README 只列了 53 个工具；实测 `tools/list` 是 69 个。README **漏掉了整组资金/情绪/龙虎榜工具**（也正好是 A 股短线最需要的）：
`get_fund_flow_rank`、`get_stock_fund_flow_history`、`get_market_fund_flow`、`get_sector_fund_flow_history`、
`get_northbound_realtime`、`get_northbound_history`、`get_northbound_holding_rank`、
`get_zt_pool`、`get_stock_changes`、`get_board_changes`、
`get_dragon_tiger_list`、`get_dragon_tiger_stats`、`get_dragon_tiger_seat_detail`、`get_block_trade`、`get_margin_data`。
（另外 README 的期权表漏了 `get_option_lhb`，实际期权类 10 个而非 9 个。）

### 用它的红线（口径）
三套数据源现在都能给「同一只票的 PE / 涨幅 / 资金流」，**基准日与口径各不相同**（§1 已因悟道-财搭子差异踩过一次）。
规则：**凡是已经在用悟道口径出报告的场景，不要中途换成 stock-sdk 取同一个指标**；
stock-sdk 只用于它独有的领域（港股/美股/期货/期权/五档盘口/全市场批量），或明确标注「stock-sdk 口径」的交叉验证。

---

## 7. 第四个数据源：tdx（通达信行情，2026-10-25 复核）

配置：`C:\Users\zkCheng\.pi\agent\mcp.json` → `"tdx": {"command":"C:\Users\zkCheng\.local\bin\tdx-mcp.exe"}`（**直接指向 uv tool 装出来的 exe**）。
PyPI 包 `tdx-mcp@0.2.4`（requires-python ≥3.12，依赖 pandas/numpy/`mcp[cli]`）；起服后 `serverInfo` 报的是 **TDX MCP Server 1.30.0**，两者版本号不是一套，别当冲突。

### 为什么不用 `uvx --from tdx-mcp tdx-mcp`
- `uvx` **不在 PATH 里**（实际位置 `C:\Users\zkCheng\.local\bin\uvx.exe`），写裸 `uvx` 会让 pi 起服时 ENOENT。
- uvx 形式本身实测也能起（33 工具、serverInfo 1.30.0 与 exe 一致），但每次是 uv 缓存的临时环境，需要时用绝对路径 + 明确 `--from`。
- 历史坑（已在 exe 那套环境里修好）：tdx-mcp 依赖 FastMCP，`mcp 2.x` 已改名 MCPServer，需钉死 `mcp[cli]<2`；重装要重跑
  `uv pip install --python <toolenv>/Scripts/python.exe 'mcp[cli]<2'`。

### 实测基线（2026-10-25 12:05）
- `get_index_overview {}` → 上证 `market:1 / code:999999`，close **3690.48**（pre_close 3658.03，+32.45），
  open 3623.57 / high 3691.4 / **low 3574.27**，amount 8986.67 亿，`up_count 1853 / down_count 437`。
  与 10-23「尾盘放量、指数超跌反弹」的群内口径一致。
- `initialize` + `tools/list` → 33 个工具。

### 33 个工具
| 组 | 工具 |
|---|---|
| 指数/大盘 (2) | `get_index_overview`、`market_monitor` |
| 个股报价 (4) | `stock_quotes`、`stock_quotes_list`、`stock_quotes_details`、`stock_top_board` |
| K线/分时 (4) | `stock_kline`、`stock_tick_chart`、`multi_tick_charts`、`stock_index_momentum` |
| 逐笔/挂单 (2) | `stock_transaction`、`stock_history_orders` |
| 竞价 (1) | `stock_auction` |
| 异动 (1) | `stock_unusual` |
| 板块/归属 (2) | `board_members_quotes`、`symbol_belong_board` |
| 资金 (1) | `symbol_zjlx` |
| F10/信息 (3) | `stock_f10`、`symbol_info`、`goods_server_info` |
| 商品期货 (5) | `goods_quotes`、`goods_quotes_list`、`goods_kline`、`goods_history_transaction`、`goods_tick_chart` |
| 纯计算指标 (8) | `indicator_ma`、`indicator_ema`、`indicator_macd`、`indicator_rsi`、`indicator_kdj`、`indicator_boll`、`indicator_atr`、`indicator_vol_ma` |

### 参数约定（最容易踩）
- **整数市场码 `market`：0=深、1=沪、2=北**（不是 `sh`/`sz` 前缀，也不是 stock-sdk 的 `sh600519` 那套）。
- 指数走 `market:1` + `code:999999`（上证）；`stock_index_momentum` 用于指数动能。
- `indicator_*` 是**纯函数**：给序列算指标，不出数源，适合与本地回测口径对齐。

### 它补上了什么
- **逐笔成交 `stock_transaction`** 与 **历史挂单 `stock_history_orders`** —— 三个现有源都没有。
- **分时/多股分时 `stock_tick_chart` / `multi_tick_charts`** 直接出分时图数据，不用自己拼分钟 K。
- **F10 `stock_f10`**、板块归属 `symbol_belong_board`（悟道只有 watchlist 侧，财搭子有 `get_stock_belongings` 但粒度不同）。
- **商品期货**（`goods_*`）与 stock-sdk 的国内期货 K 线互补：tdx 能出逐笔和分时。

---

## 6. 服务器端接入（2026-10-29 落地）

服务器 `/opt/stock_system/CodexStock` 上新增 `backend/wudao_feed.py`，在 09:25 抓完腾讯快照后自动再做一次悟道增强。

**为什么需要**：腾讯快照在 09:25:0x 常还没刷新「成交额」，代码里 `amount<=0` 会把这些票整只丢掉。实测漏票：

| 日期 | 抓取时刻 | 服务器 max | 悟道真实 max | 漏掉的名次 |
|---|---|---|---|---|
| 2026-10-22 | 09:25:09 | 1.185亿 | 7.878亿 | 前 4 名 |
| 2026-10-23 | 09:26:32 | 2.921亿 | 5.074亿 | 前 3 名 |
| 2026-10-28 | 09:25:09 | 0.914亿 | 2.137亿 | 前 19 名 |

共有票的数值两边**完全一致**（逐票核对过涨幅与金额），所以不是口径问题，是抓取时刻的快照不全。

**做法**：一次 `auction_opening_snapshot` 调用（8 个分桶合并，实测 1.72s / 105 行），按 code 并进腾讯基座——同 code 只补字段，新 code 整行追加。
`2026-10-28` 实测：腾讯 60 + 新增 81 = **141 行**，补字段 24 只，板数 27 只、题材 96 只、尾秒 101 只有值。

**配置**：`config.json` 增加

```json
{ "wudao": { "apiKey": "lb_xxx" } }
```

也可以用环境变量 `WUDAO_API_KEY` 覆盖。不填则整段增强跳过，只打一行日志，不影响主链路。

**额度**：每天 1 次调用。注意额度是**按 key 共享**的（同一把 key 在别的客户端/会话用光了，服务器这条就会失败并打 `[auction-top] 悟道增强跳过: ...`）。

**抓取耗时**（服务器实测）：腾讯 `stock_zh_a_spot_tx` 9.27s → 东财 `push2` 已 502 不可用 → 新浪 `stock_zh_a_spot` 18.28s。所以主源保持腾讯，悟道只做增强。
