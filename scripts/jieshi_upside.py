#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""借势而为「题材票」右尾/赔率分析：不只看胜率，看踩中行情能赚多少。

输出 data/feishu/jieshi_upside.json + 控制台表格

口径（与 group_backtest.py 保持一致）：
  信号日 D（消息日），入场 = close(D)
  rN      = close(D+N)/close(D)-1        （收盘兑现）
  MFE_N   = max(high[D+1..D+N])/close(D)-1   （踩中行情的最大浮盈，最优离场）
  MAE_5   = min(low[D+1..D+5])/close(D)-1
  LU_5    = D+1..D+5 内涨停天数（按板块涨跌幅上限近似）
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from group_backtest import ROOT, SIG, Series, get_kline, limit_pct, WIN_FROM, WIN_TO  # noqa: E402

OUT = os.path.join(ROOT, "data", "feishu", "jieshi_upside.json")

THEMES = {
    "算力/光模块": ["光模块", "光通信", "800G", "1.6T", "CPO", "硅光", "光芯片", "算力", "数据中心", "IDC"],
    "PCB/载板": ["PCB", "载板", "HDI", "覆铜板", "铜箔"],
    "液冷/散热": ["液冷", "散热", "CDU", "冷板", "风冷"],
    "存储": ["存储", "DRAM", "NAND", "HBM", "内存", "闪存"],
    "半导体/设备": ["半导体", "晶圆", "设备", "刻蚀", "封装", "先进制程", "中芯", "长鑫"],
    "机器人": ["机器人", "人形", "减速器", "丝杠", "灵巧手", "谐波"],
    "新能源/电池": ["固态电池", "锂电", "电池", "隔膜", "电解液", "储能", "光伏"],
    "军工/航天": ["军工", "航天", "卫星", "导弹", "无人机", "低空"],
    "医药/创新药": ["创新药", "mRNA", "疫苗", "CXO", "医疗器械", "GLP"],
    "黄金/资源": ["黄金", "铜", "稀土", "小金属", "钨", "铝"],
    "金刚石/新材料": ["金刚石", "散热材料", "碳化硅", "蓝宝石"],
    "AI应用/软件": ["AI应用", "大模型", "Agent", "Token", "短剧", "视频生成", "软件", "信创"],
    "消费/其他": ["白酒", "食品", "零售", "旅游", "服装"],
}


def tag_themes(text):
    hit = []
    for t, kws in THEMES.items():
        if any(k in text for k in kws):
            hit.append(t)
    return hit


def q(arr, p):
    if not arr:
        return None
    s = sorted(arr)
    i = min(len(s) - 1, max(0, int(len(s) * p)))
    return round(s[i], 2)


def block(rows):
    """rows: [(r1..rN, mfe3, mfe5, mfe10, mae5, lu5)]"""
    if not rows:
        return {"n": 0}
    def col(i):
        return [r[i] for r in rows if r[i] is not None]
    r1, r5, r10 = col(0), col(1), col(2)
    mfe5, mfe10, mae5 = col(3), col(4), col(5)
    lu5 = col(6)
    n = len(rows)
    out = {"n": n}
    for name, arr in (("r1", r1), ("r5", r5), ("r10", r10)):
        if not arr:
            continue
        out[name] = {"avg": round(sum(arr) / len(arr), 2), "median": q(arr, .5),
                     "p90": q(arr, .9), "max": round(max(arr), 1), "min": round(min(arr), 1),
                     "winrate": round(100.0 * sum(1 for x in arr if x > 0) / len(arr), 1),
                     "gt10": round(100.0 * sum(1 for x in arr if x > 10) / len(arr), 1),
                     "gt20": round(100.0 * sum(1 for x in arr if x > 20) / len(arr), 1)}
    for name, arr in (("mfe5", mfe5), ("mfe10", mfe10), ("mae5", mae5)):
        if not arr:
            continue
        out[name] = {"median": q(arr, .5), "p75": q(arr, .75), "p90": q(arr, .9),
                     "max": round(max(arr), 1), "min": round(min(arr), 1),
                     "gt10": round(100.0 * sum(1 for x in arr if x > 10) / len(arr), 1),
                     "gt20": round(100.0 * sum(1 for x in arr if x > 20) / len(arr), 1)}
    if lu5:
        out["lu5"] = {"avg_days": round(sum(lu5) / len(lu5), 2),
                      "any_hit": round(100.0 * sum(1 for x in lu5 if x > 0) / len(lu5), 1)}
    return out


def main():
    sig = json.load(open(SIG, encoding="utf-8"))
    js = [x for x in sig if x["group"] == "jieshi" and WIN_FROM <= x["date"] <= WIN_TO]
    print("jieshi signals in window:", len(js))

    # 聚合到 (code, date)
    agg = {}
    for x in js:
        k = (x["code"], x["date"])
        a = agg.setdefault(k, {"code": x["code"], "date": x["date"], "name": x["name"],
                               "kinds": set(), "pols": set(), "text": x["text"], "themes": set()})
        a["kinds"].add(x["kind"])
        a["pols"].add(x["polarity"])
        for t in tag_themes(x["text"]):
            a["themes"].add(t)
    rows = list(agg.values())
    print("code-days:", len(rows))

    codes = sorted({r["code"] for r in rows})
    kl = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, k in zip(codes, ex.map(get_kline, codes)):
            if k:
                kl[c] = Series(k)
    print("kline ok:", len(kl))

    def measure(info, code, date):
        S = kl.get(code)
        if not S:
            return None
        c0 = S.at(date, "close")
        if not c0:
            return None
        i = S.pos.get(date)
        if i is None or i + 1 >= len(S.kl):
            return None
        pct = limit_pct(code, info.get("name", ""))

        def close_ret(n):
            d = S.shift(date, n)
            return S.ret(date, d) if d else None

        hi, lo, lu = [], [], 0
        for n in range(1, 11):
            j = i + n
            if j >= len(S.kl):
                break
            hi.append((S.kl[j]["high"] / c0 - 1) * 100)
            if n <= 5:
                lo.append((S.kl[j]["low"] / c0 - 1) * 100)
                prev = S.kl[j - 1]["close"]
                if prev and (S.kl[j]["close"] / prev - 1) * 100 >= pct - 0.4:
                    lu += 1
        if not hi:
            return None
        return {
            "r1": close_ret(1), "r5": close_ret(5), "r10": close_ret(10),
            "mfe3": max(hi[:3]), "mfe5": max(hi[:5]), "mfe10": max(hi),
            "mae5": min(lo) if lo else None, "lu5": lu,
        }

    for r in rows:
        m = measure(r, r["code"], r["date"])
        r["m"] = m
        r["themes"] = sorted(r["themes"])
    rows = [r for r in rows if r["m"]]
    print("measured:", len(rows))

    def pick(fn):
        return [r for r in rows if fn(r)]

    def pack(sel):
        return block([(r["m"]["r1"], r["m"]["r5"], r["m"]["r10"],
                       r["m"]["mfe5"], r["m"]["mfe10"], r["m"]["mae5"], r["m"]["lu5"]) for r in sel])

    groups = {
        "借势·老师看多": pack(pick(lambda r: "teacher" in r["kinds"] and "pos" in r["pols"])),
        "借势·老师全部提及": pack(pick(lambda r: "teacher" in r["kinds"])),
        "借势·研报看多": pack(pick(lambda r: "research" in r["kinds"] and "pos" in r["pols"])),
        "借势·全部(含仅提及)": pack(rows),
    }

    # 全市场等权基准（同窗口）
    uni = []
    for c, S in list(kl.items())[:1200]:
        for d in S.dates:
            if WIN_FROM <= d <= WIN_TO:
                m = measure({"name": ""}, c, d)
                if m:
                    uni.append(m)
    print("universe code-days:", len(uni))
    # 同代码随机日基准：只取信号涉及的那批股票，但在窗口内任意一天入场（控制「选股 vs 择时」）
    same = []
    for c in codes:
        S = kl.get(c)
        if not S:
            continue
        for d in S.dates:
            if WIN_FROM <= d <= WIN_TO:
                m = measure({"name": ""}, c, d)
                if m:
                    same.append(m)
    print("same-code code-days:", len(same))
    groups["基准·同池随机日_dup"] = block([(m["r1"], m["r5"], m["r10"], m["mfe5"], m["mfe10"], m["mae5"], m["lu5"]) for m in same])
    groups["基准·同池随机日(358只)"] = block([(m["r1"], m["r5"], m["r10"], m["mfe5"], m["mfe10"], m["mae5"], m["lu5"]) for m in uni])

    # 题材维度（只取老师看多 + 全部提及）
    themes = {}
    for t in THEMES:
        sel = pick(lambda r, t=t: t in r["themes"])
        if len(sel) >= 5:
            themes[t] = pack(sel)

    res = {"window": [WIN_FROM, WIN_TO], "groups": groups, "themes": themes,
           "top": sorted([{k: r[k] for k in ("code", "name", "date", "themes")} | {"mfe10": round(r["m"]["mfe10"], 1), "r5": None if r["m"]["r5"] is None else round(r["m"]["r5"], 1), "r1": None if r["m"]["r1"] is None else round(r["m"]["r1"], 1)}
                          for r in rows], key=lambda x: -(x["mfe10"] or -99))[:30]}
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=list)
    print("wrote", OUT)

    for g, b in groups.items():
        if not b.get("n"):
            continue
        print("\n%-18s n=%d" % (g, b["n"]))
        for k in ("r1", "r5", "r10", "mfe5", "mfe10", "mae5"):
            if k in b:
                x = b[k]
                print("   %-6s 中位 %6s  p90 %6s  max %6s  >10%% %5s%%  >20%% %5s%%%s"
                      % (k, x["median"], x["p90"], x["max"], x["gt10"], x["gt20"],
                         ("  胜率 %s%%" % x["winrate"]) if "winrate" in x else ""))
        if "lu5" in b:
            print("   涨停 D+1..5 均 %.2f 天, 命中 %s%%" % (b["lu5"]["avg_days"], b["lu5"]["any_hit"]))

    print("\n== 题材 ==")
    for t, b in sorted(themes.items(), key=lambda kv: -(kv[1]["mfe10"]["median"] if kv[1].get("mfe10") else -99)):
        print("%-14s n=%3d  r5中位 %6s  mfe10中位 %6s  mfe10>20%% %4s%%" %
              (t, b["n"], b.get("r5", {}).get("median"), b.get("mfe10", {}).get("median"),
               b.get("mfe10", {}).get("gt20")))

    print("\n== 右尾最大 12 只 ==")
    for x in res["top"][:12]:
        print("  %s %s %s mfe10 %+.0f%% r5 %s" % (x["date"], x["code"], x["name"], x["mfe10"], x["r5"]))


if __name__ == "__main__":
    main()
