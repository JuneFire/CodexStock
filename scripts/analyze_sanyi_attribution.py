"""竞价三一归因分析：56 笔信号逐笔对照大盘/情绪/板块/题材，找出赚钱与亏钱的原因。

口径：D 日 09:25 出信号 → D 日开盘买 → D+1 收盘卖（ret_c）/ D+1 开盘卖（ret_o）。
用法：python scripts/analyze_sanyi_attribution.py [--cap 3000]
输出：reports/sanyi_attribution.json + 控制台报表
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import statistics
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)


def load_json(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def num(v, default=None):
    try:
        if v is None or v == "":
            return default
        f = float(v)
        return f if f == f else default
    except Exception:
        return default


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def tstat(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return None
    m = mean(xs)
    sd = statistics.pstdev(xs) * (len(xs) / max(1.0, len(xs) - 1)) ** 0.5
    return m / (sd / len(xs) ** 0.5) if sd > 0 else None


def pct_rank(hist, v):
    if not hist or v is None:
        return None
    n = sum(1 for x in hist if x < v)
    eq = sum(1 for x in hist if x == v)
    return 100.0 * (n + 0.5 * eq) / len(hist)


def win_rate(xs):
    xs = [x for x in xs if x is not None]
    return 100.0 * sum(1 for x in xs if x > 0) / len(xs) if xs else None


# ---------- 基础数据 ----------

def load_index():
    bars = load_json(P("data", "index_kline", "sh000001.json"), []) or []
    out = {}
    for i, b in enumerate(bars):
        c, pc = num(b.get("close")), num(bars[i - 1].get("close")) if i else None
        ma = lambda n: mean([num(x.get("close")) for x in bars[max(0, i - n + 1): i + 1]])
        ma5, ma10, ma20 = ma(5), ma(10), ma(20)
        out[b["date"]] = {
            "close": c,
            "chg": (c / pc - 1) * 100 if c and pc else None,
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "dev20": (c / ma20 - 1) * 100 if c and ma20 else None,
            "bear": bool(ma5 and ma10 and ma20 and ma5 < ma10 < ma20),
            "bull": bool(ma5 and ma10 and ma20 and ma5 > ma10 > ma20),
            "i": i,
        }
    return out, [b["date"] for b in bars]


def load_ztpool():
    """{date: {'n':涨停数,'maxTier':最高连板,'codes':{code:row},'themes':Counter}}"""
    out = {}
    for f in sorted(glob.glob(P("data", "market", "ztpool_bt", "*.json"))):
        d = os.path.basename(f)[:-5]
        z = load_json(f) or {}
        rows = z.get("rows") or []
        cnt = Counter()
        for r in rows:
            reason = (r.get("limitUpReason") or "").strip()
            if not reason:
                continue
            for tok in reason.replace("＋", "+").replace("、", "+").replace(",", "+").replace("，", "+").split("+"):
                tok = tok.strip()
                if len(tok) >= 2:
                    cnt[tok] += 1
        out[d] = {
            "n": num(z.get("total"), len(rows)),
            "maxTier": max([num(r.get("lb"), 1) or 1 for r in rows], default=0),
            "codes": {str(r.get("code")).zfill(6): r for r in rows},
            "themes": cnt,
        }
    return out


def load_auction_day(d):
    a = load_json(P("data", "auction_top", d + ".json")) or {}
    return a.get("records") or a.get("rows") or []


def load_sentiment(d):
    a = load_json(P("data", "auction_hist", d + ".json")) or {}
    return a.get("sentiment") or {}


GENERIC = {"地方国企", "央企", "国企改革", "三季报增长", "中报增长", "年报增长", "业绩预增",
           "并购重组", "股权转让", "举牌", "增持", "回购", "摘帽", "超跌反弹", "高送转",
           "员工持股", "公告", "拟收购", "实控人变更", "地方国资"}


def reason_strength(reason, zt, d):
    """涨停原因主词在当日涨停池的家数（只对当日涨停票有值）"""
    z = zt.get(d)
    if not z or not reason:
        return None, None
    toks = [t.strip() for t in reason.replace("＋", "+").split("+") if t.strip()]
    if not toks:
        return None, None
    prim = toks[0]
    n = sum(1 for r in z["codes"].values() if prim in (r.get("limitUpReason") or ""))
    return prim, n


def theme_strength(themes, zt, d):
    """信号股所属题材在 d 日的涨停家数（取最强题材）"""
    z = zt.get(d)
    if not z or not themes:
        return None, None
    best, bestn = None, -1
    for t in themes:
        t = (t or "").strip()
        if len(t) < 2:
            continue
        n = 0
        for code, r in z["codes"].items():
            if t in (r.get("limitUpReason") or ""):
                n += 1
        if n > bestn:
            best, bestn = t, n
    return best, (bestn if bestn >= 0 else None)


# ---------- 主流程 ----------

def build(cap):
    capdata = load_json(P("reports", "backtest_sanyi_cap.json")) or {}
    res = (capdata.get("res") or {}).get(str(cap)) or capdata.get("res", {}).get("3000")
    trades = res.get("trades") or []
    idx, cal = load_index()
    zt = load_ztpool()

    # 最高连板高度的滚动 60 日分位
    tiers = [(d, zt[d]["maxTier"]) for d in sorted(zt)]
    tier_hist = {}
    for i, (d, _) in enumerate(tiers):
        tier_hist[d] = [t for _, t in tiers[max(0, i - 60): i]]

    rows = []
    for t in trades:
        d = t["date"]
        code = str(t["code"]).zfill(6)
        i = cal.index(d) if d in cal else None
        d1 = cal[i + 1] if i is not None and i + 1 < len(cal) else None
        d_1 = cal[i - 1] if i is not None and i > 0 else None

        ix, ix1 = idx.get(d) or {}, idx.get(d1) or {} if d1 else {}
        ixm1 = idx.get(d_1) or {}

        arch = {str(r.get("code")).zfill(6): r for r in load_auction_day(d)}
        a = arch.get(code) or {}

        z = zt.get(d) or {}
        zrow = (z.get("codes") or {}).get(code)
        sent = load_sentiment(d)

        themes = a.get("themes") or ([a.get("industry")] if a.get("industry") else [])
        tb, tn = theme_strength(themes, zt, d)
        _, tn1 = theme_strength(themes, zt, d_1) if d_1 else (None, None)

        gap = num(a.get("obp"))
        if gap is None:
            px, pc = num(a.get("open")) or num(a.get("price")), num(a.get("prevClose"))
            gap = (px / pc - 1) * 100 if px and pc else None

        ru = (zrow or {}).get("limitUpReason")
        rprim, rn = reason_strength(ru, zt, d)
        pret = num(sent.get("prevLimitUp"))
        pbrk = num(sent.get("prevBroken"))
        rows.append({
            "date": d, "code": code, "name": t.get("name"),
            "ret_c": num(t.get("ret_c")), "ret_o": num(t.get("ret_o")),
            "circYi": num(t.get("circYi")), "rAmt": t.get("rAmt"), "rTrf": t.get("rTrf"),
            # 个股
            "gap": gap,
            "amtYi": (num(a.get("auctionAmount")) or 0) / 1e8 or None,
            "trfPct": (num(a.get("trf")) or 0) or None,
            "vr": num(a.get("volumeRatio")),
            "ps": num(a.get("ps"), 0),
            "isLu": bool(zrow),
            "luReason": ru, "reasonTok": rprim, "reasonN": rn,
            "lb": num((zrow or {}).get("lb")),
            "luTime": (zrow or {}).get("limitUpTime"),
            "openTimes": num((zrow or {}).get("openTimes"), 0),
            # 大盘
            "idxD": ix.get("chg"), "idxD1": ix1.get("chg"),
            "dev20D1": ixm1.get("dev20"),
            "bearD1": ixm1.get("bear"),
            "idxD1close": ix1.get("close"), "idxDclose": ix.get("close"),
            # 情绪
            "limitUpN": z.get("n"), "maxTier": z.get("maxTier"),
            "tierPct": pct_rank(tier_hist.get(d) or [], z.get("maxTier")),
            "prevLimitUp": pret, "prevBroken": pbrk,
            "breakRate": (100.0 * pbrk / (pret + pbrk)) if pret and pbrk is not None and (pret + pbrk) else None,
            "auctionLu": num(sent.get("limitUpOpen")), "auctionLd": num(sent.get("limitDownOpen")),
            "lb2": num(sent.get("lb2")), "lb3": num(sent.get("lb3")),
            # 题材
            "theme": tb, "themeN": tn, "themeN1": tn1,
            "themeDelta": (tn - tn1) if (tn is not None and tn1 is not None) else None,
            "themes": themes[:4],
        })
    return rows, res, zt, idx, cal


def fmt(v, d=2, suf=""):
    if v is None:
        return "—"
    return ("%." + str(d) + "f%s") % (v, suf)


def group_table(rows, key, buckets, title, field="ret_c"):
    print("\n### %s" % title)
    print("| 分档 | n | 均值 | 中位 | 胜率 | t | 最好 | 最差 |")
    print("|---|---|---|---|---|---|---|---|")
    for label, lo, hi in buckets:
        sel = [r for r in rows if r.get(key) is not None and lo <= r[key] < hi]
        if not sel:
            continue
        v = [r[field] for r in sel]
        print("| %s | %d | %s%% | %s%% | %s%% | %s | %s%% | %s%% |" % (
            label, len(sel), fmt(mean(v)), fmt(median(v)), fmt(win_rate(v)),
            fmt(tstat(v)), fmt(max(v)), fmt(min(v))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=3000.0)
    args = ap.parse_args()
    cap = int(args.cap) if args.cap == int(args.cap) else args.cap

    rows, res, zt, idx, cal = build(cap)
    print("# 竞价三一归因分析（%d 笔，cap=%s）" % (len(rows), cap))
    print("\n口径：D 日 09:25 出信号 → D 日开盘买 → D+1 收盘卖（ret_c）")

    # ---- 1. 总体 ----
    ret = [r["ret_c"] for r in rows]
    print("\n## 1. 总体")
    print("- 均值 %s%%，中位 %s%%，胜率 %s%%，t=%s" % (
        fmt(mean(ret)), fmt(median(ret)), fmt(win_rate(ret)), fmt(tstat(ret))))
    bins = [("大赚 >+10%", 10, 1e9), ("中赚 +5~10%", 5, 10), ("小赚 0~+5%", 0, 5),
            ("小亏 -5~0%", -5, 0), ("中亏 -10~-5%", -10, -5), ("大亏 <-10%", -1e9, -10)]
    print("\n| 区间 | n | 占比 |")
    print("|---|---|---|")
    for label, lo, hi in bins:
        n = sum(1 for x in ret if lo <= x < hi)
        print("| %s | %d | %.0f%% |" % (label, n, 100.0 * n / len(ret)))

    # ---- 2. 赢家 vs 输家 全维度对比 ----
    win = [r for r in rows if r["ret_c"] and r["ret_c"] > 0]
    lose = [r for r in rows if r["ret_c"] is not None and r["ret_c"] <= 0]
    print("\n## 2. 赢家（%d 笔）vs 输家（%d 笔）各维度均值" % (len(win), len(lose)))
    dims = [
        ("ret_c", "次日收盘卖收益", "%"),
        ("gap", "竞价涨幅", "%"),
        ("amtYi", "竞价额", "亿"),
        ("trfPct", "竞价换手(自由流通)", "%"),
        ("vr", "竞价量比", ""),
        ("circYi", "自由流通市值", "亿"),
        ("ps", "昨日连板数", ""),
        ("idxD", "上证当日涨跌", "%"),
        ("idxD1", "上证次日涨跌", "%"),
        ("dev20D1", "上证D-1距MA20", "%"),
        ("limitUpN", "当日全市场涨停数", "只"),
        ("maxTier", "当日最高连板", "板"),
        ("tierPct", "最高连板60日分位", "%"),
        ("prevLimitUp", "昨日涨停数", "只"),
        ("breakRate", "昨日炸板率", "%"),
        ("auctionLu", "竞价涨停数", "只"),
        ("auctionLd", "竞价跌停数", "只"),
        ("themeN", "题材当日涨停家数", "只"),
        ("themeN1", "题材昨日涨停家数", "只"),
        ("themeDelta", "题材家数变化", "只"),
    ]
    print("\n| 维度 | 赢家均值 | 输家均值 | 差值 | 赢家t |")
    print("|---|---|---|---|---|")
    for k, label, suf in dims:
        a = [r[k] for r in win if r.get(k) is not None]
        b = [r[k] for r in lose if r.get(k) is not None]
        if not a or not b:
            continue
        ma, mb = mean(a), mean(b)
        print("| %s | %s%s | %s%s | %s | %s |" % (
            label, fmt(ma), suf, fmt(mb), suf, fmt(ma - mb), fmt(tstat(a))))

    # ---- 3. 分档分析 ----
    print("\n## 3. 分档")
    group_table(rows, "idxD", [
        ("上证跌 <-1.5%", -1e9, -1.5), ("跌 -1.5~-0.5%", -1.5, -0.5),
        ("跌 -0.5~0%", -0.5, 0), ("涨 0~0.5%", 0, 0.5),
        ("涨 0.5~1.5%", 0.5, 1.5), ("涨 >1.5%", 1.5, 1e9),
    ], "按「上证 D 日涨跌」（当日大盘表现）")
    group_table(rows, "idxD1", [
        ("次日跌 <-2%", -1e9, -2), ("次日跌 -2~-1%", -2, -1), ("次日跌 -1~0%", -1, 0),
        ("次日涨 0~1%", 0, 1), ("次日涨 1~2%", 1, 2), ("次日涨 >2%", 2, 1e9),
    ], "按「上证 D+1 日涨跌」（次日大盘，事后解释用）")
    group_table(rows, "gap", [
        ("低开 <-5%", -1e9, -5), ("低开 -5~-2%", -5, -2), ("低开 -2~0%", -2, 0),
        ("高开 0~3%", 0, 3), ("高开 3~7%", 3, 7), ("高开 >7%", 7, 1e9),
    ], "按「竞价涨幅」")
    group_table(rows, "limitUpN", [
        ("<40 只（冰点）", 0, 40), ("40~60 只", 40, 60), ("60~90 只", 60, 90),
        ("90~120 只", 90, 120), (">120 只（高潮）", 120, 1e9),
    ], "按「当日全市场涨停数」")
    group_table(rows, "tierPct", [
        ("<20%（冰点）", 0, 20), ("20~50%", 20, 50), ("50~80%", 50, 80), (">80%（高潮）", 80, 101),
    ], "按「当日最高连板高度的 60 日分位」")
    group_table(rows, "themeN", [
        ("题材 0~1 只（孤立）", 0, 2), ("题材 2~3 只", 2, 4), ("题材 4~6 只", 4, 7),
        ("题材 7~10 只", 7, 11), ("题材 >10 只（主线）", 11, 1e9),
    ], "按「所属题材当日涨停家数」")
    group_table(rows, "themeDelta", [
        ("扩散收缩 <-2", -1e9, -2), ("略收缩 -2~0", -2, 0), ("持平", 0, 1),
        ("扩散 +1~3", 1, 4), ("强扩散 >3", 4, 1e9),
    ], "按「所属题材涨停家数变化（当日 − 昨日）」")
    group_table(rows, "dev20D1", [
        ("< -3%", -1e9, -3), ("-3~-1%", -3, -1), ("-1~+1%", -1, 1), ("> +1%", 1, 1e9),
    ], "按「上证 D-1 收盘距 MA20」")
    group_table(rows, "circYi", [
        ("<50 亿", 0, 50), ("50~100 亿", 50, 100), ("100~200 亿", 100, 200),
        ("200~500 亿", 200, 500), (">500 亿", 500, 1e9),
    ], "按「自由流通市值」")
    group_table(rows, "ps", [
        ("昨日未涨停 (ps=0)", 0, 1), ("昨日 1~2 板", 1, 3), ("昨日 3 板以上", 3, 1e9),
    ], "按「昨日连板数」")

    # ---- 4. 逐笔清单 ----
    print("\n## 4. 逐笔清单（按收益降序）")
    print("| D日 | 代码 | 名称 | 收益 | 竞价涨 | 上证D | 上证D+1 | 涨停数 | 最高板 | 题材 | 题材家数(D/昨) | 板数 | 备注 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: -(x["ret_c"] or -99)):
        note = ""
        if r["isLu"]:
            note = "涨停 %s%s" % (r["luTime"] or "", ("·开板%d次" % r["openTimes"]) if r["openTimes"] else "")
        print("| %s | %s | %s | %s%% | %s%% | %s%% | %s%% | %s | %s | %s | %s/%s | %s | %s |" % (
            r["date"], r["code"], r["name"], fmt(r["ret_c"]), fmt(r["gap"]),
            fmt(r["idxD"]), fmt(r["idxD1"]), fmt(r["limitUpN"], 0), fmt(r["maxTier"], 0),
            r["theme"] or "—", fmt(r["themeN"], 0), fmt(r["themeN1"], 0),
            fmt(r["ps"], 0), note))

    # ---- 5. 最亏的几笔细看 ----
    print("\n## 5. 最亏 8 笔明细")
    for r in sorted(rows, key=lambda x: (x["ret_c"] if x["ret_c"] is not None else 99))[:8]:
        print("\n- **%s %s %s** 收益 %s%%（次日开盘卖 %s%%）" % (
            r["date"], r["code"], r["name"], fmt(r["ret_c"]), fmt(r["ret_o"])))
        print("  竞价涨幅 %s%% · 竞价额 %s亿 · 换手 %s%% · 量比 %s · 流通 %s亿 · 昨日%s板" % (
            fmt(r["gap"]), fmt(r["amtYi"]), fmt(r["trfPct"]), fmt(r["vr"]), fmt(r["circYi"]), fmt(r["ps"], 0)))
        print("  大盘 D日 %s%% / D+1 %s%% · 距MA20(D-1) %s%%" % (
            fmt(r["idxD"]), fmt(r["idxD1"]), fmt(r["dev20D1"])))
        print("  涨停 %s只 / 最高%s板(分位%s%%) / 昨涨停%s只 / 炸板率%s%% / 竞价涨停%s只" % (
            fmt(r["limitUpN"], 0), fmt(r["maxTier"], 0), fmt(r["tierPct"], 0),
            fmt(r["prevLimitUp"], 0), fmt(r["breakRate"], 0), fmt(r["auctionLu"], 0)))
        print("  题材：%s（当日 %s 只 / 昨日 %s 只）%s" % (
            r["theme"], fmt(r["themeN"], 0), fmt(r["themeN1"], 0),
            ("· 涨停 %s %s" % (r["luReason"], r["luTime"])) if r["isLu"] else "· 当日未涨停"))

    # ---- 6. 涨停票 vs 未涨停票 ----
    lu = [r for r in rows if r["isLu"]]
    nlu = [r for r in rows if not r["isLu"]]
    print("\n## 6. 当日涨停（%d 笔）vs 未涨停（%d 笔）" % (len(lu), len(nlu)))
    for label, g in (("当日涨停", lu), ("当日未涨停", nlu)):
        v = [r["ret_c"] for r in g]
        print("- %s：均值 %s%% · 中位 %s%% · 胜率 %s%% · t=%s" % (
            label, fmt(mean(v)), fmt(median(v)), fmt(win_rate(v)), fmt(tstat(v))))

    if lu:
        print("\n### 涨停票按「涨停原因主词家数」")
        print("| 主词家数 | n | 均值 | 中位 | 胜率 |")
        print("|---|---|---|---|---|")
        for label, lo, hi in [("仅它自己 (1 只)", 0, 2), ("2~3 只", 2, 4), ("4~7 只", 4, 8), (">=8 只（主线）", 8, 1e9)]:
            sel = [r for r in lu if r.get("reasonN") is not None and lo <= r["reasonN"] < hi]
            if not sel:
                continue
            v = [r["ret_c"] for r in sel]
            print("| %s | %d | %s%% | %s%% | %s%% |" % (label, len(sel), fmt(mean(v)), fmt(median(v)), fmt(win_rate(v))))

    # ---- 7. 组合叠加 ----
    print("\n## 7. 逐层叠加过滤（找最优组合）")
    layers = [
        ("① 全部三一", lambda r: True),
        ("② + 题材当日涨停 ≥2 只", lambda r: (r.get("themeN") or 0) >= 2),
        ("③ + 上证 D 日收红", lambda r: (r.get("idxD") or 0) > 0),
        ("④ + 自由流通 100~200 亿", lambda r: 100 <= (r.get("circYi") or 0) < 200),
        ("⑤ + 竞价低开 < -2%", lambda r: (r.get("gap") or 0) < -2),
    ]
    cur = rows
    print("\n| 叠加层 | n | 均值 | 中位 | 胜率 | t | 累计 |")
    print("|---|---|---|---|---|---|---|")
    for label, fn in layers:
        cur = [r for r in cur if fn(r)]
        v = [r["ret_c"] for r in cur]
        if not v:
            print("| %s | 0 | — | — | — | — | — |" % label)
            continue
        print("| %s | %d | %s%% | %s%% | %s%% | %s | %s%% |" % (
            label, len(v), fmt(mean(v)), fmt(median(v)), fmt(win_rate(v)), fmt(tstat(v)), fmt(sum(v))))

    # 单条件 vs 双重条件（两两）
    print("\n### 两两组合（每组只保留 n≥8 的）")
    print("| 组合 | n | 均值 | 胜率 | t |")
    print("|---|---|---|---|---|")
    conds = [
        ("题材≥2只", lambda r: (r.get("themeN") or 0) >= 2),
        ("上证D收红", lambda r: (r.get("idxD") or 0) > 0),
        ("市值100-200亿", lambda r: 100 <= (r.get("circYi") or 0) < 200),
        ("低开<-2%", lambda r: (r.get("gap") or 0) < -2),
        ("高开>2%", lambda r: (r.get("gap") or 0) > 2),
        ("昨日未涨停", lambda r: (r.get("ps") or 0) == 0),
        ("昨日≥3板", lambda r: (r.get("ps") or 0) >= 3),
        ("涨停数≥90", lambda r: (r.get("limitUpN") or 0) >= 90),
    ]
    for i in range(len(conds)):
        for j in range(i + 1, len(conds)):
            (na, fa), (nb, fb) = conds[i], conds[j]
            sel = [r for r in rows if fa(r) and fb(r)]
            if len(sel) < 8:
                continue
            v = [r["ret_c"] for r in sel]
            print("| %s + %s | %d | %s%% | %s%% | %s |" % (
                na, nb, len(v), fmt(mean(v)), fmt(win_rate(v)), fmt(tstat(v))))

    # ---- 8. 相关性 ----
    print("\n## 8. 各维度与收益的相关性（Spearman，n=56）")
    def spearman(xs, ys):
        pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        if len(pairs) < 8:
            return None
        rx = {v: i for i, v in enumerate(sorted(set(x for x, _ in pairs)))}
        ry = {v: i for i, v in enumerate(sorted(set(y for _, y in pairs)))}
        a = [rx[x] for x, _ in pairs]
        b = [ry[y] for _, y in pairs]
        ma, mb = mean(a), mean(b)
        va = sum((x - ma) ** 2 for x in a) ** 0.5
        vb = sum((y - mb) ** 2 for y in b) ** 0.5
        if not va or not vb:
            return None
        return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb)

    keys = [("themeN", "题材当日涨停家数"), ("reasonN", "涨停原因主词家数"),
            ("idxD", "上证当日涨跌"), ("limitUpN", "当日涨停总数"),
            ("maxTier", "当日最高连板"), ("tierPct", "最高连板分位"),
            ("prevLimitUp", "昨日涨停数"), ("breakRate", "昨日炸板率"),
            ("gap", "竞价涨幅"), ("amtYi", "竞价额"), ("trfPct", "竞价换手"),
            ("vr", "竞价量比"), ("circYi", "自由流通市值"), ("ps", "昨日连板数"),
            ("themeDelta", "题材家数变化"), ("dev20D1", "距MA20(D-1)"),
            ("auctionLu", "竞价涨停数"), ("auctionLd", "竞价跌停数")]
    res2 = []
    for k, label in keys:
        rho = spearman([r.get(k) for r in rows], [r.get("ret_c") for r in rows])
        res2.append((label, rho))
    for label, rho in sorted(res2, key=lambda x: -(abs(x[1]) if x[1] is not None else -1)):
        print("- %s：ρ = %s" % (label, fmt(rho, 3)))

    with io.open(P("reports", "sanyi_attribution.json"), "w", encoding="utf-8") as f:
        json.dump({"cap": cap, "rows": rows}, f, ensure_ascii=False, indent=1)
    print("\n明细已写入 reports/sanyi_attribution.json")


if __name__ == "__main__":
    main()
