# -*- coding: utf-8 -*-
"""赚钱效应指数引擎（纯计算，无网络）。

双线：
  短线打板效应 = 隔日溢价50% + 连板晋级率30% + 首板2板率20%
  大盘赚钱效应 = 涨跌家数比40% + 涨跌停比30% + 量能较昨30%
缺项按剩余权重重归一，并标 partial。阈值全部外置 data/money_rules.json。
"""
import json
import os

DEFAULT_RULES = {
    "version": 1,
    "short": {
        "weights": {"premium": 0.50, "promote": 0.30, "first2": 0.20},
        "premiumRange": [-5.0, 5.0],     # 隔日溢价 % → 0..100
        "promoteRange": [0.0, 0.5],      # 连板晋级率 → 0..100
        "first2Range": [0.0, 0.5],       # 首板→2板率 → 0..100
    },
    "market": {
        "weights": {"breadth": 0.40, "limitRatio": 0.30, "amount": 0.30},
        "breadthRange": [0.2, 0.8],      # 涨跌家数比 up/(up+down) → 0..100
        "limitRange": [0.0, 0.5],        # 涨跌停比 zt/(zt+dt) → 0..100
        "amountRange": [-20.0, 20.0],    # 量能较昨 % → 0..100
    },
}


def load_rules(path=None):
    rules = json.loads(json.dumps(DEFAULT_RULES))
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in (user or {}).items():
                if isinstance(v, dict) and isinstance(rules.get(k), dict):
                    rules[k].update(v)
                else:
                    rules[k] = v
        except Exception:
            pass
    return rules


def norm(v, lo, hi):
    """线性映射 [lo,hi] → [0,100]，越界夹取；None 返回 None。"""
    if v is None:
        return None
    try:
        x = (float(v) - lo) / (hi - lo) * 100.0
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, x))


def _weighted(comps, weights):
    """按权重加权；缺项按剩余权重重归一。返回 (score|None, partial)。"""
    num = den = 0.0
    missing = False
    for k, w in weights.items():
        v = comps.get(k)
        if v is None:
            missing = True
            continue
        num += v * w
        den += w
    if den <= 0:
        return None, True
    return round(num / den, 1), missing


def money_short(day, rules=None):
    """短线打板效应 (0-100)。day 需含 avgPremium/promoteRate/first2Rate。"""
    r = (rules or DEFAULT_RULES)["short"]
    comps = {
        "premium": norm(day.get("avgPremium"), *r["premiumRange"]),
        "promote": norm(day.get("promoteRate"), *r["promoteRange"]),
        "first2": norm(day.get("first2Rate"), *r["first2Range"]),
    }
    score, partial = _weighted(comps, r["weights"])
    return score, {k: (round(v, 1) if v is not None else None) for k, v in comps.items()}, partial


def money_market(day, rules=None):
    """大盘赚钱效应 (0-100)。day 需含 up/down/zt/dt/amountChgPct。"""
    r = (rules or DEFAULT_RULES)["market"]
    up, down = day.get("up"), day.get("down")
    zt, dt = day.get("zt"), day.get("dt")
    breadth = None
    if up is not None and down is not None and (up + down) > 0:
        breadth = up / (up + down)
    limit_ratio = (zt / (zt + dt)) if (zt is not None and dt is not None and (zt + dt) > 0) else None
    comps = {
        "breadth": norm(breadth, *r["breadthRange"]),
        "limitRatio": norm(limit_ratio, *r["limitRange"]),
        "amount": norm(day.get("amountChgPct"), *r["amountRange"]),
    }
    score, partial = _weighted(comps, r["weights"])
    return score, {k: (round(v, 1) if v is not None else None) for k, v in comps.items()}, partial


def compute(day, rules=None):
    """整日：返回 {date, short, market, shortParts, marketParts, partial, raw}。"""
    rules = rules or load_rules()
    s_sc, s_parts, s_partial = money_short(day, rules)
    m_sc, m_parts, m_partial = money_market(day, rules)
    return {
        "date": day.get("date"),
        "short": s_sc,
        "market": m_sc,
        "shortParts": s_parts,
        "marketParts": m_parts,
        "partial": bool(s_partial or m_partial),
        "raw": {
            "avgPremium": day.get("avgPremium"), "promoteRate": day.get("promoteRate"),
            "first2Rate": day.get("first2Rate"), "up": day.get("up"), "down": day.get("down"),
            "zt": day.get("zt"), "dt": day.get("dt"), "amountChgPct": day.get("amountChgPct"),
        },
    }
