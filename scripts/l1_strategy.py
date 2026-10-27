# -*- coding: utf-8 -*-
"""L1（红牛预案看多）策略回测 —— 严格口径版

数据合规（2026-10-27 修正）
  L1 信号全部来自【预案】（计划截图 OCR + 文字预案），不是盘中消息。
  但预案里有一部分是**盘中/收盘后**才发的（如 09:31、11:2x），拿它们做"当日开盘买"是前视偏差
  → 本脚本按「发布时间 ≤ 09:25」或「前夜发布、归属次日」筛选，其余剔除（默认剔除 15 条）。

一字板判定（按板块，不再用统一 9.5%）
  10cm：主板 / 20cm：创业板 300/301、科创板 688/689 / 30cm：北交所 4、8、92 / 5%：ST
  开盘价 ≥ 涨停价 → 视为"涨停开盘"，默认跳过（买不到），可改选次优；也可 --include-limit 对照

用法
  NO_NET=1 python scripts/l1_strategy.py                     # 默认：跳过一字、持1日
  NO_NET=1 python scripts/l1_strategy.py --hold 1 3 5 --include-limit
  NO_NET=1 python scripts/l1_strategy.py --by-board
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preplan_resonance as pp          # noqa: E402
import resonance_v2 as rv               # noqa: E402
import group_backtest as gb             # noqa: E402

gb.fetch_kline = lambda *a, **k: []

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "laiqiu", "backtest", "l1_strategy.json")


def board_of(code, name=""):
    c = str(code).zfill(6)
    if "ST" in (name or "").upper():
        return "ST(5%)", 5.0
    if c.startswith(("300", "301", "688", "689")):
        return "20cm", 20.0
    if c.startswith(("4", "8", "92")):
        return "30cm", 30.0
    return "10cm", 10.0


def build_candidates():
    dates, didx = pp.load_cal()
    raw = json.load(open(os.path.join(ROOT, "data", "laiqiu", "stockdict_ak.json"), encoding="utf-8"))
    n2c = {str(v).zfill(6): k for k, v in raw.items()}
    cand = collections.defaultdict(list)
    dropped = 0
    fx = os.environ.get("L1_PLANS") or os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl")
    for line in open(fx, encoding="utf-8"):
        x = json.loads(line)
        if x["polarity"] != "bull":
            continue
        d = x["day"]
        # 盘中/收盘后发布（与归属日同日、时间晚于 09:25）→ 当日开盘买不到
        if x["time"][:10] == d and x["time"][11:16] > "09:25":
            dropped += 1
            continue
        c = x["code"]
        s = rv.series(c)
        if s is None or d not in s.pos:
            continue
        i = s.pos[d]
        if i == 0 or i + 1 >= len(s.kl):
            continue
        prev, op = s.kl[i - 1]["close"], s.kl[i]["open"]
        bname, pct = board_of(c, x.get("name", ""))
        lim = round(prev * (1 + pct / 100), 2)
        cand[d].append({"code": c, "name": x.get("name") or n2c.get(c, ""),
                        "gap": round(100 * (op / prev - 1), 2), "board": bname,
                        "limit_open": op >= lim - 0.005})
    return dates, didx, n2c, cand, dropped


def sim(dates, didx, cand, hold=1, skip_limit=True, capital=10000.0, only_board=None):
    cap = capital
    hold_until = -1
    n = wins = skip_day = 0
    peak, mdd = capital, 0.0
    trades = []
    for d in sorted(cand):
        if didx[d] <= hold_until:
            continue
        lst = sorted(cand[d], key=lambda x: -x["gap"])
        if skip_limit:
            lst = [x for x in lst if not x["limit_open"]]
        if only_board:
            lst = [x for x in lst if x["board"] == only_board]
        if not lst:
            skip_day += 1
            continue
        x = lst[0]
        s = rv.series(x["code"])
        i = s.pos[d]
        k = i + hold - 1
        if k >= len(s.kl):
            continue
        ret = s.kl[k]["close"] / s.kl[i]["open"] - 1
        cap *= (1 + ret)
        hold_until = didx[d] + hold - 1
        n += 1
        wins += 1 if ret > 0 else 0
        peak = max(peak, cap)
        mdd = min(mdd, cap / peak - 1)
        trades.append({"date": d, "code": x["code"], "name": x["name"], "board": x["board"],
                       "gap": x["gap"], "limit_open": x["limit_open"],
                       "ret": round(ret * 100, 2), "capital": round(cap)})
    return {"hold": hold, "skip_limit": skip_limit, "only_board": only_board,
            "n": n, "final": round(cap), "return_pct": round((cap / capital - 1) * 100, 1),
            "winrate": round(100 * wins / n, 1) if n else 0,
            "max_drawdown": round(mdd * 100, 1), "skip_days": skip_day, "trades": trades}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[1])
    ap.add_argument("--include-limit", action="store_true")
    ap.add_argument("--by-board", action="store_true")
    a = ap.parse_args()
    dates, didx, n2c, cand, dropped = build_candidates()
    print("剔除盘中/收盘后发布的信号 %d 条；候选 %d 个点 / %d 个交易日"
          % (dropped, sum(len(v) for v in cand.values()), len(cand)))
    print("候选板块分布：%s｜涨停开盘 %d 个（按板块算）"
          % (dict(collections.Counter(x["board"] for v in cand.values() for x in v)),
             sum(1 for v in cand.values() for x in v if x["limit_open"])))
    res = {"dropped": dropped, "runs": []}
    for hold in a.hold:
        r = sim(dates, didx, cand, hold=hold, skip_limit=False)   # 含涨停开盘（买不到，记 0%）
        r2 = sim(dates, didx, cand, hold=hold, skip_limit=True)
        res["runs"].append(r)
        res["runs"].append(r2)
        print("\n持%d日｜跳过一字（可执行）：%d 笔｜期末 %d｜%+.1f%%｜胜率 %.1f%%｜回撤 %.1f%%"
              % (hold, r2["n"], r2["final"], r2["return_pct"], r2["winrate"], r2["max_drawdown"]))
        print("持%d日｜含一字（记0%%）：    %d 笔｜期末 %d｜%+.1f%%｜胜率 %.1f%%｜回撤 %.1f%%"
              % (hold, r["n"], r["final"], r["return_pct"], r["winrate"], r["max_drawdown"]))
    if a.by_board:
        print("\n按板块拆分（持1日、跳过一字）：")
        for b in ("10cm", "20cm", "30cm", "ST(5%)"):
            if not any(x["board"] == b for v in cand.values() for x in v):
                continue
            r = sim(dates, didx, cand, hold=1, skip_limit=True, only_board=b)
            res["runs"].append(r)
            if r["n"]:
                print("  %-8s %3d 笔｜期末 %7d｜%+8.1f%%｜胜率 %.1f%%｜回撤 %.1f%%"
                      % (b, r["n"], r["final"], r["return_pct"], r["winrate"], r["max_drawdown"]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n→ %s" % OUT)


if __name__ == "__main__":
    main()
