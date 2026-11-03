# -*- coding: utf-8 -*-
"""竞价三一：题材处于「发酵初期」还是「退潮期」？—— 只用 09:25 之前可得的量。

对每一笔三一，取它所属题材（口径与 analyze_sanyi_attribution.theme_strength 一致），
然后用 T-1 / T-2 / T-3 ... 的**涨停家数序列**刻画题材所处阶段：

  n1      = 题材 T-1 涨停家数（= 生产里的 themeN1）
  n2      = 题材 T-2 涨停家数
  delta1  = n1 - n2       环比在扩散还是在收缩
  delta2  = n2 - n3
  peak10  = T-1 往前 10 个交易日里的最大家数
  pos     = n1 / peak10   当前处于本轮的什么位置（1.0 = 就在最高点）
  age     = 从 T-1 往回连续「家数 >= 2」的天数（本轮题材已经热了几天）

全部是 T-1 及更早日期的数据，09:25 完全可得，无未来函数。
"""
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTR = os.path.join(ROOT, "reports", "三一竞价", "sanyi_attribution.json")
OUT = os.path.join(ROOT, "reports", "三一竞价", "sanyi_theme_phase.json")
ZP = os.path.join(ROOT, "data", "market", "ztpool_bt")
IDX = os.path.join(ROOT, "data", "index_kline", "sh000001.json")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
from sanyi_hold_longer import walk  # noqa: E402


def load_cal():
    bars = json.load(open(IDX, encoding="utf-8"))
    if isinstance(bars, dict):
        bars = bars.get("bars") or bars.get("data") or []
    return [b["date"] for b in bars if isinstance(b, dict) and b.get("date")]


_Z = {}


def zt(day):
    if day in _Z:
        return _Z[day]
    p = os.path.join(ZP, day + ".json")
    rows = []
    if os.path.isfile(p):
        try:
            rows = (json.load(open(p, encoding="utf-8")) or {}).get("rows") or []
        except (OSError, ValueError):
            rows = []
    _Z[day] = rows
    return rows


_CNT = {}


def count(theme, day):
    """该题材在 day 那天涨停池里的家数（limitUpReason 文本匹配）。"""
    if not theme or not day:
        return 0
    k = (theme, day)
    if k in _CNT:
        return _CNT[k]
    n = sum(1 for r in zt(day) if theme in (r.get("limitUpReason") or ""))
    _CNT[k] = n
    return n


def pick_theme(themes, day):
    best, bn = None, -1
    for t in themes or []:
        t = (t or "").strip()
        if len(t) < 2:
            continue
        n = count(t, day)
        if n > bn:
            best, bn = t, n
    return best, max(bn, 0)


def stat(vals):
    n = len(vals)
    if not n:
        return None
    m = sum(vals) / n
    if n > 1:
        sd = st.stdev(vals)
        t = m / (sd / (n ** 0.5)) if sd else 0.0
    else:
        t = 0.0
    return {"n": n, "mean": m, "median": st.median(vals),
            "win": 100.0 * sum(1 for v in vals if v > 0) / n, "t": t}


def bucket(rows, keyfn, order):
    out = []
    for label, pred in order:
        sub = [r for r in rows if pred(r)]
        s = stat([r["ret"] for r in sub])
        sl = stat([r["lu"] for r in sub])
        out.append({"label": label, "close": s, "relay": sl})
    return out


def show(title, bs):
    print("\n" + title)
    print("  %-22s %5s %9s %8s %7s %8s %9s %7s" % ("分组", "n", "次日收盘", "中位", "胜率", "t", "涨停接力", "胜率"))
    for b in bs:
        c, r = b["close"], b["relay"]
        if not c or not r:
            print("  %-22s %5d %9s" % (b["label"], 0, "（无样本）"))
            continue
        print("  %-22s %5d %+8.2f%% %+7.2f%% %6.1f%% %+8.2f %+8.2f%% %6.1f%%" % (
            b["label"], c["n"], c["mean"], c["median"], c["win"], c["t"], r["mean"], r["win"]))


def main():
    cal = load_cal()
    pos = {d: i for i, d in enumerate(cal)}
    rows = json.load(open(ATTR, encoding="utf-8"))["rows"]
    out = []
    for r in rows:
        d = r.get("date")
        if d not in pos:
            continue
        i = pos[d]
        if i < 12:
            continue
        t1, t2, t3 = cal[i - 1], cal[i - 2], cal[i - 3]
        theme, n1 = pick_theme(r.get("themes"), t1)
        if not theme:
            continue
        n2 = count(theme, t2)
        n3 = count(theme, t3)
        peak = max([count(theme, cal[i - k]) for k in range(1, 11)] + [n1])
        age = 0
        for k in range(1, 30):
            j = i - k
            if j < 0:
                break
            if count(theme, cal[j]) >= 2:
                age += 1
            else:
                break
        w = walk(r.get("code"), d, r.get("name")) or {}
        lu = w.get("lu_chain")
        if lu is None:
            lu = r.get("ret_c") or 0.0
        out.append({"date": d, "code": r.get("code"), "name": r.get("name"),
                    "theme": theme, "n1": n1, "n2": n2, "n3": n3,
                    "delta1": n1 - n2, "delta2": n2 - n3,
                    "peak10": peak, "pos": (n1 / peak) if peak else 0.0, "age": age,
                    "ret": r.get("ret_c") or 0.0, "lu": lu})

    print("样本 %d 笔（%s ~ %s）" % (len(out), out[0]["date"], out[-1]["date"]))
    base_c = stat([r["ret"] for r in out])
    base_l = stat([r["lu"] for r in out])
    print("基线：次日收盘 %+.2f%% 胜率 %.1f%% | 涨停接力 %+.2f%% 胜率 %.1f%%" % (
        base_c["mean"], base_c["win"], base_l["mean"], base_l["win"]))

    show("① 家数环比 delta1 = n1 − n2（扩散 / 持平 / 收缩）", bucket(out, None, [
        ("收缩 <=-3", lambda r: r["delta1"] <= -3),
        ("收缩 -2~-1", lambda r: -2 <= r["delta1"] <= -1),
        ("持平 0", lambda r: r["delta1"] == 0),
        ("扩散 +1~+2", lambda r: 1 <= r["delta1"] <= 2),
        ("扩散 +3~+5", lambda r: 3 <= r["delta1"] <= 5),
        ("强扩散 >+5", lambda r: r["delta1"] > 5),
    ]))

    show("② 本轮已热天数 age（T-1 往回连续家数≥2 的天数）", bucket(out, None, [
        ("age=0（刚起步）", lambda r: r["age"] == 0),
        ("age=1", lambda r: r["age"] == 1),
        ("age=2", lambda r: r["age"] == 2),
        ("age=3~5", lambda r: 3 <= r["age"] <= 5),
        ("age>=6（老题材）", lambda r: r["age"] >= 6),
    ]))

    show("③ 位置 pos = n1 / peak10（1.0 = 本轮最高点）", bucket(out, None, [
        ("<0.4 低位", lambda r: r["pos"] < 0.4),
        ("0.4~0.7", lambda r: 0.4 <= r["pos"] < 0.7),
        ("0.7~0.99", lambda r: 0.7 <= r["pos"] < 0.99),
        (">=0.99 就在高点", lambda r: r["pos"] >= 0.99),
    ]))

    show("④ 四象限（扩散/收缩 × 位置）", bucket(out, None, [
        ("发酵初期 扩张&低位", lambda r: r["delta1"] > 0 and r["pos"] < 0.7),
        ("加速期 扩张&高位", lambda r: r["delta1"] > 0 and r["pos"] >= 0.7),
        ("高位钝化 持平&高位", lambda r: r["delta1"] == 0 and r["pos"] >= 0.7),
        ("退潮 收缩", lambda r: r["delta1"] < 0),
    ]))

    show("⑤ 按 n1 原始家数（对照，之前算过的）", bucket(out, None, [
        ("0~1 只", lambda r: r["n1"] <= 1),
        ("2~3 只", lambda r: 2 <= r["n1"] <= 3),
        ("4~6 只", lambda r: 4 <= r["n1"] <= 6),
        ("7~10 只", lambda r: 7 <= r["n1"] <= 10),
        (">=11 只", lambda r: r["n1"] >= 11),
    ]))

    show("⑥ S5 池（n1>=1）内再按 delta1 / age 拆", bucket([r for r in out if r["n1"] >= 1], None, [
        ("S5 全部", lambda r: True),
        ("S5 & delta1>0", lambda r: r["delta1"] > 0),
        ("S5 & delta1<=0", lambda r: r["delta1"] <= 0),
        ("S5 & age<=1", lambda r: r["age"] <= 1),
        ("S5 & age>=4", lambda r: r["age"] >= 4),
        ("S5 & pos>=0.99", lambda r: r["pos"] >= 0.99),
        ("S5 & pos<0.7", lambda r: r["pos"] < 0.7),
    ]))

    json.dump({"rows": out, "baseline": {"close": base_c, "relay": base_l}},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n明细 -> %s" % os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
