# -*- coding: utf-8 -*-
"""标准工作流回测：D 日 15:00 收盘后选股 → D+1 开盘买入 → D+2 竞价(开盘)卖出。

规则（用户口径，2026-11 定）：
  1. D 日收盘后遍历当日所有涨停股（lb 1~3），按二板战法条件选出候选池
  2. D+1 早上开盘买入（09:30 市价，成交价 = D+1 开盘价）
     · 若 D+1 开盘即涨停（gap >= 涨停幅度-0.6%）→ 买不进，剔除
  3. D+2 早上竞价卖出（成交价 = D+2 开盘价）
  4. A 股 T+1：D+1 买、D+2 卖，合规

收益定义：
  ret = (D+2 开盘价 / D+1 开盘价 - 1) × 100

数据依赖：
  reports/backtest_ermian_200.json   逐票收益（t1open / t2open / grade / lb / gap…）
  data/market/ztpool_bt/<date>.json  涨停池（limitUpTime / openTimes / sealMoney → 板型、封单）
"""
import glob
import json
import os
import statistics as stt
from collections import defaultdict

BT = "reports/backtest_ermian_200.json"
ZP = "data/market/ztpool_bt"

GOOD = ("最优", "健康")


def limit_pct(code, name=""):
    c = str(code)
    if c.startswith(("300", "301", "688", "689")):
        return 20.0
    if c.startswith(("4", "8", "920")):
        return 30.0
    if "ST" in (name or "").upper():
        return 5.0
    return 10.0


def board_type(z):
    """D 日板型：一字板 / 早板 / 晚板 / 换手板。"""
    t = (z.get("limitUpTime") or "")[:5]
    try:
        ot = int(z.get("openTimes") or 0)
    except Exception:
        ot = 0
    if ot > 0:
        return "换手板"
    if not t:
        return "未知"
    if t <= "09:25":
        return "一字板"
    if t <= "10:30":
        return "早板"
    if t <= "13:00":
        return "午后板"
    return "晚板"


def load():
    d = json.load(open(BT, encoding="utf-8"))
    rows = [r for r in d["rows"] if r.get("b1o") is not None]
    pool = {}
    for p in glob.glob(os.path.join(ZP, "*.json")):
        try:
            o = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        dt = o.get("date") or os.path.basename(p)[:-5]
        for r in o.get("rows") or []:
            pool[(dt, r.get("code"))] = r
    for r in rows:
        z = pool.get((r["date"], r["code"])) or {}
        r["btype"] = board_type(z)
        r["sealYi"] = float(z.get("sealMoney") or 0) / 1e8
        r["limitUpTime"] = z.get("limitUpTime")
        lp = limit_pct(r["code"], r.get("name"))
        r["limitPct"] = lp
        # D+1 开盘即涨停 → 买不进
        r["buyable"] = (r.get("gap") or 0) < (lp - 0.6)
        r["ret"] = r["b1o"]          # 主口径：D+2 竞价卖
        r["retClose"] = r.get("b1c")  # 备选：D+2 收盘卖
        r["gapBand"] = ("低开<=-2" if (r.get("gap") or 0) <= -2 else
                        "低开-2~0" if (r.get("gap") or 0) < 0 else
                        "平开0~2" if (r.get("gap") or 0) < 2 else
                        "高开2~5" if (r.get("gap") or 0) < 5 else "高开>=5")
        r["sealBand"] = ("封单>=5亿" if r["sealYi"] >= 5 else
                         "封单1~5亿" if r["sealYi"] >= 1 else "封单<1亿")
    return rows


def avg(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 2) if v else None


def win(v):
    v = [x for x in v if x is not None]
    return round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None


def main():
    rows = load()
    dates = sorted(set(r["date"] for r in rows))
    buy = [r for r in rows if r["buyable"]]

    def periods(rs, n=8):
        out = []
        for i in range(n):
            part = set(dates[i * len(dates) // n:(i + 1) * len(dates) // n])
            it = [r for r in rs if r["date"] in part]
            out.append(avg([x["ret"] for x in it]) if len(it) >= 8 else None)
        return out

    def show(tag, rs, minn=25, pers=True):
        if len(rs) < minn:
            return
        v = [r["ret"] for r in rs]
        extra = ""
        if pers and len(v) >= 8:
            try:
                t = stt.mean(v) / (stt.pstdev(v) / len(v) ** 0.5)
                extra = " t=%.2f" % t
            except Exception:
                pass
        print("  %-34s n=%-5d 均值 %6s%% 胜率 %4s%% 中位 %5s%%%s" % (
            tag, len(rs), avg(v), win(v), round(stt.median(v), 2), extra))

    print("=" * 90)
    print("标准工作流回测：D 收盘选股 → D+1 开盘买入 → D+2 竞价卖出（T+1 合规）")
    print("样本区间 %s ~ %s，涨停股 %d 条，其中 D+1 开盘可买 %d 条（剔除开盘即涨停 %.1f%%）" % (
        dates[0], dates[-1], len(rows), len(buy), (1 - len(buy) / len(rows)) * 100))
    print("=" * 90)

    print("\n【基线】")
    show("全部涨停股（不筛）", rows)
    show("其中开盘可买", buy, pers=False)

    print("\n【按板型】")
    for k in ("一字板", "早板", "午后板", "晚板", "换手板"):
        show(k, [r for r in buy if r["btype"] == k])

    print("\n【按换手率阶梯】")
    for g in ("最优", "健康", "次优", "中性", "超标", "高风险"):
        show(g, [r for r in buy if r["grade"] == g])

    print("\n【按连板数】")
    for lb in ("1", "2", "3"):
        show("%s板" % lb, [r for r in buy if str(r["lb"]) == lb])

    print("\n【按 D+1 竞价涨幅】")
    for g in ("低开<=-2", "低开-2~0", "平开0~2", "高开2~5", "高开>=5"):
        show(g, [r for r in buy if r["gapBand"] == g])

    print("\n【按 D 日封单额】")
    for g in ("封单>=5亿", "封单1~5亿", "封单<1亿"):
        show(g, [r for r in buy if r["sealBand"] == g])

    print("\n【组合规则】")
    show("候选池(最优+健康)", [r for r in buy if r["grade"] in GOOD])
    show("候选池 + 低开<=-2%", [r for r in buy if r["grade"] in GOOD and r["gapBand"] == "低开<=-2"])
    show("低开<=-2% + 晚板", [r for r in buy if r["gapBand"] == "低开<=-2" and r["btype"] == "晚板"])
    rule = [r for r in buy if r["gapBand"] == "低开<=-2" and r["btype"] == "晚板" and r["sealYi"] < 1.0]
    show("低开<=-2% + 晚板 + 封单<1亿", rule, pers=False)
    if rule:
        print("      分八期：" + " / ".join("%+.2f" % p if p is not None else "-" for p in periods(rule)))
        by = defaultdict(int)
        for r in rule:
            by[r["date"]] += 1
        v = sorted(by.values())
        print("      覆盖 %d 天，每日中位 %s 只、均值 %.1f" % (len(v), stt.median(v), sum(v) / len(v)))
        print("      备选卖点(D+2收盘)：均值 %s%%" % avg([r["retClose"] for r in rule]))

    print("\n【被剔除的开盘即涨停样本】")
    unb = [r for r in rows if not r["buyable"]]
    show("开盘即涨停（买不进）", unb, pers=False)
    show("其中 一字板 & 竞价高开>=9%",
         [r for r in rows if r["btype"] == "一字板" and (r.get("gap") or 0) >= 9], pers=False)
    print("\n说明：开盘即涨停的样本即使回测收益为正也不可交易，必须剔除后再评估。")


if __name__ == "__main__":
    main()
