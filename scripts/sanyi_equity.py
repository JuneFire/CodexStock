# -*- coding: utf-8 -*-
"""一万块复利：只在三一环境开关=开的日子买入三一票。

口径：
    信号 D 日 09:25 出，D 日开盘买入（开盘即涨停买不进，已在回测里剔除）。
    次要口径 ret_c = D+1 收盘卖（主）；ret_o = D+1 开盘卖（备）。
    持仓跨越 D、D+1 两个交易日 —— 因此必须处理「资金占用」：
      A 顺序复利：按日期顺序每笔用全部可用资金（忽略重叠，偏乐观）
      B 占用模型：持仓期间不再开新仓（同日多只则均分资金，偏保守/真实）
    开关定义见 backend/market_env.py：上证距 MA20 >= -1% 且非均线空头排列。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SW = os.path.join(ROOT, "reports", "sanyi_switch_days.json")
BT = os.path.join(ROOT, "reports", "backtest_sanyi_cap.json")
START = 10000.0


def load():
    sw = json.load(open(SW, encoding="utf-8"))
    on = set(sw["onDays"])
    tr = json.load(open(BT, encoding="utf-8"))["res"]["3000"]["trades"]
    tr = sorted(tr, key=lambda r: r["date"])
    return on, tr


def trade_days():
    """交易日历（来自 data/auction_hist 的文件名）。"""
    import glob
    return sorted(os.path.basename(p)[:-5]
                  for p in glob.glob(os.path.join(ROOT, "data", "auction_hist", "*.json")))


def curve(trades, key, start=START):
    """返回 (最终资金, 净值曲线, 最大回撤, 交易明细)。同日多只均分。"""
    by_day = {}
    for t in trades:
        by_day.setdefault(t["date"], []).append(t)
    cap = start
    pts = [(None, cap)]
    rows = []
    for d in sorted(by_day):
        group = by_day[d]
        before = cap
        per = cap / len(group)
        for t in group:
            per *= (1 + (t.get(key) or 0) / 100.0)
        cap = per * len(group)
        rows.append((d, [x["name"] for x in group], before, cap))
        pts.append((d, cap))
    peak, mdd = start, 0.0
    for _, v in pts:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak * 100)
    return cap, pts, mdd, rows


def curve_occupied(trades, key, start=START, hold_days=1):
    """持仓占用模型：信号 D 买入持有到 D+hold_days 收盘，期间新信号跳过。

    hold_days=1 即 D 开盘买 → D+1 收盘卖（ret_c 口径），占 D、D+1 两个交易日，
    所以「下一个交易日」的信号买不了。用真实交易日历计算，不能用信号日序号。
    """
    cal = trade_days()
    cidx = {d: i for i, d in enumerate(cal)}
    by_day = {}
    for t in trades:
        by_day.setdefault(t["date"], []).append(t)
    cap, peak, mdd = start, start, 0.0
    pts = [(None, cap)]
    busy_until = -1
    taken, skipped = [], []
    for d in sorted(by_day):
        i = cidx.get(d)
        if i is None:
            continue
        if i <= busy_until:
            skipped.extend(by_day[d])
            continue
        group = by_day[d]
        per = cap / len(group)
        for t in group:
            per *= (1 + (t.get(key) or 0) / 100.0)
        cap = per * len(group)
        busy_until = i + hold_days
        taken.extend(group)
        pts.append((d, cap))
        peak = max(peak, cap)
        mdd = max(mdd, (peak - cap) / peak * 100)
    return cap, pts, mdd, taken, skipped


def show(tag, cap, mdd, n):
    print("  %-22s %2d 笔  %10.2f 元  ×%.2f  最大回撤 %.1f%%"
          % (tag, n, cap, cap / START, mdd))


def main():
    on, tr = load()
    hits_on = [t for t in tr if t["date"] in on]
    hits_off = [t for t in tr if t["date"] not in on]
    print("起始资金 %.0f 元；三一全部 %d 笔，其中开关开 %d 笔、开关关 %d 笔"
          % (START, len(tr), len(hits_on), len(hits_off)))
    days_on = sorted(set(t["date"] for t in hits_on))
    print("开关开且有信号 %d 个交易日（同日多只则均分资金）" % len(days_on))
    print()

    for key, label in (("ret_c", "D+1 收盘卖（主）"), ("ret_o", "D+1 开盘卖（备）")):
        print("=" * 78)
        print("【%s】" % label)
        print("=" * 78)
        cap, pts, mdd, rows = curve(hits_on, key)
        show("A 顺序复利(全仓)", cap, mdd, len(hits_on))
        cap2, pts2, mdd2, taken, skipped = curve_occupied(hits_on, key, hold_days=1)
        show("B 持仓占用(真实)", cap2, mdd2, len(taken))
        print("      被占用跳过 %d 笔：%s" % (len(skipped),
              "、".join("%s %s" % (t["date"], t["name"]) for t in skipped) or "无"))
        cap3, _, mdd3, _ = curve(hits_off, key)
        show("对照：开关关全做", cap3, mdd3, len(hits_off))
        print()
        print("  --- A 模型逐笔净值 ---")
        for d, names, before, after in rows:
            print("    %s %-22s %9.2f → %9.2f  (%+.2f%%)"
                  % (d, "/".join(names)[:22], before, after, (after / before - 1) * 100))
        print()


if __name__ == "__main__":
    main()
