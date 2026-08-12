# -*- coding: utf-8 -*-
"""server.py 竞价封单额纯函数单元测试（无网络）。"""
import unittest
from unittest import mock

from backend import server


def _tx_line(code="sz000001", name="平安银行", price="11.29", prev_close="11.19",
             bid1_price="11.28", bid1_vol="1000", ask1_price="11.29", ask1_vol="500"):
    """构造腾讯 qt.gtimg.cn 单行（17+ 段，覆盖所需索引 3,4,9,10,15,16）。"""
    f = [""] * 30
    f[0] = "1"
    f[1] = name
    f[2] = code
    f[3] = price
    f[4] = prev_close
    f[9] = bid1_price
    f[10] = bid1_vol
    f[15] = ask1_price
    f[16] = ask1_vol
    return 'v_%s="%s";' % (code, "~".join(f))


class TestSealFromTxLine(unittest.TestCase):
    def test_parses_seal_amount(self):
        parsed = server._seal_from_tx_line(_tx_line())
        self.assertAlmostEqual(parsed["bid1Price"], 11.28)
        self.assertAlmostEqual(parsed["bid1Volume"], 1000.0)
        self.assertAlmostEqual(parsed["sealAmount"], 1000 * 100 * 11.28, places=2)

    def test_missing_equals_returns_none(self):
        self.assertIsNone(server._seal_from_tx_line("garbage"))

    def test_short_line_returns_none(self):
        self.assertIsNone(server._seal_from_tx_line('v_sz000001="1~x~000001";'))

    def test_zero_bid_volume_returns_none(self):
        self.assertIsNone(server._seal_from_tx_line(_tx_line(bid1_vol="0")))

    def test_zero_bid_price_returns_none(self):
        self.assertIsNone(server._seal_from_tx_line(_tx_line(bid1_price="0.00")))


class TestFetchSealQuotes(unittest.TestCase):
    def _mock_resp(self, text):
        return type("Resp", (), {"text": text})()

    def test_batch_and_code_recovery(self):
        text = (_tx_line(code="sz000001") + "\n" +
                _tx_line(code="sh600519", name="贵州茅台", price="1348.86", bid1_price="1348.00", bid1_vol="27") + "\n")
        with mock.patch.object(server, "_gtimg_get", return_value=self._mock_resp(text)):
            quotes = server.fetch_seal_quotes(["000001", "600519"])
        self.assertEqual(set(quotes.keys()), {"000001", "600519"})
        self.assertEqual(quotes["600519"]["name"], "贵州茅台")

    def test_missing_line_skipped(self):
        text = _tx_line(code="sz000001") + "\n"
        with mock.patch.object(server, "_gtimg_get", return_value=self._mock_resp(text)):
            quotes = server.fetch_seal_quotes(["000001", "600519"])
        self.assertEqual(set(quotes.keys()), {"000001"})

    def test_batching_calls_multiple_batches(self):
        text = _tx_line(code="sz000001") + "\n"
        orig_batch = server.SEAL_BATCH_SIZE
        server.SEAL_BATCH_SIZE = 2
        try:
            with mock.patch.object(server, "_gtimg_get", return_value=self._mock_resp(text)) as m:
                server.fetch_seal_quotes(["000001", "600519", "300059"])
        finally:
            server.SEAL_BATCH_SIZE = orig_batch
        self.assertEqual(m.call_count, 2)  # 3 只 / 批 2 = 2 批


class TestFetchSealTop20(unittest.TestCase):
    def test_top20_ranking(self):
        now = __import__("datetime").datetime(2026, 8, 10, 9, 15, 0)
        quotes = {
            "000001": {"bid1Price": 10.0, "bid1Volume": 100, "sealAmount": 100000},
            "600519": {"bid1Price": 20.0, "bid1Volume": 200, "sealAmount": 400000},
            "300059": {"bid1Price": 30.0, "bid1Volume": 300, "sealAmount": 900000},
        }
        with mock.patch.object(server, "fetch_all_codes", return_value=["000001", "600519", "300059"]), \
             mock.patch.object(server, "fetch_seal_quotes", return_value=quotes):
            snap = server.fetch_seal_top20("09:15", now=now)
        self.assertEqual(len(snap["records"]), 3)
        self.assertEqual(snap["records"][0]["code"], "300059")
        self.assertEqual(snap["records"][0]["sealRank"], 1)
        self.assertEqual(snap["records"][1]["sealRank"], 2)
        self.assertEqual(snap["date"], "2026-08-10")
        self.assertEqual(snap["sampleTime"], "09:15")


if __name__ == "__main__":
    unittest.main()
