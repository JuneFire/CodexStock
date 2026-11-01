# -*- coding: utf-8 -*-
"""二板战法（《二板战法手册》）本地回测。

数据：data/market/ztpool_hist/<date>.json（涨停池，含连板数与流通市值）
     + 腾讯日K（带换手率，抓一次后缓存到 data/backtest_kline/）

对每个交易日：
  1. 取当日涨停池里 lb ∈ [1, candLbMax] 的票作为候选池
  2. K线截断到当日，跑 tactics_engine.analyze_kline + judge_strategy
  3. 按四关结论（可买/观察/回避）、换手率阶梯档位（最优/次优/中性/警戒/剔除）分组
  4. 统计 T+1/T+2/T+3 收盘收益、T+1 开盘收益、T+1 涨停率，与「全候选池」基线对比

用法：
  python scripts/backtest_ermian.py --days 14
  python scripts/backtest_ermian.py --start 2026-09-29 --end 2026-10-23 --no-fetch
"""
import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import server  # noqa: E402
import tactics_engine  # noqa: E402

KLINE_CACHE = os.path.join(ROOT, "data", "backtest_kline")

ZTPOOL_HIST = os.path.join(ROOT, "data", "market", "ztpool_hist")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def load_kline_local(code, date_str, float_cap_yi):
    """读本地缓存日K（腾讯 qfq，含真实换手率，由 scripts/fetch_bt_kline.py 抓）。"""
    path = os.path.join(KLINE_CACHE, code + ".json")
    if not os.path.isfile(path):
        return [], ""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), "tx"
    except Exception:
        return [], ""


def forward_returns(kline, date_str, horizons=(1, 2, 3)):
    """date_str 之后第 N 个交易日的收盘/开盘相对 date_str 收盘的涨幅（%）。"""
    idx = None
    for i, r in enumerate(kline):
        if str(r.get("date"))[:10] == date_str:
            idx = i
            break
    if idx is None:
        return {}
    base = _num(kline[idx].get("close"))
    if not base:
        return {}
    out = {}
    for h in horizons:
        j = idx + h
        if j < len(kline):
            c = _num(kline[j].get("close"))
            if c:
                out["t%d" % h] = round((c / base - 1) * 100, 2)
        if h == 1:
            o = _num(kline[j].get("open")) if j < len(kline) else None
            if o:
                out["t1open"] = round((o / base - 1) * 100, 2)
    # T+1 涨停判定：涨幅 ≥ 9.8%（20cm 板另算）
    j = idx + 1
    if j < len(kline):
        c = _num(kline[j].get("close"))
        lim = 19.5 if str(kline[j].get("date")) else 9.8
        if c and (c / base - 1) * 100 >= (19.5 if abs((c / base - 1) * 100) > 15 else 9.8):
            out["t1_limitup"] = 1
        else:
            out["t1_limitup"] = 0
        _ = lim
    return out


def _pct(values):
    """池内分位（与 backend/server.py 的 _cs_percentiles 同口径）。"""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [100.0]
    keyed = [(v if v is not None else float("-inf")) for v in values]
    order = sorted(range(n), key=lambda i: keyed[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and keyed[order[j + 1]] == keyed[order[i]]:
            j += 1
        mid = (i + j) / 2.0
        for k in range(i, j + 1):
            out[order[k]] = round(mid / (n - 1) * 100.0, 1)
        i = j + 1
    return out


def avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def summarize(rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[r.get(key) or "—"].append(r)
    out = []
    for g, items in groups.items():
        out.append({
            "group": g, "n": len(items),
            "t1": avg([i.get("t1") for i in items]),
            "t2": avg([i.get("t2") for i in items]),
            "t3": avg([i.get("t3") for i in items]),
            "t1open": avg([i.get("t1open") for i in items]),
            "t1up": round(sum(i.get("t1_limitup") or 0 for i in items) / len(items) * 100, 1),
            "win": round(sum(1 for i in items if (i.get("t1") or 0) > 0) / len(items) * 100, 1),
        })
    return sorted(out, key=lambda x: -x["n"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    dates = sorted(f[:-5] for f in os.listdir(ZTPOOL_HIST) if f.endswith(".json"))
    if args.start:
        dates = [d for d in dates if d >= args.start]
    if args.end:
        dates = [d for d in dates if d <= args.end]
    if args.days:
        dates = dates[-args.days:]
    print("回测区间 %s ~ %s（%d 个交易日）" % (dates[0], dates[-1], len(dates)))

    rules = tactics_engine.load_rules(server.TACTICS_RULES_FILE)
    lb_max = int(rules.get("candLbMax") or 3)
    rows = []
    src_stat = defaultdict(int)
    for d in dates:
        pool = server._ztpool_rows(d)
        cands = [r for r in pool if 1 <= (r.get("lb") or 0) <= lb_max]
        ok = 0
        for r in cands:
            code = r.get("code")
            kline, src = load_kline_local(code, d, r.get("floatCapYi"))
            if not kline:
                continue
            src_stat[src] += 1
            kline = sorted(kline, key=lambda x: str(x.get("date"))[:10])
            hist = [k for k in kline if str(k.get("date"))[:10] <= d]
            feats = tactics_engine.analyze_kline(hist, rules)
            if not feats:
                continue
            if r.get("floatCapYi"):
                feats["floatCapYi"] = r["floatCapYi"]
            lb = int(r.get("lb") or 1)
            strategy = tactics_engine.judge_strategy(
                feats, lb, tactics_engine.LADDER_RULES,
                {"floatCapYi": feats.get("floatCapYi"), "price": feats.get("close"),
                 "sectorZt": 0, "jipoint": False,
                 "aboveMa60": feats.get("aboveMa60"), "breakMa60": feats.get("breakMa60")})
            lad = (strategy or {}).get("ladder") or {}
            four = (strategy or {}).get("fourGate") or {}
            stage, _sdesc = tactics_engine.judge_turnover_stage(
                feats.get("turnoverSeq") or [], rules)
            strategy_raw, _h, _w = tactics_engine.score_candidate(
                feats, stage, rules, sector_zt_count=0, ladder=lad,
                adjust=(strategy or {}).get("scoreAdjust") or 0)
            fr = forward_returns(kline, d)
            if not fr:
                continue
            ok += 1
            rows.append({
                "date": d, "code": code, "name": r.get("name"), "lb": lb,
                "grade": lad.get("grade") or "—", "verdictHard": four.get("verdict") or "—",
                "position": four.get("position") or "—",
                "turnover": lad.get("latest"), "raw": strategy_raw,
                **fr,
            })
        print("  %s 候选 %d 只，可回测 %d 只" % (d, len(cands), ok), flush=True)

    if not rows:
        print("无有效样本")
        return 1
    print("\n数据源：%s" % dict(src_stat))

    # 复刻生产逻辑：先按阶梯剔掉超标/高风险，再在当日池内做分位，再二分可买/观察
    EXCL = ("超标", "剔除", "高风险")
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    for d, items in by_day.items():
        keep = [r for r in items if r["grade"] not in EXCL]
        pcts = _pct([r.get("raw") or 0 for r in keep])
        for r, p in zip(keep, pcts):
            r["verdict"] = ("回避" if r.get("verdictHard") == "回避"
                            else ("可买" if p >= 60 else "观察"))
        for r in items:
            if r["grade"] in EXCL:
                r["verdict"] = "剔除"

    print("\n=== 基线：全部候选（%d 条）===" % len(rows))
    print("  T+1 %.2f%%  T+2 %.2f%%  T+3 %.2f%%  T+1开盘 %.2f%%  胜率 %.1f%%  T+1涨停 %.1f%%" % (
        avg([r.get("t1") for r in rows]) or 0, avg([r.get("t2") for r in rows]) or 0,
        avg([r.get("t3") for r in rows]) or 0, avg([r.get("t1open") for r in rows]) or 0,
        sum(1 for r in rows if (r.get("t1") or 0) > 0) / len(rows) * 100,
        sum(r.get("t1_limitup") or 0 for r in rows) / len(rows) * 100))

    for key, title in (("verdict", "按四关结论"), ("grade", "按换手率阶梯档位"),
                       ("lb", "按连板数")):
        print("\n=== %s ===" % title)
        print("  %-8s %4s %8s %8s %8s %9s %7s %7s" % ("分组", "样本", "T+1", "T+2", "T+3", "T+1开盘", "胜率", "涨停"))
        for g in summarize(rows, key):
            print("  %-8s %4d %8s %8s %8s %9s %6s%% %6s%%" % (
                g["group"], g["n"], g["t1"], g["t2"], g["t3"], g["t1open"], g["win"], g["t1up"]))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"dates": dates, "rows": rows,
                       "byVerdict": summarize(rows, "verdict"),
                       "byGrade": summarize(rows, "grade"),
                       "byLb": summarize(rows, "lb")}, f, ensure_ascii=False)
        print("\n明细已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
