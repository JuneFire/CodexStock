# -*- coding: utf-8 -*-
"""量化选股器本地服务

用法:
    python backend/server.py [--port 8010]

接口:
    GET /api/refresh   抓取东方财富竞价数据并计算超预期分数
    GET /api/latest    读取最近一次抓取结果
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import requests

try:
    import db
except ImportError:  # tests 从项目根导入 backend.server 时兼容
    from backend import db



try:
    import akshare as ak
except Exception:
    ak = None  # akshare 未安装时仍走东财直连


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LATEST_FILE = os.path.join(DATA_DIR, "latest.json")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

PUSH2_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "pn=1&pz=300&po=1&np=1&fltt=2&invt=2&fid=f6"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    "&fields=f2,f3,f5,f6,f8,f10,f12,f14,f20,f21,f100"
    "&ut=bd1d9ddb04089700cf9c27f6f7426281"
)
KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
    "secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
    "&fields1=f1,f2,f3,f4,f5,f6"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    "&klt=101&fqt=1&end=20500101&lmt=3"
)
INDEX_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get?"
    "fltt=2&secids=1.000001,0.399001,0.399006,1.000688"
    "&fields=f2,f3,f12,f14"
    "&ut=bd1d9ddb04089700cf9c27f6f7426281"
)
INDEX_NAMES = {"000001": "上证指数", "399001": "深证成指", "399006": "创业板指", "000688": "科创50"}
TX_INDEX_CODES = ["sh000001", "sz399001", "sz399006", "sh000688"]

# 腾讯行情单位：成交额=万元、市值=亿、日K换手=小数(0.0030=0.30%)
TX_AMOUNT_MULT = 1e4
TX_CAP_MULT = 1e8

# 新浪行业板块映射缓存：TTL 1 天，写入 data/sector_map.json
SECTOR_MAP_FILE = os.path.join(DATA_DIR, "sector_map.json")
SECTOR_MAP_TTL_DAYS = 1
_sector_map = {}

_lock = threading.Lock()
_fetch_lock = threading.Lock()
_review_lock = threading.Lock()
_trading_day_cache = {}  # 按日期缓存交易日判断结果，避免窗口内每次检查都请求接口

# 测试开关：--auto-anytime 时自动抓取不受 9:25-9:30 窗口限制（用于验证自动抓取+入库链路）
AUTO_ANYTIME = False

# 竞价金额/换手只在交易日 9:25 竞价结束后、开盘前有效，9:30 后 f6/f8 变为全天累计值
AUTO_FETCH_WINDOW = (9 * 60 + 25, 9 * 60 + 30)
AUTO_FETCH_TICK = 20
AUTO_FETCH_MAX_ATTEMPTS = 5  # 窗口内最多重试次数，避免高频抓取触发风控
AUTO_FETCH_IDLE_SECONDS = 300  # 当日抓取成功后静默等待，不再 20 秒反复检查

# 竞价封单额：三个抓取时点；封单额 = 买一量(手)×100×买一价(元)
SEAL_SAMPLE_TIMES = ["09:15", "09:20", "09:25"]
SEAL_BATCH_SIZE = 500       # 腾讯批量直连每批股票数
SEAL_TOP_N = 20             # 全市场封单额取前 N

# 腾讯 qt.gtimg.cn 字段索引
TX_NAME_IDX = 1
TX_LAST_PRICE_IDX, TX_PREV_CLOSE_IDX = 3, 4
TX_BID1_PRICE_IDX, TX_BID1_VOL_IDX = 9, 10
TX_ASK1_PRICE_IDX, TX_ASK1_VOL_IDX = 15, 16

_all_codes_cache = {"date": "", "codes": None}  # 全市场代码清单缓存（当日一次）


_session = requests.Session()
_session.headers.update({
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
})


def _warm_session():
    """先访问东财首页拿 Cookie，降低裸请求被风控的概率。"""
    if getattr(_session, "_warmed", False):
        return
    try:
        _session.get("https://quote.eastmoney.com/", timeout=10)
    except Exception:
        pass
    _session._warmed = True


def http_json(url, referer="https://quote.eastmoney.com/", retries=2):
    """带会话、浏览器头、指数退避的请求；网络被断开时自动重试。"""
    _warm_session()
    headers = {"Referer": referer}
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, headers=headers, timeout=10)
            if resp.status_code == 429:
                raise RuntimeError("Eastmoney 429 rate limited")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc


def to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "--", "None", "null"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def is_trading_day(now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    today = now.strftime("%Y-%m-%d")
    if today in _trading_day_cache:
        return _trading_day_cache[today]
    result = False
    try:  # 腾讯实时快照优先：竞价窗口内日K尚未收录当天，实时报价更可靠
        import io
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            df = ak.stock_zh_a_spot_tx()
        result = df is not None and not df.empty
    except Exception:
        try:  # 腾讯日线回退
            import io
            import contextlib
            start = (now - timedelta(days=14)).strftime("%Y%m%d")
            with contextlib.redirect_stderr(io.StringIO()):
                df = ak.stock_zh_a_hist_tx(symbol="sh000001", start_date=start,
                                           end_date=now.strftime("%Y%m%d"), adjust="")
            result = df is not None and not df.empty and str(df.iloc[-1]["date"]) == today
        except Exception:
            try:  # 东财回退（原逻辑）
                data = http_json(KLINE_URL.format(secid="1.000001"))
                klines = ((data.get("data") or {}).get("klines")) or []
                result = any((line.split(",")[0] if line else "") == today for line in klines)
            except Exception:
                result = True
    _trading_day_cache[today] = result
    return result


def auction_window_status(now=None, strict=True):
    now = now or datetime.now()
    if not strict:
        return True, ""
    if AUTO_ANYTIME:
        return True, ""  # 测试模式：放开竞价窗口
    hm = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return False, "周末休市，竞价数据不可用"
    if not (AUTO_FETCH_WINDOW[0] <= hm < AUTO_FETCH_WINDOW[1]):
        return False, "当前不在竞价窗口（交易日 9:25-9:30），请到时再抓取"
    if not is_trading_day(now):
        return False, "今天休市，竞价数据不可用"
    return True, ""


def secid_of(code):
    code = str(code).zfill(6)
    if code.startswith("6"):
        return "1." + code
    return "0." + code


def _bare_code(code):
    """'sh600519' -> '600519'；已 6 位则原样；不足补零。"""
    code = str(code).strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        code = code[2:]
    return code.zfill(6)


def _tx_symbol(code):
    """6 位代码 -> 腾讯带前缀符号。镜像 akshare 规则（bj 覆盖 4/8/9 起点）。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("0", "2", "3")):
        return "sz" + code
    return "bj" + code


def _is_bj(code):
    """6 位代码是否为北交所（4/8/9 开头）。"""
    return str(code).zfill(6).startswith(("4", "8", "9"))


def _prev_trading_row(df, snapshot_date):
    """升序日线中取 date 严格小于 snapshot_date 的最后一行 dict，无则 None。"""
    sdate = str(snapshot_date)
    prev = df[df["date"].astype(str) < sdate]
    return None if prev.empty else prev.iloc[-1]


def _gtimg_get(url, retries=2):
    """腾讯行情文本接口请求：带 UA + 指数退避，返回 requests.Response。"""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, timeout=10)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise last_exc


def _parse_paging(qs, default_page=1, default_size=30):
    """解析 ?page=&page_size= 参数，非法值回退默认并钳制到 >=1。返回 (page, page_size)。"""

    def _int(vals, default):
        try:
            return int((vals or [str(default)])[0])
        except (TypeError, ValueError):
            return default

    return (
        max(_int(qs.get("page"), default_page), 1),
        max(_int(qs.get("page_size"), default_size), 1),
    )


def fetch_auction_snapshot():
    """腾讯优先，东财直连/akshare 依次降级；返回 (stocks, source_label)。"""
    last_exc = None
    for label, fn in (
        ("腾讯", fetch_auction_snapshot_tencent),
        ("东方财富", _fetch_auction_snapshot_eastmoney),
        ("akshare", fetch_auction_snapshot_akshare),
    ):
        try:
            stocks = fn()
            if stocks:
                print("[fetch] 行情源=%s，%d 只" % (label, len(stocks)), flush=True)
                return stocks, label
        except Exception as exc:
            last_exc = exc
            print("[fetch] %s 失败: %r" % (label, exc), flush=True)
    raise last_exc or RuntimeError("所有行情源均失败")


def fetch_auction_snapshot_tencent():
    """腾讯全市场快照（akshare 封装），取竞价金额前 200；排除北交所。"""
    if ak is None:
        raise RuntimeError("akshare 未安装")
    import io
    import contextlib
    with contextlib.redirect_stderr(io.StringIO()):
        df = ak.stock_zh_a_spot_tx()
    if df is None or df.empty:
        raise RuntimeError("腾讯无数据")
    stocks = []
    for _, row in df.iterrows():
        code = _bare_code(row.get("code"))
        if not code or _is_bj(code):
            continue
        name = str(row.get("name") or "").strip()
        amount = to_float(row.get("turnover"))
        if not name or not amount or amount <= 0:
            continue
        price = to_float(row.get("zxj"))
        change_pct = to_float(row.get("zdf"))
        stocks.append({
            "code": code,
            "name": name,
            "industry": "其他",
            "price": price,
            "changePct": change_pct,
            "open": None,
            "prevClose": round(price / (1 + change_pct / 100.0), 2) if price and change_pct is not None else None,
            "auctionAmount": amount * TX_AMOUNT_MULT,        # 万元 -> 元
            "auctionVolume": to_float(row.get("volume")),    # 手
            "turnover": to_float(row.get("hsl")),            # 换手率 %
            "volumeRatio": to_float(row.get("lb")),          # 量比
            "floatCap": (to_float(row.get("ltsz")) or 0) * TX_CAP_MULT,   # 亿 -> 元
            "totalCap": (to_float(row.get("zsz")) or 0) * TX_CAP_MULT,
        })
    stocks.sort(key=lambda s: (s["auctionAmount"] or 0), reverse=True)
    return stocks[:200]


def _fetch_auction_snapshot_eastmoney():
    data = http_json(PUSH2_URL)
    diff = ((data.get("data") or {}).get("diff")) or []
    stocks = []
    for item in diff:
        code = str(item.get("f12") or "").zfill(6)
        name = str(item.get("f14") or "").strip()
        amount = to_float(item.get("f6"))
        if not code or not name or not amount or amount <= 0:
            continue
        price = to_float(item.get("f2"))
        change_pct = to_float(item.get("f3"))
        volume = to_float(item.get("f5"))
        turnover = to_float(item.get("f8"))
        volume_ratio = to_float(item.get("f10"))
        float_cap = to_float(item.get("f21"))
        total_cap = to_float(item.get("f20"))
        industry = str(item.get("f100") or "其他").strip()
        stocks.append({
            "code": code,
            "name": name,
            "industry": industry,
            "price": price,
            "changePct": change_pct,
            "open": None,
            "prevClose": round(price / (1 + change_pct / 100.0), 2) if price and change_pct is not None else None,
            "auctionAmount": amount,          # 元
            "auctionVolume": volume,          # 手
            "turnover": turnover,             # 竞价换手(东财口径)
            "volumeRatio": volume_ratio,
            "floatCap": float_cap,            # 元
            "totalCap": total_cap,            # 元
        })
    stocks.sort(key=lambda s: (s["auctionAmount"] or 0), reverse=True)
    return stocks[:200]


def fetch_auction_snapshot_akshare():
    """akshare 降级源：先东财全市场，再新浪全市场。"""
    if ak is None:
        raise RuntimeError("akshare 未安装")
    df = None
    source = ""
    try:
        df = ak.stock_zh_a_spot_em()
        source = "em"
    except Exception:
        df = ak.stock_zh_a_spot()
        source = "sina"
    if df is None or df.empty:
        raise RuntimeError("akshare 无数据")
    stocks = []
    for _, row in df.iterrows():
        code = str(row.get("代码") or "").zfill(6)
        name = str(row.get("名称") or "").strip()
        amount = to_float(row.get("成交额"))
        if not code or not name or not amount or amount <= 0:
            continue
        price = to_float(row.get("最新价"))
        change_pct = to_float(row.get("涨跌幅"))
        volume = to_float(row.get("成交量"))
        if source == "sina" and volume:
            volume = volume / 100.0  # 新浪成交量为股，统一转成手
        turnover = to_float(row.get("换手率")) if "换手率" in df.columns else None
        volume_ratio = to_float(row.get("量比")) if "量比" in df.columns else None
        float_cap = to_float(row.get("流通市值")) if "流通市值" in df.columns else None
        total_cap = to_float(row.get("总市值")) if "总市值" in df.columns else None
        stocks.append({
            "code": code,
            "name": name,
            "industry": "其他",
            "price": price,
            "changePct": change_pct,
            "open": None,
            "prevClose": to_float(row.get("昨收")),
            "auctionAmount": amount,          # 元
            "auctionVolume": volume,          # 手
            "turnover": turnover,
            "volumeRatio": volume_ratio,
            "floatCap": float_cap,            # 元
            "totalCap": total_cap,            # 元
        })
    stocks.sort(key=lambda s: (s["auctionAmount"] or 0), reverse=True)
    return stocks[:200]


def fetch_yesterday_metric(stock, snapshot_date):
    """以快照日期为锚取昨日成交额/换手/收盘：腾讯日线优先，东财/akshare 依次降级。"""
    for fn in (
        fetch_yesterday_metric_tencent,
        _fetch_yesterday_metric_eastmoney,
        fetch_yesterday_metric_akshare,
    ):
        try:
            result = fn(stock, snapshot_date)
        except Exception:
            result = None
        if result:
            return result
    return None


def fetch_yesterday_metric_tencent(stock, snapshot_date):
    """腾讯日K：取快照日期前一交易日的成交额/换手/收盘。"""
    if ak is None:
        return None
    try:
        sd = datetime.strptime(str(snapshot_date), "%Y-%m-%d")
        start = (sd - timedelta(days=10)).strftime("%Y%m%d")
        import io
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            df = ak.stock_zh_a_hist_tx(
                symbol=_tx_symbol(stock["code"]),
                start_date=start,
                end_date=sd.strftime("%Y%m%d"),
                adjust="",
            )
        if df is None or df.empty:
            return None
        row = _prev_trading_row(df, snapshot_date)
        if row is None:
            return None
        t = to_float(row.get("turnover"))
        return {
            "yesterdayAmount": to_float(row.get("amount")),                 # 元
            "yesterdayTurnover": round(t * 100.0, 3) if t is not None else None,  # 小数 -> %
            "yesterdayClose": to_float(row.get("close")),
        }
    except Exception:
        return None


def _fetch_yesterday_metric_eastmoney(stock, snapshot_date):
    code = stock["code"]
    try:
        time.sleep(0.1)  # 限速：避免并发请求过快触发风控
        data = http_json(KLINE_URL.format(secid=secid_of(code)), referer="https://quote.eastmoney.com/")
        klines = ((data.get("data") or {}).get("klines")) or []
        if not klines:
            return None
        prev = None
        for line in klines:
            parts = line.split(",")
            if parts and parts[0] < str(snapshot_date):
                prev = parts
        if prev is None:
            return None
        # f57=成交额(元), f61=换手率(%), f53=收盘价
        return {
            "yesterdayAmount": to_float(prev[6]) if len(prev) > 6 else None,
            "yesterdayTurnover": to_float(prev[10]) if len(prev) > 10 else None,
            "yesterdayClose": to_float(prev[2]) if len(prev) > 2 else None,
        }
    except Exception:
        return None


def fetch_yesterday_metric_akshare(code, snapshot_date):
    """akshare 日线降级源：取快照日期前一交易日的成交额/换手/收盘。"""
    if ak is None:
        return None
    try:
        prefix = "bj" if code.startswith(("4", "8", "9")) else ("sh" if code.startswith("6") else "sz")
        df = ak.stock_zh_a_daily(symbol=prefix + code, adjust="")
        if df is None or df.empty:
            return None
        prev = df[df["date"].astype(str) < str(snapshot_date)]
        if prev.empty:
            return None
        row = prev.iloc[-1]
        return {
            "yesterdayAmount": to_float(row.get("amount")),
            "yesterdayTurnover": to_float(row.get("turnover")),
            "yesterdayClose": to_float(row.get("close")),
        }
    except Exception:
        return None


def compute_metrics(stock, yesterday):
    s = dict(stock)
    amount = s.get("auctionAmount") or 0
    float_cap = s.get("floatCap") or 0
    price = s.get("price") or 0
    volume = s.get("auctionVolume") or 0

    y_amount = (yesterday or {}).get("yesterdayAmount")
    s["yesterdayAmount"] = y_amount
    s["yesterdayTurnover"] = (yesterday or {}).get("yesterdayTurnover")
    s["yesterdayClose"] = (yesterday or {}).get("yesterdayClose")

    ratio = (amount / y_amount * 100.0) if (y_amount and y_amount > 0) else None
    strength = (amount / float_cap * 10000.0) if (float_cap and float_cap > 0) else None
    float_shares = (float_cap / price) if (price and price > 0) else 0
    auction_turnover = (volume * 100 / float_shares * 100.0) if (float_shares and float_shares > 0) else None

    s["ratioToYesterday"] = round(ratio, 2) if ratio is not None else None
    s["amountStrength"] = round(strength, 2) if strength is not None else None
    s["auctionTurnover"] = round(auction_turnover, 2) if auction_turnover is not None else None

    def norm(value, cap):
        if value is None:
            return 0.0
        return max(0.0, min(float(value), cap)) / cap * 100.0

    s_strength = norm(strength, 100.0)
    s_ratio = norm(ratio, 30.0)
    s_turnover = norm(auction_turnover, 1.5)
    s_change = norm((s.get("changePct") or 0) + 2.0, 12.0)
    s_vol = norm(s.get("volumeRatio"), 8.0)
    score = round(0.30 * s_strength + 0.30 * s_ratio + 0.20 * s_turnover + 0.12 * s_change + 0.08 * s_vol, 1)
    s["score"] = score

    tags = []
    if ratio is not None and strength is not None and ratio >= 15 and strength >= 20:
        tags.append("超预期")
    if strength is not None and strength >= 30:
        tags.append("高金额强度")
    if ratio is not None and ratio >= 20:
        tags.append("大幅放量")
    if auction_turnover is not None and auction_turnover >= 0.8:
        tags.append("高换手")
    if (s.get("changePct") or 0) >= 3:
        tags.append("高开")
    s["tags"] = tags if tags else ["常规"]
    return s


def fetch_indices():
    """腾讯实时优先，东财/akshare 依次降级。"""
    for fn in (
        fetch_indices_tencent,
        _fetch_indices_eastmoney,
        fetch_indices_akshare,
    ):
        try:
            out = fn()
        except Exception:
            out = []
        if out:
            return out
    return []


def fetch_indices_tencent():
    """腾讯实时指数：qt.gtimg.cn/q=s_xxx 简版，字段[1]=名称 [3]=现价 [5]=涨跌幅。"""
    url = "https://qt.gtimg.cn/q=" + ",".join("s_" + c for c in TX_INDEX_CODES)
    resp = _gtimg_get(url)
    out = []
    for line in resp.text.splitlines():
        if "=" not in line:
            continue
        payload = line.split("=", 1)[1].strip().strip('"').strip(";")
        fields = payload.split("~")
        if len(fields) < 6:
            continue
        out.append({
            "name": fields[1],
            "price": to_float(fields[3]),
            "changePct": to_float(fields[5]),
        })
    return out


def _fetch_indices_eastmoney():
    try:
        data = http_json(INDEX_URL)
        diff = ((data.get("data") or {}).get("diff")) or []
        out = []
        for item in diff:
            code = str(item.get("f12") or "")
            out.append({
                "name": INDEX_NAMES.get(code, "指数"),
                "price": to_float(item.get("f2")),
                "changePct": to_float(item.get("f3")),
            })
        return out
    except Exception:
        return []


def fetch_indices_akshare():
    """akshare 指数降级源：新浪指数实时行情。"""
    if ak is None:
        return []
    try:
        df = ak.stock_zh_index_spot_sina()
    except Exception:
        return []
    if df is None or df.empty:
        return []
    out = []
    for name in INDEX_NAMES.values():
        row = df[df["名称"] == name]
        if not row.empty:
            r = row.iloc[0]
            out.append({
                "name": name,
                "price": to_float(r.get("最新价")),
                "changePct": to_float(r.get("涨跌幅")),
            })
    return out


def build_sector_map():
    """从新浪行业板块构建 {6位代码: 行业} 映射，first-wins，写盘缓存。"""
    if ak is None:
        return {}
    import io
    import contextlib
    mapping = {}
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            sectors = ak.stock_sector_spot(indicator="新浪行业")
        for _, sec in sectors.iterrows():
            label = str(sec.get("label") or "").strip()
            if not label:
                continue
            industry = str(sec.get("板块") or label).strip()
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    detail = ak.stock_sector_detail(sector=label)
                for _, r in detail.iterrows():
                    code = str(r.get("code") or "").zfill(6)
                    if code:
                        mapping.setdefault(code, industry)
            except Exception as exc:
                print("[sector] %s 成分获取失败: %r" % (label, exc), flush=True)
    except Exception as exc:
        print("[sector] 新浪板块构建失败: %r" % exc, flush=True)
    if mapping:
        try:
            with _lock:
                with open(SECTOR_MAP_FILE, "w", encoding="utf-8") as f:
                    json.dump({"built_at": datetime.now().isoformat(), "map": mapping}, f, ensure_ascii=False)
            print("[sector] 板块映射已构建: %d 只" % len(mapping), flush=True)
        except Exception as exc:
            print("[sector] 缓存写盘失败: %r" % exc, flush=True)
    return mapping


def load_sector_map(force=False):
    """返回 {6位代码: 行业}。模块缓存 > 磁盘缓存(TTL 1天) > 重建；失败返回 {} 不覆盖旧缓存。"""
    if not force and _sector_map.get("map"):
        return _sector_map["map"]
    try:
        with open(SECTOR_MAP_FILE, encoding="utf-8") as f:
            data = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(data["built_at"])).days
        if age < SECTOR_MAP_TTL_DAYS and isinstance(data.get("map"), dict):
            _sector_map.update(data)
            return data["map"]
    except Exception:
        pass
    mapping = build_sector_map()
    if mapping:
        _sector_map["built_at"] = datetime.now().isoformat()
        _sector_map["map"] = mapping
        return mapping
    return {}


def _in_auction_window(now):
    """交易日 9:25-9:30 竞价窗口判断（不含节假日判定，仅时间/星期）。"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return AUTO_FETCH_WINDOW[0] <= hm < AUTO_FETCH_WINDOW[1]


def build_snapshot(auto=False):
    if not _fetch_lock.acquire(blocking=False):
        raise RuntimeError("已有抓取任务进行中，请稍后再试")
    try:
        return _build_snapshot(auto)
    finally:
        _fetch_lock.release()


def _build_snapshot(auto=False):
    print("[fetch] 开始抓取竞价数据", flush=True)
    snapshot, source_label = fetch_auction_snapshot()
    print("[fetch] 竞价金额前 200 已获取", flush=True)

    now = datetime.now()
    snapshot_date = now.strftime("%Y-%m-%d")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_yesterday_metric, s, snapshot_date): s for s in snapshot}
        yesterday_map = {}
        for future in concurrent.futures.as_completed(futures):
            stock = futures[future]
            try:
                yesterday_map[stock["code"]] = future.result()
            except Exception:
                yesterday_map[stock["code"]] = None

    # 板块补齐：仅对缺失/占位行业使用新浪行业映射（东财直连命中时保留原 f100）
    sector_map = load_sector_map()
    for s in snapshot:
        if not s.get("industry") or s["industry"] in ("其他", ""):
            s["industry"] = sector_map.get(s["code"], "其他")

    stocks = [compute_metrics(s, yesterday_map.get(s["code"])) for s in snapshot]
    stocks.sort(key=lambda s: (s.get("score") or 0), reverse=True)
    for idx, stock in enumerate(stocks, start=1):
        stock["rank"] = idx

    breadth_up = sum(1 for s in stocks if (s.get("changePct") or 0) > 0)
    breadth_down = sum(1 for s in stocks if (s.get("changePct") or 0) < 0)
    breadth_flat = len(stocks) - breadth_up - breadth_down

    def is_limit_up(s):
        c = s.get("changePct") or 0
        return c >= (19.5 if s["code"].startswith(("300", "301", "688", "689")) else 9.8)

    snapshot_data = {
        "ok": True,
        "date": snapshot_date,
        "fetchedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source_label,
        "auto": auto,
        "validForAuction": True,
        "market": {
            "indices": fetch_indices(),
            "up": breadth_up,
            "down": breadth_down,
            "flat": breadth_flat,
            "limitUp": sum(1 for s in stocks if is_limit_up(s)),
            "totalAmount": round(sum((s.get("auctionAmount") or 0) for s in stocks) / 1e8, 2),
        },
        "stocks": stocks,
    }

    # 只有竞价窗口（9:25-9:30）内抓到的快照才算有效竞价数据并落盘；
    # 窗口外手动抓取（可能为全天累计值）仅返回给页面预览，不覆盖已有的有效快照。
    # 测试模式（AUTO_ANYTIME）放开窗口，用于验证自动抓取+入库链路
    in_window = _in_auction_window(now) or AUTO_ANYTIME
    if not in_window:
        snapshot_data["validForAuction"] = False
        snapshot_data["forced"] = True

    if in_window:
        os.makedirs(DATA_DIR, exist_ok=True)
        with _lock:
            with open(LATEST_FILE, "w", encoding="utf-8") as f:
                json.dump(snapshot_data, f, ensure_ascii=False)
            history_dir = os.path.join(DATA_DIR, "history")
            os.makedirs(history_dir, exist_ok=True)
            with open(os.path.join(history_dir, snapshot_data["date"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(snapshot_data, f, ensure_ascii=False)
        # 抓取成功后写入 MySQL 历史库；数据库不可用只记录日志，不影响页面
        if not db.save_snapshot(snapshot_data, allow_anytime=AUTO_ANYTIME):
            print("[db] 历史写入失败: %s" % db.last_error(), flush=True)
    else:
        print("[fetch] 非竞价窗口，本次结果仅预览、未保存（不覆盖有效竞价数据）", flush=True)
    print("[fetch] 完成，共 %d 只" % len(stocks), flush=True)
    return snapshot_data


def sleep_until_next_auction():
    """抓取成功后静默到下一个交易日 9:25，当天不再 20 秒反复检查。"""
    now = datetime.now()
    for offset in range(1, 8):
        nxt = (now + timedelta(days=offset)).replace(hour=9, minute=25, second=0, microsecond=0)
        if nxt.weekday() < 5:
            return max(1, (nxt - now).total_seconds())
    return AUTO_FETCH_IDLE_SECONDS


def _hm(t):
    """'09:15' -> 9*60+15。"""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _hm_time(now, t):
    """把 '09:15' 转成当日 datetime。"""
    h, m = t.split(":")
    return now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _sleep_until_trade_morning():
    """静默到下一个交易日 9:15 前（9:14:30），用于封单线程跨日等待。"""
    now = datetime.now()
    for offset in range(1, 8):
        nxt = (now + timedelta(days=offset)).replace(hour=9, minute=14, second=30, microsecond=0)
        if nxt.weekday() < 5:
            return max(1, (nxt - now).total_seconds())
    return AUTO_FETCH_IDLE_SECONDS


def auto_fetch_loop(enabled=True):
    if not enabled:
        return
    fetched_date = None
    attempts = 0
    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if today != fetched_date:
            attempts = 0
        if fetched_date == today:
            # 当日已抓取成功：终止当日循环，静默到下一个交易日 9:25
            print("[auto] 今日已抓取，停止当日自动检查", flush=True)
            time.sleep(sleep_until_next_auction())
            continue
        valid, _ = auction_window_status(now)
        # 只在 9:25-9:30 竞价窗口内自动抓取，每 20 秒检查一次
        if valid and attempts < AUTO_FETCH_MAX_ATTEMPTS:
            print("[auto] 交易日竞价已结束，开始自动抓取", flush=True)
            try:
                build_snapshot(auto=True)
                fetched_date = today
                print("[auto] 自动抓取完成，当日停止自动检查", flush=True)
            except Exception as exc:
                attempts += 1
                print("[auto] 自动抓取失败(%d/%d): %r" % (attempts, AUTO_FETCH_MAX_ATTEMPTS, exc), flush=True)
        time.sleep(AUTO_FETCH_TICK)


def load_latest():
    if not os.path.exists(LATEST_FILE):
        return {"ok": False, "error": "暂无缓存数据"}
    with open(LATEST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_realtime_pct(codes):
    """腾讯批量实时行情：返回 {6位代码: 当前涨幅%}。codes 去重后分页拉取。"""
    if ak is None:
        raise RuntimeError("akshare 未安装")
    out = {}
    seen = set()
    batch = []
    for c in codes:
        c = str(c).zfill(6)
        if c in seen:
            continue
        seen.add(c)
        prefix = "sh" if c.startswith("6") else "sz"
        batch.append(prefix + c)
    for i in range(0, len(batch), 40):
        url = "https://qt.gtimg.cn/q=" + ",".join(batch[i:i + 40])
        resp = _gtimg_get(url)
        for line in resp.text.splitlines():
            if "=" not in line:
                continue
            payload = line.split("=", 1)[1].strip().strip('"').strip(";")
            fields = payload.split("~")
            if len(fields) > 32 and fields[2]:
                code = str(fields[2]).zfill(6)
                out[code] = to_float(fields[32])
    return out


def build_realtime():
    """基于最新快照的 200 只，返回各自当前涨幅。"""
    snapshot = load_latest()
    stocks = snapshot.get("stocks") or []
    codes = [s.get("code") for s in stocks if s.get("code")]
    try:
        pct_map = fetch_realtime_pct(codes)
    except Exception as exc:
        return {"ok": False, "error": "实时行情获取失败: %s" % exc}
    return {
        "ok": True,
        "fetchedAt": datetime.now().strftime("%H:%M:%S"),
        "realtime": pct_map,
    }


# ---------- 行业指数合成（头马/黑马） ----------
SECTOR_INDEX_FILE = os.path.join(DATA_DIR, "sector_index_cache.json")
SECTOR_INDEX_TTL_DAYS = 1
SECTOR_INDEX_TOP = 10          # 每行业取成交额前 N 只成分股
_sector_index_cache = {}       # {"built_at": iso, "indices": {label: {name, dates, closes}}}
_sector_index_lock = threading.Lock()


def _fetch_kline_rows(symbol, start_date):
    """腾讯日K（qfq），返回按日期升序的 [{date, close, volume}]。失败返回 []。"""
    import io
    import contextlib
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start_date,
                                       end_date=datetime.now().strftime("%Y%m%d"), adjust="qfq")
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            close = to_float(r.get("close"))
            vol = to_float(r.get("volume"))
            if close is not None:
                rows.append({"date": str(r.get("date"))[:10], "close": close, "volume": vol or 0})
        return rows
    except Exception:
        return []


def _synthesize_sector_index(label, name):
    """取新浪行业成分股成交额前 SECTOR_INDEX_TOP 只，腾讯日K 等权合成行业指数。
    返回 {dates:[...升序], closes:[...], volumes:[...成分股总成交量]} | None。"""
    import io
    import contextlib
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            detail = ak.stock_sector_detail(sector=label)
        if detail is None or detail.empty:
            return None
        # 按成交额排序取前 N（amount 列降序）
        if "amount" in detail.columns:
            detail = detail.sort_values("amount", ascending=False)
        top = detail.head(SECTOR_INDEX_TOP)
        # 每只成分股拉日K，按日期累加
        start = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")  # 90 交易日起步
        date_map = {}
        for _, r in top.iterrows():
            symbol = str(r.get("symbol") or "")
            if not symbol:
                continue
            for row in _fetch_kline_rows(symbol, start):
                d = row["date"]
                item = date_map.setdefault(d, {"sum": 0.0, "cnt": 0, "vol": 0.0})
                item["sum"] += row["close"]
                item["cnt"] += 1
                item["vol"] += row["volume"]
        if not date_map:
            return None
        ordered = sorted(date_map.items())
        return {
            "name": name,
            "dates": [d for d, _ in ordered],
            "closes": [round(v["sum"] / v["cnt"], 4) for _, v in ordered],
            "volumes": [round(v["vol"], 0) for _, v in ordered],
        }
    except Exception as exc:
        print("[horses] %s 行业合成失败: %r" % (name, exc), flush=True)
        return None


def build_sector_indices():
    """新浪 49 行业 → 每行业等权合成指数，写盘缓存。较慢（约 25-40 分钟），后台调用。"""
    if ak is None:
        return {}
    import io
    import contextlib
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            sectors = ak.stock_sector_spot(indicator="新浪行业")
    except Exception as exc:
        print("[horses] 行业列表获取失败: %r" % exc, flush=True)
        return {}
    result = {}
    for _, sec in sectors.iterrows():
        label = str(sec.get("label") or "").strip()
        name = str(sec.get("板块") or label).strip()
        if not label:
            continue
        idx = _synthesize_sector_index(label, name)
        if idx:
            result[label] = idx
    if result:
        with _sector_index_lock:
            try:
                with open(SECTOR_INDEX_FILE, "w", encoding="utf-8") as f:
                    json.dump({"built_at": datetime.now().isoformat(), "indices": result}, f, ensure_ascii=False)
            except Exception as exc:
                print("[horses] 缓存写盘失败: %r" % exc, flush=True)
    print("[horses] 行业指数合成完成: %d 个行业" % len(result), flush=True)
    return result


def load_sector_indices(force=False):
    """返回 {label: {name, dates, closes, volumes}}。模块缓存 > 磁盘缓存(TTL 1天) > 重建。"""
    if not force and _sector_index_cache.get("indices"):
        return _sector_index_cache["indices"]
    try:
        with open(SECTOR_INDEX_FILE, encoding="utf-8") as f:
            data = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(data["built_at"])).days
        if age < SECTOR_INDEX_TTL_DAYS and isinstance(data.get("indices"), dict):
            _sector_index_cache.update(data)
            return data["indices"]
    except Exception:
        pass
    indices = build_sector_indices()
    if indices:
        _sector_index_cache["built_at"] = datetime.now().isoformat()
        _sector_index_cache["indices"] = indices
        return indices
    return _sector_index_cache.get("indices") or {}


def _ma(series, n):
    """近 n 日均值（不足取全部）。"""
    if not series:
        return None
    window = series[-n:]
    return sum(window) / len(window)


def _obv_strong(closes, volumes, days=20):
    """OBV 强势 = 最新 OBV > 自身 days 日均线（站上 OBV 均线，弱转强刚上穿也算）。
    OBV: 价涨+量，价跌-量，平 0。用户口径：板块 OBV 站上均线 = 短期强于大盘。"""
    if len(closes) < days + 1 or len(volumes) < len(closes):
        return False
    obv = [0.0]
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            obv.append(obv[-1] + volumes[i])
        elif diff < 0:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    ma = _ma(obv, days)
    return ma is not None and obv[-1] > ma


def classify_horses(indices):
    """对合成行业指数分类：头等马（站上MA60且OBV强）/ 黑马（未站上但OBV强）。
    返回 (head_horses, dark_horses)，各最多5个 {code, name, changePct}。"""
    head, dark = [], []
    for label, idx in (indices or {}).items():
        closes = idx.get("closes") or []
        volumes = idx.get("volumes") or []
        if len(closes) < 2:
            continue
        latest = closes[-1]
        prev = closes[-2]
        ma60 = _ma(closes, 60)
        above = ma60 is not None and latest > ma60
        obv = _obv_strong(closes, volumes)
        change_pct = round((latest / prev - 1) * 100.0, 2) if prev else None
        item = {"code": label, "name": idx.get("name", label), "changePct": change_pct}
        if above and obv:
            head.append(item)
        elif not above and obv:
            dark.append(item)
    head.sort(key=lambda x: x.get("changePct") or 0, reverse=True)
    dark.sort(key=lambda x: x.get("changePct") or 0, reverse=True)
    return head[:5], dark[:5]


def build_horses():
    """返回头马/黑马 API 响应（当前缓存）。"""
    indices = load_sector_indices()
    head, dark = classify_horses(indices)
    return {
        "ok": True,
        "date": "",
        "builtAt": _sector_index_cache.get("built_at") or "",
        "headHorses": head,
        "darkHorses": dark,
    }


def build_horses_for_date(date_str):
    """按日期读库返回头马/黑马；无则回落当前缓存。"""
    try:
        row = db.read_review(date_str)
    except Exception:
        row = None
    horses = (row or {}).get("horses") if row else None
    if horses:
        return {
            "ok": True,
            "date": date_str,
            "builtAt": (row or {}).get("fetchedAt") or "",
            "headHorses": horses.get("headHorses") or [],
            "darkHorses": horses.get("darkHorses") or [],
        }
    # 该日未保存头马/黑马，回落当前缓存并标记
    current = build_horses()
    current["date"] = date_str
    current["stale"] = True
    return current


# ---------- 竞价封单额 ----------

def _seal_from_tx_line(line):
    """解析腾讯 qt.gtimg.cn 单行，返回封单 dict | None。

    买一为空/价<=0（跌停或无挂单）视为该股本次无封单，返回 None。
    """
    if "=" not in line:
        return None
    payload = line.split("=", 1)[1].strip().strip('"').strip(";")
    fields = payload.split("~")
    if len(fields) < 17:  # 需要 f[3],f[4],f[9],f[10],f[15],f[16]
        return None
    bid1_price = to_float(fields[TX_BID1_PRICE_IDX])
    bid1_vol = to_float(fields[TX_BID1_VOL_IDX])
    if bid1_price is None or bid1_vol is None or bid1_price <= 0 or bid1_vol <= 0:
        return None
    return {
        "name": str(fields[TX_NAME_IDX] or "").strip(),
        "bid1Price": bid1_price,
        "bid1Volume": bid1_vol,
        "ask1Price": to_float(fields[TX_ASK1_PRICE_IDX]),
        "ask1Volume": to_float(fields[TX_ASK1_VOL_IDX]),
        "lastPrice": to_float(fields[TX_LAST_PRICE_IDX]),
        "prevClose": to_float(fields[TX_PREV_CLOSE_IDX]),
        "sealAmount": round(bid1_vol * 100 * bid1_price, 2),
    }


def fetch_all_codes():
    """全市场沪深代码清单（排除北交所）。当日缓存一次，约 20s 首次拉取。"""
    global _all_codes_cache
    today = datetime.now().strftime("%Y-%m-%d")
    if _all_codes_cache["date"] == today and _all_codes_cache["codes"] is not None:
        return _all_codes_cache["codes"]
    if ak is None:
        raise RuntimeError("akshare 未安装")
    import io
    import contextlib
    with contextlib.redirect_stderr(io.StringIO()):
        df = ak.stock_zh_a_spot_tx()
    if df is None or df.empty:
        raise RuntimeError("腾讯无数据")
    codes = []
    for raw in df["code"].astype(str):
        code = _bare_code(raw)
        if code and not _is_bj(code):
            codes.append(code)
    _all_codes_cache = {"date": today, "codes": codes}
    return codes


def fetch_seal_quotes(codes):
    """腾讯批量直连抓封单（买一量×买一价）。分批请求，行首符号回填 code。

    返回 {code: dict}；单只失败/无买一不落结果，批次内其它股票不受影响。
    """
    result = {}
    for i in range(0, len(codes), SEAL_BATCH_SIZE):
        chunk = codes[i:i + SEAL_BATCH_SIZE]
        url = "https://qt.gtimg.cn/q=" + ",".join(_tx_symbol(c) for c in chunk)
        resp = _gtimg_get(url)
        for line in resp.text.splitlines():
            m = re.match(r"v_([a-z]{2}\d{6})=", line)
            if not m:
                continue
            code = _bare_code(m.group(1))
            parsed = _seal_from_tx_line(line)
            if parsed:
                result[code] = parsed
    return result


def fetch_seal_top20(sample_time, now=None):
    """扫描全市场，按封单额降序取前 SEAL_TOP_N。

    返回 {"date","sampleTime","records":[ {code,name,sealAmount,sealRank,...} ],"scanned"}
    """
    now = now or datetime.now()
    codes = fetch_all_codes()
    quotes = fetch_seal_quotes(codes)
    rows = []
    for code, q in quotes.items():
        rows.append({"code": code, "name": q.pop("name", code), **q})
    rows.sort(key=lambda r: (r.get("sealAmount") or 0), reverse=True)
    rows = rows[:SEAL_TOP_N]
    for idx, r in enumerate(rows, start=1):
        r["sealRank"] = idx
        r["tradeDate"] = now.strftime("%Y-%m-%d")
        r["sampleTime"] = sample_time
        r["fetchedAt"] = now.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "date": now.strftime("%Y-%m-%d"),
        "sampleTime": sample_time,
        "records": rows,
        "scanned": len(codes),
    }


def _run_seal_sample(sample_time):
    try:
        snap = fetch_seal_top20(sample_time)
        if not snap["records"]:
            print("[seal] %s 封单抓取为空，跳过入库" % sample_time, flush=True)
            return
        if not db.save_seal(snap["records"]):
            print("[seal] %s 入库失败: %s" % (sample_time, db.last_error()), flush=True)
        else:
            print("[seal] %s 封单 Top%d 入库（扫描 %d 只）" % (
                sample_time, len(snap["records"]), snap["scanned"]), flush=True)
    except Exception as exc:
        print("[seal] %s 抓取失败: %r" % (sample_time, exc), flush=True)


def seal_fetch_loop(enabled=True):
    """交易日 9:15/9:20/9:25 各扫一轮全市场封单额 Top20，独立于竞价快照线程。"""
    if not enabled:
        return
    while True:
        now = datetime.now()
        if now.weekday() >= 5 or not is_trading_day(now):
            time.sleep(_sleep_until_trade_morning())
            continue
        hm = now.hour * 60 + now.minute
        target = next((t for t in SEAL_SAMPLE_TIMES if _hm(t) > hm), None)
        if target is None:
            # 当日三个时点已过，睡到次日 9:15 前
            time.sleep(_sleep_until_trade_morning())
            continue
        time.sleep(max(1, (_hm_time(now, target) - datetime.now()).total_seconds()))
        _run_seal_sample(target)


# 收盘涨幅入库：交易日 15:05 抓一次当日竞价股的最终涨幅
CLOSE_FETCH_HM = 15 * 60 + 5


def _run_close_sample():
    """基于最新竞价快照，抓当日竞价股的收盘涨幅并入库。"""
    try:
        snapshot = load_latest()
        stocks = snapshot.get("stocks") or []
        if not stocks:
            print("[close] 无竞价快照，跳过", flush=True)
            return
        codes = [s.get("code") for s in stocks if s.get("code")]
        pct_map = fetch_realtime_pct(codes)  # 收盘后接口返回的就是最终涨幅
        date = snapshot.get("date")
        if not db.save_close_pct(date, pct_map):
            print("[close] 入库失败: %s" % db.last_error(), flush=True)
        else:
            print("[close] 收盘涨幅已入库: %s %d 只" % (date, len(pct_map)), flush=True)
    except Exception as exc:
        print("[close] 收盘抓取失败: %r" % exc, flush=True)


def _run_close_review():
    """收盘后自动生成当天完整复盘（指数/涨停池/连板/头马黑马）并入库。"""
    try:
        snapshot = load_latest()
        date = (snapshot or {}).get("date")
        if not date:
            print("[close] 无竞价快照日期，跳过复盘生成", flush=True)
            return
        build_review(date, refresh=True)
        print("[close] 当日复盘已自动生成并入库: %s" % date, flush=True)
    except Exception as exc:
        print("[close] 自动生成复盘失败: %r" % exc, flush=True)


def close_fetch_loop(enabled=True):
    """交易日 15:05 抓收盘涨幅 + 自动生成当日复盘，独立于竞价/封单线程。"""
    if not enabled:
        return
    while True:
        now = datetime.now()
        if now.weekday() >= 5 or not is_trading_day(now):
            time.sleep(_sleep_until_trade_morning())
            continue
        hm = now.hour * 60 + now.minute
        if hm >= CLOSE_FETCH_HM:
            # 当日时点已过，睡到次日 9:15 前
            time.sleep(_sleep_until_trade_morning())
            continue
        sleep_secs = max(1, (now.replace(hour=15, minute=5, second=0, microsecond=0) - datetime.now()).total_seconds())
        time.sleep(sleep_secs)
        _run_close_sample()
        _run_close_review()

ZT_POOL_FUNCS = {
    "zt": "stock_zt_pool_em",
    "dt": "stock_zt_pool_dtgc_em",
    "zb": "stock_zt_pool_zbgc_em",
    "strong": "stock_zt_pool_strong_em",
    "prev": "stock_zt_pool_previous_em",
}


def _pool_em(func_name, date_str):
    """调东财涨停池接口，屏蔽 tqdm，空 df 抛错。日期需 YYYYMMDD。"""
    if ak is None:
        raise RuntimeError("akshare 未安装")
    import io
    import contextlib
    compact_date = date_str.replace("-", "")
    with contextlib.redirect_stderr(io.StringIO()):
        fn = getattr(ak, func_name)
        df = fn(date=compact_date)
    if df is None or df.empty:
        raise RuntimeError("%s 无数据" % func_name)
    return df


def _row_val(row, names):
    """按别名列表取第一个存在的列值。"""
    for n in names:
        if n in row:
            return row[n]
    return None


def _stock_from_pool_row(row):
    return {
        "code": str(_row_val(row, ("代码",)) or "").zfill(6),
        "name": str(_row_val(row, ("名称",)) or "").strip(),
        "changePct": to_float(_row_val(row, ("涨跌幅",))),
        "price": to_float(_row_val(row, ("最新价",))),
        "amount": to_float(_row_val(row, ("成交额",))),
        "industry": str(_row_val(row, ("所属行业",)) or "").strip(),
        "sealAmount": to_float(_row_val(row, ("封板资金",))),
        "sealTime": str(_row_val(row, ("首次封板时间",)) or "").strip(),
        "ztCount": str(_row_val(row, ("涨停统计",)) or "").strip(),
        "lianban": _row_val(row, ("连板数",)),
        "reason": str(_row_val(row, ("入选理由",)) or "").strip(),
    }


def fetch_zt_pools(date_str):
    """抓取五个涨停相关池，单池失败置空。"""
    out = {}
    for key, func in ZT_POOL_FUNCS.items():
        try:
            df = _pool_em(func, date_str)
            out[key] = [_stock_from_pool_row(row) for _, row in df.iterrows()]
        except Exception as exc:
            print("[review] %s 池抓取失败: %r" % (key, exc), flush=True)
            out[key] = []
    return out


def _zt_count_from_stats(zt_stat):
    """涨停统计 'N/N' → N（连板数）；'0/0' 断板、'23/12' 多月累计新高 → None。"""
    text = str(zt_stat or "").strip()
    if "/" not in text:
        return None
    left, right = text.split("/", 1)
    if not left.isdigit() or not right.isdigit():
        return None
    left_n, right_n = int(left), int(right)
    if left_n == 0 or right_n <= 0:
        return None
    # 累计新高（分母远大于分子，如 23/12）不计入当日连板梯队
    if left_n > right_n:
        return None
    return right_n


def parse_lianban(zt_rows, strong_rows):
    """把涨停池/连板池按连板数分桶成 8..1 板 + 涨停(首板)，同一股票去重。"""
    tier = {n: [] for n in range(8, 0, -1)}
    tier["first"] = []
    for rows in (strong_rows, zt_rows):
        for s in rows:
            n = _zt_count_from_stats(s.get("ztCount"))
            if n is None:
                continue
            bucket = "first" if n == 1 else (n if 1 < n <= 8 else None)
            if bucket is None:
                continue
            entry = {"code": s.get("code"), "name": s.get("name"), "industry": s.get("industry"), "sealAmount": s.get("sealAmount")}
            target = tier["first"] if bucket == "first" else tier[bucket]
            # 同一股票只保留一条（优先 strong 池在前，封单额高者胜）
            exist = next((e for e in target if e.get("code") == entry["code"]), None)
            if exist:
                if (entry.get("sealAmount") or 0) > (exist.get("sealAmount") or 0):
                    target.remove(exist)
                    target.append(entry)
                continue
            target.append(entry)
    for key in list(tier.keys()):
        tier[key] = sorted(tier[key], key=lambda e: (e.get("sealAmount") or 0), reverse=True)[:8]
    max_tier = 0
    for n in range(8, 1, -1):
        if tier[n]:
            max_tier = n
            break
    return {"tier": tier, "maxTier": max_tier}


def fetch_spot_breadth(date_str):
    """腾讯全市场快照计算红/绿/平盘家数（沪深，排除北交所）。"""
    if ak is None:
        raise RuntimeError("akshare 未安装")
    import io
    import contextlib
    with contextlib.redirect_stderr(io.StringIO()):
        df = ak.stock_zh_a_spot_tx()
    if df is None or df.empty:
        raise RuntimeError("腾讯无数据")
    df = df[~df["code"].astype(str).str.startswith("bj")]
    zdf = df["zdf"].astype(float)
    return {"up": int((zdf > 0).sum()), "down": int((zdf < 0).sum()), "flat": int((zdf == 0).sum())}


def compute_prev_premium(prev_rows):
    """昨涨停溢价 = prev 池全部涨跌幅均值；连板溢价 = 昨日连板数>=2 均值。"""
    all_chg = [to_float(s.get("changePct")) for s in prev_rows]
    all_chg = [v for v in all_chg if v is not None]
    lb_chg = [to_float(s.get("changePct")) for s in prev_rows if _row_val(s, ("昨日连板数",)) is not None and to_float(_row_val(s, ("昨日连板数",))) >= 2]
    lb_chg = [v for v in lb_chg if v is not None]
    return {
        "ztPremium": round(sum(all_chg) / len(all_chg), 2) if all_chg else None,
        "lbPremium": round(sum(lb_chg) / len(lb_chg), 2) if lb_chg else None,
    }


def fetch_indices_kline(date_str):
    """腾讯指数日K当日行：收盘/涨跌点数/涨跌幅度/成交量(亿)/今开。"""
    if ak is None:
        raise RuntimeError("akshare 未安装")
    import io
    import contextlib
    out = []
    amount_map = {}  # symbol -> (今日成交额亿, 昨日成交额亿)
    for symbol, name in (("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"), ("sh000688", "科创50")):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date="20200101", end_date=date_str.replace("-", ""), adjust="")
            if df is None or df.empty:
                continue
            today_rows = df[df["date"].astype(str) == date_str]
            if today_rows.empty:
                continue
            r = today_rows.iloc[-1]
            prev = _prev_trading_row(df, date_str)
            close = to_float(r.get("close"))
            prev_close = to_float(prev.get("close")) if prev is not None else None
            change_pts = round(close - prev_close, 2) if close is not None and prev_close is not None else None
            change_pct = round(change_pts / prev_close * 100.0, 2) if change_pts is not None and prev_close else None
            amount_yi = round((to_float(r.get("amount")) or 0) / 1e8, 2)
            prev_amount_yi = round((to_float(prev.get("amount")) or 0) / 1e8, 2) if prev is not None else None
            amount_map[symbol] = (amount_yi, prev_amount_yi)
            out.append({
                "name": name,
                "close": close,
                "changePts": change_pts,
                "changePct": change_pct,
                "amountYi": amount_yi,
                "open": to_float(r.get("open")),
            })
        except Exception as exc:
            print("[review] %s 指数日K失败: %r" % (symbol, exc), flush=True)
    # 沪深两市合计成交额：上证(sh000001) + 深证成指(sz399001)，并计算相对昨日增减
    sh_amount = amount_map.get("sh000001")
    sz_amount = amount_map.get("sz399001")
    if sh_amount and sz_amount:
        today_total = sh_amount[0] + sz_amount[0]
        prev_total = None
        if sh_amount[1] is not None and sz_amount[1] is not None:
            prev_total = sh_amount[1] + sz_amount[1]
        market_amount = round(today_total, 2)
        change_yi = round(today_total - prev_total, 2) if prev_total is not None else None
        change_pct = round(change_yi / prev_total * 100.0, 2) if change_yi is not None and prev_total else None
        for item in out:
            if item["name"] == "上证指数":
                item["marketAmountYi"] = market_amount
                item["marketAmountChangeYi"] = change_yi
                item["marketAmountChangePct"] = change_pct
                break
    return out


def fetch_zt_meta(zt_rows):
    """最大封单、封单亿元以上、前排题材（按所属行业聚合）。"""
    max_seal = None
    seal_yi = []
    front = {}
    for s in zt_rows:
        seal = s.get("sealAmount") or 0
        if seal > 0:
            if max_seal is None or seal > (max_seal.get("sealRaw") or 0):
                max_seal = {
                    "code": s.get("code"), "name": s.get("name"),
                    "industry": s.get("industry") or "其他",
                    "sealAmount": round(seal / 1e8, 2), "sealRaw": seal,
                }
            if seal >= 1e8:
                seal_yi.append({"code": s.get("code"), "name": s.get("name"),
                                "industry": s.get("industry") or "其他",
                                "sealYi": round(seal / 1e8, 2)})
        ind = s.get("industry") or "其他"
        if ind:
            item = front.setdefault(ind, {"name": ind, "count": 0, "zt": 0})
            item["count"] += 1
            item["zt"] += 1
    seal_yi.sort(key=lambda x: x.get("sealYi") or 0, reverse=True)  # 封单额降序
    front_list = sorted(front.values(), key=lambda x: x["count"], reverse=True)[:8]
    if max_seal:
        max_seal.pop("sealRaw", None)
    return {"maxSeal": max_seal, "sealYi": seal_yi, "frontSectors": front_list}


def compute_three_pick(stocks):
    """竞价三一票：在给定股票池里，分别取竞价金额/竞价换手/竞价涨幅第一，
    命中 ≥2 项榜首的个股入选（按命中项数降序，取前 5）。"""
    if not stocks:
        return []
    def _top(key, valid):
        ranked = [s for s in stocks if valid(s) and s.get(key) is not None]
        if not ranked:
            return None
        return max(ranked, key=lambda s: s.get(key) or 0)
    tops = {}
    amount_top = _top("auctionAmount", lambda s: (s.get("auctionAmount") or 0) > 0)
    turnover_top = _top("auctionTurnover", lambda s: (s.get("auctionTurnover") or 0) > 0)
    change_top = _top("changePct", lambda s: s.get("changePct") is not None)
    for stock in (amount_top, turnover_top, change_top):
        if stock:
            entry = tops.setdefault(stock["code"], {
                "code": stock["code"], "name": stock["name"],
                "amount": stock.get("auctionAmount"), "turnover": stock.get("auctionTurnover"),
                "changePct": stock.get("changePct"), "hits": 0,
            })
            entry["hits"] += 1
    winners = [v for v in tops.values() if v["hits"] >= 2]
    winners.sort(key=lambda x: x["hits"], reverse=True)
    return winners[:5]


def _empty_review(date_str):
    """手动字段骨架。"""
    return {
        "indicesOutlook": {"press": "", "support": "", "dailyK": "", "weeklyK": "", "outlook": ""},
        "auctionRows": [
            {"time": "09:15", "maxSeal": "", "wind": "", "sealYi": "", "front": "", "three": ""},
            {"time": "09:20", "maxSeal": "", "wind": "", "sealYi": "", "front": "", "three": ""},
            {"time": "09:25", "maxSeal": "", "wind": "", "sealYi": "", "front": "", "three": ""},
        ],
        "closeStats": {"yizi": "", "zt": "", "dt": "", "zb": "", "red": "", "green": "", "lbPremium": "", "ztPremium": "", "other": ""},
        "emotion": {"today": "", "nextWind": "", "windFix": ""},
        "sectors": [{"name": "", "point": "", "press": "", "support": "", "note": ""} for _ in range(8)],
        "news": "",
        "lianbanPlan": {str(n): "" for n in range(8, 0, -1)},
        "bestChance": {"stock": "", "sector": "", "buyPct": "", "logic": "", "premium": "", "note": ""},
        "tempTrade": "",
    }


def fetch_review_auto(date_str):
    """抓取某日全部自动复盘数据，合并成一个 dict。"""
    pools = fetch_zt_pools(date_str)
    zt_rows = pools.get("zt") or []
    strong_rows = pools.get("strong") or []
    # 竞价三一票：基于当日竞价 Top200（latest.json），金额/换手/涨幅三项第一命中≥2项
    three_pick = []
    try:
        snapshot = load_latest()
        three_pick = compute_three_pick(snapshot.get("stocks") or [])
    except Exception as exc:
        print("[review] 三一票计算失败: %r" % exc, flush=True)
    result = {
        "indices": fetch_indices_kline(date_str),
        "breadth": fetch_spot_breadth(date_str),
        "pools": {
            "ztCount": len(zt_rows),
            "dtCount": len(pools.get("dt") or []),
            "zbCount": len(pools.get("zb") or []),
            "prev": compute_prev_premium(pools.get("prev") or []),
            "ztPool": zt_rows[:200],
        },
        "lianban": parse_lianban(zt_rows, strong_rows),
        "ztMeta": fetch_zt_meta(zt_rows),
        "threePick": three_pick,
    }
    result["pools"]["ztMeta"] = result["ztMeta"]
    result["pools"]["threePick"] = three_pick  # 随 pools 持久化
    return result


def build_review(date_str, refresh=False):
    """构建某日复盘：refresh 时重新抓取，否则读库缓存；MySQL 不可用仍能取。"""
    if refresh:
        with _review_lock:
            try:
                auto = fetch_review_auto(date_str)
            except Exception as exc:
                raise RuntimeError("复盘抓取失败: %s" % exc)
            # 刷新抓取不清空用户已保存的手动字段
            try:
                row = db.read_review(date_str)
            except Exception:
                row = None
            manual = (row or {}).get("manual") or _empty_review(date_str)
            fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 头马/黑马：随收盘复盘一并合成并入库（TTL 1 天缓存，同日不重算）
            horses = None
            try:
                indices = load_sector_indices()
                head, dark = classify_horses(indices)
                horses = {"headHorses": head, "darkHorses": dark}
            except Exception as exc:
                print("[horses] 头马/黑马合成失败: %r" % exc, flush=True)
            db.save_review(
                date_str,
                indices=auto["indices"],
                breadth=auto["breadth"],
                pools=auto["pools"],
                lianban=auto["lianban"],
                manual=manual,
                horses=horses,
                fetched_at=fetched_at,
            )
            cached = False
    else:
        try:
            row = db.read_review(date_str)
        except Exception:
            row = None
        if row is not None:
            pools = row["pools"] or {}
            auto = {
                "indices": row["indices"] or [],
                "breadth": row["breadth"] or {},
                "pools": pools,
                "lianban": row["lianban"] or {},
                "ztMeta": pools.get("ztMeta") or {},
            }
            manual = row.get("manual") or _empty_review(date_str)
            fetched_at = row["fetchedAt"]
            cached = True
        else:
            try:
                auto = fetch_review_auto(date_str)
            except Exception:
                auto = {"indices": [], "breadth": {}, "pools": {}, "lianban": {"tier": {}, "maxTier": 0}, "ztMeta": {}}
            manual = _empty_review(date_str)
            fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cached = False
    return {
        "ok": True,
        "date": date_str,
        "fetchedAt": fetched_at,
        "indices": auto.get("indices") or [],
        "breadth": auto.get("breadth") or {},
        "pools": auto.get("pools") or {},
        "lianban": auto.get("lianban") or {"tier": {}, "maxTier": 0},
        "ztMeta": auto.get("ztMeta") or {},
        "manual": manual,
        "cached": cached,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_csv(self, text, filename):
        body = b"\xef\xbb\xbf" + text.encode("utf-8")  # UTF-8 BOM，Excel 可直接打开
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/latest":
            self.send_json(load_latest())
            return
        if parsed.path == "/api/refresh":
            # 手动抓取：任意时间允许，点一次只发一次请求（不做重试），不再受竞价窗口限制
            try:
                payload = build_snapshot()
                self.send_json(payload)
            except Exception as exc:
                print("[fetch] 失败: %r" % exc, flush=True)
                self.send_json({"ok": False, "error": "抓取失败: %s" % exc}, status=502)
            return
        if parsed.path == "/api/realtime":
            self.send_json(build_realtime())
            return
        if parsed.path == "/api/history/dates":
            try:
                page, page_size = _parse_paging(parse_qs(parsed.query))
                dates, total = db.list_dates_page(page, page_size)
                self.send_json({
                    "ok": True, "dates": dates, "total": total,
                    "page": page, "page_size": page_size,
                    "has_more": page * page_size < total,
                })
            except Exception as exc:
                self.send_json({"ok": False, "error": "MySQL 不可用: %s" % exc}, status=502)
            return
        if parsed.path == "/api/history/date":
            date = (parse_qs(parsed.query).get("date") or [""])[0]
            try:
                day = db.get_day(date)
            except Exception as exc:
                self.send_json({"ok": False, "error": "MySQL 不可用: %s" % exc}, status=502)
                return
            if not day:
                self.send_json({"ok": False, "error": "未找到该日期: %s" % date}, status=404)
            else:
                self.send_json(day)
            return
        if parsed.path == "/api/history/stock":
            code = (parse_qs(parsed.query).get("code") or [""])[0]
            try:
                rows = db.get_stock_history(code)
            except Exception as exc:
                self.send_json({"ok": False, "error": "MySQL 不可用: %s" % exc}, status=502)
                return
            self.send_json({"ok": True, "rows": rows})
            return
        if parsed.path == "/api/history/export.csv":
            date = (parse_qs(parsed.query).get("date") or [""])[0]
            try:
                csv_text = db.export_csv(date)
            except Exception as exc:
                self.send_json({"ok": False, "error": "MySQL 不可用: %s" % exc}, status=502)
                return
            if csv_text is None:
                self.send_json({"ok": False, "error": "未找到该日期: %s" % date}, status=404)
                return
            self.send_csv(csv_text, "auction_history_%s.csv" % date)
            return
        if parsed.path == "/api/seal/dates":
            try:
                self.send_json({"ok": True, "dates": db.list_seal_dates()})
            except Exception as exc:
                self.send_json({"ok": False, "error": "MySQL 不可用: %s" % exc}, status=502)
            return
        if parsed.path == "/api/seal":
            date = (parse_qs(parsed.query).get("date") or [""])[0]
            if not date:
                self.send_json({"ok": False, "error": "缺少 date 参数"}, status=400)
                return
            try:
                rows = db.get_seal(date)
            except Exception as exc:
                self.send_json({"ok": False, "error": "MySQL 不可用: %s" % exc}, status=502)
                return
            if not rows:
                self.send_json({"ok": False, "error": "未找到该日期封单数据: %s" % date}, status=404)
            else:
                self.send_json({"ok": True, "date": date, "rows": rows})
            return
        if parsed.path == "/api/review/dates":
            try:
                dates = db.list_review_dates(120)
                self.send_json({"ok": True, "dates": dates})
            except Exception as exc:
                self.send_json({"ok": False, "error": "MySQL 不可用: %s" % exc}, status=502)
            return
        if parsed.path == "/api/review/horses":
            date = (parse_qs(parsed.query).get("date") or [""])[0]
            if date:
                self.send_json(build_horses_for_date(date))
            else:
                self.send_json(build_horses())
            return
        if parsed.path == "/api/review":
            date = (parse_qs(parsed.query).get("date") or [""])[0]
            refresh = (parse_qs(parsed.query).get("refresh") or [""])[0] == "1"
            if not date:
                self.send_json({"ok": False, "error": "缺少 date 参数"}, status=400)
                return
            if refresh and not is_trading_day(datetime.strptime(date, "%Y-%m-%d")):
                self.send_json({"ok": False, "error": "%s 非交易日，无法抓取复盘" % date}, status=400)
                return
            try:
                self.send_json(build_review(date, refresh=refresh))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/review":
            self.send_json({"ok": False, "error": "未知接口"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body) if body else {}
        except Exception as exc:
            self.send_json({"ok": False, "error": "请求体解析失败: %s" % exc}, status=400)
            return
        date = str(payload.get("date") or "").strip()
        manual = payload.get("manual")
        if not date or not isinstance(manual, dict):
            self.send_json({"ok": False, "error": "缺少 date 或 manual"}, status=400)
            return
        try:
            row = db.read_review(date)
            indices = (row or {}).get("indices")
            breadth = (row or {}).get("breadth")
            pools = (row or {}).get("pools")
            lianban = (row or {}).get("lianban")
        except Exception:
            indices = breadth = pools = lianban = None
        ok = db.save_review(date, indices=indices, breadth=breadth, pools=pools, lianban=lianban, manual=manual)
        if ok:
            self.send_json({"ok": True})
        else:
            self.send_json({"ok": False, "error": "保存失败: %s" % db.last_error()}, status=502)

    def log_message(self, fmt, *args):
        sys.stdout.write("[http] " + fmt % args + "\n")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="量化选股器本地服务")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8010")))
    parser.add_argument("--no-auto", action="store_true", help="关闭交易日 9:25-9:30 的自动抓取")
    parser.add_argument("--auto-anytime", action="store_true", help="测试模式：自动抓取不受竞价窗口限制，用于验证抓取+入库链路")
    args = parser.parse_args()
    global AUTO_ANYTIME
    AUTO_ANYTIME = args.auto_anytime
    os.makedirs(DATA_DIR, exist_ok=True)
    # 启动时初始化 MySQL 历史库（失败不影响服务启动）
    if db.ensure_schema():
        print("[db] MySQL 历史库已就绪: %s" % db.load_config()["database"], flush=True)
    else:
        print("[db] MySQL 历史库不可用: %s" % db.last_error(), flush=True)
    threading.Thread(target=auto_fetch_loop, args=(not args.no_auto,), daemon=True).start()
    threading.Thread(target=seal_fetch_loop, args=(not args.no_auto,), daemon=True).start()
    threading.Thread(target=close_fetch_loop, args=(not args.no_auto,), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("量化选股器已启动: http://127.0.0.1:%d" % args.port, flush=True)
    if args.auto_anytime:
        print("[auto] 测试模式：自动抓取已放开竞价窗口限制，任意时间触发", flush=True)
    elif not args.no_auto:
        print("[auto] 自动抓取已开启：每个交易日 9:25-9:30", flush=True)
    if not args.no_auto:
        print("[seal] 封单抓取已开启：每个交易日 9:15/9:20/9:25", flush=True)
        print("[close] 收盘自动任务已开启：每个交易日 15:05（收盘涨幅入库 + 自动生成当日复盘）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()