# -*- coding: utf-8 -*-
"""批量抓二板战法回测所需的日K（前复权，带换手率），落 data/backtest_kline/。

默认走东财 push2his（0.08s/只，16 并发），失败回落腾讯 akshare（约 1.5s/只）。
换手率统一存小数比例（0.0163 = 1.63%），与腾讯源一致。

    python scripts/fetch_bt_kline.py --start 20250101 --workers 16
    python scripts/fetch_bt_kline.py --source tx --workers 8
"""
import argparse
import contextlib
import io
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import server  # noqa: E402

OUT = os.path.join(ROOT, "data", "backtest_kline")
POOL_DIRS = (os.path.join(ROOT, "data", "market", "ztpool_bt"),
             os.path.join(ROOT, "data", "market", "ztpool_hist"))
_lock = threading.Lock()
EM_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
          "secid={m}.{code}&fields1=f1,f2,f3,f4,f5,f6&"
          "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
          "klt=101&fqt=1&beg={beg}&end=20500101")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def pool_rows(date_str):
    """优先 ztpool_bt（同花顺，200 天），没有再用 ztpool_hist（东财，14 天）。"""
    p = os.path.join(POOL_DIRS[0], date_str + ".json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f).get("rows") or []
        except Exception:
            return []
    return server._ztpool_rows(date_str)


def pool_dates():
    ds = set()
    for d in POOL_DIRS:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".json"):
                    ds.add(f[:-5])
    return sorted(ds)


def cand_codes(lb_max=3):
    codes = []
    seen = set()
    for d in pool_dates():
        for r in pool_rows(d):
            c = r.get("code")
            if c and c not in seen and 1 <= (r.get("lb") or 0) <= lb_max:
                seen.add(c)
                codes.append(c)
    return codes


def fetch_em(code, start):
    market = "1" if code.startswith(("6", "688", "605", "5")) else "0"
    url = EM_URL.format(m=market, code=code, beg=start)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    lines = ((d.get("data") or {}).get("klines") or [])
    rows = []
    for ln in lines:
        p = ln.split(",")
        if len(p) < 11:
            continue
        close = _num(p[2])
        if close is None:
            continue
        turn = _num(p[10])
        rows.append({
            "date": p[0], "open": _num(p[1]), "close": close,
            "high": _num(p[3]), "low": _num(p[4]),
            "volume": _num(p[5]), "amount": _num(p[6]),
            "turnover": (turn / 100.0) if turn is not None else None,
        })
    return rows


def fetch_tx(code, start):
    with contextlib.redirect_stderr(io.StringIO()):
        return server._fetch_kline_full(code, start)


def fetch_one(code, start, source):
    path = os.path.join(OUT, code + ".json")
    if os.path.isfile(path) and os.path.getsize(path) > 200:
        return code, "cached"
    rows = []
    if source in ("em", "auto"):
        try:
            rows = fetch_em(code, start)
        except Exception:
            rows = []
    if not rows and source in ("tx", "auto"):
        rows = fetch_tx(code, start)
    if not rows:
        return code, "fail"
    with _lock:
        os.makedirs(OUT, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        os.replace(tmp, path)
    return code, "ok(%d)" % len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20250101")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--lb-max", type=int, default=3)
    ap.add_argument("--source", default="auto", choices=("em", "tx", "auto"))
    ap.add_argument("--retry-fail", action="store_true")
    args = ap.parse_args()

    codes = cand_codes(args.lb_max)
    todo = codes
    if args.retry_fail:
        todo = [c for c in codes
                if not (os.path.isfile(os.path.join(OUT, c + ".json"))
                        and os.path.getsize(os.path.join(OUT, c + ".json")) > 200)]
    print("候选 %d 只（lb<=%d），待抓 %d 只" % (len(codes), args.lb_max, len(todo)), flush=True)
    t0 = time.time()
    stat = {"cached": 0, "ok": 0, "fail": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, c, args.start, args.source): c for c in todo}
        for fu in as_completed(futs):
            code, res = fu.result()
            stat["cached" if res == "cached" else ("ok" if res.startswith("ok") else "fail")] += 1
            done += 1
            if done % 200 == 0:
                el = time.time() - t0
                print("  %d/%d  %s  %s  已用 %.0fs（每只 %.2fs）" % (
                    done, len(todo), code, stat, el, el / done), flush=True)
    print("完成 %s  总耗时 %.0fs" % (stat, time.time() - t0))
    if stat["fail"]:
        fails = [c for c in todo
                 if not os.path.isfile(os.path.join(OUT, c + ".json"))]
        print("失败/缺失 %d 只，例：%s" % (len(fails), fails[:20]))


if __name__ == "__main__":
    main()
