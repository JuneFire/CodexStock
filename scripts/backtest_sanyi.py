# -*- coding: utf-8 -*-
"""竞价三一回测（300 交易日口径）。

定义（docs/天地同力体系_精讲版.md:773-783）：
    竞价三一 = 竞价金额前三 + 竞价换手第一
    变体：两市三一 / 首板三一 / 断板三一 / 绿三一
    前提：只是选票方法，不是买票理由；退潮期停用。

数据来源：data/auction_hist/<date>.json 的 buckets
    · topBidAmount  —— 全市场竞价额前 N（N=12 或 25，按金额降序）→「金额前三」= 全市场前三，可靠
    · 全部 buckets 合并 —— 当日在册竞价票（40~150 只）→「换手第一」用它的近似
    涨停判定用 price >= limitUpPrice - 0.005（悟道自带 limitUpPrice，精确）

两种换手口径：
    · sanyi_wide —— 金额前三 且 合并池内换手第一
    · sanyi_tight—— 金额前三 且 金额榜内换手第一

两条线：
    A. 三一票直接交易：D 日开盘买 → D+1 开盘/收盘卖（T+1 合规）
    B. 涨停候选 + D+1 三一：D+1 开盘买 → D+2 竞价卖
"""
import glob
import json
import os
import statistics as stt
import sys

sys.path.insert(0, "scripts")
from backtest_workflow_t1 import limit_pct                       # noqa: E402
from backtest_step2_auction_filter import (                      # noqa: E402
    load_bars, load_pool, stat, four_period)

AU = "data/auction_hist"
OUT = "reports/三一竞价/backtest_sanyi.json"


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


def load_days():
    """{date: {"top": [...金额榜(降序)...], "wide": [...合并池...]}}"""
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
        # 合并池：全部桶去重（按 code 保首个）
        seen, wide = set(), []
        for k in ("topBidAmount", "topLimitBuyAmount", "limitUpOpen", "limitDownOpen",
                  "lateBidUp", "lateBidDown", "prevLimitUpFeedback", "prevBrokenFeedback"):
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


def mark(day):
    """给当日票打三一标记。返回 {code: flags}"""
    top, wide = day["top"], day["wide"]
    flags = {}
    amt_order = sorted(top, key=lambda r: -(f(r.get("bidAmount")) or 0))
    for i, r in enumerate(amt_order):
        c = str(r.get("code") or "")
        e = flags.setdefault(c, {})
        e["rAmt"] = i + 1
        e["bidAmount"] = f(r.get("bidAmount"))
    def rank(rows, key):
        rows = [r for r in rows if f(r.get(key)) and f(r.get(key)) > 0]
        for i, r in enumerate(sorted(rows, key=lambda x: -f(x.get(key)))):
            flags.setdefault(str(r.get("code") or ""), {})[("r" + key)] = i + 1
    rank(wide, "turnoverRateF")
    rank(wide, "turnoverRate")
    rank(top, "turnoverRateF_top")
    for code, e in flags.items():
        amt_top3 = (e.get("rAmt") or 99) <= 3
        e["sanyi_wide"] = amt_top3 and e.get("rturnoverRateF") == 1 or (
            amt_top3 and e.get("rturnoverRate") == 1)
        e["sanyi_tight"] = amt_top3 and e.get("rturnoverRateF_top") == 1
    # 合并池里的原始行 + 涨停/连板信息
    for r in wide:
        c = str(r.get("code") or "")
        e = flags.setdefault(c, {})
        e["raw"] = r
        e["lu"] = is_lu(r)
        e["ps"] = int(f(r.get("prevStreak")) or 0)
    return flags


def bars_index(code):
    bs = load_bars(code)
    if not bs:
        return None
    bs = sorted(bs, key=lambda x: str(x.get("date"))[:10])
    return bs, {str(b.get("date"))[:10]: i for i, b in enumerate(bs)}


def run_a(days, flags_all):
    print("\n" + "=" * 104)
    print("A 线：竞价三一票直接交易（D 日开盘买 → 卖出，T+1 合规；剔除开盘即涨停）")
    print("=" * 104)
    rows = []
    for d in sorted(days):
        fl = flags_all[d]
        for code, e in fl.items():
            # A 线样本集：金额前 10 与 换手第一 的并集（三一是其子集，便于做对照）
            if not ((e.get("rAmt") or 99) <= 10 or e.get("rturnoverRateF") == 1
                    or e.get("rturnoverRate") == 1):
                continue
            bi = bars_index(code)
            if not bi:
                continue
            bs, pos = bi
            if d not in pos:
                continue
            k = pos[d]
            if k < 1 or k + 1 >= len(bs):
                continue
            pc, o0, c0 = bs[k - 1].get("close"), bs[k].get("open"), bs[k].get("close")
            o1, c1, o1c = bs[k + 1].get("open"), bs[k + 1].get("close"), bs[k + 1].get("close")
            if not all([pc, o0, c0, o1, c1]):
                continue
            gap = (o0 / pc - 1) * 100
            if gap >= limit_pct(code, (e.get("raw") or {}).get("name")) - 0.6:
                continue          # 开盘即涨停，买不进
            rows.append({
                "date": d, "code": code, "name": (e.get("raw") or {}).get("name"),
                "ret_o": (o1 / o0 - 1) * 100,        # 开盘买 → 次日开盘卖
                "ret_c": (c1 / o0 - 1) * 100,        # 开盘买 → 次日收盘卖
                "intra": (c0 / o0 - 1) * 100,        # 当日 开盘→收盘（诊断）
                "gap": gap, "ps": e.get("ps") or 0, "lu": bool(e.get("lu")),
                "wide": bool(e.get("sanyi_wide")), "tight": bool(e.get("sanyi_tight")),
                "bidAmount": e.get("bidAmount"),
                "rAmt": e.get("rAmt"), "rTrf": e.get("rturnoverRateF"),
            })
    if not rows:
        print("无样本")
        return rows
    print("样本 %d 条 / %d 个交易日" % (len(rows), len(set(r["date"] for r in rows))))
    print("%-26s %5s %8s %8s %8s %8s" % ("分组", "n", "次开卖", "次收卖", "日内", "胜率"))
    print("-" * 78)
    groups = [
        ("任一三一", lambda r: r["wide"] or r["tight"]),
        ("宽口径(合并池换手第一)", lambda r: r["wide"]),
        ("紧口径(金额榜内换手第一)", lambda r: r["tight"]),
        ("三一 & 昨日未涨停", lambda r: (r["wide"] or r["tight"]) and r["ps"] == 0),
        ("三一 & 昨日涨停(断板)", lambda r: (r["wide"] or r["tight"]) and r["ps"] >= 1),
        ("三一 & 昨日2板+", lambda r: (r["wide"] or r["tight"]) and r["ps"] >= 2),
        ("三一 & 竞价涨幅<0", lambda r: (r["wide"] or r["tight"]) and r["gap"] < 0),
        ("对照·仅金额前3（不限换手）", lambda r: (r["rAmt"] or 99) <= 3),
        ("对照·金额 4~10 名", lambda r: 4 <= (r["rAmt"] or 99) <= 10),
        ("对照·仅换手第一（不限金额）", lambda r: r["rTrf"] == 1),
        ("对照·金额前3 且 非换手第一", lambda r: (r["rAmt"] or 99) <= 3 and r["rTrf"] != 1),
    ]
    for name, fn in groups:
        rs = [r for r in rows if fn(r)]
        s = stat([{"ret": x["ret_o"]} for x in rs]) if rs else None
        if not s or s["n"] < 10:
            print("%-26s %5d   样本不足" % (name, s["n"] if s else 0))
            continue
        vc = [x["ret_c"] for x in rs]
        tc = stt.mean(vc) / (stt.pstdev(vc) / len(vc) ** 0.5) if stt.pstdev(vc) else 0
        print("%-26s %5d %+8.2f %+8.2f %+8.2f %7.1f%%  t(次收)=%+.2f 中位次收=%+.2f" % (
            name, s["n"], s["avg"], stt.mean(vc), stt.mean([x["intra"] for x in rs]),
            s["win"], tc, stt.median(vc)))
    return rows


def run_b(days, flags_all, pool):
    print("\n" + "=" * 104)
    print("B 线：D 日涨停候选 → D+1 竞价三一过滤 → D+1 开盘买 → D+2 竞价卖")
    print("=" * 104)
    rows = []
    miss = 0
    for d, code, pr in pool:
        bi = bars_index(code)
        if not bi:
            continue
        bs, pos = bi
        if d not in pos:
            continue
        k = pos[d]
        if k + 2 >= len(bs):
            continue
        d1 = str(bs[k + 1].get("date"))[:10]
        c0, o1, o2 = bs[k].get("close"), bs[k + 1].get("open"), bs[k + 2].get("open")
        if not all([c0, o1, o2]):
            continue
        gap = (o1 / c0 - 1) * 100
        if gap >= limit_pct(code, pr.get("name")) - 0.6:
            continue
        fl = (flags_all.get(d1) or {}).get(code)
        if not fl:
            miss += 1
            rows.append({"date": d, "code": code, "name": pr.get("name"), "lb": pr.get("lb"),
                         "ret": (o2 / o1 - 1) * 100, "tag": "D+1无竞价数据"})
            continue
        rows.append({
            "date": d, "code": code, "name": pr.get("name"), "lb": pr.get("lb"),
            "ret": (o2 / o1 - 1) * 100, "gap": gap,
            "wide": bool(fl.get("sanyi_wide")), "tight": bool(fl.get("sanyi_tight")),
            "rAmt": fl.get("rAmt"), "rTrf": fl.get("rturnoverRateF"),
            "tag": "三一" if (fl.get("sanyi_wide") or fl.get("sanyi_tight")) else "普通",
        })
    print("候选可买 %d 条；D+1 命中竞价数据 %d 条，未命中 %d 条"
          % (len(rows), len(rows) - miss, miss))
    if not rows:
        return rows
    ds = sorted(set(r["date"] for r in rows))
    base = stt.mean([r["ret"] for r in rows])
    print("基线（全部候选）%+.3f%%" % base)
    print("%-24s %6s %8s %8s %7s %7s  %s" % ("分组", "n", "均值", "中位", "胜率", "t值", "四期"))
    print("-" * 104)
    for name, fn in [
        ("D+1 三一（任一）", lambda r: r["tag"] == "三一"),
        ("D+1 宽口径", lambda r: r.get("wide")),
        ("D+1 紧口径", lambda r: r.get("tight")),
        ("D+1 金额前3", lambda r: (r.get("rAmt") or 99) <= 3),
        ("D+1 换手第1", lambda r: r.get("rTrf") == 1),
        ("D+1 普通（非三一）", lambda r: r["tag"] == "普通"),
        ("D+1 无竞价数据", lambda r: r["tag"] == "D+1无竞价数据"),
    ]:
        rs = [r for r in rows if fn(r)]
        s = stat(rs)
        if not s or s["n"] < 15:
            print("%-24s %6d   样本不足" % (name, s["n"] if s else 0))
            continue
        fp = four_period(rs, ds)
        print("%-24s %6d %+8.2f %+8.2f %6.1f%% %+7.2f  %s" % (
            name, s["n"], s["avg"], s["med"], s["win"], s["t"],
            " / ".join("%+.2f" % p if p is not None else "-" for p in fp)))
    return rows


def main():
    days = load_days()
    ds = sorted(days)
    n_top = sum(len(v["top"]) for v in days.values())
    print("竞价历史 %d 个交易日（%s ~ %s）；金额榜记录 %d 条" % (len(ds), ds[0], ds[-1], n_top))
    flags_all = {d: mark(days[d]) for d in ds}
    cnt_w = sum(1 for d in ds for e in flags_all[d].values() if e.get("sanyi_wide"))
    cnt_t = sum(1 for d in ds for e in flags_all[d].values() if e.get("sanyi_tight"))
    print("三一命中：宽口径 %d 次（%.1f 次/百交易日）、紧口径 %d 次"
          % (cnt_w, cnt_w / len(ds) * 100, cnt_t))
    ra = run_a(ds, flags_all)
    rb = run_b(ds, flags_all, load_pool())
    json.dump({"days": len(ds), "a": ra, "b": rb},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("\n明细已写 %s" % OUT)


if __name__ == "__main__":
    main()
