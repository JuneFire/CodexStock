#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用通达信（tdx-mcp）免费拉取集合竞价快照，落成与 data/auction_top 一致的 schema。

为什么单独做这个脚本
--------------------
悟道 MCP 的 auction_market_scan 每天只有 50 次免费额度，而竞价是每天必须抓的数据。
tdx-mcp 直连通达信服务器，**不限次数**，并且 stock_quotes 返回的
`open` / `open_amount` / `opening_rush` 与悟道 `price` / `auctionAmount` / `obp`
逐条精确一致（2026-10-25 用 10-23 数据核验，6 只票全部对到 4 位有效数字）。

tdx 拿不到的东西（保持为 null，不要用 0 冒充）
---------------------------------------------
bidStrength(竞价强度) / limitBuyAmount(涨停委买) / limitBuyAmountAfter920 /
finalSealAmount(封单) / sealAmount / volumeRatio(量比) / floatCap / industry /
themes / obs(尾秒方向标签) / auctionNetAmount —— 这些只有悟道能给。
本脚本产出的文件用于「免费、可回补、可离线复算」，不能替代悟道的增强字段。

关键实测结论（别踩）
--------------------
1. `stock_quotes` 单次最多 **80 只**，传 150 只会静默截断到 80 条。
2. `stock_quotes_list` 虽然能翻全市场，但 `open_amount` 恒为 0、`sortType=29`(开盘金额)
   不生效 —— 只能拿来建代码表，**不能用来做竞价额排行**。
3. `stock_auction` 在盘后只保留 09:15:00 首笔（价格=昨收），拿不到 09:25 结果，
   所以本脚本不用它。
4. `open_amount` 单位是 **万元**；`stock_quotes` 的价格是真实价（只有 stock_kline 才 ×10）。
5. TDX 只保存"最近一个交易日"的竞价结果，所以 `--date` 仅作文件名/标注用，
   无法回补更早的日期（要回补历史只能用 data/auction_hist 里已有的悟道数据）。
6. **tdx-mcp 冷启动要 3~5 分钟**（实测 spawn+initialize = 292s，opentdx 在建连/选主站）。
   但**首次调用之后每次只要 0.1~0.2 秒** —— 全市场 70 批暖调用合计约 7 秒。
   所以 09:25 竞价抢时段的正确用法不是 `python 脚本.py`，而是 `--serve` 常驻：
   09:00 前把进程拉起来保温，09:25 直接发指令。每次重启脚本 = 白扔 5 分钟。

用法
----
    # 观察池 80 只以内，一次调用 4 秒
    python scripts/tdx_auction_fetch.py --codes 600519,300308,000001

    # 从文件读代码清单（每行一个，支持 600519 / sh600519 / 600519,贵州茅台）
    python scripts/tdx_auction_fetch.py --codes-file pool.txt

    # 全市场（约 5500 只，68 次调用，3~5 分钟），默认缓存代码表到 data/tdx_universe.json
    python scripts/tdx_auction_fetch.py --universe

    # 拿 10-23 的悟道结果做对账，看口径差多少
    python scripts/tdx_auction_fetch.py --universe --date 2026-10-23 \
        --out-dir data/auction_top_tdx --verify data/auction_top/2026-10-23.json

    # 常驻模式（竞价抢时段用这个，避免每次 5 分钟冷启动）
    python scripts/tdx_auction_fetch.py --serve
    # 然后在同一个进程里逐行发指令（每行一条，回一行 JSON）：
    #   ping
    #   universe 2026-10-23
    #   fetch 2026-10-23 600519,300308,000001
    #   quit
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_DIR = os.path.join(ROOT, "data", "auction_top_tdx")
UNIVERSE_CACHE = os.path.join(ROOT, "data", "tdx_universe.json")
NAMEDICT = os.path.join(ROOT, "data", "laiqiu", "stockdict.json")

TDX_EXE = os.environ.get("TDX_MCP_EXE") or os.path.join(
    os.path.expanduser("~"), ".local", "bin", "tdx-mcp.exe"
)

BATCH_LIMIT = 80          # 实测上限：超过 80 会被静默截断
LIST_PAGE = 800           # stock_quotes_list 分页大小
CATEGORY_A = 6            # 0=上证A 2=深证A 6=A股 8=科创板 12=北证A 14=创业板


# --------------------------------------------------------------------------
# TDX MCP stdio 客户端
# --------------------------------------------------------------------------
class TdxClient(object):
    """以子进程方式驱动 tdx-mcp，走 JSON-RPC over stdio（不依赖 mcp 客户端库）。"""

    def __init__(self, exe=TDX_EXE, verbose=False):
        if not os.path.exists(exe):
            raise SystemExit(
                "找不到 tdx-mcp 可执行文件：%s\n"
                "安装：uv tool install tdx-mcp --python 3.12 && "
                "uv pip install --python <toolenv>/Scripts/python.exe 'mcp[cli]<2'" % exe
            )
        self.exe = exe
        self.verbose = verbose
        self._id = 0
        self.restarts = 0
        self._spawn()

    def _spawn(self):
        self.proc = subprocess.Popen(
            [self.exe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "tdx_auction_fetch", "version": "1.0"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _restart(self):
        """opentdx 长连接会掉（'no available server'）——只能重启进程。
        冷启动约 292s，所以调用方必须先落盘、再续跑。"""
        self.restarts += 1
        print("  [restart #%d] tdx-mcp 连接掉了，正在重启（冷启动约 5 分钟）…" % self.restarts)
        try:
            self.proc.kill()
        except Exception:
            pass
        self._spawn()

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("tdx-mcp 提前退出（无输出）")
        return json.loads(line)

    def _rpc(self, method, params):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        return self._read()

    def call(self, tool, arguments, retries=1, allow_restart=True):
        """调一个工具，返回 list[dict]。掉线时重启进程再试一次。"""
        last = None
        attempt = 0
        while attempt <= retries:
            attempt += 1
            try:
                self._id += 1
                self._send({
                    "jsonrpc": "2.0",
                    "id": self._id,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                })
                resp = self._read()
                if resp.get("error"):
                    raise RuntimeError("%s: %s" % (tool, json.dumps(resp["error"], ensure_ascii=False)[:300]))
                result = resp.get("result") or {}
                if result.get("isError"):
                    raise RuntimeError("%s: isError %s" % (tool, str(result)[:300]))
                # 优先 structuredContent.result（完整列表）
                structured = result.get("structuredContent") or {}
                value = structured.get("result")
                if value is None:
                    # 否则把 content 的每一段当一条 JSON 解析
                    value = []
                    for item in result.get("content") or []:
                        text = (item or {}).get("text")
                        if not text:
                            continue
                        try:
                            value.append(json.loads(text))
                        except ValueError:
                            pass
                if isinstance(value, dict):
                    value = [value]
                return value or []
            except Exception as exc:
                last = exc
                msg = str(exc)
                fatal = ("no available server" in msg or "提前退出" in msg
                         or "BrokenPipe" in msg or "WinError" in msg)
                if self.verbose:
                    print("  [call fail %d/%d] %s" % (attempt, retries + 1, msg[:120]))
                if fatal and allow_restart and attempt <= retries:
                    self._restart()
                    continue
                if attempt <= retries:
                    time.sleep(1.5)
        raise RuntimeError("调用 %s 失败：%s" % (tool, last))

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.kill()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 代码表 / 名称表
# --------------------------------------------------------------------------
def market_of(code):
    """6 位代码 -> tdx 整数市场码：0=深 1=沪 2=北。"""
    if code.startswith("6"):
        return 1
    if code.startswith(("4", "8", "92")):
        return 2
    return 0


def load_names():
    try:
        with open(NAMEDICT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def build_universe(client, use_cache=True):
    """翻全市场代码表。只用 list 接口的 code 字段，不看它的行情字段。"""
    if use_cache and os.path.exists(UNIVERSE_CACHE):
        try:
            with open(UNIVERSE_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("codes"):
                print("复用代码表缓存 %s（%d 只，built_at=%s）"
                      % (UNIVERSE_CACHE, len(cached["codes"]), cached.get("built_at")))
                return cached["codes"]
        except Exception:
            pass

    codes, start = [], 0
    while True:
        rows = client.call("stock_quotes_list", {
            "category": CATEGORY_A, "start": start, "count": LIST_PAGE, "sortType": 0,
        })
        if not rows:
            break
        codes.extend(r["code"] for r in rows if r.get("code"))
        print("  代码表 start=%-5d 拿到 %d 条（累计 %d）" % (start, len(rows), len(codes)))
        if len(rows) < LIST_PAGE:
            break
        start += len(rows)
        if start > 8000:
            break
    codes = sorted(set(codes))
    with open(UNIVERSE_CACHE, "w", encoding="utf-8") as f:
        json.dump({"built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "codes": codes},
                  f, ensure_ascii=False)
    print("代码表已缓存 %s（%d 只）" % (UNIVERSE_CACHE, len(codes)))
    return codes


def parse_codes_arg(raw, codes_file):
    codes = []
    if raw:
        codes.extend(raw.split(","))
    if codes_file:
        with open(codes_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                codes.append(line.split(",")[0])
    out = []
    for item in codes:
        item = item.strip()
        for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
            if item.startswith(prefix):
                item = item[len(prefix):]
        if len(item) == 6 and item.isdigit() and item not in out:
            out.append(item)
    return out


# --------------------------------------------------------------------------
# 记录转换：tdx -> auction_top schema
# --------------------------------------------------------------------------
def limit_pct_of(code, name):
    if name and "ST" in name.upper():
        return 5.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("4", "8")):
        return 30.0
    if code.startswith("92"):
        return 30.0
    return 10.0


def status_of(change_pct, open_price, prev_close, limit_pct):
    """近似悟道 status 口径（阈值经 2026-10-23 的 102 条悟道数据校准）。"""
    if not prev_close or prev_close <= 0:
        return "flat"
    limit_up = round(prev_close * (1 + limit_pct / 100.0), 2)
    limit_down = round(prev_close * (1 - limit_pct / 100.0), 2)
    if open_price and open_price >= limit_up - 0.011:
        return "limit_up_open"
    if open_price and open_price <= limit_down + 0.011:
        return "limit_down_open"
    if change_pct is None:
        return "flat"
    if change_pct >= 3:
        return "hot_open"
    if change_pct <= -3:
        return "low_open"
    return "flat"


def pct_of(value):
    """'0.79%' / 0.79 -> 0.79"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("%", "").strip())
    except ValueError:
        return None


def to_record(q, names, trade_date, fetched_at):
    code = q.get("code")
    name = names.get(code)
    prev_close = q.get("pre_close")
    open_price = q.get("open")
    open_amount_wan = q.get("open_amount") or 0.0
    auction_amount = round(float(open_amount_wan) * 10000.0, 2)
    auction_volume = None
    if open_price and open_price > 0:
        auction_volume = round(auction_amount / open_price, 2)
    change_pct = None
    if prev_close and open_price and prev_close > 0:
        change_pct = round((open_price / prev_close - 1) * 100, 2)
    limit_pct = limit_pct_of(code or "", name or "")
    limit_up_price = round(prev_close * (1 + limit_pct / 100.0), 2) if prev_close else None
    status = status_of(change_pct, open_price, prev_close, limit_pct)

    record = {
        "code": code,
        "name": name,
        "industry": None,                 # tdx 不提供
        "price": open_price,              # 竞价撮合价
        "changePct": change_pct,
        "open": open_price,
        "prevClose": prev_close,
        "auctionAmount": auction_amount,  # 元
        "auctionVolume": auction_volume,  # 股（= 额 / 撮合价，非交易所原始竞价量）
        "turnover": None,                 # 竞价换手需要流通股，tdx 不给
        "volumeRatio": None,              # 量比 tdx 不给
        "floatCap": None,
        "totalCap": None,
        "rank": None,                     # 排序后回填
        "tradeDate": trade_date,
        "fetchedAt": fetched_at,
        "sealAmount": None,
        "sealIsLimitBid": None,
        "ps": None,
        "plt": None,
        "obp": pct_of(q.get("opening_rush")),   # 尾秒竞价涨幅%，对应悟道 obp
        "obs": None,                            # 尾秒方向标签只有悟道有
        "themes": [],
        "trf": None,
        "limitPct": limit_pct,
        "limitUpPrice": limit_up_price,
        "bidStrength": None,
        "isLimitUpOpen": status == "limit_up_open",
        "status": status,
        "market": {0: "sz", 1: "sh", 2: "bj"}.get(q.get("market"), None),
        "preLimitBuyAmount": None,
        "limitBuyAmountAfter920": None,
        "limitBuyAmount": None,
        "limitBuyAmountAfter920Available": None,
        "finalSealAmount": None,
        "auctionNetAmount": None,
        "src": "tdx",
        # ---- tdx 独有、悟道文件里没有的字段 ----
        "dayTurnover": q.get("turnover"),        # 全天换手%
        "dayAmount": q.get("amount"),            # 全天成交额（元）
        "dayVol": q.get("vol"),                  # 全天成交量（股）
        "depth": q.get("depth"),                  # 委比
        "min2Amount": q.get("min2_amount"),      # 最近 2 分钟成交额
        "shortTurnover": q.get("short_turnover"),
    }
    return record


def fetch_quotes(client, codes, sleep=0.0, partial_path=None, resume=False):
    """按 80 只一批拉，返回原始 quotes 列表。

    边拉边写 jsonl（partial_path），配合 resume=True 就能在掉线重启后
    从断点继续，不用为一次 5 分钟的冷启动重拉 5000 只。
    """
    got = {}
    if resume and partial_path and os.path.exists(partial_path):
        with open(partial_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    q = json.loads(line)
                except ValueError:
                    continue
                if q.get("code"):
                    got[q["code"]] = q
        print("  续跑：断点已有 %d 条" % len(got))

    pending = [c for c in codes if c not in got]
    total = len(pending)
    print("  待拉 %d 只（跳过已入库 %d）" % (total, len(codes) - total))
    pf = open(partial_path, "a", encoding="utf-8") if partial_path else None
    failed = 0
    try:
        for i in range(0, total, BATCH_LIMIT):
            chunk = pending[i:i + BATCH_LIMIT]
            pairs = [[market_of(c), c] for c in chunk]
            try:
                rows = client.call("stock_quotes", {"code_list": pairs}, retries=2)
            except Exception as exc:
                failed += len(chunk)
                print("  本批失败 %d 只，跳过：%s" % (len(chunk), str(exc)[:120]))
                rows = []
            for r in rows:
                if not r.get("code"):
                    continue
                got[r["code"]] = r
                if pf:
                    pf.write(json.dumps(r, ensure_ascii=False) + "\n")
            if pf:
                pf.flush()
            print("  报价 %d/%d 本批 %d 条（累计 %d）"
                  % (min(i + len(chunk), total), total, len(rows), len(got)))
            if sleep:
                time.sleep(sleep)
    finally:
        if pf:
            pf.close()
    if failed:
        print("  ⚠️ 共 %d 只未拿到（可重跑 --resume 补齐）" % failed)
    return list(got.values())


# --------------------------------------------------------------------------
# 对账
# --------------------------------------------------------------------------
def verify_against(tdx_records, wudao_path, top_n=0):
    with open(wudao_path, encoding="utf-8") as f:
        ref = json.load(f)
    ref_map = {r["code"]: r for r in ref.get("records") or []}
    mine = {r["code"]: r for r in tdx_records}

    common = [c for c in ref_map if c in mine]
    if top_n:
        common = common[:top_n]
    print("\n对账 %s ↔ %s" % ("tdx", os.path.basename(wudao_path)))
    print("悟道 %d 条 / tdx %d 条 / 交集 %d 条" % (len(ref_map), len(mine), len(common)))
    print("%-8s %-8s %14s %14s %10s  %-9s %-9s" %
          ("代码", "名称", "tdx竞价额", "悟道竞价额", "偏差", "tdx状态", "悟道状态"))
    bad = 0
    for code in common:
        a, b = mine[code], ref_map[code]
        ta = a.get("auctionAmount") or 0
        tb = b.get("auctionAmount") or 0
        diff = (ta - tb) / tb * 100 if tb else 0
        flag = "" if abs(diff) < 0.5 else "  <<<"
        if a.get("status") != b.get("status"):
            flag += " [status差]"
            bad += 1
        print("%-8s %-8s %14.0f %14.0f %9.3f%%  %-9s %-9s%s" %
              (code, (a.get("name") or "")[:6], ta, tb, diff, a.get("status"), b.get("status"), flag))
    print("状态标签不一致：%d / %d" % (bad, len(common)))


# --------------------------------------------------------------------------
def write_snapshot(records, out_dir, trade_date, fetched_at):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s.json" % trade_date)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": trade_date, "fetchedAt": fetched_at,
                   "source": "tdx_quotes", "records": records},
                  f, ensure_ascii=False)
    return path


def run_fetch(client, codes, trade_date, out_dir, limit=0, sleep=0.0, resume=False):
    """拉一批代码 -> 转 schema -> 写出文件，返回 (path, records)。"""
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(out_dir, exist_ok=True)
    partial = os.path.join(out_dir, "partial_%s.jsonl" % trade_date)
    quotes = fetch_quotes(client, codes, sleep=sleep, partial_path=partial, resume=resume)
    names = load_names()
    records = [to_record(q, names, trade_date, fetched_at) for q in quotes]
    records = [r for r in records if r["auctionAmount"] and r["auctionAmount"] > 0]
    records.sort(key=lambda r: -r["auctionAmount"])
    for i, r in enumerate(records, 1):
        r["rank"] = i
    if limit:
        records = records[:limit]
    path = write_snapshot(records, out_dir, trade_date, fetched_at)
    return path, records


def serve_loop(client, out_dir, sleep=0.0):
    """常驻模式：从 stdin 逐行读指令，每行回一条 JSON。

    指令：ping | universe <date> | fetch <date> <code,code,...> | quit
    """
    print(json.dumps({"ready": True, "cold_start_note": "进程已保温，后续调用约 0.1s/批"},
                     ensure_ascii=False), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        try:
            if cmd == "quit":
                print(json.dumps({"ok": True, "bye": True}), flush=True)
                return 0
            if cmd == "ping":
                print(json.dumps({"ok": True, "ts": datetime.now().strftime("%H:%M:%S")}), flush=True)
                continue
            if cmd == "universe":
                trade_date = parts[1] if len(parts) > 1 else datetime.now().strftime("%Y-%m-%d")
                t0 = time.time()
                codes = build_universe(client, use_cache=True)
                path, records = run_fetch(client, codes, trade_date, out_dir, sleep=sleep)
                print(json.dumps({"ok": True, "n": len(records), "path": path,
                                  "secs": round(time.time() - t0, 1)}, ensure_ascii=False), flush=True)
                continue
            if cmd == "fetch":
                trade_date = parts[1]
                codes = parse_codes_arg(parts[2] if len(parts) > 2 else "", None)
                t0 = time.time()
                path, records = run_fetch(client, codes, trade_date, out_dir, sleep=sleep)
                print(json.dumps({"ok": True, "n": len(records), "path": path,
                                  "secs": round(time.time() - t0, 1),
                                  "top": [{"code": r["code"], "name": r["name"],
                                           "amt": r["auctionAmount"], "chg": r["changePct"],
                                           "status": r["status"]} for r in records[:5]]},
                                 ensure_ascii=False), flush=True)
                continue
            raise ValueError("未知指令: %s" % cmd)
        except Exception as exc:
            print(json.dumps({"ok": False, "cmd": line, "error": str(exc)[:300]},
                             ensure_ascii=False), flush=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="用通达信免费拉取集合竞价快照")
    ap.add_argument("--codes", help="逗号分隔的 6 位代码")
    ap.add_argument("--codes-file", help="代码清单文件（每行一个）")
    ap.add_argument("--universe", action="store_true", help="全市场（约 5500 只，3~5 分钟）")
    ap.add_argument("--refresh-universe", action="store_true", help="强制刷新代码表缓存")
    ap.add_argument("--date", help="标记日期（默认今天）；TDX 只保留最近交易日")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--limit", type=int, default=0, help="按竞价额只保留前 N 只（0=全部）")
    ap.add_argument("--sleep", type=float, default=0.0, help="每批之间休眠秒数")
    ap.add_argument("--resume", action="store_true",
                    help="从 partial_<date>.jsonl 断点续跑（掉线重启后必用）")
    ap.add_argument("--verify", help="与悟道 auction_top JSON 对账")
    ap.add_argument("--serve", action="store_true",
                    help="常驻模式：从 stdin 读指令（ping/universe/fetch/quit），避免重复冷启动")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    trade_date = args.date or datetime.now().strftime("%Y-%m-%d")
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    client = TdxClient(verbose=args.verbose)
    if args.serve:
        try:
            return serve_loop(client, args.out_dir, sleep=args.sleep)
        finally:
            client.close()
    try:
        if args.universe:
            codes = build_universe(client, use_cache=not args.refresh_universe)
        else:
            codes = parse_codes_arg(args.codes, args.codes_file)
            if not codes:
                raise SystemExit("没有代码可查：请给 --codes / --codes-file / --universe")
        print("待查 %d 只" % len(codes))

        t0 = time.time()
        out_path, records = run_fetch(client, codes, trade_date, args.out_dir,
                                      limit=args.limit, sleep=args.sleep,
                                      resume=args.resume)
        print("报价完成：%d 条，耗时 %.1fs" % (len(records), time.time() - t0))
    finally:
        client.close()

    print("已写出 %s（%d 条，竞价额合计 %.2f 亿）"
          % (out_path, len(records), sum(r["auctionAmount"] for r in records) / 1e8))

    if records:
        print("\n竞价额前 10：")
        print("%-8s %-8s %10s %8s %8s %-9s" % ("代码", "名称", "竞价额(亿)", "涨幅%", "尾秒%", "状态"))
        for r in records[:10]:
            print("%-8s %-8s %10.3f %8.2f %8s %-9s" %
                  (r["code"], (r["name"] or "")[:6], r["auctionAmount"] / 1e8,
                   r["changePct"] or 0,
                   "-" if r["obp"] is None else "%.2f" % r["obp"], r["status"]))

    if args.verify:
        verify_against(records, args.verify)
    return 0


if __name__ == "__main__":
    sys.exit(main())
