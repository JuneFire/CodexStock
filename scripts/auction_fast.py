#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""竞价抢时段专用采集器：裸腾讯行情批量接口，全市场 1.8 秒。

为什么不用 tdx / akshare：
    tdx 全市场 70 批 + 冷启动 ≈ 6~10 分钟   -> 只能复盘用
    ak.stock_zh_a_spot_tx()        ≈ 27 秒  -> 项目现役路径，太慢
    东财 push2.eastmoney.com       -> 本机 http=000 根本连不上
    裸 https://qt.gtimg.cn/q=      -> 800 只/请求 × 8 并发 = 1.78 秒 ✅

09:25:00 集合竞价撮合完成，到 09:30:00 连续竞价开始前，腾讯的
「成交额」字段就等于竞价成交额、「今开」字段就等于竞价撮合价，
中间 5 分钟不会变。所以 09:25:02 拉一次，拿到的就是当日竞价全貌。

用法：
    # 全市场（默认 800 只/请求 × 8 并发）
    python scripts/auction_fast.py --universe

    # 只看前 30 名
    python scripts/auction_fast.py --universe --top 30

    # 观察池
    python scripts/auction_fast.py --codes 600519,300308,000001

    # 只测速不写文件
    python scripts/auction_fast.py --universe --bench

    # 与悟道快照对账
    python scripts/auction_fast.py --universe --date 2026-10-23 \\
        --verify data/auction_top/2026-10-23.json

注意：本脚本**只能在盘中/收盘后当天跑**。腾讯「成交额」是当日累计值，
过了 09:30 就变成「竞价+连续竞价」，不再是竞价额。要复盘历史竞价的
请用 scripts/tdx_auction_fetch.py（同样受限于「只保留最近一个交易日」）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tdx_auction_fetch import limit_pct_of, load_names, status_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_DIR = os.path.join(ROOT, "data", "auction_top_fast")
UNIVERSE_CACHE = os.path.join(ROOT, "data", "tdx_universe.json")

TX_URL = "https://qt.gtimg.cn/q="
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
BATCH = 800        # 实测：单请求 800 只不被截断，5577 只 = 7 个请求
WORKERS = 8        # 实测：8 并发最稳；12 并发提升有限，容易被限速

# 腾讯 qt.gtimg.cn 返回 88 个 ~ 分隔字段，下标含义（2026-10-25 实测标定）
F_NAME, F_CODE, F_PRICE, F_PREV, F_OPEN = 1, 2, 3, 4, 5
F_VOL, F_OUTER, F_INNER = 6, 7, 8
F_TS = 30                  # yyyymmddHHMMSS
F_CHG, F_CHGPCT, F_HIGH, F_LOW = 31, 32, 33, 34
F_AMOUNT_WAN = 37          # 成交额（万元）—— 09:25~09:30 期间 == 竞价成交额
F_TURNOVER = 38            # 换手率 % —— 09:25~09:30 期间 == 竞价换手
F_AMPLITUDE = 43
F_FLOAT_CAP = 44           # 流通市值（亿）
F_TOTAL_CAP = 45           # 总市值（亿）
F_PB = 46
F_LIMIT_UP, F_LIMIT_DOWN = 47, 48
F_VOL_RATIO = 49           # 量比


def prefix(code):
    if code[0] == "6":
        return "sh" + code
    if code[:2] in ("43", "83", "87", "92"):
        return "bj" + code
    return "sz" + code


def num(raw, idx):
    try:
        v = raw[idx].strip()
        return float(v) if v not in ("", "-") else None
    except (IndexError, ValueError):
        return None


def parse_one(block):
    """v_sh600519="1~贵州茅台~600519~..." -> dict"""
    f = block.split("~")
    if len(f) < 50:
        return None
    code = (f[F_CODE] or "").strip()
    if not code.isdigit():
        return None
    return {
        "code": code.zfill(6),
        "name": (f[F_NAME] or "").strip(),
        "price": num(f, F_PRICE),
        "pre_close": num(f, F_PREV),
        "open": num(f, F_OPEN),
        "vol": num(f, F_VOL),
        "ts": (f[F_TS] or "").strip(),
        "high": num(f, F_HIGH),
        "low": num(f, F_LOW),
        "amount_wan": num(f, F_AMOUNT_WAN),
        "turnover": num(f, F_TURNOVER),
        "amplitude": num(f, F_AMPLITUDE),
        "float_cap_yi": num(f, F_FLOAT_CAP),
        "total_cap_yi": num(f, F_TOTAL_CAP),
        "pb": num(f, F_PB),
        "limit_up": num(f, F_LIMIT_UP),
        "limit_down": num(f, F_LIMIT_DOWN),
        "vol_ratio": num(f, F_VOL_RATIO),
    }


def fetch_chunk(chunk, timeout=15.0):
    url = TX_URL + ",".join(prefix(c) for c in chunk)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": "https://gu.qq.com/"})
    body = urllib.request.urlopen(req, timeout=timeout).read()
    text = body.decode("gbk", "ignore")
    out = []
    for seg in text.split(";"):
        seg = seg.strip()
        if not seg.startswith("v_"):
            continue
        payload = seg.split('="', 1)
        if len(payload) != 2:
            continue
        rec = parse_one(payload[1].rstrip('"'))
        if rec:
            out.append(rec)
    return out


def fetch_all(codes, batch=BATCH, workers=WORKERS):
    """并发拉全市场，返回 (records, elapsed, timestamps)。"""
    chunks = [codes[i:i + batch] for i in range(0, len(codes), batch)]
    t0 = time.time()
    records, stamps = [], set()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_chunk, c) for c in chunks]
        for fu in futures:
            try:
                rows = fu.result()
            except Exception as exc:
                print("  某批失败：%s" % str(exc)[:120], flush=True)
                continue
            records.extend(rows)
            for r in rows:
                if r.get("ts"):
                    stamps.add(r["ts"])
    return records, time.time() - t0, sorted(stamps)


def to_record(q, names, trade_date, fetched_at, src_tag="tencent_fast"):
    code = q["code"]
    name = q.get("name") or names.get(code)
    prev_close = q.get("pre_close")
    open_price = q.get("open")
    amount_wan = q.get("amount_wan") or 0.0
    auction_amount = round(amount_wan * 10000.0, 2)          # 万元 -> 元
    change_pct = None
    if prev_close and open_price and prev_close > 0:
        change_pct = round((open_price / prev_close - 1) * 100, 2)
    limit_pct = limit_pct_of(code, name or "")
    float_cap = (q.get("float_cap_yi") or 0) * 1e8 or None   # 亿 -> 元
    status = status_of(change_pct, open_price, prev_close, limit_pct)
    # 自建强度口径：竞价额占流通市值万分比。悟道的 bidStrength 口径未公开，
    # 这里单独命名，避免和悟道数字混用。
    bid_strength_self = None
    if float_cap and auction_amount:
        bid_strength_self = round(auction_amount / float_cap * 10000, 2)
    auction_volume = round(auction_amount / open_price, 0) if open_price else None

    return {
        "code": code,
        "name": name,
        "industry": None,
        "price": open_price,
        "changePct": change_pct,
        "open": open_price,
        "prevClose": prev_close,
        "auctionAmount": auction_amount,
        "auctionVolume": auction_volume,
        "turnover": q.get("turnover"),          # 09:25~09:30 期间 == 竞价换手
        "volumeRatio": q.get("vol_ratio"),
        "floatCap": float_cap,
        "totalCap": (q.get("total_cap_yi") or 0) * 1e8 or None,
        "rank": None,
        "tradeDate": trade_date,
        "fetchedAt": fetched_at,
        "sealAmount": None,
        "sealIsLimitBid": None,
        "ps": None,
        "plt": None,
        "obp": None,                            # 腾讯不直接给尾秒涨幅
        "obs": None,
        "themes": [],
        "trf": None,
        "limitPct": limit_pct,
        "limitUpPrice": q.get("limit_up") or (
            round(prev_close * (1 + limit_pct / 100.0), 2) if prev_close else None),
        "bidStrengthSelf": bid_strength_self,
        "bidStrength": None,                    # 悟道口径，拿不到就留空，不冒充
        "isLimitUpOpen": status == "limit_up_open",
        "status": status,
        "market": {"sh": "sh", "sz": "sz", "bj": "bj"}.get(prefix(code)[:2]),
        "preLimitBuyAmount": None,
        "limitBuyAmountAfter920": None,
        "limitBuyAmount": None,
        "limitBuyAmountAfter920Available": None,
        "finalSealAmount": None,
        "auctionNetAmount": None,
        "src": src_tag,
        # 腾讯独有
        "quoteTs": q.get("ts"),
        "dayHigh": q.get("high"),
        "dayLow": q.get("low"),
        "amplitude": q.get("amplitude"),
        "pb": q.get("pb"),
        "limitDownPrice": q.get("limit_down"),
    }


def load_universe():
    if os.path.exists(UNIVERSE_CACHE):
        with open(UNIVERSE_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        codes = d.get("codes") or []
        if codes:
            print("代码表：%d 只（built_at=%s）" % (len(codes), d.get("built_at")))
            return codes
    raise SystemExit("缺 data/tdx_universe.json，先跑一次 "
                     "`python scripts/tdx_auction_fetch.py --universe`")


def parse_codes(raw):
    out = []
    for p in (raw or "").split(","):
        p = p.strip()
        if not p:
            continue
        p = p.split("(")[0].split(" ")[0]
        if p[:2].lower() in ("sh", "sz", "bj"):
            p = p[2:]
        if p.isdigit():
            out.append(p.zfill(6))
    return out


def verify(records, wudao_path, top_n=0):
    with open(wudao_path, encoding="utf-8") as f:
        blob = json.load(f)
    ref = blob.get("records") or blob.get("stocks") or []
    if top_n:
        ref = ref[:top_n]
    mine = {r["code"]: r for r in records}
    print("\n对账基准：%s（%d 条）" % (os.path.basename(wudao_path), len(ref)))
    miss = [r["code"] for r in ref if r["code"] not in mine]
    print("  缺失：%d 只 %s" % (len(miss), miss[:5]))
    mism = [r["code"] for r in ref
            if r["code"] in mine and r["status"] != mine[r["code"]]["status"]]
    print("  status 不一致：%d 只 %s" % (len(mism), mism[:5]))
    devs = []
    for r in ref:
        m = mine.get(r["code"])
        if not m or not r.get("auctionAmount"):
            continue
        devs.append(abs(m["auctionAmount"] - r["auctionAmount"]) / r["auctionAmount"] * 100)
    if devs:
        print("  竞价额偏差：最大 %.4f%% / 均值 %.4f%%" % (max(devs), sum(devs) / len(devs)))
    print("  Top25 排序一致：%s" % (
        [r["code"] for r in ref[:25]] == [r["code"] for r in records[:25]]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="竞价抢时段采集（腾讯裸接口，全市场 ~1.8s）")
    ap.add_argument("--universe", action="store_true", help="全市场")
    ap.add_argument("--codes", help="逗号分隔，如 600519,300308")
    ap.add_argument("--codes-file", help="每行一个代码")
    ap.add_argument("--top", type=int, default=0, help="只保留竞价额前 N 名（0=全留）")
    ap.add_argument("--date", default=None, help="标注用的交易日 YYYY-MM-DD")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--bench", action="store_true", help="只测速不写文件")
    ap.add_argument("--verify", default=None, help="与参考 json 对账")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)

    now = datetime.now()
    trade_date = args.date or now.strftime("%Y-%m-%d")

    if args.universe:
        codes = load_universe()
    elif args.codes:
        codes = parse_codes(args.codes)
    elif args.codes_file:
        raw = open(args.codes_file, encoding="utf-8").read()
        codes = parse_codes(",".join(raw.split()))
    else:
        raise SystemExit("给 --universe / --codes / --codes-file 之一")

    print("待拉 %d 只（%d 只/请求 × %d 并发）" % (len(codes), args.batch, args.workers))
    raw, elapsed, stamps = fetch_all(codes, args.batch, args.workers)
    print("拿到 %d 只，耗时 %.2fs" % (len(raw), elapsed))
    if stamps:
        print("行情时间戳：%s%s" % (", ".join(stamps[:3]),
                                   " ..." if len(stamps) > 3 else ""))

    if args.bench:
        return 0

    names = load_names()
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
    records = [to_record(q, names, trade_date, fetched_at) for q in raw]
    records = [r for r in records if r["auctionAmount"] and r["auctionAmount"] > 0]
    records.sort(key=lambda r: r["auctionAmount"], reverse=True)
    for i, r in enumerate(records, 1):
        r["rank"] = i
    if args.verify:
        verify(records, args.verify, args.top)
    if args.top:
        records = records[:args.top]

    if args.no_save:
        pass
    else:
        os.makedirs(args.out_dir, exist_ok=True)
        path = os.path.join(args.out_dir, "%s.json" % trade_date)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"tradeDate": trade_date, "fetchedAt": fetched_at,
                       "source": "tencent_qt_gtimg_fast", "records": records},
                      f, ensure_ascii=False, indent=1)
        print("已写出 %s（%d 条）" % (path, len(records)))

    total = sum(r["auctionAmount"] for r in records)
    print("\n竞价额合计 %.2f 亿　前 15 名：" % (total / 1e8))
    print("%-8s %-10s %10s %8s %8s %8s %8s" %
          ("代码", "名称", "竞价额(亿)", "涨幅%", "换手%", "量比", "状态"))
    for r in records[:15]:
        print("%-8s %-10s %10.3f %8.2f %8s %8s %8s" % (
            r["code"], (r["name"] or "")[:10], r["auctionAmount"] / 1e8,
            r["changePct"] if r["changePct"] is not None else 0,
            "%.2f" % r["turnover"] if r["turnover"] is not None else "-",
            "%.2f" % r["volumeRatio"] if r["volumeRatio"] is not None else "-",
            r["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
