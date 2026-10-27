# -*- coding: utf-8 -*-
"""L3 分批止盈 + 止损 模拟器（参数化）

采用规则（默认参数）
    D 日（预案日）开盘：**全仓买入**
    5% 止损：D+1 起盘中跌破买入价 ×0.95 立即清仓
    D+1：收绿（收盘 < 前一日收盘）→ 清仓；收红 → 尾盘卖出 **1/3**
    剩余仓位：**每日新高**（当日最高 > 入场以来最高）才继续持有，否则当日收盘清仓
    D+3：再卖出剩余部分的 **1/2**
    D+5：尾盘全部清仓

用法
    python scripts/l3_exit_sim.py                     # 默认规则、本金 1 万、满仓复利
    python scripts/l3_exit_sim.py --stop 0.08 --first-sell 0.5 --no-stop
    python scripts/l3_exit_sim.py --fixed 10000       # 每笔固定 1 万
    python scripts/l3_exit_sim.py --detail            # 打印逐笔
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preplan_resonance as pp      # noqa: E402
import resonance_v2 as rv           # noqa: E402
import group_backtest as gb         # noqa: E402

gb.fetch_kline = lambda *a, **k: []

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVERSE_WORDS = ("反核", "次弱", "地板", "跌停开", "低吸", "反包", "低位")


def load_signals():
    """复用 L3 定义：红牛看多 × 来去空头风标(含风标名单) × 红牛原文含反核词，首波去重"""
    dates, didx = pp.load_cal()
    raw = json.load(open(os.path.join(ROOT, "data", "laiqiu", "stockdict_ak.json"), encoding="utf-8"))
    n2c = {str(v).zfill(6): k for k, v in raw.items()}
    plans = {}
    for line in open(os.path.join(ROOT, "data", "feishu", "hongniu", "plans.jsonl"), encoding="utf-8"):
        p = json.loads(line)
        plans[p["key"]] = p
    hn, hnb, htxt = collections.defaultdict(set), collections.defaultdict(set), {}
    for line in open(os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl"), encoding="utf-8"):
        x = json.loads(line)
        if x["polarity"] == "bull":
            hn[x["day"]].add(x["code"])
            p = plans.get(x.get("src"))
            if p:
                htxt[(x["day"], x["code"])] = (p.get("text") or "").replace(chr(10), " ")
        elif x["polarity"] == "bear":
            hnb[x["day"]].add(x["code"])
    lq = json.load(open(os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "fixed.json"), encoding="utf-8"))
    spots = []
    for d, v in lq.items():
        A = hn.get(d, set()) - hnb.get(d, set())
        bear = {x["code"] for x in v.get("bear") or []} | set(v.get("fengkiao") or [])
        for c in A & bear:
            if not any(w in htxt.get((d, c), "") for w in REVERSE_WORDS):
                continue
            s = rv.series(c)
            if s is None or d not in s.pos or not rv.rets(c, d):
                continue
            spots.append((d, c))
    by = collections.defaultdict(list)
    for d, c in spots:
        by[c].append(d)
    eps = []
    for c, ds in by.items():
        ds.sort(key=lambda z: didx.get(z, 10 ** 9))
        cur = None
        for z in ds:
            if cur is None or didx.get(z, 0) - didx.get(cur[-1], 0) > 3:
                if cur:
                    eps.append((cur[0], c))
                cur = [z]
            else:
                cur.append(z)
        if cur:
            eps.append((cur[0], c))
    eps.sort()
    return eps, dates, didx, n2c


def plan_exits(S, i, stop, first_sell, third_day_half, max_day):
    """返回 [(日索引, 卖出比例(占剩余), 价格, 原因)]；比例相对"当时剩余仓位" """
    kl = S.kl
    n = len(kl)
    entry = kl[i]["open"]
    stop_px = entry * (1 - stop) if stop else None
    out = []
    remain = 1.0
    high = kl[i]["high"]
    for k in range(1, max_day + 1):
        j = i + k
        if j >= n:
            out.append((n - 1, remain, kl[n - 1]["close"], "数据结束"))
            return out
        b = kl[j]
        if stop_px and b["low"] <= stop_px:
            out.append((j, remain, stop_px, "止损%.0f%%" % (stop * 100)))
            return out
        if k == 1:
            if b["close"] <= kl[i]["close"]:
                out.append((j, remain, b["close"], "D+1收绿清仓"))
                return out
            f = remain * first_sell
            out.append((j, f, b["close"], "D+1收红卖%.0f%%" % (first_sell * 100)))
            remain -= f
            high = max(high, b["high"])
            continue
        if k in (2, 3, 4):
            if b["high"] <= high:
                out.append((j, remain, b["close"], "D+%d未新高清仓" % k))
                return out
            high = max(high, b["high"])
            if k == 3 and third_day_half:
                f = remain * 0.5
                out.append((j, f, b["close"], "D+3卖1/2"))
                remain -= f
            continue
        out.append((j, remain, b["close"], "D+%d清仓" % k))
        return out
    return out


def run_sim(eps, dates, didx, n2c, capital=10000.0, stop=0.05, first_sell=1/3.0,
            third_half=True, max_day=5, fixed=0.0):
    """逐日组合模拟（仓位按剩余单位逐个扣减，现金按实际回笼计入）。

    返回 (trades, equity_curve, final_cash, max_drawdown)
    """
    cash = capital
    pos = None
    trades = []
    eq = []

    def bar(S, d):
        j = S.pos.get(d)
        return S.kl[j] if (j is not None and 0 <= j < len(S.kl)) else None

    i0 = didx[eps[0][0]]
    i1 = min(didx[eps[-1][0]] + max_day + 3, len(dates))
    for i in range(i0, i1):
        d = dates[i]
        # 开盘开仓（满仓只剩一个仓位）
        if pos is None:
            cand = [c for (dd, c) in eps if dd == d]
            if cand:
                c = cand[0]
                S = rv.series(c)
                j = S.pos[d]
                b = S.kl[j]
                stake = cash if fixed <= 0 else min(fixed, cash)
                if stake > 0:
                    pos = {"code": c, "name": n2c.get(c, c), "entry": b["open"],
                           "units": stake / b["open"], "stake": stake, "back": 0.0,
                           "i": i, "d": d, "S": S, "h": b["high"], "entry_d": d, "why": ""}
                    cash -= stake
        # 收盘处理
        if pos:
            S = pos["S"]
            b = bar(S, d)
            if b is not None and i > pos["i"]:
                k = i - pos["i"]
                done = False
                sp = pos["entry"] * (1 - stop) if stop else None
                if sp and b["low"] <= sp:
                    pos["back"] += pos["units"] * sp
                    pos["units"] = 0.0
                    pos["why"] = "止损%.0f%%" % (stop * 100)
                    done = True
                if not done and k == 1:
                    prev = bar(S, pos["entry_d"])
                    if prev and b["close"] <= prev["close"]:
                        pos["back"] += pos["units"] * b["close"]
                        pos["units"] = 0.0
                        pos["why"] = "D+1收绿清仓"
                        done = True
                    else:
                        f = pos["units"] * first_sell
                        pos["back"] += f * b["close"]
                        pos["units"] -= f
                        pos["h"] = max(pos["h"], b["high"])
                if not done and 2 <= k <= 4:
                    if b["high"] <= pos["h"]:
                        pos["back"] += pos["units"] * b["close"]
                        pos["units"] = 0.0
                        pos["why"] = "D+%d未新高清仓" % k
                        done = True
                    else:
                        pos["h"] = max(pos["h"], b["high"])
                    if not done and k == 3 and third_half:
                        f = pos["units"] * 0.5
                        pos["back"] += f * b["close"]
                        pos["units"] -= f
                if not done and k >= max_day:
                    pos["back"] += pos["units"] * b["close"]
                    pos["units"] = 0.0
                    pos["why"] = "D+%d清仓" % k
                if pos["units"] <= 1e-9:
                    cash += pos["back"]
                    trades.append({"d": pos["d"], "name": pos["name"], "code": pos["code"],
                                   "stake": pos["stake"], "back": pos["back"],
                                   "ret": pos["back"] / pos["stake"] - 1,
                                   "why": pos["why"], "exit_d": d})
                    pos = None
        mtm = cash + (pos["back"] if pos else 0.0)
        if pos:
            b = bar(pos["S"], d)
            if b:
                mtm += pos["units"] * b["close"]
        eq.append((d, mtm))
    if pos:
        b = bar(pos["S"], dates[i1 - 1])
        if b:
            cash += pos["back"] + pos["units"] * b["close"]
    peak = capital
    mdd = 0.0
    for _, v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return trades, eq, cash, mdd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--stop", type=float, default=0.05, help="止损比例，0 表示不止损")
    ap.add_argument("--first-sell", type=float, default=1 / 3.0, help="D+1 收红卖出比例")
    ap.add_argument("--no-third-half", action="store_true", help="D+3 不卖一半")
    ap.add_argument("--max-day", type=int, default=5)
    ap.add_argument("--fixed", type=float, default=0.0, help="每笔固定金额；0 = 满仓复利")
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()

    eps, dates, didx, n2c = load_signals()
    print("L3 首波信号 %d 个（%s → %s）" % (len(eps), eps[0][0], eps[-1][0]))
    trades, eq, cash, mdd = run_sim(eps, dates, didx, n2c,
                                     capital=a.capital, stop=a.stop, first_sell=a.first_sell,
                                     third_half=not a.no_third_half, max_day=a.max_day,
                                     fixed=a.fixed)
    n = len(trades)
    wins = sum(1 for t in trades if t["ret"] > 0)
    print()
    print("参数：本金 %.0f｜止损 %s｜D+1 卖 %.0f%%｜D+3 卖一半 %s｜最多持 %d 日｜%s"
          % (a.capital, ("%.0f%%" % (a.stop * 100)) if a.stop else "无",
             a.first_sell * 100, "是" if not a.no_third_half else "否", a.max_day,
             "满仓复利" if a.fixed <= 0 else "每笔固定 %.0f" % a.fixed))
    print("结果：%d 笔｜期末 %.0f 元｜收益 %+.1f%%｜胜率 %.1f%%｜最大回撤(MTM) %.1f%%"
          % (n, cash, (cash / a.capital - 1) * 100, 100 * wins / max(1, n), mdd * 100))
    if a.detail:
        print("\n%-11s %-9s %8s %8s  %s" % ("买入日", "名称", "投入", "整笔%", "卖出过程"))
        for t in trades:
            det = "%+.1f%% %s" % (t["ret"] * 100, t["why"])
            print("%-11s %-9s %8.0f %8.1f  %s" % (t["d"], t["name"], t["stake"], t["ret"] * 100, det))


if __name__ == "__main__":
    main()
