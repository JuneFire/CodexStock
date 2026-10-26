# -*- coding: utf-8 -*-
"""共振票"买不进率"测算（33 波双预案共振）

判定（按用户口径）
  涨停价 limit = round(prevClose*(1+涨跌幅限制), 2)
  A 开盘非涨停            open < limit - 0.005                     → 可买
  B 开盘涨停但盘中开板     open≈limit 且 low < limit - 0.005        → 可买（T字/回踩）
  C 一字封死并且竞价薄     open=high=low≈limit 且 竞价额 < 封单额×10% → 买不到
  D 一字但竞价够厚         open=high=low≈limit 且 竞价额 ≥ 封单额×10% → 可挂到（用户规则）

输入
  data/feishu/hongniu/auction_entries.json   入场日竞价（竞价额 amt / 涨停委买额 seal）
输出
  data/feishu/hongniu/executability.json
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import group_backtest as gb         # noqa: E402
import resonance_v2 as rv           # noqa: E402
import preplan_resonance as pp      # noqa: E402

gb.fetch_kline = lambda *a, **k: []

ROOT = rv.ROOT
AUC = os.path.join(ROOT, "data", "feishu", "hongniu", "auction_entries.json")
OUT = os.path.join(ROOT, "data", "feishu", "hongniu", "executability.json")


def limit_pct(code, name=""):
    c = str(code).zfill(6)
    if c.startswith(("300", "301", "688", "689")):
        return 20.0
    if c.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def main():
    dates, didx = pp.load_cal()
    hn = pp.hn_bull()
    lq, _ = pp.lq_bull()
    auc = json.load(open(AUC, encoding="utf-8"))

    # 首波共振
    spots = []
    for d in sorted(set(hn) & set(lq)):
        for c in hn.get(d, set()) & lq.get(d, set()):
            s = rv.series(c)
            if s is None or d not in s.pos:
                continue
            if not rv.rets(c, d):
                continue
            spots.append({"plan_date": d, "code": c})
    by = collections.defaultdict(list)
    for x in spots:
        by[x["code"]].append(x)
    eps = []
    for c, lst in by.items():
        lst.sort(key=lambda z: didx[z["plan_date"]])
        cur = None
        for z in lst:
            if cur is None or didx[z["plan_date"]] - didx[cur[-1]["plan_date"]] > 3:
                if cur:
                    eps.append(cur[0])
                cur = [z]
            else:
                cur.append(z)
        if cur:
            eps.append(cur[0])

    rows = []
    for x in eps:
        c, d = x["code"], x["plan_date"]
        i = didx[d]
        if i + 1 >= len(dates):
            continue
        e = dates[i + 1]
        s = rv.series(c)
        j = s.pos.get(e)
        if j is None or j == 0:
            continue
        bar = s.kl[j]
        prev = s.kl[j - 1]["close"]
        lim = round(prev * (1 + limit_pct(c) / 100.0), 2)
        rec = [r for r in auc.get(e, []) if r["code"] == c]
        a = rec[0] if rec else {}
        amt, seal = a.get("amt"), a.get("seal")
        ratio = (100.0 * amt / seal) if (amt and seal) else None

        if bar["open"] < lim - 0.005:
            cls, why = "A", "开盘非涨停"
        elif bar["low"] < lim - 0.005:
            cls, why = "B", "开盘涨停但盘中开板"
        else:
            if ratio is not None and ratio >= 10:
                cls, why = "D", "一字但竞价额≥封单10%"
            else:
                cls, why = "C", "一字封死（竞价薄）"

        # 收益（open(E) 入场）
        base = bar["open"]
        def r(n):
            k = j + n
            return round(100.0 * (s.kl[k]["close"] / base - 1), 2) if k < len(s.kl) and base else None
        rows.append({"plan_date": d, "entry_date": e, "code": c, "cls": cls, "why": why,
                     "auction_chg": a.get("chg"), "auction_amt_yi": round(amt / 1e8, 2) if amt else None,
                     "seal_yi": round(seal / 1e8, 2) if seal else None,
                     "amt_over_seal_pct": round(ratio, 1) if ratio is not None else None,
                     "open": bar["open"], "low": bar["low"], "limit": lim,
                     "r1": r(1), "r3": r(3), "r5": r(5)})

    def stat(sel, label):
        out = {"label": label, "n": len(sel)}
        for h in (1, 3, 5):
            v = [x["r%d" % h] for x in sel if x["r%d" % h] is not None]
            m = gb.metrics(v) if v else {"n": 0}
            out["d%d" % h] = m
        return out

    def show(b):
        def f(h):
            d = b["d%d" % h]
            return "%3d 胜%5.1f 均%+6.2f" % (d.get("n", 0), d.get("winrate") or 0, d.get("avg") or 0)
        print("%-34s n=%2d  D1 %s | D3 %s | D5 %s" % (b["label"], b["n"], f(1), f(3), f(5)))

    print("首波共振 %d 波\n" % len(rows))
    print("%-11s %-6s %-4s %-5s %-8s %-8s %-6s %s" %
          ("入场日", "代码", "类", "竞价%", "竞价额亿", "封单亿", "额/封%", "说明"))
    for x in sorted(rows, key=lambda z: z["entry_date"]):
        print("%-11s %-6s %-4s %-5s %-8s %-8s %-6s %s" %
              (x["entry_date"], x["code"], x["cls"], x["auction_chg"],
               x["auction_amt_yi"], x["seal_yi"], x["amt_over_seal_pct"], x["why"]))

    cnt = collections.Counter(x["cls"] for x in rows)
    print("\n分类统计：", dict(cnt))
    tradable = [x for x in rows if x["cls"] in ("A", "B", "D")]
    blocked = [x for x in rows if x["cls"] == "C"]
    blocks = [stat(rows, "全部 33 波"),
              stat(tradable, "可执行（A+B+D）"),
              stat(blocked, "买不到（C 一字封死）")]
    print()
    for b in blocks:
        show(b)
    for c in ("A", "B", "D"):
        sel = [x for x in rows if x["cls"] == c]
        if sel:
            b = stat(sel, "  └ %s %s" % (c, sel[0]["why"]))
            blocks.append(b)
            show(b)

    out = {"n": len(rows), "counts": dict(cnt), "blocks": blocks, "rows": rows}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
