# -*- coding: utf-8 -*-
"""竞价选股器本地服务

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
_trading_day_cache = {}  # 按日期缓存交易日判断结果，避免窗口内每次检查都请求接口

# 竞价金额/换手只在交易日 9:25 竞价结束后、开盘前有效，9:30 后 f6/f8 变为全天累计值
AUTO_FETCH_WINDOW = (9 * 60 + 25, 9 * 60 + 30)
AUTO_FETCH_TICK = 20
AUTO_FETCH_MAX_ATTEMPTS = 5  # 窗口内最多重试次数，避免高频抓取触发风控
AUTO_FETCH_IDLE_SECONDS = 300  # 当日抓取成功后静默等待，不再 20 秒反复检查


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
    try:  # 腾讯日线优先
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
    # 窗口外手动抓取（可能为全天累计值）仅返回给页面预览，不覆盖已有的有效快照
    in_window = _in_auction_window(now)
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
        if not db.save_snapshot(snapshot_data):
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
        super().do_GET()

    def log_message(self, fmt, *args):
        sys.stdout.write("[http] " + fmt % args + "\n")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="竞价选股器本地服务")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8010")))
    parser.add_argument("--no-auto", action="store_true", help="关闭交易日 9:25-9:30 的自动抓取")
    args = parser.parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)
    # 启动时初始化 MySQL 历史库（失败不影响服务启动）
    if db.ensure_schema():
        print("[db] MySQL 历史库已就绪: %s" % db.load_config()["database"], flush=True)
    else:
        print("[db] MySQL 历史库不可用: %s" % db.last_error(), flush=True)
    threading.Thread(target=auto_fetch_loop, args=(not args.no_auto,), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("竞价选股器已启动: http://127.0.0.1:%d" % args.port, flush=True)
    if not args.no_auto:
        print("[auto] 自动抓取已开启：每个交易日 9:25-9:30", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()