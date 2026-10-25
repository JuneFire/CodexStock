# tdx 竞价采集（免费替代悟道）

> 结论先说：**竞价"结果组"数据用 tdx 免费拿到，与悟道逐条一致；只有"涨停委买额/封单/逐笔演进"仍需悟道。**
> 脚本：`scripts/tdx_auction_fetch.py`　产物：`data/auction_top_tdx/YYYY-MM-DD.json`

## 一、口径校验（2026-10-23，与 `data/auction_top/2026-10-23.json` 对账）

| 指标 | 结果 |
|---|---|
| 竞价额最大偏差 | **0.0009%**（纯浮点舍入） |
| `status` 状态标签 | **102/102 完全一致**，0 不一致 |
| 竞价额 Top 25 排序 | **完全一致** |
| 记录数 | tdx **5480 条**（全市场）/ 悟道 102 条 |
| 竞价金额覆盖 | tdx **160.45 亿** / 悟道 102 条合计 49.99 亿（仅 **31%**） |

**tdx 覆盖了悟道那份文件的 3.2 倍**——悟道按 `bidAmount` 只返回前 100 名，剩下的中小竞价票全丢了。

字段对应关系：

| 语义 | 悟道 | tdx `stock_quotes` | 换算 |
|---|---|---|---|
| 竞价撮合价 | `price` | `open` | 一致 |
| 竞价成交额 | `auctionAmount`（元） | `open_amount`（**万元**） | ×1e4 |
| 尾秒竞价涨幅 | `obp` | `opening_rush`（"0.79%"） | 去 % 转 float |
| 昨收 | `prevClose` | `pre_close` | 一致 |
| 竞价换手 | `turnover` | 无 | 需流通股，tdx 不给 |
| 涨停价 | `limitUpPrice` | 自己算 `prevClose×(1+limitPct)` | 见 `limit_pct_of()` |

## 二、性能（实测，别踩）

| 场景 | 耗时 |
|---|---|
| **tdx-mcp 冷启动** | **292 秒**（spawn + initialize，opentdx 建连/选主站） |
| 暖调用单批（80 只） | 0.1 ~ 0.2 秒 |
| 观察池 80 只 | 12 秒（含冷启动分摊） |
| **全市场 5578 只（70 批）** | **6 分 12 秒**（其中冷启动 3~5 分钟） |

→ 竞价抢时段（09:25）**绝不能用一次性脚本**，必须 `--serve` 常驻保温：

```bash
python scripts/tdx_auction_fetch.py --serve     # 09:00 前拉起来
# 逐行发指令，每行回一条 JSON：
ping
fetch 2026-10-23 600519,300308,000001
universe 2026-10-23
quit
```

## 三、已知故障与对策

| 现象 | 原因 | 对策 |
|---|---|---|
| `no available server` | opentdx 长连接在连续 ~67 次调用后掉线 | 已内置：检测到即重启进程重试；**先落盘再续跑** |
| 单次 >80 只静默截断 | 服务端上限 | 脚本自动按 80 分批 |
| 全市场跑到一半挂掉 | 见上 | `--resume` 从 `partial_<date>.jsonl` 断点续跑 |
| `stock_quotes_list` 的 `open_amount` 恒 0 | List 协议不回该字段 | 只用它建代码表，不用它排行 |
| `stock_kline` 价格 ×10 | 定点数 | 只用 `stock_quotes` 取价 |

## 四、拿不到的字段（只能用悟道）

- `limitBuyAmount` / `limitBuyAmountAfter920` —— 9:25 定盘涨停价位委买额（判断排队强弱的核心）
- `finalSealAmount` / `sealAmount` —— 封单额
- `bidStrength` —— 市值归一化竞价强度（**可自算**：竞价额 ÷ 流通市值，东财 `f20` 有流通市值）
- `obs` —— 尾秒方向标签（late_bid_up / late_bid_down，**可自算**：`opening_rush > 0` 即 up）
- `bidAmountPercentile` —— 竞价额历史分位（**可自建**：tdx 每日落盘，20 个交易日后就有）
- 09:15→09:25 逐笔撮合演进（`stock_auction` 盘后只剩首笔）

**盘中的替代**：09:25 之后 `stock_quotes.handicap` 五档盘口里"买一价 == 涨停价"时，买一量 × 价格 = 实时封单额，比悟道那个 09:25 快照更新更准。

## 五、用法

```bash
# 观察池（≤80 只，最快）
python scripts/tdx_auction_fetch.py --codes 600519,300308,000001

# 从文件读（每行一个，支持 sh600519 / 600519,贵州茅台）
python scripts/tdx_auction_fetch.py --codes-file pool.txt

# 全市场（首次会建 data/tdx_universe.json 代码表缓存，之后复用）
python scripts/tdx_auction_fetch.py --universe

# 掉线后续跑
python scripts/tdx_auction_fetch.py --universe --resume

# 与悟道对账
python scripts/tdx_auction_fetch.py --universe --date 2026-10-23 \
    --out-dir data/auction_top_tdx --verify data/auction_top/2026-10-23.json
```

输出 schema 与 `data/auction_top/` **完全兼容**（缺失字段置 `null`，不拿 0 冒充），
额外多给 `dayTurnover` / `dayAmount` / `depth`(委比) / `min2Amount` / `shortTurnover`。

## 六、重要限制

**TDX 只保留"最近一个交易日"的竞价结果。** `--date` 只是文件名/标注，不能回补历史。
要回补更早的日期，只能用 `data/auction_hist/` 里已有的悟道快照。
所以从今天起**每天必须落一份**，攒够 20 天才有人人可用的历史分位。
