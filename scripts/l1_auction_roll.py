#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1 竞价滚动策略回测（D 开盘买 → D+1 开盘卖 → 同一次竞价立刻换股）

实盘时间轴（A 股集合竞价规则）：
  09:15-09:20  可挂单、可撤单      → 挂「卖出」委托（跌停价保成交）
  09:20-09:25  可挂单、不可撤单    → 无法反悔，卖单必须已在此前想清楚
  09:25        集合竞价成交        → 持仓按开盘价卖出，资金 T+0 可用于买入
  09:25-09:30  静默期：可挂单不可撤 → 挂「买入」委托（涨停价保成交）
  09:30        连续竞价开始        → 买单按 9:30 首笔成交（≈开盘价，强势票会跳空）

因此回测的三条关键假设：
  1) 卖出价 = D+1 开盘价（9:25 集合竞价成交价）
  2) 买入价 = D 开盘价 × (1 + slip)   ← slip 是 9:30 首笔相对 9:25 竞价的跳空
  3) 费用 = fee（双边佣金+印花税，默认 0.1%/笔）

用法：
  python scripts/l1_auction_roll.py                    # 默认 slip=0.3% fee=0.1%
  python scripts/l1_auction_roll.py --slip 0 0.3 0.5 1.0 --fee 0.001
  python scripts/l1_auction_roll.py --gapcap 8         # 剔除 10cm 竞价涨幅≥8%（20cm 按 1.6 倍）
  python scripts/l1_auction_roll.py --detail           # 打印逐笔
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import l1_strategy as l1          # noqa: E402
import resonance_v2 as rv         # noqa: E402


def roll(cand, dates, slip=0.003, fee=0.001, gapcap=None):
    cap, hold = 10000.0, None
    n = wins = 0
    peak, mdd = cap, 0.0
    rows = []
    for d in dates:
        # ---- 09:25 卖出（用 D+1 开盘价）----
        if hold:
            code, buy, buy_day = hold
            s = rv.series(code)
            i = s.pos.get(d, -1)
            if i >= 0:
                sell = s.kl[i]["open"] * (1 - fee / 2)
                ret = sell / buy - 1
                cap *= 1 + ret
                n += 1
                wins += 1 if ret > 0 else 0
                peak = max(peak, cap)
                mdd = min(mdd, cap / peak - 1)
                rows.append({"buy_day": buy_day, "sell_day": d, "code": code,
                             "name": getattr(s, "name", ""), "ret": ret * 100,
                             "capital": cap})
            hold = None
        # ---- 09:30 买入（用当日开盘价 × (1+slip)）----
        pool = [x for x in cand.get(d, []) if not x["limit_open"]]
        if gapcap:
            pool = [x for x in pool if x["gap"] < (gapcap if x["board"] == "10cm" else gapcap * 1.6)]
        if not pool:
            continue
        x = max(pool, key=lambda z: z["gap"])
        s = rv.series(x["code"])
        i = s.pos.get(d, -1)
        if i < 0:
            continue
        hold = (x["code"], s.kl[i]["open"] * (1 + slip) * (1 + fee / 2), d)
    return {"n": n, "final": cap, "win": wins / n if n else 0, "mdd": mdd, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slip", type=float, nargs="+", default=[0.003],
                    help="买入跳空滑点，可给多个做敏感性（如 0 0.003 0.005 0.01）")
    ap.add_argument("--fee", type=float, default=0.001, help="每笔双边费用合计")
    ap.add_argument("--gapcap", type=float, default=None, help="10cm 竞价涨幅上限（20cm 按 1.6 倍）")
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()

    dates, didx, n2c, cand, dropped = l1.build_candidates()
    print("候选 %d 个 / 有信号交易日 %d｜费用 %.2f%%/笔｜涨幅上限 %s"
          % (sum(len(v) for v in cand.values()), len(cand), a.fee * 100, a.gapcap))
    print("\n%-30s %5s %10s %10s %7s %8s" % ("买入跳空", "笔数", "期末", "收益", "胜率", "回撤"))
    last = None
    for slip in a.slip:
        r = roll(cand, dates, slip=slip, fee=a.fee, gapcap=a.gapcap)
        print("%-30s %5d %10s %+9.1f%% %6.1f%% %7.1f%%"
              % ("slip %+.1f%%" % (slip * 100), r["n"], format(round(r["final"]), ","),
                 (r["final"] / 10000 - 1) * 100, r["win"] * 100, r["mdd"] * 100))
        last = r
    if a.detail and last:
        print("\n逐笔（slip=%+.1f%%）：" % (a.slip[-1] * 100))
        print("%-3s %-11s %-11s %-7s %-8s %8s %10s" % ("#", "买入日", "卖出日", "代码", "名称", "本笔%", "累计资金"))
        for i, x in enumerate(last["rows"], 1):
            print("%-3d %-11s %-11s %-7s %-8s %+8.2f %10s"
                  % (i, x["buy_day"], x["sell_day"], x["code"], (x["name"] or "")[:8],
                     x["ret"], format(round(x["capital"]), ",")))


if __name__ == "__main__":
    main()
