# -*- coding: utf-8 -*-
"""卡位晋级 + 量窒息 单元测试（无网络）。"""
import unittest

from backend import tactics_engine as te


def zt(code, name, lb, industry="AI"):
    return {"code": code, "name": name, "lb": lb, "industry": industry}


class TestLeadership(unittest.TestCase):
    def test_plain_promotion_no_break(self):
        prev = [zt("600001", "A", 2), zt("600002", "B", 1)]
        today = [zt("600001", "A", 3), zt("600002", "B", 2)]
        res = te.judge_leadership(prev, today)
        self.assertEqual({p["type"] for p in res["promotions"]}, {"晋级"})
        self.assertEqual(res["lostSlots"], [])

    def test_kakwei_and_displaced(self):
        # 昨高位3板A断板 → 2板B、1板C 卡位晋级
        prev = [zt("600001", "高位A", 3), zt("600002", "中位B", 2), zt("600003", "低位C", 1)]
        today = [zt("600002", "中位B", 3), zt("600003", "低位C", 2)]
        res = te.judge_leadership(prev, today)
        types = {p["code"]: p["type"] for p in res["promotions"]}
        self.assertEqual(types["600002"], "卡位晋级")
        self.assertEqual(types["600003"], "卡位晋级")
        self.assertEqual([l["code"] for l in res["lostSlots"]], ["600001"])
        self.assertIn("拉升只卖", res["lostSlots"][0]["note"])

    def test_broken_first_board_not_displaced(self):
        # 断板的只是首板(1板) → 不算被卡位
        prev = [zt("600001", "首板", 1), zt("600002", "低位C", 1)]
        today = [zt("600002", "低位C", 2)]
        res = te.judge_leadership(prev, today)
        self.assertEqual(res["lostSlots"], [])

    def test_new_stock_not_promotion(self):
        # 今日新涨停(昨日无) 不算晋级
        prev = [zt("600001", "A", 1)]
        today = [zt("600009", "新", 1)]
        res = te.judge_leadership(prev, today)
        self.assertEqual(res["promotions"], [])

    def test_empty(self):
        res = te.judge_leadership([], [])
        self.assertEqual(res["promotions"], [])
        self.assertEqual(res["lostSlots"], [])


def make_kline(closes, vols, highs=None, lows=None, opens=None):
    n = len(closes)
    highs = highs or [c * 1.005 for c in closes]
    lows = lows or [c * 0.995 for c in closes]
    opens = opens or closes
    return [{"date": "2026-01-%02d" % (i + 1), "open": opens[i], "close": closes[i],
             "high": highs[i], "low": lows[i], "volume": vols[i], "turnover": 0.01}
            for i in range(n)]


class TestCongestion(unittest.TestCase):
    def test_congestion_hit(self):
        # 20 天横盘窄幅 + 近5日缩量；今日收红
        closes = [10.0 + (i % 2) * 0.02 for i in range(21)]
        vols = [100000] * 16 + [50000] * 5  # 近5日缩量
        rows = make_kline(closes, vols, opens=[c - 0.01 for c in closes])
        res = te.judge_congestion(rows)
        self.assertIsNotNone(res)
        self.assertIn("量能萎缩", " ".join(res["hits"]))

    def test_not_congestion_when_volume_expands(self):
        closes = [10.0 + (i % 2) * 0.02 for i in range(21)]
        vols = [100000] * 16 + [200000] * 5  # 放量
        rows = make_kline(closes, vols)
        self.assertIsNone(te.judge_congestion(rows))

    def test_not_congestion_when_wide_amplitude(self):
        closes = [10.0] * 21
        vols = [100000] * 16 + [50000] * 5
        highs = [12.0] * 21  # 近5日大幅波动
        lows = [8.0] * 21
        rows = make_kline(closes, vols, highs=highs, lows=lows)
        self.assertIsNone(te.judge_congestion(rows))

    def test_too_short_returns_none(self):
        rows = make_kline([10.0] * 10, [100000] * 10)
        self.assertIsNone(te.judge_congestion(rows))


if __name__ == "__main__":
    unittest.main()
