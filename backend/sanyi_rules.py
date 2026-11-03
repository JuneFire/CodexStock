# -*- coding: utf-8 -*-
"""竞价三一的持仓台账 + 卖出规则（B 规则：涨停接力 + 分档卖出）。

策略链路
--------
D 日 09:25   同花顺全市场竞价快照 → 剔除流通市值 >3000 亿（次新豁免）
             → 金额前三 且 换手第一 = 三一 → 当日开盘（竞价）价买入
D 日 15:05   记录 D 日收盘是否封板 → ``dLu``
D+1 09:25    ``dLu`` 为真且竞价高开 ≥ GAP_SELL_TH% → 竞价卖出（分档）
D+k 15:05    收盘封住涨停 → 续持；第一根没封住涨停 → 当日收盘卖出（接力）

回测（242 交易日 / 开关开 40 笔 / 1 万元本金复利）
--------------------------------------------------
    纯竞价卖出（D+1 竞价卖）    ×2.13   回撤 37.4%
    次日收盘卖                 ×3.32   回撤 23.0%
    本规则（接力 + 分档）       ×5.42   回撤 23.0%
详见 ``reports/三一竞价/竞价三一_接力持有回测_242日.md``。

本模块无第三方依赖，也不 import server，便于离线回放与单测。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime

# ---------------------------------------------------------------- 常量

GAP_SELL_TH = 6.0          # 次日竞价高开 ≥ 6% 卖竞价（回测 5~8% 为平台区）
MAX_HOLD = 15              # 最长接力天数，防数据异常导致永不出清
SKIP_GAP_FRAC = 0.94       # 竞价涨幅 ≥ 涨停幅×0.94 视为开盘即涨停、买不进

# S5 加注标记（L4）：S5 命中且竞价跌幅 ≤ S5_DIP_TH，即「跌停开盘」。
# 依据 reports/三一竞价/sanyi_s5_vs_l4.json（242 日 / 56 笔三一）：
#     S5 ∩ L4   6 笔  均值 +13.13%  胜率 100%  最差 +5.42%
#     S5 独有  19 笔  均值  +6.06%  胜率  79%
#     L4 单独  10 笔  均值 +11.25%  胜率  90%（含唯一亏损笔 600598 北大荒 -4.72%）
# 它**不改变 S5 的入选条件**，只做加注提醒。阈值沿用回测口径 -9.0：
# 创业板/科创板 -9% 其实还没到跌停，但回测就是按 -9 切的，保持一致。
S5_DIP_TH = -9.0

RULE_SHORT = "D日开盘买 · 涨停接力 · 次日高开≥%g%%卖竞价" % GAP_SELL_TH
RULE_NOTE = (
    "买入 D 日开盘（竞价）价。卖出：① D 日收盘封板且次日竞价高开 ≥" + ("%g" % GAP_SELL_TH) + "%，次日竞价卖；"
    "② 否则收盘封住涨停就续持，第一根没封住涨停的当天收盘卖。"
    "242 日回测（开关开 40 笔，1 万复利）：×5.42、回撤 23.0%；"
    "对照纯竞价卖出 ×2.13、回撤 37.4%。"
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "data", "sanyi")
_PATH = os.path.join(_DIR, "positions.json")
_lock = threading.RLock()


# ---------------------------------------------------------------- 小工具

def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f else default          # 过滤 NaN


def limit_pct(code, name=""):
    """涨跌幅限制（%）。与 backend/server.py 的 _limit_pct_for 保持一致。"""
    code = str(code or "")
    name = str(name or "")
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("4", "8")):
        return 30.0
    if "ST" in name.upper():
        return 5.0
    return 10.0


def limit_up_price(prev_close, code="", name=""):
    """涨停价（2 位小数）。prev_close 非法返回 None。"""
    pc = _num(prev_close)
    if not pc or pc <= 0:
        return None
    return round(pc * (1 + limit_pct(code, name) / 100.0), 2)


def is_close_lu(close, prev_close, code="", name=""):
    """收盘价是否封住涨停（容差 0.005）。"""
    px, lpx = _num(close), limit_up_price(prev_close, code, name)
    if px is None or lpx is None:
        return None
    return px >= lpx - 0.005


def gap_pct(auction_px, prev_close):
    """竞价/开盘涨幅（%）。"""
    px, pc = _num(auction_px), _num(prev_close)
    if not px or not pc:
        return None
    return (px / pc - 1) * 100


def is_dip(rec, th=S5_DIP_TH):
    """竞价跌幅 ≤ th（跌停开盘）—— L4 判定，不看是否三一。

    返回值只回答「今天是不是大幅低开」，环境开关由调用方另外判。
    """
    if not rec:
        return False
    chg = _num(rec.get("changePct"))
    if chg is None:
        chg = _num(rec.get("chg"))
    return chg is not None and chg <= th


def is_s5_dip(rec, th=S5_DIP_TH):
    """S5 命中且竞价跌幅 ≤ th —— S5 内部的 L4 加注标记。

    只做提醒，不参与入选判定：`s5` 为假时恒为假。
    """
    if not rec:
        return False
    hit = rec.get("s5")
    if hit is None:
        hit = rec.get("isSanyi")
    if not hit:
        return False
    return is_dip(rec, th)


# ---------------------------------------------------------------- 台账

def _empty():
    return {"updatedAt": "", "rule": RULE_NOTE, "open": [], "closed": []}


def load():
    """读台账；文件缺失/损坏返回空台账（绝不抛）。"""
    with _lock:
        if not os.path.isfile(_PATH):
            return _empty()
        try:
            with open(_PATH, encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            return _empty()
    data.setdefault("open", [])
    data.setdefault("closed", [])
    data.setdefault("updatedAt", "")
    data.setdefault("rule", RULE_NOTE)
    data["open"] = [p for p in data["open"] if isinstance(p, dict) and p.get("status") == "open"]
    data["closed"] = [p for p in data["closed"] if isinstance(p, dict)]
    return data


def save(state):
    """原子写台账。返回 (ok, msg)。"""
    with _lock:
        try:
            os.makedirs(_DIR, exist_ok=True)
            state["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tmp = _PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _PATH)
            return True, _PATH
        except OSError as exc:
            return False, repr(exc)


def find_open(state, code, buy_date=None):
    for p in state.get("open") or []:
        if str(p.get("code")) == str(code) and (buy_date is None or p.get("buyDate") == buy_date):
            return p
    return None


def find_closed(state, code, buy_date):
    for p in state.get("closed") or []:
        if str(p.get("code")) == str(code) and p.get("buyDate") == buy_date:
            return p
    return None


# ---------------------------------------------------------------- 买入

def open_position(state, rec, date_str, buy_px=None, quotes=None):
    """登记一笔买入。返回 (pos | None, reason)。

    rec 为竞价榜记录（含 code/name/auctionAmount/changePct/prevClose/limitPct/
    amtRankFull/trfRankFull/floatCap/turnover/themes/prevStreak）。
    buy_px 缺省用 rec 的竞价价（price/open）。
    quotes 提供 {code: {prevClose}} 时用于判「开盘即涨停买不进」。
    """
    code = str(rec.get("code") or "").zfill(6) if rec.get("code") else ""
    if not code:
        return None, "无代码"
    if find_open(state, code, date_str) or find_closed(state, code, date_str):
        return None, "已登记"

    px = _num(buy_px) or _num(rec.get("price")) or _num(rec.get("open"))
    prev_close = _num((quotes or {}).get(code, {}).get("prevClose")) or _num(rec.get("prevClose"))
    if not px:
        return None, "无买入价"

    # 开盘即涨停 → 买不进（回测同口径剔除）
    pct = _num(rec.get("limitPct")) or limit_pct(code, rec.get("name") or "")
    gap = gap_pct(px, prev_close)
    if gap is not None and gap >= pct * SKIP_GAP_FRAC:
        return None, "开盘即涨停买不进（%+.2f%%）" % gap

    pos = {
        "code": code,
        "name": str(rec.get("name") or ""),
        "buyDate": date_str,
        "buyPx": round(px, 3),
        "prevClose": prev_close,
        "gapPct": None if gap is None else round(gap, 2),
        "amtYi": round((_num(rec.get("auctionAmount"), 0) or 0) / 1e8, 3),
        "amtRank": rec.get("amtRankFull"),
        "trfRank": rec.get("trfRankFull"),
        "chgPct": _num(rec.get("changePct")),
        "turnover": _num(rec.get("turnover")),
        "floatCapYi": round((_num(rec.get("floatCap"), 0) or 0) / 1e8, 1),
        "themes": rec.get("themes") or [],
        "prevStreak": rec.get("prevStreak"),
        "limitPct": pct,
        "dLu": None,          # 买入日收盘是否封板
        "dLuDate": None,
        "seq": [],            # [{"date","close","prevClose","chgPct","lu"}]
        "status": "open",
        "openedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    state.setdefault("open", []).append(pos)
    return pos, "ok"


# ---------------------------------------------------------------- 卖出

def _sell(state, pos, date_str, px, why, phase):
    """把持仓从 open 移到 closed，算收益率。"""
    buy = _num(pos.get("buyPx")) or 0
    px = _num(px)
    ret = None if (not px or not buy) else (px / buy - 1) * 100
    pos["status"] = "closed"
    pos["exitDate"] = date_str
    pos["exitPx"] = None if px is None else round(px, 3)
    pos["exitWhy"] = why
    pos["exitPhase"] = phase
    # 竞价卖出发生在开盘，seq 只到今天之前；收盘卖出已把当日 bar 写进 seq
    n = len(pos.get("seq") or [])
    pos["holdDays"] = max(1, n if phase == "open" else n - 1)
    pos["retPct"] = None if ret is None else round(ret, 2)
    pos["closedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        state["open"].remove(pos)
    except ValueError:
        pass
    state.setdefault("closed", []).append(pos)
    return pos


def decide_open(state, date_str, quotes):
    """09:25 时点：分档卖出（D 日封板 + 今日竞价高开 ≥6% → 竞价卖）。

    quotes: {code: {"price": 今日竞价价, "prevClose": 昨收}}
    返回 [{code,name,buyDate,buyPx,exitPx,retPct,why}]；已就地更新台账。
    """
    out = []
    for pos in list(state.get("open") or []):
        if pos.get("buyDate") == date_str:
            continue                                    # 当日买入不当日卖
        q = quotes.get(str(pos.get("code"))) or {}
        px = _num(q.get("price")) or _num(q.get("close"))
        pc = _num(q.get("prevClose"))
        if not px:
            continue
        gap = gap_pct(px, pc) if pc else None
        if pos.get("dLu") and gap is not None and gap >= GAP_SELL_TH:
            why = "D日封板+次日高开%+.1f%%≥%g%%→竞价卖" % (gap, GAP_SELL_TH)
            out.append(dict(_sell(state, pos, date_str, px, why, "open")))
    return out


def decide_close(state, date_str, quotes):
    """15:05 时点：标记封板状态 + 接力断板卖出。

    quotes: {code: {"close": 当日收盘, "prevClose": 昨收}}
    返回 [{code,name,buyDate,buyPx,exitPx,retPct,why}]；已就地更新台账。
    """
    out = []
    for pos in list(state.get("open") or []):
        code = str(pos.get("code"))
        q = quotes.get(code) or {}
        close = _num(q.get("close"))
        pc = _num(q.get("prevClose"))
        if close is None:
            continue
        lu = is_close_lu(close, pc or pos.get("prevClose"), code, pos.get("name") or "")
        chg = gap_pct(close, pc) if pc else None

        pos.setdefault("seq", []).append({
            "date": date_str,
            "close": round(close, 3),
            "prevClose": None if pc is None else round(pc, 3),
            "chgPct": None if chg is None else round(chg, 2),
            "lu": lu,
        })

        if pos.get("buyDate") == date_str:
            # 买入日：只记录是否封板，供次日分档判断
            pos["dLu"] = lu
            pos["dLuDate"] = date_str
            continue
        if lu:
            continue                                    # 封住涨停 → 续持
        held = len(pos.get("seq") or []) - 1
        if held <= 1:
            why = "次日收盘卖（D+1 未封板）"
        else:
            why = "涨停接力 %d 日后断板收盘卖" % held
        out.append(dict(_sell(state, pos, date_str, close, why, "close")))
    return out


def close_position(state, code, buy_date, date_str, px, why="人工登记卖出", phase="manual"):
    """人工登记卖出（不经过规则判断）。返回 pos | None。"""
    pos = find_open(state, code, buy_date)
    if not pos:
        return None
    return _sell(state, pos, date_str, px, why or "人工登记卖出", phase)


def delete_position(state, code, buy_date=None):
    """删除误登记的持仓（只删 open，不动 closed）。返回 True/False。"""
    pos = find_open(state, code, buy_date)
    if not pos:
        return False
    try:
        state["open"].remove(pos)
    except ValueError:
        return False
    return True


def expire(state, date_str, max_hold=MAX_HOLD):
    """兜底：持有超过 max_hold 天仍未触发卖出的，按缺价止损出清（不应发生）。"""
    out = []
    for pos in list(state.get("open") or []):
        if len(pos.get("seq") or []) < max_hold:
            continue
        last = (pos.get("seq") or [{}])[-1]
        out.append(dict(_sell(state, pos, date_str, last.get("close"),
                              "超 %d 日兜底出清" % max_hold, "expire")))
    return out


# ---------------------------------------------------------------- 汇总

def summary(state=None):
    """持仓 + 关闭交易的汇总，供 API / 推送使用。"""
    st = state if state is not None else load()
    opens, closed = st.get("open") or [], st.get("closed") or []
    rets = [_num(p.get("retPct")) for p in closed if _num(p.get("retPct")) is not None]
    n = len(rets)
    eq = 1.0
    for r in rets:
        eq *= (1 + r / 100.0)
    return {
        "updatedAt": st.get("updatedAt"),
        "rule": st.get("rule"),
        "gapSellTh": GAP_SELL_TH,
        "openCount": len(opens),
        "closedCount": n,
        "winRate": (sum(1 for r in rets if r > 0) / n * 100) if n else None,
        "avgRet": (sum(rets) / n) if n else None,
        "equity": eq,
        "open": [{
            "code": p.get("code"), "name": p.get("name"), "buyDate": p.get("buyDate"),
            "buyPx": p.get("buyPx"), "dLu": p.get("dLu"),
            "holdDays": len(p.get("seq") or []),
            "amtYi": p.get("amtYi"), "amtRank": p.get("amtRank"), "trfRank": p.get("trfRank"),
            "themes": p.get("themes"),
        } for p in opens],
        "closed": sorted(closed, key=lambda p: (p.get("exitDate") or "", p.get("code") or ""),
                         reverse=True)[:40],
    }
