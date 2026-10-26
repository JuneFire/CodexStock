# -*- coding: utf-8 -*-
"""共振复判（口径统一版）

用法: python scripts/resonance_v2.py

输出: data/laiqiu/backtest/resonance_v2.json

三层样本
  S 严格方向  polarity=pos/neg（短线群专用黑话词典）
  M 提及      仅要求出现个股别名（含中性语境）
  R 共振      借势老师看多 × 短线群同日/±1 日

入场口径与 group_backtest.py 一致: entry=close(D)，rN=close(D+N)/close(D)-1。
"""
import json
import os
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import group_signals as gs      # noqa: E402
import group_backtest as gb     # noqa: E402

# 腾讯K线口已被 WAF 拦截，禁止回源（缺失票直接跳过）
gb.fetch_kline = lambda *a, **k: []

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIG = os.path.join(ROOT, "data", "laiqiu", "backtest", "group_signals.json")
OUT = os.path.join(ROOT, "data", "laiqiu", "backtest", "resonance_v2.json")

SHORT = ("hongniu", "linshangwo", "jshen")
RES_W = ("2026-06-29", "2026-10-23")     # 三群/借势 重叠窗口（共振判定用）

_SER = {}


def series(code):
    if code not in _SER:
        kl = gb.get_kline(code)
        _SER[code] = gb.Series(kl) if kl else None
    return _SER[code]


def rets(code, date):
    s = series(code)
    if s is None or date not in s.pos:
        return None
    out = {}
    c0 = s.at(date, "close")
    for n in (1, 3, 5):
        c1 = s.at(s.shift(date, n), "close") if s.shift(date, n) else None
        out["r%d" % n] = round(100.0 * (c1 / c0 - 1), 2) if c1 else None
    return out if out.get("r1") is not None else None


def stat(rows):
    """rows: [(date, code, dict r1/r3/r5)]"""
    res = {"n": len(rows)}
    for h in (1, 3, 5):
        v = [r[2]["r%d" % h] for r in rows if r[2].get("r%d" % h) is not None]
        res["d%d" % h] = gb.metrics(v) if v else {"n": 0}
    return res


_UDM = {}
_UNI = None


def build_universe(window=None):
    """一次遍历 K 线缓存：等权 r1/r3/r5 序列 + 每日均值。"""
    global _UNI
    w = window or RES_W
    if _UNI is not None:
        return _UNI
    series_by_h = {1: [], 3: [], 5: []}
    day = {1: collections.defaultdict(list), 3: collections.defaultdict(list),
           5: collections.defaultdict(list)}
    n = 0
    for fn in os.listdir(gb.KL_DIR):
        if not fn.endswith(".json") or fn.endswith("_idx.json"):
            continue
        try:
            kl = json.load(open(os.path.join(gb.KL_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        if not kl:
            continue
        n += 1
        for i, r in enumerate(kl):
            d = r["date"]
            if not (w[0] <= d <= w[1]) or not r["close"]:
                continue
            for h in (1, 3, 5):
                j = i + h
                if j >= len(kl):
                    continue
                v = round(100.0 * (kl[j]["close"] / r["close"] - 1), 2)
                series_by_h[h].append(v)
                day[h][d].append(v)
    _UNI = {"codes": n, "series": series_by_h,
            "mean": {h: {d: sum(v) / len(v) for d, v in day[h].items()} for h in day}}
    return _UNI


def uni_day_mean(h=1, window=None):
    return build_universe(window)["mean"][h]


def excess(rows, h=1):
    mean = uni_day_mean(h)
    vals = []
    for d, c, r in rows:
        base = mean.get(d)
        v = r.get("r%d" % h)
        if base is None or v is None:
            continue
        vals.append(round(v - base, 2))
    if not vals:
        return {"n": 0}
    m = gb.metrics(vals)
    m["n"] = len(vals)
    return m


def main():
    sigs = json.load(open(SIG, encoding="utf-8"))

    # ---------- 短线群两层索引 ----------
    strict = collections.defaultdict(set)   # (date,code) -> {group}
    mention = collections.defaultdict(set)
    by_date_mention = collections.defaultdict(set)   # date -> {code}
    short_rows = []
    for s in sigs:
        if s["group"] not in SHORT:
            continue
        key = (s["date"], s["code"])
        mention[key].add(s["group"])
        by_date_mention[s["date"]].add(s["code"])
        if s["polarity"] != "neutral":
            strict[key].add(s["group"])
        short_rows.append(s)

    # 交易日前后一天用 K 线序列求（以信号票自身的序列为准，票停牌时退化为同日/前后各 1 个交易日）
    def neighbours(code, date):
        s = series(code)
        if s is None or date not in s.pos:
            return [date]
        ds = [date]
        for n in (-1, 1):
            d = s.shift(date, n)
            if d:
                ds.append(d)
        return ds

    # ---------- A. 短线群 standalone ----------
    A = {}
    for g in SHORT + ("all",):
        for layer in ("strict", "mention"):
            rows = []
            for s in short_rows:
                if g != "all" and s["group"] != g:
                    continue
                if layer == "strict" and s["polarity"] == "neutral":
                    continue
                if layer == "mention" and s["kind"] not in ("mention", "text"):
                    continue
                r = rets(s["code"], s["date"])
                if r:
                    rows.append((s["date"], s["code"], r))
            # 同 (date,code) 去重
            seen, uniq = set(), []
            for row in rows:
                if (row[0], row[1]) in seen:
                    continue
                seen.add((row[0], row[1]))
                uniq.append(row)
            A["%s|%s" % (g, layer)] = stat(uniq)
            A["%s|%s" % (g, layer)]["excess_d1"] = excess(uniq)

    # 短线群严格层按方向拆
    for pol in ("pos", "neg"):
        rows = []
        for s in short_rows:
            if s["polarity"] != pol:
                continue
            r = rets(s["code"], s["date"])
            if r:
                rows.append((s["date"], s["code"], r))
        seen, uniq = set(), []
        for row in rows:
            if (row[0], row[1]) in seen:
                continue
            seen.add((row[0], row[1]))
            uniq.append(row)
        A["all|%s" % pol] = stat(uniq)
        A["all|%s" % pol]["excess_d1"] = excess(uniq)

    # ---------- B. 共振 ----------
    def base_rows(kind):
        rows, seen = [], set()
        for s in sigs:
            if s["group"] != "jieshi" or s["kind"] != kind or s["polarity"] != "pos":
                continue
            if not (RES_W[0] <= s["date"] <= RES_W[1]):
                continue
            if (s["date"], s["code"]) in seen:
                continue
            seen.add((s["date"], s["code"]))
            r = rets(s["code"], s["date"])
            if r:
                rows.append((s["date"], s["code"], r))
        return rows

    B = {}
    for kind in ("teacher", "research"):
        base = base_rows(kind)
        buckets = collections.defaultdict(list)
        for d, c, r in base:
            near = set(neighbours(c, d))
            has_same_strict = any((d2, c) in strict for d2 in near)
            has_same_mention = any((d2, c) in mention for d2 in near)
            if has_same_strict:
                buckets["sameday_or_pm1_strict"].append((d, c, r))
            elif has_same_mention:
                buckets["sameday_or_pm1_mention"].append((d, c, r))
            else:
                buckets["no_short_group"].append((d, c, r))
        B[kind + "|base"] = stat(base)
        B[kind + "|base"]["excess_d1"] = excess(base)
        for k, v in buckets.items():
            B["%s|%s" % (kind, k)] = stat(v)
            B["%s|%s" % (kind, k)]["excess_d1"] = excess(v)

    # ---------- C. 当日短线群热度分层（借势老师看多） ----------
    teach = base_rows("teacher")
    if teach:
        heat = {d: len(cs) for d, cs in by_date_mention.items()}
        vals = sorted(heat.values())
        med = vals[len(vals) // 2]
        hot = [(d, c, r) for d, c, r in teach if heat.get(d, 0) >= med]
        cold = [(d, c, r) for d, c, r in teach if heat.get(d, 0) < med]
        B["teacher|hot_day"] = stat(hot)
        B["teacher|cold_day"] = stat(cold)
        B["teacher|hot_day"]["excess_d1"] = excess(hot)
        B["teacher|cold_day"]["excess_d1"] = excess(cold)
        B["teacher|hot_day"]["_universe_d1_on_those_days"] = round(
            sum(uni_day_mean(1).get(d, 0) for d, _, _ in hot) / max(1, len(hot)), 2)
        B["teacher|cold_day"]["_universe_d1_on_those_days"] = round(
            sum(uni_day_mean(1).get(d, 0) for d, _, _ in cold) / max(1, len(cold)), 2)
        B["_median_mention_per_day"] = med

    # ---------- D. 基准（全市场等权） ----------
    U = build_universe()
    for n in (1, 3, 5):
        B["universe|d%d" % n] = gb.metrics(U["series"][n])
    B["_universe_codes"] = U["codes"]

    res = {"A_short_group": A, "B_resonance": B,
           "meta": {"sigs": len(sigs), "short_signals": len(short_rows),
                    "strict_pairs": len(strict), "mention_pairs": len(mention),
                    "window": RES_W}}
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    def line(key, v):
        d1, d3, d5 = v.get("d1", {}), v.get("d3", {}), v.get("d5", {})

        def f(d):
            if not d.get("n"):
                return "   0"
            return "%4d 胜%5.1f 均%+6.2f 比%s" % (d.get("n", 0), d.get("winrate") or 0,
                                                d.get("avg") or 0, d.get("pl_ratio"))
        ex = v.get("excess_d1") or {}
        e = " 超额%+5.2f(胜%4.1f)" % (ex.get("avg") or 0, ex.get("winrate") or 0) if ex.get("n") else ""
        print("%-30s D1: %s | D3: %s | D5: %s%s" % (key, f(d1), f(d3), f(d5), e))
    print("=== A 短线群 standalone ===")
    for k in sorted(A):
        line(k, A[k])
    print("=== B 共振 ===")
    for k in sorted(B):
        if isinstance(B[k], dict) and "d1" in B[k]:
            line(k, B[k])
        else:
            print(k, B[k])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
