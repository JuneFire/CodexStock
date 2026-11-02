# -*- coding: utf-8 -*-
"""市场环境开关：上证指数 MA20 位置 + 昨日涨停家数。

用途：竞价窗口页顶部「环境」横幅，以及「三一」信号的开关。

为什么要这两个数（242 个交易日 / 46 笔三一交易回测，D 日开盘买 → 次日收盘卖）：
    全部三一                          n=46  +3.14%  中位 +2.96%  胜率 67.4%  t=2.05
    上证 ≥ MA20                       n=29  +5.41%  中位 +5.44%  胜率 75.9%  t=2.63
    上证 < MA20                       n=17  ≈ -0.7%
    上证 ≥ MA60 / < MA60              +3.45% / +2.66%（MA60 无区分度）
    昨收≥MA20 且 昨日涨停≥60            n=26  +5.24%  胜率 73.1%  t=2.35
    昨收≥MA20 且 昨日涨停≥80            n=14  +5.84%  胜率 78.6%  t=1.86
    昨收≥MA20 且 昨日涨停≥100           n=9   +5.80%  胜率 66.7%  t=1.22

结论：开关是「上证在 MA20 上方」；昨日涨停家数只披露、不参与开关（阈值越高 t 越低＝过热）。

注意口径：
- 09:25 时今日还没收盘，所以上证用 **上一交易日收盘**，涨停家数用 **上一交易日**。
- 涨停家数取同花顺 limit-up-pool（`data/market/ztpool_bt/<date>.json`），
  与回测 `scripts/backtest_sanyi.py` 完全同源；该接口只保留 250 个交易日。
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_DIR = os.path.join(ROOT, "data", "index_kline")
ZTPOOL_DIR = os.path.join(ROOT, "data", "market", "ztpool_bt")
INDEX_NAME = "sh000001"
# 三一开关阈值：上证收盘距 MA20 不低于该百分比（-1.0 = 允许小幅跌破 1%）。
# 依据 scripts/analyze_sanyi_ma.py（242 日 / 56 笔）：距 MA20 ≥ -1% 41 笔 +4.61% 胜率 70.7%，
# < -1% 15 笔 -0.49% 胜率 46.7%（开盘卖胜率仅 33.3%）。
SANYI_DEV_MIN = -1.0
INDEX_LABEL = "上证指数"
EM_IDX_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
              "?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6"
              "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
              "&klt=101&fqt=1&beg=20230101&end=20500101")
_CACHE = {"key": None, "val": None}


def _load_hithink_key():
    try:
        from hithink_feed import load_key
    except Exception:
        try:
            from backend.hithink_feed import load_key
        except Exception:
            return ""
    try:
        return load_key()
    except Exception:
        return ""


# ---------------- 上证指数日K ----------------

def _read_index_cache():
    path = os.path.join(INDEX_DIR, INDEX_NAME + ".json")
    if not os.path.isfile(path):
        return []
    try:
        return json.load(open(path, encoding="utf-8")) or []
    except Exception:
        return []


def _write_index_cache(bars):
    os.makedirs(INDEX_DIR, exist_ok=True)
    path = os.path.join(INDEX_DIR, INDEX_NAME + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bars, f, ensure_ascii=False)
    os.replace(tmp, path)


def _fetch_index_bars():
    req = urllib.request.Request(EM_IDX_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
    klines = ((body or {}).get("data") or {}).get("klines") or []
    bars = []
    for k in klines:
        p = k.split(",")
        if len(p) < 7:
            continue
        bars.append({
            "date": p[0], "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "volume": float(p[5]), "amount": float(p[6]),
            "turnover": (float(p[10]) / 100) if len(p) > 10 else None,
        })
    return bars


def index_bars(force=False):
    """上证指数日K（升序）。缓存一天：文件最后日期已是今天就不再重抓。"""
    cache = _read_index_cache()
    today = datetime.now().strftime("%Y-%m-%d")
    if not force and cache and str(cache[-1].get("date"))[:10] >= today:
        return cache
    try:
        bars = _fetch_index_bars()
    except Exception as exc:
        print("[env] 上证指数日K拉取失败，用本地缓存: %r" % (exc,), flush=True)
        return cache
    if bars:
        _write_index_cache(bars)
        return bars
    return cache


def _ma(bars, i, n):
    """bars[i] 为末位的 n 日均线（含 i）。"""
    if i + 1 < n:
        return None
    seg = [b["close"] for b in bars[i + 1 - n:i + 1]]
    return sum(seg) / len(seg)


# ---------------- 昨日涨停家数 ----------------

def _count_from_file(date_str):
    path = os.path.join(ZTPOOL_DIR, date_str + ".json")
    if not os.path.isfile(path):
        return None
    try:
        o = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    rows = o.get("rows") or []
    return len(rows) or o.get("total") or None


def _fetch_ztpool(date_str):
    """同花顺 limit-up-pool（无调用次数限制）→ 落 ztpool_bt。返回家数或 None。"""
    key = _load_hithink_key()
    if not key:
        return None
    ms = int(time.mktime(time.strptime(date_str, "%Y-%m-%d")) * 1000)
    url = ("https://fuyao.aicubes.cn/api/a-share/special-data/limit-up-pool?"
           + urllib.parse.urlencode({"date_ms": ms, "size": 200}))
    try:
        req = urllib.request.Request(url, headers={"X-api-key": key})
        with urllib.request.urlopen(req, timeout=25) as r:
            body = json.loads(r.read())
    except Exception as exc:
        print("[env] 涨停池 %s 拉取失败: %r" % (date_str, exc), flush=True)
        return None
    if (body or {}).get("code") != 0:
        return None
    items = ((body or {}).get("data") or {}).get("item") or []
    rows = []
    for it in items:
        code = str(it.get("ticker") or str(it.get("thscode") or "").split(".")[-1] or "")
        if not code:
            continue
        rows.append({
            "code": code, "name": it.get("name"),
            "lb": int(it.get("continue_day_cnt") or 1),
            "limitUpTime": it.get("limit_up_time"),
            "sealMoney": it.get("seal_money"),
            "maxSealMoney": it.get("max_seal_money"),
            "openTimes": it.get("open_times"),
            "chg": it.get("price_change_ratio_pct"),
            "limitUpReason": it.get("limit_up_reason"),
            "isSt": bool(it.get("is_st")), "isNew": bool(it.get("is_new")),
            "lastPrice": it.get("last_price"), "src": "hithink",
        })
    if not rows:
        return None
    os.makedirs(ZTPOOL_DIR, exist_ok=True)
    path = os.path.join(ZTPOOL_DIR, date_str + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "total": len(rows), "source": "hithink",
                   "rows": rows}, f, ensure_ascii=False)
    os.replace(tmp, path)
    return len(rows)


def limit_up_count(date_str, allow_fetch=True):
    """某交易日涨停家数（同花顺涨停池口径）。本地优先，缺失时拉一次并落盘。"""
    n = _count_from_file(date_str)
    if n:
        return n
    if allow_fetch:
        return _fetch_ztpool(date_str)
    return None


# ---------------- 组装环境 ----------------

def env(date_str=None, allow_fetch=True):
    """`date_str` = 竞价日 D。返回环境开关（上证用 D-1 收盘，涨停用 D-1）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    date_str = date_str or today
    bars = index_bars()
    dates = [str(b.get("date"))[:10] for b in bars]
    prev = None
    prev_i = None
    for i, d in enumerate(dates):
        if d < date_str:
            prev, prev_i = d, i
        else:
            break
    if prev is None:
        return {"ok": False, "error": "上证指数日K不足", "date": date_str}

    close = bars[prev_i]["close"]
    ma5 = _ma(bars, prev_i, 5)
    ma10 = _ma(bars, prev_i, 10)
    ma20 = _ma(bars, prev_i, 20)
    ma60 = _ma(bars, prev_i, 60)
    ma20_prev5 = _ma(bars, prev_i - 5, 20) if prev_i >= 5 else None
    above20 = bool(ma20 and close > ma20)
    dev20 = round((close / ma20 - 1) * 100, 2) if ma20 else None
    # 均线空头排列 MA5 < MA10 < MA20：下跌刚开始，三一一票否决
    # （242 日/56 笔回测：该形态 14 笔 次收 -1.44%、次开 -2.21%、开盘胜率仅 28.6%）
    bear_stack = bool(ma5 and ma10 and ma20 and ma5 < ma10 < ma20)
    bull_stack = bool(ma5 and ma10 and ma20 and ma5 > ma10 > ma20)
    # 三一开关：距 MA20 不低于 -1%（不是「站上 MA20」）
    # 242 日/56 笔回测：距 MA20 >= -1% 41 笔 +4.61%（胜率 70.7%）vs < -1% 15 笔 -0.49%，
    # 且累计 +188.9% > 全部 56 笔的 +181.6%；单看「站上 MA20」只有 33 笔 +5.31%，
    # 白白丢掉「小跌破」那一批的 +13.8%。MA5/MA10 单独看无区分度（差 0.9pp，t<2.2）。
    on = bool(dev20 is not None and dev20 >= SANYI_DEV_MIN and not bear_stack)
    if bear_stack:
        note = "上证均线空头排列（MA5<MA10<MA20），三一一票否决（回测 14 笔次收 -1.44%）"
    elif dev20 is not None and dev20 < SANYI_DEV_MIN:
        note = "上证距 MA20 %.2f%%，跌破超过 %.0f%%，三一停用（回测 15 笔均值 -0.49%%）" % (
            dev20, abs(SANYI_DEV_MIN))
    else:
        note = "上证距 MA20 %+.2f%%，三一可用" % (dev20 or 0)
    zt = limit_up_count(prev, allow_fetch=allow_fetch)
    return {
        "ok": True,
        "date": date_str,                  # 竞价日
        "asOf": prev,                      # 环境数据所属交易日（D-1）
        "index": INDEX_NAME,
        "indexName": INDEX_LABEL,
        "close": round(close, 2),
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "aboveMa20": above20,
        "aboveMa60": bool(ma60 and close > ma60),
        "devMa20Pct": dev20,
        "ma20Slope5Pct": round((ma20 / ma20_prev5 - 1) * 100, 2) if ma20 and ma20_prev5 else None,
        "bearStack": bear_stack,
        "bullStack": bull_stack,
        "prevLimitUp": zt,
        "prevLimitUpDate": prev,
        "sanyiOn": on,
        "sanyiDevMin": SANYI_DEV_MIN,
        "sanyiBearVeto": bear_stack,
        "sanyiNote": note,
    }


def env_cached(date_str=None):
    """同一分钟内复用，避免页面刷新反复读盘/拉网。"""
    key = date_str or datetime.now().strftime("%Y-%m-%d")
    now = time.time()
    c = _CACHE
    if c["key"] == key and c["val"] and now - c["val"].get("_ts", 0) < 60:
        return {k: v for k, v in c["val"].items() if k != "_ts"}
    val = env(key)
    if val.get("ok"):
        val["_ts"] = now
        _CACHE["key"], _CACHE["val"] = key, val
    return {k: v for k, v in val.items() if k != "_ts"}


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(env(d), ensure_ascii=False, indent=2))
