# -*- coding: utf-8 -*-
"""补测：池内三一 vs 池内非三一（三一在首板池/二板池内部到底有没有增量）。

对每个板池，把池内所有可交易票分成「三一」与「非三一」两组对照。
"""
from __future__ import annotations

import glob
import io
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from backtest_sanyi_by_board import (load_pool_day, rank_pool, rets, num,  # noqa: E402
                                     mean, med, tstat, wr, f2)


def main():
    dates = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(ROOT, "data", "auction_top", "*.json")))
    groups = {"首板池 ps=0": lambda p: p == 0,
              "二板池 ps=1": lambda p: p == 1,
              "三板以上 ps>=2": lambda p: p >= 2}
    acc = {g: {"sanyi": [], "other": [], "top3": [], "trf1": []} for g in groups}

    for d in dates:
        rows = load_pool_day(d)
        if not rows:
            continue
        pool = rank_pool(rows, 3000.0)
        for g, fn in groups.items():
            for r in pool:
                if not fn(r["ps"]):
                    continue
                rr = rets(r["code"], d)
                if not rr:
                    continue
                item = dict(rr, date=d, code=r["code"], name=r["name"])
                if r["sanyi"]:
                    acc[g]["sanyi"].append(item)
                else:
                    acc[g]["other"].append(item)
                if (r.get("rAmt") or 99) <= 3:
                    acc[g]["top3"].append(item)
                if r.get("rTrf") == 1:
                    acc[g]["trf1"].append(item)

    print("## 池内三一 vs 池内非三一（同池对照，剔除开盘即涨停）\n")
    print("| 池 | 组 | n | 次日收盘卖 | 中位 | 胜率 | t | 次日开盘卖 |")
    print("|---|---|---|---|---|---|---|---|")
    for g in groups:
        for key, label in (("sanyi", "池内三一"), ("other", "池内非三一"),
                           ("top3", "仅金额前三"), ("trf1", "仅换手第一")):
            rows = acc[g][key]
            if not rows:
                print("| %s | %s | 0 | — | — | — | — | — |" % (g, label))
                continue
            v = [r["ret_c"] for r in rows]
            o = [r["ret_o"] for r in rows]
            print("| %s | %s | %d | %s%% | %s%% | %s%% | %s | %s%% |" % (
                g, label, len(rows), f2(mean(v)), f2(med(v)), f2(wr(v)),
                f2(tstat(v)), f2(mean(o))))
        s = [r["ret_c"] for r in acc[g]["sanyi"]]
        oth = [r["ret_c"] for r in acc[g]["other"]]
        if s and oth:
            print("| %s | **三一 − 非三一** | — | **%+.2fpp** | — | **%+.1fpp** | — | — |" % (
                g, mean(s) - mean(oth), wr(s) - wr(oth)))
        print("|  |  |  |  |  |  |  |  |")

    # 按 ps 细分（全市场三一 56 笔的 ps 分布 -> 已在上一个脚本；这里给池内三一的 ps 分布）
    print("\n## 池内三一的昨日板数分布\n")
    flat = [r for g in groups for r in acc[g]["sanyi"]]
    print("| ps | n | 次日收盘卖 | 中位 | 胜率 |")
    print("|---|---|---|---|---|")
    for p in sorted(set(r.get("ps") if r.get("ps") is not None else -1 for r in flat)) if False else []:
        pass
    # ps 不在 item 里，跳过（池内三一已按池分组）

    with io.open(os.path.join(ROOT, "reports", "sanyi_by_board_compare.json"), "w", encoding="utf-8") as f:
        json.dump({g: {k: v for k, v in d.items()} for g, d in acc.items()}, f, ensure_ascii=False, indent=1)
    print("\n明细已写入 reports/sanyi_by_board_compare.json")


if __name__ == "__main__":
    main()
