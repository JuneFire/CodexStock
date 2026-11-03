# -*- coding: utf-8 -*-
"""二板战法 × 竞价三一 联合回测（250 交易日）。

流程（用户 m03080 定义）：
    D 日 15:00 收盘后，遍历当日涨停股，用二板战法（换手率阶梯为核心的规则流水线）筛出合格个股
    → 这些个股在 D+1 日 09:25 若同时符合「竞价三一」（金额前三 + 换手第一）
    → D+1 开盘买入 → 卖出（T+1 合规）
    → 统计胜率与盈利

池子口径（与生产 scan_tactics 一致）：
    GOOD = ("最优", "健康")  ← 生产 GOOD_GRADES
    EXCL = ("超标", "剔除", "高风险")  ← 生产剔除区

卖出规则各测一遍：
    T1  D+1 开盘买 → D+2 竞价（开盘）卖   （用户标准工作流）
    T2  D+1 开盘买 → D+1 收盘卖
    T3  D+1 开盘买 → D+2 收盘卖

三一判定来自 scripts/backtest_sanyi.py 的 mark()：
    金额前三 = buckets.topBidAmount 降序前 3（真全市场前三）
    换手第一 = 当日全部 bucket 合并池 turnoverRateF 降序第一
    涨停判定 = price >= limitUpPrice - 0.005
"""
import json
import os
import statistics as stt
import sys
from collections import defaultdict

sys.path.insert(0, "backend")
sys.path.insert(0, "scripts")

from tactics_engine import (                                     # noqa: E402
    DEFAULT_RULES, LADDER_RULES, analyze_kline, judge_strategy)
from backtest_workflow_t1 import limit_pct                       # noqa: E402
from backtest_sanyi import load_days, mark                       # noqa: E402

ZT = "data/market/ztpool_bt"
KD = "data/backtest_kline"
OUT = "reports/三一竞价/backtest_sanyi_ladder.json"
GOOD = ("最优", "健康")
EXCL = ("超标", "剔除", "高风险")
_BARS = {}


def load_bars(code):
    code = str(code).zfill(6)
    if code in _BARS:
        return _BARS[code]
    p = os.path.join(KD, code + ".json")
    bars = []
    if os.path.isfile(p):
        try:
            o = json.load(open(p, encoding="utf-8"))
            bars = o.get("bars") if isinstance(o, dict) else o
        except Exception:
            bars = []
    bars = sorted(bars or [], key=lambda x: str(x.get("date"))[:10])
    _BARS[code] = bars
    return bars


def load_zt():
    """[(date, code, lb, name)]，只留 lb 1~3"""
    out = []
    for fn in sorted(os.listdir(ZT)):
        if not fn.endswith(".json"):
            continue
        try:
            o = json.load(open(os.path.join(ZT, fn), encoding="utf-8"))
        except Exception:
            continue
        d = o.get("date") or fn[:-5]
        for r in o.get("rows") or []:
            lb = r.get("lb") or 0
            if 1 <= lb <= 3 and r.get("code"):
                out.append((d, str(r["code"]).zfill(6), int(lb), r.get("name")))
    return out


def float_cap_yi(bars, i):
    b = bars[i]
    v, t, c = b.get("volume"), b.get("turnover"), b.get("close")
    if not (v and t and c and t > 0):
        return None
    shares = v * 100.0 / t          # 流通股（股）
    return c * shares / 1e8


def evaluate(d, code, lb, bars, pos):
    """D 日二板战法判定，返回 (grade, latestTurnover, floatCapYi) 或 None"""
    k = pos[d]
    hist = bars[:k + 1]
    if len(hist) < 30:
        return None
    feats = analyze_kline(hist, DEFAULT_RULES)
    if not feats:
        return None
    feats["floatCapYi"] = float_cap_yi(bars, k)
    s = judge_strategy(feats, lb, LADDER_RULES, {"floatCapYi": feats["floatCapYi"]})
    if not s or not s.get("ladder"):
        return None
    return s["ladder"].get("grade"), feats.get("latestTurnover"), feats["floatCapYi"]


def stat(rs, key):
    v = [r[key] for r in rs]
    n = len(v)
    if n < 2:
        return {"n": n}
    m = stt.mean(v)
    sd = stt.pstdev(v)
    return {"n": n, "avg": m, "med": stt.median(v),
            "win": sum(1 for x in v if x > 0) / n * 100,
            "t": m / (sd / n ** 0.5) if sd else 0,
            "sum": sum(v), "min": min(v), "max": max(v)}


def four_period(rs, key, dates):
    ds = sorted(set(r["date"] for r in rs))
    if len(ds) < 8:
        return []
    out = []
    for i in range(4):
        part = set(ds[i * len(ds) // 4:(i + 1) * len(ds) // 4])
        v = [r[key] for r in rs if r["date"] in part]
        out.append(round(stt.mean(v), 2) if len(v) >= 5 else None)
    return out


def show(title, groups, dates):
    print("\n" + "=" * 112)
    print(title)
    print("=" * 112)
    print("%-30s %5s %8s %8s %7s %7s %8s  %s" % (
        "分组", "n", "均值%", "中位%", "胜率", "t值", "累计%", "四期均值"))
    print("-" * 112)
    for name, rs in groups:
        if len(rs) < 8:
            print("%-30s %5d   样本不足" % (name, len(rs)))
            continue
        s = stat(rs, "ret")
        fp = four_period(rs, "ret", dates)
        print("%-30s %5d %+8.2f %+8.2f %6.1f%% %+7.2f %+8.1f  %s" % (
            name, s["n"], s["avg"], s["med"], s["win"], s["t"], s["sum"],
            " / ".join("%+.2f" % p if p is not None else "-" for p in fp)))
    print("\n（累计% = 每笔等额 1 万元的单利累加，不是复利；真实资金曲线见下方每日等权）")


def main():
    zt = load_zt()
    days = load_days()
    flags = {d: mark(days[d]) for d in days}
    print("涨停池 %d 条（lb 1~3）；竞价历史 %d 天（%s ~ %s）"
          % (len(zt), len(days), min(days), max(days)))

    rows = []
    gcnt = defaultdict(int)
    t0 = 0
    for d, code, lb, name in zt:
        bars = load_bars(code)
        if not bars:
            continue
        pos = {str(b.get("date"))[:10]: i for i, b in enumerate(bars)}
        if d not in pos:
            continue
        k = pos[d]
        if k + 2 >= len(bars):
            continue
        d1 = str(bars[k + 1].get("date"))[:10]
        if d1 not in flags:
            continue
        r0 = evaluate(d, code, lb, bars, pos)
        if not r0:
            continue
        grade, tr, fcap = r0
        gcnt[grade] += 1
        c0 = bars[k].get("close")
        o1, c1 = bars[k + 1].get("open"), bars[k + 1].get("close")
        o2, c2 = bars[k + 2].get("open"), bars[k + 2].get("close")
        if not all([c0, o1, c1, o2, c2]):
            continue
        gap = (o1 / c0 - 1) * 100
        if gap >= limit_pct(code, name) - 0.6:
            continue                # D+1 开盘即涨停，买不进
        fl = (flags[d1] or {}).get(code) or {}
        sanyi = bool(fl.get("sanyi_wide") or fl.get("sanyi_tight"))
        raw = fl.get("raw") or {}

        def _n(v):
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        rows.append({
            "date": d, "d1": d1, "code": code, "name": name, "lb": lb,
            "grade": grade, "tr": tr, "fcap": fcap, "gap": gap,
            "sanyi": sanyi, "rAmt": fl.get("rAmt"), "rTrf": fl.get("rturnoverRateF"),
            "hasAuction": bool(fl),
            "amt": _n(raw.get("bidAmount")) or _n(fl.get("bidAmount")),
            "trf": _n(raw.get("turnoverRateF")) or _n(raw.get("turnoverRate")),
            # 三种卖出口径（都以 D+1 开盘为买入价）
            "ret": (o2 / o1 - 1) * 100,      # T1 D+2 竞价卖
            "ret_c1": (c1 / o1 - 1) * 100,   # T2 D+1 收盘卖
            "ret_c2": (c2 / o1 - 1) * 100,   # T3 D+2 收盘卖
        })
        t0 += 1
    print("可评估 %d 条；档位分布：%s" % (
        t0, "、".join("%s %d" % (k, v) for k, v in sorted(gcnt.items(), key=lambda x: -x[1]))))

    pool = [r for r in rows if r["grade"] in GOOD]
    excl = [r for r in rows if r["grade"] in EXCL]
    other = [r for r in rows if r["grade"] not in GOOD and r["grade"] not in EXCL]
    print("二板战法合格池（%s）%d 条；剔除区 %d 条；其余 %d 条"
          % ("/".join(GOOD), len(pool), len(excl), len(other)))

    core = [r for r in pool if r["sanyi"]]
    print("★ 二板战法合格池 ∩ D+1 全市场竞价三一：%d 条" % len(core))

    # 池内三一：「首板三一/断板三一」那一类限定范围的口径 —— 在当日合格池内部排名
    byday = defaultdict(list)
    for r in pool:
        if r.get("amt") and r.get("trf"):
            byday[r["d1"]].append(r)
    for d1, lst in byday.items():
        amt_ord = sorted(lst, key=lambda x: -x["amt"])
        trf_ord = sorted(lst, key=lambda x: -x["trf"])
        for i, r in enumerate(amt_ord):
            r["pAmt"] = i + 1
        for i, r in enumerate(trf_ord):
            r["pTrf"] = i + 1
    for r in pool:
        r["poolSanyi"] = (r.get("pAmt") or 99) <= 3 and r.get("pTrf") == 1
    pcore = [r for r in pool if r["poolSanyi"]]
    print("★ 二板战法合格池 ∩ 池内三一（池内金额前三 + 池内换手第一）：%d 条" % len(pcore))
    print("   （当日有竞价数据的合格池中位 %d 只）" % (
        stt.median([len(v) for v in byday.values()]) if byday else 0))

    dates = sorted(set(r["d1"] for r in rows))
    groups = [
        ("★ 合格池 ∩ 全市场三一", core),
        ("★ 合格池 ∩ 池内三一", pcore),
        ("合格池 ∩ 池内金额前三", [r for r in pool if (r.get("pAmt") or 99) <= 3]),
        ("合格池 ∩ 池内换手第一", [r for r in pool if r.get("pTrf") == 1]),
        ("合格池（全部，不过滤三一）", pool),
        ("合格池 · 非三一", [r for r in pool if not r["sanyi"]]),
        ("合格池 ∩ 金额前三", [r for r in pool if (r["rAmt"] or 99) <= 3]),
        ("合格池 ∩ 换手第一", [r for r in pool if r["rTrf"] == 1]),
        ("合格池 ∩ 无竞价数据", [r for r in pool if not r["hasAuction"]]),
        ("剔除区（超标/剔除/高风险）", excl),
        ("其余档位（中性/次优/警戒）", other),
    ]
    show("A. 标准工作流：D+1 开盘买 → D+2 竞价卖（T+1）", groups, dates)

    for k, t in (("ret_c1", "B. D+1 开盘买 → D+1 收盘卖"),
                 ("ret_c2", "C. D+1 开盘买 → D+2 收盘卖")):
        print("\n" + "=" * 112)
        print("%s（同一批样本，换卖出时点）" % t)
        print("=" * 112)
        print("%-30s %5s %8s %8s %7s %7s %8s" % ("分组", "n", "均值%", "中位%", "胜率", "t值", "累计%"))
        print("-" * 112)
        for name, rs in groups:
            if len(rs) < 8:
                continue
            s = stat(rs, k)
            print("%-30s %5d %+8.2f %+8.2f %6.1f%% %+7.2f %+8.1f" % (
                name, s["n"], s["avg"], s["med"], s["win"], s["t"], s["sum"]))

    # 每日等权资金曲线（每笔 1 万元，观察是否只靠个别大赢家）
    if len(pcore) >= 8:
        print("\n★ 合格池 ∩ 池内三一 · 逐笔清单（%d 笔）" % len(pcore))
        print("%-12s %-7s %-7s %3s %-6s %8s %7s %7s %7s" % (
            "D+1日", "代码", "名称", "lb", "档位", "竞价额亿", "T1%", "T2%", "T3%"))
        for r in sorted(pcore, key=lambda x: x["d1"]):
            print("%-12s %-7s %-7s %3d %-6s %8.2f %+7.2f %+7.2f %+7.2f" % (
                r["d1"], r["code"], (r["name"] or "")[:6], r["lb"], r["grade"],
                (r.get("amt") or 0) / 1e8, r["ret"], r["ret_c1"], r["ret_c2"]))

    if len(core) >= 8:
        per = defaultdict(list)
        for r in core:
            per[r["d1"]].append(r["ret"])
        curve, tot = [], 0.0
        for d in sorted(per):
            day = stt.mean(per[d])
            tot += day
            curve.append({"day": d, "n": len(per[d]), "ret": round(day, 2), "cum": round(tot, 2)})
        print("\n★ 合格池 ∩ 三一 · 逐笔清单（%d 笔）" % len(core))
        print("%-12s %-7s %-7s %3s %-7s %7s %7s" % ("D日", "代码", "名称", "lb", "档位", "竞价额亿", "T1收益%"))
        for r in sorted(core, key=lambda x: x["d1"]):
            print("%-12s %-7s %-7s %3d %-7s %7.2f %+7.2f" % (
                r["d1"], r["code"], (r["name"] or "")[:6], r["lb"], r["grade"],
                (r.get("amt") or 0) / 1e8, r["ret"]))
        print("\n每日等权累计 %%：%s" % round(tot, 2))

    json.dump({"pool": pool, "core": core, "pcore": pcore, "excl": excl, "other": other},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("\n明细已写 %s" % OUT)

if __name__ == "__main__":
    main()
