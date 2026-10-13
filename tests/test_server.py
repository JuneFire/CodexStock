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


class TestIsTradingDay(unittest.TestCase):
    def setUp(self):
        server._trading_day_cache.clear()
        server._trading_calendar_cache.clear()

    def _dt(self, day, hm):
        from datetime import datetime
        return datetime(2026, 10, day, hm // 100, hm % 100)

    def test_trading_day(self):
        from unittest import mock
        with mock.patch.object(server, "_fetch_month_trading_days",
                               return_value={"2026-10-08", "2026-10-09", "2026-10-12"}) as fetch:
            self.assertTrue(server.is_trading_day(self._dt(9, 930)))
            self.assertTrue(server.is_trading_day(self._dt(8, 800)))  # 盘前也是交易日
        self.assertEqual(fetch.call_count, 1)  # 同月只查一次日历

    def test_holiday(self):
        # 2026-10-05 周一（国庆休市），日历里没有 → False
        from unittest import mock
        with mock.patch.object(server, "_fetch_month_trading_days",
                               return_value={"2026-10-08", "2026-10-09"}):
            self.assertFalse(server.is_trading_day(self._dt(5, 930)))

    def test_calendar_unavailable_falls_back_to_true(self):
        # 接口不可用 → 按工作日兜底 True，避免漏抓交易日数据
        from unittest import mock
        with mock.patch.object(server, "_fetch_month_trading_days", return_value=None):
            self.assertTrue(server.is_trading_day(self._dt(9, 930)))

    def test_weekend_short_circuits(self):
        from unittest import mock
        with mock.patch.object(server, "_fetch_month_trading_days") as fetch:
            self.assertFalse(server.is_trading_day(self._dt(10, 930)))  # 周六
        self.assertFalse(fetch.called)


class TestParsePaging(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(server._parse_paging({}), (1, 30))

    def test_values(self):
        self.assertEqual(server._parse_paging({"page": ["2"], "page_size": ["10"]}), (2, 10))

    def test_invalid(self):
        self.assertEqual(server._parse_paging({"page": ["abc"], "page_size": ["0"]}), (1, 1))

    def test_clamp_negative(self):
        self.assertEqual(server._parse_paging({"page": ["-5"], "page_size": ["100"]}), (1, 100))


class TestBuildHorsesForDate(unittest.TestCase):
    def test_reads_horses_from_db(self):
        row = {"horses": {"headHorses": [{"code": "new_swzz", "name": "生物制药", "changePct": 2.06}],
                          "darkHorses": [{"code": "new_dqhy", "name": "电器行业", "changePct": 2.96}]},
               "fetchedAt": "2026-08-11 15:05:00"}
        import unittest.mock as mock
        with mock.patch.object(server.db, "read_review", return_value=row):
            result = server.build_horses_for_date("2026-08-11")
        self.assertTrue(result["ok"])
        self.assertEqual(result["date"], "2026-08-11")
        self.assertEqual(result["headHorses"][0]["name"], "生物制药")
        self.assertFalse(result.get("stale"))

    def test_no_db_horses_falls_back_to_current(self):
        import unittest.mock as mock
        with mock.patch.object(server.db, "read_review", return_value=None), \
             mock.patch.object(server, "build_horses", return_value={"ok": True, "date": "", "builtAt": "", "headHorses": [], "darkHorses": []}):
            result = server.build_horses_for_date("2026-08-10")
        self.assertEqual(result["date"], "2026-08-10")
        self.assertTrue(result["stale"])


class TestFetchAuctionTop(unittest.TestCase):
    def test_adds_rank_and_metadata(self):
        from unittest import mock
        from datetime import datetime
        stocks = [{"code": "600000", "name": "A", "auctionAmount": 500.0},
                  {"code": "000001", "name": "B", "auctionAmount": 300.0}]
        with mock.patch.object(server, "fetch_auction_snapshot", return_value=(stocks, "腾讯")):
            result = server.fetch_auction_top(n=2)
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["records"][0]["rank"], 1)
        self.assertEqual(result["records"][1]["rank"], 2)
        self.assertEqual(result["records"][0]["tradeDate"], datetime.now().strftime("%Y-%m-%d"))
        self.assertIn("fetchedAt", result["records"][0])

    def test_fetch_snapshot_passes_top_n(self):
        from unittest import mock
        calls = []

        def fake_tencent(top_n):
            calls.append(top_n)
            return [{"code": "600000", "name": "A", "auctionAmount": 500.0}]

        with mock.patch.object(server, "fetch_auction_snapshot_tencent", side_effect=fake_tencent):
            stocks, label = server.fetch_auction_snapshot(top_n=60)
        self.assertEqual(calls[0], 60)
        self.assertEqual(len(stocks), 1)
        self.assertEqual(label, "腾讯")


if __name__ == "__main__":
    unittest.main()
