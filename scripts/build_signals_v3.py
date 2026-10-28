#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用「线上口径」重建历史信号（v3）：OCR 空格清洗 + AI 判定所有被点名的票。

背景
  回测一直用 plans_fixed.jsonl（词表法，±60 字窗口）。今天线上改了两件事：
    1) 汉字间空格清洗（OCR 常把「桂发祥」切成「桂发 祥」→ 名字漏匹配）
    2) AI 判定「所有被点名的票」，不再只复核规则判多的
  不把这两条用到历史数据上，回测样本就仍是旧口径（会系统性漏票）。

输出
  data/feishu/hongniu/plans_v3.jsonl   （字段与 plans_fixed.jsonl 兼容）

用法
  AI_API_KEY=sk-xxx python scripts/build_signals_v3.py --run
  python scripts/build_signals_v3.py --stat
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
V3 = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_v3.jsonl")
CACHE = os.path.join(ROOT, "data", "feishu", "hongniu", "ai_v3_cache.json")

import ai_client          # noqa: E402
import group_signals as gs   # noqa: E402
from polarity_v2 import clauses, WIN_FWD   # noqa: E402

CJK_GAP = re.compile(r"(?<=[\u4e00-\u9fff])[ \t\u3000]+(?=[\u4e00-\u9fff])")

SYS = ("你是A股短线预案解析器。输入一批片段，**每个片段开头的那个股票名就是目标股票**，判断它的方向。"
       "输出 JSON：{\"r\":[\"bull\"/\"bear\"/\"neutral\", ...]}，长度与顺序一一对应，不要解释。\n"
       "bull=明确看多/建议参与（低吸/买点/打底/埋伏/加仓/接回/强锁买点/反核低吸）\n"
       "bear=明确看空或提示出局（断板/兑现/补跌/走弱/减仓/清仓/走人/保本走掉/核按钮/不参与/回避）\n"
       "neutral=仅提及/条件句未定/纯描述；「先手为主」「观察」「看戏」一律 neutral")


def norm(text):
    t = gs.clean(text or "")
    for _ in range(3):
        t2 = CJK_GAP.sub("", t)
        if t2 == t:
            break
        t = t2
    return t


CJK = re.compile(r"[一-鿿]")


def _alias_ok(text, pos, ln):
    """只有 2 字简称才做边界检查（全名出现在中文句子里是正常的）"""
    if ln > 2:
        return True
    end = pos + ln
    if end < len(text) and CJK.match(text[end]):
        return False
    if pos > 0 and CJK.match(text[pos - 1]):
        return False
    return True


def mentions_of(text, n2c, keys, window=60):
    """所有被点名的票：{code: (name, snippet)}"""
    pos = []
    for nm in keys:
        if nm not in text:
            continue
        for m in re.finditer(re.escape(nm), text):
            if not _alias_ok(text, m.start(), len(nm)):
                continue
            pos.append((m.start(), len(nm), nm))
    pos.sort()
    out = {}
    for i, (p, ln, nm) in enumerate(pos):
        code = n2c.get(nm)
        if not code:
            continue
        end = min(p + ln + window, len(text))
        nxt = [q for q, _l, _n in pos[i + 1:] if q > p + ln]
        if nxt and min(nxt) - (p + ln) >= 6:
            end = min(end, min(nxt))
        snip = text[p:end].strip()
        if len(snip) < len(nm) + 2:
            continue
        old = out.get(code)
        if old is None or len(snip) > len(old[1]):
            out[code] = (nm, snip[:140])
    return out


def load_cache():
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        return {}


def build(limit=0, batch=16):
    n2c, keys, name_of, short2 = gs.build_dict()
    for a, c in (short2 or {}).items():
        n2c.setdefault(a, c)
    recs = [json.loads(l) for l in open(PLANS, encoding="utf-8") if l.strip()]
    allm = {}          # (day, code) -> (name, snippet)
    for r in recs:
        text = norm(r.get("text") or "")
        if not text:
            continue
        for code, (nm, snip) in mentions_of(text, n2c, keys).items():
            k = (r["day"], code)
            if k not in allm or len(snip) > len(allm[k][1]):
                allm[k] = (nm, snip)
    print("历史预案里共点名 %d 个 (日期,股票) 组合" % len(allm))

    cache = load_cache()
    todo = [(k, v) for k, v in allm.items() if v[1] not in cache]
    print("AI 缓存 %d｜待判 %d" % (len(allm) - len(todo), len(todo)))
    if limit:
        todo = todo[:limit]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        payload = {"数量": len(chunk), "片段": [c[1][1] for c in chunk]}
        out, err = ai_client.chat_json(SYS, json.dumps(payload, ensure_ascii=False),
                                       max_tokens=900)
        if err or out is None:
            print("AI_FAIL %s" % (err or "empty")[:100])
            continue
        arr = None
        if isinstance(out, dict):
            for kk in ("r", "结果", "data", "items"):
                if isinstance(out.get(kk), list):
                    arr = out[kk]
                    break
        elif isinstance(out, list):
            arr = out
        if not arr:
            continue
        for c, v in zip(chunk, arr):
            v = str(v).strip().lower() if isinstance(v, str) else ""
            if v in ("bull", "bear", "neutral"):
                cache[c[1][1]] = v
        if (i // batch) % 5 == 0:
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
            print("  进度 %d/%d" % (min(i + batch, len(todo)), len(todo)))
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

    rows, miss = [], 0
    for (day, code), (nm, snip) in sorted(allm.items()):
        v = cache.get(snip)
        if not v:
            miss += 1
            continue
        rows.append({"day": day, "time": "", "code": code, "name": name_of.get(code, nm),
                     "polarity": v, "snippet": snip[:140], "src": "v3"})
    # 兜底：纯图片日（plans.jsonl 无 text）沿用 v1
    have = {(r["day"], r["code"]) for r in rows}
    for line in open(V1, encoding="utf-8"):
        x = json.loads(line)
        k = (x["day"], x["code"])
        if k in have:
            continue
        y = dict(x)
        y["v"] = "v1-fallback"
        rows.append(y)
    rows.sort(key=lambda z: (z["day"], z["code"]))
    with open(V3, "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print("写出 %s：%d 条（未判成功 %d）" % (V3, len(rows), miss))
    print("方向分布：", dict(Counter(x["polarity"] for x in rows if x.get("src") == "v3")))


def stat():
    if not os.path.exists(V3):
        print("还没有 %s" % V3)
        return
    v3 = [json.loads(l) for l in open(V3, encoding="utf-8")]
    v1 = {(json.loads(l)["day"], json.loads(l)["code"]): json.loads(l)["polarity"]
          for l in open(V1, encoding="utf-8")}
    print("v3 %d 条：%s" % (len(v3), dict(Counter(x["polarity"] for x in v3))))
    bull3 = {(x["day"], x["code"]) for x in v3 if x["polarity"] == "bull"}
    bull1 = {k for k, v in v1.items() if v == "bull"}
    print("v1 bull %d｜v3 bull %d｜共有 %d｜仅 v1 有 %d｜仅 v3 有 %d"
          % (len(bull1), len(bull3), len(bull1 & bull3), len(bull1 - bull3), len(bull3 - bull1)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--stat", action="store_true")
    a = ap.parse_args()
    if a.run:
        if not ai_client.available():
            print("AI_API_KEY 未配置")
            sys.exit(1)
        build(a.limit, a.batch)
    if a.stat:
        stat()
