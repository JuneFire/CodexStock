# -*- coding: utf-8 -*-
"""竞价三一 × 上证均线位置（MA5 / MA10 / MA20 / MA60）胜率拆分。

口径：
    三一明细来自 reports/backtest_sanyi_cap.json（scripts/backtest_sanyi_cap.py 产出）。
    收益 ret_c = D 日开盘买 → D+1 收盘卖；ret_o = D 日开盘买 → D+1 开盘卖。
    均线只用 **D-1 及之前**的收盘（09:25 那一刻能看到的信息），不用 D 日收盘——
    否则就是未来函数。

    python scripts/analyze_sanyi_ma.py            # 默认按生产口径（剔除>3000亿）
    python scripts/analyze_sanyi_ma.py --cap 0    # 不剔除
"""
import argparse
import json
import os
import statistics as stt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT = os.path.join(ROOT, "reports", "backtest_sanyi_cap.json")
IDX = os.path.join(ROOT, "data", "index_kline", "sh000001.json")
OUT = os.path.join(ROOT, "reports", "sanyi_ma.json")
MAS = (5, 10, 20, 60)


def load_idx():
    bars = json.load(open(IDX, encoding="utf-8"))
    bars = sorted(bars, key=lambda b: str(b["date"])[:10])
    return bars, {str(b["date"])[:10]: i for i, b in enumerate(bars)}


def ma_at(bars, i, n):
    if i + 1 < n:
        return None
    seg = bars[i - n + 1:i + 1]
    return sum(float(b["close"]) for b in seg) / n


def stat(rs, key="ret_c"):
    v = [r[key] for r in rs if r.get(key) is not None]
    n = len(v)
    if n == 0:
        return None
    m, sd = stt.mean(v), stt.pstdev(v)
    return {"n": n, "mean": m, "median": stt.median(v),
            "win": sum(1 for x in v if x > 0) / n * 100,
            "t": (m / (sd / n ** 0.5)) if sd else 0.0, "cum": sum(v)}


def line(name, s, key=""):
    if not s:
        print("  %-30s n=0" % name)
        return
    print("  %-30s n=%3d 均值%+7.2f%% 中位%+7.2f%% 胜率%5.1f%% t=%+6.2f 累计%+8.1f%%"
          % (name, s["n"], s["mean"], s["median"], s["win"], s["t"], s["cum"] * (1 if key == "ret_c" else 1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", default="3000", help="backtest_sanyi_cap.json 里的 cap 键：0/1000/2000/3000/5000")
    args = ap.parse_args()

    d = json.load(open(BT, encoding="utf-8"))["res"]
    if args.cap not in d:
        print("可用的 cap 键：", list(d))
        return
    trades = d[args.cap]["trades"]
    bars, pos = load_idx()

    rows = []
    for t in trades:
        i = pos.get(t["date"])
        if not i or i < 1:
            continue
        j = i - 1                      # 均线取 D-1 收盘（09:25 可见）
        c = float(bars[j]["close"])
        rec = dict(t)
        rec["idxDate"] = str(bars[j]["date"])[:10]
        rec["close"] = c
        ok_all = True
        for n in MAS:
            m = ma_at(bars, j, n)
            rec["ma%d" % n] = round(m, 2) if m else None
            rec["above%d" % n] = bool(m and c >= m)
            if not rec["above%d" % n]:
                ok_all = False
        rec["aboveAll"] = ok_all
        rec["nAbove"] = sum(1 for n in (5, 10, 20) if rec["above%d" % n])
        m5, m10, m20 = rec["ma5"], rec["ma10"], rec["ma20"]
        rec["bullStack"] = bool(m5 and m10 and m20 and m5 > m10 > m20)
        rec["bearStack"] = bool(m5 and m10 and m20 and m5 < m10 < m20)
        rec["dev20"] = round((c / rec["ma20"] - 1) * 100, 2) if rec["ma20"] else None
        rows.append(rec)

    print("三一 %d 笔（cap 键 %s）；上证均线取 D-1 收盘，共 %d 根指数K线（%s ~ %s）"
          % (len(rows), args.cap, len(bars), str(bars[0]["date"])[:10], str(bars[-1]["date"])[:10]))
    for key, label in (("ret_c", "次日收盘卖"), ("ret_o", "次日开盘卖")):
        print()
        print("=" * 100)
        print("【%s】" % label)
        print("=" * 100)
        line("全部三一", stat(rows, key), key)
        for n in MAS:
            up = [r for r in rows if r["above%d" % n]]
            dn = [r for r in rows if not r["above%d" % n]]
            print()
            print("  --- 上证 MA%d ---" % n)
            line("  站上 MA%d" % n, stat(up, key), key)
            line("  跌破 MA%d" % n, stat(dn, key), key)
        print()
        print("  --- 组合条件 ---")
        line("三条全站上(MA5/10/20)", stat([r for r in rows if r["aboveAll"]], key), key)
        line("至少跌破一条", stat([r for r in rows if not r["aboveAll"]], key), key)
        line("均线多头排列 5>10>20", stat([r for r in rows if r["bullStack"]], key), key)
        line("均线空头排列 5<10<20", stat([r for r in rows if r["bearStack"]], key), key)
        for k in (3, 2, 1, 0):
            line("站上 %d 条(MA5/10/20)" % k, stat([r for r in rows if r["nAbove"] == k], key), key)
        print()
        print("  --- 距离 MA20 的偏离度分档 ---")
        for lo, hi, tag in ((-99, -3, "偏离 < -3%"), (-3, -1, "-3% ~ -1%"),
                            (-1, 1, "-1% ~ +1%"), (1, 3, "+1% ~ +3%"), (3, 99, "> +3%")):
            line(tag, stat([r for r in rows if r["dev20"] is not None
                            and lo <= r["dev20"] < hi], key), key)

    json.dump({"cap": args.cap, "rows": rows}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print()
    print("明细 → reports/sanyi_ma.json")


if __name__ == "__main__":
    main()
