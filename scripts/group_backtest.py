#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""群信号回测：胜率 / 盈亏比 / 期望，含「来去由心 + 借势而为」共振对比。

输入  data/laiqiu/backtest/group_signals.json   （scripts/group_signals.py 产出）
      data/laiqiu/kline/<code>.json             （腾讯 qfq 日K 缓存，缺失自动补拉）
输出  data/laiqiu/backtest/group_winrate.json
      reports/群信号回测_胜率盈亏比.md

口径：
  信号日 D = 群里出现「个股 + 方向」的日期
  entry_close : 以 D 收盘价买入      exit = D+n 收盘（n=1/3/5）
  entry_open  : 以 D+1 开盘价买入    exit = D+n 收盘（无未来函数版本）
  胜率        = 收益>0 的占比（看空信号取反向）
  盈亏比      = 平均盈利幅度 / |平均亏损幅度|
  超额        = 个股收益 - 上证同期收益
"""
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIG = os.path.join(ROOT, "data", "laiqiu", "backtest", "group_signals.json")
KL_DIR = os.path.join(ROOT, "data", "laiqiu", "kline")
OUT_JSON = os.path.join(ROOT, "data", "laiqiu", "backtest", "group_winrate.json")
REPORT = os.path.join(ROOT, "reports", "群信号回测_胜率盈亏比.md")
BENCH = "sh000001"
WIN_FROM, WIN_TO = "2026-06-29", "2026-10-23"     # 两群重叠窗口（借势而为最早信号日）
_lock = threading.Lock()


def tx_symbol(code):
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def fetch_kline(symbol, adjust="qfq"):
    """腾讯日K（curl 走系统代理，python 直连不稳）。设 NO_NET=1 可完全禁用回源。"""
    if os.environ.get("NO_NET"):
        return []
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,2025-01-01,2026-12-31,600,%s"
           % (symbol, adjust))
    try:
        r = subprocess.run(["curl", "-s", "-m", "25", url], capture_output=True)
        d = json.loads(r.stdout.decode("utf-8", "replace"))
        node = (d.get("data") or {}).get(symbol) or {}
        rows = node.get("qfqday") or node.get("day") or []
        out = []
        for x in rows:
            out.append({"date": x[0], "open": float(x[1]), "close": float(x[2]),
                        "high": float(x[3]), "low": float(x[4])})
        return out
    except Exception:
        return []


def get_kline(code_or_sym, adjust="qfq"):
    sym = code_or_sym if code_or_sym.startswith(("sh", "sz", "bj")) else tx_symbol(code_or_sym)
    fn = sym + (".json" if adjust == "qfq" else "_idx.json")
    path = os.path.join(KL_DIR, fn)
    if not os.path.isfile(path) and sym[:2] in ("sh", "sz", "bj"):
        # 缓存里存在两套命名（sz002081.json / 002081.json），回退到裸代码
        alt = os.path.join(KL_DIR, sym[2:] + (".json" if adjust == "qfq" else "_idx.json"))
        if os.path.isfile(alt):
            path = alt
    if os.path.isfile(path):
        try:
            kl = json.load(open(path, encoding="utf-8"))
            if kl and kl[-1]["date"] >= "2026-10-20":
                return kl
        except Exception:
            pass
    kl = fetch_kline(sym, adjust)
    if kl:
        with _lock:
            os.makedirs(KL_DIR, exist_ok=True)
            json.dump(kl, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return kl


def limit_pct(code, name=""):
    c = str(code).zfill(6)
    if "ST" in (name or "").upper():
        return 5.0
    if c.startswith(("300", "301", "688", "689")):
        return 20.0
    if c.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


class Series:
    def __init__(self, kl):
        self.dates = [r["date"] for r in kl]
        self.pos = {d: i for i, d in enumerate(self.dates)}
        self.kl = kl

    def at(self, date, field):
        i = self.pos.get(date)
        if i is None:
            return None
        return self.kl[i][field]

    def shift(self, date, n):
        i = self.pos.get(date)
        if i is None or i + n >= len(self.kl) or i + n < 0:
            return None
        return self.dates[i + n]

    def ret(self, d0, d1):
        a = self.at(d0, "close")
        b = self.at(d1, "close")
        if not a or b is None:
            return None
        return (b / a - 1) * 100


def metrics(rets):
    n = len(rets)
    if not n:
        return {"n": 0}
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg = sum(rets) / n
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    srt = sorted(rets)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    return {"n": n, "winrate": round(100.0 * len(wins) / n, 1),
            "avg": round(avg, 2), "avg_win": round(aw, 2), "avg_loss": round(al, 2),
            "pl_ratio": round(aw / abs(al), 2) if al else None,
            "expectancy": round(avg, 2), "median": round(med, 2),
            "p90": round(srt[int(n * 0.9)], 2), "p10": round(srt[int(n * 0.1)], 2),
            "limit_up_rate": None}


def main():
    sigs = json.load(open(SIG, encoding="utf-8"))
    # ---- 聚合成 (group, kind, date, code) ----
    agg = {}
    for s in sigs:
        k = (s["group"], s["kind"], s["date"], s["code"])
        a = agg.setdefault(k, {"pos": 0, "neg": 0, "name": s["name"], "text": s["text"],
                               "time": s.get("time") or ""})
        if s["polarity"] == "pos":
            a["pos"] += 1
        elif s["polarity"] == "neg":
            a["neg"] += 1
    rows = []
    for (g, kind, date, code), a in agg.items():
        pol = "pos" if a["pos"] > a["neg"] else ("neg" if a["neg"] > a["pos"] else "neutral")
        rows.append({"group": g, "kind": kind, "date": date, "code": code,
                     "name": a["name"], "polarity": pol, "score": abs(a["pos"] - a["neg"]),
                     "text": a["text"], "time": a["time"]})
    print("aggregated signal-days:", len(rows))

    codes = sorted({r["code"] for r in rows})
    print("codes:", len(codes))

    # ---- K线 ----
    def work(c):
        return c, get_kline(c)
    series = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, kl in ex.map(work, codes):
            if kl:
                series[c] = Series(kl)
    print("kline ok:", len(series), "missing:", len(codes) - len(series))
    bench_kl = get_kline(BENCH, adjust="none")
    if not bench_kl:
        bench_kl = fetch_kline("sh000001", "none")
    bench = Series(bench_kl)
    if not bench_kl:
        print("!! benchmark kline failed")

    # ---- 逐条算收益 ----
    def build(r, entry):
        s = series.get(r["code"])
        if not s:
            return None
        D = s.shift(r["date"], 0)
        if D is None:                                  # 信号日停牌/非交易日 -> 顺延
            return None
        d1, d3, d5 = s.shift(D, 1), s.shift(D, 3), s.shift(D, 5)
        out = {"group": r["group"], "kind": r["kind"], "date": D, "code": r["code"],
               "name": r["name"], "polarity": r["polarity"], "score": r["score"],
               "time": r.get("time") or ""}
        if entry == "close":
            base = s.at(D, "close")
            out["d0"] = s.ret(D, D)
        else:
            nxt = s.shift(D, 1)
            base = s.at(nxt, "open") if nxt else None
            out["d0"] = None
            D1open = nxt
        for n, dd in ((1, d1), (3, d3), (5, d5)):
            v = None
            if base and dd:
                v = (s.at(dd, "close") / base - 1) * 100
            out["r%d" % n] = round(v, 2) if v is not None else None
            bv = bench.ret(D, dd) if bench_kl else None
            out["x%d" % n] = round(v - bv, 2) if (v is not None and bv is not None) else None
        if entry != "close":
            # 次日开盘入场时，D+1 的收益就是 收盘/开盘
            if base and d1:
                v = (s.at(d1, "close") / base - 1) * 100
                out["r1"] = round(v, 2)
                bv = bench.ret(D, d1) if bench_kl else None
                out["x1"] = round(v - bv, 2) if bv is not None else None
        # 涨停判定（信号日）
        pc = s.shift(D, -1)
        if pc:
            prev = s.at(pc, "close")
            cur = s.at(D, "close")
            lim = limit_pct(r["code"], r["name"])
            if prev and cur:
                out["d0_limit_up"] = ((cur / prev - 1) * 100) >= lim - 0.6
        return out

    det_close = [x for x in (build(r, "close") for r in rows) if x]
    det_open = [x for x in (build(r, "open") for r in rows) if x]
    print("detail ok:", len(det_close))

    # ---- 共振标记（借势而为 与 来去由心 同股 +-3 交易日）----
    lq_codes = {}
    for r in det_close:
        if r["group"] == "laiqiu" and r["polarity"] in ("pos", "neg"):
            lq_codes.setdefault(r["code"], []).append((r["date"], r["polarity"]))
    all_dates = sorted({r["date"] for r in det_close})
    di = {d: i for i, d in enumerate(all_dates)}

    def near_lq(code, date, pol, span=3):
        for d, p in lq_codes.get(code, []):
            if p != pol:
                continue
            if d in di and date in di and abs(di[d] - di[date]) <= span:
                return True
        return False

    reso = set()
    for r in det_close:
        hit = (r["group"] == "jieshi" and r["polarity"] == "pos"
               and near_lq(r["code"], r["date"], "pos"))
        r["resonance"] = hit
        if hit:
            reso.add((r["code"], r["date"]))
    for r in det_open:
        r["resonance"] = (r["code"], r["date"]) in reso
        if r.get("resonance") is None:
            r["resonance"] = False

    # ---- 分组统计 ----
    def sel(fn):
        return [r for r in det_close if fn(r)]

    def stats_block(name, fn, note=""):
        rows_ = sel(fn)
        out = {"name": name, "note": note, "n": len(rows_)}
        for h in (1, 3, 5):
            rr = [r["r%d" % h] for r in rows_ if r.get("r%d" % h) is not None]
            xx = [r["x%d" % h] for r in rows_ if r.get("x%d" % h) is not None]
            m = metrics(rr)
            blk = {"n": m.get("n", 0), "winrate": m.get("winrate"), "avg": m.get("avg"),
                   "avg_win": m.get("avg_win"), "avg_loss": m.get("avg_loss"),
                   "pl_ratio": m.get("pl_ratio"), "median": m.get("median")}
            if xx:
                blk["avg_ex"] = round(sum(xx) / len(xx), 2)
            out["d%d" % h] = blk
        return out

    res = {"generated": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "window": [WIN_FROM, WIN_TO], "n_signal_days": len(rows), "n_detailed": len(det_close),
           "groups": [], "detail_close": det_close, "detail_open": det_open}

    W = lambda a, b: (lambda r: WIN_FROM <= r["date"] <= WIN_TO)
    res["groups"].append(stats_block("来去由心·文字看多（全样本 2025-08→）",
                                     lambda r: r["group"] == "laiqiu" and r["kind"] == "text"
                                     and r["polarity"] == "pos"))
    res["groups"].append(stats_block("来去由心·文字看空（全样本）",
                                     lambda r: r["group"] == "laiqiu" and r["kind"] == "text"
                                     and r["polarity"] == "neg"))
    res["groups"].append(stats_block("来去由心·文字看多（重叠窗口）",
                                     lambda r: r["group"] == "laiqiu" and r["kind"] == "text"
                                     and r["polarity"] == "pos" and WIN_FROM <= r["date"] <= WIN_TO))
    res["groups"].append(stats_block("来去由心·仅提及（重叠窗口）",
                                     lambda r: r["group"] == "laiqiu" and r["kind"] == "text"
                                     and r["polarity"] == "neutral" and WIN_FROM <= r["date"] <= WIN_TO))
    res["groups"].append(stats_block("借势而为·老师看多",
                                     lambda r: r["group"] == "jieshi" and r["kind"] == "teacher"
                                     and r["polarity"] == "pos"))
    res["groups"].append(stats_block("借势而为·老师看空",
                                     lambda r: r["group"] == "jieshi" and r["kind"] == "teacher"
                                     and r["polarity"] == "neg"))
    res["groups"].append(stats_block("借势而为·老师仅提及",
                                     lambda r: r["group"] == "jieshi" and r["kind"] == "teacher"
                                     and r["polarity"] == "neutral"))
    res["groups"].append(stats_block("借势而为·研报转发看多",
                                     lambda r: r["group"] == "jieshi" and r["kind"] == "research"
                                     and r["polarity"] == "pos"))
    res["groups"].append(stats_block("★共振·借势看多 ∩ 来去由心看多(±3日)",
                                     lambda r: r.get("resonance")))
    res["groups"].append(stats_block("借势看多（无来去由心共振）",
                                     lambda r: r["group"] == "jieshi" and r["kind"] == "teacher"
                                     and r["polarity"] == "pos" and not r.get("resonance")))

    # 次日开盘入场的对照（同一批共振/单群）
    def stats_open(name, fn):
        rows_ = [x for x in det_open if fn(x)]
        out = {"name": name, "n": len(rows_)}
        for h in (1, 3, 5):
            rr = [r["r%d" % h] for r in rows_ if r.get("r%d" % h) is not None]
            m = metrics(rr)
            out["d%d" % h] = {"n": m.get("n", 0), "winrate": m.get("winrate"),
                              "avg": m.get("avg"), "pl_ratio": m.get("pl_ratio")}
        return out

    res["groups_open_entry"] = [
        stats_open("来去由心看多（重叠窗口）", lambda r: r["group"] == "laiqiu" and r["kind"] == "text"
                   and r["polarity"] == "pos" and WIN_FROM <= r["date"] <= WIN_TO),
        stats_open("借势而为看多", lambda r: r["group"] == "jieshi" and r["kind"] == "teacher"
                   and r["polarity"] == "pos"),
        stats_open("★共振", lambda r: r.get("resonance")),
    ]
    # ---- 全市场等权基准（同一批可用 K 线的票，窗口内所有交易日）----
    uni = []
    for c, sr in series.items():
        for d in sr.dates:
            if WIN_FROM <= d <= WIN_TO:
                nd = sr.shift(d, 1)
                if nd:
                    v = sr.ret(d, nd)
                    if v is not None:
                        uni.append(v)
    if uni:
        res["universe_baseline"] = {"codes": len(series), "code_days": len(uni),
                                    "avg_d1": round(sum(uni) / len(uni), 2),
                                    "winrate_d1": round(100.0 * sum(1 for x in uni if x > 0) / len(uni), 1)}

    # ---- 时段切片（盘前 vs 盘中）----
    res["slices"] = [
        stats_block("来去由心看多·盘前(≤09:25)",
                    lambda r: r["group"] == "laiqiu" and r["kind"] == "text" and r["polarity"] == "pos"
                    and r["time"] and r["time"] <= "09:25" and WIN_FROM <= r["date"] <= WIN_TO),
        stats_block("来去由心看多·盘中(>09:25)",
                    lambda r: r["group"] == "laiqiu" and r["kind"] == "text" and r["polarity"] == "pos"
                    and (not r["time"] or r["time"] > "09:25") and WIN_FROM <= r["date"] <= WIN_TO),
        stats_block("借势而为看多·盘前(≤09:25)",
                    lambda r: r["group"] == "jieshi" and r["kind"] == "teacher" and r["polarity"] == "pos"
                    and r["time"] and r["time"] <= "09:25"),
        stats_block("借势而为看多·盘中(>09:25)",
                    lambda r: r["group"] == "jieshi" and r["kind"] == "teacher" and r["polarity"] == "pos"
                    and (not r["time"] or r["time"] > "09:25")),
    ]

    # ---- 月度（主对比组）----
    def monthly(fn):
        out = {}
        for r in [x for x in det_close if fn(x)]:
            ym = r["date"][:7]
            b = out.setdefault(ym, {"n": 0, "win": 0, "sum1": 0.0, "cnt1": 0})
            b["n"] += 1
            if r.get("r1") is not None:
                b["cnt1"] += 1
                b["sum1"] += r["r1"]
                if r["r1"] > 0:
                    b["win"] += 1
        return {k: {"n": v["n"], "winrate": round(100.0 * v["win"] / v["cnt1"], 1) if v["cnt1"] else None,
                    "avg_d1": round(v["sum1"] / v["cnt1"], 2) if v["cnt1"] else None}
                for k, v in sorted(out.items())}
    res["monthly"] = {
        "来去由心看多": monthly(lambda r: r["group"] == "laiqiu" and r["kind"] == "text" and r["polarity"] == "pos"),
        "借势而为看多": monthly(lambda r: r["group"] == "jieshi" and r["kind"] == "teacher" and r["polarity"] == "pos"),
        "共振": monthly(lambda r: r.get("resonance")),
    }

    # 基准
    if bench_kl:
        bdates = [d for d in bench.dates if WIN_FROM <= d <= WIN_TO]
        br = [bench.ret(d, bench.shift(d, 1)) for d in bdates if bench.shift(d, 1)]
        br = [x for x in br if x is not None]
        res["benchmark"] = {"symbol": BENCH, "days": len(br),
                            "avg_d1": round(sum(br) / len(br), 2),
                            "winrate_d1": round(100.0 * sum(1 for x in br if x > 0) / len(br), 1)}

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(res, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    print("wrote", OUT_JSON)

    for g in res["groups"]:
        d1 = g["d1"]
        print("%-34s n=%-4d D+1 胜率%5s%% 均%6s 盈亏比%5s | D+3 胜率%5s%% 均%6s | D+5 胜%5s%% 均%6s"
              % (g["name"], g["n"], d1.get("winrate"), d1.get("avg"), d1.get("pl_ratio"),
                 g["d3"].get("winrate"), g["d3"].get("avg"), g["d5"].get("winrate"), g["d5"].get("avg")))
    print()
    for g in res.get("slices", []):
        d1 = g["d1"]
        print("%-26s n=%-4d D+1 胜率%5s%% 均%6s 盈亏比%5s" %
              (g["name"], g["n"], d1.get("winrate"), d1.get("avg"), d1.get("pl_ratio")))
    print("universe", res.get("universe_baseline"))
    if res.get("benchmark"):
        print("基准", res["benchmark"])


if __name__ == "__main__":
    main()
