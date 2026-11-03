#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""题材强度 × 卖出规则 交叉回测。

问题：题材当日涨停家数 >= 2 的票，用「涨停接力 / 分档卖出」会不会比「次日收盘卖」更好？

口径：
  D 日 09:25 出信号 -> D 日开盘买入
  卖出规则四种：
    next_open   次日竞价（开盘）卖
    next_close  次日收盘卖（基线）
    lu_chain    次日收盘封住涨停才续持，断板当日收盘卖
    tier        分档：D 日封板且次日高开 >= GAP_TH 卖竞价，否则次日收盘卖
    rule_b      分档 + 涨停接力（生产 B 规则）
  ret = 卖出价 / D 日开盘价 - 1

数据：reports/三一竞价/sanyi_attribution.json（56 笔逐笔 + themeN/luReason）
      scripts/sanyi_hold_longer.py 的 walk() 取各口径收益
      data/index_kline/sh000001.json 作交易日历

输出：reports/三一竞价/sanyi_theme_relay.json + 控制台表
"""
import os
import sys
import json
import math

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from sanyi_hold_longer import walk  # noqa: E402

ATTR = os.path.join(ROOT, "reports", "三一竞价", "sanyi_attribution.json")
CAL_PATH = os.path.join(ROOT, "data", "index_kline", "sh000001.json")
OUT = os.path.join(ROOT, "reports", "三一竞价", "sanyi_theme_relay.json")

GAP_TH = 6.0


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


def win(xs):
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
        d1 = days[0]
        gap = (d1["open"] / d1["prevClose"] - 1) * 100 if d1["prevClose"] else None
        lu_chain = w.get("lu_chain")
        lu_hold = w.get("lu_hold")
        # 涨停接力：如果首日就断板，等价次日收盘
        tier = None
        tier_tag = "close"
        if w["d_lu"] and gap is not None and gap >= GAP_TH:
            tier = w["next_open"]
            tier_tag = "gap_open"
        else:
            tier = w["next_close"]
        rule_b = tier if tier_tag == "gap_open" else lu_chain
        rule_b_hold = 1 if tier_tag == "gap_open" else lu_hold
        rule_b_sell = "open" if tier_tag == "gap_open" else "close"

        out.append({
            "date": r["date"], "code": r["code"], "name": r["name"],
            "theme": r.get("theme"), "themeN": r.get("themeN"),
            "themeN1": r.get("themeN1"), "themeDelta": r.get("themeDelta"),
            "reasonN": r.get("reasonN"), "reasonTok": r.get("reasonTok"),
            "circYi": r.get("circYi"), "lb": r.get("lb"), "ps": r.get("ps"),
            "d_lu": w["d_lu"], "gap": gap, "d1Lu": bool(d1["lu"]),
            "next_open": w["next_open"], "next_close": w["next_close"],
            "lu_chain": lu_chain, "lu_hold": lu_hold,
            "tier": tier, "tier_tag": tier_tag,
            "rule_b": rule_b, "rule_b_hold": rule_b_hold, "rule_b_sell": rule_b_sell,
        })
    return out


# ---------------------------------------------------------------- 统计
KEYS = [
    ("next_open", "次日竞价卖", 1, "open"),
    ("next_close", "次日收盘卖", 1, "close"),
    ("lu_chain", "涨停接力", None, "close"),
    ("tier", "分档(高开≥6%卖竞价,否则次收)", None, None),
    ("rule_b", "B规则(分档+涨停接力)", None, None),
]


def stat_block(rows, label):
    res = {"label": label, "n": len(rows), "rules": {}}
    for key, name, fixed_hold, fixed_sell in KEYS:
        vals = []
        holds = []
        sells = []
        for r in rows:
            v = r.get(key)
            if v is None:
                continue
            vals.append(v)
            if key == "lu_chain":
                holds.append(r["lu_hold"])
                sells.append("close")
            elif key == "tier":
                holds.append(1 if r["tier_tag"] == "gap_open" else 1)
                sells.append("open" if r["tier_tag"] == "gap_open" else "close")
            elif key == "rule_b":
                holds.append(r["rule_b_hold"])
                sells.append(r["rule_b_sell"])
            else:
                holds.append(fixed_hold)
                sells.append(fixed_sell)
        if not vals:
            continue
        res["rules"][key] = {
            "name": name, "n": len(vals), "mean": avg(vals), "median": med(vals),
            "win": win(vals), "t": tstat(vals), "sum": sum(vals),
            "avgHold": avg(holds) if holds else None,
            "gapOpenCount": sum(1 for r in rows if r.get("tier_tag") == "gap_open"),
        }
    return res


def equity(rows, key, cal_dates, cap0=10000.0, n_td=242):
    """单槽位复利；收盘卖出资金次日开盘才可用，竞价卖出资金当日可用。"""
    cidx = {d: i for i, d in enumerate(cal_dates)}
    items = []
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        i = cidx.get(r["date"])
        if i is None:
            continue
        if key == "lu_chain":
            hold, sell = r["lu_hold"], "close"
        elif key == "tier":
            hold, sell = 1, ("open" if r["tier_tag"] == "gap_open" else "close")
        elif key == "rule_b":
            hold, sell = r["rule_b_hold"], r["rule_b_sell"]
        else:
            hold, sell = 1, ("open" if key == "next_open" else "close")
        busy = i + hold + (1 if sell == "close" else 0)
        items.append((i, busy, v))
    items.sort()
    cash, peak, mdd, taken, skipped = cap0, cap0, 0.0, 0, 0
    free_at = -1
    for i, busy, v in items:
        if i < free_at:
            skipped += 1
            continue
        stake = cash
        cash += stake * (v / 100.0)
        taken += 1
        free_at = busy
        peak = max(peak, cash)
        mdd = max(mdd, (peak - cash) / peak * 100 if peak > 0 else 0)
    mult = cash / cap0
    geo = (mult ** (1.0 / taken) - 1) * 100 if taken > 0 and mult > 0 else 0.0
    return {"taken": taken, "skipped": skipped, "end": cash, "mult": mult,
            "geo": geo, "mdd": mdd}


def show(block):
    print("\n===== %s（n=%d）=====" % (block["label"], block["n"]))
    print("%-34s %5s %8s %8s %7s %7s %7s %6s" % (
        "规则", "n", "均值%", "中位%", "胜率%", "t", "累计%", "均持"))
    for key, name, _, _ in KEYS:
        s = block["rules"].get(key)
        if not s:
            continue
        print("%-34s %5d %+8.2f %+8.2f %7.1f %+7.2f %+7.1f %6s" % (
            s["name"], s["n"], s["mean"], s["median"], s["win"], s["t"],
            s["sum"], ("%.2f" % s["avgHold"]) if s["avgHold"] else "-"))


def main():
    cal_dates = cal()
    rows = build()
    print("样本 %d 笔（walk 成功）" % len(rows))

    groups = [
        ("全部", rows),
        ("题材涨停≥2只", [r for r in rows if (r.get("themeN") or 0) >= 2]),
        ("题材涨停<2只", [r for r in rows if (r.get("themeN") or 0) < 2]),
        ("题材≥2 且 D日封板", [r for r in rows if (r.get("themeN") or 0) >= 2 and r["d_lu"]]),
        ("题材<2 且 D日封板", [r for r in rows if (r.get("themeN") or 0) < 2 and r["d_lu"]]),
        ("题材≥4只", [r for r in rows if (r.get("themeN") or 0) >= 4]),
    ]

    report = {"gapTh": GAP_TH, "n": len(rows), "groups": []}
    for label, g in groups:
        blk = stat_block(g, label)
        show(blk)
        eq = {}
        for key, name, _, _ in KEYS:
            if blk["rules"].get(key):
                eq[key] = equity(g, key, cal_dates, n_td=242)
        blk["equity"] = eq
        print("  复利(1万,单槽位): " + " | ".join(
            "%s ×%.2f(成交%d 跳过%d 回撤%.1f%%)" % (
                blk["rules"][k]["name"][:6], eq[k]["mult"], eq[k]["taken"],
                eq[k]["skipped"], eq[k]["mdd"])
            for k in eq))
        report["groups"].append(blk)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("\n明细 -> %s" % OUT)


if __name__ == "__main__":
    main()
