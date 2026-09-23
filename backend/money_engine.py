# -*- coding: utf-8 -*-
"""赚钱效应指数引擎（纯计算，无网络）。

三线：
  短线打板效应 = 隔日溢价50% + 连板晋级率30% + 首板2板率20%
  大盘赚钱效应 = 涨跌家数比40% + 涨跌停比30% + 量能较昨30%
  亏钱效应     = 跌停占比30% + 炸板率30% + 隔日溢价25% + 断板率15%（越高越亏）
缺项按剩余权重重归一，并标 partial。阈值全部外置 data/money_rules.json。

另含 alerts：情绪溢出 / 极致亏钱 两个提示条件（阈值同样外置）。
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
    "lose": {
        "weights": {"dtRatio": 0.30, "zbRate": 0.30, "premium": 0.25, "breakRate": 0.15},
        "dtRatioRange": [0.0, 0.35],     # 跌停占比 dt/(zt+dt) → 0..100
        "zbRateRange": [0.0, 0.55],      # 炸板率 zb/(zt+zb) → 0..100
        "premiumRange": [3.0, -1.5],     # 隔日溢价 % → 0..100（逆向，越负越亏）
        "breakRateRange": [0.40, 0.90],  # 断板率 1-连板晋级率 → 0..100
    },
    "alerts": {
        "overflow": {"shortMin": 80, "loseMax": 20},   # 情绪溢出：赚钱强且几乎不亏
        "extremeLose": {"loseMin": 75, "dtMin": 25},   # 极致亏钱：任一命中即报
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


def _lose_comps(day, r):
    """亏钱效应四个分项（缺项为 None）。day 需含 zt/dt/zb/avgPremium/promoteRate。"""
    zt, dt, zb = day.get("zt"), day.get("dt"), day.get("zb")
    promote = day.get("promoteRate")
    dt_ratio = (dt / (zt + dt)) if (zt is not None and dt is not None and (zt + dt) > 0) else None
    zb_rate = (zb / (zt + zb)) if (zt is not None and zb is not None and (zt + zb) > 0) else None
    break_rate = (1.0 - promote) if promote is not None else None
    return {
        "dtRatio": norm(dt_ratio, *r["dtRatioRange"]),
        "zbRate": norm(zb_rate, *r["zbRateRange"]),
        "premium": norm(day.get("avgPremium"), *r["premiumRange"]),
        "breakRate": norm(break_rate, *r["breakRateRange"]),
    }


def money_lose(day, rules=None):
    """亏钱效应 (0-100，越高越亏)。day 需含 zt/dt/zb/avgPremium/promoteRate。"""
    r = (rules or DEFAULT_RULES)["lose"]
    comps = _lose_comps(day, r)
    score, partial = _weighted(comps, r["weights"])
    return score, {k: (round(v, 1) if v is not None else None) for k, v in comps.items()}, partial


def score_lose(raw, zb, rules=None):
    """读取时补算亏钱效应：已落盘 raw + 当日 sentiment 的 zb。返回 (score, parts, partial)。

    历史日落的 money 文件里没有 lose 字段（且东财涨停池只有约两周窗口，补不回来），
    故在读取时用同口径现算，避免数据迁移。
    """
    day = dict(raw or {})
    day["zb"] = zb
    return money_lose(day, rules)


def detect_alerts(rows, rules=None):
    """逐日判定提示，返回命中列表（按 rows 顺序）。

    rows 每行需含 date/short/lose/loseParts/raw{dt}。
    仅在**亏钱效应自身分项齐全**时才判定：_weighted 会在缺项时静默重归一，
    缺 zb 的日子少一个分项也能算出高分。注意不能用整行的 partial 当闸门——
    那是 breadth(up/down) 缺失导致的，与亏钱效应无关。
    """
    rules = rules or load_rules()
    ov = (rules.get("alerts") or {}).get("overflow") or {}
    ex = (rules.get("alerts") or {}).get("extremeLose") or {}
    out = []
    for row in rows or []:
        lose = row.get("lose")
        parts = row.get("loseParts") or {}
        if lose is None or not parts or any(v is None for v in parts.values()):
            continue
        raw = row.get("raw") or {}
        short, dt = row.get("short"), raw.get("dt")
        if short is not None and short >= ov.get("shortMin", 80) and lose <= ov.get("loseMax", 20):
            out.append({
                "date": row.get("date"), "type": "overflow",
                "label": "赚钱效应情绪溢出", "tone": "pos",
                "advice": "情绪过热，关注次新（N/C）爆发",
                "detail": "短线打板效应 %s · 亏钱效应 %s" % (short, lose),
            })
            continue
        if lose >= ex.get("loseMin", 75) or (dt is not None and dt >= ex.get("dtMin", 25)):
            out.append({
                "date": row.get("date"), "type": "extremeLose",
                "label": "极致亏钱效应", "tone": "neg",
                "advice": "极致亏钱，关注反转·次新先行",
                "detail": "亏钱效应 %s · 跌停 %s 家" % (lose, dt if dt is not None else "—"),
            })
    return out


def compute(day, rules=None):
    """整日：返回 {date, short, market, lose, *Parts, partial, raw}。"""
    rules = rules or load_rules()
    s_sc, s_parts, s_partial = money_short(day, rules)
    m_sc, m_parts, m_partial = money_market(day, rules)
    l_sc, l_parts, l_partial = money_lose(day, rules)
    return {
        "date": day.get("date"),
        "short": s_sc,
        "market": m_sc,
        "lose": l_sc,
        "shortParts": s_parts,
        "marketParts": m_parts,
        "loseParts": l_parts,
        "partial": bool(s_partial or m_partial or l_partial),
        "raw": {
            "avgPremium": day.get("avgPremium"), "promoteRate": day.get("promoteRate"),
            "first2Rate": day.get("first2Rate"), "up": day.get("up"), "down": day.get("down"),
            "zt": day.get("zt"), "dt": day.get("dt"), "zb": day.get("zb"),
            "amountChgPct": day.get("amountChgPct"),
        },
    }
