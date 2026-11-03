# -*- coding: utf-8 -*-
"""从一堆首板里选「一只」三一票 —— 每日只买 1 只，看 243 个交易日的统计。

板池：昨日未涨停（prevStreak=0），D-1 收盘已知，09:25 可见，无未来函数。
样本范围：当日竞价榜池（data/auction_top，约 40~150 只，`topBidAmount` 桶含全市场金额前 12~25）。

对比 6 种「选一只」规则
    R1 严格三一     ：池内金额前三 且 池内换手第一；多只取金额名次最前；0 只空仓
    R2 全市场三一   ：全池金额前三 且 全池换手第一，且 ps=0；多只取金额最大
    R3 只换手第一   ：池内换手第一；多只取金额最大
    R4 只金额第一   ：池内金额最大的那 1 只
    R5 综合分 top1  ：池内（金额名次 + 换手名次）最小者；平手取金额大
    R6 综合分 top1 + 金额前3：在金额前三里取换手名次最小

每只都按 D 日开盘买 → D+1 收盘卖（T+1 合规，剔除 D 日开盘即涨停）
另给一份加环境开关（距 MA20 ≥ -1% 且非空头排列）的对照。

用法：python scripts/pick_one_sanyi_first_board.py [--cap 3000] [--topn 3]
输出：reports/pick_one_sanyi_first_board.json
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
from backtest_sanyi_by_board import load_pool_day, rets, num, mean, med, tstat, wr, f2  # noqa: E402
from backtest_step2_auction_filter import load_bars                       # noqa: E402

_SELL = {}


def sell_date(code, d):
    """D 日的下一根日线日期（D+1 收盘卖）"""
    key = (code, d)
    if key in _SELL:
        return _SELL[key]
    out = None
    try:
        bs = sorted(load_bars(code) or [], key=lambda x: str(x.get("date"))[:10])
        ds = [str(b.get("date"))[:10] for b in bs]
        k = ds.index(d)
        if k + 1 < len(ds):
            out = ds[k + 1]
    except Exception:
        out = None
    _SELL[key] = out
    return out

P = lambda *a: os.path.join(ROOT, *a)


def index_cal():
    try:
        with io.open(P("data", "index_kline", "sh000001.json"), encoding="utf-8") as f:
            bars = json.load(f)
        return [str(b["date"])[:10] for b in bars]
    except Exception:
        return []


def switch_on(cal, d):
    """环境开关：上证 D-1 收盘距 MA20 >= -1% 且非空头排列（MA5<MA10<MA20）"""
    try:
        with io.open(P("data", "index_kline", "sh000001.json"), encoding="utf-8") as f:
            bars = json.load(f)
    except Exception:
        return None
    idx = [i for i, b in enumerate(bars) if str(b["date"])[:10] < d]
    if len(idx) < 61:
        return None
    i = idx[-1]
    c = [num(b["close"]) for b in bars]
    ma = lambda n: sum(c[i - n + 1:i + 1]) / n
    ma5, ma10, ma20 = ma(5), ma(10), ma(20)
    dev = (c[i] / ma20 - 1) * 100
    bear = ma5 < ma10 < ma20
    return bool(dev >= -1.0 and not bear)


def pick(rows, rule, topn=3):
    """从一个板池里按规则挑 1 只"""
    if not rows:
        return None
    by_amt = sorted(rows, key=lambda r: -r["amt"])
    for i, r in enumerate(by_amt, 1):
        r["aRank"] = i
    cand = [r for r in rows if (r["trf"] or 0) > 0]
    by_trf = sorted(cand, key=lambda r: -r["trf"])
    for i, r in enumerate(by_trf, 1):
        r["tRank"] = i
    if rule == "R1":
        sel = [r for r in by_amt if r["aRank"] <= topn and r.get("tRank") == 1]
        return sel[0] if sel else None
    if rule == "R3":
        return by_trf[0] if by_trf else None
    if rule == "R4":
        return by_amt[0]
    if rule == "R5":
        pool = [r for r in rows if r.get("tRank")]
        if not pool:
            return None
        return sorted(pool, key=lambda r: (r["aRank"] + r["tRank"], r["aRank"]))[0]
    if rule == "R6":
        top = [r for r in by_amt if r["aRank"] <= topn and r.get("tRank")]
        if not top:
            return None
        return sorted(top, key=lambda r: (r["tRank"], r["aRank"]))[0]
    return None


def equity(picks, cap0=10000.0):
    """单票满仓复利；D+1 收盘卖 → 资金 D+2 开盘才可用"""
    cal = index_cal()
    pos = {d: i for i, d in enumerate(cal)}
    cash = cap0
    peak = cap0
    mdd = 0.0
    busy_until = -1
    taken = 0
    curve = []
    for p in sorted(picks, key=lambda x: x["date"]):
        i = pos.get(p["date"])
        if i is None:
            continue
        if i <= busy_until:
            continue                      # 资金还被上一笔占着
        cash *= (1 + p["ret_c"] / 100.0)
        taken += 1
        j = pos.get(p["sellDate"])
        busy_until = (j + 1) if j is not None else i + 2
        peak = max(peak, cash)
        if peak > 0:
            mdd = max(mdd, (peak - cash) / peak * 100)
        curve.append({"date": p["date"], "equity": cash})
    return {"final": cash, "mult": cash / cap0, "taken": taken, "mdd": mdd, "curve": curve}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=3000.0)
    ap.add_argument("--topn", type=int, default=3)
    args = ap.parse_args()

    dates = sorted(os.path.basename(p)[:-5] for p in glob.glob(P("data", "auction_top", "*.json")))
    n_day = len(dates)
    print("# 从首板池（昨日未涨停 ps=0）里选「一只」三一票（%d 个交易日，cap=%s 亿）" % (n_day, args.cap))
    print("\nD 日开盘买 → D+1 收盘卖（T+1），剔除 D 日开盘即涨停。每日最多 1 只，同池多只时按规则取一只。")

    rules = ["R1", "R2", "R3", "R4", "R5", "R6"]
    picks = {r: [] for r in rules}
    multi = {r: 0 for r in rules}
    pool_n = []

    for d in dates:
        rows = load_pool_day(d)
        if not rows:
            continue
        pool = [r for r in rows if (args.cap <= 0 or (r["cap"] or 0) <= args.cap * 1e8)]
        # 首板池
        fb = [r for r in pool if (r["ps"] or 0) == 0]
        pool_n.append(len(fb))
        # R2 需要全池排名
        allby_amt = sorted(pool, key=lambda r: -r["amt"])
        for i, r in enumerate(allby_amt, 1):
            r["mA"] = i
        allby_trf = sorted([r for r in pool if (r["trf"] or 0) > 0], key=lambda r: -r["trf"])
        for i, r in enumerate(allby_trf, 1):
            r["mT"] = i

        for r in rules:
            if r == "R2":
                sel = [x for x in fb if (x.get("mA") or 99) <= args.topn and x.get("mT") == 1]
                sel = sorted(sel, key=lambda x: -x["amt"])
                cand = sel[0] if sel else None
                n_hit = len(sel)
            else:
                cand = pick(fb, r, args.topn)
                n_hit = None
            if n_hit is None:
                # 统计同池多只命中数（仅 R1/R5 用得上）
                n_hit = 1 if cand else 0
            if cand is None:
                continue
            if n_hit > 1:
                multi[r] += 1
            rr = rets(cand["code"], d)
            if not rr:
                continue
            sd = sell_date(cand["code"], d)
            picks[r].append(dict(rr, date=d, sellDate=sd, code=cand["code"], name=cand["name"],
                                 amtYi=cand["amt"] / 1e8, trfPct=cand["trf"],
                                 capYi=(cand["cap"] or 0) / 1e8,
                                 aRank=cand.get("aRank"), tRank=cand.get("tRank"),
                                 on=switch_on(None, d),
                                 themes=cand["themes"][:3]))

    print("\n首板池每日只数：平均 %.0f 只（中位 %.0f，最少 %d，最多 %d）" % (
        mean(pool_n), med(pool_n), min(pool_n), max(pool_n)))

    print("\n## 1. 六种「选一只」规则对比\n")
    print("| 规则 | 说明 | 信号笔数 | 覆盖天数 | 均值 | 中位 | 胜率 | t | 次日开盘卖 | 1万复利 | 最大回撤 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    desc = {"R1": "首板池内金额前三 + 换手第一", "R2": "全市场金额前三 + 换手第一 且 ps=0",
            "R3": "首板池内换手第一", "R4": "首板池内金额第一",
            "R5": "首板池内（金额名次+换手名次）最小", "R6": "首板池金额前三里换手名次最小"}
    res = {}
    for r in rules:
        rows = picks[r]
        if not rows:
            print("| %s | %s | 0 | — | — | — | — | — | — | — | — |" % (r, desc[r]))
            continue
        v = [x["ret_c"] for x in rows]
        o = [x["ret_o"] for x in rows]
        eq = equity(rows)
        ds = len({x["date"] for x in rows})
        print("| **%s** | %s | %d | %d（%.1f%%） | %s%% | %s%% | %s%% | %s | %s%% | ×%s | %s%% |" % (
            r, desc[r], len(rows), ds, 100.0 * ds / n_day,
            f2(mean(v)), f2(med(v)), f2(wr(v)), f2(tstat(v)), f2(mean(o)),
            f2(eq["mult"]), f2(eq["mdd"], 1)))
        res[r] = {"n": len(rows), "days": ds, "mean": mean(v), "med": med(v),
                  "win": wr(v), "t": tstat(v), "mean_o": mean(o),
                  "mult": eq["mult"], "mdd": eq["mdd"], "taken": eq["taken"],
                  "rows": rows}

    print("\n## 2. 加环境开关（上证 D-1 距 MA20 ≥ -1% 且非空头排列）\n")
    print("| 规则 | 开 n | 开均值 | 开胜率 | 开1万复利 | 关 n | 关均值 | 关胜率 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rules:
        rows = picks[r]
        if not rows:
            continue
        a = [x for x in rows if x.get("on")]
        b = [x for x in rows if not x.get("on")]
        ea = equity(a) if a else {"mult": None}
        print("| %s | %d | %s%% | %s%% | %s | %d | %s%% | %s%% |" % (
            r, len(a), f2(mean([x["ret_c"] for x in a])), f2(wr([x["ret_c"] for x in a])),
            ("×" + f2(ea["mult"])) if ea["mult"] else "—",
            len(b), f2(mean([x["ret_c"] for x in b])), f2(wr([x["ret_c"] for x in b]))))
        if r in res:
            res[r]["on"] = {"n": len(a), "mean": mean([x["ret_c"] for x in a]),
                            "win": wr([x["ret_c"] for x in a]), "mult": ea["mult"]}

    print("\n## 3. R1（严格首板三一）逐笔\n")
    rows = sorted(picks["R1"], key=lambda x: -x["ret_c"])
    if rows:
        print("| D日 | 代码 | 名称 | 金额名次 | 换手名次 | 竞价额 | 换手% | 流通亿 | 竞价涨% | 收益% | 开关 | 当日涨停 | 题材 |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for x in rows:
            print("| %s | %s | %s | %d | %d | %s亿 | %s | %s | %s | %s%% | %s | %s | %s |" % (
                x["date"], x["code"], x["name"], x["aRank"], x["tRank"], f2(x["amtYi"]),
                f2(x["trfPct"]), f2(x["capYi"], 0), f2(x["gap"]), f2(x["ret_c"]),
                "开" if x.get("on") else "关", "✅" if x["luDay"] else "—",
                "/".join(x["themes"]) or "—"))

    print("\n## 4. R5（综合分 top1，每天都有票）逐笔\n")
    rows = sorted(picks["R5"], key=lambda x: -x["ret_c"])
    if rows:
        print("| D日 | 代码 | 名称 | 金额名次 | 换手名次 | 竞价额 | 竞价涨% | 收益% | 开关 | 当日涨停 |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for x in rows:
            print("| %s | %s | %s | %d | %d | %s亿 | %s | %s%% | %s | %s |" % (
                x["date"], x["code"], x["name"], x["aRank"], x["tRank"], f2(x["amtYi"]),
                f2(x["gap"]), f2(x["ret_c"]), "开" if x.get("on") else "关",
                "✅" if x["luDay"] else "—"))

    out = {"dates": n_day, "cap": args.cap, "poolMean": mean(pool_n)}
    out.update({r: res.get(r) or {} for r in rules})
    with io.open(P("reports", "pick_one_sanyi_first_board.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n明细已写入 reports/pick_one_sanyi_first_board.json")


if __name__ == "__main__":
    main()
