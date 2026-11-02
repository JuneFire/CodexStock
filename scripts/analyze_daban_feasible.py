# -*- coding: utf-8 -*-
"""打板路（D 日涨停价买入 → D+1 卖出）的成交可行性与 T+1 合规收益。

A 股 T+1：D 日买入，最早 D+1 卖出。本脚本所有收益口径均合规。
成交可行性用涨停池字段近似：
  · 一字板（09:25 即封死且全天未开板）→ 排板极难，基本买不到
  · 早板（≤10:30 首次封板且未开板）  → 需排板，部分能成交
  · 换手板（开过板 / 尾盘才封板）    → 涨停价有成交，可买
"""
import glob
import json
import os
import statistics as st
from collections import defaultdict

BT = "reports/backtest_ermian_200.json"
ZP = "data/market/ztpool_bt"


def avg(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 2) if v else 0


def win(v):
    v = [x for x in v if x is not None]
    return round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else 0


def board_type(r):
    """按涨停时间 + 开板次数判板型。"""
    t = (r.get("limitUpTime") or "")[:5]
    ot = r.get("openTimes")
    try:
        ot = int(ot or 0)
    except Exception:
        ot = 0
    if ot > 0:
        return "换手板(开过板)"
    if not t:
        return "未知"
    if t <= "09:25":
        return "一字板"
    if t <= "10:30":
        return "早板(未开板)"
    if t <= "13:00":
        return "上午后段板"
    return "午后板"


def main():
    d = json.load(open(BT, encoding="utf-8"))
    rows = d["rows"]

    # 涨停池补充字段
    pool = {}
    for p in glob.glob(os.path.join(ZP, "*.json")):
        try:
            o = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        dt = o.get("date") or os.path.basename(p)[:-5]
        for r in o.get("rows") or []:
            pool[(dt, r.get("code"))] = r

    hit = 0
    for r in rows:
        z = pool.get((r["date"], r["code"]))
        if not z:
            continue
        hit += 1
        r["limitUpTime"] = z.get("limitUpTime")
        r["openTimes"] = z.get("openTimes")
        r["sealMoney"] = z.get("sealMoney")
        r["btype"] = board_type(r)

    print("样本 %d 条，命中涨停池 %d 条" % (len(rows), hit))
    print("")
    print("=== 打板路：D 日涨停价买入 → D+1 卖出（T+1 合规） ===")
    print("  %-16s %6s %9s %10s %10s %8s" % ("板型", "样本", "D+1开盘卖", "D+1收盘卖", "D+2开盘卖", "胜率"))
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("btype") or "未知"].append(r)
    order = ["一字板", "早板(未开板)", "上午后段板", "午后板", "换手板(开过板)", "未知"]
    for k in order:
        items = groups.get(k) or []
        if not items:
            continue
        print("  %-16s %6d %9s %10s %10s %7s%%" % (
            k, len(items),
            avg([i.get("t1open") for i in items]),
            avg([i.get("t1") for i in items]),
            avg([i.get("t2open") for i in items]),
            win([i.get("t1open") for i in items])))

    print("")
    print("=== 打板路 × 换手率阶梯（按板型） ===")
    for k in ("一字板", "早板(未开板)", "换手板(开过板)"):
        items = [r for r in rows if r.get("btype") == k]
        if not items:
            continue
        g2 = defaultdict(list)
        for r in items:
            g2[r.get("grade")].append(r)
        print("  [%s]" % k)
        for g in ("最优", "次优", "健康", "中性", "超标", "高风险"):
            it = g2.get(g) or []
            if len(it) < 20:
                continue
            print("    %-6s n=%-5d D+1开盘卖 %6s D+1收盘卖 %6s" % (
                g, len(it), avg([i.get("t1open") for i in it]), avg([i.get("t1") for i in it])))

    print("")
    print("=== 打板路 × 连板数（T+1 合规） ===")
    for lb in (1, 2, 3):
        it = [r for r in rows if r.get("lb") == lb]
        if len(it) < 20:
            continue
        print("  %d板 n=%-5d D+1开盘卖 %6s D+1收盘卖 %6s D+2开盘卖 %6s" % (
            lb, len(it), avg([i.get("t1open") for i in it]),
            avg([i.get("t1") for i in it]), avg([i.get("t2open") for i in it])))

    print("")
    print("=== 只有「换手板」可实际买到：其 T+1 合规收益 ===")
    easy = [r for r in rows if r.get("btype") == "换手板(开过板)"]
    if easy:
        print("  n=%d  D+1开盘卖 %s%%  D+1收盘卖 %s%%  D+2开盘卖 %s%%  胜率 %s%%" % (
            len(easy), avg([i.get("t1open") for i in easy]), avg([i.get("t1") for i in easy]),
            avg([i.get("t2open") for i in easy]), win([i.get("t1open") for i in easy])))
        dates = sorted(set(i["date"] for i in easy))
        for i in range(4):
            part = set(dates[i * len(dates) // 4:(i + 1) * len(dates) // 4])
            it = [r for r in easy if r["date"] in part]
            if len(it) < 20:
                continue
            print("    %s~%s n=%-4d D+1开盘卖 %s%%" % (
                min(part), max(part), len(it), avg([x.get("t1open") for x in it])))
        by = defaultdict(int)
        for r in easy:
            by[r["date"]] += 1
        v = sorted(by.values())
        print("    覆盖 %d 天，每日中位 %s 只、均值 %.1f" % (len(v), st.median(v), sum(v) / len(v)))


if __name__ == "__main__":
    main()
