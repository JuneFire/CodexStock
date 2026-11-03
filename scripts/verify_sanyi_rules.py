# -*- coding: utf-8 -*-
"""用 242 日历史逐日重放，验证 backend/sanyi_rules.py 与回测口径一致。

重放顺序（每个交易日）
--------------------
09:25  ① decide_open  ：D 日封板 + 今日竞价高开 ≥6% → 竞价卖
       ② 若资金空闲且有当日三一信号 → open_position（买在今日开盘价）
15:05  ③ decide_close ：标记封板；非买入日且收盘未封板 → 当日收盘卖

期望：成交 32 笔、1 万 → ≈54,184 元（×5.42），与
``scripts/sanyi_equity_compare.py`` 的 B 规则结果完全一致。

用法：python scripts/verify_sanyi_rules.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sanyi_hold_longer as H          # noqa: E402
import sanyi_equity as E               # noqa: E402
from backend import sanyi_rules as R    # noqa: E402

BT = "reports/三一竞价/backtest_sanyi_cap.json"
CAL = "data/index_kline/sh000001.json"
CAP0 = 10000.0


def calendar():
    b = json.load(open(CAL, encoding="utf-8"))
    return [x["date"] for x in b]


def signals(cap=3000):
    d = json.load(open(BT, encoding="utf-8"))
    tr = sorted(d["res"][str(cap)]["trades"], key=lambda x: x["date"])
    on, _ = E.load()
    on = set(on)
    return [t for t in tr if t["date"] in on]        # 只取开关开


def quotes_on(code, d, bars_cache):
    """返回 (open_px, close_px, prev_close)"""
    b = bars_cache.get(code) or H.bars(code)
    bars_cache[code] = b
    if not b:
        return None
    i = H.idx_of(b, d)
    if i < 1 or i >= len(b):
        return None
    return float(b[i]["open"]), float(b[i]["close"]), float(b[i - 1]["close"])


def main():
    cal = calendar()
    cal_idx = {d: i for i, d in enumerate(cal)}
    sig = signals()
    sig_by_date = {}
    for t in sig:
        sig_by_date.setdefault(t["date"], []).append(t)

    state = R._empty()
    bars_cache = {}
    cash = CAP0
    trades = []
    busy_until = -1
    i_lo = cal_idx[sig[0]["date"]]
    i_hi = min(len(cal) - 1, cal_idx[sig[-1]["date"]] + 15)   # 多跑 15 天让最后一笔出清
    window = cal[i_lo:i_hi + 1]

    for d in window:
        i = cal_idx[d]
        codes = [str(p["code"]) for p in state["open"]]
        q_open, q_close = {}, {}
        for c in set(codes + [str(t["code"]) for t in sig_by_date.get(d, [])]):
            q = quotes_on(c, d, bars_cache)
            if not q:
                continue
            o, cl, pc = q
            q_open[c] = {"price": o, "prevClose": pc}
            q_close[c] = {"close": cl, "prevClose": pc}

        # ---- 09:25 分档卖出
        sells = R.decide_open(state, d, q_open)
        for s in sells:
            cash += s.get("stake", 0) * (1 + s["retPct"] / 100.0)
            trades.append(s)
            busy_until = i
            print("  %s 卖 %-8s %+7.2f%%  %s" % (d, s["name"][:8], s["retPct"], s["exitWhy"]))

        # ---- 09:25 登记买入（资金空闲才买；同日多信号均分）
        group = sig_by_date.get(d, [])
        if group and i >= busy_until and not state["open"]:
            stake = cash / len(group)
        else:
            group, stake = [], 0.0
        for t in group:
            q = q_open.get(str(t["code"])) or {}
            rec = {
                "code": t["code"], "name": t["name"],
                "prevClose": q.get("prevClose"), "price": q.get("price"),
                "auctionAmount": (t.get("amt") or 0) * 1e8,
                "changePct": t.get("chg"), "amtRankFull": t.get("rAmt"),
                "trfRankFull": t.get("rTrf"), "themes": [],
            }
            pos, why = R.open_position(state, rec, d, q.get("price"))
            if pos:
                pos["stake"] = stake
                cash -= stake
                busy_until = i + R.MAX_HOLD       # 先锁死，卖出时再更新
                print("  %s 买 %-8s @%.2f  仓位%.0f" % (d, pos["name"][:8], pos["buyPx"], stake))
            elif why != "已登记":
                print("  %s 跳过 %-8s %s" % (d, t["name"][:8], why))

        # ---- 15:05 标记封板 + 接力断板卖出
        for s in R.decide_close(state, d, q_close):
            cash += s.get("stake", 0) * (1 + s["retPct"] / 100.0)
            trades.append(s)
            busy_until = i + 1                    # 收盘卖出 → 次日开盘资金可用
            print("  %s 卖 %-8s %+7.2f%%  %s" % (d, s["name"][:8], s["retPct"], s["exitWhy"]))

    # 结算仍未出清（回测里数据到 2026-11-02 为止，正常不应有）
    for p in state["open"]:
        last = (p.get("seq") or [{}])[-1]
        print("  !! 未出清 %s %s 持 %d 日" % (p["buyDate"], p["name"], len(p.get("seq") or [])))

    print("\n" + "=" * 78)
    n = len(trades)
    print("成交 %d 笔   1 万 → %.0f 元   ×%.2f" % (n, cash, cash / CAP0))
    win = sum(1 for t in trades if t["retPct"] > 0)
    print("胜率 %.1f%%   均值 %+.2f%%   几何 %+.2f%%/笔" % (
        win / n * 100 if n else 0,
        sum(t["retPct"] for t in trades) / n if n else 0,
        ((cash / CAP0) ** (1.0 / n) - 1) * 100 if n else 0))
    exp_n, exp_mult = 32, 5.42
    print("期望 %d 笔 / ×%.2f   → %s" % (
        exp_n, exp_mult, "✅ 一致" if n == exp_n and abs(cash / CAP0 - exp_mult) < 0.06 else "❌ 不一致"))

    bad = [t for t in trades if t.get("exitPx") in (None, 0)]
    if bad:
        print("⚠ 缺卖出价 %d 笔" % len(bad))
    return 0 if (n == exp_n and abs(cash / CAP0 - exp_mult) < 0.06) else 1


if __name__ == "__main__":
    sys.exit(main())
