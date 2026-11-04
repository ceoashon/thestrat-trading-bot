"""The rejection check in place_entry, with Tradovate's answers faked:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import tradovate  # noqa: E402
from bot.broker import BrokerError  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.tradovate import TradovateBroker  # noqa: E402


class FakeTradovate(TradovateBroker):
    def __init__(self, status="Working", reports=None, status_error=False):
        self.cfg = Config(secret="x")
        self.account_spec, self.account_id = "DEMO", 1
        self.status, self.reports, self.status_error = status, reports or [], status_error
        self.canceled = []

    def _call(self, method, path, body=None, retry=True, **params):
        if path == "/order/placeoso":
            return {"orderId": 10, "oso1Id": 11}
        if path == "/order/item":
            if self.status_error:
                raise BrokerError("no answer")
            return {"ordStatus": self.status}
        if path == "/commandReport/deps":
            return self.reports
        if path == "/order/cancelorder":
            self.canceled.append(body["orderId"])
            return {}
        raise AssertionError(path)


@mock.patch.object(tradovate, "REJECT_CHECK_S", 0)
class PlaceEntryTest(unittest.TestCase):
    def place(self, b):
        return b.place_entry("long", 1, 100.0, 90.0)

    def test_accepted_entry_returns_its_orders(self):
        for status in ("Working", "PendingNew", "Filled"):
            self.assertEqual(self.place(FakeTradovate(status)), (10, 11))

    def test_rejected_entry_raises_with_the_reason(self):
        b = FakeTradovate("Rejected", [{"commandStatus": "RiskPassed"},
                                       {"rejectReason": "InvalidPrice", "text": "Please check the order price."}])
        with self.assertRaises(BrokerError) as e:
            self.place(b)
        self.assertIn("already passed", str(e.exception))
        self.assertIn("InvalidPrice, Please check the order price.", str(e.exception))
        self.assertEqual(b.canceled, [11])

    def test_rejected_entry_without_a_reason_still_raises(self):
        with self.assertRaisesRegex(BrokerError, "No reason was given"):
            self.place(FakeTradovate("Rejected"))

    def test_a_failed_check_keeps_tracking_the_order(self):
        self.assertEqual(self.place(FakeTradovate(status_error=True)), (10, 11))


if __name__ == "__main__":
    unittest.main()
