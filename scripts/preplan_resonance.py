# -*- coding: utf-8 -*-
"""双预案共振回测：红牛一号「预案」× 来去由心「预案」

数据（均为已有落盘，不重新拉群）
  红牛预案  data/feishu/hongniu/plans.jsonl   {day, time, stocks:[{code,name,alias}], points, text}
  来去预案  data/laiqiu/plans_ocr/index.json  days[].{date, bull[], bear[]}

口径
  D = 预案日（盘前发布）；入场两种：close(D)（当日收盘）与 open(D+1)（次日开盘，更贴近"盘前预案→次日执行"）
  rN = close(D+N)/入场价 - 1；超额 = rN - 同日全市场等权
输出 data/feishu/hongniu/preplan_resonance.json
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import group_signals as gs          # noqa: E402
import group_backtest as gb         # noqa: E402
import resonance_v2 as rv           # noqa: E402

gb.fetch_kline = lambda *a, **k: []

ROOT = rv.ROOT
HN_PLANS = os.path.join(ROOT, "data", "feishu", "hongniu", "plans.jsonl")
HN_WR = os.path.join(ROOT, "data", "feishu", "hongniu", "plan_winrate.json")
HN_FIX = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl")
LQ_FIX = os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "fixed.json")
LQ_PLANS = os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "index.json")
CAL = os.path.join(ROOT, "data", "laiqiu", "kline", "sh000001_idx.json")
SIG = os.path.join(ROOT, "data", "laiqiu", "backtest", "group_signals.json")
OUT = os.path.join(ROOT, "data", "feishu", "hongniu", "preplan_resonance.json")


def load_cal():
    kl = json.load(open(CAL, encoding="utf-8"))
    dates = [r["date"] for r in kl]
    return dates, {d: i for i, d in enumerate(dates)}


def lq_all():
    """返回 (bull, bear)，优先用修正版（已剔除情绪风标、复核描述行）"""
    bull, bear = collections.defaultdict(set), collections.defaultdict(set)
    if os.path.isfile(LQ_FIX):
        d = json.load(open(LQ_FIX, encoding="utf-8"))
        for day, v in d.items():
            for x in v.get("bull") or []:
                bull[day].add(str(x["code"]).zfill(6))
            for x in v.get("bear") or []:
                bear[day].add(str(x["code"]).zfill(6))
        if bull:
            return bull, bear
    b, _ = lq_bull()
    return b, bear


def lq_bull():
    d = json.load(open(LQ_PLANS, encoding="utf-8"))
    raw = json.load(open(gs.STOCKDICT, encoding="utf-8"))     # name -> code
    n2c = {k: str(v).zfill(6) for k, v in raw.items()}
    out = {}
    miss = collections.Counter()
    for day in d.get("days", []):
        codes = set()
        for nm in day.get("bull") or []:
            c = n2c.get(nm)
            if c:
                codes.add(c)
            else:
                miss[nm] += 1
        out[day["date"]] = codes
    return out, miss


def hn_all():
    """返回 (bull, bear)，优先用修正版（日期归属已按标题/收盘后修正）"""
    bull, bear = collections.defaultdict(set), collections.defaultdict(set)
    if os.path.isfile(HN_FIX):
        for line in open(HN_FIX, encoding="utf-8"):
            x = json.loads(line)
            tgt = bull if x["polarity"] == "bull" else (bear if x["polarity"] == "bear" else None)
            if tgt is not None:
                tgt[x["day"]].add(x["code"])
        if bull:
            return bull, bear
    for line in open(HN_PLANS, encoding="utf-8"):
        p = json.loads(line)
        for s2 in p.get("stocks") or []:
            if s2.get("code"):
                bull[p["day"]].add(str(s2["code"]).zfill(6))
    return bull, bear


def hn_bull():
    """红牛预案「看多点名」（复用上一会合已算好的 polarity 标注）；缺失则回退到 plans.jsonl 全量。"""
    out = collections.defaultdict(set)
    if os.path.isfile(HN_WR):
        d = json.load(open(HN_WR, encoding="utf-8"))
        for x in d.get("detail") or []:
            if x.get("polarity") in ("pos", "bull") and x.get("code"):
                out[x.get("d") or x.get("date")].add(str(x["code"]).zfill(6))
        if out:
            return {k: v for k, v in out.items()}
    for line in open(HN_PLANS, encoding="utf-8"):
        p = json.loads(line)
        for s in p.get("stocks") or []:
            if s.get("code"):
                out[p["day"]].add(str(s["code"]).zfill(6))
    return {k: v for k, v in out.items()}


def price_ret(code, date, entry, n):
    s = rv.series(code)
    if s is None or date not in s.pos:
        return None
    i = s.pos[date]
    if entry == "close":
        base = s.kl[i]["close"]
    else:
        if i + 1 >= len(s.kl):
            return None
        base = s.kl[i + 1]["open"]
    j = i + n
    if j >= len(s.kl) or not base:
        return None
    return round(100.0 * (s.kl[j]["close"] / base - 1), 2)


def block(rows, label, entry="close"):
    res = {"label": label, "entry": entry, "n": len(rows)}
    for h in (1, 3, 5):
        v = [r["r%d" % h] for r in rows if r.get("r%d" % h) is not None]
        res["d%d" % h] = gb.metrics(v) if v else {"n": 0}
        res["d%d" % h]["excess"] = rv.excess([(r["date"], r["code"], {"r%d" % h: r["r%d" % h]})
                                             for r in rows], h) if v else {"n": 0}
    return res


def show(b):
    def f(h):
        d = b["d%d" % h]
        return "%4d 胜%5.1f 均%+6.2f 比%4s" % (d.get("n", 0), d.get("winrate") or 0,
                                              d.get("avg") or 0, d.get("pl_ratio"))
    def e(h):
        return "%+5.2f" % (b["d%d" % h].get("excess", {}).get("avg") or 0)
    print("%-34s [%s] D1 %s 超%s | D3 %s 超%s | D5 %s 超%s"
          % (b["label"], b["entry"], f(1), e(1), f(3), e(3), f(5), e(5)))


def main():
    dates, didx = load_cal()
    hn, hn_bear = hn_all()
    lq, lq_bear, miss = lq_all()[0], lq_all()[1], collections.Counter()
    print("红牛预案日 %d（%d 票）｜来去预案日 %d｜名称未映射 %d 个"
          % (len(hn), sum(len(v) for v in hn.values()), len(lq), len(miss)))

    days = sorted(set(hn) & set(lq))
    print("交集日", len(days), days[0], "→", days[-1])

    def collect(entry):
        rows = {"hn_only": [], "lq_only": [], "both": [], "both_or_pm1": [], "either": []}
        lq_prev = {}
        for d in days:
            i = didx.get(d)
            lq_prev[d] = lq.get(d, set()) | (lq.get(dates[i - 1], set()) if i else set())
        for d in days:
            A = hn.get(d, set()) - hn_bear.get(d, set())
            B = lq.get(d, set()) - lq_bear.get(d, set())
            for lst, tag in ((A - B, "hn_only"), (B - A, "lq_only"), (A & B, "both"),
                             (A & lq_prev[d], "both_or_pm1"), (A | B, "either")):
                for c in lst:
                    r = {k: price_ret(c, d, entry, k) for k in (1, 3, 5)}
                    if r[1] is None:
                        continue
                    rows[tag].append({"date": d, "code": c, **{"r%d" % k: r[k] for k in (1, 3, 5)}})
        return rows

    out = {"intersect_days": len(days), "blocks": []}
    for entry in ("close", "open"):
        rows = collect(entry)
        print("\n=== 入场口径 %s ===" % ("当日收盘 close(D)" if entry == "close" else "次日开盘 open(D+1)"))
        for tag, label in (("both", "双预案共振（同日都点名）"),
                           ("both_or_pm1", "双预案共振（红牛当日 ∩ 来去当日/前一日）"),
                           ("hn_only", "仅红牛预案点名"),
                           ("lq_only", "仅来去由心预案点名"),
                           ("either", "两预案并集（任一提到）")):
            b = block(rows[tag], label, entry)
            out["blocks"].append(b)
            show(b)

    # 首波去重叠（同票 ≤3 交易日内的重复提及只算第一次）
    def to_eps(rows):
        dates, didx = load_cal()
        by = collections.defaultdict(list)
        for x in rows:
            by[x["code"]].append(x)
        out = []
        for c, lst in by.items():
            lst.sort(key=lambda z: didx.get(z["date"], 10 ** 9))
            cur = None
            for z in lst:
                if cur is None or (didx.get(z["date"], 0) - didx.get(cur[-1]["date"], 0)) > 3:
                    if cur:
                        out.append(cur[0])
                    cur = [z]
                else:
                    cur.append(z)
            if cur:
                out.append(cur[0])
        return out

    print("")
    print("=== 首波去重叠（同票一波只算一次）===")
    for entry in ("close", "open"):
        rows = collect(entry)
        for tag, label in (("both", "双预案共振·首波"), ("both_or_pm1", "双预案共振(±1)·首波"),
                           ("hn_only", "仅红牛·首波"), ("lq_only", "仅来去·首波")):
            b = block(to_eps(rows[tag]), label, entry)
            out["blocks"].append(b)
            show(b)

    # 明细：双预案共振的样本
    rows_c = collect("close")["both"]
    out["both_samples"] = rows_c[:80]
    print("\n双预案共振样本 %d 个（前 20）:" % len(rows_c))
    for r in rows_c[:20]:
        print("  %s %s r1=%s r3=%s" % (r["date"], r["code"], r["r1"], r["r3"]))
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
