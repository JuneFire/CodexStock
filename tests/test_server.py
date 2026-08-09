# -*- coding: utf-8 -*-
"""server.py 纯函数单元测试（无网络）。"""
import unittest

import pandas as pd

from backend import server


class TestBareCode(unittest.TestCase):
    def test_prefixed(self):
        self.assertEqual(server._bare_code("sh600519"), "600519")
        self.assertEqual(server._bare_code("SZ000001"), "000001")

    def test_already_six(self):
        self.assertEqual(server._bare_code("600519"), "600519")

    def test_pad(self):
        self.assertEqual(server._bare_code("1"), "000001")


class TestTxSymbol(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(server._tx_symbol("600000"), "sh600000")
        self.assertEqual(server._tx_symbol("000001"), "sz000001")
        self.assertEqual(server._tx_symbol("300750"), "sz300750")
        self.assertEqual(server._tx_symbol("830000"), "bj830000")
        self.assertEqual(server._tx_symbol("900901"), "sh900901")


class TestIsBj(unittest.TestCase):
    def test_bj_codes(self):
        self.assertTrue(server._is_bj("430047"))
        self.assertTrue(server._is_bj("830000"))
        self.assertTrue(server._is_bj("920001"))

    def test_shsz_codes(self):
        self.assertFalse(server._is_bj("600000"))
        self.assertFalse(server._is_bj("000001"))
        self.assertFalse(server._is_bj("300750"))


class TestPrevTradingRow(unittest.TestCase):
    def _df(self, dates):
        return pd.DataFrame({"date": dates, "close": range(len(dates))})

    def test_prior_row(self):
        df = self._df(["2026-08-05", "2026-08-06", "2026-08-07"])
        row = server._prev_trading_row(df, "2026-08-07")
        self.assertIsNotNone(row)
        self.assertEqual(row["date"], "2026-08-06")

    def test_excludes_snapshot_date(self):
        df = self._df(["2026-08-06", "2026-08-07"])
        row = server._prev_trading_row(df, "2026-08-07")
        self.assertEqual(row["date"], "2026-08-06")

    def test_empty(self):
        df = self._df(["2026-08-07"])
        self.assertIsNone(server._prev_trading_row(df, "2026-08-07"))

    def test_all_after(self):
        df = self._df(["2026-08-08", "2026-08-09"])
        self.assertIsNone(server._prev_trading_row(df, "2026-08-07"))


class TestInAuctionWindow(unittest.TestCase):
    def _dt(self, day, hm):
        from datetime import datetime
        return datetime(2026, 8, day, hm // 100, hm % 100)

    def test_weekday_in_window(self):
        # 2026-08-07 是周五
        self.assertTrue(server._in_auction_window(self._dt(7, 925)))
        self.assertTrue(server._in_auction_window(self._dt(7, 929)))

    def test_weekday_outside_window(self):
        self.assertFalse(server._in_auction_window(self._dt(7, 924)))
        self.assertFalse(server._in_auction_window(self._dt(7, 930)))
        self.assertFalse(server._in_auction_window(self._dt(7, 1500)))

    def test_weekend(self):
        # 2026-08-08 是周六
        self.assertFalse(server._in_auction_window(self._dt(8, 925)))
        self.assertFalse(server._in_auction_window(self._dt(8, 1000)))


if __name__ == "__main__":
    unittest.main()
