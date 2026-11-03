# -*- coding: utf-8 -*-
"""竞价三一：先在选票阶段剔除超大市值个股（默认 >3000 亿），再算「金额前三 + 换手第一」。

与 scripts/backtest_sanyi.py 的区别（这是关键）：
    原版        ：在全市场（含茅台/宁德这类万亿票）里排金额前三和换手第一。
    本脚本      ：**先把超大市值票从排名池里剔除**，再排名。
                  大票永远做不到「换手第一」，却会占掉「金额前三」的名额，
                  把它们留在池里等于把真正的候选票挤到第四名。
    例：2026-11-02 原版金额前三 = 新华传媒/光启技术/贵州茅台（换手第 1 是金额第 4 的东方海洋）
        剔除 >3000 亿后前三 = 新华传媒/光启技术/东方海洋 → 东方海洋即三一。

次新股豁免：D 日距上市不足 365 天的票不按市值剔除（沿用用户口径）。
上市日来自 data/cache/ticker_list_date.json（同花顺 meta/tickers/list 的 list_date）。

口径：D 日开盘买 → D+1 开盘卖（ret_o）/ D+1 收盘卖（ret_c）；剔除 D 日开盘即涨停。
    python scripts/backtest_sanyi_cap.py                 # 默认 3000 亿
    python scripts/backtest_sanyi_cap.py --cap 0         # 不剔除（复现原版）
    python scripts/backtest_sanyi_cap.py --sweep         # 阈值敏感性
"""
import argparse
import glob
import json
import os
import statistics as stt
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

from backtest_workflow_t1 import limit_pct                      # noqa: E402
from backtest_step2_auction_filter import load_bars             # noqa: E402

AU = os.path.join(ROOT, "data", "auction_hist")
TICK = os.path.join(ROOT, "data", "cache", "ticker_list_date.json")
OUT = os.path.join(ROOT, "reports", "三一竞价", "backtest_sanyi_cap.json")
BUCKETS = ("topBidAmount", "topLimitBuyAmount", "limitUpOpen", "limitDownOpen",
           "lateBidUp", "lateBidDown", "prevLimitUpFeedback", "prevBrokenFeedback")
NEW_DAYS = 365


def f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def is_lu(r):
    px, lpx = f(r.get("price")), f(r.get("limitUpPrice"))
    if px and lpx:
        return px >= lpx - 0.005
    chg, lp = f(r.get("changeRate")), f(r.get("limitPct"))
    return bool(chg is not None and lp and chg >= lp - 0.6)


def load_tickers():
    if not os.path.isfile(TICK):
        return {}
    return json.load(open(TICK, encoding="utf-8"))


def load_days():
    out = {}
    for p in sorted(glob.glob(os.path.join(AU, "*.json"))):
        d = os.path.basename(p)[:-5]
        try:
            o = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        b = o.get("buckets") or {}
        top = [r for r in ((b.get("topBidAmount") or {}).get("rows") or [])
               if f(r.get("bidAmount"))]
        seen, wide = set(), []
        for k in BUCKETS:
            for r in ((b.get(k) or {}).get("rows") or []):
                c = str(r.get("code") or "")
                if not c or c in seen:
                    continue
                seen.add(c)
                wide.append(r)
        if not top and not wide:
            continue
        out[d] = {"top": top, "wide": wide}
    return out


def is_new(code, date_str, tickers):
    ld = (tickers.get(str(code)) or {}).get("listDate")
    if not ld:
        return False
    try:
        days = (datetime.strptime(date_str, "%Y-%m-%d")
                - datetime.strptime(str(ld)[:10], "%Y-%m-%d")).days
        return 0 <= days < NEW_DAYS
    except Exception:
        return False


def keep(r, date_str, cap, tickers):
    """cap<=0 表示不剔除。次新股豁免。缺市值时保留（宁可留下也不误杀）。"""
    if cap <= 0:
        return True
    cap_yi = f(r.get("circMcap"))
    if not cap_yi:
        return True
    if cap_yi / 1e8 <= cap:
        return True
    return is_new(r.get("code"), date_str, tickers)


def mark(day, date_str, cap, tickers):
    """带市值预筛的三一标记。排名池 = 剔除超大市值后的票。"""
    top = [r for r in day["top"] if keep(r, date_str, cap, tickers)]
    wide = [r for r in day["wide"] if keep(r, date_str, cap, tickers)]
    flags = {}
    for i, r in enumerate(sorted(top, key=lambda x: -(f(x.get("bidAmount")) or 0)), 1):
        e = flags.setdefault(str(r.get("code") or ""), {})
        e["rAmt"], e["bidAmount"] = i, f(r.get("bidAmount"))

    def rank(rows, key):
        rows = [r for r in rows if (f(r.get(key)) or 0) > 0]
        for i, r in enumerate(sorted(rows, key=lambda x: -f(x.get(key))), 1):
            flags.setdefault(str(r.get("code") or ""), {})["r" + key] = i

    rank(wide, "turnoverRateF")
    rank(wide, "turnoverRate")
    rank(top, "turnoverRateF_top")
    for e in flags.values():
        top3 = (e.get("rAmt") or 99) <= 3
        e["sanyi_wide"] = bool(top3 and (e.get("rturnoverRateF") == 1
                                         or e.get("rturnoverRate") == 1))
        e["sanyi_tight"] = bool(top3 and e.get("rturnoverRateF_top") == 1)
    for r in wide:
        c = str(r.get("code") or "")
        e = flags.setdefault(c, {})
        e["raw"], e["lu"] = r, is_lu(r)
        e["ps"] = int(f(r.get("prevStreak")) or 0)
    return flags


_BARS = {}


def bars_index(code):
    if code in _BARS:
        return _BARS[code]
    bs = load_bars(code)
    val = None
    if bs:
        bs = sorted(bs, key=lambda x: str(x.get("date"))[:10])
        val = (bs, {str(b.get("date"))[:10]: i for i, b in enumerate(bs)})
    _BARS[code] = val
    return val


def run(days, cap, tickers, verbose=False):
    """A 线：三一票 D 日开盘买 → D+1 卖。返回逐笔明细。"""
    rows = []
    for d in sorted(days):
        fl = mark(days[d], d, cap, tickers)
        for code, e in fl.items():
            if not ((e.get("rAmt") or 99) <= 10 or e.get("rturnoverRateF") == 1
                    or e.get("rturnoverRate") == 1):
                continue
            bi = bars_index(code)
            if not bi or d not in bi[1]:
                continue
            bs, pos = bi
            k = pos[d]
            if k < 1 or k + 1 >= len(bs):
                continue
            pc, o0 = bs[k - 1].get("close"), bs[k].get("open")
            o1, c1 = bs[k + 1].get("open"), bs[k + 1].get("close")
            if not all([pc, o0, o1, c1]):
                continue
            gap = (o0 / pc - 1) * 100
            raw = e.get("raw") or {}
            if gap >= limit_pct(code, raw.get("name")) - 0.6:
                continue
            rows.append({
                "date": d, "code": code, "name": raw.get("name"),
                "ret_o": (o1 / o0 - 1) * 100, "ret_c": (c1 / o0 - 1) * 100,
                "gap": gap, "ps": e.get("ps") or 0, "lu": bool(e.get("lu")),
                "wide": e.get("sanyi_wide"), "tight": e.get("sanyi_tight"),
                "bidAmount": e.get("bidAmount"),
                "rAmt": e.get("rAmt"), "rTrf": e.get("rturnoverRateF"),
                "circYi": round((f(raw.get("circMcap")) or 0) / 1e8, 1) or None,
            })
    return rows


def summarize(name, rs, key="ret_c"):
    v = [r[key] for r in rs if r.get(key) is not None]
    n = len(v)
    if n == 0:
        print("  %-30s n=0" % name)
        return None
    if n < 3:
        print("  %-30s n=%2d  %s" % (name, n, " ".join("%+.1f" % x for x in v)))
        return {"n": n}
    m = stt.mean(v)
    sd = stt.pstdev(v)
    t = m / (sd / n ** 0.5) if sd else 0
    d = {"n": n, "mean": m, "median": stt.median(v),
         "win": sum(1 for x in v if x > 0) / n * 100, "t": t, "cum": sum(v)}
    print("  %-30s n=%3d 均值%+6.2f%% 中位%+6.2f%% 胜率%5.1f%% t=%+5.2f 累计%+7.1f%%"
          % (name, n, m, d["median"], d["win"], t, d["sum"] if "sum" in d else sum(v)))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=3000.0, help="剔除超过该流通市值(亿)的票，0=不剔除")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--list", action="store_true", help="打印三一逐笔")
    args = ap.parse_args()

    days = load_days()
    tickers = load_tickers()
    print("竞价历史 %d 个交易日；上市日期表 %d 只" % (len(days), len(tickers)))
    if not tickers:
        print("!! 缺 data/cache/ticker_list_date.json，次新股豁免失效")

    caps = [0, 1000, 2000, 3000, 5000, 10000] if args.sweep else [args.cap]
    res = {}
    for cap in caps:
        tag = "不剔除" if cap <= 0 else "剔除>%g亿" % cap
        rows = run(days, cap, tickers)
        tri = [r for r in rows if r["wide"] or r["tight"]]
        print()
        print("=" * 96)
        print("【%s】三一 %d 笔 / %d 个交易日（覆盖 %d 天）"
              % (tag, len(tri), len(set(r["date"] for r in tri)), len(days)))
        print("=" * 96)
        res[str(cap)] = {
            "n_tri": len(tri),
            "ret_c": summarize("三一 · 次日收盘卖", tri, "ret_c"),
            "ret_o": summarize("三一 · 次日开盘卖", tri, "ret_o"),
            "all_ret_c": summarize("对照·全体样本(额前10∪换1)", rows, "ret_c"),
            "amt3": summarize("对照·仅金额前3", [r for r in rows if (r["rAmt"] or 99) <= 3], "ret_c"),
            "trf1": summarize("对照·仅换手第一", [r for r in rows if r["rTrf"] == 1], "ret_c"),
        }
        if args.list and tri:
            print()
            for r in sorted(tri, key=lambda x: x["date"]):
                print("    %s %-7s %-6s 额#%-2s 换#%-3s 流通%8s亿 次收%+7.2f%% 次开%+7.2f%%"
                      % (r["date"], r["code"], (r["name"] or "")[:5], r["rAmt"],
                         r["rTrf"], r["circYi"], r["ret_c"], r["ret_o"]))
        res[str(cap)]["trades"] = [{k: r[k] for k in
                                    ("date", "code", "name", "ret_o", "ret_c", "circYi",
                                     "rAmt", "rTrf")} for r in tri]

    json.dump({"caps": caps, "res": res}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print()
    print("明细 → reports/三一竞价/backtest_sanyi_cap.json")


if __name__ == "__main__":
    main()
