# -*- coding: utf-8 -*-
"""同花顺金融数据（fuyao.aicubes.cn）竞价快照接入 —— 09:25 竞价榜主数据源。

为什么换源：
  · 腾讯全市场快照在 09:25:0x 常还没刷新成交额，竞价额最大的若干只被
    `amount <= 0` 过滤掉（实测 2026-10-30 漏掉前 19 名，榜单最大值只有 0.9982 亿，
    而真实最大值是 6.3466 亿）。
  · 悟道 MCP 免费版 09:15-10:30 直接停服（code -32030），09:25 一定失败。
  同花顺 `auction/snapshot` 不受时段限制，直接给 auction_amount，
  且口径与悟道一致（2026-10-30 TOP12 逐只对到小数点后 4 位）。

只做「抓取 + 归一化」，不写任何文件；落盘由 server 侧负责。
"""
import concurrent.futures
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BASE = "https://fuyao.aicubes.cn"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "cache")
BATCH = 100            # auction/snapshot 单次 thscodes 上限
TICKER_PAGE = 10000    # meta/tickers/list limit 上限
WORKERS = 8            # 并发批数（56 批串行 28s，8 并发约 4s）
# 竞价三一选票池：先剔除流通市值超过该值（亿）的票，次新股除外。
# 依据 scripts/backtest_sanyi_cap.py（242 交易日）：剔除 >3000 亿后 t 由 2.05 升到 2.34、
# 累计收益 +144.4% → +181.6%，且原有命中一笔不丢（只新增 10 笔，均值 +3.72%）。
SANYI_CAP_MAX_YI = 3000.0
NEW_STOCK_DAYS = 365   # 次新股豁免：距上市不足该天数不按市值剔除


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if n == n else default    # 过滤 NaN


def load_key():
    """API Key 优先级：环境变量 HITHINK_FINANCE_API_KEY > config.json 的 hithink.apiKey。"""
    key = (os.environ.get("HITHINK_FINANCE_API_KEY") or "").strip()
    if key:
        return key
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        key = ((cfg.get("hithink") or {}).get("apiKey") or "").strip()
        if key:
            return key
    except (OSError, ValueError):
        pass
    # 本机通过 skillhub 安装的 CLI 会把 key 放在这里，便于本地调试
    try:
        path = os.path.join(os.environ.get("APPDATA", ""), "hithink-finance", "credentials.env")
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("HITHINK_FINANCE_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def api_get(path, params=None, key=None, timeout=30, retries=3):
    """GET 同花顺接口，返回信封里的 data。code!=0 抛 RuntimeError；4001 限流重试。"""
    key = key or load_key()
    if not key:
        raise RuntimeError("未配置同花顺 API Key（HITHINK_FINANCE_API_KEY 或 config.json hithink.apiKey）")
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={
            "X-api-key": key, "Accept": "application/json",
            "User-Agent": "CodexStock/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429 and attempt < retries - 1:
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(1.0 * (2 ** attempt))
                continue
            raise
    else:
        raise last or RuntimeError("同花顺请求失败")
    code = payload.get("code")
    if code == 4001 and attempt < retries - 1:
        time.sleep(1.5 * (2 ** attempt))
        return api_get(path, params, key, timeout, retries - attempt - 1)
    if code != 0:
        raise RuntimeError("同花顺返回 code=%s message=%s" % (code, payload.get("message")))
    return payload.get("data") or {}


def fetch_tickers(key=None, force=False):
    """A 股代码表 [{thscode, ticker, name}]，分页取尽。

    结果按自然日缓存在 data/cache/hithink_tickers.json（代码表一天只变几次，
    但每次拉要 8s，09:25 窗口里这 8s 很珍贵）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(CACHE_DIR, "hithink_tickers.json")
    if not force:
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f) or {}
            # 旧缓存没有 listDate（次新股豁免要用），视为过期重拉
            if (cached.get("date") == today and cached.get("tickers")
                    and cached["tickers"][0].get("listDate") is not None):
                return cached["tickers"]
        except (OSError, ValueError):
            pass
    out, offset = [], 0
    while True:
        data = api_get("/api/meta/tickers/list",
                       {"asset_type": "a-share", "limit": TICKER_PAGE, "offset": offset}, key)
        items = data.get("item") or []
        for it in items:
            thscode = str(it.get("thscode") or "").strip().upper()
            if not thscode:
                continue
            out.append({
                "thscode": thscode,
                "ticker": str(it.get("ticker") or thscode.split(".")[0]).strip(),
                "name": str(it.get("name") or "").strip(),
                "listDate": str(it.get("list_date") or "").strip()[:10],
            })
        if len(items) < TICKER_PAGE:
            break
        offset += len(items)
    if out:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"date": today, "tickers": out}, f, ensure_ascii=False)
        except OSError:
            pass
    return out


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def fetch_auction_rows(stage="live", key=None, tickers=None, workers=WORKERS):
    """全市场竞价快照（并发分批）。

    返回 (rows, meta)：rows 为接口原始 item 列表；meta = {phase, status, batches, total, returned}。
    stage="live" 竞价过程中取实时值，="final" 取终态；收盘后两者一致。
    """
    key = key or load_key()
    tickers = tickers if tickers is not None else fetch_tickers(key)
    codes = [t["thscode"] for t in tickers if t.get("thscode")]
    chunks = list(_chunks(codes, BATCH))

    def _one(chunk):
        data = api_get("/api/a-share/auction/snapshot",
                       {"thscodes": ",".join(chunk), "stage": stage}, key)
        return data.get("item") or [], data.get("auction_phase"), data.get("data_status")

    rows, phase, status = [], None, None
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for items, ph, st in pool.map(_one, chunks):
            rows.extend(items)
            phase = ph or phase
            status = st or status
    return rows, {"phase": phase, "status": status, "batches": len(chunks),
                  "total": len(codes), "returned": len(rows)}


def to_local_row(src):
    """同花顺 item → 本项目 auction_top 记录（字段名对齐 fetch_auction_snapshot_tencent）。

    单位：auction_volume 是「手」，float_market_cap 是「元」，auction_amount 是「元」。
    """
    code = str(src.get("ticker") or (src.get("thscode") or "").split(".")[0]).strip()
    return {
        "code": code.zfill(6) if code else "",
        "name": str(src.get("name") or "").strip(),
        "industry": "其他",
        "price": _num(src.get("auction_price")),
        "changePct": _num(src.get("auction_pct")),
        "open": _num(src.get("open_price")),
        "prevClose": _num(src.get("pre_close_price")),
        "auctionAmount": _num(src.get("auction_amount")) or 0.0,
        "auctionVolume": _num(src.get("auction_volume")),
        "turnover": _num(src.get("auction_turnover_pct")),
        "volumeRatio": _num(src.get("auction_volume_ratio")),
        "floatCap": _num(src.get("float_market_cap")),
        "totalCap": None,
        # 同花顺独有：竞价额占昨日成交额比例（%）
        "auctionYesterdayRatio": _num(src.get("auction_yesterday_ratio_pct")),
        "lastPrice": _num(src.get("last_price")),
        "thscode": src.get("thscode"),
        "src": "hithink",
    }


def list_dates(tickers=None, key=None):
    """{6位代码: 上市日 YYYY-MM-DD}，用于次新股豁免（来自 fetch_tickers 的 list_date）。"""
    tickers = tickers if tickers is not None else fetch_tickers(key)
    out = {}
    for t in tickers or []:
        ld = str(t.get("listDate") or "")[:10]
        if t.get("ticker") and ld:
            out[str(t["ticker"]).zfill(6)] = ld
    return out


def is_new_stock(code, dates, ref_date):
    """ref_date 距上市不足 NEW_STOCK_DAYS 天（次新股）。缺数据按「非次新」处理。"""
    ld = (dates or {}).get(str(code or "").zfill(6))
    if not ld:
        return False
    try:
        days = (datetime.strptime(str(ref_date)[:10], "%Y-%m-%d")
                - datetime.strptime(ld, "%Y-%m-%d")).days
    except ValueError:
        return False
    return 0 <= days < NEW_STOCK_DAYS


def sanyi_eligible(rec, ref_date=None, cap_max=None, dates=None):
    """三一选票池准入：流通市值 cap_max（亿）以内，次新股豁免。缺市值不误杀。"""
    cap_max = SANYI_CAP_MAX_YI if cap_max is None else cap_max
    if cap_max <= 0:
        return True
    cap = _num(rec.get("floatCap")) or _num(rec.get("circMcap"))
    if not cap:
        return True
    if cap / 1e8 <= cap_max:
        return True
    if dates is None:
        dates = list_dates()
    return is_new_stock(rec.get("code"), dates,
                        ref_date or datetime.now().strftime("%Y-%m-%d"))


def fetch_auction_stocks(top_n=None, stage="live", key=None, keep=None,
                         ref_date=None):
    """全市场竞价快照，按竞价额降序，可选截断 top_n。

    keep：额外保留谓词。竞价涨停但竞价额很小的票经常排在 top_n 之外，
    而「竞价涨停家数」是情绪判断的硬指标，漏一只就少一只——调用方传
    判涨停的函数把它强制保留（对齐封单扫描的 SEAL_KEEP_LIMIT_UP 语义）。

    返回 {"stocks":[...], "scanned":N, "meta":{...}}；调用方负责异常兜底。
    过滤：无代码/无名称/竞价额<=0（停牌或未参与竞价）的标的不算入榜。
    """
    key = key or load_key()
    tickers = fetch_tickers(key)
    rows, meta = fetch_auction_rows(stage=stage, key=key, tickers=tickers)
    stocks = []
    for item in rows:
        rec = to_local_row(item)
        if not rec["code"] or not rec["name"]:
            continue
        if not rec["auctionAmount"] or rec["auctionAmount"] <= 0:
            continue
        stocks.append(rec)
    stocks.sort(key=lambda s: s["auctionAmount"] or 0, reverse=True)
    # 全市场排名（截断前算，否则前排排名失真）：金额名次 + 换手名次
    # 「竞价三一」判据 = 金额前三 且 换手第一（见 reports/三一竞价/竞价三一回测_242日.md）
    # 选票阶段先剔除超大市值（SANYI_CAP_MAX_YI 亿，次新股除外）：大票永远做不到
    # 换手第一，却会占掉「金额前三」的名额，留在池里等于把真候选挤到第四名。
    # 242 日回测：剔除 >3000 亿后 t 2.05→2.34、累计 +144%→+182%，且原有命中一笔不丢。
    ref = ref_date or datetime.now().strftime("%Y-%m-%d")
    dates = list_dates(tickers)
    pool = []
    for s in stocks:
        if SANYI_CAP_MAX_YI <= 0 or sanyi_eligible(s, ref_date=ref, dates=dates):
            pool.append(s)
        else:
            s["sanyiOut"] = "cap"
    for i, s in enumerate(pool, 1):
        s["amtRankFull"] = i
    trf_key = lambda s: (s.get("freeTurnover") or s.get("turnover") or 0)
    for i, s in enumerate(sorted(pool, key=trf_key, reverse=True), 1):
        s["trfRankFull"] = i
    for s in pool:
        s["isSanyi"] = bool(s.get("amtRankFull", 99) <= 3 and s.get("trfRankFull") == 1)
    meta["sanyiCapMax"] = SANYI_CAP_MAX_YI
    meta["sanyiPool"] = len(pool)
    meta["sanyiExcluded"] = len(stocks) - len(pool)
    meta["matched"] = len(stocks)
    meta["kept"] = 0
    if top_n and len(stocks) > top_n:
        head = stocks[:top_n]
        if keep:
            seen = {s["code"] for s in head}
            for s in stocks[top_n:]:
                if s["code"] not in seen and keep(s):
                    head.append(s)
                    seen.add(s["code"])
            meta["kept"] = len(head) - top_n
        stocks = head
    return {"stocks": stocks, "scanned": len(rows), "meta": meta}
