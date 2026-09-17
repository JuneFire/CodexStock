# -*- coding: utf-8 -*-
"""tactics_engine 二板战法引擎单元测试（无网络）。"""
import unittest

from backend import tactics_engine


def make_kline(closes, highs=None, lows=None, volumes=None, turnovers=None):
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    volumes = volumes or [100000] * n
    turnovers = turnovers or [0.01] * n
    return [
        {"date": "2026-01-%02d" % (i + 1), "open": closes[i], "close": closes[i],
         "high": highs[i], "low": lows[i], "volume": volumes[i], "turnover": turnovers[i]}
        for i in range(n)
    ]


class TestLoadRules(unittest.TestCase):
    def test_defaults(self):
        r = tactics_engine.load_rules(None)
        self.assertEqual(r["turnover"]["healthyLow"], 3.0)
        self.assertIn("sectorResonance", r["weights"])

    def test_override(self):
        import json, os, tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"topN": 5, "turnover": {"burstFloor": 50}}, f)
            r = tactics_engine.load_rules(path)
            self.assertEqual(r["topN"], 5)
            self.assertEqual(r["turnover"]["burstFloor"], 50)
            self.assertEqual(r["boxWidthMax"], 0.45)  # 未覆盖保留
        finally:
            os.remove(path)


class TestAnalyzeKline(unittest.TestCase):
    def test_bullish_align_and_box(self):
        # 90 天横盘 + 温和上升，均线粘合、多头排列
        closes = [10.0 + i * 0.01 for i in range(90)]
        rows = make_kline(closes)
        f = tactics_engine.analyze_kline(rows, tactics_engine.load_rules(None))
        self.assertTrue(f["bullishAlign"])
        self.assertTrue(f["aboveMa60"])
        self.assertIsNotNone(f["boxWidth"])
        self.assertLess(f["boxWidth"], 1.0)

    def test_turnover_seq_extracted(self):
        rows = make_kline([10.0] * 10, turnovers=[0.01] * 8 + [0.06, 0.09])
        f = tactics_engine.analyze_kline(rows, tactics_engine.load_rules(None))
        self.assertEqual(f["turnoverSeq"][-2:], [6.0, 9.0])
        self.assertEqual(f["latestTurnover"], 9.0)

    def test_empty_returns_none(self):
        self.assertIsNone(tactics_engine.analyze_kline([], tactics_engine.load_rules(None)))


class TestTurnoverStage(unittest.TestCase):
    def setUp(self):
        self.rules = tactics_engine.load_rules(None)

    def test_first_board_healthy(self):
        stage, _ = tactics_engine.judge_turnover_stage([4.88], self.rules)
        self.assertEqual(stage, "首板健康")
        # 3-9% 区间内都算健康
        self.assertEqual(tactics_engine.judge_turnover_stage([8.5], self.rules)[0], "首板健康")

    def test_lift(self):
        stage, _ = tactics_engine.judge_turnover_stage([8, 9, 13, 23, 33], self.rules)
        self.assertEqual(stage, "抬升")

    def test_shrink_accelerate(self):
        stage, _ = tactics_engine.judge_turnover_stage([5, 6, 4], self.rules)
        self.assertEqual(stage, "缩量加速")

    def test_burst_cash_out(self):
        stage, _ = tactics_engine.judge_turnover_stage([5, 6, 50], self.rules)
        self.assertEqual(stage, "放量兑现")


class TestScoreCandidate(unittest.TestCase):
    def setUp(self):
        self.rules = tactics_engine.load_rules(None)
        self.feats = {
            "bullishAlign": True, "aboveMa60": True, "breakMa60": False,
            "boxWidth": 0.2, "maConverge": 0.03, "floatCapYi": 50.0, "upside": 0.15,
        }

    def test_sector_resonance_hit(self):
        sc, hits, warns = tactics_engine.score_candidate(self.feats, "抬升", self.rules, sector_zt_count=3)
        self.assertIn("板块共振3只", hits)
        self.assertGreater(sc, 80)

    def test_burst_warns(self):
        sc, hits, warns = tactics_engine.score_candidate(self.feats, "放量兑现", self.rules, sector_zt_count=0)
        self.assertTrue(any("放量兑现" in w for w in warns))

    def test_bad_float_cap_no_hit(self):
        feats = dict(self.feats, floatCapYi=500.0)  # 超出上下限
        _, hits, _ = tactics_engine.score_candidate(feats, "抬升", self.rules)
        self.assertNotIn("流通盘适中", hits)

    def test_vol_double_hit(self):
        feats = dict(self.feats, prevVolRatio=2.5)
        _, hits, _ = tactics_engine.score_candidate(feats, "抬升", self.rules)
        self.assertTrue(any("倍量" in h for h in hits))

    def test_vol_double_not_hit(self):
        feats = dict(self.feats, prevVolRatio=1.3)
        _, hits, _ = tactics_engine.score_candidate(feats, "抬升", self.rules)
        self.assertFalse(any("倍量" in h for h in hits))


class TestResonance(unittest.TestCase):
    def setUp(self):
        self.rules = tactics_engine.load_rules(None)

    def test_full_resonance(self):
        hits = ["多头排列", "站上60线", "倍量2.5x"]
        r = tactics_engine.judge_resonance(hits, "抬升", 3, self.rules)
        self.assertTrue(r["stock"])
        self.assertTrue(r["sector"])
        self.assertTrue(r["news"])
        self.assertTrue(r["resonant"])

    def test_sector_weak_not_resonant(self):
        hits = ["多头排列", "站上60线", "倍量2.5x"]
        r = tactics_engine.judge_resonance(hits, "抬升", 1, self.rules)
        self.assertFalse(r["sector"])
        self.assertFalse(r["resonant"])

    def test_message_needs_3(self):
        hits = ["多头排列", "站上60线"]
        r = tactics_engine.judge_resonance(hits, "抬升", 2, self.rules)
        self.assertTrue(r["sector"])   # ≥2
        self.assertFalse(r["news"])   # <3
        self.assertFalse(r["resonant"])

    def test_tech_insufficient(self):
        hits = ["多头排列"]  # 只有1项技术面
        r = tactics_engine.judge_resonance(hits, "抬升", 5, self.rules)
        self.assertFalse(r["stock"])
        self.assertFalse(r["resonant"])

    def test_burst_stage_not_healthy(self):
        hits = ["多头排列", "站上60线", "倍量2.5x"]
        r = tactics_engine.judge_resonance(hits, "放量兑现", 5, self.rules)
        self.assertFalse(r["stock"])  # 放量兑现非健康阶段
        self.assertFalse(r["resonant"])


class TestSectorForm(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(tactics_engine.judge_sector(None))
        self.assertIsNone(tactics_engine.judge_sector({"closes": [1, 2, 3]}))

    def test_good_low_reclaim(self):
        # 先前高位12 → 下跌到10 → 低位回升站上60线，5/10/20多头
        closes = [12.0] * 20 + [12.0 - i * 0.025 for i in range(80)] + [10.025 + i * 0.03 for i in range(21)]
        r = tactics_engine.judge_sector({"name": "测试板块", "closes": closes})
        self.assertIsNotNone(r)
        self.assertTrue(r["good"], r)
        self.assertIn("站上60线", r["hits"])
        self.assertIn("5-10-20多头", r["hits"])

    def test_far_above_ma60_not_good(self):
        # 持续大涨远离60线 → 不算"刚站上"
        closes = [10.0] * 60 + [10.0 + i * 0.3 for i in range(30)]
        r = tactics_engine.judge_sector({"name": "高位板块", "closes": closes})
        self.assertIsNotNone(r)
        self.assertFalse(r["good"])


if __name__ == "__main__":
    unittest.main()
