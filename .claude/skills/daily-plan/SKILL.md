---
name: daily-plan
description: 生成 A 股短线交易者每日早盘预案（复盘+次日预案文本），并同步填充情绪复盘页面的 manual 字段。交易日 9:10 开盘前运行，基于昨日收盘数据 + 隔夜消息。
---

# 每日早盘预案生成

为短线情绪周期交易者生成"早盘预案"——一份基于昨日收盘复盘 + 隔夜消息的次日交易预案文本，同时把同一份分析填入 review 页面的 manual 字段。

## 运行前提

- 本地服务 `http://127.0.0.1:8010` 需运行（`backend/server.py`），数据通过 HTTP API 拉取
- 交易日 9:10 执行（开盘前）。非交易日跳过
- 预案基于**昨日**收盘数据（今日未开盘），manual 填到**昨日** review 记录
- 全部输出 **UTF-8 with BOM（utf-8-sig）**，禁止混写 GBK

## 数据源

| 数据 | 接口 | 说明 |
|---|---|---|
| 昨日复盘数据 | `GET /api/review?date=<昨日>` | indices/breadth/pools/lianban/ztMeta/manual |
| 近几日历史 | `GET /api/review?date=<前2~4日>` | 情绪周期环比（ztCount/maxTier/溢价/炸板） |
| 头马/黑马 | `GET /api/review/horses?date=<昨日>` | MA60+OBV 行业分类 → 题材轮动 |
| 封单数据 | `GET /api/seal?date=<昨日>` | 昨日 09:15/09:20/09:25 买一封单强度 |
| 昨日竞价快照 | `GET /api/latest` + `data/history/<昨日>.json` | 竞价涨幅/金额强度/量比 → 断板/低吸候选 |
| 今日已有 manual | **`db.read_review(<今日>)` 纯 SELECT** | 合并基线；**不要** `GET /api/review?date=今日`（会触发空抓取） |
| 隔夜消息 | WebSearch | 美股/A50/政策/产业 |
| 保存 manual | `POST /api/review` body `{date, manual}` | **整体替换** manual_json，必须先读后合并再全量 POST |

失败降级：昨日 review 无 → 回退 `data/history/<昨日>.json`（竞价视角）；horses/seal 404 → 忽略；WebSearch 失败 → 消息节写"隔夜消息未获取到"；服务未启动 → 尝试拉起 `backend/server.py`。

## manual 字段结构与归属

`GET /api/review` 返回的 `manual` 是嵌套 JSON，结构与 review.html 的 `data-manual-key` 一致。**每个字段标注 agent 是否填**：

| 字段 | 归属 | 内容来源 |
|---|---|---|
| `indicesOutlook.press/support` | **AGENT 填** | 昨日收盘 + 近期K线结构 → 今日压力/支撑 |
| `indicesOutlook.dailyK/weeklyK/outlook` | **AGENT 填** | 昨日指数 + 开盘情景推演（高开/低开/平开） |
| `emotion.nextWind` | **AGENT 填** | 今日多头看 X、空头看 Y（风标，非推荐） |
| `news` | **AGENT 填** | WebSearch 隔夜消息汇总 |
| `sectors[i].name/note` | **AGENT 填 name/note** | ztMeta.frontSectors + horses 最强板块；note 写今日预判 |
| `lianbanPlan[n]` + `lianbanPlan.first` | **AGENT 填** | 连板梯队逐档预案：断板预期/一进二/回避/减仓 |
| `bestChance.stock/sector/buyPct/logic` | **AGENT 填** | 当天最好机会预判（buyPct=预判低吸位置） |
| `auctionRows[0].wind` | **AGENT 填**（仅 09:15 一格） | 09:15 风标预判 |
| `indicesOutlook.openPoint` | 前端自动回填 | 上证今开（review.js 回填） |
| `auctionRows[i].maxSeal/sealYi/front/three` | 前端自动回填 | 9:25 封单数据（review.js 回填） |
| `auctionRows[1].wind` / `auctionRows[2].wind` | 留给用户/盘中 | 09:20/09:25 竞价风标 |
| `emotion.today` / `emotion.windFix` | 留给用户/盘后 | 今日情绪、盘中修正 |
| `sectors[i].point/press/support` | 留给用户/盘中 | 实时点位/压力支撑 |
| `bestChance.premium/note` / `tempTrade` | 留给用户/盘后 | 结果记录 |
| `closeStats.*` | **一律不填** | "一字/涨停/跌停/炸板/红/绿/溢价/昨"语义是当日vs昨，9:10 当日未知，留空；昨日实况写在大局观 |

## 合并规则（关键）

- **POST 是整体替换** manual_json → 必须先 `db.read_review(<今日>)` 读回，**只填空字段**，然后**全量 POST**
- 仅当字段为空串/None 才写 agent 生成值；**任何非空值不覆盖**（保留用户手写）
- 未列入"AGENT 填"的键一律不动
- 若 manual 已全满，跳过 POST 并在摘要说明

## 预案文本骨架

```
<今日>早盘预案
大局观
指数:昨日开/收/涨跌、今日关键位、压力/支撑、开盘情景推演
情绪:昨日情绪定性、涨跌家数。今日多头看XX;空头看YY（情绪风标，非推荐）
题材:昨日最强板块排序、今日先看主线分化力度、再看次线持续性
消息:隔夜政策/产业/美股
具体机会解析
短线方面:
- 高位股:{N板}，{涨停/断板预期}。{断板后调整低吸/今日回避/一字预期看炸板6原则}
- 高位抱团:{大分歧预期，减仓做厚利润垫}
题材方面:
- {板块}:{消息}|{短线风标}|{趋势风标}|{产业链标的，容量/弹性/低吸}
其他对流:
- 高位抱团 / 消费
总结:
{周期判断、风格、节奏}。风险提示
```

## 生成规范

- **指数关键位**：昨日开盘/收盘、前高/前低、整数关、MA5/MA10 构造压力/支撑；高开(>关键位上方)→修复，低开(<昨收)→回踩，中间→整理盘
- **情绪定性**：breadth + ztCount/dtCount/zbCount + lianban.maxTier + 溢价环比 → 分歧/一致、修复/退潮、冰点/高潮
- **风标**：多头=昨日最高连板/最强封单（maxTier 首只 + maxSeal），空头=昨日炸板/断板/高位负反馈。标注"仅为情绪风标，非推荐"
- **题材**：按 ztMeta.frontSectors 行业分布排序；每题材=隔夜消息 + 短线风标 + 趋势风标(趋势票关键位) + 产业链标的
- **短线**：高位股按连板数分级，一字/断板/低吸判断 + 回避/减仓提示
- **总结**：近3-5日情绪周期对比 → 周期位置（启动/发酵/高潮/退潮）、风格（轮动/抱团/高低切）、节奏
- **风险提示**：预案末尾加"投资有风险，入市需谨慎"（用户固定话术）

## 话术库（用户专用术语）

- **风标**：情绪风向标个股，仅观察非推荐（多头/空头、短线/趋势风标）
- **炸板6原则**：涨停打开→回封的6个观察条件，按用户口径看，不替用户发明规则
- **一进二/二进三**：首板晋级二板等连板模式，用于 lianbanPlan
- **断板预期**：高位股预期断板 → 回避 or 断板后调整低吸
- **容量/弹性/低吸**：容量=大成交额龙头，弹性=高波动小票，低吸=回调买入点
- **绿肥红瘦**：弱势期绿盘低吸风格
- **利润垫**：高位持仓减仓做厚缓冲
- **高位抱团**：资金聚集少数高位股，分歧→减仓/离场
- **一字预期/一字强度、炸板/回封**：竞价一字涨停预期

输出语气：口语化短句、分号分隔、每句一个判断。

## 数据口径警告

- 9:10 时**今日竞价数据尚不存在**（竞价 9:25 才开始），严禁写成今日实况
- `/api/latest` 存的是最近一个交易日快照，只用于昨日视角
- 只可引用昨日数据 + 隔夜消息 + 对今日的预判
