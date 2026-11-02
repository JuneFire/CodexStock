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
        "sectorForm": 15,        # 板块形态好（低位站上60线+5/10/20多头向上）
        "volDouble": 15,         # 首板/二板倍量
        "ladderBest": 12,        # 手册换手率阶梯：最优档加分
        "ladderGood": 6,         # 次优档
        "ladderWarn": -8,        # 警戒档
        "ladderExclude": -22,    # 剔除档（>9%首板 / >28%三板 / 四板放量覆盖）
    },
    "sectorResonanceMin": 2,   # 同行业涨停家数达到此值算板块共振
    "messageZtMin": 3,         # 消息共振：同行业涨停 ≥ 此值（板块热度高=有题材催化）
    "resonanceTechMin": 2,     # 个股共振：技术面核心维度至少命中几项
    "onlyResonant": True,      # 只输出三方共振标的（凑不齐时回落按分排序）
    "candLbMax": 3,           # 候选连板数上限（手册二板战法主做1→2板，三板缩量优先也保留）
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
        # 以下为长序列，供换手率阶梯（压力位/庄股/爆量覆盖前量）使用，不入库
        "turnsHistory": turns[-130:], "closesHistory": closes[-130:],
        "volsHistory": vols[-130:], "open": rows[-1].get("open"),
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


def score_candidate(features, stage, rules, sector_zt_count=0, sector_form=None, ladder=None,
                    adjust=0):
    """技术面特征 + 阶段 + 板块涨停家数 + 板块形态 → (score, hits[], warns[])。

    三方共振：消息面(板块热度近似) + 板块(同行业涨停家数) + 个股(技术面/换手率)。
    sector_form: judge_sector 的结果；good=True 时加板块形态分。
    ladder: judge_ladder 的结果；按手册换手率阶梯给档位加减分。
    adjust: judge_strategy 的软项加权分（庄股/连阳/逆势），已在四关里算好。
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
    # 板块形态好（三方·板块质量）：低位站上60线 + 5/10/20多头向上
    if sector_form and sector_form.get("good"):
        sc += w.get("sectorForm", 0); hits.append("板块形态好")
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
    # 手册换手率阶梯：档位优先于「阶段」的粗略判断
    if ladder:
        grade = ladder.get("grade")
        if grade == "最优":
            sc += w.get("ladderBest", 0); hits.append("阶梯最优%d档" % (ladder.get("ladder") or 0))
        elif grade in ("次优", "健康"):
            sc += w.get("ladderGood", 0); hits.append("阶梯%s%d档" % (grade, ladder.get("ladder") or 0))
        elif grade in ("警戒", "高风险"):
            sc += w.get("ladderWarn", 0); warns.append("阶梯%s(%s)" % (grade, ladder.get("action") or ""))
        elif grade in ("超标", "剔除"):
            sc += w.get("ladderExclude", 0); warns.append("阶梯%s：%s" % (grade, ladder.get("action") or ""))
    if adjust:
        sc += adjust
        hits.append("软项加权%+d" % adjust) if adjust > 0 else warns.append("软项加权%+d" % adjust)
    return max(0, sc), hits, warns


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


# ---------- 板块形态（三方共振·板块质量加分） ----------

SECTOR_FORM_RULES = {
    "weight": 15,            # 板块形态好 → 加分
    "maNearMax": 0.10,       # close 在 ma60 上方 0~10% = 刚站上
    "posLookback": 120,      # 低位判定回看天数
    "posMax": 0.50,          # 在近N日区间的下半区 = 低位
}


def judge_sector(idx, rules=None):
    """板块指数技术形态：低位站上60线 + 5/10/20多头向上。

    idx: {name, closes, volumes} | None。返回 {good, hits, ...} 或 None。
    good = 站上60线 且 刚站上(<=maNearMax) 且 MA5>MA10>MA20 且 MA20上行 且 低位
    """
    r = rules or SECTOR_FORM_RULES
    if not idx:
        return None
    closes = idx.get("closes") or []
    if len(closes) < 61:
        return None
    close = closes[-1]
    ma5, ma10, ma20, ma60 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20), _ma(closes, 60)
    if None in (ma5, ma10, ma20, ma60) or ma60 <= 0:
        return None
    above = close > ma60
    dist = (close - ma60) / ma60
    near = 0 <= dist <= r["maNearMax"]          # 刚站上60线（未远离）
    bull = ma5 > ma10 > ma20                     # 5/10/20 多头排列
    up = (ma20 > _ma(closes[:-1], 20)) and (ma5 > _ma(closes[:-1], 5))  # 均线上行
    look = closes[-r["posLookback"]:]
    rng = max(look) - min(look)
    pos = (close - min(look)) / rng if rng > 0 else None
    low = pos is not None and pos < r["posMax"]
    hits = []
    if above: hits.append("站上60线")
    if near: hits.append("刚站上")
    if bull: hits.append("5-10-20多头")
    if up: hits.append("均线上行")
    if low: hits.append("低位%.0f%%" % (pos * 100) if pos is not None else "低位")
    good = above and near and bull and up and low
    return {"good": good, "hits": hits, "dist": round(dist, 4),
            "pos": round(pos, 3) if pos is not None else None, "name": idx.get("name")}


# ============================================================
# 《二板战法手册》规则层：换手率阶梯 → 六维串联 → 四关 → 仓位
# 手册章节对照：C.1 阶梯表 / 1.7 五层漏斗 / 4.4 六维 / 2.3 三板缩量 / 3.8 四关
# ============================================================

TURNOVER_LADDER = (3, 6, 9, 13, 18, 23, 28, 33, 43, 55)

LADDER_RULES = {
    # 首板：3% 最优，6% 中性，9% 次优，>10% 排除（手册写 9%，实盘放宽到 10%）
    "board1": {"best": 3.5, "mid": 6.5, "weak": 9.0, "exclude": 10.0},
    # 二板：9% 最优，13% 次优，18% 警戒，>18% 高风险（>20% 尤甚）
    "board2": {"best": 9.5, "good": 13.5, "warn": 18.0, "danger": 20.0},
    # 三板未缩量：18/23/28 健康（越小越好），>28% 剔除
    "board3": {"ok": 18.0, "good": 23.0, "max": 28.0},
    # 四板起：温和放量可不覆盖前量（13-18 档）
    "board4": {"ok": 18.0},
    "surgeMax": 20.0,       # 两三板放量 >20% = 主力仓位过猛
    "lateBurst": 23.0,      # 末段爆量档起点（23/33/43/55）
    "pressureMax": 10.0,    # 前高附近最高换手 < 约 10% = 上方无强套牢盘
    "pressureTol": 0.5,     # 临界容差（手册原文是「约 10%」）
    "zhuang": {"turnMin": 0.5, "turnMax": 8.0, "aboveMa5Ratio": 0.5,
               "ampMax": 0.25, "lookback": 20},
    "lianyang": {"noNew": 6, "cut": 7, "empty": 8, "clear": 9},
    "counterTrend": {"ok": 1, "soso": 2, "stop": 3},
    "sectorStaleDays": 5,   # 板块指数缓存落后超过此天数 → 逆势判定置为不可用
}


def nearest_ladder(pct):
    """换手率 → 最近的阶梯档位（3·6·9·13·18·23·28·33·43·55）。"""
    if pct is None:
        return None
    return min(TURNOVER_LADDER, key=lambda x: abs(x - pct))


def judge_ladder(board, turnover_seq, vols=None, rules=None):
    """按手册 C.1 换手率阶梯判定当前档位与动作。

    board: 连板数（1=首板，2=二板，3=三板，4+=高板）
    turnover_seq: 升序换手率(%)，末位=当日
    vols: 升序成交量序列，用于四板「放量是否覆盖前量」
    返回 {board,latest,prev,ladder,shrink,grade,action,tone,exclude,coverPrevVol,notes}
    """
    r = rules or LADDER_RULES
    seq = [float(x) for x in (turnover_seq or []) if x is not None]
    if not seq:
        return None
    board = max(1, int(board or 1))
    latest = seq[-1]
    prev = seq[-2] if len(seq) >= 2 else None
    shrink = bool(prev and prev > 0 and latest < prev)
    ladder = nearest_ladder(latest)
    notes = []
    cover = False
    if vols and len(vols) > board:
        window = [v for v in vols[-(board + 1):-1] if v]
        if window:
            cover = bool(vols[-1] >= max(window))
    grade, action, tone, exclude = "常规", "", "flat", False

    if board <= 1:
        b1 = r["board1"]
        if latest <= b1["best"]:
            grade, action, tone = "最优", "首选·锁筹好抛压小", "up"
        elif latest <= b1["mid"]:
            grade, action, tone = "中性", "需二板量能配合确认", "warn"
        elif latest <= b1["weak"]:
            grade, action, tone = "次优", "偏弱·谨慎观察", "warn"
        elif latest <= b1["exclude"]:
            grade, action, tone = "次优", "临界（9~10%）·可做但仓位降至三成", "warn"
        else:
            grade, action, tone, exclude = "超标", "超过10%·排除，不列入选项", "down", True
    elif board == 2:
        b2 = r["board2"]
        if latest <= b2["best"]:
            grade, action, tone = "最优", "可重仓（缩量更佳）", "up"
            if shrink:
                notes.append("二板缩量(6-9档最佳)")
        elif latest <= b2["good"]:
            grade, action, tone = "次优", "可参与·仓位略降", "warn"
        elif latest <= b2["warn"]:
            grade, action, tone = "警戒", "不新开仓·持有者准备离场", "down"
        else:
            grade, action, tone = "高风险", "慎之又慎·只做冲高即走", "down"
            if latest >= b2["danger"]:
                notes.append("换手超20%：新进主力接盘压力极大")
    elif board == 3:
        b3 = r["board3"]
        if shrink:
            grade, action, tone = "最优", "缩量优先·一致性与集中度最佳", "up"
        elif latest <= b3["ok"]:
            grade, action, tone = "健康", "18档健康，越小越好", "up"
        elif latest <= b3["good"]:
            grade, action, tone = "次优", "23档健康，越小越好", "warn"
        elif latest <= b3["max"]:
            grade, action, tone = "健康", "28档边缘，越小越好", "warn"
        else:
            grade, action, tone, exclude = "剔除", "超出上限·放弃跟踪", "down", True
    else:
        b4 = r["board4"]
        if shrink:
            grade, action, tone = "最优", "缩量加速·场外进不来", "up"
        elif cover:
            grade, action, tone, exclude = "剔除", "放量覆盖前量=溢价开始消失", "down", True
        elif latest <= b4["ok"]:
            grade, action, tone = "中性", "温和放量·未覆盖前量", "warn"
        else:
            grade, action, tone = "警戒", "放量过大·准备离场", "down"

    if board >= 2 and latest >= r["lateBurst"] and not shrink:
        notes.append("已进爆量档(23+/33/43/55)→离场区")
    if 2 <= board <= 3 and latest > r["surgeMax"]:
        notes.append("两三板放量超20%：主力仓位过猛，次日缩量加速可修复，再度放量则减仓")
    return {"board": board, "latest": round(latest, 2),
            "prev": round(prev, 2) if prev is not None else None,
            "ladder": ladder, "shrink": shrink, "grade": grade, "action": action,
            "tone": tone, "exclude": exclude, "coverPrevVol": cover, "notes": notes}


def judge_pressure_level(turns, board, rules=None, closes=None):
    """压力位判定（手册 4.6）：前高附近最高换手 < 约 10% = 上方无强套牢区。

    turns/closes: 升序换手率(%)与收盘价，同一长度；board 用于剔除本波连板段。
    取「前高日±10 日」区间内的最高换手作为套牢密度的代理指标。
    """
    r = rules or LADDER_RULES
    hist = [float(t) for t in (turns or []) if t]
    cut = max(1, int(board or 1))
    if len(hist) <= cut:
        return None
    base = hist[:-cut][-120:]
    cs = [float(c) for c in (closes or []) if c]
    prev_max = None
    how = "区间最高换手"
    if len(cs) == len(hist):
        seq_c = cs[:-cut][-120:]
        if len(seq_c) == len(base) and seq_c:
            i_hi = max(range(len(seq_c)), key=lambda i: seq_c[i])
            seg = base[max(0, i_hi - 10): i_hi + 11]
            if seg:
                prev_max = max(seg)
                how = "前高日±10日最高换手"
    if prev_max is None:
        prev_max = max(base)
    ok = prev_max <= r["pressureMax"] + r.get("pressureTol", 0.0)
    return {"prevMaxTurnover": round(prev_max, 2), "pass": ok, "basis": how,
            "note": ("%s%.1f%%<%.0f%%，上方抛压有限"
                     % (how, prev_max, r["pressureMax"])) if ok else
                    ("%s%.1f%%，前高套牢盘密集" % (how, prev_max))}


def judge_zhuanggu(turns, closes, board, rules=None):
    """庄股特征（手册 1.7 第2层）：首板前换手温和，且「沿 5 日线上行」或「长期横盘/阴跌后启动」。

    阶梯只看首板之前的那段（剔除本波连板段），否则连板自身会把形态抬高。
    """
    r = (rules or LADDER_RULES)["zhuang"]
    cut = max(1, int(board or 1))
    hist = [float(t) for t in (turns or []) if t]
    cs_all = [float(c) for c in (closes or []) if c]
    if len(hist) <= cut or len(cs_all) <= cut:
        return None
    pre_t = hist[:-cut][-r["lookback"]:]
    pre_c = cs_all[:-cut][-(r["lookback"] + 5):]
    if len(pre_t) < 5 or len(pre_c) < 6:
        return None
    mean_t = sum(pre_t) / len(pre_t)
    # 沿 5 日线上行：首板前最近 10 日收盘在 MA5 上方的比例
    above = 0
    n = 0
    for i in range(max(4, len(pre_c) - 10), len(pre_c)):
        ma5 = sum(pre_c[i - 4:i + 1]) / 5
        n += 1
        if ma5 > 0 and pre_c[i] >= ma5:
            above += 1
    ratio = above / n if n else 0.0
    amp = (max(pre_t) - min(pre_t)) / max(pre_t) if max(pre_t) > 0 else None
    turn_ok = r["turnMin"] <= mean_t <= r["turnMax"]
    trend_ok = ratio >= r["aboveMa5Ratio"]
    flat_ok = amp is not None and amp <= r["ampMax"]
    if turn_ok and trend_ok:
        form = "沿5日线上行"
    elif turn_ok and flat_ok:
        form = "横盘/阴跌后启动"
    else:
        form = "未控盘"
    return {"meanTurnover": round(mean_t, 2), "aboveMa5Ratio": round(ratio, 2),
            "turnRange": round(amp * 100, 1) if amp is not None else None,
            "pass": bool(turn_ok and (trend_ok or flat_ok)), "form": form,
            "note": "首板前均换手%.1f%%/沿5日线%.0f%%/振幅%s → %s"
                    % (mean_t, ratio * 100, ("%.0f%%" % (amp * 100)) if amp is not None else "—", form)}


def judge_lianyang(closes, rules=None):
    """连阳计数（手册 C.2）：收盘价连续走高天数 → 买入侧/持有侧动作与仓位上限。"""
    r = (rules or LADDER_RULES)["lianyang"]
    cs = [float(c) for c in (closes or []) if c]
    if len(cs) < 2:
        return None
    cnt = 0
    for i in range(len(cs) - 1, 0, -1):
        if cs[i] > cs[i - 1]:
            cnt += 1
        else:
            break
    if cnt >= r["clear"]:
        action, cap, tone = "清仓", "0", "down"
    elif cnt >= r["empty"]:
        action, cap, tone = "基本空仓（观察仓）", "≤20%", "down"
    elif cnt >= r["cut"]:
        action, cap, tone = "减仓", "≤50%", "warn"
    elif cnt >= r["noNew"]:
        action, cap, tone = "不新开仓（买入侧直接否决）", "满仓不动", "warn"
    else:
        action, cap, tone = "可持有·可介入", "—", "up"
    return {"count": cnt, "action": action, "positionCap": cap, "tone": tone,
            "noNew": cnt >= r["noNew"]}


def judge_counter_trend(sector_closes, rules=None, stale_days=None):
    """逆势纪律（手册 4.6）：板块连续下跌天数 → 可参与/勉强承接/必须走。"""
    r = (rules or LADDER_RULES)
    if stale_days is not None and stale_days > r["sectorStaleDays"]:
        return {"downDays": None, "verdict": "不可用", "tone": "flat",
                "note": "板块指数缓存落后%d天，逆势判定跳过" % stale_days}
    cs = [float(c) for c in (sector_closes or []) if c]
    if len(cs) < 2:
        return None
    days = 0
    for i in range(len(cs) - 1, 0, -1):
        if cs[i] < cs[i - 1]:
            days += 1
        else:
            break
    ct = r["counterTrend"]
    if days >= ct["stop"]:
        return {"downDays": days, "verdict": "必须走", "tone": "down",
                "note": "板块%d连跌，逆势个股次日最低开跌停" % days}
    if days >= ct["soso"]:
        return {"downDays": days, "verdict": "勉强承接", "tone": "warn",
                "note": "板块%d连跌，减仓只留底仓" % days}
    if days >= ct["ok"]:
        return {"downDays": days, "verdict": "可参与", "tone": "warn",
                "note": "板块%d连跌，可持有但不再加仓" % days}
    return {"downDays": days, "verdict": "板块正常", "tone": "up", "note": ""}


def _six_dim(feats, board, ctx, rules, pressure, zhuang):
    """六维串联否决（手册 4.4）：盘子/庄股/名字/贴合度/排雷/压力位。

    可计算的三维给出 pass/fail；名字、贴合度、排雷需要公告与主营数据，
    本仓库无可靠数据源 → 标 manual（人工复核），不伪造结论。
    """
    fc = feats.get("floatCapYi")
    price = ctx.get("price") or feats.get("close")
    cap_max = rules.get("floatCapHigh") or 150.0
    items = []
    if fc is None:
        items.append({"key": "cap", "label": "盘子", "status": "unknown", "detail": "流通市值缺失"})
    else:
        ok = fc <= cap_max
        detail = "流通盘%.0f亿" % fc + ("，股价%.2f元" % price if price else "")
        items.append({"key": "cap", "label": "盘子", "status": "pass" if ok else "fail",
                      "detail": detail + ("" if ok else "，盘子过大封不住")})
    if zhuang is None:
        items.append({"key": "zhuang", "label": "庄股", "status": "unknown", "detail": "历史数据不足"})
    else:
        items.append({"key": "zhuang", "label": "庄股",
                      "status": "pass" if zhuang["pass"] else "fail", "detail": zhuang["note"]})
    items.append({"key": "name", "label": "名字", "status": "manual",
                  "detail": "辨识度（行业词/地名/记忆点）需人工判断"})
    items.append({"key": "fit", "label": "贴合度", "status": "manual",
                  "detail": "主营与概念是否真贴合，需人工核对公告"})
    items.append({"key": "risk", "label": "排雷", "status": "manual",
                  "detail": "减持/解禁/质押/商誉/业绩雷，需查公告"})
    if pressure is None:
        items.append({"key": "pressure", "label": "压力位", "status": "unknown", "detail": "数据不足"})
    else:
        items.append({"key": "pressure", "label": "压力位",
                      "status": "pass" if pressure["pass"] else "fail", "detail": pressure["note"]})
    return items


# 标准工作流（用户口径，2026-11 定）：D 日 15:00 收盘后遍历当日涨停股选股 → D+1 开盘买入 → D+2 竞价卖出。
# A 股 T+1 合规（D+1 买、D+2 卖）。
# 200 交易日 / 15,337 涨停股 / 剔除 D+1 开盘即涨停（9.2% 买不进）后 13,923 条实测：
#   ret = D+2开盘 / D+1开盘 - 1 → 均值 -0.46%，胜率 43.0%，中位 -0.81%
#   候选池（阶梯最优+健康） -0.54%；按板型 一字板 -1.70% / 早板 -0.93% / 换手板 -0.17%
#   唯一微正组合：D+1竞价低开≤-2% + D日晚板(13:00后封板) + 封单<1亿 → +0.65%（n=213，每日中位 1 只，不显著）
#   被剔除的「开盘即涨停」1414 条均值 +1.25%、中位 +3.17% —— 涨停溢价全在这批买不到的票里。
EXIT_PLAN = {"route": "路线2·盘后复盘",
             "buyAt": "D+1开盘",
             "holdDays": 2,
             "exitAt": "D+2竞价",
             "backup": "D+2收盘",
             "expect": -0.46,
             "expectNote": "200日/13923条可买样本：D+1开盘买→D+2竞价卖 -0.46%（t=-5.87），负期望，仅作观察池",
             "note": "D+1 开盘即涨停的票（9.2%）根本买不进，它们的均值是 +1.25%、中位 +3.17%；"
                     "能买到的都是弱者。要赚涨停溢价只能在 D 日涨停价排板成交（打板路线）。"}


def _four_gate(ladder, pressure, lianyang, counter, gate6, ctx, rules):
    """硬否决 + 软项加权（2026-11 回测结论重做）。

    旧四关把庄股/连阳/逆势当一票否决，回测显示「可买 vs 回避」几乎无差
    （+1.03% vs +0.95%），这三项没有独立 alpha。现改为：
      硬否决：阶梯超标 / 压力位不通过 / 连阳≥空仓线 / 板块连跌≥3天 / 六维盘子否决
      软项加权：阶梯档位、连阳 4~7、逆势 1~2 天、庄股形态 → scoreAdjust
    verdict 在这里只给「通过/回避」，候选/观察由调用方按阶梯档位切。
    """
    adj, notes, blocked = 0, [], []
    ly = rules["lianyang"]

    if ladder is None:
        notes.append("换手率阶梯数据不足")
    elif ladder.get("exclude"):
        blocked.append(("换手率阶梯", ladder.get("action") or "超标"))
    elif ladder.get("grade") == "最优":
        adj += 4; notes.append("阶梯最优%d档 +4" % (ladder.get("ladder") or 0))
    elif ladder.get("grade") in ("超标", "剔除", "高风险"):
        blocked.append(("换手率阶梯", "%s%s档：%s" % (ladder.get("grade"),
                        ladder.get("ladder") or "", ladder.get("action") or "")))
    elif ladder.get("grade") == "警戒":
        adj -= 6; notes.append("阶梯警戒 -6")
    elif ladder.get("grade") == "次优" and str(ladder.get("action") or "").startswith("偏弱"):
        adj -= 2; notes.append("阶梯次优偏弱 -2")

    if pressure is None:
        notes.append("压力位数据不足")
    elif not pressure.get("pass"):
        blocked.append(("压力位", pressure.get("note")))

    if lianyang is None:
        notes.append("连阳数据不足")
    else:
        c = lianyang.get("count") or 0
        if c >= ly["empty"]:
            blocked.append(("连阳高位", "%d连阳→空仓线" % c))
        elif c >= ly["cut"]:
            adj -= 10; notes.append("%d连阳→减仓 -10" % c)
        elif c >= ly["noNew"]:
            adj -= 6; notes.append("%d连阳→不新开仓 -6" % c)
        elif c >= 4:
            adj -= 2; notes.append("%d连阳 -2" % c)

    if counter:
        v = counter.get("verdict")
        if v == "必须走":
            blocked.append(("板块逆势", counter.get("note") or "连跌≥3天"))
        elif v == "勉强承接":
            adj -= 6; notes.append("板块连跌2天 -6")
        elif v == "可参与":
            adj -= 2; notes.append("板块连跌1天 -2")

    gate6 = gate6 or []
    for g in gate6:
        if g.get("key") == "cap" and g.get("status") == "fail":
            blocked.append(("盘子", g.get("detail")))
        elif g.get("key") == "zhuang":
            if g.get("status") == "pass":
                adj += 6; notes.append("庄股控盘形态 +6")
            elif g.get("status") == "fail":
                adj -= 4; notes.append("未控盘 -4")
    manual = [g["label"] for g in gate6 if g.get("status") == "manual"]
    unknown = [g["label"] for g in gate6 if g.get("status") == "unknown"]

    is_ji = bool(ctx.get("jipoint"))
    strong = (ctx.get("sectorZt") or 0) >= 3
    if blocked:
        pos, pos_tone = "不参与", "down"
    elif is_ji:
        pos, pos_tone = "重仓", "up"
    elif strong:
        pos, pos_tone = "五成", "warn"
    else:
        pos, pos_tone = "三成", "warn"

    def _g(no, name, ok, detail, warn=False):
        return {"gate": no, "name": name, "detail": detail,
                "status": "fail" if not ok else ("warn" if warn else "pass")}

    ladder_ok = not (ladder and ladder.get("exclude"))
    gates = [
        {"gate": 1, "name": "概念持续性/传播力", "status": "manual",
         "detail": "需人工判断：能否持续 + 能否传播（否=终局放弃）"},
        _g(2, "板块时间节点/逆势", not (counter and counter.get("verdict") == "必须走"),
           (counter or {}).get("note") or "板块未连续下跌",
           warn=bool(counter and counter.get("verdict") in ("勉强承接", "可参与"))),
        _g(3, "压力位/阶梯/盘子", ladder_ok and bool(pressure and pressure.get("pass"))
           and not any(b[0] in ("压力位", "盘子", "连阳高位") for b in blocked),
           "；".join("%s：%s" % (b[0], b[1]) for b in blocked) if blocked
           else ((pressure or {}).get("note") or "压力位通过")),
        _g(4, "仓位", not blocked, "起爆点→重仓" if is_ji else
           ("非起爆点·概念强→五成" if strong else "非起爆点·概念一般→三成")),
    ]

    return {"gates": gates, "verdict": "回避" if blocked else "通过",
            "position": pos, "positionTone": pos_tone,
            "blocked": [b[0] for b in blocked],
            "blockedDetail": ["%s：%s" % (b[0], b[1]) for b in blocked],
            "scoreAdjust": adj, "adjustNotes": notes,
            "manualChecks": manual, "unknownChecks": unknown}


def judge_strategy(feats, board, rules=None, ctx=None):
    """手册规则流水线总入口：阶梯 → 压力位/庄股 → 连阳/逆势 → 六维 → 四关 → 仓位。

    ctx 约定：floatCapYi / price / sectorZt / sectorCloses / sectorStaleDays /
             aboveMa60 / breakMa60 / jipoint（是否处于起爆点近似）
    """
    if not feats:
        return None
    r = rules or LADDER_RULES
    ctx = dict(ctx or {})
    turns = feats.get("turnsHistory") or []
    closes = feats.get("closesHistory") or []
    vols = feats.get("volsHistory") or []
    ladder = judge_ladder(board, feats.get("turnoverSeq") or [], vols, r)
    pressure = judge_pressure_level(turns, board, r, closes)
    zhuang = judge_zhuanggu(turns, closes, board, r)
    lianyang = judge_lianyang(closes, r)
    counter = judge_counter_trend(ctx.get("sectorCloses") or [], r,
                                  ctx.get("sectorStaleDays"))
    gate6 = _six_dim(feats, board, ctx, r, pressure, zhuang)
    four = _four_gate(ladder, pressure, lianyang, counter, gate6, ctx, r)
    return {"ladder": ladder, "pressure": pressure, "zhuang": zhuang,
            "lianyang": lianyang, "counterTrend": counter, "gate6": gate6,
            "fourGate": four, "exit": dict(EXIT_PLAN), "scoreAdjust": four.get("scoreAdjust") or 0}


