"""The rejection check in place_entry, with Tradovate's answers faked:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import tradovate  # noqa: E402
from bot.broker import BrokerError, PriceAlreadyPast  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.tradovate import TradovateBroker  # noqa: E402


class FakeTradovate(TradovateBroker):
    def __init__(self, status="Working", reports=None, status_error=False, stop_status="Suspended"):
        self.cfg = Config(secret="x")
        self.stop_status, self.flattened, self.sent = stop_status, False, None
        self.snapshot = {"netLiq": 17401.06, "totalCashValue": 17000.0}
        self.account_spec, self.account_id = "DEMO", 1
        self.status, self.reports, self.status_error = status, reports or [], status_error
        self.canceled = []

    def _call(self, method, path, body=None, retry=True, **params):
        if path == "/order/placeoso":
            self.sent = body
            return {"orderId": 10, "oso1Id": 11}
        if path == "/order/item":
            if self.status_error:
                raise BrokerError("no answer")
            return {"ordStatus": self.stop_status if params.get("id") == 11 else self.status}
        if path == "/commandReport/deps":
            return self.reports
        if path == "/cashBalance/getcashbalancesnapshot":
            return self.snapshot
        if path == "/order/cancelorder":
            self.canceled.append(body["orderId"])
            return {}
        raise AssertionError(path)

    def flatten(self):
        self.flattened = True


@mock.patch.object(tradovate, "REJECT_CHECK_S", 0)
@mock.patch.object(tradovate, "LATE_POLL_S", 0)
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
        self.assertIsInstance(e.exception, PriceAlreadyPast)
        self.assertIn("already passed", str(e.exception))
        self.assertIn("InvalidPrice, Please check the order price.", str(e.exception))
        self.assertEqual(b.canceled, [11])

    def test_rejected_entry_without_a_reason_still_raises(self):
        with self.assertRaisesRegex(BrokerError, "No reason was given"):
            self.place(FakeTradovate("Rejected"))

    def test_a_failed_check_keeps_tracking_the_order(self):
        self.assertEqual(self.place(FakeTradovate(status_error=True)), (10, 11))

    def test_other_rejections_are_not_a_passed_price(self):
        b = FakeTradovate("Rejected", [{"rejectReason": "InsufficientFunds", "text": "No margin."}])
        with self.assertRaises(BrokerError) as e:
            self.place(b)
        self.assertNotIsInstance(e.exception, PriceAlreadyPast)

    def test_balance_is_the_net_liquidation_value(self):
        self.assertEqual(FakeTradovate().account_balance(), 17401.06)

    def test_unreadable_balance_raises(self):
        b = FakeTradovate()
        b.snapshot = {"errorText": "Access is denied"}
        with self.assertRaisesRegex(BrokerError, "Access is denied"):
            b.account_balance()

    def test_orders_go_to_the_chosen_contract(self):
        b = FakeTradovate()
        b.place_entry("long", 3, 100.0, 90.0, symbol="NQZ6")
        self.assertEqual(b.sent["symbol"], "NQZ6")
        b.place_entry("long", 3, 100.0, 90.0)
        self.assertEqual(b.sent["symbol"], "MNQZ6")
        b = FakeTradovate("Filled", stop_status="Working")
        b.place_late_entry("long", 3, 100.0, 90.0, "NQZ6")
        self.assertEqual(b.sent["symbol"], "NQZ6")

    def late(self, b, side="long", limit=102.5, stop=90.0):
        return b.place_late_entry(side, 1, limit, stop)

    def test_late_entry_is_a_limit_that_cannot_rest(self):
        b = FakeTradovate("Filled", stop_status="Working")
        self.assertEqual(self.late(b), (10, 11))
        self.assertEqual((b.sent["orderType"], b.sent["timeInForce"], b.sent["price"]), ("Limit", "FOK", 102.5))
        self.assertEqual((b.sent["action"], b.sent["bracket1"]["action"], b.sent["bracket1"]["stopPrice"]), ("Buy", "Sell", 90.0))

    def test_late_entry_short_sends_the_opposite_sides(self):
        b = FakeTradovate("Filled", stop_status="Working")
        self.late(b, "short", 87.5, 100.0)
        self.assertEqual((b.sent["action"], b.sent["bracket1"]["action"]), ("Sell", "Buy"))

    def test_late_entry_not_filled_returns_nothing(self):
        b = FakeTradovate("Canceled")
        self.assertIsNone(self.late(b))
        self.assertEqual(b.canceled, [11])

    def test_late_entry_stuck_on_the_book_is_canceled(self):
        b = FakeTradovate("Working")
        self.assertIsNone(self.late(b))
        self.assertEqual(b.canceled, [10, 11])

    def test_late_entry_rejected_raises(self):
        b = FakeTradovate("Rejected", [{"rejectReason": "InvalidPrice", "text": "No."}])
        with self.assertRaisesRegex(BrokerError, "late entry rejected"):
            self.late(b)
        self.assertEqual(b.canceled, [11])

    def test_late_entry_filled_without_a_stop_is_closed(self):
        b = FakeTradovate("Filled", stop_status="Rejected")
        with self.assertRaisesRegex(BrokerError, "position was closed"):
            self.late(b)
        self.assertTrue(b.flattened)

    def test_late_entry_unreadable_stays_tracked(self):
        self.assertEqual(self.late(FakeTradovate(status_error=True)), (10, 11))



class TwoContracts(TradovateBroker):
    def __init__(self, positions, fills=()):
        self.cfg = Config(secret="x", big_symbol="NQZ6")
        self.account_id = 1
        self.contract_id = 100
        self.contract_values = {100: 2.0, 200: 20.0}
        self.positions, self.fills, self.liquidated, self.canceled = positions, list(fills), [], []

    def _call(self, method, path, body=None, retry=True, **params):
        if path == "/position/list":
            return self.positions
        if path == "/fill/list":
            return self.fills
        if path == "/order/liquidateposition":
            self.liquidated.append(body["contractId"])
            return {}
        raise AssertionError(path)


class TwoContractsTest(unittest.TestCase):
    def pos(self, contract, net, account=1):
        return {"accountId": account, "contractId": contract, "netPos": net}

    def test_a_position_on_either_contract_counts(self):
        self.assertEqual(TwoContracts([self.pos(100, 0), self.pos(200, 3)]).net_position(), 3)
        self.assertEqual(TwoContracts([self.pos(100, -16)]).net_position(), -16)

    def test_other_accounts_and_contracts_do_not_count(self):
        self.assertEqual(TwoContracts([self.pos(300, 5), self.pos(200, 5, account=2)]).net_position(), 0)

    def test_flatten_closes_only_what_is_open(self):
        b = TwoContracts([self.pos(100, 0), self.pos(200, 3)])
        b.flatten()
        self.assertEqual(b.liquidated, [200])

    def test_daily_pnl_values_each_contract_by_its_own_size(self):
        ts = "2026-11-05T01:00:00Z"
        fills = [{"contractId": 200, "action": "Buy", "price": 100.0, "qty": 2, "timestamp": ts},
                 {"contractId": 200, "action": "Sell", "price": 101.0, "qty": 2, "timestamp": ts},
                 {"contractId": 100, "action": "Buy", "price": 100.0, "qty": 10, "timestamp": ts},
                 {"contractId": 100, "action": "Sell", "price": 99.0, "qty": 10, "timestamp": ts}]
        self.assertEqual(TwoContracts([], fills).realized_pnl_since(0), 20.0)


if __name__ == "__main__":
    unittest.main()
