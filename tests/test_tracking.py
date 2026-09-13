# -*- coding: utf-8 -*-
"""二板战法跨日跟踪池单元测试（mock 网络）。"""
import json
import os
import tempfile
import unittest
from unittest import mock

from backend import server


def kline_with(turnovers, day_chg=1.0):
    """构造日K，turnover 为小数序列；未指定高开时按 day_chg 递增收盘。"""
    rows = []
    close = 10.0
    for i, t in enumerate(turnovers):
        prev = close
        close = round(prev * (1 + day_chg / 100), 2)
        rows.append({"date": "2026-01-%02d" % (i + 1), "open": prev, "close": close,
                     "high": close * 1.01, "low": prev * 0.99, "volume": 100000, "turnover": t})
    return rows


class TrackingTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)  # 从空开始
        self._orig = server.TRACK_FILE
        server.TRACK_FILE = self.path

    def tearDown(self):
        server.TRACK_FILE = self._orig
        if os.path.exists(self.path):
            os.remove(self.path)

    def _today(self, code="600001", resonant=True):
        return {"candidates": [{"code": code, "name": "测试股", "industry": "半导体",
                                "lb": 1, "score": 90, "stage": "首板健康",
                                "latestTurnover": 4.9, "prevVolRatio": 2.5,
                                "resonance": {"stock": True, "sector": True, "news": True,
                                              "resonant": resonant}}]}

    def test_new_entry_added(self):
        with mock.patch.object(server, "load_tactics", return_value=self._today()), \
             mock.patch.object(server, "_fetch_kline_full", return_value=kline_with([0.05, 0.05])):
            server.update_tracking("2026-01-02")
        t = server.load_tracking()
        self.assertEqual(len(t["entries"]), 1)
        self.assertEqual(t["entries"][0]["code"], "600001")
        self.assertFalse(t["entries"][0]["closed"])
        self.assertEqual(len(server.tracking_open()), 1)

    def test_non_resonant_not_added(self):
        with mock.patch.object(server, "load_tactics", return_value=self._today(resonant=False)), \
             mock.patch.object(server, "_fetch_kline_full", return_value=kline_with([0.05, 0.05])):
            server.update_tracking("2026-01-02")
        self.assertEqual(len(server.load_tracking()["entries"]), 0)

    def test_cash_out_closes_entry(self):
        # 第一天入池
        with mock.patch.object(server, "load_tactics", return_value=self._today()), \
             mock.patch.object(server, "_fetch_kline_full", return_value=kline_with([0.05, 0.05])):
            server.update_tracking("2026-01-02")
        # 次日放量兑现（换手 5→6→50）→ 出池
        with mock.patch.object(server, "load_tactics", return_value={"candidates": []}), \
             mock.patch.object(server, "_fetch_kline_full", return_value=kline_with([0.05, 0.06, 0.50])):
            server.update_tracking("2026-01-03")
        e = server.load_tracking()["entries"][0]
        self.assertTrue(e["closed"])
        self.assertEqual(e["status"], "兑现")
        self.assertEqual(len(server.tracking_open()), 0)

    def test_expire_after_track_days(self):
        with mock.patch.object(server, "load_tactics", return_value=self._today()), \
             mock.patch.object(server, "_fetch_kline_full", return_value=kline_with([0.05, 0.05])):
            server.update_tracking("2026-01-02")
        # 连续跟踪到超过 TRACK_DAYS 天（换手平缓，不触发兑现）
        for i in range(server.TRACK_DAYS):
            with mock.patch.object(server, "load_tactics", return_value={"candidates": []}), \
                 mock.patch.object(server, "_fetch_kline_full", return_value=kline_with([0.05, 0.05])):
                server.update_tracking("2026-01-%02d" % (3 + i))
        e = server.load_tracking()["entries"][0]
        self.assertTrue(e["closed"])
        self.assertEqual(e["exitReason"], "到期")


if __name__ == "__main__":
    unittest.main()
