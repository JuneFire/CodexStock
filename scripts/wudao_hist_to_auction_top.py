#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把悟道竞价历史缓存（data/auction_hist/*.json）转成本地竞价榜归档（data/auction_top/*.json）。

为什么需要这层转换：
  · data/auction_hist 是 scripts/auction_hist_pull.py 拉下来的悟道原始行（字段是悟道命名：
    bidAmount / changeRate / preClose / circMcap / prevStreak ...）。
  · 后端 /api/auction_top/dynamic 读的是 data/auction_top/<date>.json，字段是本地命名
    （auctionAmount / changePct / prevClose / floatCap / sealAmount ...）。
  · 两边命名不同，直接拷过去页面会全空，所以必须做一次字段映射。

除标准字段外，额外透传悟道独有的好东西（本地抓取拿不到的）：
  ps=prevStreak 几天几板 / plt=prevLimitUpType 昨日板型 / obp=openBidPct 尾秒%
  obs=openBidSignal 尾秒信号 / themes 题材 / trf=turnoverRateF 自由流通换手%
  preLimitBuyAmount=09:20 前涨停委买额 / limitBuyAmountAfter920=09:20 后 / limitBuyAmount=09:25 终值

用法：
    python scripts/wudao_hist_to_auction_top.py                 # 全部转换
    python scripts/wudao_hist_to_auction_top.py --days 30       # 只转最近 30 个缓存日
    python scripts/wudao_hist_to_auction_top.py --date 2026-10-15
    python scripts/wudao_hist_to_auction_top.py --dry-run
    python scripts/wudao_hist_to_auction_top.py --force        # 覆盖已有归档
"""
import argparse
import glob
import io
import json
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(ROOT, "data", "auction_hist")
OUT_DIR = os.path.join(ROOT, "data", "auction_top")


def num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def convert_row(src, rank, date_str):
    """悟道行 → 本地竞价榜行。"""
    prev_close = num(src.get("preClose"))
    price = num(src.get("price"))
    bid_amount = num(src.get("bidAmount")) or 0.0
    limit_buy = num(src.get("limitBuyAmount"))
    final_seal = num(src.get("finalSealAmount"))
    circ = num(src.get("circMcap"))
    turnover = num(src.get("turnoverRate"))

    # 封单额列必须用 09:25 口径：limitBuyAmount（09:25 涨停价委买额终值）。
    # 不能用 finalSealAmount——那是「盘后最后一次落库」的封单截面，
    # 盘中开过板的票会得到极小值（实例：慈文传媒 09:25 委买 2.48亿，finalSealAmount 仅 0.01亿）。
    seal = limit_buy

    change_pct = num(src.get("changeRate"))

    return {
        # —— 后端标准字段（本地命名）——
        "code": src.get("code"),
        "name": src.get("name"),
        "industry": src.get("industry") or "其他",
        "price": price,
        "changePct": change_pct,
        "open": price,
        "prevClose": prev_close,
        "auctionAmount": bid_amount,
        "auctionVolume": num(src.get("bidVolHands")),
        "turnover": turnover,
        "volumeRatio": num(src.get("volumeRatio")),
        "floatCap": circ,
        "totalCap": None,                       # 悟道不提供总市值
        "rank": rank,
        "tradeDate": date_str,
        "fetchedAt": "%s 09:25:30" % date_str,
        "sealAmount": seal,
        # —— 悟道独有的增强字段（本地抓取拿不到）——
        "ps": src.get("prevStreak"),
        "plt": src.get("prevLimitUpType") or None,
        "obp": num(src.get("openBidPct")),
        "obs": src.get("openBidSignal") or None,
        "themes": src.get("themes") or [],
        "trf": num(src.get("turnoverRateF")),
        "limitPct": num(src.get("limitPct")),
        "limitUpPrice": num(src.get("limitUpPrice")),
        "bidStrength": num(src.get("bidStrength")),
        "isLimitUpOpen": bool(src.get("isLimitUpOpen")),
        "status": src.get("status"),
        "market": src.get("market"),
        # 涨停委买额三时点（悟道口径：09:20 前 / 09:20 后 / 09:25 终值）
        "preLimitBuyAmount": num(src.get("preLimitBuyAmount")),
        "limitBuyAmountAfter920": num(src.get("limitBuyAmountAfter920")),
        "limitBuyAmount": limit_buy,
        "limitBuyAmountAfter920Available": src.get("limitBuyAmountAfter920Available"),
        "finalSealAmount": final_seal,
        "auctionNetAmount": num(src.get("auctionNetAmount")),
        "src": "wudao",
    }


def convert_file(path, force=False, dry=False):
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(OUT_DIR, base + ".json")
    if os.path.isfile(out) and not force:
        return base, "skip(已存在)"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return base, "err(读取失败 %r)" % (exc,)

    rows = data.get("rows") or []
    if not rows:
        return base, "skip(无 rows)"
    trade_date = base
    records = [convert_row(r, i + 1, trade_date) for i, r in enumerate(rows)]
    # 按竞价额降序，rank 重排（悟道原 rank 是按 bidAmount 排的，保险起见重算）
    records.sort(key=lambda r: r.get("auctionAmount") or 0, reverse=True)
    for i, r in enumerate(records, start=1):
        r["rank"] = i

    if dry:
        return base, "dry(%d 行)" % len(records)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"date": trade_date, "fetchedAt": "%s 09:25:30" % trade_date,
                   "source": "wudao_auction_hist", "records": records},
                  f, ensure_ascii=False, indent=1)
    return base, "ok(%d 行)" % len(records)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="只转最近 N 个缓存日（0=全部）")
    ap.add_argument("--date", help="只转指定日期 YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="覆盖已有归档")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.date:
        files = [os.path.join(HIST_DIR, args.date + ".json")]
        if not os.path.isfile(files[0]):
            raise SystemExit("缓存不存在: %s" % files[0])
    else:
        files = sorted(glob.glob(os.path.join(HIST_DIR, "*.json")))
        if args.days:
            files = files[-args.days:]
    print("待处理 %d 个缓存文件 → %s" % (len(files), OUT_DIR))

    stat = {}
    for p in files:
        base, msg = convert_file(p, force=args.force, dry=args.dry_run)
        stat[msg.split("(")[0]] = stat.get(msg.split("(")[0], 0) + 1
    for k, v in sorted(stat.items()):
        print("  %-12s %d" % (k, v))
    print("完成")


if __name__ == "__main__":
    main()
