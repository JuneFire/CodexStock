---
name: daily-review
description: 盘后生成 A 股四层复盘（大盘→情绪→板块→个股），写 data/review_text/<date>.txt 并展示在 review 页面。交易日 15:05 后运行，基于当日收盘数据 + 悟道 MCP 外部口径。
---

# 每日盘后四层复盘

为短线情绪周期交易者生成"盘后复盘"——一份按 **大盘 → 情绪 → 板块 → 个股** 四层推进的复盘文本，回答"今天市场发生了什么、为什么、明天盯什么"。

与 `daily-plan`（盘前预案）配对，但**是两个不同的东西**，别混：

| | `daily-plan` | 本 skill |
|---|---|---|
| 时机 | 盘前 9:10 | 盘后 15:05 后 |
| 数据源 | 纯本地 backend | backend **+ 悟道 MCP** |
| 结构 | 大局观 / 短线 / 题材 / 总结 | **大盘 / 情绪 / 板块 / 个股** |
| 面向 | 明天怎么做（预案） | 今天发生了什么（复盘） |

## 运行前提

- 本地服务 `http://127.0.0.1:8010` 需运行（`backend/server.py`）
- 交易日 **15:05 后**（收盘数据已齐）。非交易日跳过
- 输出 **UTF-8 with BOM（utf-8-sig）**，禁止混写 GBK
- 后端会在 15:05 自动写一份**数据版**骨架（`_run_review_text`）；本 skill 生成的是**富文本版**，**必须覆盖**骨架

## 数据源

| 数据 | 来源 | 说明 |
|---|---|---|
| 当日复盘数据 | `GET /api/review?date=<当日>&refresh=1` | indices / breadth / pools / lianban / ztMeta / manual |
| 涨跌停统计 | MCP `limit_stats` | 涨停/触板/炸板/封板率/跌停 |
| 连板梯队+主类题材 | MCP `limit_up_ladder` | maxTier、各档梯队、主类题材排行、晋级率 |
| 涨停明细+原因 | MCP `limit_up_filter(includeReasonInfo=true, sortBy=continue_num)` | `primaryTheme`/`reasonType`/`orderAmount`/`firstLimitUpTimeText` |
| 市场宽度 | MCP `market_overview` | 涨跌家数（与本地 breadth 口径略有差异） |
| 竞价全景 | MCP `auction_opening_snapshot(format="json")` | 六个桶：竞价涨停/跌停/涨停委买额/竞价成交额/昨炸板反馈/昨涨停反馈 |
| 题材资金 | MCP `theme_intraday_capital(format="json")` | `themeName`/`mainNetAmount`/`pctChg`/`strength` |
| 炸板/跌停名单 | MCP `broken_limit_up` / `limit_down` | |
| 保存文本 | `POST /api/review_text` body `{date, text}` | 同时落盘 `data/review_text/<date>.txt` |
| 读回 | `GET /api/review_text?date=<当日>` | |

**注意 MCP 返回形态**：多数工具默认返回**单行 headline**；要 JSON 明细必须显式传 `format="json"`，且数据可能在 `data.rows` / `data.buckets` 下而非顶层 `rows`。

## 四层结构与判定口径

### 一、大盘

写：四大指数（开/收/涨跌幅/成交额）、开收关系、大小分化、量能环比、涨跌家数。

- 上证昨收 = 收盘 - 涨跌点数；比较 `open` 与昨收 → 高开/低开
- 大小分化看 创业板/科创50 与 上证 的涨跌幅差
- 量能看 `marketAmountYi` 与 `marketAmountChangePct`（两市口径取上证行的这两个字段）
- 定性：普跌/普涨/分化 × 放量/缩量

### 二、情绪

写：涨停/跌停/炸板/封板率、昨涨停溢价、连板梯队、晋级率、竞价反馈、周期定性。

- **周期定性**按 backend `classify_environment()` 的硬条件推（该函数结果依赖 `data/sentiment/`，若该日文件缺失就手推）：
  - 冰点：涨停 ≤25 且连跌 ≥2 天
  - 退潮：涨停 ≥40 且红盘占比 <45%，或连跌 ≥1 天且（涨停降 ≥15% 或 炸板 ≥20 或 涨停 ≤40）
  - 上升：涨停 ≥55 且最高板 ≥4 且涨停不低于昨 且 lb3p ≥1
  - 其余：分歧
- **晋级率**（limit_up_ladder 直接给）：1→2 / 2→3 / 3→4 / 高位。**1→2 是最灵敏的低位接力指标**，显著偏低是退潮的直接证据
- **竞价反馈**：昨炸板池今表现（有无强反包）、昨涨停池今表现（续板/走弱）

### 三、板块

**必须三个口径分开列**，混在一起会误判：

1. **涨停家数口径**（MCP `limit_up_ladder` 的 `primaryThemeStats` / `limit_up_filter` 的 `primaryTheme`）
2. **行业口径**（本地 `ztMeta.frontSectors`）
3. **资金口径**（MCP `theme_intraday_capital` 的 `mainNetAmount`）

三者排序经常不一致——不一致本身就是信息。然后判真伪（见下方三条判读方法）。

### 四、个股

写：高标（含连板数/题材/涨停原因/封单/首封时间）、竞价委买留存率、炸板/跌停名单、假强样本。

- 高标取 `limit_up_filter` 里 `continueNum` 最大的几只 + `ztMeta.sealYi` 封单榜
- 首封时间用 `firstLimitUpTimeText`（09:25-09:30 秒板 = 真强；14:20 后 = 尾盘偷袭）
- 封单用 `orderAmount`（元）换算

## 三条必须应用的跨层判读方法

1. **尾盘偷袭识别**——题材涨停家数第一 ≠ 真主线。
   看该题材各股的 `firstLimitUpTimeText`：若多数集中在 **14:20 之后**，且封单普遍小，则为**尾盘凑板块效应**，不是全天资金 buy-in，**不算真主线**。
   对比 9:25-9:30 秒板的才是真强。

2. **竞价撤单识别**——`limitBuyAmount` 含 9:20 前可撤单的噪音。
   真实强度看 `limitBuyAmountAfter920`，算**留存率 = After920 / limitBuyAmount**。
   留存率极低（如 <5%）但绝对额巨大 = **巨量挂单诱多**。

3. **假强识别**——单票竞价 `bidStrength` 高（甚至全场第一）但**所属题材排不进前列** → 假强。
   典型表现：竞价高开甚至涨停，当日炸板。

## 降级路径（必须遵守）

MCP 不可用（额度耗尽 / 返回 `FREE_TIER_MARKET_OPEN_RESTRICTED` / `DAILY_LIMIT_EXCEEDED` / 超时）时：

- **仍出「大盘 + 情绪」两层**（本地 `/api/review` 数据足够）
- 「板块 + 个股」层标注 `MCP 不可用，未取外部口径`，只给本地区块（`ztMeta` 行业分布 + 封单榜 + 涨停池）
- **绝不**因 MCP 失败而整个 skill 跑不出来，也**绝不**编造竞价/题材/资金数据

## 输出规范

- 文本用 markdown 标题前缀：`#` 一级（`# <date> 复盘`）、`##` 章节（`## 一、大盘`）、`###` 子节
  —— review 页与 plan 页共用 `frontend/rich_text.js` 渲染，只识别这三类前缀
- 写 `data/review_text/<date>.txt`（utf-8-sig），并 `POST /api/review_text` 落库
- **完整覆盖当日数据版骨架**（后端 15:05 写的），不要只追加
- 末尾固定加「投资有风险，入市需谨慎！」
- 输出语气：口语化短句、分号分隔、每句一个判断

## 话术库

引用术语前以 `.claude/skills/daily-plan/references/交易知识库.md` 为准：趋势建仓 / 炸板 6 原则 / 便宜老大 / 卡位晋级 / 利润垫 / 断板预期 / 绿肥红瘦 / 一字强度。

## 与 daily-plan 的边界

- 本 skill **只写** `data/review_text/` 与 `/api/review_text`，**不碰** `manual` 字段、不碰 `data/plan/`
- `manual` 的填写归 `daily-plan`（盘前）；本 skill 看到 manual 为空是正常的，不要代填
