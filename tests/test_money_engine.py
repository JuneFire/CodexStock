# -*- coding: utf-8 -*-
"""money_engine 赚钱效应双线单元测试（无网络）。"""
import unittest

from backend import money_engine


class TestNorm(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(money_engine.norm(-5, -5, 5), 0.0)
        self.assertEqual(money_engine.norm(5, -5, 5), 100.0)
        self.assertEqual(money_engine.norm(0, -5, 5), 50.0)

    def test_clamp(self):
        self.assertEqual(money_engine.norm(-100, -5, 5), 0.0)
        self.assertEqual(money_engine.norm(100, -5, 5), 100.0)

    def test_none(self):
        self.assertIsNone(money_engine.norm(None, 0, 1))


class TestMoneyShort(unittest.TestCase):
    def test_all_present(self):
        day = {"avgPremium": 5.0, "promoteRate": 0.5, "first2Rate": 0.5}
        score, parts, partial = money_engine.money_short(day)
        self.assertEqual(score, 100.0)   # 三项全满
        self.assertFalse(partial)

    def test_low(self):
        day = {"avgPremium": -5.0, "promoteRate": 0.0, "first2Rate": 0.0}
        score, parts, partial = money_engine.money_short(day)
        self.assertEqual(score, 0.0)

    def test_missing_marked_partial(self):
        day = {"avgPremium": 0.0, "promoteRate": None, "first2Rate": 0.5}
        score, parts, partial = money_engine.money_short(day)
        self.assertTrue(partial)
        self.assertIsNotNone(score)  # 缺项按剩余权重重归一

    def test_all_missing(self):
        score, parts, partial = money_engine.money_short({})
        self.assertIsNone(score)


class TestMoneyMarket(unittest.TestCase):
    def test_all_present(self):
        day = {"up": 4000, "down": 1000, "zt": 50, "dt": 0, "amountChgPct": 20.0}
        score, parts, partial = money_engine.money_market(day)
        self.assertEqual(score, 100.0)
        self.assertFalse(partial)

    def test_breadth_ratio(self):
        # up/(up+down) = 0.5 → breadth 成分 50
        day = {"up": 500, "down": 500, "zt": None, "dt": None, "amountChgPct": None}
        score, parts, partial = money_engine.money_market(day)
        self.assertEqual(parts["breadth"], 50.0)
        self.assertTrue(partial)

    def test_missing_breadth_renormalized(self):
        # 缺家数时用涨跌停比+量能重归一
        day = {"up": None, "down": None, "zt": 50, "dt": 0, "amountChgPct": 20.0}
        score, parts, partial = money_engine.money_market(day)
        self.assertTrue(partial)
        self.assertEqual(score, 100.0)  # 剩余两项都满

    def test_zero_zt_dt_no_crash(self):
        day = {"up": 100, "down": 100, "zt": 0, "dt": 0, "amountChgPct": 0}
        score, parts, partial = money_engine.money_market(day)
        self.assertIsNone(parts["limitRatio"])  # 分母 0 → None


class TestCompute(unittest.TestCase):
    def test_structure(self):
        day = {"date": "2026-09-16", "avgPremium": 3.65, "promoteRate": 0.429,
               "first2Rate": 0.36, "up": 3888, "down": 1134, "zt": 89, "dt": 4,
               "amountChgPct": 14.04}
        res = money_engine.compute(day)
        self.assertEqual(res["date"], "2026-09-16")
        self.assertIsNotNone(res["short"])
        self.assertIsNotNone(res["market"])
        self.assertGreater(res["short"], 50)
        self.assertGreater(res["market"], 50)
        self.assertFalse(res["partial"])


if __name__ == "__main__":
    unittest.main()
