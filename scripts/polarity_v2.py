#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""预案极性判定 v2（句子级 + 否决优先 + 否定处理 + 权重）

v1（hongniu_plan_backtest.polarity）的问题：
  1. ±60 字对称窗口跨句、跨票 → 上一只票的"打底"会算到下一只票头上
  2. 只数词频不看优先级 → "断板兑现为主"里只要同窗口出现"强锁/转强"就判成看多
  3. 无否定处理 → "不能转强"里的"转强"仍计正分
  4. bull 词表 29 个 vs bear 21 个，且 bull 含描述性名词（空间/溢价/新高）→ 结构性偏向看多

v2 规则：
  A. 先按 。；;\n 与 "1. 2. 3." 枚举切成 clause（句子）
  B. 每只票只看「它所在 clause 内、名字之后、下一只票名之前」的片段（最多 60 字）
  C. 否决优先：片段含 NEG（断板/兑现/补跌/走弱/清仓…）且无明确买入动作 → bear
  D. 否定处理："不/没/未/难/否则/不建议" + 正词 → 该正词作废
  E. 权重：动作词 2 分（低吸/买点/加仓/打底/埋伏/接回/强锁），描述词 1 分（反包/修复/转强/空间…）
  F. 条件句降权：含 如果/若/除非/否则/不然 → 总分 ×0.5
  G. "先手为主"从两侧词表移除（语义是"提前买"，不是看空也不是明确看多 → neutral）

用法：
  python scripts/polarity_v2.py --rebuild          # 重建 data/feishu/hongniu/plans_v2.jsonl
  python scripts/polarity_v2.py --diff 40          # 打印 v1/v2 分歧样本（人工核对）
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANS = os.path.join(ROOT, "data", "feishu", "hongniu", "plans.jsonl")
V1 = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl")
V2 = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_v2.jsonl")

# 动作词（权重 2）：直接表达"要买"
POS_ACTION = ["低吸", "买点", "买入", "打底", "埋伏", "加仓", "接回", "强锁", "首版买点",
              "恐慌吸", "水下吸", "半路", "头铁买点", "分歧买点", "深水捞"]
# 描述词（权重 1）：表达"看强/看修复"，但不等于买
POS_DESC = ["反包", "修复", "转强", "套利", "空间", "新高", "补涨", "卡异动", "反核",
            "换手板", "溢价", "止跌", "预期差", "跟踪", "走强", "关注", "博弈", "涨停",
            "缩量板", "一字", "封板", "晋级"]
# 否决词（权重 3）：明确看空/出局
NEG = ["断板", "兑现", "补跌", "减仓", "清仓", "走弱", "杀跌", "不接", "回避", "放弃",
       "不参与", "退潮", "出局", "走人", "止损", "出货", "砸盘", "走完", "停牌",
       "崩", "天地板", "跌停"]
NEG_SOFT = ["先手为主", "观察", "看戏", "慎", "风险"]        # 软看空（不否决，只减分）
NEGATION = ["不", "没", "未", "难", "别", "勿", "无法", "不建议", "不宜", "不会", "不能"]
COND = ["如果", "若", "除非", "否则", "不然", "假如", "一旦", "要是"]

for _l in (POS_ACTION, POS_DESC, NEG, NEG_SOFT, NEGATION, COND):
    _l.sort(key=len, reverse=True)

CLAUSE_SPLIT = re.compile(r"[\n。；;！!？?]+|(?<![0-9])[0-9]{1,2}[.、)．](?=[\u4e00-\u9fff])")
WIN_FWD = 60
CUT_AT_NEXT = os.environ.get("POL_CUT_NEXT") == "1"   # 是否在下一只票名前截断（默认否）


def clauses(text):
    """切句，返回 [(start, end)]，覆盖全文"""
    spans, prev = [], 0
    for m in CLAUSE_SPLIT.finditer(text):
        if m.start() > prev:
            spans.append((prev, m.start()))
        prev = m.end()
    if prev < len(text):
        spans.append((prev, len(text)))
    return spans or [(0, len(text))]


def _negated(frag, pos, word):
    """word 在 frag[pos:] 处是否被前 5 字的否定词修饰"""
    left = frag[max(0, pos - 5):pos]
    return any(n in left for n in NEGATION)


def score(frag):
    """返回 (pos_score, neg_score, hits_pos, hits_neg)"""
    p = 0
    hp = []
    for w in POS_ACTION:
        for m in re.finditer(re.escape(w), frag):
            if _negated(frag, m.start(), w):
                continue
            p += 2
            hp.append(w)
            break
    for w in POS_DESC:
        for m in re.finditer(re.escape(w), frag):
            if _negated(frag, m.start(), w):
                continue
            p += 1
            hp.append(w)
            break
    n = 0
    hn = []
    for w in NEG:
        if w in frag:
            n += 3
            hn.append(w)
    for w in NEG_SOFT:
        if w in frag:
            n += 1
            hn.append(w)
    if any(c in frag for c in COND):
        p = p * 0.5
        n = n * 0.5
    return p, n, hp, hn


def judge(frag):
    p, n, hp, hn = score(frag)
    if n >= 3 and not any(w in hp for w in POS_ACTION):
        return "bear", p, n, hp, hn              # 否决优先
    if p > n:
        return "bull", p, n, hp, hn
    if n > p:
        return "bear", p, n, hp, hn
    return "neutral", p, n, hp, hn


def judge_record(text, stocks):
    """对一条预案（含多只票）批量判极性，clauses 只算一次"""
    import bisect
    cls = clauses(text)
    starts = [c[0] for c in cls]
    occ = {}
    for st in stocks:
        nm = st.get("alias") or st.get("name")
        occ.setdefault(nm, []).extend(m.start() for m in re.finditer(re.escape(nm), text))
    allpos = sorted(q for v in occ.values() for q in v)
    res = {}
    for st in stocks:
        nm = st.get("alias") or st.get("name")
        code = st["code"]
        best = None
        for p in occ.get(nm, []):
            ci = max(0, bisect.bisect_right(starts, p) - 1)
            cs, ce = cls[ci] if ci < len(cls) else (p, len(text))
            end = min(p + len(nm) + WIN_FWD, ce)
            if CUT_AT_NEXT:
                nxt = [q for q in allpos if p + len(nm) <= q < end]
                if nxt and min(nxt) - (p + len(nm)) >= 6:
                    end = min(nxt)
            frag = text[p + len(nm):end]
            pol, ps, ns, hp, hn = judge(frag)
            snip = text[max(0, p - 4):min(end, p + len(nm) + 40)].replace(chr(10), " ")
            if best is None or (ps - ns) > (best[1] - best[2]):
                best = (pol, ps, ns, hp, hn, snip)
        if best:
            res[code] = best
    return res


def build(verbose=False):
    """重建 v2 信号：只用有 text 的记录；纯图片记录沿用 v1 判定并标 src=img"""
    pass
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import group_signals as gs

    n2c, keys, name_of, short2 = gs.build_dict()
    recs = [json.loads(l) for l in open(PLANS, encoding="utf-8") if l.strip()]
    v1 = {}
    for l in open(V1, encoding="utf-8"):
        x = json.loads(l)
        v1[(x["day"], x["code"])] = x

    out = []
    seen = set()
    stat = Counter()
    for r in recs:
        text = r.get("text") or ""
        stocks = r.get("stocks") or []
        if not text or not stocks:
            for st in stocks:
                k = (r["day"], st["code"])
                if k in seen:
                    continue
                seen.add(k)
                o = v1.get(k)
                if o:
                    out.append(dict(o, src="img", v="v1-img"))
                    stat["v1-img"] += 1
            continue
        judged = judge_record(text, stocks)
        for st in stocks:
            nm = st.get("alias") or st.get("name")
            code = st["code"]
            k = (r["day"], code)
            res = judged.get(code)
            if not res:
                continue
            pol, ps, ns, hp, hn, snip = res
            stat[pol] += 1
            old = v1.get(k)
            if old and old["polarity"] == "bull" and pol != "bull":
                stat["v1bull->" + pol] += 1
            if old and old["polarity"] != "bull" and pol == "bull":
                stat["v1%s->bull" % old["polarity"]] += 1
            out.append({"day": r["day"], "time": r.get("time", ""), "code": code,
                        "name": st.get("name") or nm, "polarity": pol,
                        "pos": ps, "neg": ns, "hits_pos": hp, "hits_neg": hn,
                        "snippet": snip, "src": r.get("key", ""), "v": "v2"})
    # 兜底：v1 有、v2 没有的（原文里该票不在 stocks 列表）→ 原样带过来
    have = {(x["day"], x["code"]) for x in out}
    for k, o in v1.items():
        if k in have:
            continue
        out.append(dict(o, v="v1-carry", src=o.get("src", "")))
        stat["v1-carry"] += 1
    # 去重：同 (day, code, polarity)
    uniq, seen2 = [], set()
    for x in out:
        k = (x["day"], x["code"], x["polarity"])
        if k in seen2:
            continue
        seen2.add(k)
        uniq.append(x)
    os.makedirs(os.path.dirname(V2), exist_ok=True)
    with open(V2, "w", encoding="utf-8") as f:
        for x in uniq:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print("写出 %s：%d 条" % (V2, len(uniq)))
    print("方向分布：", dict(Counter(x["polarity"] for x in uniq)))
    print("v1→v2 变化：", dict(stat))
    return uniq


def diff_sample(n=40, only_flips=True):
    """打印 v1/v2 分歧样本，供人工核对"""
    v2 = [json.loads(l) for l in open(V2, encoding="utf-8")] if os.path.exists(V2) else []
    v1 = {}
    for l in open(V1, encoding="utf-8"):
        x = json.loads(l)
        v1[(x["day"], x["code"])] = x["polarity"]
    rows = []
    for x in v2:
        if x.get("v") != "v2":
            continue
        k = (x["day"], x["code"])
        if k not in v1:
            continue
        if v1[k] != x["polarity"]:
            rows.append((k, v1[k], x))
    rows.sort(key=lambda z: z[0])
    print("分歧 %d 条（v1 与 v2 判定不同）\n" % len(rows))
    for (d, c), p1, x in rows[:n]:
        print("%s %s %-8s v1=%-7s v2=%-7s pos=%.1f neg=%.1f | %s"
              % (d, c, x["name"], p1, x["polarity"], x["pos"], x["neg"],
                 (x.get("snippet") or "")[:64]))
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--diff", type=int, default=0)
    a = ap.parse_args()
    if a.rebuild:
        build()
    if a.diff:
        diff_sample(a.diff)
