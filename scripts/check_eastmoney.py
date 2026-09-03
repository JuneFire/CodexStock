#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
腾讯云部署 · 东方财富接口连通性专项检查

背景：本地开发机是"腾讯行情通、东财被连接层拦截"；但腾讯云环境可能相反
      （腾讯接口被限制、东财反而通）。本脚本在腾讯云上跑一次，判断东财
      各关键接口是否可用，从而知道系统会走哪个数据源。

用法（腾讯云服务器上执行）：
    cd /opt/stock_system/CodexStock
    /root/venv/bin/python scripts/check_eastmoney.py

只做 HTTP 探测，不修改任何文件、不写库。
"""
import contextlib
import io
import sys
import time

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 8

# 东财关键域名探测（与 backend/server.py 数据源对应）
CHECKS = [
    {
        "name": "push2.eastmoney.com (全市场快照)",
        "url": "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&fltt=2&invt=2&fid=f6&fs=m:0+t:6&fields=f2,f3,f6,f12,f14",
        "desc": "竞价快照降级源",
    },
    {
        "name": "push2his.eastmoney.com (日K线)",
        "url": "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&ut=fa5fd1943c7b386f172d6893dbfba10b&fields1=f1,f2,f3&fields2=f51,f52,f53&klt=101&fqt=1&end=20500101&lmt=3",
        "desc": "昨日成交额/换手降级源",
    },
    {
        "name": "push2ex.eastmoney.com (涨停池)",
        "url": "https://push2ex.eastmoney.com/getYesterdayZTPool",
        "desc": "复盘涨停池/连板（akshare 实际域名）",
    },
    {
        "name": "quote.eastmoney.com (东财首页Cookie)",
        "url": "https://quote.eastmoney.com/",
        "desc": "session 预热用的首页",
    },
]


def check_http(name, url):
    t0 = time.time()
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}, timeout=TIMEOUT)
        dt = (time.time() - t0) * 1000
        if resp.status_code == 200 and len(resp.text) > 50:
            return True, dt, "HTTP %d, %d bytes" % (resp.status_code, len(resp.text))
        return False, dt, "HTTP %d (内容过短)" % resp.status_code
    except requests.exceptions.ProxyError:
        return False, (time.time() - t0) * 1000, "被代理拦截 (ProxyError)"
    except requests.exceptions.ConnectTimeout:
        return False, (time.time() - t0) * 1000, "连接超时 (可能被防火墙/安全组拦)"
    except requests.exceptions.ConnectionError as exc:
        return False, (time.time() - t0) * 1000, "连接失败: %r" % exc
    except Exception as exc:
        return False, (time.time() - t0) * 1000, "%r" % exc


def check_akshare_zt():
    """akshare 涨停池实测（走 push2ex + datacenter-web）。"""
    try:
        import akshare as ak
    except Exception as exc:
        return False, 0, "akshare 未安装: %r" % exc
    t0 = time.time()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            df = ak.stock_zt_pool_previous_em(date="20260801")
        dt = (time.time() - t0) * 1000
        if df is not None and len(df) > 0:
            return True, dt, "返回 %d 行 (昨日涨停池可用)" % len(df)
        return False, dt, "返回空"
    except Exception as exc:
        return False, (time.time() - t0) * 1000, "%r" % exc


def main():
    print("=" * 62)
    print("东方财富接口连通性检查（腾讯云）")
    print("时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 62)
    print()

    results = []
    print("[1] 东财各域名直连")
    print("-" * 62)
    for c in CHECKS:
        ok, ms, detail = check_http(c["name"], c["url"])
        results.append((c["name"], ok))
        print("  %s %s" % ("✅" if ok else "❌", c["name"]))
        print("       %s | %6.0f ms | %s" % (c["desc"], ms, detail))
    print()

    print("[2] akshare 涨停池实测")
    print("-" * 62)
    ok, ms, detail = check_akshare_zt()
    results.append(("akshare涨停池", ok))
    print("  %s akshare.stock_zt_pool_previous_em" % ("✅" if ok else "❌"))
    print("       %6.0f ms | %s" % (ms, detail))
    print()

    print("=" * 62)
    print("[3] 结论")
    print("=" * 62)
    push2 = next((ok for n, ok in results if n.startswith("push2.eastmoney")), False)
    zt = next((ok for n, ok in results if "涨停池" in n or n == "akshare涨停池"), False)
    if zt and push2:
        print("  ✅ 东财全接口可用 → 竞价快照/涨停池/复盘均走东财，功能完整")
    elif zt:
        print("  ✅ 东财涨停池可用（但 push2 直连被拦）→ 复盘涨停池/连板正常，竞价走腾讯主源")
    elif push2:
        print("  ⚠️  push2 可用但涨停池不可用 → 竞价快照可降级东财，但复盘涨停池抓取会失败")
    else:
        print("  ❌ 东财全部不可达 → 复盘涨停池/连板会抓取失败，预案质量下降")
        print("     排障：")
        print("       - ping push2.eastmoney.com 看是否 DNS/网络问题")
        print("       - 腾讯云安全组出站规则是否放行 80/443")
        print("       - 是否需 HTTP 代理: export https_proxy=http://...")
        print("     另：如果腾讯行情(qt.gtimg.cn)通，竞价/封单/指数仍可用，只是复盘受限")
    print()
    print("说明: 本脚本仅探测连通性，不修改配置。若东财不通而腾讯通，系统会自动走腾讯主源。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
