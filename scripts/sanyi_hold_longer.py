# -*- coding: utf-8 -*-
"""
竞价三一：改「次日收盘卖」为「涨停/收红接力持有」。

用户口径（m03547）：
  「既然满足『金额前三 + 换手第一』这种条件的票这么稀少，那么我们是不是可以持股时间
   长一点，比如隔日继续涨停/收红就持有，断板就离场（尾盘 14:50 没有回封）」

入场：D 日 09:25 出三一信号 → D 日开盘买入（与既有回测一致）。
T+1 约束：最早只能 D+1 卖出。
出场规则：
  next_close      D+1 收盘卖（既有基线 ret_c）
  next_open       D+1 竞价卖（既有基线 ret_o）
  lu_chain        D+1 起，收盘封住涨停就继续持有；第一根没封住的当日收盘卖出
  red_chain       D+1 起，收盘收红就继续持有；第一根收绿的当日收盘卖出
  broken_exit     用户原话口径：收盘涨停 → 持有；收盘收红且未炸板 → 持有；
                  断板（盘中摸过涨停价但收盘没封住）或收绿 → 当日收盘卖出
"""
import json
import os
import sys
import math
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from backtest_workflow_t1 import limit_pct  # noqa: E402

CJS = os.path.join(ROOT, "reports", "backtest_sanyi_cap.json")
KDIR = os.path.join(ROOT, "data", "backtest_kline")
OUT = os.path.join(ROOT, "reports", "sanyi_hold.json")
CAP_KEY = "3000"
MAX_HOLD = 20          # 最多持有 20 个交易日
COST = 0.0             # 暂不计费用


# ---------------------------------------------------------------- 数据
_bars = {}


def bars(code):
    if code in _bars:
        return _bars[code]
    p = os.path.join(KDIR, "%s.json" % code)
    b = []
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            b = d["bars"] if isinstance(d, dict) and "bars" in d else d
        except Exception:
            b = []
    _bars[code] = b
    return b


def idx_of(b, ds):
    lo, hi = 0, len(b) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if b[mid]["date"] < ds:
            lo = mid + 1
        elif b[mid]["date"] > ds:
            hi = mid - 1
        else:
            return mid
    return -1


def day_state(b, i, pct):
    """返回第 i 根 bar 的封板状态。prevClose 用前一根收盘。"""
    if i <= 0 or i >= len(b):
        return None
    prev = float(b[i - 1]["close"])
    if prev <= 0:
        return None
    limit = round(prev * (1 + pct / 100.0) + 1e-9, 2)
    hi = float(b[i]["high"])
    cl = float(b[i]["close"])
    lu = cl >= limit - 0.005
    touched = hi >= limit - 0.005
    return {
        "date": b[i]["date"],
        "prevClose": prev,
        "limit": limit,
        "open": float(b[i]["open"]),
        "high": hi,
        "low": float(b[i]["low"]),
        "close": cl,
        "lu": lu,
        "touched": touched,
        "broken": touched and not lu,      # 摸过涨停但没封住 = 断板/炸板
        "red": cl > prev + 1e-9,
        "chg": (cl / prev - 1) * 100,
    }


def walk(code, d, name):
    b = bars(code)
    if not b:
        return None
    i0 = idx_of(b, d)
    if i0 < 0:
        return None
    pct = limit_pct(code, name)
    # D 日买入价 = D 开盘
    buy = float(b[i0]["open"])

    # 收盘即涨停无法买入（开盘就在涨停价）→ 与既有回测一致，剔除
    d_state = day_state(b, i0, pct)
    if d_state and d_state["touched"] and abs(buy - d_state["limit"]) < 0.006:
        return {"skip": "D 日开盘即涨停"}

    days = []
    for k in range(1, MAX_HOLD + 1):
        i = i0 + k
        if i >= len(b):
            break
        st = day_state(b, i, pct)
        if st is None:
            break
        st["k"] = k
        days.append(st)

    if not days:
        return None

    out = {
        "date": d, "code": code, "name": name, "buy": buy,
        "d_lu": bool(d_state and d_state["lu"]),
        "d_chg": d_state["chg"] if d_state else None,
        "days": days,
    }
    out["next_open"] = (days[0]["open"] / buy - 1) * 100
    out["next_close"] = (days[0]["close"] / buy - 1) * 100

    # 涨停接力
    for st in days:
        if not st["lu"]:
            out["lu_chain"] = (st["close"] / buy - 1) * 100
            out["lu_hold"] = st["k"]
            break
    else:
        out["lu_chain"] = (days[-1]["close"] / buy - 1) * 100
        out["lu_hold"] = days[-1]["k"]

    # 收红接力
    for st in days:
        if not st["red"]:
            out["red_chain"] = (st["close"] / buy - 1) * 100
            out["red_hold"] = st["k"]
            break
    else:
        out["red_chain"] = (days[-1]["close"] / buy - 1) * 100
        out["red_hold"] = days[-1]["k"]

    # 用户原话：涨停 or （收红且未炸板）→ 持有；否则当日收盘走
    for st in days:
        hold = st["lu"] or (st["red"] and not st["broken"])
        if not hold:
            out["broken_exit"] = (st["close"] / buy - 1) * 100
            out["broken_hold"] = st["k"]
            out["broken_why"] = "断板" if st["broken"] else "收绿"
            break
    else:
        out["broken_exit"] = (days[-1]["close"] / buy - 1) * 100
        out["broken_hold"] = days[-1]["k"]
        out["broken_why"] = "持有到上限"
    return out


# ---------------------------------------------------------------- 统计
def stats(rows, key):
    v = [r[key] for r in rows if r.get(key) is not None]
    n = len(v)
    if not n:
        return None
    mean = sum(v) / n
    sv = sorted(v)
    med = sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1)) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    win = sum(1 for x in v if x > 0) / n * 100
    return {"n": n, "mean": mean, "median": med, "sd": sd, "t": t,
            "win": win, "sum": sum(v), "min": min(v), "max": max(v)}


def equity(rows, key, hold_key=None):
    """顺序全仓复利。hold_key 给出时按持仓天数占用资金（占用期间跳过信号）。"""
    start = 10000.0
    cap = start
    peak = start
    mdd = 0.0
    taken = 0
    busy_until = None
    for r in rows:
        if hold_key and busy_until is not None and r["date"] <= busy_until:
            continue
        v = r.get(key)
        if v is None:
            continue
        before = cap
        cap *= (1 + v / 100.0)
        taken += 1
        if hold_key:
            busy_until = add_td(r["date"], r[hold_key])
        if cap > peak:
            peak = cap
        dd = (peak - cap) / peak * 100
        if dd > mdd:
            mdd = dd
    return cap, mdd, taken


CAL = None


def cal():
    """交易日历取上证指数日 K（684 根），不能用个股 K——backtest_kline 里只有
    涨停池的 3409 只，600000 这类标杆股并不在其中，取不到会把占用算成 0 天。"""
    global CAL
    if CAL is None:
        p = os.path.join(ROOT, "data", "index_kline", "sh000001.json")
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            b = d["bars"] if isinstance(d, dict) and "bars" in d else d
            CAL = [x["date"] for x in b]
        except Exception:
            CAL = []
    return CAL


def add_td(ds, n):
    c = cal()
    try:
        i = c.index(ds)
    except ValueError:
        return ds
    j = min(len(c) - 1, i + n)
    return c[j]


def show(label, s, cap=None, mdd=None, taken=None, hold=None):
    if not s:
        print("  %-22s 无样本" % label)
        return
    extra = ""
    if cap is not None:
        extra = "  终值 %8.0f  ×%.2f  回撤 %5.1f%%" % (cap, cap / 10000.0, mdd or 0)
    hp = "  均持 %.1f 日" % hold if hold else ""
    print("  %-22s n=%2d  均值 %+7.2f%%  中位 %+7.2f%%  胜率 %5.1f%%  t=%+5.2f%s%s"
          % (label, s["n"], s["mean"], s["median"], s["win"], s["t"], hp, extra))


def main():
    with open(CJS, encoding="utf-8") as f:
        d = json.load(f)
    trades = d["res"][CAP_KEY]["trades"]
    trades.sort(key=lambda x: x["date"])

    rows = []
    skipped = 0
    for t in trades:
        r = walk(t["code"], t["date"], t["name"])
        if not r or "skip" in r:
            skipped += 1
            continue
        r["circYi"] = t.get("circYi")
        r["rAmt"] = t.get("rAmt")
        r["rTrf"] = t.get("rTrf")
        rows.append(r)

    print("=" * 100)
    print("竞价三一 · 涨停/收红接力持有回测")
    print("=" * 100)
    print("三一信号 %d 笔 → 可评估 %d 笔（剔除开盘即涨停 %d 笔）"
          % (len(trades), len(rows), skipped))
    dl = sum(1 for r in rows if r["d_lu"])
    print("其中 D 日（买入当日）收盘封住涨停的：%d 笔（%.0f%%）"
          % (dl, dl / max(1, len(rows)) * 100))
    print()

    keys = [
        ("next_open", "D+1 竞价卖（基线）", None, None),
        ("next_close", "D+1 收盘卖（基线）", None, None),
        ("lu_chain", "涨停接力·收盘走", "lu_hold", None),
        ("red_chain", "收红接力·收盘走", "red_hold", None),
        ("broken_exit", "涨停/收红·断板即走", "broken_hold", None),
    ]
    print("─" * 100)
    print("【口径一：等权平均（每笔 1 万，只看单笔收益）】")
    for key, label, hk, _ in keys:
        s = stats(rows, key)
        hold = None
        if hk:
            hv = [r[hk] for r in rows if r.get(hk)]
            hold = sum(hv) / len(hv) if hv else None
        show(label, s, hold=hold)

    print()
    print("─" * 100)
    print("【口径二：1 万全仓顺序复利（接力规则无固定持有期，按实际占用跳过）】")
    for key, label, hk, _ in keys:
        cap, mdd, taken = equity(rows, key, hold_key=hk)
        s = stats(rows, key)
        show(label, s, cap=cap, mdd=mdd, taken=taken,
             hold=(sum(r[hk] for r in rows if r.get(hk)) / max(1, len([r for r in rows if r.get(hk)]))) if hk else None)
        print("      实际成交 %d 笔" % taken)

    print()
    print("─" * 100)
    print("【最长连板 / 持有天数分布】")
    for key, label, hk, _ in keys[2:]:
        dist = {}
        for r in rows:
            k = r.get(hk)
            if k:
                dist[k] = dist.get(k, 0) + 1
        print("  %-22s %s" % (label, "  ".join("%d日:%d" % (k, dist[k]) for k in sorted(dist))))
    ld = sum(1 for r in rows if r["lu_chain"] > r["next_close"])
    print()
    print("  涨停接力优于次日收盘卖的比例：%d/%d = %.0f%%" % (ld, len(rows), ld / len(rows) * 100))

    print()
    print("─" * 100)
    print("【逐笔明细】")
    print("  %-11s %-7s %-9s %6s %6s %5s %8s %8s %8s %8s %8s"
          % ("D日", "代码", "名称", "D涨幅", "D封板", "持有", "次开", "次收", "涨停接力", "收红接力", "断板即走"))
    for r in rows:
        print("  %-11s %-7s %-9s %+6.2f %6s %5s %+8.2f %+8.2f %+8.2f %+8.2f %+8.2f %s"
              % (r["date"], r["code"], r["name"][:9], r["d_chg"] or 0,
                 "是" if r["d_lu"] else "否",
                 "%d日" % r["broken_hold"],
                 r["next_open"], r["next_close"], r["lu_chain"],
                 r["red_chain"], r["broken_exit"], r.get("broken_why", "")))

    # 保存
    slim = []
    for r in rows:
        slim.append({k: r[k] for k in
                     ("date", "code", "name", "buy", "d_lu", "d_chg",
                      "next_open", "next_close", "lu_chain", "lu_hold",
                      "red_chain", "red_hold", "broken_exit", "broken_hold",
                      "broken_why", "circYi", "rAmt", "rTrf")})
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"cap": CAP_KEY, "n": len(rows),
                   "stats": {k: stats(rows, k) for k, _, _, _ in keys},
                   "trades": slim}, f, ensure_ascii=False, indent=1)
    print()
    print("明细已写入 %s" % OUT)


if __name__ == "__main__":
    main()
