"""Engine tests with a fake broker:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker import LIVE_STATUSES, BrokerError  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.engine import Engine  # noqa: E402
from bot.server import parse_alert  # noqa: E402

NY = ZoneInfo("America/New_York")
TF = 300_000


class FakeBroker:
    def __init__(self):
        self.orders: dict[int, dict] = {}
        self.pos = 0
        self.next = 1
        self.calls: list[tuple] = []
        self.pnl = 0.0

    def place_entry(self, side, qty, entry_px, stop_px, limit_px=None):
        eid, sid = self.next, self.next + 1
        self.next += 2
        self.orders[eid] = {"status": "Working", "px": entry_px, "side": side, "stop": sid}
        self.orders[sid] = {"status": "Suspended", "px": stop_px}
        self.calls.append(("place", side, entry_px, stop_px))
        return eid, sid

    def order_status(self, i):
        return self.orders[i]["status"]

    def cancel(self, i):
        if self.orders[i]["status"] in LIVE_STATUSES:
            self.orders[i]["status"] = "Canceled"
            self.calls.append(("cancel", i))
            return True
        return False

    def modify_stop(self, i, qty, px):
        self.orders[i]["px"] = px
        self.calls.append(("modify", i, px))
        return True

    def net_position(self):
        return self.pos

    def cancel_all(self):
        for i, o in self.orders.items():
            if o["status"] in LIVE_STATUSES:
                o["status"] = "Canceled"

    def flatten(self):
        self.pos = 0
        self.calls.append(("flatten",))

    def realized_pnl_since(self, since_ms):
        return self.pnl

    # what the exchange does
    def fill(self, side):
        eid = next(i for i, o in self.orders.items() if o.get("side") == side and o["status"] == "Working")
        self.orders[eid]["status"] = "Filled"
        self.orders[self.orders[eid]["stop"]]["status"] = "Working"
        self.pos += 1 if side == "long" else -1
        return eid

    def stop_out(self, sid):
        self.orders[sid]["status"] = "Filled"
        self.pos = 0


def ms(h, m):
    return int(datetime(2026, 11, 3, h, m, tzinfo=NY).timestamp() * 1000)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(secret="x", start=dtime(9, 30), end=dtime(16, 0), max_trades_per_day=0)
        self.b = FakeBroker()
        self.now = ms(15, 25) / 1000
        self.e = Engine(self.cfg, self.b, clock=lambda: self.now)

    def bar(self, h_, m_, hi, lo, L=0, S=0, late=1.0):
        """The candle that opened at h_:m_ closes; the alert arrives `late` seconds after the close."""
        t = ms(h_, m_)
        self.now = (t + TF) / 1000 + late
        self.e.handle({"ev": "bar", "sym": "MNQ1!", "tf": "5", "t": t, "tc": t + TF, "h": hi, "l": lo, "L": L, "S": S})

    def armed(self, side):
        return self.e.s["armed"].get(side)

    def test_the_screenshot_trade(self):
        # 15:25 failed 2 down closes: arm a buy stop 1 tick above its high, stop 1 tick under its low
        self.bar(15, 25, 28402.0, 28380.0, L=3)
        a = self.armed("long")
        self.assertEqual((a["entry"], a["stop"]), (28402.25, 28379.75))
        # 15:30 candle breaks the high: the exchange fills the buy stop
        self.b.fill("long")
        self.bar(15, 30, 28440.0, 28395.0)
        tr = self.e.s["trade"]
        self.assertEqual(tr["side"], "long")
        self.assertEqual(tr["stop"], 28394.75)        # trailed to the entry candle's low - 1 tick
        self.assertEqual(self.e.s["trades_today"], 1)
        # every closed candle moves the stop up to its low - 1 tick
        self.bar(15, 35, 28480.0, 28430.0)
        self.assertEqual(tr["stop"], 28429.75)
        self.bar(15, 40, 28560.0, 28470.0)
        self.assertEqual(self.e.s["trade"]["stop"], 28469.75)
        # a lower low never moves the stop down
        self.bar(15, 45, 28555.0, 28460.0)
        self.assertEqual(self.e.s["trade"]["stop"], 28469.75)
        # the next candle takes out the low: stopped out, flat, and the trade is closed
        self.b.stop_out(self.e.s["trade"]["stop_id"])
        self.bar(15, 50, 28500.0, 28440.0)
        self.assertIsNone(self.e.s["trade"])
        self.assertNotIn(("flatten",), self.b.calls)

    def test_no_break_cancels_and_rearms(self):
        self.bar(10, 0, 100.0, 90.0, L=3)
        first = self.armed("long")["entry_id"]
        self.bar(10, 5, 99.0, 91.0, L=4)
        self.assertEqual(self.b.orders[first]["status"], "Canceled")
        self.assertEqual(self.armed("long")["entry"], 99.25)

    def test_cancel_message_when_the_other_side_breaks_first(self):
        self.bar(10, 0, 100.0, 90.0, L=3)
        eid = self.armed("long")["entry_id"]
        self.e.handle({"ev": "cancel", "sym": "MNQ1!", "tf": "5", "side": "long", "t": ms(10, 0)})
        self.assertEqual(self.b.orders[eid]["status"], "Canceled")
        self.assertIsNone(self.armed("long"))

    def test_both_sides_one_fills_the_other_is_canceled(self):
        self.bar(10, 0, 100.0, 90.0, L=15, S=4)
        self.assertIsNotNone(self.armed("long"))
        self.assertIsNotNone(self.armed("short"))
        self.b.fill("short")
        self.bar(10, 5, 95.0, 85.0)
        self.assertEqual(self.e.s["trade"]["side"], "short")
        self.assertEqual(self.e.s["trade"]["stop"], 95.25)
        long_id = next(i for i, o in self.b.orders.items() if o.get("side") == "long")
        self.assertEqual(self.b.orders[long_id]["status"], "Canceled")

    def test_both_sides_off_keeps_the_better_rank(self):
        self.cfg.both_sides = False
        self.bar(10, 0, 100.0, 90.0, L=15, S=4)   # 3-1-2 reversal (4) ranks above 3-1-2 continuation (15)
        self.assertIsNone(self.armed("long"))
        self.assertIsNotNone(self.armed("short"))

    def test_late_alert_does_not_arm(self):
        self.bar(10, 0, 100.0, 90.0, L=3, late=40)
        self.assertEqual(self.e.s["armed"], {})

    def test_outside_window_and_after_flatten(self):
        self.bar(8, 0, 100.0, 90.0, L=3)
        self.assertEqual(self.e.s["armed"], {})
        self.bar(15, 55, 100.0, 90.0, L=3)
        self.assertEqual(self.e.s["armed"], {})

    def test_flatten_time_closes_the_trade(self):
        self.bar(15, 40, 100.0, 90.0, L=3)
        self.b.fill("long")
        self.bar(15, 45, 105.0, 95.0)
        self.assertIsNotNone(self.e.s["trade"])
        self.now = ms(15, 55) / 1000 + 2
        self.e.on_timer()
        self.assertIn(("flatten",), self.b.calls)
        self.assertIsNone(self.e.s["trade"])
        self.assertEqual(self.b.pos, 0)

    def test_daily_limits(self):
        self.cfg.max_trades_per_day = 1
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.b.fill("long")
        self.bar(10, 5, 105.0, 95.0)
        self.b.stop_out(self.e.s["trade"]["stop_id"])
        self.bar(10, 10, 104.0, 94.0, L=3)
        self.assertEqual(self.e.s["armed"], {})            # 1 trade today, the max
        self.cfg.max_trades_per_day = 0
        self.cfg.max_daily_loss = 100.0
        self.b.pnl = -120.0
        self.bar(10, 15, 104.0, 94.0, L=3)
        self.assertEqual(self.e.s["armed"], {})            # daily loss hit

    def test_stop_size_filter(self):
        self.cfg.max_stop_ticks = 20
        self.bar(10, 0, 100.0, 90.0, L=3)                  # 41 ticks
        self.assertEqual(self.e.s["armed"], {})

    def test_unknown_position_is_flattened(self):
        self.b.pos = 2
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.assertIn(("flatten",), self.b.calls)
        self.assertEqual(self.e.s["armed"], {})

    def test_missing_stop_flattens(self):
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.b.fill("long")
        sid = self.armed("long")["stop_id"]
        self.b.orders[sid]["status"] = "Canceled"           # someone canceled the stop by hand
        self.bar(10, 5, 105.0, 95.0)
        self.assertIn(("flatten",), self.b.calls)
        self.assertIsNone(self.e.s["trade"])

    def test_rejected_entry_is_not_armed(self):
        def reject(*a, **k):
            raise BrokerError("price already through")
        self.b.place_entry = reject
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.assertEqual(self.e.s["armed"], {})

    def test_duplicate_and_foreign_messages_are_ignored(self):
        self.bar(10, 0, 100.0, 90.0, L=3)
        n = len(self.b.calls)
        self.e.handle({"ev": "bar", "sym": "MNQ1!", "tf": "5", "t": ms(10, 0), "tc": ms(10, 5), "h": 1, "l": 0, "L": 3})
        self.e.handle({"ev": "bar", "sym": "ES1!", "tf": "5", "t": ms(10, 5), "tc": ms(10, 10), "h": 1, "l": 0, "L": 3})
        self.e.handle({"ev": "bar", "sym": "MNQ1!", "tf": "15", "t": ms(10, 5), "tc": ms(10, 10), "h": 1, "l": 0, "L": 3})
        self.assertEqual(len(self.b.calls), n)

    def test_parse_alert(self):
        self.assertIsNone(parse_alert(b"TRIGGER NOW LONG MNQ1! 5 | ...", "s"))
        self.assertIsNone(parse_alert(b'{"key":"bad","ev":"bar"}', "s"))
        self.assertEqual(parse_alert(b'{"key":"s","ev":"bar","h":1}', "s"), {"ev": "bar", "h": 1})


if __name__ == "__main__":
    unittest.main()
