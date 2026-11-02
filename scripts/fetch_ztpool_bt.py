# -*- coding: utf-8 -*-
"""回测用涨停池历史回补（同花顺 limit-up-pool，支持任意历史日）。

落 data/market/ztpool_bt/<date>.json：
    {"date","total","source","rows":[{code,name,lb,limitUpTime,sealMoney,
                                      maxSealMoney,openTimes,chg,limitUpReason,isSt,isNew}]}

换手率/流通市值不在这里取，回测时从日K反算（同花顺该接口不提供）。

    python scripts/fetch_ztpool_bt.py --days 200
    python scripts/fetch_ztpool_bt.py --start 2026-01-05 --end 2026-10-30
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "market", "ztpool_bt")
BASE = "https://fuyao.aicubes.cn"
CRED = os.path.expandvars(r"%APPDATA%\hithink-finance\credentials.env")


def load_key():
    key = os.environ.get("HITHINK_FINANCE_API_KEY")
    if key:
        return key.strip()
    if os.path.isfile(CRED):
        for ln in open(CRED, encoding="utf-8"):
            if ln.startswith("HITHINK_FINANCE_API_KEY="):
                return ln.split("=", 1)[1].strip()
    raise SystemExit("未找到 HITHINK_FINANCE_API_KEY")


def api_get(path, params, key, retries=3):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"X-api-key": key})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read())
            if body.get("code") == 0:
                return body.get("data") or {}
            if body.get("code") == 4001:
                time.sleep(1.5 * (i + 1))
                continue
            raise RuntimeError("code=%s msg=%s" % (body.get("code"), body.get("message")))
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.0 * (i + 1))
    return {}


def trade_days(days, end=None):
    """交易日历：取本地日K（data/laiqiu/kline/000001.json）的日期序列，
    再并入 data/auction_hist/ 里已知的更新交易日。
    不用 ak.tool_trade_date_hist_sina（本机 py_mini_racer 快照损坏会崩）。
    """
    ds = set()
    kp = os.path.join(ROOT, "data", "laiqiu", "kline", "000001.json")
    if os.path.isfile(kp):
        for r in json.load(open(kp, encoding="utf-8")):
            d = str(r.get("date"))[:10]
            if len(d) == 10:
                ds.add(d)
    ah = os.path.join(ROOT, "data", "auction_hist")
    if os.path.isdir(ah):
        for f in os.listdir(ah):
            if f.endswith(".json"):
                ds.add(f[:-5])
    ds = sorted(ds)
    if end:
        ds = [d for d in ds if d <= end]
    return ds[-days:] if days else ds


def to_rows(items):
    rows = []
    for it in items:
        t = str(it.get("ticker") or "")
        suffix = str(it.get("thscode") or "").split(".")[-1]
        code = t or suffix
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
            "isSt": bool(it.get("is_st")),
            "isNew": bool(it.get("is_new")),
            "lastPrice": it.get("last_price"),
            "src": "hithink",
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=200)
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    key = load_key()
    if args.start and args.end:
        cal = trade_days(0)
        dates = [d for d in cal if args.start <= d <= args.end]
    else:
        dates = trade_days(args.days, args.end or None)
    print("待回补 %d 个交易日：%s ~ %s" % (len(dates), dates[0], dates[-1]), flush=True)

    os.makedirs(OUT, exist_ok=True)
    ok = skip = empty = 0
    for i, d in enumerate(dates, 1):
        path = os.path.join(OUT, d + ".json")
        if os.path.isfile(path) and os.path.getsize(path) > 100 and not args.force:
            skip += 1
            continue
        ms = int(time.mktime(time.strptime(d, "%Y-%m-%d")) * 1000)
        try:
            data = api_get("/api/a-share/special-data/limit-up-pool",
                           {"date_ms": ms, "size": 200}, key)
        except Exception as e:
            print("  %s 失败 %r" % (d, e), flush=True)
            continue
        rows = to_rows(data.get("item") or [])
        if not rows:
            empty += 1
            print("  %s 空（非交易日？）" % d, flush=True)
            continue
        payload = {"date": d, "total": len(rows), "source": "hithink", "rows": rows}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        ok += 1
        if i % 20 == 0 or i == len(dates):
            print("  %d/%d %s 涨停%d只 累计 ok=%d skip=%d" % (
                i, len(dates), d, len(rows), ok, skip), flush=True)
        time.sleep(0.05)
    print("完成 ok=%d skip=%d empty=%d → %s" % (ok, skip, empty, OUT))


if __name__ == "__main__":
    main()
