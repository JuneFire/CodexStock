# -*- coding: utf-8 -*-
"""悟道 MCP 竞价数据接入（服务器端）。

用途
----
腾讯快照在 09:25:0x 常常还没把「成交额」刷新完，导致竞价额最大的若干只被
`amount<=0` 过滤掉（实测 10-28 漏了 19 只、10-22 漏 4 只、10-23 漏 3 只）。
悟道这一路数据齐全且**带腾讯/东财都没有的字段**：
  几天几板 prevStreak / 昨日板型 prevLimitUpType / 尾秒 openBidPct+openBidSignal /
  题材 themes / 自由流通换手 turnoverRateF / 涨停委买额三时点。

设计原则
--------
1. **绝不拖垮主链路**：所有函数都不抛异常给调用方（内部 try/except 后返回 []）。
2. **一次调用拿尽量多**：用 auction_opening_snapshot，一次响应含 8 个分桶，
   合并去重后能覆盖 100+ 只（单桶 topBidAmount 只有 25 行，不够）。
   auction_market_scan 实测单次硬上限 30 行，don't bother。
3. **只做增强，不做替代**：腾讯那 60 行是基座，悟道负责补票 + 补字段。

配置
----
优先环境变量 `WUDAO_API_KEY`，其次 `config.json` 的 `wudao.apiKey`。
"""
import io
import json
import os
import urllib.error
import urllib.request

MCP_URL = "https://stock.quicktiny.cn/api/mcp"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def load_key():
    """读悟道 API Key：环境变量优先，其次 config.json。"""
    key = (os.environ.get("WUDAO_API_KEY") or "").strip()
    if key:
        return key
    try:
        with io.open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        key = str(((cfg.get("wudao") or {}).get("apiKey")) or "").strip()
        return key or ""
    except Exception:
        return ""


def call(tool, arguments, key=None, timeout=60):
    """调一次悟道 MCP 工具，返回 data dict。失败抛异常（由上层兜）。"""
    key = key or load_key()
    if not key:
        raise RuntimeError("未配置悟道 API Key（环境变量 WUDAO_API_KEY 或 config.json 的 wudao.apiKey）")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError("JSON-RPC error: %s" % json.dumps(payload["error"], ensure_ascii=False)[:200])
    result = payload.get("result") or {}
    sc = result.get("structuredContent")
    if isinstance(sc, dict) and sc:
        if sc.get("success") is False:
            raise RuntimeError("工具返回失败: %s" % str(sc)[:200])
        return sc.get("data") if isinstance(sc.get("data"), dict) else sc
    content = result.get("content") or []
    text = (content[0].get("text") if content else "") or ""
    if not text.strip():
        raise RuntimeError("空响应")
    return json.loads(text)


def fetch_payload(trade_date, key=None, limit=100, prev_pool_limit=300):
    """一次调用取当日竞价全景（含 8 个分桶）。trade_date 支持 YYYY-MM-DD / YYYYMMDD。"""
    return call("auction_opening_snapshot", {
        "tradeDate": trade_date,
        "limit": limit,
        "prevPoolLimit": prev_pool_limit,
        "detailLevel": "standard",
    }, key=key)


def rows_from_payload(payload):
    """把 8 个分桶的行并成一张表（按 code 去重，只留 bidAmount>0 的）。

    单桶 topBidAmount 会被服务端截断到 25 行，但同一份响应里还有：
    limitUpOpen / limitDownOpen / topLimitBuyAmount / lateBidUp / lateBidDown /
    prevLimitUpFeedback / prevBrokenFeedback。
    反馈池里未参与竞价的票（bidAmount 为空/0）不能进竞价榜。
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    rows = list(data.get("rows") or [])
    seen = {str(r.get("code")) for r in rows if r.get("code")}
    for _name, bucket in (data.get("buckets") or {}).items():
        for r in ((bucket or {}).get("rows") or []):
            code = str(r.get("code") or "")
            if not code or code in seen:
                continue
            if not _num(r.get("bidAmount")):
                continue
            seen.add(code)
            rows.append(r)
    return rows


def to_local_row(src):
    """悟道行 → 本项目 auction_top 记录（字段名对齐 fetch_auction_top）。"""
    price = _num(src.get("price"))
    prev_close = _num(src.get("preClose"))
    return {
        # —— 与腾讯源一致的字段 ——
        "code": src.get("code"),
        "name": src.get("name"),
        "industry": src.get("industry") or "其他",
        "price": price,
        "changePct": _num(src.get("changeRate")),
        "open": price,
        "prevClose": prev_close,
        "auctionAmount": _num(src.get("bidAmount")) or 0.0,
        "auctionVolume": _num(src.get("bidVolHands")),
        "turnover": _num(src.get("turnoverRate")),
        "volumeRatio": _num(src.get("volumeRatio")),
        "floatCap": _num(src.get("circMcap")),
        "totalCap": None,
        # 封单额取 09:25 涨停委买额终值；不能用 finalSealAmount（盘后截面）
        "sealAmount": _num(src.get("limitBuyAmount")),
        # —— 悟道独有 ——
        "ps": src.get("prevStreak"),
        "plt": src.get("prevLimitUpType") or None,
        "obp": _num(src.get("openBidPct")),
        "obs": src.get("openBidSignal") or None,
        "themes": src.get("themes") or [],
        "trf": _num(src.get("turnoverRateF")),
        "limitPct": _num(src.get("limitPct")),
        "limitUpPrice": _num(src.get("limitUpPrice")),
        "bidStrength": _num(src.get("bidStrength")),
        "isLimitUpOpen": bool(src.get("isLimitUpOpen")),
        "status": src.get("status"),
        "market": src.get("market"),
        "preLimitBuyAmount": _num(src.get("preLimitBuyAmount")),
        "limitBuyAmountAfter920": _num(src.get("limitBuyAmountAfter920")),
        "limitBuyAmount": _num(src.get("limitBuyAmount")),
        "finalSealAmount": _num(src.get("finalSealAmount")),
        "src": "wudao",
    }


# 合并时允许从悟道回填的字段（缺/空才填，腾讯已有的金额、价格不覆盖）
_FILL_KEYS = (
    "ps", "plt", "obp", "obs", "themes", "trf", "limitPct", "limitUpPrice",
    "bidStrength", "isLimitUpOpen", "status", "market",
    "preLimitBuyAmount", "limitBuyAmountAfter920", "limitBuyAmount",
    "finalSealAmount", "prevStreak", "prevLimitUpType",
)


def merge_into(base_rows, wudao_rows):
    """把悟道行并进腾讯基座：同 code 只补字段，新 code 整行追加。

    返回 (merged_rows, stats)。不修改入参。
    """
    merged = [dict(r) for r in base_rows]
    index = {str(r.get("code")): r for r in merged}
    added = enriched = 0
    for w in wudao_rows:
        code = str(w.get("code") or "")
        if not code:
            continue
        local = to_local_row(w)
        cur = index.get(code)
        if cur is None:
            merged.append(local)
            index[code] = local
            added += 1
            continue
        for k in _FILL_KEYS:
            v = local.get(k)
            if v in (None, "", []) or v == 0:
                continue
            if cur.get(k) in (None, "", [], 0):
                cur[k] = v
        # 腾讯若把封单额算成 0/缺失，用悟道的涨停委买额补
        if not cur.get("sealAmount") and local.get("sealAmount"):
            cur["sealAmount"] = local["sealAmount"]
        enriched += 1

    merged.sort(key=lambda r: (r.get("auctionAmount") or 0), reverse=True)
    for i, r in enumerate(merged, start=1):
        r["rank"] = i
    return merged, {"tencent": len(base_rows), "wudao": len(wudao_rows),
                    "added": added, "enriched": enriched, "total": len(merged)}


def fetch_and_merge(base_rows, trade_date, key=None):
    """取悟道当日数据并并进基座。任何异常都返回原基座 + error 说明。"""
    try:
        payload = fetch_payload(trade_date, key=key)
        wrows = rows_from_payload(payload)
        if not wrows:
            return base_rows, {"error": "悟道返回空"}
        merged, stats = merge_into(base_rows, wrows)
        return merged, stats
    except Exception as exc:
        return base_rows, {"error": "%s: %s" % (type(exc).__name__, exc)}
