# -*- coding: utf-8 -*-
"""多群共振回测：来去由心 / 借势而为 / 红牛一号 / 林尚沃 / J神

共振定义（无未来函数版）：
  某票在 D 日被提到，k = 最近 3 个交易日内提到过该票的**不同群数量**（含 D 日本身）
  入场 = close(D)，rN = close(D+N)/close(D)-1
  超额 = rN - 同日全市场等权均值（剔除 beta）

另给「提及后视版」：k_fwd = [D-1, D+1] 内不同群数量（含次日，略含未来信息，仅作对照）
以及「episode 去重叠版」：同票连续提及（间隔≤3 交易日）合并为一次，避免同一波被重复计数。

用法 python scripts/multigroup_resonance.py
输出 data/laiqiu/backtest/multigroup_resonance.json
"""
import json
import os
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import group_signals as gs          # noqa: E402
import group_backtest as gb         # noqa: E402
import resonance_v2 as rv           # noqa

gb.fetch_kline = lambda *a, **k: []

ROOT = rv.ROOT
SIG = os.path.join(ROOT, "data", "laiqiu", "backtest", "group_signals.json")
OUT = os.path.join(ROOT, "data", "laiqiu", "backtest", "multigroup_resonance.json")

GROUPS = ("laiqiu", "jieshi", "hongniu", "linshangwo", "jshen")
NAME_CN = {"laiqiu": "来去由心", "jieshi": "借势而为", "hongniu": "红牛一号",
           "linshangwo": "林尚沃", "jshen": "J神"}
CAL = os.path.join(ROOT, "data", "laiqiu", "kline", "sh000001_idx.json")


def calendar():
    kl = json.load(open(CAL, encoding="utf-8"))
    ds = [r["date"] for r in kl]
    return ds, {d: i for i, d in enumerate(ds)}


def main():
    sigs = json.load(open(SIG, encoding="utf-8"))
    dates, didx = calendar()

    mention = collections.defaultdict(set)     # (date, code) -> {group}
    pos = collections.defaultdict(set)
    for s in sigs:
        if s["group"] not in GROUPS:
            continue
        k = (s["date"], s["code"])
        mention[k].add(s["group"])
        if s["polarity"] == "pos":
            pos[k].add(s["group"])
    print("mention pairs", len(mention), "pos pairs", len(pos))

    def window_dates(d, back=2, fwd=0):
        i = didx.get(d)
        if i is None:
            return []
        lo, hi = max(0, i - back), min(len(dates) - 1, i + fwd)
        return dates[lo:hi + 1]

    rows = []
    for (d, c), grps in mention.items():
        s = rv.series(c)
        if s is None or d not in s.pos:
            continue
        r = rv.rets(c, d)
        if not r:
            continue
        back = window_dates(d, back=2)
        k_past = set()
        for d2 in back:
            k_past |= mention.get((d2, c), set())
        fwd = window_dates(d, back=1, fwd=1)
        k_fwd = set()
        for d2 in fwd:
            k_fwd |= mention.get((d2, c), set())
        k_pos = set()
        for d2 in back:
            k_pos |= pos.get((d2, c), set())
        rows.append({"date": d, "code": c, "groups_same_day": sorted(grps),
                     "win_groups": sorted(k_past),
                     "k_past": len(k_past), "k_fwd": len(k_fwd), "k_pos": len(k_pos),
                     "r": r})
    print("rows", len(rows))

    def block(sel, label):
        if not sel:
            return {"label": label, "n": 0, "d1": {"n": 0}, "d3": {"n": 0}, "d5": {"n": 0}}
        out = {"label": label, "n": len(sel)}
        for h in (1, 3, 5):
            v = [x["r"]["r%d" % h] for x in sel if x["r"].get("r%d" % h) is not None]
            out["d%d" % h] = gb.metrics(v) if v else {"n": 0}
            out["d%d" % h]["excess"] = rv.excess([(x["date"], x["code"], x["r"]) for x in sel], h)
        return out

    def show(b):
        f = lambda h: "%4d 胜%5.1f 均%+6.2f 比%4s" % (
            b["d%d" % h].get("n", 0), b["d%d" % h].get("winrate") or 0,
            b["d%d" % h].get("avg") or 0, b["d%d" % h].get("pl_ratio"))
        e = lambda h: "%+5.2f" % (b["d%d" % h].get("excess", {}).get("avg") or 0)
        print("%-34s D1 %s 超 %s | D3 %s 超 %s | D5 %s 超 %s"
              % (b["label"], f(1), e(1), f(3), e(3), f(5), e(5)))

    res = {"mention_pairs": len(mention), "rows": len(rows), "blocks": []}

    print("\n=== 按共振群数（无未来函数，回看 3 个交易日）===")
    for k in (1, 2, 3, 4, 5):
        b = block([x for x in rows if x["k_past"] == k], "k_past=%d" % k)
        res["blocks"].append(b)
        show(b)
    b = block([x for x in rows if x["k_past"] >= 3], "k_past>=3")
    res["blocks"].append(b)
    show(b)

    print("\n=== 按共振群数（仅看多方向 pos）===")
    for k in (1, 2, 3):
        b = block([x for x in rows if x["k_pos"] == k], "k_pos=%d" % k)
        res["blocks"].append(b)
        show(b)
    b = block([x for x in rows if x["k_pos"] >= 3], "k_pos>=3")
    res["blocks"].append(b)
    show(b)

    print("\n=== 后视版（含次日提及，仅对照）===")
    for k in (1, 2, 3):
        b = block([x for x in rows if x["k_fwd"] == k], "k_fwd=%d" % k)
        res["blocks"].append(b)
        show(b)
    b = block([x for x in rows if x["k_fwd"] >= 4], "k_fwd>=4")
    res["blocks"].append(b)
    show(b)

    # episode 去重叠：同票连续提及（≤3 交易日）合并，取该段首次入场 + 最大 k
    by_code = collections.defaultdict(list)
    for x in rows:
        by_code[x["code"]].append(x)
    eps = []
    for c, lst in by_code.items():
        lst.sort(key=lambda z: didx.get(z["date"], 10 ** 9))
        cur = None
        for z in lst:
            if cur is None or (didx.get(z["date"], 0) - didx.get(cur[-1]["date"], 0)) > 3:
                cur = [z]
                eps.append(cur)
            else:
                cur.append(z)
    ep_rows = []
    for e in eps:
        first = e[0]
        k = max(x["k_past"] for x in e)
        eg = set()
        for x in e[:3]:
            eg |= set(x.get("win_groups") or [])
        ep_rows.append({"date": first["date"], "code": first["code"], "k_past": k,
                        "k_pos": max(x["k_pos"] for x in e), "ep_groups": sorted(eg),
                        "r": first["r"]})
    print("\n=== episode 去重叠（同票一波只算一次，取首次入场）===")
    for k in (1, 2, 3):
        b = block([x for x in ep_rows if x["k_past"] == k], "ep k=%d" % k)
        res["blocks"].append(b)
        show(b)
    b = block([x for x in ep_rows if x["k_past"] >= 3], "ep k>=3")
    res["blocks"].append(b)
    show(b)
    res["episodes"] = len(ep_rows)

    # 组合维度（episode，k>=2）
    print("")
    print("=== episode k>=2：哪些群组合最强 ===")
    combo = collections.defaultdict(list)
    for x in ep_rows:
        if x["k_past"] < 2:
            continue
        combo["+".join(NAME_CN[g] for g in x["ep_groups"])].append(x)
    combos = []
    for name, sel in sorted(combo.items(), key=lambda kv: -len(kv[1])):
        if len(sel) < 5:
            continue
        b = block(sel, name)
        combos.append(b)
        show(b)
    res["combos"] = combos

    # 典型高共振样本
    top = sorted([x for x in rows if x["k_past"] >= 3], key=lambda z: -z["k_past"])[:20]
    res["samples"] = [{"date": x["date"], "code": x["code"], "k": x["k_past"],
                       "groups": x["groups_same_day"], "r1": x["r"].get("r1")} for x in top]
    print("\n=== 高共振样本（k_past>=3）===")
    for x in top:
        print("  %s %s k=%d %s r1=%s" % (x["date"], x["code"], x["k_past"],
                                         ",".join(NAME_CN[g] for g in x["groups_same_day"]),
                                         x["r"].get("r1")))

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
