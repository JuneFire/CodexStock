#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回测：每日竞价得分第一名，当天是否大面

用 data/history/ 的历史快照，找出每天竞价 score 排名第一（并列则都取）的股票，
拉取该股当天的日K收盘价，计算"竞价价买入 → 收盘"的涨跌，判断是封住/炸板/大面。

用法：
    cd /opt/stock_system/CodexStock
    /root/venv/bin/python scripts/backtest_top1.py

只读 data/history 和行情接口，不写任何文件。
"""
import glob
import json
import os
import sys
import io

if hasattr(sys.stdout, "buffer"):  # Windows 终端用 UTF-8 输出避免乱码
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 兼容 backend 导入
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import akshare as ak

STOCKS = []


def load_top1(date_str):
    """返回该日 score 第一的股票列表（并列全取）。"""
    path = os.path.join("data", "history", date_str + ".json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception:
        return []
    stocks = snap.get("stocks") or []
    if not stocks:
        return []
    top_score = max((s.get("score") or 0) for s in stocks)
    return [s for s in stocks if (s.get("score") or 0) >= top_score and top_score > 0]


def tx_symbol(code):
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("0", "2", "3")):
        return "sz" + code
    return "bj" + code


def bars_of(date_str, code, span_days=12):
    """拉 date_str 起 span_days 天的日K。返回 [{date,open,high,low,close}, ...]（升序）。"""
    import datetime as _dt
    try:
        d0 = _dt.date.fromisoformat(date_str)
    except Exception:
        return []
    d1 = d0 + _dt.timedelta(days=span_days)
    try:
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            df = ak.stock_zh_a_hist_tx(
                symbol=tx_symbol(code),
                start_date=d0.strftime("%Y%m%d"),
                end_date=d1.strftime("%Y%m%d"),
                adjust="",
            )
        if df is None or df.empty:
            return []
        out = []
        for _, row in df.iterrows():
            try:
                out.append({
                    "date": str(row.get("date"))[:10],
                    "open": float(row.get("open")),
                    "high": float(row.get("high")),
                    "low": float(row.get("low")),
                    "close": float(row.get("close")),
                })
            except Exception:
                continue
        out = [b for b in out if b["date"] >= date_str]
        out.sort(key=lambda b: b["date"])
        return out
    except Exception:
        return []


def main():
    dates = sorted(os.path.basename(p)[:-5] for p in glob.glob("data/history/*.json"))
    dates = [d for d in dates if d >= "2026-08-10"]  # 只回测有效竞价日
    if not dates:
        print("data/history/ 无历史快照，请先运行服务抓取几日")
        return

    print("=" * 78)
    print("竞价得分第一名 · 当天表现回测")
    print("=" * 78)
    print()

    rows = []
    for date_str in dates:
        tops = load_top1(date_str)
        if not tops:
            print("  %s: 无有效快照" % date_str)
            continue
        for s in tops:
            code = s.get("code")
            name = s.get("name")
            bid = s.get("price")
            bid_chg = s.get("changePct")
            bars = bars_of(date_str, code)
            if not bars or not bid:
                print("  %s %s(%s) 竞价价%.2f → K线缺失" % (date_str, name, code, bid))
                continue
            day0 = bars[0]
            day1 = bars[1] if len(bars) > 1 else None
            close = day0["close"]
            # 当日盘中诊断（违反 T+1，仅用于判断封住/炸板，不可当作收益）
            pnl_intraday = (close / bid - 1) * 100 if bid else None
            # T+1 合规收益：D 日竞价价买入 → D+1 卖出（最早可卖时点）
            pnl = (day1["open"] / bid - 1) * 100 if (day1 and bid) else None
            pnl_t1close = (day1["close"] / bid - 1) * 100 if (day1 and bid) else None
            # 分类
            if bid_chg is not None and bid_chg >= 9.5:
                cat = "竞价一字/顶板"
            elif bid_chg is not None and bid_chg >= 5:
                cat = "竞价高开"
            else:
                cat = "竞价平/低开"
            if pnl is not None:
                if pnl >= 0:
                    outcome = "赚"
                elif pnl >= -3:
                    outcome = "小亏"
                elif pnl >= -7:
                    outcome = "[大面]"
                else:
                    outcome = "[巨亏]"
            else:
                outcome = "?"
            rows.append({
                "date": date_str, "code": code, "name": name,
                "bid": bid, "bid_chg": bid_chg, "close": close,
                "pnl": pnl, "pnl_intraday": pnl_intraday, "pnl_t1close": pnl_t1close,
                "cat": cat, "outcome": outcome,
            })
            print("  %s %-6s %-6s 竞价%.2f(%+.1f%%) 当日收%.2f(%+.1f%%) → 次日开%+.1f%%/收%+.1f%%  [%s] %s" % (
                date_str, code, name, bid, bid_chg or 0, close,
                pnl_intraday if pnl_intraday is not None else 0,
                pnl if pnl is not None else 0,
                pnl_t1close if pnl_t1close is not None else 0, cat, outcome))

    # 汇总
    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)
    if not rows:
        print("无数据")
        return
    rows = [r for r in rows if r.get("pnl") is not None]
    if not rows:
        print("无 T+1 合规样本（缺次日K线）")
        return
    n = len(rows)
    bad = [r for r in rows if (r["pnl"] or 0) < -3]
    big = [r for r in rows if (r["pnl"] or 0) < -7]
    ok = [r for r in rows if (r["pnl"] or 0) >= 0]
    intr = sum(r["pnl_intraday"] or 0 for r in rows) / n
    t1c = [r["pnl_t1close"] for r in rows if r.get("pnl_t1close") is not None]
    print("  样本数: %d 天第一（T+1 合规口径）" % n)
    print("  竞价买→次日开盘卖 赚钱: %d (%.0f%%)" % (len(ok), len(ok) / n * 100))
    print("  大面(<-3%%): %d (%.0f%%)" % (len(bad), len(bad) / n * 100))
    print("  巨亏(<-7%%): %d (%.0f%%)" % (len(big), len(big) / n * 100))
    avg = sum(r["pnl"] or 0 for r in rows) / n
    print("  平均盈亏(次日开盘卖): %+.1f%%" % avg)
    if t1c:
        print("  平均盈亏(次日收盘卖): %+.1f%%" % (sum(t1c) / len(t1c)))
    print("  [诊断]平均当日收盘盈亏: %+.1f%%（违反T+1，仅看封板质量，不可当收益）" % intr)
    if bad:
        print()
        print("  大面明细:")
        for r in bad:
            print("    %s %s(%s) 竞价%.2f→收%.2f %+.1f%% [%s]" % (
                r["date"], r["name"], r["code"], r["bid"], r["close"], r["pnl"], r["cat"]))
    print()
    print("说明: 主口径盈亏 = (次日开盘价 / 当日竞价价 - 1)，符合 A 股 T+1。")
    print("      当日收盘盈亏仅作封板质量诊断，当日买入当日不可卖。")


if __name__ == "__main__":
    main()
