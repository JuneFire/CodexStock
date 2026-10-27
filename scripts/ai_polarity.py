#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用 AI（DeepSeek）判定预案里每只被点名股票的方向，替代/校验词表法。

输出：data/feishu/hongniu/plans_ai.jsonl（字段与 plans_fixed.jsonl 兼容，可直接喂 l1_strategy.py）

用法：
  AI_API_KEY=sk-xxx python scripts/ai_polarity.py --run        # 全量判定（带缓存，可断点续跑）
  AI_API_KEY=sk-xxx python scripts/ai_polarity.py --run --limit 60
  python scripts/ai_polarity.py --stat                          # 看缓存与统计
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PLANS = os.path.join(ROOT, "data", "feishu", "hongniu", "plans.jsonl")
V1 = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl")
V2 = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_v2.jsonl")
CACHE = os.path.join(ROOT, "data", "feishu", "hongniu", "ai_polarity_cache.json")
OUT = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_ai.jsonl")

import ai_client          # noqa: E402
from polarity_v2 import clauses, WIN_FWD   # noqa: E402

SYS = ("你是A股短线预案文本解析器。给出一批「片段 + 目标股票」，判断该股票在这段文字里的方向。"
       "只输出 JSON 数组，不要解释。\n"
       "bull = 明确看多/建议参与（低吸、买点、打底、埋伏、加仓、接回、可做、强锁买点等动作）\n"
       "bear = 明确看空/提示出局（断板、兑现、补跌、走弱、减仓、清仓、走人、不参与、回避等）\n"
       "neutral = 仅提及/条件句未定/纯描述（如图形、位置、分歧、情绪、观察）\n"
       "注意：条件句（如果…则…/不符合就…）里只有条件明确时才判方向，否则 neutral；"
       "「先手为主」「观察」「看戏」一律 neutral。")


def fragments():
    """构造 (day, code, name, frag) —— clause 内、名字之后最多 60 字"""
    recs = [json.loads(l) for l in open(PLANS, encoding="utf-8") if l.strip()]
    out, seen = [], set()
    for r in recs:
        text = r.get("text") or ""
        stocks = r.get("stocks") or []
        if not text or not stocks:
            continue
        cls = clauses(text)
        starts = [c[0] for c in cls]
        import bisect
        for st in stocks:
            nm = st.get("alias") or st.get("name")
            for m in re.finditer(re.escape(nm), text):
                p = m.start()
                ci = max(0, bisect.bisect_right(starts, p) - 1)
                cs, ce = cls[ci]
                end = min(p + len(nm) + WIN_FWD, ce)
                frag = text[p:end].replace("\n", " ").strip()
                if len(frag) < len(nm) + 2:
                    continue
                k = (r["day"], st["code"], frag[:40])
                if k in seen:
                    continue
                seen.add(k)
                out.append((r["day"], r["code"] if "code" in r else st["code"],
                            st.get("name") or nm, frag, r.get("time", ""), r.get("key", "")))
    return out


def load_cache():
    if os.path.exists(CACHE):
        return json.load(open(CACHE, encoding="utf-8"))
    return {}


def save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(c, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)


def run(limit=0, batch=16):
    frs = fragments()
    cache = load_cache()
    todo = [f for f in frs if f[3] not in cache]
    print("片段 %d 条｜已缓存 %d｜待判 %d" % (len(frs), len(frs) - len(todo), len(todo)))
    if limit:
        todo = todo[:limit]
    n_call = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i: i + batch]
        payload = {"数量": len(chunk), "片段": [f[3] for f in chunk]}
        res, err = ai_client.chat_json(SYS, json.dumps(payload, ensure_ascii=False), max_tokens=900)
        if err or res is None:
            print("AI_FAIL %s" % (err or "empty")[:120])
            continue
        n_call += 1
        # 解析：兼容 {"r":[...]} / {"data":[{"片段":..,"方向":..}]} / 直接 list
        def _pick(res):
            if isinstance(res, list):
                return res
            if isinstance(res, dict):
                for k in ("r", "结果", "data", "方向", "批次", "items", "res"):
                    v = res.get(k)
                    if isinstance(v, list):
                        return v
            return None

        arr = _pick(res)
        if arr is None:
            print("AI_SHAPE %s" % str(res)[:100])
            continue
        # 情况1：元素是字符串，按顺序对齐
        if all(isinstance(it, str) for it in arr):
            for f, it in zip(chunk, arr):
                v = it.strip().lower()
                if v in ("bull", "bear", "neutral"):
                    cache[f[3]] = v
        else:
            # 情况2：元素是 dict，优先按「片段」文本匹配
            for it in arr:
                if not isinstance(it, dict):
                    continue
                v = None
                for _k, val in it.items():
                    if isinstance(val, str) and val.strip().lower() in ("bull", "bear", "neutral"):
                        v = val.strip().lower()
                        break
                if v is None:
                    continue
                frag_key = it.get("片段") or it.get("frag") or it.get("text")
                tgt = None
                if frag_key:
                    tgt = next((f for f in chunk if f[3][:20] in frag_key or frag_key[:20] in f[3]), None)
                if tgt is None:
                    idx = arr.index(it)
                    tgt = chunk[idx] if idx < len(chunk) else None
                if tgt:
                    cache[tgt[3]] = v
        if n_call % 10 == 0:
            save_cache(cache)
            print("  已判 %d/%d" % (min(i + batch, len(todo)), len(todo)))
    save_cache(cache)

    # 输出 plans_ai.jsonl：同一 (day,code) 多片段取「最强」判定（bull > bear > neutral 的共识：bull/bear 任一优先）
    agg = {}
    for day, code, name, frag, tm, key in frs:
        v = cache.get(frag)
        if not v:
            continue
        k = (day, code)
        cur = agg.get(k)
        rank = {"bull": 2, "bear": 2, "neutral": 1}
        if cur is None or (rank[v] > rank[cur[0]] and v != "neutral"):
            agg[k] = (v, name, frag, tm, key)
    # v1 有、AI 没有的（纯图片记录）→ 原样带过来
    v1recs = {}
    for l in open(V1, encoding="utf-8"):
        x = json.loads(l)
        v1recs.setdefault((x["day"], x["code"]), x)
    rows = []
    for k, (v, name, frag, tm, key) in agg.items():
        rows.append({"day": k[0], "time": tm, "code": k[1], "name": name,
                     "polarity": v, "snippet": frag, "src": key, "v": "ai"})
    have = {k for k in agg}
    for k, x in v1recs.items():
        if k in have:
            continue
        y = dict(x)
        y["v"] = "v1-fallback"
        rows.append(y)
    rows.sort(key=lambda z: (z["day"], z["code"]))
    with open(OUT, "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print("写出 %s：%d 条" % (OUT, len(rows)))
    print("方向分布：", dict(Counter(x["polarity"] for x in rows if x.get("v") == "ai")))


def stat():
    cache = load_cache()
    print("缓存 %d 条：%s" % (len(cache), dict(Counter(cache.values()))))
    if os.path.exists(OUT):
        rows = [json.loads(l) for l in open(OUT, encoding="utf-8")]
        print("plans_ai.jsonl %d 条：%s" % (len(rows), dict(Counter(x["polarity"] for x in rows))))
        # 与 v1/v2 对比
        for name, path in (("v1", V1), ("v2", V2)):
            if not os.path.exists(path):
                continue
            m = {}
            for l in open(path, encoding="utf-8"):
                x = json.loads(l)
                m[(x["day"], x["code"])] = x["polarity"]
            agree = tot = 0
            flips = Counter()
            for x in rows:
                if x.get("v") != "ai":
                    continue
                k = (x["day"], x["code"])
                if k in m:
                    tot += 1
                    if m[k] == x["polarity"]:
                        agree += 1
                    else:
                        flips["%s→%s" % (m[k], x["polarity"])] += 1
            if tot:
                print("  与 %s 一致率 %.1f%%（%d/%d）｜主要分歧 %s"
                      % (name, 100.0 * agree / tot, agree, tot, flips.most_common(4)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--stat", action="store_true")
    a = ap.parse_args()
    if not ai_client.available():
        print("AI_API_KEY 未配置")
        sys.exit(1)
    if a.run:
        run(a.limit, a.batch)
    if a.stat:
        stat()
