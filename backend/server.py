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
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen


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

_lock = threading.Lock()
_fetch_lock = threading.Lock()

# 竞价金额/换手只在交易日 9:25 竞价结束后、开盘前有效，9:30 后 f6/f8 变为全天累计值
AUTO_FETCH_WINDOW = (9 * 60 + 25, 9 * 60 + 30)
AUTO_FETCH_TICK = 20


def http_json(url, referer="https://quote.eastmoney.com/"):
    req = Request(url, headers={"User-Agent": UA, "Referer": referer})
    with urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


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
    try:
        data = http_json(KLINE_URL.format(secid="1.000001"))
        klines = ((data.get("data") or {}).get("klines")) or []
        today = now.strftime("%Y-%m-%d")
        return any((line.split(",")[0] if line else "") == today for line in klines)
    except Exception:
        return True


def auction_window_status(now=None):
    now = now or datetime.now()
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


def fetch_auction_snapshot():
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


def fetch_yesterday_metric(stock):
    code = stock["code"]
    try:
        data = http_json(KLINE_URL.format(secid=secid_of(code)), referer="https://quote.eastmoney.com/")
        klines = ((data.get("data") or {}).get("klines")) or []
        if not klines:
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        prev = None
        for line in klines:
            parts = line.split(",")
            if parts and parts[0] < today:
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


def build_snapshot(auto=False):
    if not _fetch_lock.acquire(blocking=False):
        raise RuntimeError("已有抓取任务进行中，请稍后再试")
    try:
        return _build_snapshot(auto)
    finally:
        _fetch_lock.release()


def _build_snapshot(auto=False):
    print("[fetch] 开始抓取东方财富竞价数据", flush=True)
    snapshot = fetch_auction_snapshot()
    print("[fetch] 竞价金额前 200 已获取", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch_yesterday_metric, s): s for s in snapshot}
        yesterday_map = {}
        for future in concurrent.futures.as_completed(futures):
            stock = futures[future]
            try:
                yesterday_map[stock["code"]] = future.result()
            except Exception:
                yesterday_map[stock["code"]] = None

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
        "date": datetime.now().strftime("%Y-%m-%d"),
        "fetchedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "东方财富",
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

    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        with open(LATEST_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot_data, f, ensure_ascii=False)
        history_dir = os.path.join(DATA_DIR, "history")
        os.makedirs(history_dir, exist_ok=True)
        with open(os.path.join(history_dir, snapshot_data["date"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(snapshot_data, f, ensure_ascii=False)
    print("[fetch] 完成，共 %d 只" % len(stocks), flush=True)
    return snapshot_data


def auto_fetch_loop(enabled=True):
    if not enabled:
        return
    fetched_date = None
    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        valid, _ = auction_window_status(now)
        if fetched_date != today and valid:
            print("[auto] 交易日竞价已结束，开始自动抓取", flush=True)
            try:
                build_snapshot(auto=True)
                fetched_date = today
                print("[auto] 自动抓取完成", flush=True)
            except Exception as exc:
                print("[auto] 自动抓取失败: %r" % exc, flush=True)
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
            valid, message = auction_window_status()
            if not valid:
                self.send_json({"ok": False, "error": message}, status=400)
                return
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