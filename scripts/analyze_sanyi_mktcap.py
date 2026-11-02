# -*- coding: utf-8 -*-
"""竞价三一：剔除大市值个股后的回测。

用户口径（m03241）：选三一票时把 500 亿市值以上的剔除，次新股除外。

数据来源（均本地/已缓存，零配额）：
- 三一交易明细：reports/backtest_sanyi.json 的 a 线（242 交易日）
- 流通市值：data/auction_hist/<D>.json 的 circMcap（悟道原始字段，单位元）
- 自由流通市值：同上，用 prevClose × auctionVolume × 10000 / turnoverRateF 反推
- 上市日期：data/cache/ticker_list_date.json（同花顺 meta/tickers/list 的 list_date）
  次新股判据：D 日距上市不足 365 天

口径：D 日开盘买 → 次日收盘卖（ret_c）/ 次日开盘卖（ret_o）。
      三一 = 当日竞价金额前三 且 竞价换手第一（全市场）。
"""
import json
import os
import statistics as stt
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAN = os.path.join(ROOT, "reports", "backtest_sanyi.json")
AU = os.path.join(ROOT, "data", "auction_hist")
TICK = os.path.join(ROOT, "data", "cache", "ticker_list_date.json")
NEW_STOCK_DAYS = 365


def _num(v):
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return f if f == f else None
    except Exception:
        return None


def load_tickers():
    if not os.path.isfile(TICK):
        return {}
    return json.load(open(TICK, encoding="utf-8"))


def load_auction_rows(date_str):
    """该日悟道合并池的 {code: row}（含 circMcap / turnoverRateF / prevClose）。"""
    path = os.path.join(AU, date_str + ".json")
    if not os.path.isfile(path):
        return {}
    try:
        o = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    return {str(r.get("code")): r for r in (o.get("rows") or []) if r.get("code")}


def free_cap_yi(a):
    """自由流通市值（亿元）= prevClose × auctionVolume × 10000 / turnoverRateF。

    auctionVolume 单位「手」，turnoverRateF 是百分数（0.26 = 0.26%），
    1 手 = 100 股 → prevClose × vol × 100 是成交额（元），再 /(trf/100)。
    """
    px = _num(a.get("preClose"))
    vol = _num(a.get("auctionVolume")) or _num(a.get("bidVolHands"))
    trf = _num(a.get("turnoverRateF"))
    if not (px and vol and trf and trf > 0):
        return None
    return px * vol * 100 / (trf / 100.0) / 1e8


def attached(rows, tickers):
    """给每笔三一补 流通市值 / 自由流通市值 / 是否次新。"""
    cache = {}
    out = []
    for r in rows:
        d = r["date"]
        if d not in cache:
            cache[d] = load_auction_rows(d)
        a = cache[d].get(str(r["code"])) or {}
        circ = _num(a.get("circMcap"))
        r = dict(r)
        r["circYi"] = round(circ / 1e8, 1) if circ else None
        f = free_cap_yi(a)
        r["freeYi"] = round(f, 1) if f else None
        ld = (tickers.get(str(r["code"])) or {}).get("listDate")
        r["listDate"] = ld
        if ld:
            try:
                days = (datetime.strptime(d, "%Y-%m-%d")
                        - datetime.strptime(str(ld)[:10], "%Y-%m-%d")).days
                r["listedDays"] = days
                r["isNew"] = days < NEW_STOCK_DAYS
            except Exception:
                r["listedDays"], r["isNew"] = None, False
        else:
            r["listedDays"], r["isNew"] = None, False
        out.append(r)
    return out


def stat(name, rs, key="ret_c"):
    v = [x[key] for x in rs if x.get(key) is not None]
    n = len(v)
    if n < 3:
        print("  %-34s n=%2d  %s" % (name, n, " ".join("%+.1f" % x for x in v)))
        return None
    m = stt.mean(v)
    sd = stt.pstdev(v)
    t = m / (sd / n ** 0.5) if sd else 0
    cum = sum(v)
    print("  %-34s n=%3d 均值%+6.2f%% 中位%+6.2f%% 胜率%5.1f%% t=%+5.2f 累计%+7.1f%%"
          % (name, n, m, stt.median(v), sum(1 for x in v if x > 0) / n * 100, t, cum))
    return {"n": n, "mean": m, "median": stt.median(v),
            "win": sum(1 for x in v if x > 0) / n * 100, "t": t, "cum": cum}


def main():
    days = json.load(open(SAN, encoding="utf-8"))["a"]
    tri = [r for r in days if r.get("wide") or r.get("tight")]
    tickers = load_tickers()
    rows = attached(tri, tickers)

    miss_c = [r for r in rows if r.get("circYi") is None]
    print("三一共 %d 笔；流通市值缺失 %d 笔；次新股 %d 笔"
          % (len(rows), len(miss_c), sum(1 for r in rows if r.get("isNew"))))
    print()

    print("=== 逐笔（按流通市值降序） ===")
    print("  %-11s %-7s %-7s %9s %9s %8s %s"
          % ("日期", "代码", "名称", "流通市值亿", "自由流通亿", "上市天数", "次日收盘"))
    for r in sorted(rows, key=lambda x: -(x.get("circYi") or 0)):
        print("  %-11s %-7s %-7s %9s %9s %8s %+7.2f%%%s"
              % (r["date"], r["code"], (r["name"] or "")[:5],
                 r.get("circYi"), r.get("freeYi"), r.get("listedDays"),
                 r["ret_c"], "  次新" if r.get("isNew") else ""))

    print()
    print("=== 剔除大市值后的效果（次新股豁免） ===")
    res = {}
    for th in (0, 200, 300, 500, 800, 1000):
        if th == 0:
            keep = list(rows)
            lab = "全部（不剔除）"
        else:
            keep = [r for r in rows if r.get("isNew") or r.get("circYi") is None
                    or r["circYi"] <= th]
            lab = "流通市值≤%d亿" % th
        res["c%d" % th] = {"ret_c": stat(lab + " · 次日收盘", keep, "ret_c"),
                           "ret_o": stat("               —— 次日开盘", keep, "ret_o")}

    print()
    print("=== 自由流通市值口径（对照） ===")
    for th in (300, 500):
        keep = [r for r in rows if r.get("isNew") or (r.get("freeYi") or 0) <= th
                or r.get("freeYi") is None]
        stat("自由流通≤%d亿 · 次日收盘" % th, keep, "ret_c")

    print()
    print("=== 市值分档（看是不是单调） ===")
    bands = [(0, 50), (50, 100), (100, 200), (200, 300), (300, 500), (500, 99999)]
    for lo, hi in bands:
        sub = [r for r in rows if r.get("circYi") is not None and lo <= r["circYi"] < hi]
        stat("流通市值 %d~%d亿" % (lo, hi if hi < 99999 else 99999), sub, "ret_c")
    print()
    for lo, hi in ((50, 200), (0, 200), (0, 300)):
        sub = [r for r in rows if r.get("circYi") is not None and lo <= r["circYi"] < hi]
        stat("只看流通市值 %d~%d亿" % (lo, hi), sub, "ret_c")

    print()
    print("=== 被剔除的票长什么样 ===")
    dropped = [r for r in rows if not r.get("isNew") and (r.get("circYi") or 0) > 500]
    for r in sorted(dropped, key=lambda x: -(x.get("circYi") or 0)):
        print("  %-11s %-7s %-7s %8s亿  次收%+7.2f%%  次开%+7.2f%%"
              % (r["date"], r["code"], (r["name"] or "")[:5], r.get("circYi"),
                 r["ret_c"], r["ret_o"]))
    if dropped:
        stat("被剔除的 %d 笔" % len(dropped), dropped, "ret_c")

    out = os.path.join(ROOT, "reports", "sanyi_mktcap.json")
    json.dump({"rows": rows, "res": res, "thresholds": [0, 200, 300, 500, 800, 1000]},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print()
    print("明细 → reports/sanyi_mktcap.json")


if __name__ == "__main__":
    main()
