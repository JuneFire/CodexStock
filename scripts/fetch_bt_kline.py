# -*- coding: utf-8 -*-
"""批量抓二板战法回测所需的日K（腾讯 qfq，带真实换手率），落 data/backtest_kline/。

    python scripts/fetch_bt_kline.py --start 20250101 --workers 8
"""
import argparse
import io
import contextlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import server  # noqa: E402

OUT = os.path.join(ROOT, "data", "backtest_kline")
ZTPOOL_HIST = os.path.join(ROOT, "data", "market", "ztpool_hist")
_lock = threading.Lock()


def cand_codes(lb_max=3):
    codes = []
    seen = set()
    for f in sorted(os.listdir(ZTPOOL_HIST)):
        if not f.endswith(".json"):
            continue
        for r in server._ztpool_rows(f[:-5]):
            c = r.get("code")
            if c and c not in seen and 1 <= (r.get("lb") or 0) <= lb_max:
                seen.add(c)
                codes.append(c)
    return codes


def fetch_one(code, start):
    path = os.path.join(OUT, code + ".json")
    if os.path.isfile(path) and os.path.getsize(path) > 200:
        return code, "cached"
    with contextlib.redirect_stderr(io.StringIO()):
        rows = server._fetch_kline_full(code, start)
    if not rows:
        return code, "fail"
    with _lock:
        os.makedirs(OUT, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
    return code, "ok(%d)" % len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20250101")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--lb-max", type=int, default=3)
    args = ap.parse_args()

    codes = cand_codes(args.lb_max)
    print("待抓 %d 只（lb<=%d）" % (len(codes), args.lb_max), flush=True)
    stat = {"cached": 0, "ok": 0, "fail": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, c, args.start): c for c in codes}
        for fu in as_completed(futs):
            code, res = fu.result()
            stat["cached" if res == "cached" else ("ok" if res.startswith("ok") else "fail")] += 1
            done += 1
            if done % 20 == 0 or res == "fail":
                print("  %d/%d  %s %s  %s" % (done, len(codes), code, res, stat), flush=True)
    print("完成", stat)


if __name__ == "__main__":
    main()
