# -*- coding: utf-8 -*-
"""赚钱环境打分引擎（纯计算，无网络）。

输入 signals（由 server._compute_day_signals 产出）+ rules（data/env_rules.json，可选），
输出四类环境分数与结论。全部阈值集中在 rules，便于用户调整。

四类环境：
  trend 趋势票赚钱 / short 短线赚钱 / group 抱团赚钱 / chaos 混乱轮动（不赚钱）
"""
import json
import os

# 默认规则（env_rules.json 缺失时使用；顶层字段与文件合并，缺项回落到此）
DEFAULT_RULES = {
    "version": 1,
    "activateFloor": 45,   # 主结论最低分
    "mixGap": 10,          # 主/次分差 <= 此值 且次分>=mixFloor → 输出伴生类
    "mixFloor": 35,
    "short": {
        "promoteRate": [[0.40, 30], [0.25, 20], [0.15, 10]],   # [阈值, 分] 降序
        "premium": [[2, 20], [0, 12], [-2, 6]],
        "tierHigh": 20, "tierMid": 14, "tierLb2": 8,           # 高度梯队
        "zbHealthy": [[25, 15], [35, 8]],                      # 炸板率越低越好(<=)
        "ztScale": [[60, 15], [40, 9]],
        "ztRising": 5,                                         # 涨停较昨不降
    },
    "trend": {
        "idxAbove20": [[2, 25], [1, 15]],
        "ma20Up": 5, "redBroad": [[0.58, 20], [0.52, 12]],
        "upDownDiff": 1500, "diffBonus": 5,
        "headN": [[3, 15], [2, 9]], "sectorJaccard": 0.33, "jaccardBonus": 10,
        "sectorCount": 8, "sectorsGe3": 3, "breadthBonus": 15,
        "healthyTurnover": 0.35, "healthyZb": 30, "healthyBonus": 15,
        "amountYi": [[15000, 15], [9000, 10]],
    },
    "group": {
        "top10Share": [[15, 25], [12, 15]], "top20Share": 25, "top20Bonus": 5,
        "diverge": [[0.45, 25], [0.50, 12]], "divergeZt": 35,
        "dragonTier": 3, "dragonPromote": 0.25, "dragonBonus": 15, "dragonLb3p": 5,
        "mainlineGe4Max": 2, "top1Share": 0.25, "convergeBonus": 15,
        "stockAmountMax": 14000, "stockChgMax": 15, "stockBonus": 10,
    },
    "chaos": {
        "jaccard": [[0.20, 25], [0.33, 15]],                   # 重合度越低越混乱(<=)
        "leaderGoneZt": 1, "leaderGoneBonus": 20,
        "zbRate": [[40, 20], [30, 10]],                        # 炸板率越高越乱(>=)
        "premium": [[-2, 20], [-0.5, 10]],                     # 溢价越低越亏(<=)
        "collapseRate": 0.10, "collapseBonus": 15, "collapseZt": 40,
    },
    "labels": {
        "trend": "趋势环境", "short": "短线环境", "group": "抱团环境", "chaos": "混乱轮动",
    },
    "advice": {
        "trend": "适合趋势低吸：沿均线做主线，少打板",
        "short": "适合短线：打板/低吸有肉，注意梯队与晋级",
        "group": "抱团为主：资金集中核心，做龙头不做跟风",
        "chaos": "轮动电风扇：不赚钱，控仓/空仓防守",
    },
    "tone": {"trend": "pos", "short": "warn", "group": "warn", "chaos": "neg"},
}


def _deep_merge(base, over):
    """over 覆盖 base（dict 递归合并）。"""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_rules(path=None):
    """读 env_rules.json 与内置默认合并。文件缺失/损坏 → 用默认。"""
    rules = json.loads(json.dumps(DEFAULT_RULES))  # 深拷贝
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
            rules = _deep_merge(rules, user)
        except Exception:
            pass
    return rules


def _ge(value, tiers, default=0):
    """降序阈值档：value>=阈值 取对应分（第一个命中）。tiers=[[th,score],...]"""
    if value is None:
        return 0
    for th, sc in tiers:
        if value >= th:
            return sc
    return default


def _le(value, tiers, default=0):
    """升序阈值档：value<=阈值 取分。用于'越低越好'（炸板率/溢价）。"""
    if value is None:
        return 0
    for th, sc in tiers:
        if value <= th:
            return sc
    return default


def score_short(s, r):
    c = r["short"]
    parts = []
    sc = _ge(s["promoteRate"], c["promoteRate"])
    parts.append(("连板晋级率", s["promoteRate"], sc))
    sc += _ge(s["avgPremium"], c["premium"])
    parts.append(("隔日溢价", s["avgPremium"], _ge(s["avgPremium"], c["premium"])))
    # 高度梯队
    if (s["maxTier"] or 0) >= 4 and (s["lb3p"] or 0) >= 1:
        t = c["tierHigh"]
    elif (s["maxTier"] or 0) == 3 and (s["lb3p"] or 0) >= 1:
        t = c["tierMid"]
    elif (s["lb2"] or 0) >= 8:
        t = c["tierLb2"]
    else:
        t = 0
    sc += t
    parts.append(("高度梯队", s["maxTier"], t))
    zb = _le(s["zbRate"], c["zbHealthy"])
    sc += zb
    parts.append(("炸板健康", s["zbRate"], zb))
    zsc = _ge(s["zt"], c["ztScale"])
    if s["ztChangePct"] is not None and s["ztChangePct"] >= 0:
        zsc += c["ztRising"]
    sc += zsc
    parts.append(("涨停规模", s["zt"], zsc))
    return min(sc, 100), parts


def score_trend(s, r):
    c = r["trend"]
    sc = _ge(s["idxAbove20"], c["idxAbove20"])
    if s["ma20Up"]:
        sc += c["ma20Up"]
    # 红盘广度
    broad = _ge(s["redRatio"], c["redBroad"])
    diff = (s["red"] or 0) - (s["green"] or 0)
    if diff > c["upDownDiff"]:
        broad += c["diffBonus"]
    sc += broad
    # 主线/头马
    main = _ge(s["headN"], c["headN"])
    if s["jaccard"] is not None and s["jaccard"] >= c["sectorJaccard"]:
        main += c["jaccardBonus"]
    sc += main
    # 题材广度
    if (s["sectorCount"] or 0) >= c["sectorCount"] and (s["sectorsGe3"] or 0) >= c["sectorsGe3"]:
        sc += c["breadthBonus"]
    # 结构健康：趋势不靠打板
    if s["promoteRate"] is not None and s["promoteRate"] < c["healthyTurnover"] and \
       s["zbRate"] is not None and s["zbRate"] <= c["healthyZb"]:
        sc += c["healthyBonus"]
    # 量能
    sc += _ge(s["amountYi"], c["amountYi"])
    return min(sc, 100), []


def score_group(s, r):
    c = r["group"]
    sc = _ge(s["top10Share"], c["top10Share"])
    if s["top20Share"] is not None and s["top20Share"] >= c["top20Share"]:
        sc += c["top20Bonus"]
    # 红绿背离
    if s["redRatio"] is not None and (s["zt"] or 0) >= c["divergeZt"]:
        sc += _le(s["redRatio"], c["diverge"])
    # 龙头高度
    if (s["maxTier"] or 0) >= c["dragonTier"] and s["promoteRate"] is not None and s["promoteRate"] >= c["dragonPromote"]:
        sc += c["dragonBonus"]
    if (s["lb3p"] or 0) >= 1:
        sc += c["dragonLb3p"]
    # 主线收敛
    if (s["sectorsGe4"] or 0) <= c["mainlineGe4Max"] and (s["top1ZtShare"] or 0) >= c["top1Share"]:
        sc += c["convergeBonus"]
    # 存量特征
    if s["amountYi"] is not None and s["amountYi"] < c["stockAmountMax"] and \
       (s["amountChgPct"] is None or s["amountChgPct"] < c["stockChgMax"]):
        sc += c["stockBonus"]
    return min(sc, 100), []


def score_chaos(s, r):
    c = r["chaos"]
    sc = _le(s["jaccard"], c["jaccard"])  # 重合度低=一日游
    if s["prevLeaderTodayZt"] is not None and s["prevLeaderTodayZt"] <= c["leaderGoneZt"]:
        sc += c["leaderGoneBonus"]
    sc += _ge(s["zbRate"], c["zbRate"])  # 炸板率高
    sc += _le(s["avgPremium"], c["premium"])  # 追涨亏钱
    # 高度坍塌
    collapse = False
    if s["promoteRate"] is not None and s["promoteRate"] < c["collapseRate"]:
        collapse = True
    if (s["lb3p"] or 0) == 0 and (s["zt"] or 0) < c["collapseZt"]:
        collapse = True
    if collapse:
        sc += c["collapseBonus"]
    return min(sc, 100), []


def compute(signals, rules=None):
    """signals（S1..S6）→ {scores, conclusion}。"""
    rules = rules or load_rules()
    s1, s2, s3, s4, s5, s6 = (signals.get(k) or {} for k in ("S1", "S2", "S3", "S4", "S5", "S6"))
    zt = s1.get("zt") or 0
    flat = {
        "zt": zt, "ztReal": s1.get("ztReal"), "dt": s1.get("dt"), "zb": s1.get("zb"),
        "zbRate": s1.get("zbRate"), "lb2": s1.get("lb2"), "lb3": s1.get("lb3"),
        "lb3p": s1.get("lb3p"), "maxTier": s1.get("maxTier"), "ztChangePct": s1.get("ztChangePct"),
        "red": s2.get("red"), "green": s2.get("green"), "redRatio": s2.get("redRatio"),
        "amountYi": s2.get("amountYi"), "amountChgPct": s2.get("amountChgPct"),
        "promoteRate": s3.get("promoteRate"), "first2Rate": s3.get("first2Rate"),
        "lbPromoteRate": s3.get("lbPromoteRate"), "avgPremium": s3.get("avgPremium"),
        "lbAvgPremium": s3.get("lbAvgPremium"),
        "jaccard": s4.get("jaccard"), "prevLeaderTodayZt": s4.get("prevLeaderTodayZt"),
        "sectorCount": s4.get("sectorCount"), "sectorsGe3": s4.get("sectorsGe3"),
        "sectorsGe4": s4.get("sectorsGe4"), "top1ZtShare": s4.get("top1ZtShare"),
        "top10Share": s5.get("top10Share"), "top20Share": s5.get("top20Share"),
        "idxAbove20": s6.get("idxAbove20"), "idxAbove60": s6.get("idxAbove60"),
        "ma20Up": s6.get("ma20Up"), "headN": s6.get("headN"),
    }
    short_sc, short_parts = score_short(flat, rules)
    trend_sc, _ = score_trend(flat, rules)
    group_sc, _ = score_group(flat, rules)
    chaos_sc, _ = score_chaos(flat, rules)
    scores = {"short": short_sc, "trend": trend_sc, "group": group_sc, "chaos": chaos_sc}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top_val = ranked[0]
    second_key, second_val = ranked[1]
    labels = rules["labels"]
    advice = rules["advice"]
    tone_map = rules["tone"]

    floor, gap, mix_floor = rules["activateFloor"], rules["mixGap"], rules["mixFloor"]
    if top_val >= floor:
        state = labels[top_key]
        if second_val >= mix_floor and (top_val - second_val) <= gap:
            label = "%s为主·%s伴生" % (labels[top_key], labels[second_key])
            adv = advice[top_key] + "；兼有" + labels[second_key]
            confidence = "medium"
        else:
            label = labels[top_key]
            adv = advice[top_key]
            confidence = "high" if top_val >= 60 else "medium"
        return {"scores": scores, "conclusion": {
            "state": state, "label": label, "tone": tone_map.get(top_key, "warn"),
            "advice": adv, "top": top_key, "second": second_key,
            "topScore": top_val, "secondScore": second_val, "confidence": confidence,
            "components": [{"name": n, "value": v, "score": sc} for n, v, sc in short_parts] if top_key == "short" else [],
        }}
    # 无明显赚钱环境 → 防守
    return {"scores": scores, "conclusion": {
        "state": "防守", "label": "无明显赚钱环境", "tone": "ice",
        "advice": "各环境均不突出，防守优先，控仓等主线明确",
        "top": top_key, "second": second_key,
        "topScore": top_val, "secondScore": second_val, "confidence": "low", "components": [],
    }}
