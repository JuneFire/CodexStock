# -*- coding: utf-8 -*-
"""预案重建（修正版）：日期归属 + 极性复核

修两个问题
  1) 同日两份预案：开盘前发的是「当日预案」，收盘后发的是「次日预案」→ 按标题日期(M.D策略)优先，
     其次 时间<15:00→当日 / ≥15:00→下一交易日。
  2) 极性误判：来去由心「多头/空头风标」只是情绪温度计（原文自己写明"不代表涨跌方向"）→ 整段剔除；
     并对两边的名单做邻近窗口极性复核，"走人/卖点/断板/回避/放量出货"等词优先判空。

输出
  data/feishu/hongniu/plans_fixed.jsonl   {day, time, code, name, polarity, snippet}
  data/laiqiu/plans_ocr/fixed.json        {date: {bull:[{code,name}], bear:[...], fengkiao:[...]}}
"""
import collections
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HN_PLANS = os.path.join(ROOT, "data", "feishu", "hongniu", "plans.jsonl")
HN_OUT = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl")
LQ_OCR = os.path.join(ROOT, "data", "laiqiu", "ocr")
LQ_PLANS = os.path.join(ROOT, "data", "laiqiu", "plans_ocr")
LQ_OUT = os.path.join(LQ_PLANS, "fixed.json")
STOCKDICT = os.path.join(ROOT, "data", "laiqiu", "stockdict_ak.json")
HN_WR = os.path.join(ROOT, "data", "feishu", "hongniu", "plan_winrate.json")
SELL_PHRASES = ("走人即可", "只存在卖点", "只存在卖", "冲高走人", "走人", "卖点")

# 来去由心预案的段落切分：红牛是短线选手，只该跟「短线方面」共振
SECTIONS = {"短线方面": "short", "题材方面": "theme", "趋势票": "trend",
            "其他方向": "other", "其他对流": "other", "总结": "end"}
CAL = os.path.join(ROOT, "data", "laiqiu", "kline", "sh000001_idx.json")

BEAR = ["走人即可", "走人", "卖点", "止盈", "兑现", "回避", "断板", "放弃", "不参与",
        "冲高走", "减仓", "出局", "落袋", "不追", "放人", "高抛", "清仓", "无持续性",
        "只存在卖", "只存在卖点", "卡位失败", "被卡", "跑路", "走弱"]
BULL = ["低吸", "买入", "参与", "上车", "加仓", "一字", "卡位", "空间", "反包", "套利",
        "溢价", "仓位", "做T", "隔夜", "新高", "补涨", "卡异动", "先手", "强锁", "弹性",
        "晋级", "顶一", "给到仓位", "存在空间", "可关注", "关注趋势", "预期", "冲高",
        "板", "顶", "尝试", "可以做", "套利空间", "新高预期", "上限", "先手为主"]
BEAR.sort(key=len, reverse=True)
BULL.sort(key=len, reverse=True)

DEF_FENG = ("风标", "不代表标的", "多头较弱", "多头、空头")


def trading_days():
    kl = json.load(open(CAL, encoding="utf-8"))
    ds = [r["date"] for r in kl]
    return ds, {d: i for i, d in enumerate(ds)}


def next_day(ds, d):
    i = None
    for k, x in enumerate(ds):
        if x > d:
            return x
    return None


def day_index(ds, d):
    for k, x in enumerate(ds):
        if x >= d:
            return x
    return None


def polarity_in(text, name_aliases, win=40, forward=False):
    """极性判定：bear 优先。

    forward=True 只看「名字之后」28 字（中文主谓宾顺序），并跳过「…的新华文轩」
    这种作宾语的位置，避免把别人的动作算到它头上。
    """
    best = ("neutral", "")
    for a in name_aliases:
        for m in re.finditer(re.escape(a), text):
            if forward:
                if m.start() > 0 and text[m.start() - 1] in "的之":
                    continue
                ctx = text[m.end(): m.end() + 28]
            else:
                ctx = text[max(0, m.start() - win): m.end() + win]
            nb = [w for w in BEAR if w in ctx]
            nu = [w for w in BULL if w in ctx]
            snip = text[max(0, m.start() - 6): m.end() + 26].replace(chr(10), " ")[:80]
            if nb:
                return "bear", snip
            if nu and best[0] == "neutral":
                best = ("bull", snip)
    return best


def title_date(text, fallback_year):
    """从 '9.29策略' / '2026年9月28日' / '9月28日早盘预案' 抽日期"""
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\.(\d{1,2})策略", text)
    if m:
        return "%d-%02d-%02d" % (fallback_year, int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        return "%d-%02d-%02d" % (fallback_year, int(m.group(1)), int(m.group(2)))
    return None


def norm_label(day, img_day, ds):
    """标题日期离图片日期太远则视为 OCR 误读，回退"""
    if not day:
        return None
    try:
        import datetime
        a = datetime.date.fromisoformat(day)
        b = datetime.date.fromisoformat(img_day)
    except Exception:
        return None
    if abs((a - b).days) > 5:
        return None
    return day_index(ds, day)


def main():
    ds, _ = trading_days()

    # ---------- 红牛 ----------
    raw = json.load(open(STOCKDICT, encoding="utf-8"))
    n2c = {k: str(v).zfill(6) for k, v in raw.items()}
    c2n = {v: k for k, v in n2c.items()}
    base_pol = {}
    if os.path.isfile(HN_WR):
        for x in json.load(open(HN_WR, encoding="utf-8")).get("detail") or []:
            base_pol[(x.get("src"), str(x.get("code") or "").zfill(6))] = x.get("polarity")

    def item_override(text, names):
        """按「1. / 2. / 3.」切条目；名字在该条目里，且其**后面**出现卖出措辞 → bear"""
        segs = re.split(r"(?=\d+\.)", text)
        for seg in segs:
            for nm in names:
                pos = seg.find(nm)
                if pos < 0:
                    continue
                tail = seg[pos + len(nm):]
                if any(w in tail for w in SELL_PHRASES):
                    return "bear", seg.replace(chr(10), " ")[:80]
        return None, ""

    out = []
    stat = collections.Counter()
    for line in open(HN_PLANS, encoding="utf-8"):
        p = json.loads(line)
        img_day, t = p["day"], p["time"]
        hh = int(t[11:13])
        td = title_date(p.get("text", "")[:200], int(img_day[:4]))
        day = norm_label(td, img_day, ds) if td else None
        if day:
            stat["标题日期"] += 1
        elif hh >= 15:
            day = next_day(ds, img_day) if img_day in ds else day_index(ds, img_day)
            stat["收盘后→次日"] += 1
        else:
            day = day_index(ds, img_day)
            stat["当日"] += 1
        if not day:
            continue
        for s2 in p.get("stocks") or []:
            code = str(s2.get("code") or "").zfill(6)
            nm = s2.get("name") or c2n.get(code)
            if not code or not nm:
                continue
            # 红牛沿用上一会合的极性判定（OCR 文本嵌套括号多，逐条改判反而更糟）
            pol = base_pol.get((p.get("key"), code)) or "neutral"
            snip = ""
            out.append({"day": day, "time": t, "code": code, "name": nm,
                        "polarity": pol, "snippet": snip, "src": p.get("key")})
            stat["sig_" + pol] += 1
    seen, ded = set(), []
    for x in out:
        k = (x["day"], x["code"], x["polarity"])
        if k in seen:
            continue
        seen.add(k)
        ded.append(x)
    with open(HN_OUT, "w", encoding="utf-8") as f:
        for x in ded:
            f.write(json.dumps(x, ensure_ascii=False) + chr(10))
    print("红牛：信号 %d 条（去重后）%s" % (len(ded), dict(stat)))

    # ---------- 来去由心：沿用原 bull/bear，只做「风标剔除 + 描述行极性复核」----------
    fixed = {}
    st2 = collections.Counter()
    AMBIGUOUS = {"机器人", "半导体", "光伏", "储能", "军工", "芯片", "算力", "液冷", "光模块", "白酒"}
    name_keys = sorted([k for k in n2c if len(k) >= 3 and not any(ch.isdigit() for ch in k)
                        and k not in AMBIGUOUS], key=len, reverse=True)
    for f in sorted(glob.glob(os.path.join(LQ_PLANS, "*.json"))):
        if os.path.basename(f) == "index.json":
            continue
        rec0 = json.load(open(f, encoding="utf-8"))
        day0 = rec0.get("date")
        if not day0:
            continue
        day = day_index(ds, day0)
        if not day:
            continue
        texts = []
        for src in rec0.get("sources") or []:
            k = src.get("key")
            pth = os.path.join(LQ_OCR, "%s.json" % k)
            if os.path.isfile(pth):
                texts.append(json.load(open(pth, encoding="utf-8")).get("text") or "")
        text = chr(10).join(texts)
        if not text:
            st2["无OCR文本"] += 1
            continue
        # 2) 可执行段
        body = text
        i0 = body.find("具体机会解析")
        if i0 >= 0:
            body = body[i0:]
        i1 = body.find(chr(10) + "以上")
        if i1 > 0:
            body = body[:i1]
        lines = body.split(chr(10))

        rec = fixed.setdefault(day, {"bull": {}, "bear": {}, "fengkiao": set()})
        bull_codes = {x["code"]: x["name"] for x in (rec0.get("bull") or []) if x.get("code")}
        bear_codes = {x["code"]: x["name"] for x in (rec0.get("bear") or []) if x.get("code")}

        # 1) 风标剔除（按句子切，避免 OCR 断行导致漏判）
        import re as _re
        for ln in _re.split(chr(10) + "|" + "[。；]", text):
            if any(k in ln for k in DEF_FENG) or ("多头" in ln and "空头" in ln):
                for code in list(bull_codes):
                    if bull_codes[code] in ln:
                        rec["fengkiao"].add(code)
                        bull_codes.pop(code, None)
                        st2["风标剔除"] += 1

        # 段落切分（短线方面 / 题材方面 / 趋势票 / 其他）
        sec_of_line = []
        cur = "other"
        for ln in lines:
            key = ln.strip().strip("：: 【】")
            hit_sec = None
            for k, v in SECTIONS.items():
                if key.startswith(k) or (k in key and len(key) <= len(k) + 4):
                    hit_sec = v
                    break
            if hit_sec:
                cur = hit_sec
            sec_of_line.append((ln, cur))

        # 2.5) 只保留「具体机会解析」段里出现过的票（风标-only 的票直接丢弃）
        for code in list(bull_codes):
            if bull_codes[code] not in body:
                rec["fengkiao"].add(code)
                bull_codes.pop(code, None)
                st2["仅风标出现→剔除"] += 1

        # 2.6) 各段逐行抽取点名票（补上原 plans_ocr 名单漏掉的，尤其【短线方面】）
        seg_names = []
        for ln, sec in sec_of_line:
            if sec == "end" or not ln.strip():
                continue
            hits = []
            for nm in name_keys:
                if nm in ln and not any(nm in h or h in nm for h in hits):
                    hits.append(nm)
            if not hits:
                continue
            seg_names.extend(hits)
            for nm in hits:
                code = n2c[nm]
                if code in rec["bull"] or code in rec["bear"]:
                    continue
                q, sn = polarity_in(ln, (nm,), forward=True)
                if q == "bull":
                    rec["bull"][code] = {"code": code, "name": nm, "section": sec,
                                         "snippet": ln.strip()[:70]}
                    st2["段内新增·看多"] += 1
                elif q == "bear":
                    rec["bear"][code] = {"code": code, "name": nm, "section": sec, "snippet": ""}
                    st2["段内新增·看空"] += 1
                else:
                    st2["段内·无方向"] += 1

        # 3) 描述行极性复核（bear 优先）
        for code, nm in list(bull_codes.items()):
            hit = [ln for ln, _sec in sec_of_line if nm in ln]
            sec = next((_sec for ln, _sec in sec_of_line if nm in ln), "other")
            pol, snip = "neutral", ""
            for ln in hit:
                q, sn = polarity_in(ln, (nm,), win=30)
                if q == "bear":
                    pol, snip = "bear", sn
                    break
            if pol == "bear":
                bear_codes[code] = nm
                bull_codes.pop(code, None)
                st2["描述复核降级为看空"] += 1
            else:
                rec["bull"][code] = {"code": code, "name": nm, "section": sec,
                                     "snippet": (hit[0][:60] if hit else "")}
        for code, nm in bear_codes.items():
            sec = next((_sec for ln, _sec in sec_of_line if nm in ln), "other")
            rec["bear"][code] = {"code": code, "name": nm, "section": sec, "snippet": ""}
        st2["plan_days"] += 1

    out2 = {}
    for day, rec in fixed.items():
        out2[day] = {"bull": list(rec["bull"].values()),
                     "bear": list(rec["bear"].values()),
                     "fengkiao": sorted(rec["fengkiao"])}
    json.dump(out2, open(LQ_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("来去由心：%d 天  平均 bull %.1f / bear %.1f / 风标剔除 %.1f │ %s" % (
        len(out2),
        sum(len(v["bull"]) for v in out2.values()) / max(1, len(out2)),
        sum(len(v["bear"]) for v in out2.values()) / max(1, len(out2)),
        sum(len(v["fengkiao"]) for v in out2.values()) / max(1, len(out2)), dict(st2)))


if __name__ == "__main__":
    main()
