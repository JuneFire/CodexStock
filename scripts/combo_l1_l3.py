#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1 / L3 合并策略回测：有 L3 就梭哈 L3，否则梭哈 L1。

规则
  资金：1 万起，满仓单票，复利
  选票：当日候选中竞价涨幅最高、且能买到（非涨停开盘）
  优先级：当日有 L3 信号 → 只做 L3；否则做 L1
  出场：
    L3（定稿口径）：D+1 收盘卖半仓 + 剩余半仓 D+2 收盘卖；盘中跌破成本 -5% 止损
    L1：D+1 收盘全出（隔日卖）
  占用：卖完当天才算空仓（L3 半仓留到 D+2 → 下次买入最早 D+3）

用法：
  NO_NET=1 python scripts/combo_l1_l3.py
  NO_NET=1 python scripts/combo_l1_l3.py --l3-unified-exit      # L3 也用隔日收盘卖
  NO_NET=1 python scripts/combo_l1_l3.py --detail
"""
import argparse
import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import l1_strategy as l1          # noqa: E402
import l3_strategy as l3s         # noqa: E402
import resonance_v2 as rv         # noqa: E402

FEE = 0.001


def load_pools():
    dates, didx, n2c, cand, _ = l1.build_candidates()          # L1
    _, _, n2c3, hn, hnb, htxt, lq = l3s.load_all()
    l3c = l3s.candidates(n2c3, hn, hnb, htxt, lq)              # L3
    return dates, didx, cand, l3c


def board_of(code):
    c = str(code)
    if c[:3] in ("300", "301", "688", "689"):
        return "20cm"
    if c[0] in ("4", "8") or c[:2] == "92":
        return "30cm"
    return "10cm"


def cap_pct(code):
    return {"20cm": 20.0, "30cm": 30.0}.get(board_of(code), 10.0)


def exec_l1(code, d, s, fee):
    """L1：D+1 收盘全出"""
    i = s.pos[d]
    if i + 1 >= len(s.kl):
        return None
    cost = s.kl[i]["open"]
    mult = s.kl[i + 1]["close"] / cost * (1 - fee) - fee * 0  # 买入费
    return {"mult": mult * (1 - fee), "exit_day": s.dates[i + 1], "why": "D+1 收盘全出"}


def exec_l3(code, d, s, stop, half, offset, fee, unified=False):
    """L3：定稿口径（-5% 止损 + 半仓 D+1 + 半仓 D+2）；unified=True 时退化为 D+1 收盘全出"""
    i = s.pos[d]
    j = i + 1
    if j >= len(s.kl):
        return None
    cost = s.kl[i]["open"]
    if unified or not half:
        if half is False and not unified:
            pass
        if unified:
            mult = s.kl[j]["close"] / cost
            return {"mult": mult * (1 - fee) * (1 - fee), "exit_day": s.dates[j], "why": "D+1 收盘全出"}
    if s.kl[j]["low"] <= cost * (1 - stop):
        return {"mult": (1 - stop) * (1 - fee) * (1 - fee), "exit_day": s.dates[j], "why": "D+1 止损(全仓)"}
    if not half:
        mult = s.kl[j]["close"] / cost
        return {"mult": mult * (1 - fee) * (1 - fee), "exit_day": s.dates[j], "why": "D+1 收盘全出"}
    legA = 0.5 * (s.kl[j]["close"] / cost)
    k = min(j + offset, len(s.kl) - 1)
    if s.kl[k]["low"] <= cost * (1 - stop):
        legB = 0.5 * (1 - stop)
        why = "半仓 D+1 + 半仓止损"
    else:
        legB = 0.5 * (s.kl[k]["close"] / cost)
        why = "半仓 D+1 + 半仓 D+%d" % (offset + 1)
    return {"mult": (legA + legB) * (1 - fee) * (1 - fee), "exit_day": s.dates[k], "why": why}


def run(mode="combo", stop=0.05, half=True, offset=1, fee=FEE,
        l3_feeds_l1=True, l3_unified=False, detail=False):
    """mode: l1 / l3 / combo"""
    dates, didx, cand_l1, cand_l3 = load_pools()
    cap, capital0 = 10000.0, 10000.0
    hold = -1
    trades = []
    for d in dates:
        if didx.get(d, 0) <= hold:
            continue
        use_l3 = mode in ("l3", "combo") and cand_l3.get(d)
        if use_l3 and mode == "l1":
            use_l3 = False
        if mode == "l3" and not cand_l3.get(d):
            continue
        if use_l3:
            pool = []
            for x in sorted(cand_l3[d], key=lambda z: -z["gap"]):
                s = rv.series(x["code"])
                if s is None or d not in s.pos:
                    continue
                i = s.pos[d]
                if i == 0:
                    continue
                if s.kl[i]["open"] >= s.kl[i - 1]["close"] * (1 + cap_pct(x["code"]) / 100.0) - 0.011:
                    continue           # 涨停开盘买不到
                pool.append(x)
            if not pool:
                continue
            pick = pool[0]
            s = rv.series(pick["code"])
            ex = exec_l3(pick["code"], d, s, stop, half, offset, fee, unified=l3_unified)
            if not ex:
                continue
            tag = "L3"
        else:
            if mode == "l3":
                continue
            pool = [x for x in cand_l1.get(d, []) if not x["limit_open"]]
            if not pool:
                continue
            pick = max(pool, key=lambda z: z["gap"])
            s = rv.series(pick["code"])
            ex = exec_l1(pick["code"], d, s, fee)
            if not ex:
                continue
            tag = "L1"
        cap *= ex["mult"]
        hold = didx.get(ex["exit_day"], didx.get(d, 0) + 1)
        trades.append({"date": d, "code": pick["code"], "name": pick.get("name", "?"),
                       "board": board_of(pick["code"]), "gap": pick.get("gap"),
                       "src": tag, "ret": (ex["mult"] - 1) * 100,
                       "exit": ex["why"], "capital": cap})
    peak, mdd = capital0, 0.0
    for t in trades:
        peak = max(peak, t["capital"])
        mdd = min(mdd, t["capital"] / peak - 1)
    wins = sum(1 for t in trades if t["ret"] > 0)
    stat = {
        "n": len(trades), "final": cap, "ret": (cap / capital0 - 1) * 100,
        "wr": 100 * wins / len(trades) if trades else 0, "mdd": mdd * 100,
        "best": max((t["ret"] for t in trades), default=0),
        "worst": min((t["ret"] for t in trades), default=0),
        "n_l3": sum(1 for t in trades if t["src"] == "L3"),
        "n_l1": sum(1 for t in trades if t["src"] == "L1"),
        "trades": trades,
    }
    if detail:
        stat["_detail"] = trades
    return stat


def show(label, s):
    print("%-30s %3d 笔 (L3 %2d / L1 %2d)｜期末 %9s｜%+9.1f%%｜胜率 %5.1f%%｜回撤 %6.1f%%｜最好 %+6.1f%% 最差 %+6.1f%%"
          % (label, s["n"], s["n_l3"], s["n_l1"], format(round(s["final"]), ","),
             s["ret"], s["wr"], s["mdd"], s["best"], s["worst"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop", type=float, default=0.05)
    ap.add_argument("--fee", type=float, default=FEE)
    ap.add_argument("--l3-unified-exit", action="store_true", help="L3 也用 D+1 收盘全出")
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    print("口径：满仓单票复利｜费用 %.2f%%/笔｜L3 止损 %.0f%%｜L3 出场 %s"
          % (a.fee * 100, a.stop * 100,
             "D+1 收盘全出（统一）" if a.l3_unified_exit else "半仓 D+1 + 半仓 D+2"))
    print()
    for lab, mode in (("纯 L1（竞价最高·隔日卖）", "l1"),
                      ("纯 L3（定稿口径）", "l3"),
                      ("★合并：有 L3 梭哈 L3，否则 L1", "combo")):
        s = run(mode=mode, stop=a.stop, fee=a.fee, l3_unified=a.l3_unified_exit, detail=a.detail)
        show(lab, s)
    if a.l3_unified_exit:
        print("\n（上面 L3 已切换为 D+1 收盘全出）")
    print("\n无费用版本对照：")
    for lab, mode in (("纯 L1", "l1"), ("纯 L3", "l3"), ("合并", "combo")):
        s = run(mode=mode, stop=a.stop, fee=0.0, l3_unified=a.l3_unified_exit)
        show(lab, s)
    if a.detail:
        for lab, mode in (("纯 L1", "l1"), ("纯 L3", "l3"), ("合并", "combo")):
            s = run(mode=mode, stop=a.stop, fee=a.fee, l3_unified=a.l3_unified_exit)
            print("\n=== %s 逐笔（%d）===" % (lab, s["n"]))
            print("%-3s %-11s %-7s %-8s %-4s %-6s %7s %-18s %10s"
                  % ("#", "日期", "代码", "名称", "源", "板", "本笔%", "出场", "资金"))
            for i, t in enumerate(s["trades"], 1):
                print("%-3d %-11s %-7s %-8s %-4s %-6s %+7.2f %-18s %10s"
                      % (i, t["date"], t["code"], (t["name"] or "")[:8], t["src"], t["board"],
                         t["ret"], t["exit"], format(round(t["capital"]), ",")))


if __name__ == "__main__":
    main()
