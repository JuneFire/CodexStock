# -*- coding: utf-8 -*-
"""env_engine 赚钱环境打分引擎单元测试（无网络）。"""
import unittest

from backend import env_engine


def make_signals(**kw):
    base = {k: None for k in [
        'zt', 'ztReal', 'dt', 'zb', 'zbRate', 'lb2', 'lb3', 'lb3p', 'maxTier', 'ztChangePct',
        'red', 'green', 'redRatio', 'amountYi', 'amountChgPct',
        'promoteRate', 'first2Rate', 'lbPromoteRate', 'avgPremium', 'lbAvgPremium',
        'jaccard', 'prevLeaderTodayZt', 'sectorCount', 'sectorsGe3', 'sectorsGe4', 'top1ZtShare',
        'top10Share', 'top20Share', 'idxAbove20', 'idxAbove60', 'ma20Up', 'headN',
    ]}
    base.update(kw)
    return {"S1": base, "S2": base, "S3": base, "S4": base, "S5": base, "S6": base}


class TestLoadRules(unittest.TestCase):
    def test_default_when_no_file(self):
        rules = env_engine.load_rules(None)
        self.assertEqual(rules["activateFloor"], 45)
        self.assertIn("short", rules)

    def test_user_override_merges(self):
        import json
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"activateFloor": 60, "short": {"ztRising": 99}}, f)
            rules = env_engine.load_rules(path)
            self.assertEqual(rules["activateFloor"], 60)      # 覆盖
            self.assertEqual(rules["short"]["ztRising"], 99)  # 深层覆盖
            self.assertEqual(rules["mixGap"], 10)             # 未覆盖项保留默认
        finally:
            os.remove(path)


class TestComputeScoring(unittest.TestCase):
    def test_short_environment(self):
        """连板晋级率高+高度梯队+涨停规模 → 短线环境。"""
        res = env_engine.compute(make_signals(
            zt=55, zb=10, zbRate=15, lb2=8, lb3=2, lb3p=2, maxTier=5, promoteRate=0.45,
            avgPremium=2.5, red=3000, green=1500, redRatio=0.67))
        self.assertGreater(res["scores"]["short"], 60)
        self.assertEqual(res["conclusion"]["top"], "short")
        self.assertIn("短线", res["conclusion"]["label"])

    def test_trend_environment(self):
        """指数多头+红盘广度+主线持续 → 趋势环境。"""
        res = env_engine.compute(make_signals(
            zt=50, zb=10, zbRate=15, red=3000, green=1500, redRatio=0.67, amountYi=16000,
            idxAbove20=2, idxAbove60=2, ma20Up=True, headN=4, maxTier=3, lb3p=1,
            promoteRate=0.2, jaccard=0.4, sectorCount=10, sectorsGe3=4))
        self.assertEqual(res["conclusion"]["top"], "trend")
        self.assertIn("趋势", res["conclusion"]["label"])

    def test_chaos_environment(self):
        """板块一日游+炸板高+追涨亏钱 → 混乱轮动。"""
        res = env_engine.compute(make_signals(
            zt=35, zb=25, zbRate=42, red=1500, green=3200, redRatio=0.32, amountYi=9000,
            maxTier=3, lb3p=0, promoteRate=0.05, avgPremium=-3, jaccard=0.1, prevLeaderTodayZt=0))
        self.assertEqual(res["conclusion"]["top"], "chaos")
        self.assertEqual(res["conclusion"]["tone"], "neg")

    def test_defensive_when_all_weak_ambiguous(self):
        """全空 → 无环境达标 → 防守。"""
        res = env_engine.compute({"S1": {}, "S2": {}, "S3": {}, "S4": {}, "S5": {}, "S6": {}})
        self.assertEqual(res["conclusion"]["state"], "防守")
        self.assertEqual(res["conclusion"]["tone"], "ice")

    def test_mix_conclusion(self):
        """两类接近且都达标 → 主类+伴生类。"""
        # 构造短线略高、抱团紧随
        res = env_engine.compute(make_signals(
            zt=55, zb=10, zbRate=18, lb2=8, lb3=2, lb3p=2, maxTier=5, promoteRate=0.42,
            avgPremium=2.0, top10Share=14, top20Share=24, red=1500, green=3200, redRatio=0.32))
        c = res["conclusion"]
        self.assertGreaterEqual(c["topScore"], 45)
        if c["secondScore"] >= 35 and (c["topScore"] - c["secondScore"]) <= 10:
            self.assertIn("伴生", c["label"])

    def test_scores_within_range(self):
        res = env_engine.compute(make_signals(
            zt=200, zb=0, zbRate=0, red=5000, green=100, redRatio=0.98, amountYi=30000,
            idxAbove20=2, idxAbove60=2, ma20Up=True, headN=5, promoteRate=1.0, avgPremium=10,
            jaccard=1.0, top10Share=50, top20Share=80, lb2=50, lb3=20, lb3p=20, maxTier=10))
        for k, v in res["scores"].items():
            self.assertGreaterEqual(v, 0, k)
            self.assertLessEqual(v, 100, k)


if __name__ == "__main__":
    unittest.main()
