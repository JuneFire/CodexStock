# -*- coding: utf-8 -*-
import os
import unittest

from backend import db

TEST_DATE = "2099-01-05"
TEST_CODE = "600000"


def sample_snapshot():
    return {
        "ok": True,
        "date": TEST_DATE,
        "fetchedAt": "2099-01-05 09:26:00",
        "source": "东方财富",
        "auto": True,
        "validForAuction": True,
        "market": {"indices": [], "up": 1, "down": 0, "flat": 0, "limitUp": 0, "totalAmount": 1},
        "stocks": [
            {
                "code": TEST_CODE, "name": "集成测试", "industry": "银行",
                "price": 10.0, "changePct": 1.0, "open": None, "prevClose": 9.9,
                "auctionAmount": 1000.0, "auctionVolume": 100.0, "turnover": 1.0,
                "volumeRatio": 1.1, "floatCap": 1e9, "totalCap": 1e9,
                "yesterdayAmount": 500.0, "yesterdayTurnover": 0.5,
                "yesterdayClose": 9.9, "ratioToYesterday": 200.0,
                "amountStrength": 10.0, "auctionTurnover": 1.0, "score": 80.0,
                "rank": 1, "tags": ["超预期"],
            }
        ],
    }


def cleanup():
    conn = db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM auction_day WHERE trade_date = %s", (TEST_DATE,))
            row = cur.fetchone()
            if row:
                cur.execute("DELETE FROM auction_stock WHERE day_id = %s", (row["id"],))
                cur.execute("DELETE FROM auction_day WHERE id = %s", (row["id"],))
            cur.execute("DELETE FROM auction_seal WHERE trade_date = %s", (TEST_DATE,))
        conn.commit()
    finally:
        conn.close()


def seal_record():
    return {
        "tradeDate": TEST_DATE, "code": TEST_CODE, "name": "集成测试",
        "sampleTime": "09:15", "bid1Price": 10.0, "bid1Volume": 1000.0,
        "ask1Price": 10.01, "ask1Volume": 200.0, "lastPrice": 10.0,
        "prevClose": 9.9, "sealAmount": 1000000.0, "sealRank": 1,
        "fetchedAt": "2099-01-05 09:15:02",
    }


@unittest.skipUnless(os.environ.get("RUN_DB_TESTS") == "1", "需要 RUN_DB_TESTS=1 且本地 MySQL 可连接")
class MySqlIntegrationTest(unittest.TestCase):
    def test_roundtrip(self):
        self.assertTrue(db.ensure_schema(), db.last_error())
        cleanup()
        try:
            self.assertTrue(db.save_snapshot(sample_snapshot()), db.last_error())
            # 重复写入同一日期只保留一条
            self.assertTrue(db.save_snapshot(sample_snapshot()), db.last_error())
            day = db.get_day(TEST_DATE)
            self.assertIsNotNone(day)
            self.assertEqual(len(day["stocks"]), 1)
            dates = db.list_dates()
            self.assertTrue(any(d["date"] == TEST_DATE for d in dates))
            rows = db.get_stock_history(TEST_CODE)
            self.assertTrue(rows)
            self.assertEqual(rows[0]["date"], TEST_DATE)
            csv_text = db.export_csv(TEST_DATE)
            self.assertIsNotNone(csv_text)
            self.assertTrue(csv_text.startswith("日期,代码,名称"))
        finally:
            cleanup()

    def test_seal_roundtrip(self):
        self.assertTrue(db.ensure_schema(), db.last_error())
        cleanup()
        try:
            self.assertTrue(db.save_seal([seal_record()]), db.last_error())
            rows = db.get_seal(TEST_DATE)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["code"], TEST_CODE)
            self.assertEqual(rows[0]["sampleTime"], "09:15")
            self.assertEqual(rows[0]["sealAmount"], 1000000.0)
            dates = db.list_seal_dates()
            self.assertTrue(any(d["date"] == TEST_DATE for d in dates))
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()