# -*- coding: utf-8 -*-
"""竞价三一 —— 只用「干净变量」重跑归因。

干净的定义：D 日 09:25 那一刻就已知，或 D-1 收盘已知。
任何需要 D 日 15:00 收盘后才知道的量（当日涨停数、当日题材家数、当日最高连板、
当日板型、上证当日涨跌…）一律不纳入。

输出：
  1. 单变量分档表（次收 & 涨停接力）
  2. Spearman 秩相关
  3. S5 内部再拆（21 笔，小样本，仅看方向）
  4. 贪心叠加（从 S5 出发，逐个尝试加干净条件）
  5. 分期稳定性（四期）
"""
from __future__ import print_function

import io
import json
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from sanyi_hold_longer import walk  # noqa: E402

ATTR = os.path.join(_ROOT, "reports", "三一竞价", "sanyi_attribution.json")
OUT = os.path.join(_ROOT, "reports", "三一竞价", "sanyi_clean.json")
MD = os.path.join(_ROOT, "reports", "三一竞价", "三一干净变量归因_242日.md")


def num(v, d=None):
    try:
        if v is None:
            return d
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return d
        return f
    except (TypeError, ValueError):
        return d


def avg(a):
    return (sum(a) / len(a)) if a else None


def tstat(a):
    n = len(a)
    if n < 3:
        return None
    m = avg(a)
    var = sum((x - m) ** 2 for x in a) / (n - 1)
    sd = math.sqrt(var)
    if sd <= 1e-12:
        return None
    return m / (sd / math.sqrt(n))


def winrate(a):
    return (100.0 * sum(1 for x in a if x > 0) / len(a)) if a else None


def med(a):
    if not a:
        return None
    s = sorted(a)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def rank_corr(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 8:
        return None, 0

    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg_rank
            i = j + 1
        return r

    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx, my = avg(rx), avg(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if vx <= 0 or vy <= 0:
        return None, n
    return cov / (vx * vy), n


# ---------- 干净变量定义 ----------
# (key, 显示名, 分档边界, 单位, 说明)
CLEAN = [
    ("themeN1", "题材 T-1 涨停家数", [0, 1, 2, 4, 7, 10 ** 9], "只", "T-1 该票最强题材的涨停家数"),
    ("dev20D1", "上证 D-1 距 MA20", [-10 ** 9, -3, -1, 1, 3, 10 ** 9], "%", "D-1 收盘偏离，-1% 是开关线"),
    ("idxD1", "上证 D-1 涨跌", [-10 ** 9, -1.5, -0.5, 0.5, 1.5, 10 ** 9], "%", "T-1 大盘涨跌"),
    ("prevLimitUp", "昨日涨停总数", [0, 40, 60, 80, 100, 10 ** 9], "只", "D-1 涨停家数"),
    ("breakRate", "昨日炸板率", [0, 25, 30, 35, 45, 101], "%", "D-1 炸板/(涨停+炸板)"),
    ("gap", "竞价涨幅", [-10 ** 9, -5, -2, 0, 3, 7, 10 ** 9], "%", "09:25 竞价高开幅度"),
    ("amtYi", "竞价额", [0, 1, 2, 4, 7, 10 ** 9], "亿", "09:25 竞价成交额"),
    ("trfPct", "竞价换手", [0, 2, 4, 6, 9, 10 ** 9], "%", "09:25 竞价换手率"),
    ("vr", "量比", [0, 40, 80, 150, 300, 10 ** 9], "倍", "09:25 竞价量比"),
    ("circYi", "流通市值", [0, 50, 100, 200, 500, 10 ** 9], "亿", "个股流通市值"),
    ("ps", "昨日连板", [-1, 0, 1, 2, 3, 10 ** 9], "板", "D-1 连板数"),
    ("bearD1", "D-1 空头排列", None, "bool", "MA5<MA10<MA20"),
]

# 脏变量（仅列出以便核对，不参与统计）
DIRTY = {
    "idxD": "上证当日涨跌",
    "themeN": "题材当日涨停家数",
    "themeDelta": "题材家数变化",
    "limitUpN": "当日涨停总数",
    "maxTier": "当日最高连板",
    "tierPct": "最高连板分位",
    "reasonN": "涨停原因主词家数",
    "isLu": "当日是否涨停",
    "lb": "当日连板数",
    "luTime": "封板时间",
    "openTimes": "开板次数",
}


def load():
    d = json.load(open(ATTR, encoding="utf-8"))
    rows = d.get("rows") or []
    cal = sorted({r["date"] for r in rows})
    out = []
    for r in rows:
        w = walk(r["code"], r["date"], r.get("name") or "")
        if not w or not w.get("days"):
            continue
        days = w["days"]
        ret_c = (days[0]["close"] / w["buy"] - 1) * 100 if w.get("buy") else None
        hold = w.get("lu_hold") or 1
        ret_lu = (days[hold - 1]["close"] / w["buy"] - 1) * 100 if w.get("buy") else None
        out.append({
            "date": r["date"], "code": r["code"], "name": r.get("name"),
            "ret_c": ret_c, "ret_lu": ret_lu, "hold": hold,
            "themeN1": num(r.get("themeN1")),
            "dev20D1": num(r.get("dev20D1")),
            "idxD1": num(r.get("idxD1")),
            "prevLimitUp": num(r.get("prevLimitUp")),
            "breakRate": num(r.get("breakRate")),
            "gap": num(r.get("gap")),
            "amtYi": num(r.get("amtYi")),
            "trfPct": num(r.get("trfPct")),
            "vr": num(r.get("vr")),
            "circYi": num(r.get("circYi")),
            "ps": num(r.get("ps"), 0),
            "bearD1": bool(r.get("bearD1")),
            "theme": r.get("theme"),
        })
    return sorted(out, key=lambda x: x["date"]), cal


def band(v, edges):
    if v is None:
        return None
    for i in range(len(edges) - 1):
        if edges[i] <= v < edges[i + 1]:
            return i
    return len(edges) - 2


def band_label(i, edges, unit):
    lo, hi = edges[i], edges[i + 1]
    ls = "" if lo <= -10 ** 8 else ("%g" % lo)
    hs = "" if hi >= 10 ** 8 else ("%g" % hi)
    if not ls:
        return "≤%s%s" % (hs, unit)
    if not hs:
        return "≥%s%s" % (ls, unit)
    return "%s~%s%s" % (ls, hs, unit)


def cell(a):
    if not a:
        return "| — | — | — | — | — |"
    t = tstat(a)
    return "| %d | %+.2f | %+.2f | %.1f | %s |" % (
        len(a), avg(a), med(a), winrate(a), ("%.2f" % t) if t is not None else "—")


def main():
    rows, cal = load()
    r_c = [r["ret_c"] for r in rows if r["ret_c"] is not None]
    r_lu = [r["ret_lu"] for r in rows if r["ret_lu"] is not None]

    md = io.StringIO()
    md.write("# 竞价三一 · 干净变量归因（242 交易日）\n\n")
    md.write("**干净 = D 日 09:25 或 D-1 收盘即已知。** 任何需要 D 日 15:00 才知道的量都不纳入。\n\n")
    md.write("样本 %d 笔 / 242 个交易日（2025-11-03 ~ 2026-11-02）\n\n" % len(rows))
    md.write("## 0. 基线\n\n")
    md.write("| 口径 | n | 均值% | 中位% | 胜率% | t |\n|---|---|---|---|---|---|\n")
    md.write("| 次日收盘卖" + cell(r_c) + "\n")
    md.write("| 涨停接力" + cell(r_lu) + "\n\n")

    # ---------- 1. 单变量分档 ----------
    md.write("## 1. 单变量分档（全 %d 笔）\n\n" % len(rows))
    summary = {}
    for key, label, edges, unit, note in CLEAN:
        md.write("### %s（%s）\n\n" % (label, note))
        md.write("| 档位 | n | 次收均值% | 次收中位% | 次收胜率% | 次收 t | n | 接力均值% | 接力胜率% | 接力 t |\n")
        md.write("|---|---|---|---|---|---|---|---|---|---|\n")
        stat = []
        if edges is None:
            groups = [("True", [r for r in rows if r.get(key)]), ("False", [r for r in rows if not r.get(key)])]
        else:
            groups = [(band_label(i, edges, unit), [r for r in rows if band(r.get(key), edges) == i])
                      for i in range(len(edges) - 1)]
        for gname, g in groups:
            if not g:
                continue
            a1 = [x["ret_c"] for x in g if x["ret_c"] is not None]
            a2 = [x["ret_lu"] for x in g if x["ret_lu"] is not None]
            t1, t2 = tstat(a1), tstat(a2)
            md.write("| %s | %d | %+.2f | %+.2f | %.1f | %s | %d | %+.2f | %.1f | %s |\n" % (
                gname, len(a1), avg(a1), med(a1), winrate(a1), ("%.2f" % t1) if t1 is not None else "—",
                len(a2), avg(a2), winrate(a2), ("%.2f" % t2) if t2 is not None else "—"))
            stat.append({"band": gname, "n": len(a1), "mean_c": avg(a1), "t_c": t1,
                         "n_lu": len(a2), "mean_lu": avg(a2), "t_lu": t2})
        # 档间极差
        if edges is not None and len(stat) >= 2:
            span_c = max(s["mean_c"] for s in stat) - min(s["mean_c"] for s in stat)
            span_lu = max(s["mean_lu"] for s in stat) - min(s["mean_lu"] for s in stat)
            md.write("\n档间极差：次收 **%.2fpp** ｜ 接力 **%.2fpp**\n\n" % (span_c, span_lu))
        summary[key] = stat

    # ---------- 2. 相关 ----------
    md.write("## 2. Spearman 秩相关（vs 涨停接力收益）\n\n")
    md.write("| 变量 | ρ | n |\n|---|---|---|\n")
    corr = []
    for key, label, edges, unit, note in CLEAN:
        if edges is None:
            xs = [1.0 if r.get(key) else 0.0 for r in rows]
        else:
            xs = [r.get(key) for r in rows]
        ys = [r["ret_lu"] for r in rows]
        rho, n = rank_corr(xs, ys)
        if rho is not None:
            corr.append({"key": key, "label": label, "rho": rho, "n": n})
    for c in sorted(corr, key=lambda x: -abs(x["rho"])):
        md.write("| %s | %+.3f | %d |\n" % (c["label"], c["rho"], c["n"]))
    md.write("\n")

    # ---------- 3. S5 内部再拆 ----------
    s5 = [r for r in rows
          if num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0
          and not r.get("bearD1") and (r.get("themeN1") or 0) >= 1]
    md.write("## 3. S5 内部再拆（%d 笔，小样本，只看方向）\n\n" % len(s5))
    a5 = [r["ret_lu"] for r in s5 if r["ret_lu"] is not None]
    md.write("S5 基线：" + cell(a5).replace("| ", "| ", 1) + "\n\n")
    md.write("| 条件 | n | 接力均值% | 接力胜率% | 相对 S5 |\n|---|---|---|---|---|\n")
    base = avg(a5)
    inner = []
    for key, label, edges, unit, note in CLEAN:
        if key in ("themeN1", "dev20D1", "bearD1"):
            continue
        if edges is None:
            continue
        for i in range(len(edges) - 1):
            g = [x["ret_lu"] for x in s5 if band(x.get(key), edges) == i and x["ret_lu"] is not None]
            if len(g) < 4:
                continue
            m = avg(g)
            inner.append({"key": key, "band": band_label(i, edges, unit), "n": len(g),
                          "mean": m, "wr": winrate(g), "delta": m - base})
    for it in sorted(inner, key=lambda x: -x["delta"]):
        md.write("| %s %s | %d | %+.2f | %.1f | %+.2f |\n" % (
            [c[1] for c in CLEAN if c[0] == it["key"]][0], it["band"], it["n"], it["mean"], it["wr"], it["delta"]))
    md.write("\n注意：S5 只有 %d 笔，再拆任何一档 n 都会掉到个位数，**只能看方向不能定规则**。\n\n" % len(s5))

    # ---------- 4. 贪心叠加 ----------
    md.write("## 4. 贪心叠加（从全样本出发，每次加一个干净条件）\n\n")
    ladder = [
        ("① 全部三一", lambda r: True),
        ("② + D-1 距MA20≥-1%", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0),
        ("③ + 非空头排列", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0 and not r.get("bearD1")),
        ("④ + 题材T-1≥1（=S5）", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0
         and not r.get("bearD1") and (r.get("themeN1") or 0) >= 1),
        ("⑤ + 题材T-1≥2", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0
         and not r.get("bearD1") and (r.get("themeN1") or 0) >= 2),
        ("⑥ + 昨日炸板率≥30%", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0
         and not r.get("bearD1") and (r.get("themeN1") or 0) >= 1 and (num(r.get("breakRate")) or 0) >= 30),
        ("⑦ + 竞价额≥2亿", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0
         and not r.get("bearD1") and (r.get("themeN1") or 0) >= 1 and (num(r.get("amtYi")) or 0) >= 2),
        ("⑧ + 流通市值50~500亿", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0
         and not r.get("bearD1") and (r.get("themeN1") or 0) >= 1
         and 50 <= (num(r.get("circYi")) or 0) <= 500),
        ("⑨ + 竞价涨幅≤5%", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0
         and not r.get("bearD1") and (r.get("themeN1") or 0) >= 1 and (num(r.get("gap")) or 0) <= 5),
        ("⑩ + 昨日连板≤2", lambda r: num(r.get("dev20D1")) is not None and r["dev20D1"] >= -1.0
         and not r.get("bearD1") and (r.get("themeN1") or 0) >= 1 and (num(r.get("ps")) or 0) <= 2),
    ]
    md.write("| 累计条件 | n | 次收均值% | 次收 t | 接力均值% | 接力中位% | 接力胜率% | 接力 t |\n")
    md.write("|---|---|---|---|---|---|---|---|\n")
    lad = []
    for name, fn in ladder:
        g = [r for r in rows if fn(r)]
        a1 = [x["ret_c"] for x in g if x["ret_c"] is not None]
        a2 = [x["ret_lu"] for x in g if x["ret_lu"] is not None]
        t1, t2 = tstat(a1), tstat(a2)
        md.write("| %s | %d | %+.2f | %s | %+.2f | %+.2f | %.1f | %s |\n" % (
            name, len(a1), avg(a1), ("%.2f" % t1) if t1 else "—",
            avg(a2), med(a2), winrate(a2), ("%.2f" % t2) if t2 else "—"))
        lad.append({"name": name, "n": len(a2), "mean_lu": avg(a2), "t_lu": t2, "wr": winrate(a2)})
    md.write("\n")

    # ---------- 5. 分期稳定性 ----------
    md.write("## 5. 分期稳定性（四期，按交易日数等分）\n\n")
    all_dates = sorted({r["date"] for r in rows})
    uni = sorted({r["date"] for r in rows})
    q = max(1, len(uni) // 4)
    parts = []
    for k in range(4):
        lo = k * q
        hi = (k + 1) * q if k < 3 else len(uni)
        parts.append(set(uni[lo:hi]))
    md.write("| 分组 | 第1期 | 第2期 | 第3期 | 第4期 |\n|---|---|---|---|---|\n")
    for name, fn in ladder[:5]:
        cells = []
        for p in parts:
            g = [r["ret_lu"] for r in rows if fn(r) and r["date"] in p and r["ret_lu"] is not None]
            cells.append(("%+.2f(n=%d)" % (avg(g), len(g))) if g else "—")
        md.write("| %s | %s |\n" % (name, " | ".join(cells)))
    md.write("\n")

    # ---------- 6. 结论 ----------
    md.write("## 6. 结论\n\n")
    md.write("见文首表与第 4 节。所有参与统计的变量都能在 09:25 或 D-1 收盘获得。\n\n")
    md.write("### 已剔除的脏变量（不可用于实盘筛子）\n\n")
    md.write("| 变量 | 含义 |\n|---|---|\n")
    for k, v in sorted(DIRTY.items(), key=lambda x: x[1]):
        md.write("| `%s` | %s |\n" % (k, v))

    txt = md.getvalue()
    open(MD, "w", encoding="utf-8").write(txt)
    json.dump({"rows": rows, "summary": summary, "corr": sorted(corr, key=lambda x: -abs(x["rho"])),
               "ladder": lad, "inner": inner, "dirty": DIRTY},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(txt)
    print("已写入 %s\n已写入 %s" % (MD, OUT))


if __name__ == "__main__":
    main()
