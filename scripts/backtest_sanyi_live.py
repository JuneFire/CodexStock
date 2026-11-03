#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""竞价三一 · 实盘口径回测（1 万本金，含所有实盘约束与成本）。

与之前回测的区别（全部按「09:25 时刻真实可知」实现）：
  1. 题材筛子只用 T-1 涨停家数（themeN1），不用 D 日家数（那要收盘才知道）
  2. 环境开关用 D-1 收盘的上证均线（dev20D1 >= -1% 且非空头排列）
  3. 买入价 = D 日开盘价 × (1+滑点)，卖出价 = 卖出日收盘价 × (1-滑点)
  4. 100 股一手的整手约束 —— 本金不够买一手的票直接跳过（真实买不进）
  5. 成本：佣金（万2.5，最低 5 元，双边）+ 印花税（千1，卖出）+ 过户费（万0.01，双边）
  6. 资金占用：收盘卖出 → 资金次日开盘才可用；竞价卖出 → 当日可用
  7. 卖出规则 = 涨停接力（D+1 起收盘封板才续持，断板当日收盘卖），不做分档卖竞价

数据：reports/三一竞价/sanyi_attribution.json（逐笔题材家数 / D-1 环境）
      scripts/sanyi_hold_longer.py 的 walk()（各票日线与封板判定）
      data/index_kline/sh000001.json（交易日历）

输出：reports/三一竞价/sanyi_live.json
"""
import os
import sys
import json
import math
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from sanyi_hold_longer import walk  # noqa: E402

ATTR = os.path.join(ROOT, "reports", "三一竞价", "sanyi_attribution.json")
CAL_PATH = os.path.join(ROOT, "data", "index_kline", "sh000001.json")
OUT = os.path.join(ROOT, "reports", "三一竞价", "sanyi_live.json")

CAP0 = 10000.0
N_TD = 242

# ---------------------------------------------------------------- 成本参数
COST = {
    "slip": 0.003,        # 单边滑点 0.3%
    "comm": 0.00025,      # 佣金 万 2.5
    "commMin": 5.0,       # 最低 5 元/笔
    "stamp": 0.001,       # 印花税 千 1（仅卖出）
    "transfer": 0.00001,  # 过户费 万 0.1（双边）
}


def cal():
    with open(CAL_PATH, encoding="utf-8") as f:
        d = json.load(f)
    bars = d["bars"] if isinstance(d, dict) and "bars" in d else d
    return [b["date"] for b in bars]


def avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def med(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def tstat(xs):
    if len(xs) < 3:
        return 0.0
    m = avg(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    sd = math.sqrt(v)
    return m / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0


def winrate(xs):
    return 100.0 * sum(1 for x in xs if x > 0) / len(xs) if xs else 0.0


# ---------------------------------------------------------------- 建样本
def build():
    with open(ATTR, encoding="utf-8") as f:
        rows = json.load(f)["rows"]
    out = []
    for r in rows:
        w = walk(r["code"], r["date"], r["name"])
        if not w or "skip" in w:
            continue
        days = w["days"]
        buy_raw = w["buy"]
        # 次日收盘卖
        sell_c1 = days[0]["close"]
        # 涨停接力
        hold = w.get("lu_hold") or 1
        sell_lu = days[hold - 1]["close"]
        out.append({
            "date": r["date"], "code": r["code"], "name": r["name"],
            "theme": r.get("theme"), "themeN": r.get("themeN"),
            "themeN1": r.get("themeN1"),
            "dev20D1": r.get("dev20D1"), "bearD1": bool(r.get("bearD1")),
            # 以下均为 D 日 09:25 或 D-1 收盘即已知的干净变量（无未来函数）
            "gap": r.get("gap"), "idxD1": r.get("idxD1"),
            "prevLimitUp": r.get("prevLimitUp"), "breakRate": r.get("breakRate"),
            "amtYi": r.get("amtYi"), "trfPct": r.get("trfPct"), "vr": r.get("vr"),
            "circYi": r.get("circYi"), "ps": r.get("ps"),
            "d_lu": w["d_lu"], "buy_raw": buy_raw,
            "sell_c1": sell_c1, "sell_lu": sell_lu, "lu_hold": hold,
        })
    return out


def env_on(r):
    d = r.get("dev20D1")
    return (d is not None and d >= -1.0 and not r.get("bearD1"))


# ---------------------------------------------------------------- 单笔成交
def fill(stake, buy_raw, sell_raw, cost):
    """按整手 + 全部成本算一笔真实成交。返回 None 表示买不起一手。"""
    slip = cost["slip"]
    buy_px = buy_raw * (1 + slip)
    sell_px = sell_raw * (1 - slip)
    lot_cost = buy_px * 100.0
    if lot_cost > stake:
        return None
    lots = int(stake / lot_cost)
    shares = lots * 100
    buy_amt = shares * buy_px
    buy_comm = max(cost["commMin"], buy_amt * cost["comm"])
    buy_trans = buy_amt * cost["transfer"]
    spend = buy_amt + buy_comm + buy_trans
    if spend > stake:  # 佣金吃掉最后一点钱的边界情况，退一手
        shares -= 100
        if shares <= 0:
            return None
        buy_amt = shares * buy_px
        buy_comm = max(cost["commMin"], buy_amt * cost["comm"])
        buy_trans = buy_amt * cost["transfer"]
        spend = buy_amt + buy_comm + buy_trans
    sell_amt = shares * sell_px
    sell_comm = max(cost["commMin"], sell_amt * cost["comm"])
    stamp = sell_amt * cost["stamp"]
    sell_trans = sell_amt * cost["transfer"]
    back = sell_amt - sell_comm - stamp - sell_trans
    return {
        "shares": shares, "buyPx": buy_px, "sellPx": sell_px,
        "spend": spend, "back": back, "profit": back - spend,
        # 收益率必须按「实际投入」算：整手取整后没花出去的钱留在现金里，不是亏损
        "ret": (back / spend - 1) * 100,
        "fee": (buy_comm + buy_trans) + (sell_comm + stamp + sell_trans),
        "idle": stake - spend,
        "util": spend / stake if stake > 0 else 0.0,
    }


# ---------------------------------------------------------------- 组合回放
def replay(rows, cal_dates, sell_key, cost, cap0=CAP0):
    """单槽位复利。sell_key: 'sell_c1' 收盘卖 | 'sell_lu' 涨停接力。"""
    if sell_key == "lu":
        sell_key = "sell_lu"
    cidx = {d: i for i, d in enumerate(cal_dates)}
    items = []
    for r in rows:
        i = cidx.get(r["date"])
        if i is None:
            continue
        # 收盘卖出：资金次日开盘才可用
        hold = 1 if sell_key == "sell_c1" else (r["lu_hold"] or 1)
        busy = i + hold + 1
        items.append((i, busy, r))
    items.sort(key=lambda x: (x[0], x[2]["code"]))

    cash = cap0
    free_at = -1
    peak = cap0
    mdd = 0.0
    taken = []
    skipped_busy = 0
    skipped_poor = 0
    total_fee = 0.0
    i = 0
    while i < len(items):
        day = items[i][0]
        group = []
        while i < len(items) and items[i][0] == day:
            group.append(items[i])
            i += 1
        if day < free_at:
            skipped_busy += len(group)
            continue
        stake = cash / len(group)
        live = []
        for _, busy, r in group:
            f = fill(stake, r["buy_raw"], r[sell_key], cost)
            if f is None:
                skipped_poor += 1
                continue
            live.append((busy, r, f))
        if not live:
            continue
        # 同日多笔：等权，未成交的那份留在现金里
        cash -= sum(f["spend"] for _, _, f in live)
        free_at = max(b for b, _, _ in live)
        for _, r, f in live:
            cash += f["back"]
            total_fee += f["fee"]
            taken.append({
                "date": r["date"], "code": r["code"], "name": r["name"],
                "theme": r["theme"], "themeN1": r["themeN1"],
                "gap": r.get("gap"), "idxD1": r.get("idxD1"),
                "breakRate": r.get("breakRate"), "circYi": r.get("circYi"),
                "buyPx": round(f["buyPx"], 3), "sellPx": round(f["sellPx"], 3),
                "shares": f["shares"], "stake": round(stake, 2),
                "spend": round(f["spend"], 2),
                "profit": round(f["profit"], 2), "ret": round(f["ret"], 3),
                "fee": round(f["fee"], 2),
                "hold": (r["lu_hold"] or 1) if sell_key == "sell_lu" else 1,
            })
        peak = max(peak, cash)
        if peak > 0:
            mdd = max(mdd, (peak - cash) / peak * 100)

    mult = cash / cap0
    rets = [t["ret"] for t in taken]
    geo = (mult ** (1.0 / len(taken)) - 1) * 100 if taken and mult > 0 else 0.0
    return {
        "n": len(taken), "skippedBusy": skipped_busy, "skippedPoor": skipped_poor,
        "end": round(cash, 2), "mult": round(mult, 3),
        "geo": round(geo, 2), "mdd": round(mdd, 1),
        "mean": round(avg(rets), 2), "median": round(med(rets), 2),
        "win": round(winrate(rets), 1), "t": round(tstat(rets), 2),
        "fee": round(total_fee, 2),
        "annual": round((mult - 1) * 100, 1),
        "holdAvg": round(avg([t["hold"] for t in taken]), 2) if taken else 0.0,
        "trades": taken,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=CAP0)
    ap.add_argument("--slip", type=float, default=COST["slip"])
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args()

    cost = dict(COST)
    cost["slip"] = args.slip
    cal_dates = cal()
    rows = build()
    print("样本 %d 笔（日线可算、T+1 合规）" % len(rows))

    g_env = [r for r in rows if env_on(r)]
    g_env_theme = [r for r in g_env if (r.get("themeN1") or 0) >= 1]
    g_theme = [r for r in rows if (r.get("themeN1") or 0) >= 1]

    scen = [
        ("S1 只做三一 · 次日收盘卖", rows, "sell_c1"),
        ("S2 只做三一 · 涨停接力", rows, "sell_lu"),
        ("S3 环境开关 · 涨停接力（当前生产）", g_env, "sell_lu"),
        ("S4 题材T-1≥1 · 涨停接力", g_theme, "sell_lu"),
        ("S5 环境开关 + 题材T-1≥1 · 涨停接力（新方案）", g_env_theme, "sell_lu"),
    ]

    print("\n本金 %.0f 元｜单边滑点 %.1f%%｜佣金万2.5(最低5元)｜印花税千1｜过户费万0.1" % (
        args.cap, cost["slip"] * 100))
    print("%-42s %4s %5s %5s %7s %7s %8s %8s %7s %7s" % (
        "场景", "信号", "成交", "买不起", "胜率%", "均值%", "终值", "倍数", "回撤%", "手续费"))
    res = {}
    for label, g, key in scen:
        r = replay(g, cal_dates, key, cost, cap0=args.cap)
        res[label] = r
        print("%-42s %4d %5d %5d %7.1f %+7.2f %8.0f %8.3f %7.1f %7.0f" % (
            label, len(g), r["n"], r["skippedPoor"], r["win"], r["mean"],
            r["end"], r["mult"], r["mdd"], r["fee"]))

    print("\n【成本敏感性】新方案 S5 的终值")
    tmp = dict(cost)
    for slip in (0.0, 0.003, 0.005, 0.008):
        tmp["slip"] = slip
        r = replay(g_env_theme, cal_dates, "sell_lu", tmp, cap0=args.cap)
        print("  滑点 %.1f%% → 终值 %.0f 元（×%.3f），成交 %d 笔" % (
            slip * 100, r["end"], r["mult"], r["n"]))

    s5 = res["S5 环境开关 + 题材T-1≥1 · 涨停接力（新方案）"]
    print("\n【S5 逐笔】%d 笔，本金 %.0f → %.0f 元（×%.3f）" % (
        s5["n"], args.cap, s5["end"], s5["mult"]))
    print("%-11s %-7s %-6s %5s %8s %6s %9s %8s" % (
        "买入日", "名称", "题材", "N1", "买入→卖出", "股数", "盈亏元", "收益率%"))
    for t in s5["trades"]:
        print("%-11s %-7s %-6s %5s %8.2f→%-8.2f %5d %+9.2f %+8.2f" % (
            t["date"], t["name"][:4], (t["theme"] or "")[:4], t["themeN1"],
            t["buyPx"], t["sellPx"], t["shares"], t["profit"], t["ret"]))

    # 鲁棒性：剔除最大盈利几笔后还剩多少
    prof = sorted([t["profit"] for t in s5["trades"]])
    print("\n【S5 鲁棒性】总盈亏 %.0f 元" % sum(prof))
    for k in (1, 2, 3, 5):
        if k < len(prof):
            print("  剔除最大 %d 笔盈利 → 剩余 %+.0f 元（初始 %.0f）" % (
                k, sum(prof[:-k]), args.cap))
    mu = [t["spend"] / t["stake"] for t in s5["trades"]] if s5["trades"] else [0]
    print("  平均资金利用率 %.1f%%（整手取整后没花出去的钱留现金）" % (100 * avg(mu)))
    print("  平均每笔持有 %.2f 个交易日；21 笔 / 242 交易日 → 空仓约 %.0f%%" % (
        s5.get("holdAvg", 0), 100 - 100.0 * sum(t["hold"] for t in s5["trades"]) / 242))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"cost": cost, "cap0": args.cap, "nDays": N_TD,
                   "scenarios": res}, f, ensure_ascii=False, indent=1)
    print("\n明细 -> %s" % OUT)


if __name__ == "__main__":
    main()
