# -*- coding: utf-8 -*-
"""竞价三一按「首板池 / 二板池 / 三板以上池」分组回测（242 交易日）。

口径要点
--------
* 板池用 **D-1 收盘已知的连板数**（`prevStreak`）定义，09:25 完全可见，无未来函数：
    首板池 = 昨日未涨停（ps=0），今天若涨停即首板
    二板池 = 昨日 1 板（ps=1），今天若涨停即二板
    三板以上池 = ps>=2
* 池内三一 = 该池内 **竞价金额前三 + 竞价换手第一**（池排序，不是全市场排序）。
* 交易：D 日开盘买 → D+1 收盘卖（`ret_c`）/ D+1 开盘卖（`ret_o`）；剔除开盘即涨停。
* 另给一版「全市场三一」按 ps 分组的对照，以及「当日涨停板数」的事后分组。

已知测量限制
------------
* `data/auction_top/` 每日只有 40~150 只（8 桶合并，`topBidAmount` 桶服务端硬截 12~25 行），
  所以「换手第一」只是**池内第一**，不是全市场第一。
* 「金额前三」在池内排序，但池子里含全市场金额前 12~25，「金额前三」基本等价于全市场前三。

用法：python scripts/backtest_sanyi_by_board.py [--cap 3000]
输出：reports/sanyi_by_board.json
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from backtest_step2_auction_filter import load_bars          # noqa: E402
from backtest_workflow_t1 import limit_pct                    # noqa: E402

P = lambda *a: os.path.join(ROOT, *a)


def num(v, d=None):
    try:
        if v is None or v == "":
            return d
        f = float(v)
        return f if f == f else d
    except Exception:
        return d


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def tstat(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return None
    m = mean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return m / (sd / len(xs) ** 0.5) if sd else None


def wr(xs):
    xs = [x for x in xs if x is not None]
    return 100.0 * sum(1 for x in xs if x > 0) / len(xs) if xs else None


def f2(v, d=2):
    return "—" if v is None else ("%." + str(d) + "f") % v


_BARS = {}


def bars_of(code):
    if code not in _BARS:
        bs = load_bars(code) or []
        bs = sorted(bs, key=lambda x: str(x.get("date"))[:10])
        _BARS[code] = (bs, {str(b.get("date"))[:10]: i for i, b in enumerate(bs)})
    return _BARS[code]


def load_pool_day(d):
    """当日竞价池（含 ps / 金额 / 换手 / 市值 / 涨停价）"""
    p = P("data", "auction_top", d + ".json")
    try:
        with io.open(p, encoding="utf-8") as f:
            o = json.load(f)
    except Exception:
        return []
    rows = o.get("records") or o.get("rows") or []
    out = []
    for r in rows:
        code = str(r.get("code") or "").zfill(6)
        if not code.isdigit():
            continue
        amt = num(r.get("auctionAmount"))
        if not amt or amt <= 0:
            continue
        cap = num(r.get("floatCap")) or num(r.get("circMcap"))
        trf = num(r.get("trf")) or num(r.get("turnoverRateF"))
        out.append({
            "code": code, "name": r.get("name"), "amt": amt, "cap": cap, "trf": trf,
            "ps": int(num(r.get("ps"), num(r.get("prevStreak"), 0)) or 0),
            "open": num(r.get("open")) or num(r.get("price")),
            "prevClose": num(r.get("prevClose")),
            "lpx": num(r.get("limitUpPrice")),
            "limitPct": num(r.get("limitPct")),
            "themes": r.get("themes") or [],
            "isLuOpen": bool(r.get("isLimitUp") or r.get("isLimitUpOpen")),
        })
    return out


def rets(code, d):
    bi = bars_of(code)
    if not bi:
        return None
    bs, pos = bi
    if d not in pos:
        return None
    k = pos[d]
    if k < 1 or k + 1 >= len(bs):
        return None
    pc = num(bs[k - 1].get("close"))
    o0 = num(bs[k].get("open"))
    o1 = num(bs[k + 1].get("open"))
    c1 = num(bs[k + 1].get("close"))
    c0 = num(bs[k].get("close"))
    if not all([pc, o0, o1, c1, c0]):
        return None
    gap = (o0 / pc - 1) * 100
    lp = limit_pct(code)
    if gap >= lp - 0.6:
        return None                      # 开盘即涨停，买不进
    # 当日是否涨停（收盘）
    lu = False
    lpx = None
    return {
        "ret_o": (o1 / o0 - 1) * 100,
        "ret_c": (c1 / o0 - 1) * 100,
        "gap": gap,
        "luDay": (c0 / pc - 1) * 100 >= lp - 0.6,
    }


def _mark(rows, pre=""):
    """在给定集合内排名：金额前三 + 换手第一（pre 区分不同层级的标记）"""
    ka, kt, ks = pre + "rAmt", pre + "rTrf", pre + "sanyi"
    by_amt = sorted(rows, key=lambda r: -r["amt"])
    for i, r in enumerate(by_amt, 1):
        r[ka] = i
    by_trf = sorted([r for r in rows if (r["trf"] or 0) > 0], key=lambda r: -r["trf"])
    for i, r in enumerate(by_trf, 1):
        r[kt] = i
    for r in rows:
        r[ks] = bool(r.get(ka, 99) <= 3 and r.get(kt) == 1)


def rank_pool(rows, cap_max):
    """全市场池内排名 + 三一标记；先按 cap 过滤"""
    pool = [r for r in rows if (cap_max <= 0 or (r["cap"] or 0) <= cap_max * 1e8)]
    _mark(pool)
    return pool


def rank_group(pool):
    """按昨日连板数分组后，**组内**排名（天地同力说的「首板三一 / 断板三一」口径）"""
    def key(p):
        return 0 if p == 0 else (1 if p == 1 else 2)
    buckets = {}
    for r in pool:
        buckets.setdefault(key(r["ps"]), []).append(r)
    for v in buckets.values():
        _mark(v, "g")
    return buckets


def group_stat(rows, label, out):
    v = [r["ret_c"] for r in rows]
    o = [r["ret_o"] for r in rows]
    if not rows:
        print("| %s | 0 | — | — | — | — | — | — |" % label)
        return
    luRate = 100.0 * sum(1 for r in rows if r.get("luDay")) / len(rows)
    print("| %s | %d | %s%% | %s%% | %s%% | %s | %s%% | %s%% |" % (
        label, len(rows), f2(mean(v)), f2(med(v)), f2(wr(v)), f2(tstat(v)),
        f2(mean(o)), f2(luRate)))
    out.append({"label": label, "n": len(rows), "mean_c": mean(v), "med_c": med(v),
                "win_c": wr(v), "t_c": tstat(v), "mean_o": mean(o), "luRate": luRate,
                "rows": rows})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=3000.0)
    args = ap.parse_args()
    cap = args.cap

    dates = sorted(os.path.basename(p)[:-5] for p in glob.glob(P("data", "auction_top", "*.json")))
    print("# 竞价三一按板池分组（%d 个交易日，cap=%s 亿）" % (len(dates), cap))
    print("\n板池用 D-1 收盘已知的连板数（`prevStreak`）定义，09:25 可见，无未来函数。")
    print("池内三一 = 该池内「竞价金额前三 + 竞价换手第一」。")

    # ---------- 组内三一 / 全市场三一 ----------
    groups = {"首板池（昨日未涨停 ps=0）": lambda p: p == 0,
              "二板池（昨日 1 板 ps=1）": lambda p: p == 1,
              "三板以上池（ps>=2）": lambda p: p >= 2}
    pool_size = {k: [] for k in groups}
    pool_sanyi = {k: [] for k in groups}     # 组内三一（首板三一 / 二板三一 / 断板三一）
    mkt_sanyi = {k: [] for k in groups}      # 全市场三一落到各板池
    for d in dates:
        rows = load_pool_day(d)
        if not rows:
            continue
        pool = rank_pool(rows, cap)
        gb = rank_group(pool)
        for gname, fn in groups.items():
            sub = [r for r in pool if fn(r["ps"])]
            pool_size[gname].append(len(sub))
            for r in sub:
                rr = rets(r["code"], d)
                if not rr:
                    continue
                if r["sanyi"]:
                    mkt_sanyi[gname].append(dict(rr, date=d, code=r["code"], name=r["name"],
                                                 ps=r["ps"], amtYi=r["amt"] / 1e8,
                                                 trfPct=r["trf"], capYi=(r["cap"] or 0) / 1e8,
                                                 rAmt=r["rAmt"], rTrf=r["rTrf"],
                                                 themes=r["themes"][:3]))
            gk = 0 if gname.startswith("首板") else (1 if gname.startswith("二板") else 2)
            for r in gb.get(gk, []):
                if not r.get("gsanyi"):
                    continue
                rr = rets(r["code"], d)
                if not rr:
                    continue
                pool_sanyi[gname].append(dict(rr, date=d, code=r["code"], name=r["name"],
                                              ps=r["ps"], amtYi=r["amt"] / 1e8,
                                              trfPct=r["trf"], capYi=(r["cap"] or 0) / 1e8,
                                              rAmt=r.get("grAmt"), rTrf=r.get("grTrf"),
                                              themes=r["themes"][:3]))

    print("\n## 1. 组内三一（首板三一 / 二板三一 / 断板三一，在各自板池内排名）")
    print("\n各池每日平均股票数：")
    for gname in groups:
        if pool_size[gname]:
            print("- %s：平均 %.0f 只（中位 %.0f，最少 %d，最多 %d）" % (
                gname, mean(pool_size[gname]), med(pool_size[gname]),
                min(pool_size[gname]), max(pool_size[gname])))
    print("\n| 分组 | n | 次日收盘卖 | 中位 | 胜率 | t | 次日开盘卖 | 当日涨停率 |")
    print("|---|---|---|---|---|---|---|---|")
    out1 = []
    for gname in groups:
        group_stat(pool_sanyi[gname], gname, out1)

    print("\n## 1b. 全市场三一（金额前三+换手第一，全市场口径）落到各板池")
    print("\n| 分组 | n | 次日收盘卖 | 中位 | 胜率 | t | 次日开盘卖 | 当日涨停率 |")
    print("|---|---|---|---|---|---|---|---|")
    for gname in groups:
        group_stat(mkt_sanyi[gname], gname, [])

    # ---------- 全市场三一按 ps ----------
    print("\n## 2. 全市场三一（现有 56 笔）按昨日连板数分组")
    try:
        with io.open(P("reports", "sanyi_attribution.json"), encoding="utf-8") as f:
            att = json.load(f)["rows"]
    except Exception:
        att = []
    if att:
        print("\n| 分组 | n | 次日收盘卖 | 中位 | 胜率 | t | 次日开盘卖 | 当日涨停率 |")
        print("|---|---|---|---|---|---|---|---|")
        out2 = []
        sub = {"首板池（ps=0）": [r for r in att if (r["ps"] or 0) == 0],
               "二板池（ps=1）": [r for r in att if (r["ps"] or 0) == 1],
               "三板以上（ps>=2）": [r for r in att if (r["ps"] or 0) >= 2]}
        for k, v in sub.items():
            rows = [{"ret_c": r["ret_c"], "ret_o": r["ret_o"], "luDay": r["isLu"]} for r in v]
            group_stat(rows, k, out2)

        # ---------- 事后：当日涨停板数 ----------
        print("\n## 3. 事后视角：全市场三一按「当日是否涨停 / 当日板数」分组")
        print("\n| 分组 | n | 次日收盘卖 | 中位 | 胜率 | t | 次日开盘卖 |")
        print("|---|---|---|---|---|---|---|")
        def cls(r):
            ps = r["ps"] or 0
            if not r["isLu"]:
                return "当日未涨停"
            if ps == 0:
                return "当日首板"
            if ps == 1:
                return "当日二板"
            return "当日 %d 板" % (ps + 1)
        buckets = {}
        for r in att:
            buckets.setdefault(cls(r), []).append(
                {"ret_c": r["ret_c"], "ret_o": r["ret_o"], "luDay": True})
        order = ["当日首板", "当日二板", "当日 3 板", "当日 4 板", "当日 5 板",
                 "当日未涨停"]
        out3 = []
        for k in order:
            if k in buckets:
                group_stat(buckets[k], k, out3)
        for k in sorted(buckets):
            if k not in order:
                group_stat(buckets[k], k, out3)

    # ---------- 交叉：板池 × 题材/大盘 ----------
    print("\n## 4. 池内三一 × 是否当日涨停（事后，仅供机制理解）")
    print("\n| 分组 | n | 次日收盘卖 | 中位 | 胜率 |")
    print("|---|---|---|---|---|")
    allp = [r for g in pool_sanyi for r in pool_sanyi[g]]
    for label, fn in (("当日涨停", lambda r: r["luDay"]), ("当日未涨停", lambda r: not r["luDay"])):
        sel = [r for r in allp if fn(r)]
        if not sel:
            continue
        v = [r["ret_c"] for r in sel]
        print("| %s | %d | %s%% | %s%% | %s%% |" % (label, len(sel), f2(mean(v)), f2(med(v)), f2(wr(v))))

    # ---------- 逐笔清单 ----------
    print("\n## 5. 池内三一逐笔清单")
    for gname in groups:
        rows = sorted(pool_sanyi[gname], key=lambda x: -(x["ret_c"] or -99))
        if not rows:
            continue
        print("\n### %s（%d 笔）" % (gname, len(rows)))
        print("\n| D日 | 代码 | 名称 | 昨板 | 金额名次 | 换手名次 | 竞价额 | 换手%% | 流通亿 | 竞价涨%% | 收益%% | 当日涨停 | 题材 |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            print("| %s | %s | %s | %d | %d | %d | %s亿 | %s | %s | %s | %s%% | %s | %s |" % (
                r["date"], r["code"], r["name"], r["ps"], r["rAmt"], r["rTrf"],
                f2(r["amtYi"]), f2(r["trfPct"]), f2(r["capYi"], 0), f2(r["gap"]),
                f2(r["ret_c"]), "✅" if r["luDay"] else "—", "/".join(r["themes"]) or "—"))

    with io.open(P("reports", "sanyi_by_board.json"), "w", encoding="utf-8") as f:
        json.dump({
            "cap": cap, "dates": len(dates),
            "pool_size": {k: {"mean": mean(v), "median": med(v), "min": min(v), "max": max(v)}
                          for k, v in pool_size.items() if v},
            "pool_sanyi": {k: v for k, v in pool_sanyi.items()},
        }, f, ensure_ascii=False, indent=1)
    print("\n明细已写入 reports/sanyi_by_board.json")


if __name__ == "__main__":
    main()
