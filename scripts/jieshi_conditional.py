#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""借势而为：什么情况下他的标的成功率高？（条件分组回测）

输入 data/laiqiu/backtest/group_signals.json（group_signals.py 产出，含 laiqiu+jieshi）
输出 data/feishu/jieshi_conditional.json + 控制台表

成功主指标：r1 = close(D+1)/close(D)-1（次日收盘兑现，与实盘最接近）
辅助：r3 / r5 / MFE5 / D+1..5 涨停命中
"""
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from group_backtest import ROOT, SIG, Series, get_kline, limit_pct, WIN_FROM, WIN_TO  # noqa
from jieshi_upside import tag_themes  # noqa

OUT = os.path.join(ROOT, "data", "feishu", "jieshi_conditional.json")
IDX = {"上证": "sh000001", "创业板指": "sz399006"}
STRONG = ("重点", "龙头", "核心", "强call", "主升", "首推", "明确", "确定性", "重点讲",
          "重点品种", "刚起势", "底部", "启动", "看好", "核心梯队", "稀缺", "唯一")


def pct(a, b):
    return None if (not a or b is None) else (b / a - 1) * 100


def agg_sig(sig):
    """jieshi (code,date) 聚合"""
    rows = {}
    for x in sig:
        if x["group"] != "jieshi" or not (WIN_FROM <= x["date"] <= WIN_TO):
            continue
        k = (x["code"], x["date"])
        r = rows.setdefault(k, {"code": x["code"], "date": x["date"], "name": x["name"],
                                "times": [], "kinds": set(), "pols": set(),
                                "texts": [], "themes": set()})
        r["times"].append(x["time"])
        r["kinds"].add(x["kind"])
        r["pols"].add(x["polarity"])
        r["texts"].append(x["text"])
        for t in tag_themes(x["text"]):
            r["themes"].add(t)
    return list(rows.values())


def stats(sel, key="r1"):
    vals = [r[key] for r in sel if r.get(key) is not None]
    if len(vals) < 3:
        return None
    n = len(vals)
    win = sum(1 for v in vals if v > 0) / n * 100
    return {"n": n, "win": round(win, 1), "avg": round(sum(vals) / n, 2),
            "med": round(sorted(vals)[n // 2], 2)}


def main():
    sig = json.load(open(SIG, encoding="utf-8"))
    rows = agg_sig(sig)
    print("jieshi code-days:", len(rows))

    codes = sorted({r["code"] for r in rows})
    kl = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, k in zip(codes, ex.map(get_kline, codes)):
            if k:
                kl[c] = Series(k)
    # 指数
    idx = {}
    for name, sym in IDX.items():
        k = get_kline(sym, adjust="idx")
        if k:
            idx[name] = Series(k)
        else:
            s = Series(k or [])
            idx[name] = s
    print("kline", len(kl), "index", {k: len(v.dates) for k, v in idx.items()})

    # 全池连板/涨停家数（近似市场温度）
    prev_mentions = defaultdict(list)
    for r in sorted(rows, key=lambda x: x["date"]):
        prev_mentions[r["code"]].append(r["date"])
    seen_cnt = defaultdict(int)

    theme_day = defaultdict(int)   # (date, theme) -> 当日被提的票数
    for r in rows:
        for t in r["themes"]:
            theme_day[(r["date"], t)] += 1

    # 来去由心提及（做跨群共振）
    lq = defaultdict(set)
    for x in sig:
        if x["group"] == "laiqiu":
            lq[x["code"]].add(x["date"])

    mkt_hot = {}   # date -> 池内涨停家数
    allc = [c for c in kl]
    for d in (idx["上证"].dates if "上证" in idx else []):
        if not (WIN_FROM <= d <= WIN_TO):
            continue
        up = 0
        for c in allc:
            S = kl[c]
            i = S.pos.get(d)
            if i and i > 0:
                p = limit_pct(c)
                if pct(S.kl[i - 1]["close"], S.kl[i]["close"]) >= p - 0.4:
                    up += 1
        mkt_hot[d] = up

    out = []
    for r in rows:
        S = kl.get(r["code"])
        if not S:
            continue
        d = r["date"]
        i = S.pos.get(d)
        if not i or i + 1 >= len(S.kl):
            continue
        c0 = S.kl[i]["close"]
        n = len(S.kl)
        ret = {}
        for N in (1, 3, 5):
            ret["r%d" % N] = pct(c0, S.kl[i + N]["close"]) if i + N < n else None
        hi = [(S.kl[j]["high"] / c0 - 1) * 100 for j in range(i + 1, min(i + 6, n))]
        lu = 0
        for j in range(i + 1, min(i + 6, n)):
            p = limit_pct(r["code"], r["name"])
            if pct(S.kl[j - 1]["close"], S.kl[j]["close"]) >= p - 0.4:
                lu += 1
        r["r1"], r["r3"], r["r5"] = ret["r1"], ret["r3"], ret["r5"]
        r["mfe5"] = max(hi) if hi else None
        r["lu5"] = lu
        r["r0"] = pct(S.kl[i - 1]["close"], c0) if i > 0 else None          # 当日涨幅
        ma20 = [S.kl[j]["close"] for j in range(max(0, i - 19), i + 1)]
        r["ma20dev"] = pct(sum(ma20) / len(ma20), c0) if len(ma20) >= 10 else None
        r["pos_hi20"] = pct(max(S.kl[j]["high"] for j in range(max(0, i - 19), i + 1)), c0)
        # 首次/第几次提及
        seq = [x for x in prev_mentions[r["code"]] if x <= d]
        r["seq"] = len(seq)
        # 连板：D 日是否涨停 + 前一日是否涨停
        p_lim = limit_pct(r["code"], r["name"])
        r["d_limit"] = bool(r["r0"] is not None and r["r0"] >= p_lim - 0.4)
        r["prev_limit"] = False
        if i > 1:
            pv = pct(S.kl[i - 2]["close"], S.kl[i - 1]["close"])
            r["prev_limit"] = bool(pv is not None and pv >= p_lim - 0.4)
        # 时段
        t0 = min(r["times"])
        hh, mm = int(t0[:2]), int(t0[3:5])
        mins = hh * 60 + mm
        r["slot"] = ("盘前<9:30" if mins < 570 else "早盘9:30-11:30" if mins < 690 else
                     "午盘13:00-14:00" if mins < 840 else "尾盘14:00-15:00" if mins < 900 else "盘后≥15:00")
        r["wd"] = "周" + "一二三四五六日"[__import__("datetime").date.fromisoformat(d).weekday()]
        # 环境
        for nm, S2 in idx.items():
            if not S2.dates:
                r["idx_" + nm] = None
                continue
            j = S2.pos.get(d)
            if j and j > 0:
                r["idx_" + nm] = pct(S2.kl[j - 1]["close"], S2.kl[j]["close"])
            else:
                r["idx_" + nm] = None
        r["hot"] = mkt_hot.get(d)
        # 交叉：来去由心 ±1 日提及
        ds = lq.get(r["code"], set())
        r["lq_hit"] = any(abs((__import__("datetime").date.fromisoformat(d) -
                               __import__("datetime").date.fromisoformat(x)).days) <= 1 for x in ds)
        r["theme_n"] = max([theme_day[(d, t)] for t in r["themes"]], default=0)
        r["strong"] = any(k in t for t in r["texts"] for k in STRONG)
        r["teacher"] = "teacher" in r["kinds"]
        r["teacher_pos"] = r["teacher"] and "pos" in r["pols"]
        out.append(r)

    out = [r for r in out if r["r1"] is not None]
    print("measured:", len(out))

    def bucket(name, fn):
        groups = defaultdict(list)
        for r in out:
            k = fn(r)
            if k is not None:
                groups[k].append(r)
        return (name, fn,
                {k: stats(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))})

    dims = [
        bucket("提及次序", lambda r: "第1次" if r["seq"] == 1 else ("第2次" if r["seq"] == 2 else "第3次+")),
        bucket("当日涨幅", lambda r: (None if r["r0"] is None else "跌>3%" if r["r0"] < -3 else "跌0~3%" if r["r0"] < 0 else
                                 "涨0~3%" if r["r0"] < 3 else "涨3~7%" if r["r0"] < 7 else "涨>7%(含板)")),
        bucket("是否当日涨停", lambda r: "D日涨停" if r["d_limit"] else "D日未涨停"),
        bucket("连板状态", lambda r: "首板" if (r["d_limit"] and not r["prev_limit"]) else
                                ("连板" if r["d_limit"] else "非涨停")),
        bucket("20日线偏离", lambda r: (None if r["ma20dev"] is None else
                                    ("低于MA20" if r["ma20dev"] < 0 else
                                     "MA20上方<10%" if r["ma20dev"] < 10 else "MA20上方>10%"))),
        bucket("距20日最高", lambda r: (None if r["pos_hi20"] is None else
                                    ("接近新高(>-2%)" if r["pos_hi20"] > -2 else "距高点-2~-10%" if r["pos_hi20"] > -10 else "距高点<-10%"))),
        bucket("推送时段", lambda r: r["slot"]),
        bucket("星期", lambda r: r["wd"]),
        bucket("措辞强度", lambda r: "强措辞(重点/龙头/核心…)" if r["strong"] else "一般提及"),
        bucket("题材集群", lambda r: ("同日同题材≥3只" if r["theme_n"] >= 3 else "1-2只")),
        bucket("上证当日", lambda r: (_("跌>1%", "跌0~1%", "涨0~1%", "涨>1%", r["idx_上证"])) if r["idx_上证"] is not None else None),
        bucket("创业板当日", lambda r: (_("跌>1%", "跌0~1%", "涨0~1%", "涨>1%", r["idx_创业板指"])) if r["idx_创业板指"] is not None else None),
        bucket("池内涨停家数", lambda r: ("≤10(冷)" if r["hot"] is not None and r["hot"] <= 10 else
                                    "11-20" if r["hot"] is not None and r["hot"] <= 20 else
                                    ">20(热)" if r["hot"] is not None else None)),
        bucket("跨群共振", lambda r: "来去由心±1日也提" if r["lq_hit"] else "无其他群"),
        bucket("消息类型", lambda r: ("老师直发" if r["teacher"] else "") + ("·看多" if r["teacher_pos"] else "")),
    ]

    res = {"window": [WIN_FROM, WIN_TO], "n": len(out), "dims": {}}
    print("\n" + "=" * 96)
    for name, fkey, g in dims:
        res["dims"][name] = g
        print("\n### %s" % name)
        print("   %-22s %5s %8s %8s %8s %8s %7s" % ("分组", "n", "R1胜率", "R1均值", "R3均值", "R5均值", "涨停%"))
        for k, v in g.items():
            if not v:
                continue
            sub = [r for r in out if fkey(r) == k]
            lu = sum(1 for r in sub if r["lu5"] > 0) / len(sub) * 100 if sub else 0
            r3 = stats(sub, "r3")
            r5 = stats(sub, "r5")
            print("   %-22s %5d %7.1f%% %7.2f%% %8s %8s %6.1f%%" %
                  (k, v["n"], v["win"], v["avg"],
                   ("%.2f%%" % r3["avg"]) if r3 else "-", ("%.2f%%" % r5["avg"]) if r5 else "-", lu))

    # ---- 条件组合（交集） ----
    def combo(name, fn):
        sel = [r for r in out if fn(r)]
        if len(sel) < 5:
            return name, {"n": len(sel)}
        v = stats(sel)
        r3 = stats(sel, "r3")
        r5 = stats(sel, "r5")
        lu = sum(1 for r in sel if r["lu5"] > 0) / len(sel) * 100
        return name, {"n": v["n"], "win": v["win"], "avg": v["avg"],
                      "r3": r3["avg"] if r3 else None, "r5": r5["avg"] if r5 else None,
                      "lu": round(lu, 1)}

    C = [
        ("① 全部信号（基准）", lambda r: True),
        ("② 强措辞", lambda r: r["strong"]),
        ("③ 强措辞 + 非盘中推送", lambda r: r["strong"] and r["slot"] in ("盘前<9:30", "盘后≥15:00")),
        ("④ 强措辞 + 当日涨停", lambda r: r["strong"] and r["d_limit"]),
        ("⑤ 当日涨停（首板/连板）", lambda r: r["d_limit"]),
        ("⑥ 强措辞 + 同题材≥3只", lambda r: r["strong"] and r["theme_n"] >= 3),
        ("⑦ 强措辞 + 上证当日小跌", lambda r: r["strong"] and r["idx_上证"] is not None and -1 <= r["idx_上证"] < 0),
        ("⑧ 强措辞 + 池内涨停≤10(冷)", lambda r: r["strong"] and r["hot"] is not None and r["hot"] <= 10),
        ("⑨ 强措辞 + 跨群共振", lambda r: r["strong"] and r["lq_hit"]),
        ("⑩ 盘中推送（避开）", lambda r: r["slot"] in ("午盘13:00-14:00", "尾盘14:00-15:00")),
    ]
    res["combos"] = {}
    print()
    print("=" * 88)
    print("### 条件组合（R1 为主要成功指标）")
    print("   %-26s %5s %8s %8s %8s %8s %7s" % ("组合", "n", "R1胜率", "R1均值", "R3均值", "R5均值", "涨停%"))
    for nm, f in C:
        k, v = combo(nm, f)
        res["combos"][k] = v
        if v.get("n", 0) < 5:
            print("   %-26s %5d  (样本不足)" % (k, v["n"]))
            continue
        print("   %-26s %5d %7.1f%% %7.2f%% %7s %7s %6.1f%%" %
              (k, v["n"], v["win"], v["avg"],
               ("%.2f%%" % v["r3"]) if v["r3"] is not None else "-",
               ("%.2f%%" % v["r5"]) if v["r5"] is not None else "-", v["lu"]))

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=list)
    print("\nwrote", OUT)


def _(a, b, c, d, v):
    return a if v < -1 else (b if v < 0 else (c if v < 1 else d))


if __name__ == "__main__":
    main()
