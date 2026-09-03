#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
腾讯云部署 · 连通性检查脚本

用途：部署"量化选股器"后，逐项检测外部数据源与本机服务是否连通，
     判断在当前网络环境下系统能否正常抓取竞价/复盘数据。

用法：
    cd /opt/stock_system/CodexStock
    /root/venv/bin/python scripts/check_connectivity.py

输出：每个检查项 ✅/❌ + 耗时，末尾给出综合结论与排障建议。

注意：本脚本只做连通性探测（HTTP 请求），不修改任何文件、不写库。
"""
import socket
import sys
import time
from urllib.parse import urlparse

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 8

# 各数据源探测 URL（与 backend/server.py 一致）
CHECKS = [
    {
        "name": "腾讯行情直连 qt.gtimg.cn",
        "url": "https://qt.gtimg.cn/q=sh000001,sz399001",
        "label": "腾讯", "desc": "竞价快照/封单/指数 主源",
    },
    {
        "name": "腾讯全市场快照(akshare)",
        "url": "akshare:stock_zh_a_spot_tx", "label": "腾讯", "desc": "竞价金额前200主源",
    },
    {
        "name": "东方财富 push2 直连",
        "url": "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&fltt=2&invt=2&fid=f6&fs=m:0+t:6&fields=f2,f3,f6,f12,f14",
        "label": "东财", "desc": "全市场快照 兜底源",
    },
    {
        "name": "东方财富涨停池(akshare)",
        "url": "akshare:stock_zt_pool_previous_em", "label": "东财", "desc": "复盘涨停池/连板",
    },
    {
        "name": "新浪全市场(akshare)",
        "url": "akshare:stock_zh_a_spot", "label": "新浪", "desc": "全市场快照 兜底源",
    },
    {
        "name": "新浪行业板块(akshare)",
        "url": "akshare:stock_sector_spot", "label": "新浪", "desc": "板块映射",
    },
]

SERVICE_URL = "http://127.0.0.1:8010/api/latest"


def check_http(name, url):
    """直连 HTTP 探测，返回 (ok, ms, detail)。"""
    t0 = time.time()
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=TIMEOUT)
        dt = (time.time() - t0) * 1000
        if resp.status_code == 200 and len(resp.text) > 50:
            return True, dt, "HTTP %d, %d bytes" % (resp.status_code, len(resp.text))
        return False, dt, "HTTP %d (内容过短)" % resp.status_code
    except requests.exceptions.ProxyError:
        return False, (time.time() - t0) * 1000, "被代理拦截 (ProxyError)"
    except requests.exceptions.ConnectTimeout:
        return False, (time.time() - t0) * 1000, "连接超时"
    except requests.exceptions.ConnectionError:
        return False, (time.time() - t0) * 1000, "连接失败 (网络不通/域名被限)"
    except Exception as exc:
        return False, (time.time() - t0) * 1000, "%r" % exc


def check_akshare(ak_func_name, desc):
    """akshare 封装接口探测（会真实拉取少量数据）。返回 (ok, ms, detail)。"""
    try:
        import akshare as ak
    except Exception as exc:
        return False, 0, "akshare 未安装: %r" % exc
    if not hasattr(ak, ak_func_name):
        return False, 0, "akshare %s 不存在" % ak_func_name
    import contextlib
    import io
    t0 = time.time()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            fn = getattr(ak, ak_func_name)
            if ak_func_name == "stock_zt_pool_previous_em":
                df = fn(date="20260801")  # 用历史日期避免交易日限制
            else:
                df = fn()
        dt = (time.time() - t0) * 1000
        if df is not None and (hasattr(df, "__len__") and len(df) > 0):
            return True, dt, "返回 %d 行" % len(df)
        return False, dt, "返回空"
    except Exception as exc:
        return False, (time.time() - t0) * 1000, "%r" % exc


def check_service():
    """本机 8010 服务是否在运行。"""
    t0 = time.time()
    try:
        resp = requests.get(SERVICE_URL, timeout=3)
        dt = (time.time() - t0) * 1000
        if resp.status_code == 200:
            return True, dt, "HTTP %d (服务在运行)" % resp.status_code
        return False, dt, "HTTP %d" % resp.status_code
    except Exception as exc:
        return False, (time.time() - t0) * 1000, "连接失败: %r (服务未启动?)" % exc


def check_mysql():
    """MySQL 连通性。返回 (ok, ms, detail)。"""
    try:
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
        import db
    except Exception:
        return None, 0, "db 模块不可用"
    t0 = time.time()
    ok = db.is_available()
    dt = (time.time() - t0) * 1000
    if ok:
        return True, dt, "MySQL 已连接"
    # is_available 可能只是初始 False，主动试一次连接
    try:
        conn = db._connect()
        conn.close()
        return True, dt, "MySQL 可连接"
    except Exception as exc:
        return False, dt, "连接失败: %r" % exc


def main():
    print("=" * 60)
    print("量化选股器 · 腾讯云部署连通性检查")
    print("时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)
    print()

    results = []
    # 1. 数据源探测
    print("[1] 外部数据源连通性")
    print("-" * 60)
    for c in CHECKS:
        url = c["url"]
        if url.startswith("akshare:"):
            ok, ms, detail = check_akshare(url.split(":", 1)[1], c["desc"])
        else:
            ok, ms, detail = check_http(c["name"], url)
        mark = "OK " if ok else "FAIL"
        results.append((c["name"], c["label"], ok))
        print("  %s [%s] %s" % ("✅" if ok else "❌", mark, c["name"]))
        print("       %s | %6.0f ms | %s" % (c["desc"], ms, detail))
    print()

    # 2. 本机服务
    print("[2] 本机服务 (8010)")
    print("-" * 60)
    ok, ms, detail = check_service()
    results.append(("本机服务 8010", "local", ok))
    print("  %s [%s] 本机服务" % ("✅" if ok else "❌", "OK " if ok else "FAIL"))
    print("       %6.0f ms | %s" % (ms, detail))
    print()

    # 3. MySQL
    print("[3] MySQL 数据库")
    print("-" * 60)
    ok, ms, detail = check_mysql()
    results.append(("MySQL", "local", ok))
    if ok is None:
        print("  ⚠️  [SKIP] %s" % detail)
    else:
        print("  %s [%s] MySQL" % ("✅" if ok else "❌", "OK " if ok else "FAIL"))
        print("       %6.0f ms | %s" % (ms, detail))
    print()

    # 4. 综合结论
    print("=" * 60)
    print("[4] 综合结论")
    print("=" * 60)
    tx_ok = any(r[1] == "腾讯" and r[2] for r in results)
    em_ok = any(r[1] == "东财" and r[2] for r in results)
    sina_ok = any(r[1] == "新浪" and r[2] for r in results)
    svc_ok = next((r[2] for r in results if r[1] == "local"), False)

    if tx_ok:
        print("  ✅ 腾讯行情可用 → 竞价快照/封单/指数可正常工作（主源）")
    else:
        print("  ⚠️  腾讯行情不可用 → 竞价抓取会降级到东财/akshare 兜底")

    if em_ok:
        print("  ✅ 东财可用 → 涨停池/连板复盘可正常工作（兜底源 OK）")
    else:
        print("  ⚠️  东财不可用 → 复盘涨停池/连板可能抓取失败，预案质量下降")

    if sina_ok:
        print("  ✅ 新浪可用 → 板块映射/全市场兜底 OK")
    else:
        print("  ⚠️  新浪不可用 → 板块映射可能缺失")

    if svc_ok:
        print("  ✅ 本机服务在运行 → 浏览器访问 http://<服务器IP>:8010")
    else:
        print("  ❌ 本机服务未启动 → 运行: systemctl start quant 或 python backend/server.py")

    mysql_ok = next((r[2] for r in results if r[1] == "local" and r[0] == "MySQL"), None)
    if mysql_ok is True:
        print("  ✅ MySQL 可用 → 历史/复盘/封单完整入库")
    elif mysql_ok is False:
        print("  ⚠️  MySQL 不可用 → 历史页/复盘降级为 JSON 归档，封单不落库")
    print()

    # 5. 排障建议
    print("=" * 60)
    print("[5] 排障建议")
    print("=" * 60)
    if not tx_ok and not em_ok and not sina_ok:
        print("  全部外部数据源不可达，可能原因：")
        print("    - 服务器无外网或 DNS 解析失败: ping qt.gtimg.cn / curl -v https://qt.gtimg.cn")
        print("    - 安全组未放行 80/443 出站: 腾讯云控制台 → 安全组 → 出站规则")
        print("    - 需要 HTTP 代理: export https_proxy=http://... ")
    elif not tx_ok:
        print("  腾讯行情不可达但东财/新浪可用，属正常（部分云环境限制腾讯域名）：")
        print("    - 系统会自动降级到东财/akshare，功能仍可用但数据源不同")
        print("    - 若想用腾讯主源，检查安全组/代理是否拦截 qt.gtimg.cn")
    if not svc_ok:
        print("  服务未启动或端口不通：")
        print("    - 启动: sudo systemctl start quant")
        print("    - 防火墙: sudo ufw allow 8010")
        print("    - 验证: curl http://127.0.0.1:8010/api/latest")
    print()
    print("说明: 本脚本仅探测连通性，不修改任何配置。完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
