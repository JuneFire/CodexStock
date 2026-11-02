# -*- coding: utf-8 -*-
"""竞价三一：两种卖出规则的复利对比（初始 1 万元）

对比口径
--------
A 竞价卖出      : D 日开盘买 → D+1 竞价（开盘）卖     占用 1 个交易日
B 涨停接力+分档 : D 日收盘封板且 D+1 竞价高开 ≥6% → D+1 竞价卖
                  否则 涨停接力（收盘封住涨停就续持，第一根没封住当日收盘卖）

资金模型
--------
A 股卖出所得当日即可用于买入（T+1 只限制提现），所以
  买入日 D、卖出日 X  →  资金从 X 起可用，X 当天的新信号能接。
单槽位：上一个仓位未卖出则跳过新信号；同一天多个信号则均分资金。

用法
----
  python scripts/sanyi_equity_compare.py
  python scripts/sanyi_equity_compare.py --cap 3000 --gap 6
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sanyi_hold_longer as H  # noqa: E402
import sanyi_equity as E       # noqa: E402

BT = "reports/backtest_sanyi_cap.json"
CAL_PATH = "data/index_kline/sh000001.json"


def cal_index():
    """交易日 -> 序号；以及序号 -> 交易日"""
    b = json.load(open(CAL_PATH, encoding="utf-8"))
    ds = [x["date"] for x in b]
    return {d: i for i, d in enumerate(ds)}, ds


def build(cap, gap_th):
    """抽取全部三一信号，算两种规则的收益与持有天数"""
    d = json.load(open(BT, encoding="utf-8"))
    tr = sorted(d["res"][str(cap)]["trades"], key=lambda x: x["date"])
    on, _ = E.load()
    on = set(on)
    rows = []
    for t in tr:
        r = H.walk(t["code"], t["date"], t["name"])
        if not r or "skip" in r:
            continue
        days = r["days"]
        gap = (days[0]["open"] / days[0]["prevClose"] - 1) * 100 if days[0].get("prevClose") else 0.0

        # A 竞价卖出（09:25 成交，当日资金即可用于买入）
        a = {"ret": r["next_open"], "hold": 1, "sell": "open"}

        # B 涨停接力 + 分档
        if r["d_lu"] and gap >= gap_th:
            b = {"ret": r["next_open"], "hold": 1, "sell": "open",
                 "why": "高开≥%g%%卖竞价" % gap_th}
        else:
            b = {"ret": r["lu_chain"], "hold": r["lu_hold"], "sell": "close",
                 "why": "接力续持" if r["lu_hold"] > 1 else "当日收盘卖"}

        # 参考：D+1 收盘卖
        c = {"ret": r["next_close"], "hold": 1, "sell": "close"}

        rows.append({
            "date": t["date"], "code": t["code"], "name": t["name"],
            "on": t["date"] in on, "gap": gap, "d_lu": r["d_lu"],
            "amt": t.get("amt"), "lb": t.get("lb"), "grade": t.get("grade"),
            "A": a, "B": b, "C": c,
        })
    return rows


def run(rows, key, cap0=10000.0, n_td=242, allow_skip=False):
    """单槽位复利。allow_skip=False 表示按真实交易日历判断资金是否被占用。"""
    td, _ = cal_index()
    # 同一天多个信号 -> 均分
    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    cash = cap0
    peak = cash
    mdd = 0.0
    trades = []
    skipped = []
    busy_until = -1          # 序号，资金从该交易日之后可用
    for date in sorted(by_date):
        i = td.get(date)
        if i is None:
            continue
        if i < busy_until:
            skipped.append({"date": date,
                            "names": [x["name"] for x in by_date[date]],
                            "ret": [round(x[key]["ret"], 2) for x in by_date[date]]})
            continue
        group = by_date[date]
        n = len(group)
        part = cash / n
        gain = 0.0
        busy = 0                      # 相对买入日的资金解锁偏移
        for r in group:
            ret = r[key]["ret"]
            hold = max(1, int(r[key]["hold"]))
            # 收盘卖出 -> 资金次日开盘才可用；开盘(竞价)卖出 -> 当日即可再买
            busy = max(busy, hold + (1 if r[key].get("sell") == "close" else 0))
            gain += part * ret / 100.0
            trades.append({"date": date, "code": r["code"], "name": r["name"],
                           "ret": ret, "hold": hold, "why": r[key].get("why", ""),
                           "eq": None})
        cash += gain
        busy_until = i + busy
        peak = max(peak, cash)
        mdd = max(mdd, (peak - cash) / peak * 100)
        for t in trades[-n:]:
            t["eq"] = cash

    rets = [t["ret"] for t in trades]
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    geo = (cash / cap0) ** (1.0 / n) - 1 if n and cash > 0 else 0.0
    win = sum(1 for x in rets if x > 0) / n * 100 if n else 0.0
    years = n_td / 244.0
    ann = ((cash / cap0) ** (1.0 / years) - 1) * 100 if cash > 0 and years > 0 else 0.0
    return {
        "n": n, "start": cap0, "end": cash, "mult": cash / cap0,
        "geo": geo * 100, "ann": ann, "mdd": mdd, "win": win,
        "avg_hold": sum(max(1, int(t["hold"])) for t in trades) / n if n else 0,
        "skipped_n": len(skipped), "skipped": skipped,
        "trades": trades,
    }


def cmp_universe(rows, label):
    print("\n" + "=" * 92)
    print("%s   信号 %d 笔" % (label, len(rows)))
    print("=" * 92)
    out = {}
    for key, name in (("A", "竞价卖出（D 开盘买 → D+1 竞价卖）"),
                      ("C", "次日收盘卖（参考）"),
                      ("B", "涨停接力 + 分档卖出")):
        s = run(rows, key)
        out[key] = s
        print("\n%s" % name)
        print("  成交 %2d 笔（跳过 %d 次资金占用）   胜率 %.1f%%   均持 %.2f 个交易日" % (
            s["n"], s["skipped_n"], s["win"], s["avg_hold"]))
        print("  1 万 → %10.0f 元   ×%.2f   几何 %+.2f%%/笔   年化 %+.0f%%   最大回撤 %.1f%%" % (
            s["end"], s["mult"], s["geo"], s["ann"], s["mdd"]))
        if s["skipped_n"]:
            print("  跳过信号: %s" % ", ".join(
                "%s(%s)" % (x["date"][5:], "/".join(x["names"])[:12]) for x in s["skipped"]))
    return out


def path(rows, key, n_show=12):
    s = run(rows, key)
    print("\n  逐笔净值（%s）" % key)
    for t in s["trades"]:
        print("   %s %-8s %+7.2f%% 持%d日  → %8.0f" % (
            t["date"], t["name"][:8], t["ret"], t["hold"], t["eq"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=3000)
    ap.add_argument("--gap", type=float, default=6.0)
    ap.add_argument("--path", action="store_true")
    a = ap.parse_args()

    rows = build(a.cap, a.gap)
    on = [r for r in rows if r["on"]]
    off = [r for r in rows if not r["on"]]
    print("三一信号 %d 笔（开关开 %d / 开关关 %d），剔除阈值 %g 亿，分档高开 %g%%" % (
        len(rows), len(on), len(off), a.cap, a.gap))
    print("区间 242 个交易日（2025-11-03 ~ 2026-11-02），初始资金 1 万元")

    res = {}
    res["on"] = cmp_universe(on, "【开关开】40 笔（推荐口径）")
    res["all"] = cmp_universe(rows, "【全部】56 笔（不看环境开关）")
    res["off"] = cmp_universe(off, "【开关关】16 笔（仅作对照）")

    if a.path:
        print("\n" + "-" * 92)
        path(on, "B")

    json.dump(res, open("reports/sanyi_equity_compare.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n明细已写入 reports/sanyi_equity_compare.json")


if __name__ == "__main__":
    main()
