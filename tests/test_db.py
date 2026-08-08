# -*- coding: utf-8 -*-
import unittest
from datetime import date, datetime
from unittest import mock

from backend import db


class FakeCursor:
    def __init__(self, rows=None):
        self.executed = []
        self.rows = list(rows or [])

    def execute(self, sql, args=None):
        self.executed.append((sql, args))

    def executemany(self, sql, args):
        self.executed.append((sql, args))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        out = self.rows
        self.rows = []
        return out

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self, rows=None):
        self.cur = FakeCursor(rows)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def valid_snapshot():
    return {
        "ok": True,
        "date": "2026-08-10",
        "fetchedAt": "2026-08-10 09:26:01",
        "source": "东方财富",
        "auto": True,
        "validForAuction": True,
        "market": {"indices": [], "up": 1, "down": 0, "flat": 0, "limitUp": 0, "totalAmount": 1},
        "stocks": [
            {
                "code": "600000", "name": "测试股份", "industry": "银行",
                "price": 10.0, "changePct": 1.0, "open": None, "prevClose": 9.9,
                "auctionAmount": 1000.0, "auctionVolume": 100.0, "turnover": 1.0,
                "volumeRatio": 1.1, "floatCap": 1e9, "totalCap": 1e9,
                "yesterdayAmount": 500.0, "yesterdayTurnover": 0.5,
                "yesterdayClose": 9.9, "ratioToYesterday": 200.0,
                "amountStrength": 10.0, "auctionTurnover": 1.0, "score": 80.0,
                "rank": 1, "tags": ["超预期", "高开"],
            }
        ],
    }


class SaveSnapshotTest(unittest.TestCase):
    def setUp(self):
        db._available = True
        db._last_error = ""

    def test_invalid_snapshot_not_saved(self):
        snap = valid_snapshot()
        snap["validForAuction"] = False
        with mock.patch.object(db, "_connect") as connect:
            self.assertFalse(db.save_snapshot(snap))
            connect.assert_not_called()

    def test_empty_stocks_not_saved(self):
        snap = valid_snapshot()
        snap["stocks"] = []
        with mock.patch.object(db, "_connect") as connect:
            self.assertFalse(db.save_snapshot(snap))
            connect.assert_not_called()

    def test_outside_window_not_saved(self):
        snap = valid_snapshot()
        snap["fetchedAt"] = "2026-08-10 12:17:00"
        with mock.patch.object(db, "_connect") as connect:
            self.assertFalse(db.save_snapshot(snap))
            connect.assert_not_called()
        self.assertIn("竞价窗口", db.last_error())

    def test_fetched_at_date_mismatch_not_saved(self):
        snap = valid_snapshot()
        snap["fetchedAt"] = "2026-08-09 09:26:00"
        with mock.patch.object(db, "_connect") as connect:
            self.assertFalse(db.save_snapshot(snap))
            connect.assert_not_called()
        self.assertIn("不一致", db.last_error())

    def test_save_uses_upsert_and_replace(self):
        fake = FakeConn(rows=[{"id": 7}])
        with mock.patch.object(db, "_connect", return_value=fake):
            self.assertTrue(db.save_snapshot(valid_snapshot()))
        sqls = [x[0] for x in fake.cur.executed]
        self.assertIn("ON DUPLICATE KEY UPDATE", sqls[0])
        self.assertIn("DELETE FROM auction_stock", sqls[2])
        self.assertTrue(any("INSERT INTO auction_stock" in sql for sql in sqls))
        self.assertTrue(fake.committed)

    def test_db_error_fails_soft(self):
        with mock.patch.object(db, "_connect", side_effect=RuntimeError("boom")):
            self.assertFalse(db.save_snapshot(valid_snapshot()))
        self.assertFalse(db.is_available())
        self.assertIn("boom", db.last_error())


class QueryTest(unittest.TestCase):
    def test_list_dates_shape(self):
        rows = [{
            "trade_date": date(2026, 8, 10),
            "fetched_at": datetime(2026, 8, 10, 9, 26, 1),
            "source": "东方财富",
            "auto": 1,
            "valid": 1,
            "stock_count": 200,
        }]
        with mock.patch.object(db, "_connect", return_value=FakeConn(rows=rows)):
            out = db.list_dates()
        self.assertEqual(out[0]["date"], "2026-08-10")
        self.assertEqual(out[0]["fetchedAt"], "2026-08-10 09:26:01")
        self.assertEqual(out[0]["stockCount"], 200)
        self.assertTrue(out[0]["auto"])

    def test_get_day_shape(self):
        day_row = {
            "id": 1,
            "trade_date": date(2026, 8, 10),
            "fetched_at": datetime(2026, 8, 10, 9, 26, 1),
            "source": "东方财富",
            "auto": 0,
            "valid": 1,
            "market_json": '{"up": 1, "down": 0}',
        }
        stock_row = {
            "code": "600000", "name": "测试股份", "industry": "银行",
            "price": 10.0, "change_pct": 1.0, "open": None, "prev_close": 9.9,
            "auction_amount": 1000.0, "auction_volume": 100.0, "turnover": 1.0,
            "volume_ratio": 1.1, "float_cap": 1e9, "total_cap": 1e9,
            "yesterday_amount": 500.0, "yesterday_turnover": 0.5,
            "yesterday_close": 9.9, "ratio_to_yesterday": 200.0,
            "amount_strength": 10.0, "auction_turnover": 1.0, "score": 80.0,
            "rank": 1, "tags": "超预期,高开",
        }
        with mock.patch.object(db, "_connect", return_value=FakeConn(rows=[day_row, stock_row])):
            day = db.get_day("2026-08-10")
        self.assertEqual(day["date"], "2026-08-10")
        self.assertTrue(day["validForAuction"])
        self.assertEqual(day["market"]["up"], 1)
        self.assertEqual(len(day["stocks"]), 1)
        self.assertEqual(day["stocks"][0]["tags"], ["超预期", "高开"])

    def test_get_stock_history(self):
        stock_row = {
            "trade_date": date(2026, 8, 10),
            "code": "600000", "name": "测试股份", "industry": "银行",
            "price": 10.0, "change_pct": 1.0, "open": None, "prev_close": 9.9,
            "auction_amount": 1000.0, "auction_volume": 100.0, "turnover": 1.0,
            "volume_ratio": 1.1, "float_cap": 1e9, "total_cap": 1e9,
            "yesterday_amount": 500.0, "yesterday_turnover": 0.5,
            "yesterday_close": 9.9, "ratio_to_yesterday": 200.0,
            "amount_strength": 10.0, "auction_turnover": 1.0, "score": 80.0,
            "rank": 1, "tags": None,
        }
        with mock.patch.object(db, "_connect", return_value=FakeConn(rows=[stock_row])):
            rows = db.get_stock_history("600000")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-08-10")
        self.assertEqual(rows[0]["code"], "600000")
        self.assertEqual(rows[0]["tags"], [])


class CsvTest(unittest.TestCase):
    def test_format_csv_headers_and_quoting(self):
        stocks = [
            {
                "code": "600000", "name": "测试,股份", "industry": "银行",
                "price": 10.0, "changePct": 1.0, "auctionAmount": 1000.0,
                "auctionVolume": 100.0, "auctionTurnover": 1.0, "volumeRatio": 1.1,
                "floatCap": 1e9, "yesterdayAmount": 500.0, "ratioToYesterday": 200.0,
                "amountStrength": 10.0, "score": 80.0, "rank": 1, "tags": ["超预期"],
            }
        ]
        text = db.format_csv("2026-08-10", stocks)
        lines = text.strip("\n").split("\n")
        self.assertTrue(lines[0].startswith("日期,代码,名称"))
        self.assertIn('"测试,股份"', lines[1])
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()