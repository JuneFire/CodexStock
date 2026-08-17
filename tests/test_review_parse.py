# -*- coding: utf-8 -*-
"""server.py 情绪复盘纯函数单元测试（无网络）。"""
import unittest

import pandas as pd

from backend import server


def _zt(code, name, zt_count, industry="半导体", seal=1e8, lianban=None):
    return {
        "code": code, "name": name, "industry": industry, "sealAmount": seal,
        "ztCount": zt_count, "lianban": lianban, "changePct": 10.0, "price": 10.0,
        "amount": 1e9, "sealTime": "09:25", "reason": "",
    }


class TestZtCountParse(unittest.TestCase):
    def test_parses_streaks(self):
        self.assertEqual(server._zt_count_from_stats("1/1"), 1)
        self.assertEqual(server._zt_count_from_stats("2/2"), 2)
        self.assertEqual(server._zt_count_from_stats("5/5"), 5)

    def test_ignores_invalid(self):
        self.assertIsNone(server._zt_count_from_stats("0/0"))
        self.assertIsNone(server._zt_count_from_stats("23/12"))
        self.assertIsNone(server._zt_count_from_stats("10/6"))
        self.assertIsNone(server._zt_count_from_stats("abc"))
        self.assertIsNone(server._zt_count_from_stats(""))


class TestParseLianban(unittest.TestCase):
    def test_bucketing(self):
        zt = [
            _zt("600001", "首板A", "1/1", seal=3e8),
            _zt("600002", "二板A", "2/2", seal=2e8),
            _zt("600003", "三板A", "3/3", seal=5e8),
            _zt("600004", "断板", "0/0"),
            _zt("600005", "累计", "23/12"),
        ]
        res = server.parse_lianban(zt, [])
        self.assertEqual([s["name"] for s in res["tier"]["first"]], ["首板A"])
        self.assertEqual([s["name"] for s in res["tier"][2]], ["二板A"])
        self.assertEqual([s["name"] for s in res["tier"][3]], ["三板A"])
        self.assertEqual(res["maxTier"], 3)
        self.assertEqual(res["tier"][4], [])
        self.assertEqual(res["tier"][8], [])

    def test_sorted_by_seal(self):
        zt = [
            _zt("600001", "小封", "2/2", seal=1e8),
            _zt("600002", "大封", "2/2", seal=9e8),
        ]
        res = server.parse_lianban(zt, [])
        self.assertEqual([s["name"] for s in res["tier"][2]], ["大封", "小封"])

    def test_strong_pool_also_bucketed(self):
        strong = [_zt("600010", "强A", "4/4", seal=4e8)]
        res = server.parse_lianban([], strong)
        self.assertEqual([s["name"] for s in res["tier"][4]], ["强A"])
        self.assertEqual(res["maxTier"], 4)

    def test_dedup_same_stock_across_pools(self):
        # 同一股票在 strong 池和 zt 池都出现，应只保留一条（取封单额高者）
        strong = [_zt("600721", "百花医药", "7/7", seal=3e8)]
        zt = [_zt("600721", "百花医药", "7/7", seal=4e8)]
        res = server.parse_lianban(zt, strong)
        self.assertEqual(len(res["tier"][7]), 1)
        self.assertEqual(res["tier"][7][0]["sealAmount"], 4e8)  # 保留封单额高者


class TestFetchZtMeta(unittest.TestCase):
    def test_max_seal_and_front_sectors(self):
        zt = [
            _zt("600001", "小A", "1/1", industry="半导体", seal=1e8),
            _zt("600002", "大A", "2/2", industry="半导体", seal=9e8),
            _zt("600003", "中A", "1/1", industry="医药", seal=5e8),
            _zt("600004", "亿A", "1/1", industry="半导体", seal=3e8),
        ]
        meta = server.fetch_zt_meta(zt)
        self.assertEqual(meta["maxSeal"]["name"], "大A")
        self.assertEqual(meta["maxSeal"]["sealAmount"], 9.0)
        front = {f["name"]: f for f in meta["frontSectors"]}
        self.assertEqual(front["半导体"]["count"], 3)
        self.assertEqual(front["医药"]["count"], 1)
        self.assertEqual([s["name"] for s in meta["sealYi"]], ["大A", "中A", "亿A", "小A"])


class TestPrevPremium(unittest.TestCase):
    def test_premium(self):
        prev = [
            {"changePct": 3.0, "昨日连板数": 1},
            {"changePct": 1.0, "昨日连板数": 3},
            {"changePct": -2.0, "昨日连板数": 2},
            {"changePct": None, "昨日连板数": 1},
        ]
        res = server.compute_prev_premium(prev)
        self.assertAlmostEqual(res["ztPremium"], (3.0 + 1.0 - 2.0) / 3, places=2)
        self.assertAlmostEqual(res["lbPremium"], (1.0 - 2.0) / 2, places=2)

    def test_empty(self):
        res = server.compute_prev_premium([])
        self.assertIsNone(res["ztPremium"])
        self.assertIsNone(res["lbPremium"])


class TestSpotBreadth(unittest.TestCase):
    def test_breadth(self):
        df = pd.DataFrame({"code": ["sh600001", "sh600002", "sh600003", "sh600004", "bj920001", "sz000001"],
                           "zdf": ["1.5", "-0.5", "0", "2.0", "-3.0", "1.0"]})
        original = server.ak
        server.ak = type("Ak", (), {"stock_zh_a_spot_tx": staticmethod(lambda: df)})()
        try:
            res = server.fetch_spot_breadth("2026-08-10")
        finally:
            server.ak = original
        self.assertEqual(res, {"up": 3, "down": 1, "flat": 1})  # bj 排除


class TestIndicesKline(unittest.TestCase):
    def test_kline(self):
        df = pd.DataFrame({
            "date": ["2026-08-06", "2026-08-07", "2026-08-10"],
            "open": [3900.0, 3896.0, 3960.0],
            "close": [3900.35, 3940.04, 3966.59],
            "amount": [1.16e12, 1.20e12, 1.16e12],
        })
        original = server.ak
        def _fake_hist(symbol, start_date, end_date, adjust):
            if symbol == "sh000001":
                return df
            return pd.DataFrame(columns=["date", "open", "close", "amount"])
        server.ak = type("Ak", (), {"stock_zh_a_hist_tx": staticmethod(_fake_hist)})()
        try:
            res = server.fetch_indices_kline("2026-08-10")
        finally:
            server.ak = original
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "上证指数")
        self.assertAlmostEqual(res[0]["close"], 3966.59, places=2)
        self.assertAlmostEqual(res[0]["changePts"], 26.55, places=2)
        self.assertAlmostEqual(res[0]["changePct"], 0.67, places=2)
        self.assertEqual(res[0]["open"], 3960.0)
        self.assertAlmostEqual(res[0]["amountYi"], 11600.0, places=1)


class TestMa(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(server._ma([1, 2, 3, 4, 5], 3), 4.0)

    def test_short_series(self):
        self.assertEqual(server._ma([1, 2], 60), 1.5)

    def test_empty(self):
        self.assertIsNone(server._ma([], 60))


class TestObvStrong(unittest.TestCase):
    def test_obv_above_ma_strong(self):
        # 价格长期上涨 → OBV 持续走高 → 站上自身 MA20
        closes = [100 + i * 0.5 for i in range(60)]
        volumes = [1000] * 60
        self.assertTrue(server._obv_strong(closes, volumes))

    def test_obv_below_ma_not_strong(self):
        # 价格长期下跌 → OBV 持续走低 → 低于自身 MA20
        closes = [100 - i * 0.5 for i in range(60)]
        volumes = [1000] * 60
        self.assertFalse(server._obv_strong(closes, volumes))

    def test_recent_cross_above_strong(self):
        # 长期跌后近期反弹 → OBV 刚上穿均线（弱转强）→ 强势
        closes = [100 - i * 0.3 for i in range(50)] + [85, 86, 88, 91, 95, 100, 106, 113]
        volumes = [1000] * 50 + [1000, 1500, 2000, 2500, 3000, 3500, 4000, 5000]
        self.assertTrue(server._obv_strong(closes, volumes))

    def test_too_short(self):
        self.assertFalse(server._obv_strong([100], [1000]))


class TestClassifyHorses(unittest.TestCase):
    def _idx(self, name, closes, volumes):
        return {"name": name, "dates": [], "closes": closes, "volumes": volumes}

    def test_head_and_dark(self):
        head_closes = [100 + i * 0.2 for i in range(70)]  # 站上MA60 且 OBV 强
        head_vols = [1000 + i * 10 for i in range(70)]
        # 黑马：未站上MA60（整体缓跌），但近期放量反弹 OBV 强
        dark_closes = [100 - i * 0.2 for i in range(65)] + [87.5, 87.8, 88.2, 88.5, 88.8]
        dark_vols = [800 + i * 5 for i in range(65)] + [1000, 2000, 3000, 4000, 5000]
        flat_closes = [100] * 70  # 平盘，OBV 不强
        flat_vols = [1000] * 70
        indices = {
            "headA": self._idx("头马", head_closes, head_vols),
            "darkB": self._idx("黑马", dark_closes, dark_vols),
            "flatC": self._idx("平淡", flat_closes, flat_vols),
        }
        head, dark = server.classify_horses(indices)
        self.assertEqual([h["name"] for h in head], ["头马"])
        self.assertEqual([h["name"] for h in dark], ["黑马"])
        self.assertEqual(head[0]["changePct"], 0.18)

    def test_empty(self):
        head, dark = server.classify_horses({})
        self.assertEqual(head, [])
        self.assertEqual(dark, [])

    def test_caps_at_eight(self):
        indices = {}
        for i in range(12):
            indices["s%02d" % i] = self._idx("行业%d" % i, [100 + i * 0.1 + j * 0.2 for j in range(70)], [1000] * 70)
        head, dark = server.classify_horses(indices)
        self.assertLessEqual(len(head), 8)


if __name__ == "__main__":
    unittest.main()


class TestComputeThreePick(unittest.TestCase):
    def test_hits_two_tops_wins(self):
        stocks = [
            {"code": "000001", "name": "妖股A", "auctionAmount": 900, "auctionTurnover": 3.0, "changePct": 5.0},
            {"code": "000002", "name": "强股B", "auctionAmount": 850, "auctionTurnover": 2.0, "changePct": 9.0},
            {"code": "000003", "name": "普通C", "auctionAmount": 700, "auctionTurnover": 1.0, "changePct": 4.0},
        ]
        res = server.compute_three_pick(stocks)
        self.assertEqual([r["name"] for r in res], ["妖股A"])
        self.assertEqual(res[0]["hits"], 2)

    def test_no_winner_when_each_top_distinct(self):
        stocks = [
            {"code": "000001", "name": "A", "auctionAmount": 900, "auctionTurnover": 1.0, "changePct": 5.0},
            {"code": "000002", "name": "B", "auctionAmount": 800, "auctionTurnover": 2.5, "changePct": 4.0},
            {"code": "000003", "name": "C", "auctionAmount": 700, "auctionTurnover": 2.0, "changePct": 9.0},
        ]
        res = server.compute_three_pick(stocks)
        self.assertEqual(res, [])

    def test_empty(self):
        self.assertEqual(server.compute_three_pick([]), [])


if __name__ == "__main__":
    unittest.main()


class TestBuildPlanText(unittest.TestCase):
    def _review(self):
        return {
            "indices": [{"name": "上证指数", "close": 3946.68, "changePct": 0.32, "open": 3933.55}],
            "breadth": {"up": 3860, "down": 1217},
            "pools": {
                "ztCount": 92, "dtCount": 0, "zbCount": 13,
                "ztMeta": {"frontSectors": [{"name": "专用设备", "count": 8}, {"name": "通用设备", "count": 8}]},
            },
            "lianban": {"maxTier": 7, "tier": {
                "7": [{"code": "600721", "name": "百花医药", "industry": "医疗服务", "sealAmount": 8.35e7}],
                "4": [{"code": "603758", "name": "秦安股份", "industry": "汽车零部", "sealAmount": 2.71e8}],
                "first": [{"code": "600105", "name": "永鼎股份", "industry": "通用设备", "sealAmount": 1e8}],
            }},
            "horses": {"headHorses": [{"name": "房地产", "changePct": 4.67}]},
        }

    def test_structure(self):
        original = server._fetch_plan_news
        server._fetch_plan_news = lambda: ["测试消息"]
        try:
            text = server._build_plan_text("2026-08-13", self._review())
        finally:
            server._fetch_plan_news = original
        for kw in ["2026年8月13日早盘预案", "大局观", "具体机会解析", "总结", "投资有风险", "百花医药", "7板", "封单8350万", "秦安股份", "封单2.71亿", "测试消息"]:
            self.assertIn(kw, text, "缺少: " + kw)

    def test_no_review_fallback(self):
        text = server._build_plan_text("2026-08-13", None)
        self.assertIn("复盘数据缺失", text)


if __name__ == "__main__":
    unittest.main()
