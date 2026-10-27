# -*- coding: utf-8 -*-
"""每日盘后共振扫描 + 窗口回测

用法
  python scripts/daily_scan.py --backtest --days 90          # 最近 90 天回测
  python scripts/daily_scan.py --scan 2026-10-23            # 生成该日的次日候选清单
  python scripts/daily_scan.py --scan latest                # 用数据里最新一天

共振口径（与 research 结论一致）
  k = 最近 3 个交易日内提到该票的不同群数（只用过去信息）
  「首波」= 同票连续提及（间隔≤3 交易日）合并，只取第一次入场日
  预案层 = 来去由心当日预案 bull 名单（data/laiqiu/plans_ocr）
输出
  data/laiqiu/backtest/daily_scan.json + reports/每日共振扫描_<date>.md
"""
import argparse
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
HN_WR = os.path.join(ROOT, "data", "feishu", "hongniu", "plan_winrate.json")
LQ_PLANS = os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "index.json")
SIG = os.path.join(ROOT, "data", "laiqiu", "backtest", "group_signals.json")
PRE = os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "index.json")
CAL = os.path.join(ROOT, "data", "laiqiu", "kline", "sh000001_idx.json")
OUT_JSON = os.path.join(ROOT, "data", "laiqiu", "backtest", "daily_scan.json")

GROUPS = ("laiqiu", "jieshi", "hongniu", "linshangwo", "jshen")
NAME_CN = {"laiqiu": "来去由心", "jieshi": "借势而为", "hongniu": "红牛一号",
           "linshangwo": "林尚沃", "jshen": "J神"}


def load_all():
    sigs = json.load(open(SIG, encoding="utf-8"))
    cal = json.load(open(CAL, encoding="utf-8"))
    dates = [r["date"] for r in cal]
    didx = {d: i for i, d in enumerate(dates)}

    mention = collections.defaultdict(set)     # (date, code) -> {group}
    pos = collections.defaultdict(set)
    meta = collections.defaultdict(list)       # (date, code) -> [(group, time, text)]
    for s in sigs:
        if s["group"] not in GROUPS:
            continue
        k = (s["date"], s["code"])
        mention[k].add(s["group"])
        if s["polarity"] == "pos":
            pos[k].add(s["group"])
        meta[k].append((s["group"], s.get("time") or "", (s.get("text") or "")[:60]))
    return sigs, dates, didx, mention, pos, meta


def preplan_map():
    """来去由心预案：date -> set(股票名)（bull 名单）"""
    if not os.path.isfile(PRE):
        return {}
    d = json.load(open(PRE, encoding="utf-8"))
    out = {}
    for day in d.get("days", []):
        out[day["date"]] = set(day.get("bull") or [])
    return out


def name2code():
    raw = json.load(open(gs.STOCKDICT, encoding="utf-8"))     # name -> code
    return {k: str(v).zfill(6) for k, v in raw.items()}


def build_rows(dates, didx, mention, pos, meta):
    rows = []
    for (d, c), grps in mention.items():
        s = rv.series(c)
        if s is None or d not in s.pos:
            continue
        r = rv.rets(c, d)
        i = didx.get(d)
        if not r or i is None:
            continue
        win = dates[max(0, i - 2): i + 1]
        wg = set()
        for d2 in win:
            wg |= mention.get((d2, c), set())
        wp = set()
        for d2 in win:
            wp |= pos.get((d2, c), set())
        rows.append({"date": d, "code": c, "k": len(wg), "kpos": len(wp),
                     "groups": sorted(wg), "r": r})
    rows.sort(key=lambda x: (x["date"], x["code"]))
    return rows


def to_episodes(rows, didx, gap=3):
    by = collections.defaultdict(list)
    for x in rows:
        by[x["code"]].append(x)
    eps = []
    for c, lst in by.items():
        lst.sort(key=lambda z: didx.get(z["date"], 10 ** 9))
        cur = None
        for z in lst:
            if cur is None or (didx.get(z["date"], 0) - didx.get(cur[-1]["date"], 0)) > gap:
                cur = [z]
                eps.append(cur)
            else:
                cur.append(z)
    out = []
    for e in eps:
        g = set()
        for x in e[:3]:
            g |= set(x["groups"])
        out.append({"date": e[0]["date"], "code": e[0]["code"], "k": len(g),
                    "groups": sorted(g), "r": e[0]["r"], "n_mentions": len(e)})
    out.sort(key=lambda x: x["date"])
    return out


def stat(rows, key="k", label=""):
    res = {"label": label, "n": len(rows)}
    for h in (1, 3, 5):
        v = [x["r"]["r%d" % h] for x in rows if x["r"].get("r%d" % h) is not None]
        res["d%d" % h] = gb.metrics(v) if v else {"n": 0}
        res["d%d" % h]["excess"] = rv.excess([(x["date"], x["code"], x["r"]) for x in rows], h)
    return res


def show(b):
    f = lambda h: "%4d 胜%5.1f 均%+6.2f 比%4s" % (
        b["d%d" % h].get("n", 0), b["d%d" % h].get("winrate") or 0,
        b["d%d" % h].get("avg") or 0, b["d%d" % h].get("pl_ratio"))
    e = lambda h: "%+5.2f" % (b["d%d" % h].get("excess", {}).get("avg") or 0)
    print("%-30s D1 %s 超%s | D3 %s 超%s | D5 %s 超%s"
          % (b["label"], f(1), e(1), f(3), e(3), f(5), e(5)))


def backtest(days=90):
    sigs, dates, didx, mention, pos, meta = load_all()
    rows = build_rows(dates, didx, mention, pos, meta)
    eps = to_episodes(rows, didx)
    end = dates[-1]
    start = dates[max(0, len(dates) - days)]
    print("window %s → %s" % (start, end))

    def win(sel):
        return [x for x in sel if start <= x["date"] <= end]

    out = {"window": [start, end], "blocks": []}
    for label, sel in (("首波 k=1", [x for x in eps if x["k"] == 1]),
                       ("首波 k=2", [x for x in eps if x["k"] == 2]),
                       ("首波 k>=3", [x for x in eps if x["k"] >= 3]),
                       ("首波 k>=2", [x for x in eps if x["k"] >= 2])):
        b = stat(win(sel), label=label)
        out["blocks"].append(b)
        show(b)

    # 组合
    for combo in (("hongniu", "laiqiu"), ("jieshi", "laiqiu"), ("jshen", "laiqiu"),
                  ("jieshi", "jshen"), ("hongniu", "linshangwo")):
        sel = [x for x in eps if set(combo) <= set(x["groups"])]
        if len(sel) < 5:
            continue
        b = stat(win(sel), label="+".join(NAME_CN[g] for g in combo))
        out["blocks"].append(b)
        show(b)

    # 预案层
    pm = preplan_map()
    n2c = name2code()
    pre_rows = []
    for d, names in pm.items():
        if not (start <= d <= end):
            continue
        for nm in names:
            c = n2c.get(nm)
            if not c:
                continue
            s = rv.series(c)
            if s is None or d not in s.pos:
                continue
            r = rv.rets(c, d)
            if not r:
                continue
            i = didx.get(d)
            g = set()
            for d2 in dates[max(0, i - 1): i + 1]:
                g |= mention.get((d2, c), set())
            pre_rows.append({"date": d, "code": c, "name": nm, "k": len(g),
                             "groups": sorted(g), "r": r})
    print("\n预案样本 %d（%s→%s）" % (len(pre_rows), start, end))
    for label, sel in (("预案·全部", pre_rows),
                       ("预案·无其他群提及", [x for x in pre_rows if x["k"] == 0]),
                       ("预案·有群共振", [x for x in pre_rows if x["k"] >= 1]),
                       ("预案·≥2群共振", [x for x in pre_rows if x["k"] >= 2])):
        b = stat(sel, label=label)
        out["blocks"].append(b)
        show(b)
    out["preplan_n"] = len(pre_rows)
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nwrote", OUT_JSON)


def scan(date="latest"):
    sigs, dates, didx, mention, pos, meta = load_all()
    d = dates[-1] if date == "latest" else date
    i = didx[d]
    agg = {}
    for d2 in dates[max(0, i - 2): i + 1]:
        for (dd, c), grps in list(mention.items()):
            if dd != d2:
                continue
            a = agg.setdefault(c, {"code": c, "groups": set(), "events": []})
            a["groups"] |= grps
            a["events"] += meta.get((dd, c), [])
    rows = []
    for c, a in agg.items():
        rows.append({"code": c, "k": len(a["groups"]), "groups": sorted(a["groups"]),
                     "events": sorted(set(a["events"]))[-4:]})
    rows = [x for x in rows if x["k"] >= 2]
    rows.sort(key=lambda x: (-x["k"], x["code"]))
    print("== %s 共振候选（k>=2）==" % d)
    for x in rows:
        print("%s k=%d %s" % (x["code"], x["k"],
                              "  ".join(NAME_CN[g] for g in x["groups"])))
        for g, t, txt in x["events"][-3:]:
            print("    [%s %s] %s" % (NAME_CN[g], t, txt))
    out = {"date": d, "candidates": rows}
    p = os.path.join(ROOT, "reports", "每日共振扫描_%s.json" % d)
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", p, "n=%d" % len(rows))


HN_FIX = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl")
PLANS_RAW = os.path.join(ROOT, "data", "feishu", "hongniu", "plans.jsonl")
LQ_FIX = os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "fixed.json")


REVERSE_WORDS = ("反核", "次弱", "地板", "跌停开", "低吸", "反包", "低位")
AUCTION_DIR = os.path.join(ROOT, "data", "auction_top")
_AUC_CACHE = {}


def auction_of(date):
    """读悟道竞价归档：date -> {code: record}"""
    if date in _AUC_CACHE:
        return _AUC_CACHE[date]
    p = os.path.join(AUCTION_DIR, "%s.json" % date)
    m = {}
    if os.path.isfile(p):
        try:
            for r in json.load(open(p, encoding="utf-8")).get("records") or []:
                m[r["code"]] = r
        except Exception:
            m = {}
    _AUC_CACHE[date] = m
    return m


def executable(date, code):
    """L5 执行判定：竞价额 ≥ 封单额×10% → 可挂到；非涨停开盘直接可买。

    返回 (可买?, 说明)
    """
    a = auction_of(date).get(code)
    sr = rv.series(code)
    i = sr.pos.get(date) if sr else None
    open_chg = None
    lim_open = False
    if i and i > 0:
        prev = sr.kl[i - 1]["close"]
        op = sr.kl[i]["open"]
        if prev and op:
            open_chg = 100.0 * (op / prev - 1)
            pct = 20.0 if str(code).startswith(("300", "301", "688", "689")) else 10.0
            lim_open = abs(op - round(prev * (1 + pct / 100), 2)) < 0.005
    if a:
        seal, amt = a.get("sealAmount"), a.get("auctionAmount")
        if not seal:
            return True, "非涨停开盘"
        ratio = 100.0 * amt / seal
        if ratio >= 10:
            return True, "竞价/封单 %.0f%%" % ratio
        return False, "封死 竞价/封单 %.1f%%" % ratio
    if lim_open:
        return None, "涨停开盘·无竞价数据"
    return True, "非涨停开盘(推定)"



AUCTION_DIR = os.path.join(ROOT, "data", "auction_top")
_AUC = {}


def auction_of(date):
    """读取当日竞价归档（悟道 auction_hist）→ {code: (auctionAmount, sealAmount)}"""
    if date in _AUC:
        return _AUC[date]
    m = {}
    fp = os.path.join(AUCTION_DIR, "%s.json" % date)
    if os.path.isfile(fp):
        try:
            for r in json.load(open(fp, encoding="utf-8")).get("records") or []:
                if r.get("tradeDate") == date:
                    m[str(r["code"]).zfill(6)] = {"auctionAmount": r.get("auctionAmount"),
                                                  "sealAmount": r.get("sealAmount")}
        except Exception:
            pass
    _AUC[date] = m
    return m


def buyable(code, date):
    """可买判定（L3/L5 共用）：非涨停开盘 → 可买；涨停/一字 → 竞价额≥封单额×10% 才算可挂到"""
    v, _why = executable(date, code)
    return v


def load_preplan():
    """返回 (hn_bull, hn_bear, lq_rec, hn_text)  —— 日期归属已修正 + 风标剔除 + bear 复核"""
    hn_bull, hn_bear = collections.defaultdict(set), collections.defaultdict(set)
    hn_text = {}
    plans_by_key = {}
    if os.path.isfile(PLANS_RAW):
        for line in open(PLANS_RAW, encoding="utf-8"):
            p = json.loads(line)
            plans_by_key[p.get("key")] = p
    if os.path.isfile(HN_FIX):
        for line in open(HN_FIX, encoding="utf-8"):
            x = json.loads(line)
            if x["polarity"] == "bull":
                hn_bull[x["day"]].add(x["code"])
                p = plans_by_key.get(x.get("src"))
                if p:
                    hn_text[(x["day"], x["code"])] = (p.get("text") or "").replace(chr(10), " ")
            elif x["polarity"] == "bear":
                hn_bear[x["day"]].add(x["code"])
    lq = {}
    if os.path.isfile(LQ_FIX):
        lq = json.load(open(LQ_FIX, encoding="utf-8"))
    return hn_bull, hn_bear, lq, hn_text


def layers_for(day, hn_bull, hn_bear, lq, hn_text=None):
    """四层：L1 红牛单群 / L2 红牛×来去题材趋势 / L3 红牛×来去看空(逆势) / L4 来去题材段"""
    A = hn_bull.get(day, set()) - hn_bear.get(day, set())
    v = lq.get(day) or {}
    lqb = {x["code"]: x for x in v.get("bull") or []}
    lqbear = {x["code"] for x in v.get("bear") or []}
    theme = {c for c, x in lqb.items() if x.get("section") in ("theme", "trend")}
    short = {c for c, x in lqb.items() if x.get("section") == "short"}
    short = {c for c, x in lqb.items() if x.get("section") == "short"}
    L1 = A
    L2 = A & theme
    L5 = A & short
    H = hn_text or {}
    L3_raw = A & lqbear
    L3 = {c for c in L3_raw if any(w in H.get((day, c), "") for w in REVERSE_WORDS)} if H else L3_raw
    shortbull = {c for c, x in lqb.items() if x.get("section") == "short"}
    L5 = A & shortbull - lqbear
    L4 = set(lqb) - lqbear
    return {"L1": L1, "L2": L2, "L3": L3, "L3_raw": L3_raw, "L4": L4, "L5": L5, "L5": L5,
            "L4_theme": theme & set(lqb), "L4_short": short,
            "lq_all": set(lqb)}


def layer_stats(hn_bull, hn_bear, lq, dates, didx, hn_text=None):
    """各层历史表现（首波去重，预案日开盘买入；L3/L5 另给可执行子集）"""
    buckets = collections.defaultdict(list)
    for d in sorted(set(hn_bull) | set(lq)):
        L = layers_for(d, hn_bull, hn_bear, lq, hn_text)
        for k in ("L1", "L2", "L3", "L5"):
            for c in L[k]:
                buckets[k].append((d, c))
        for c in L["L4_theme"]:
            buckets["L4_theme"].append((d, c))
        for c in L["L3"]:
            if executable(d, c)[0]:
                buckets["L3_exec"].append((d, c))
        for c in L["L5"]:
            if executable(d, c)[0]:
                buckets["L5_exec"].append((d, c))
    out = {}
    for k, sel in buckets.items():
        by = collections.defaultdict(list)
        for d, c in sel:
            by[c].append(d)
        eps = []
        for c, ds in by.items():
            ds.sort(key=lambda z: didx.get(z, 10 ** 9))
            cur = None
            for z in ds:
                if cur is None or didx.get(z, 0) - didx.get(cur[-1], 0) > 3:
                    if cur:
                        eps.append((cur[0], c))
                    cur = [z]
                else:
                    cur.append(z)
            if cur:
                eps.append((cur[0], c))
        st = {"n": len(eps)}
        if k in ("L3", "L5"):
            ok = sum(1 for d, c in eps if executable(d, c)[0])
            st["buyable_n"] = ok
            st["buyable_pct"] = round(100.0 * ok / len(eps), 1) if eps else None
        for h in (1, 3, 5):
            v = []
            for d, c in eps:
                sr = rv.series(c)
                if sr is None or d not in sr.pos:
                    continue
                i = sr.pos[d]
                if i + h - 1 >= len(sr.kl):
                    continue
                base = sr.kl[i]["open"]
                if not base:
                    continue
                v.append(round(100.0 * (sr.kl[i + h - 1]["close"] / base - 1), 2))
            st["d%d" % h] = gb.metrics(v) if v else {"n": 0}
            if h == 1 and v:
                st["win1"] = round(100.0 * sum(1 for x in v if x > 0) / len(v), 1)
        out[k] = st
    return out


def preplan(date="latest"):
    """四层预案扫描：L1 红牛 / L2 共振(题材趋势) / L3 逆势(红牛×来去看空) / L4 来去题材段"""
    sigs, dates, didx, mention, pos, meta = load_all()
    d = dates[-1] if date == "latest" else date
    hn_bull, hn_bear, lq, hn_text = load_preplan()
    n2c = {str(v).zfill(6): k for k, v in json.load(open(gs.STOCKDICT, encoding="utf-8")).items()}
    L = layers_for(d, hn_bull, hn_bear, lq, hn_text)
    ST = layer_stats(hn_bull, hn_bear, lq, dates, didx, hn_text)

    def fmt(codes):
        return "、".join("%s(%s)" % (n2c.get(c, "?"), c) for c in sorted(codes)) or "—"

    print("=== %s 预案四层扫描 ===" % d)
    print("【日期】数据最新交易日 %s" % dates[-1])
    titles = {"L1": "L1 红牛预案看多（主力）",
              "L2": "L2 红牛 × 来去题材/趋势段看多（共振加成）",
              "L3": "L3 红牛×来去空头风标（超跌反核）｜执行：开盘全仓买 + 盘中-5%止损 + D+1收绿清/收红卖1/3 + 每日新高续持 + D+3卖1/2 + D+5清",
              "L4_theme": "L4 来去【题材方面】段看多（独立题材源）",
              "L5": "L5 来去【短线方面】× 红牛（接力延续）｜执行：非一字，或竞价额≥封单10%"}
    rows = []
    TITLES_EXEC = {"L3_exec": "L3 可执行子集（已按一字/竞价规则过滤）",
                   "L5_exec": "L5 可执行子集（非一字 或 竞价额≥封单10%）"}
    titles.update(TITLES_EXEC)
    for k in ("L1", "L2", "L3", "L3_exec", "L5", "L5_exec", "L4_theme"):
        sel = L.get(k if k not in ("L3_exec", "L5_exec") else k.replace("_exec", ""), set())
        st = ST.get(k, {})
        d1 = st.get("d1", {})
        d3 = st.get("d3", {})
        extra = ""
        if st.get("buyable_pct") is not None:
            extra = "  可买 %s%%" % st.get("buyable_pct")
        print(chr(10) + "%s  (历史 n=%d%s, D+1 %s%%/%+.2f%%, D+3 %s%%/%+.2f%%)" % (
            titles[k], st.get("n", 0), extra, d1.get("winrate"), d1.get("avg") or 0,
            d3.get("winrate"), d3.get("avg") or 0))
        print("   " + fmt(sel))
        rows.append({"layer": k, "title": titles[k], "codes": sorted(sel),
                     "names": [n2c.get(c, "?") for c in sorted(sel)],
                     "hist": {h: st.get(h) for h in ("n", "d1", "d3", "d5")}})
    out = {"date": d, "layers": rows}
    p = os.path.join(ROOT, "reports", "预案四层扫描_%s.json" % d)
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(chr(10) + "wrote " + p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--scan", default="")
    ap.add_argument("--preplan", default="")
    a = ap.parse_args()
    if a.backtest:
        backtest(a.days)
    if a.scan:
        scan(a.scan)
    if a.preplan:
        preplan(a.preplan)
    if not a.backtest and not a.scan and not a.preplan:
        backtest(90)
