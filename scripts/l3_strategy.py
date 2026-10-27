# -*- coding: utf-8 -*-
"""L3 实盘策略回测 + 当日竞价排序

策略（用户 2026-10-27 定稿）
  候选：L3 = 红牛预案看多 ∩ 来去空头风标/看空 ∩ 红牛原文含反核词
  选票：当日多只候选 → 买【竞价涨幅最高】的一只
  入场：预案日开盘价（09:30 开盘买，满仓单票）
  止损：-5%（A股 T+1，最早 D+1 才能卖；盘中触及即按 -5% 成交估算）
  出场：D+1 收盘卖半仓 + 剩余半仓 D+2 收盘卖（最优变体）
  资金：单票满仓、复利；持仓期间出现的新信号跳过

用法
  NO_NET=1 python scripts/l3_strategy.py --backtest
  NO_NET=1 python scripts/l3_strategy.py --quotes   # 今日候选 + 竞价涨幅排序（需 09:25 后）
"""
import argparse
import collections
import json
import os
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preplan_resonance as pp          # noqa: E402
import resonance_v2 as rv               # noqa: E402
import group_backtest as gb             # noqa: E402

gb.fetch_kline = lambda *a, **k: []

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "laiqiu", "backtest", "l3_strategy.json")
REVERSE_WORDS = ("反核", "次弱", "地板", "跌停开", "低吸", "反包", "低位")


def load_all():
    dates, didx = pp.load_cal()
    raw = json.load(open(os.path.join(ROOT, "data", "laiqiu", "stockdict_ak.json"), encoding="utf-8"))
    n2c = {str(v).zfill(6): k for k, v in raw.items()}
    plans = {}
    fp = os.path.join(ROOT, "data", "feishu", "hongniu", "plans.jsonl")
    for line in open(fp, encoding="utf-8"):
        p = json.loads(line)
        plans[p["key"]] = p
    hn, hnb, htxt = collections.defaultdict(set), collections.defaultdict(set), {}
    fx = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl")
    for line in open(fx, encoding="utf-8"):
        x = json.loads(line)
        if x["polarity"] == "bull":
            hn[x["day"]].add(x["code"])
            p = plans.get(x.get("src"))
            if p:
                htxt[(x["day"], x["code"])] = (p.get("text") or "").replace("\n", " ")
        elif x["polarity"] == "bear":
            hnb[x["day"]].add(x["code"])
    lq = json.load(open(os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "fixed.json"), encoding="utf-8"))
    return dates, didx, n2c, hn, hnb, htxt, lq


def candidates(n2c, hn, hnb, htxt, lq):
    """{(date): [(code, name, 红牛snippet, 来去snippet)]}"""
    out = collections.defaultdict(list)
    for d, v in lq.items():
        A = hn.get(d, set()) - hnb.get(d, set())
        bear = {x["code"] for x in v.get("bear") or []} | set(v.get("fengkiao") or [])
        for c in A & bear:
            snip = htxt.get((d, c), "")
            if not any(w in snip for w in REVERSE_WORDS):
                continue
            s = rv.series(c)
            if s is None or d not in s.pos:
                continue
            i = s.pos[d]
            if i == 0 or i + 1 >= len(s.kl):
                continue
            lqsnip = ""
            for x in v.get("bear") or []:
                if x["code"] == c:
                    lqsnip = x.get("snippet") or ""
            out[d].append({"code": c, "name": n2c.get(c, "?"),
                           "gap": round(100 * (s.kl[i]["open"] / s.kl[i - 1]["close"] - 1), 2),
                           "hn": snip[:60], "lq": lqsnip[:40]})
    return out


def backtest(stop=0.05, half=True, remain_offset=1, capital=10000.0):
    dates, didx, n2c, hn, hnb, htxt, lq = load_all()
    cand = candidates(n2c, hn, hnb, htxt, lq)
    cap = capital
    hold = -1
    trades = []
    for d in sorted(cand):
        lst = sorted(cand[d], key=lambda x: -x["gap"])
        if not lst or didx[d] <= hold:
            continue
        pick = lst[0]
        c = pick["code"]
        s = rv.series(c)
        i = s.pos[d]
        cost = s.kl[i]["open"]
        j = i + 1
        if s.kl[j]["low"] <= cost * (1 - stop):
            cap *= (1 - stop)
            hold = didx[s.dates[j]]
            trades.append({"date": d, "code": c, "name": pick["name"], "gap": pick["gap"],
                           "ret": round(-stop * 100, 2), "capital": round(cap),
                           "exit": "D+1 止损(全仓)"})
            continue
        if not half:
            mult = s.kl[j]["close"] / cost
            k = j
            exit_why = "D+1 收盘全出"
        else:
            legA = 0.5 * (s.kl[j]["close"] / cost)
            k = min(j + remain_offset, len(s.kl) - 1)
            if s.kl[k]["low"] <= cost * (1 - stop):
                legB = 0.5 * (1 - stop)
                exit_why = "半仓 D+1 + 半仓止损"
            else:
                legB = 0.5 * (s.kl[k]["close"] / cost)
                exit_why = "半仓 D+1 + 半仓 D+%d" % (remain_offset + 1)
            mult = legA + legB
        cap *= mult
        hold = didx[s.dates[k]]
        trades.append({"date": d, "code": c, "name": pick["name"], "gap": pick["gap"],
                       "ret": round((mult - 1) * 100, 2), "capital": round(cap), "exit": exit_why})
    peak = capital
    mdd = 0
    for t in trades:
        peak = max(peak, t["capital"])
        mdd = min(mdd, t["capital"] / peak - 1)
    wins = sum(1 for t in trades if t["ret"] > 0)
    res = {"params": {"stop": stop, "half": half, "remain_offset": remain_offset,
                      "capital": capital},
           "n_trades": len(trades), "final": round(cap, 0),
           "return_pct": round((cap / capital - 1) * 100, 1),
           "winrate": round(100 * wins / len(trades), 1) if trades else 0,
           "max_drawdown": round(mdd * 100, 1),
           "best": max((t["ret"] for t in trades), default=0),
           "worst": min((t["ret"] for t in trades), default=0),
           "trades": trades}
    return res


def quotes(codes):
    """取实时/收盘行情：竞价撮合价 f46 与昨收 f60（东财接口，字段需 /100）"""
    out = {}
    for c in codes:
        secid = ("1." if str(c).startswith(("6", "9")) else ("0." if str(c).startswith(("0", "3")) else "0.")) + str(c)
        url = ("https://push2.eastmoney.com/api/qt/stock/get?secid=%s&fields=f43,f46,f60,f57,f58" % secid)
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8")).get("data") or {}
            op, prev = d.get("f46"), d.get("f60")
            if op and op > 0 and prev and prev > 0:
                out[str(c)] = {"name": d.get("f58"), "open": op / 100.0,
                               "prev": prev / 100.0,
                               "gap": round(100 * (op / prev - 1), 2)}
        except Exception:
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--quotes", action="store_true")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    if a.quotes:
        dates, didx, n2c, hn, hnb, htxt, lq = load_all()
        d = a.date or dates[-1]
        lst = candidates(n2c, hn, hnb, htxt, lq).get(d, [])
        if not lst:
            print("%s 无 L3 候选" % d)
            return
        q = quotes([x["code"] for x in lst])
        for x in lst:
            x.update(q.get(x["code"], {}))
        lst.sort(key=lambda x: -(x.get("gap") if x.get("gap") is not None else -999))
        print("%s L3 候选（按竞价涨幅排序）：" % d)
        for i, x in enumerate(lst, 1):
            print("  %d. %s(%s) 竞价%+.2f%% ｜红牛：%s ｜来去：%s"
                  % (i, x["name"], x["code"], x["gap"], x["hn"][:40], x["lq"][:30]))
        if lst:
            print("\n→ 按规则买：%s(%s)（竞价涨幅最高）" % (lst[0]["name"], lst[0]["code"]))
        return
    r = backtest()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(r, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("L3 策略回测（止损%.0f%%，半仓D+1 + 半仓D+%d）" % (r["params"]["stop"] * 100,
                                                    r["params"]["remain_offset"] + 1))
    print("  成交 %d 笔｜期末 %.0f 元｜收益 %+.1f%%｜胜率 %.1f%%｜最大回撤 %.1f%%｜最好 %+.1f%%｜最差 %+.1f%%"
          % (r["n_trades"], r["final"], r["return_pct"], r["winrate"], r["max_drawdown"],
             r["best"], r["worst"]))
    print("  → %s" % OUT)


if __name__ == "__main__":
    main()
