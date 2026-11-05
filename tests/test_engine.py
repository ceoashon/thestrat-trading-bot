"""Engine tests with a fake broker:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker import LIVE_STATUSES, BrokerError, PriceAlreadyPast  # noqa: E402
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
        self.late_fills = True
        self.balance = 17401.06
        self.partial: dict[int, int] = {}
        self.stop_qty = None
        self.fill_ms: dict[int, int] = {}
        self.fees = 0.0
        self.closing_fill = None

    def place_entry(self, side, qty, entry_px, stop_px, limit_px=None, symbol=None):
        eid, sid = self.next, self.next + 1
        self.next += 2
        self.orders[eid] = {"status": "Working", "px": entry_px, "side": side, "stop": sid, "qty": qty, "symbol": symbol}
        self.orders[sid] = {"status": "Suspended", "px": stop_px}
        self.calls.append(("place", side, entry_px, stop_px))
        return eid, sid

    def place_late_entry(self, side, qty, limit_px, stop_px, symbol=None):
        self.calls.append(("late", side, limit_px, stop_px))
        if not self.late_fills:
            return None
        eid, sid = self.next, self.next + 1
        self.next += 2
        self.orders[eid] = {"status": "Filled", "px": limit_px - 0.5 if side == "long" else limit_px + 0.5, "side": side, "stop": sid, "qty": qty}
        self.orders[sid] = {"status": "Working", "px": stop_px}
        self.pos += qty if side == "long" else -qty
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
        self.stop_qty = qty
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

    def filled_qty(self, i):
        return self.partial.get(i, 0)

    def fill_time(self, i):
        return self.fill_ms.get(i)

    def order_fees(self, i):
        return self.fees

    def last_fill(self):
        return self.closing_fill

    def account_balance(self):
        if self.balance is None:
            raise BrokerError("could not read the account balance")
        return self.balance

    def fill_price(self, i):
        return self.orders[i]["px"] if self.orders[i]["status"] == "Filled" else None

    # what the exchange does
    def fill(self, side):
        eid = next(i for i, o in self.orders.items() if o.get("side") == side and o["status"] == "Working")
        self.orders[eid]["status"] = "Filled"
        self.orders[self.orders[eid]["stop"]]["status"] = "Working"
        q = self.orders[eid]["qty"]
        self.pos += q if side == "long" else -q
        return eid

    def stop_out(self, sid):
        self.orders[sid]["status"] = "Filled"
        self.pos = 0


class FakeNotifier:
    def __init__(self):
        self.msgs: list[str] = []

    def send(self, text):
        self.msgs.append(text)

    def ping(self):
        pass


def ms(h, m):
    return int(datetime(2026, 11, 3, h, m, tzinfo=NY).timestamp() * 1000)


class FakeJournal:
    def __init__(self):
        self.trades: list[dict] = []

    def log(self, trade):
        self.trades.append(trade)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(secret="x", start=dtime(9, 30), end=dtime(16, 0), max_trades_per_day=0)
        self.b = FakeBroker()
        self.now = ms(15, 25) / 1000
        self.n = FakeNotifier()
        self.j = FakeJournal()
        self.e = Engine(self.cfg, self.b, clock=lambda: self.now, notifier=self.n, journal=self.j)
        self.e.FLAT_WAIT_S = 0

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

    def test_discord_messages_for_a_trade(self):
        self.bar(10, 0, 28402.0, 28380.0, L=3)
        self.b.fill("long")
        self.bar(10, 5, 28440.0, 28395.0)
        self.b.stop_out(self.e.s["trade"]["stop_id"])
        self.b.pnl = 2 * (28394.75 - 28402.25)
        self.bar(10, 10, 28400.0, 28380.0)
        text = "\n".join(self.n.msgs)
        self.assertIn("ARMED LONG", text)
        self.assertIn("FILLED LONG", text)
        self.assertIn("at 28402.25", text)
        self.assertIn("Stop moved 28379.75 → 28394.75", text)
        self.assertIn("CLOSED LONG", text)
        self.assertIn("-7.50 pts x1 = **-$15.00**", text)
        self.assertIn("today -$15.00", text)

    def test_skips_outside_the_window_are_quiet(self):
        self.bar(8, 0, 100.0, 90.0, L=3)
        self.assertEqual(self.n.msgs, [])
        self.bar(10, 0, 100.0, 90.0, L=3, late=40)
        self.assertIn("not taken", self.n.msgs[-1])

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

    def miss_the_break(self):
        def rejected(*a, **k):
            raise PriceAlreadyPast("rejected by Tradovate")
        self.b.place_entry = rejected
        self.cfg.late_entry_ticks = 10

    def test_setups_list_keeps_only_those_setups(self):
        self.cfg.setups = [1, 2, 3]
        self.bar(10, 0, 100.0, 90.0, L=23)
        self.assertEqual(self.e.s["armed"], {})
        self.assertEqual(self.n.msgs, [])
        self.bar(10, 5, 100.0, 90.0, L=3)
        self.assertIsNotNone(self.armed("long"))

    def test_setups_list_works_per_side(self):
        self.cfg.setups = [3]
        self.bar(10, 0, 100.0, 90.0, L=14, S=3)
        self.assertIsNone(self.armed("long"))
        self.assertIsNotNone(self.armed("short"))

    def test_setups_list_can_take_outside_bars(self):
        self.cfg.setups = [14]
        self.bar(10, 0, 100.0, 90.0, L=14)
        self.assertIsNotNone(self.armed("long"))

    def test_empty_setups_list_takes_everything(self):
        self.bar(10, 0, 100.0, 90.0, L=23)
        self.assertIsNotNone(self.armed("long"))

    def test_late_break_enters_within_the_cap(self):
        self.miss_the_break()
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.assertEqual(self.b.calls, [("late", "long", 102.75, 89.75)])
        self.assertIn("LATE ENTRY LONG", self.n.msgs[-1])
        self.bar(10, 5, 105.0, 101.0)
        self.assertEqual(self.e.s["trades_today"], 1)
        self.assertEqual(self.e.s["trade"]["side"], "long")
        self.assertEqual(self.e.s["trade"]["stop"], 100.75)

    def test_late_break_short_caps_below_the_entry(self):
        self.miss_the_break()
        self.bar(10, 0, 100.0, 90.0, S=3)
        self.assertEqual(self.b.calls, [("late", "short", 87.25, 100.25)])

    def test_late_break_too_far_is_no_trade(self):
        self.miss_the_break()
        self.b.late_fills = False
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.assertEqual(self.e.s["armed"], {})
        self.assertIn("missed", self.n.msgs[-1])
        self.assertEqual(self.e.s["trades_today"], 0)

    def test_late_break_off_only_reports(self):
        self.miss_the_break()
        self.cfg.late_entry_ticks = 0
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.assertEqual(self.b.calls, [])
        self.assertIn("was not placed", self.n.msgs[-1])

    def test_other_rejections_do_not_enter_late(self):
        def rejected(*a, **k):
            raise BrokerError("rejected by Tradovate. InsufficientFunds")
        self.b.place_entry = rejected
        self.cfg.late_entry_ticks = 10
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.assertEqual(self.b.calls, [])
        self.assertIn("was not placed", self.n.msgs[-1])

    def test_risk_percent_sizes_the_trade(self):
        self.cfg.risk_pct = 5.0
        self.bar(10, 0, 126.0, 100.0, L=3)          # 26.5 points of risk = $53 a contract, 5% of the balance = $870.05
        self.assertEqual(self.armed("long")["qty"], 16)
        self.assertIn("x16", self.n.msgs[-1])
        self.assertIn("risk -$848.00", self.n.msgs[-1])

    def test_a_very_tight_stop_stops_at_the_most_contracts(self):
        self.cfg.risk_pct = 5.0
        self.bar(10, 0, 102.0, 100.0, L=3)          # 2.5 points = $5 a contract would be 174 contracts
        self.assertEqual(self.armed("long")["qty"], 100)

    def size(self, high):
        """The contract and size for a long setup candle from 100.0 up to high. 5% of the balance is $870.05."""
        self.cfg.risk_pct, self.cfg.big_symbol = 5.0, "NQZ6"
        self.e.s["armed"] = {}
        self.e.s["last_bar_t"] = 0
        self.bar(10, 0, high, 100.0, L=3)
        a = self.armed("long")
        return (a["symbol"], a["qty"]) if a else (None, 0)

    def test_under_twenty_micros_stays_on_micros(self):
        self.assertEqual(self.size(122.5), ("MNQZ6", 18))      # 23 points = $46 a micro

    def test_twenty_micros_or_more_becomes_nq_rounded_down(self):
        self.assertEqual(self.size(121.0), ("NQZ6", 2))        # 21.5 points = 20 micros
        self.assertEqual(self.size(117.0), ("NQZ6", 2))        # 17.5 points = 24 micros
        self.assertEqual(self.size(115.0), ("NQZ6", 2))        # 15.5 points = 28 micros, 3 NQ would be 30
        self.assertEqual(self.size(113.5), ("NQZ6", 3))        # 14 points = 31 micros
        self.assertEqual(self.size(110.0), ("NQZ6", 4))        # 10.5 points = 41 micros

    def test_nq_never_risks_more_than_the_percent(self):
        limit = self.b.balance * 0.05
        for ticks in range(1, 2000):
            high = 100.0 + ticks * 0.25
            symbol, qty = self.size(high)
            if not qty:
                continue
            risk = (high + 0.25 - 99.75) * self.cfg.value_of(symbol) * qty
            self.assertLessEqual(risk, limit, (ticks, symbol, qty))

    def test_a_tight_stop_on_nq_uses_the_full_percent(self):
        self.assertEqual(self.size(102.0), ("NQZ6", 17))       # 2.5 points = 174 micros, past the 100 micro limit

    def test_nq_size_still_has_a_limit(self):
        self.cfg.max_qty = 5
        self.assertEqual(self.size(102.0), ("NQZ6", 5))

    def test_an_nq_trade_reports_dollars_at_twenty_a_point(self):
        self.size(110.0)                                       # 4 NQ, entry 110.25, stop 99.75
        self.assertIn("ARMED LONG** NQZ6 x4", self.n.msgs[-1])
        self.assertIn("risk -$840.00", self.n.msgs[-1])
        self.b.fill("long")
        self.bar(10, 5, 115.0, 108.0)
        tr = self.e.s["trade"]
        self.assertEqual((tr["symbol"], tr["qty"], self.b.pos), ("NQZ6", 4, 4))
        self.assertNotIn(("flatten",), self.b.calls)
        self.b.orders[tr["stop_id"]]["px"] = 107.75
        self.b.stop_out(tr["stop_id"])
        self.bar(10, 10, 109.0, 106.0)
        self.assertIn("CLOSED LONG** NQZ6", self.n.msgs[-1])
        self.assertIn("-2.50 pts x4 = **-$200.00**", self.n.msgs[-1])
        t = self.j.trades[-1]
        self.assertEqual((t["symbol"], t["qty"], t["gross"], t["riskUsd"]), ("NQZ6", 4, -200.0, 840.0))

    def test_a_late_entry_can_be_nq(self):
        self.miss_the_break()
        self.cfg.risk_pct, self.cfg.big_symbol = 5.0, "NQZ6"
        self.bar(10, 0, 110.0, 100.0, L=3)                     # limit 112.75, stop 99.75: 13 points = 33 micros
        self.assertEqual(self.b.calls, [("late", "long", 112.75, 99.75)])
        a = self.armed("long")
        self.assertEqual((a["symbol"], a["qty"]), ("NQZ6", 3))
        self.assertIn("LATE ENTRY LONG** NQZ6 x3", self.n.msgs[-1])

    def test_a_sized_trade_runs_with_its_own_size(self):
        self.cfg.risk_pct = 5.0
        self.bar(10, 0, 126.0, 100.0, L=3)
        self.b.fill("long")
        self.bar(10, 5, 130.0, 110.0)
        tr = self.e.s["trade"]
        self.assertEqual((tr["qty"], self.b.stop_qty, self.b.pos), (16, 16, 16))
        self.assertNotIn(("flatten",), self.b.calls)
        self.b.stop_out(tr["stop_id"])
        self.bar(10, 10, 112.0, 105.0)
        self.assertIn("x16 = **", self.n.msgs[-1])
        self.assertIsNone(self.e.s["trade"])

    def test_stop_too_wide_for_one_contract_is_not_taken(self):
        self.cfg.risk_pct = 5.0
        self.bar(10, 0, 1000.0, 100.0, L=3)
        self.assertEqual(self.e.s["armed"], {})
        self.assertEqual(self.b.calls, [])
        self.assertIn("not taken", self.n.msgs[-1])

    def test_unreadable_balance_places_nothing(self):
        self.cfg.risk_pct = 5.0
        self.b.balance = None
        self.bar(10, 0, 126.0, 100.0, L=3)
        self.assertEqual(self.e.s["armed"], {})
        self.assertEqual(self.b.calls, [])
        self.assertIn("was not placed", self.n.msgs[-1])

    def test_fixed_size_when_risk_percent_is_off(self):
        self.bar(10, 0, 126.0, 100.0, L=3)
        self.assertEqual(self.armed("long")["qty"], 1)

    def test_partly_filled_entry_becomes_a_trade_of_that_size(self):
        self.cfg.risk_pct = 5.0
        self.bar(10, 0, 126.0, 100.0, L=3)
        a = self.armed("long")
        self.b.partial[a["entry_id"]] = 5
        self.b.pos = 5
        self.bar(10, 5, 130.0, 110.0)
        tr = self.e.s["trade"]
        self.assertEqual((tr["qty"], self.b.stop_qty), (5, 5))
        self.assertNotEqual(self.b.orders[a["stop_id"]]["status"], "Canceled")
        self.assertNotIn(("flatten",), self.b.calls)

    def test_late_entry_sizes_from_its_limit(self):
        self.miss_the_break()
        self.cfg.risk_pct = 5.0
        self.bar(10, 0, 126.0, 100.0, L=3)          # limit 128.75, stop 99.75: 29 points = $58 a contract
        self.assertEqual(self.armed("long")["qty"], 15)
        self.assertIn("x15", self.n.msgs[-1])

    def test_a_closed_trade_is_logged_with_its_real_numbers(self):
        self.cfg.risk_pct = 5.0
        self.b.fees = 15.04
        ev = {"ev": "bar", "sym": "MNQ1!", "tf": "5", "t": ms(10, 0), "tc": ms(10, 5), "h": 126.0, "l": 100.0, "L": 3,
              "Ln": "Failed 2-2 Reversal A+ 1H LOW RECLAIM | 3 HIGHS +FLIP"}
        self.now = ms(10, 5) / 1000 + 1
        self.e.handle(ev)
        eid = self.armed("long")["entry_id"]
        self.b.fill("long")
        self.b.fill_ms[eid] = ms(10, 6)
        self.bar(10, 5, 130.0, 110.0)
        self.bar(10, 10, 140.0, 125.0)
        tr = self.e.s["trade"]
        self.b.orders[tr["stop_id"]]["px"] = 124.75
        self.b.stop_out(tr["stop_id"])
        self.b.fill_ms[tr["stop_id"]] = ms(10, 16)
        self.bar(10, 15, 128.0, 120.0)
        t = self.j.trades[-1]
        self.assertEqual(t["id"], f"bot-{eid}")
        self.assertEqual((t["direction"], t["signal"], t["grade"], t["formation"]), ("Long", "f22", "A+", "Reclaim + Flip"))
        self.assertEqual((t["entry"], t["stop"], t["exit"], t["best"], t["qty"]), ("126.25", "99.75", "124.75", "140.0", 16))
        self.assertEqual((t["pts"], t["gross"], t["fees"], t["pnl"], t["riskUsd"]), (-1.5, -48.0, 30.08, -78.08, 848.0))
        self.assertEqual((t["holdMins"], t["realizedR"], t["maxR"]), (10.0, -0.057, 0.519))

    def test_a_trade_stopped_in_its_entry_candle_is_logged(self):
        self.bar(10, 0, 126.0, 100.0, L=3)
        a = self.armed("long")
        self.b.fill("long")
        self.b.orders[a["stop_id"]]["status"] = "Filled"
        self.b.pos = 0
        self.bar(10, 5, 130.0, 95.0)
        self.assertEqual(self.j.trades[-1]["id"], f"bot-{a['entry_id']}")
        self.assertEqual(self.j.trades[-1]["exit"], "99.75")

    def test_a_flattened_trade_is_logged_at_its_closing_fill(self):
        self.bar(10, 0, 126.0, 100.0, L=3)
        self.b.fill("long")
        self.bar(10, 5, 130.0, 110.0)
        self.b.closing_fill = {"order_id": 99, "price": 128.5, "ms": ms(15, 55)}
        self.now = ms(15, 56) / 1000
        self.e.on_timer()
        self.assertEqual(self.j.trades[-1]["exit"], "128.5")
        self.assertIn("flatten time", self.j.trades[-1]["notes"])

    def test_flatten_without_a_closing_fill_warns(self):
        self.bar(10, 0, 126.0, 100.0, L=3)
        self.b.fill("long")
        self.bar(10, 5, 130.0, 110.0)
        self.now = ms(15, 56) / 1000
        self.e.on_timer()
        self.assertEqual(self.j.trades, [])
        self.assertTrue(any("not logged to the journal" in m for m in self.n.msgs))

    def test_a_journal_problem_does_not_touch_the_trading(self):
        def broken(trade):
            raise OSError("disk full")
        self.j.log = broken
        self.bar(10, 0, 126.0, 100.0, L=3)
        self.b.fill("long")
        self.bar(10, 5, 130.0, 110.0)
        self.b.stop_out(self.e.s["trade"]["stop_id"])
        self.bar(10, 10, 112.0, 105.0)
        self.assertIsNone(self.e.s["trade"])
        self.assertTrue(any("Could not log" in m for m in self.n.msgs))

    def test_rejection_after_arming_is_reported(self):
        self.bar(10, 0, 100.0, 90.0, L=3)
        self.b.orders[self.armed("long")["entry_id"]]["status"] = "Rejected"
        self.bar(10, 5, 99.0, 95.0)
        self.assertEqual(self.e.s["armed"], {})
        self.assertEqual(self.e.s["trades_today"], 0)
        self.assertIn("ended as Rejected", self.n.msgs[-1])

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
