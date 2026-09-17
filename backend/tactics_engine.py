# -*- coding: utf-8 -*-
"""二板战法扫描引擎（纯计算，无网络）。

核心度量 = 换手率序列：健康路径为首板温和(约5%)→连板有序抬升(6/9/13/23/33%)→
头部放量兑现(>44%)。战法要义：抬升段介入、兑现段跑路；缩量加速=控盘好。

输入日K行（含 high/low/volume/turnover）+ rules（data/tactics_rules.json 可选），
输出技术面特征 + 阶段判定 + 得分。
"""
import json
import os

DEFAULT_RULES = {
    "version": 1,
    "ma": {"short": 5, "mid": 10, "long": 20, "base": 60},
    "boxLookback": 60,          # 横盘箱体回看天数
    "boxWidthMax": 0.45,        # 箱体宽度上限(最高-最低)/最低 < 此值 = 窄箱体
    "maConvergeMax": 0.08,      # 均线粘合度 (max-ma-min-ma)/min-ma < 此值
    "breakLookback": 10,        # 突破60线判定回看天数
    "volLookback": 20,          # 量能均线回看
    "turnoverSeqLen": 6,        # 换手率序列长度
    "turnover": {
        "healthyLow": 3.0, "healthyHigh": 9.0,   # 首板健康换手区间 %
        "burstMult": 1.5,        # 放量兑现：较前日倍数
        "burstFloor": 40.0,      # 放量兑现：绝对下限 %
        "liftMin": 1.0,          # 抬升段：换手较前日增幅下限倍数
    },
    "volDoubleMult": 2.0,        # 倍量：当日量较前一日 ≥ 此倍数
    "floatCapLow": 20.0, "floatCapHigh": 150.0,  # 流通盘适中区间(亿)
    "upsideMin": 0.08,          # 距前高空间下限
    "prevHighLookback": 120,    # 前高回看天数
    "weights": {
        "bullishAlign": 20, "aboveMa60": 15, "breakMa60": 10,
        "boxFlat": 15, "maConverge": 10, "floatOk": 10, "upside": 10,
        "stageHealthy": 20, "stageLift": 15, "stageBurst": -25,
        "sectorResonance": 15,   # 板块共振（同行业≥2只涨停）
        "volDouble": 15,         # 首板/二板倍量
    },
    "sectorResonanceMin": 2,   # 同行业涨停家数达到此值算板块共振
    "messageZtMin": 3,         # 消息共振：同行业涨停 ≥ 此值（板块热度高=有题材催化）
    "resonanceTechMin": 2,     # 个股共振：技术面核心维度至少命中几项
    "onlyResonant": True,      # 只输出三方共振标的（凑不齐时回落按分排序）
    "topN": 20,
}

# 个股"技术面核心"维度前缀（用于三方共振的个股判定）
TECH_DIM_PREFIXES = ("多头排列", "站上60线", "突破60线", "横盘箱体", "均线粘合", "倍量")
HEALTHY_STAGES = ("首板健康", "抬升", "缩量加速")


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_rules(path=None):
    rules = json.loads(json.dumps(DEFAULT_RULES))
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                rules = _deep_merge(rules, json.load(f))
        except Exception:
            pass
    return rules


def _ma(series, n):
    if not series:
        return None
    window = series[-n:] if len(series) >= n else series
    return sum(window) / len(window)


def analyze_kline(rows, rules):
    """日K行(升序，含 open/close/high/low/volume/turnover) → 技术面特征 dict。"""
    if not rows:
        return None
    closes = [r["close"] for r in rows if r.get("close") is not None]
    highs = [r["high"] for r in rows if r.get("high") is not None]
    lows = [r["low"] for r in rows if r.get("low") is not None]
    if len(closes) < 2:
        return None
    m = rules["ma"]
    close = closes[-1]
    ma5 = _ma(closes, m["short"])
    ma10 = _ma(closes, m["mid"])
    ma20 = _ma(closes, m["long"])
    ma60 = _ma(closes, m["base"])
    above60 = (ma60 is not None and close > ma60)
    bullish = all(v is not None for v in (ma5, ma10, ma20, ma60)) and ma5 > ma10 > ma20 > ma60
    # 突破60线：近 breakLookback 内首次 close>ma60（当日成立、前一日不成立）
    break60 = False
    if ma60 is not None and len(closes) >= 2:
        prev_close = closes[-2]
        prev_ma60 = _ma(closes[:-1], m["base"])
        if prev_ma60 is not None:
            break60 = close > ma60 and prev_close <= prev_ma60
    # 均线粘合度
    mas = [v for v in (ma5, ma10, ma20, ma60) if v]
    ma_converge = None
    if len(mas) >= 2 and min(mas) > 0:
        ma_converge = (max(mas) - min(mas)) / min(mas)
    # 横盘箱体
    box_look = rules["boxLookback"]
    box_h = max(highs[-box_look:]) if highs else None
    box_l = min(lows[-box_look:]) if lows else None
    box_width = (box_h - box_l) / box_l if (box_h and box_l and box_l > 0) else None
    # 前高与空间
    ph_look = rules["prevHighLookback"]
    prev_high = max(highs[-ph_look:]) if highs else None
    upside = (prev_high - close) / close if (prev_high and close) else None
    # 量能
    vols = [r.get("volume") or 0 for r in rows]
    v_look = rules["volLookback"]
    avg_vol = _ma(vols, v_look)
    vol_ratio = (vols[-1] / avg_vol) if (avg_vol and avg_vol > 0) else None
    # 倍量：当日量较前一日倍数（首板/二板倍量）
    prev_vol_ratio = (vols[-1] / vols[-2]) if (len(vols) >= 2 and vols[-2] and vols[-2] > 0) else None
    # 换手率序列（%）
    turns = [round((r.get("turnover") or 0) * 100, 2) for r in rows if r.get("turnover") is not None]
    seq_len = rules["turnoverSeqLen"]
    turnover_seq = turns[-seq_len:]
    latest_turnover = turns[-1] if turns else None
    day_chg = round((closes[-1] / closes[-2] - 1) * 100, 2) if (len(closes) >= 2 and closes[-2]) else None
    return {
        "close": close, "dayChg": day_chg, "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "aboveMa60": above60, "bullishAlign": bullish, "breakMa60": break60,
        "maConverge": round(ma_converge, 4) if ma_converge is not None else None,
        "boxWidth": round(box_width, 4) if box_width is not None else None,
        "prevHigh": prev_high, "upside": round(upside, 4) if upside is not None else None,
        "volRatio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "prevVolRatio": round(prev_vol_ratio, 2) if prev_vol_ratio is not None else None,
        "turnoverSeq": turnover_seq, "latestTurnover": latest_turnover,
        "floatCapYi": None,  # 由调用方从全市场快照 ltsz 填入（日K volume 单位不稳定，不反推）
    }


def judge_turnover_stage(turnover_seq, rules):
    """换手率序列(升序%) → 阶段标签 (stage, desc)。"""
    t = rules["turnover"]
    if not turnover_seq:
        return "无数据", ""
    latest = turnover_seq[-1]
    if len(turnover_seq) < 2:
        if t["healthyLow"] <= latest <= t["healthyHigh"]:
            return "首板健康", "首板换手%.1f%% 温和" % latest
        return "首板", "首板换手%.1f%%" % latest
    prev = turnover_seq[-2]
    if prev > 0 and latest > prev * t["burstMult"] and latest > t["burstFloor"]:
        return "放量兑现", "换手%.1f%%(较前日%.1f%% 激增)" % (latest, prev)
    if latest < prev:
        return "缩量加速", "换手%.1f%%(较前日%.1f%% 收缩，控盘好)" % (latest, prev)
    if latest >= prev:
        return "抬升", "换手%.1f%%(较前日%.1f%% 抬升)" % (latest, prev)
    return "常规", ""


def score_candidate(features, stage, rules, sector_zt_count=0):
    """技术面特征 + 阶段 + 板块涨停家数 → (score, hits[], warns[])。

    三方共振：消息面(用板块热度近似) + 板块(同行业涨停家数) + 个股(技术面/换手率)。
    """
    w = rules["weights"]
    sc = 0
    hits, warns = [], []
    if features.get("bullishAlign"):
        sc += w["bullishAlign"]; hits.append("多头排列")
    if features.get("aboveMa60"):
        sc += w["aboveMa60"]; hits.append("站上60线")
    if features.get("breakMa60"):
        sc += w["breakMa60"]; hits.append("突破60线")
    bw = features.get("boxWidth")
    if bw is not None and bw < rules["boxWidthMax"]:
        sc += w["boxFlat"]; hits.append("横盘箱体")
    mc = features.get("maConverge")
    if mc is not None and mc < rules["maConvergeMax"]:
        sc += w["maConverge"]; hits.append("均线粘合(庄股)")
    fc = features.get("floatCapYi")
    if fc is not None and rules["floatCapLow"] <= fc <= rules["floatCapHigh"]:
        sc += w["floatOk"]; hits.append("流通盘适中")
    up = features.get("upside")
    if up is not None and up >= rules["upsideMin"]:
        sc += w["upside"]; hits.append("距前高%d%%" % round(up * 100))
    # 板块共振（三方之一：消息面/热点催化近似）
    if sector_zt_count >= rules["sectorResonanceMin"]:
        sc += w["sectorResonance"]; hits.append("板块共振%d只" % sector_zt_count)
    # 倍量（首板/二板当日量较前一日 ≥ 倍数）
    pvr = features.get("prevVolRatio")
    if pvr is not None and pvr >= rules["volDoubleMult"]:
        sc += w["volDouble"]; hits.append("倍量%.1fx" % pvr)
    # 个股共振（三方之一：换手率阶段）
    if stage in ("首板健康", "抬升"):
        sc += w["stageHealthy"]; hits.append(stage)
    elif stage == "缩量加速":
        sc += w["stageHealthy"]; hits.append("缩量加速")
    elif stage == "放量兑现":
        sc += w["stageBurst"]; warns.append("放量兑现(顶部风险)")
    return max(0, min(100, sc)), hits, warns


def judge_resonance(hits, stage, sector_zt_count, rules):
    """三方共振判定：消息面 + 板块 + 个股。

    - 个股：技术面核心维度命中 ≥ resonanceTechMin 且换手阶段健康
    - 板块：同行业涨停 ≥ sectorResonanceMin
    - 消息：同行业涨停 ≥ messageZtMin（板块热度高，视为有题材催化）
    返回 {"stock","sector","news","resonant"}。
    """
    tech_n = sum(1 for h in hits if str(h).startswith(TECH_DIM_PREFIXES))
    stock_ok = tech_n >= rules["resonanceTechMin"] and stage in HEALTHY_STAGES
    sector_ok = (sector_zt_count or 0) >= rules["sectorResonanceMin"]
    news_ok = (sector_zt_count or 0) >= rules["messageZtMin"]
    return {"stock": stock_ok, "sector": sector_ok, "news": news_ok,
            "resonant": stock_ok and sector_ok and news_ok, "techN": tech_n}


# ---------- 卡位晋级（梯队位置） ----------

def judge_leadership(prev_rows, today_rows):
    """对比昨日/今日涨停池，判定晋级 / 卡位晋级 / 被卡位。

    - 晋级：昨日 N 板 → 今日 N+1 板（位置没变、顺利上）
    - 卡位晋级：今日晋级者昨日板位 < 昨日最高断板票板位（后排挤掉前排）
    - 被卡位：昨日高位票(>=2)今日断板，且今日存在卡位晋级 → 拉升只有卖点

    返回 {"promotions":[...], "lostSlots":[...]}。
    """
    P = {r["code"]: r for r in (prev_rows or []) if r.get("code") and (r.get("lb") or 0) >= 1}
    T = {r["code"]: r for r in (today_rows or []) if r.get("code") and (r.get("lb") or 0) >= 1}
    promotions = []
    for code, t in T.items():
        p = P.get(code)
        if not p:
            continue
        prev_lb = p.get("lb") or 0
        today_lb = t.get("lb") or 0
        if today_lb == prev_lb + 1:
            promotions.append({
                "code": code, "name": t.get("name") or p.get("name") or "",
                "industry": t.get("industry") or p.get("industry") or "",
                "prevLb": prev_lb, "todayLb": today_lb, "type": "晋级",
            })
    broken = [p for code, p in P.items() if code not in T]
    max_broken_lb = max([p.get("lb") or 0 for p in broken if (p.get("lb") or 0) >= 2], default=None)
    has_kakwei = False
    for pr in promotions:
        if max_broken_lb is not None and pr["prevLb"] < max_broken_lb:
            pr["type"] = "卡位晋级"
            has_kakwei = True
    lost = []
    if has_kakwei:
        for p in broken:
            if (p.get("lb") or 0) >= 2:
                lost.append({
                    "code": p.get("code"), "name": p.get("name") or "",
                    "industry": p.get("industry") or "", "prevLb": p.get("lb") or 0,
                    "note": "被卡位·拉升只卖",
                })
    promotions.sort(key=lambda x: (x.get("todayLb") or 0), reverse=True)
    lost.sort(key=lambda x: (x.get("prevLb") or 0), reverse=True)
    return {"promotions": promotions, "lostSlots": lost}


# 量窒息（缩量横盘）默认阈值
CONGESTION_RULES = {
    "volShrinkMax": 0.75,   # 近5日均量 / 近20日均量 < 此值 = 量能萎缩
    "ampMax": 0.10,         # 近5日 (最高-最低)/最低 < 此值 = 振幅收窄
    "nearMaMax": 0.04,      # |close-MA20|/MA20 < 此值 = 近支撑
    "score": {"shrink": 40, "amp": 30, "red": 15, "nearMa": 15},
}


def judge_congestion(feats, rules=None):
    """量窒息判定：量能萎缩 且 振幅收窄（核心）；收红 + 近MA20 加分。

    需 feats 含 closes/highs/lows/vols/open 等；此处直接用日K行计算。
    """
    r = rules or CONGESTION_RULES
    if not feats or len(feats) < 21:
        return None
    vols = [x.get("volume") or 0 for x in feats]
    highs = [x.get("high") for x in feats if x.get("high") is not None]
    lows = [x.get("low") for x in feats if x.get("low") is not None]
    closes = [x.get("close") for x in feats if x.get("close") is not None]
    if len(closes) < 21 or len(highs) < 6 or len(lows) < 6:
        return None
    v5 = sum(vols[-5:]) / 5 if sum(vols[-5:]) else 0
    v20 = sum(vols[-20:]) / 20 if sum(vols[-20:]) else 0
    vol_shrink = (v5 / v20) if v20 > 0 else None
    h5, l5 = max(highs[-5:]), min(lows[-5:])
    amplitude = (h5 - l5) / l5 if l5 else None
    ma20 = sum(closes[-20:]) / 20
    close = closes[-1]
    near_ma = abs(close - ma20) / ma20 if ma20 else None
    last = feats[-1]
    red = (last.get("close") is not None and last.get("open") is not None
           and last["close"] > last["open"])
    shrink_ok = vol_shrink is not None and vol_shrink < r["volShrinkMax"]
    amp_ok = amplitude is not None and amplitude < r["ampMax"]
    if not (shrink_ok and amp_ok):
        return None
    sc = r["score"]["shrink"] + r["score"]["amp"]
    hits = ["量能萎缩%.2f" % vol_shrink, "振幅%.1f%%" % (amplitude * 100)]
    if red:
        sc += r["score"]["red"]; hits.append("收红")
    if near_ma is not None and near_ma < r["nearMaMax"]:
        sc += r["score"]["nearMa"]; hits.append("近MA20")
    return {"volShrink": round(vol_shrink, 3), "amplitude": round(amplitude, 4),
            "nearMa": round(near_ma, 4) if near_ma is not None else None,
            "red": red, "score": sc, "hits": hits}
