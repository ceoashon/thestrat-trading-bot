"""The Discord P&L commands:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, time as dtime
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker import BrokerError  # noqa: E402
from bot.pnl import CACHE_S, PnlLedger, trading_day  # noqa: E402

ET = ZoneInfo("America/New_York")
RESET = dtime(18, 0)


def row(i, day, kind, delta):
    y, m, d = day
    return {"id": i, "tradeDate": {"year": y, "month": m, "day": d}, "cashChangeType": kind, "delta": delta}


class FakeBroker:
    def __init__(self, rows):
        self.rows = rows
        self.snap = {"netLiq": 15623.0, "openPnL": 0.0}
        self.reads = 0

    def cash_log(self):
        self.reads += 1
        return self.rows

    def cash_snapshot(self):
        return self.snap


class TradingDayTest(unittest.TestCase):
    def day(self, *when):
        return trading_day(datetime(*when, tzinfo=ET), RESET)

    def test_evening_counts_as_the_next_day(self):
        self.assertEqual(self.day(2026, 11, 4, 17, 59), date(2026, 11, 4))
        self.assertEqual(self.day(2026, 11, 4, 18, 0), date(2026, 11, 5))

    def test_weekend_shows_friday(self):
        self.assertEqual(self.day(2026, 11, 6, 19, 0), date(2026, 11, 6))
        self.assertEqual(self.day(2026, 11, 7, 12, 0), date(2026, 11, 6))
        self.assertEqual(self.day(2026, 11, 8, 17, 0), date(2026, 11, 6))

    def test_sunday_evening_is_monday(self):
        self.assertEqual(self.day(2026, 11, 8, 18, 0), date(2026, 11, 9))


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "pnl.jsonl")
        self.now = datetime(2026, 11, 5, 1, 0, tzinfo=ET).timestamp()
        self.b = FakeBroker([
            row(1, (2026, 10, 30), "TradePaired", 100.0),    # last month, last week
            row(2, (2026, 11, 2), "TradePaired", 200.0),     # this month, this week
            row(3, (2026, 11, 2), "Commission", -5.0),
            row(4, (2026, 11, 5), "TradePaired", -50.0),     # today
            row(5, (2026, 11, 5), "ExchangeFee", -1.5),
            row(6, (2026, 11, 5), "NewSession", 0.0),
            row(7, (2026, 11, 3), "Deposit", 9000.0),        # money moved in is not profit
        ])

    def ledger(self):
        return PnlLedger(self.b, self.path, "America/New_York", RESET, clock=lambda: self.now)

    def test_each_period(self):
        lg = self.ledger()
        self.assertIn("Today (Thu Nov 5): -$51.50**", lg.report("day"))
        self.assertIn("This week (Nov 2 to Nov 6): +$143.50**", lg.report("week"))
        self.assertIn("This month (November): +$143.50**", lg.report("month"))
        self.assertIn("Total (since Oct 30, 2026): +$243.50**", lg.report("total"))

    def test_fees_and_balance_are_shown(self):
        self.assertIn("After $1.50 in fees | Balance $15,623.00", self.ledger().report("day"))

    def test_open_trade_is_shown_but_not_counted(self):
        self.b.snap["openPnL"] = 42.0
        text = self.ledger().report("day")
        self.assertIn("-$51.50**", text)
        self.assertIn("Open trade right now: +$42.00", text)

    def test_quiet_day_shows_zero(self):
        self.b.rows = []
        self.assertIn("**Today (Thu Nov 5): $0.00**", self.ledger().report("day"))

    def test_history_survives_tradovate_trimming_it_and_a_restart(self):
        self.ledger().report("total")
        self.b.rows = [row(8, (2026, 11, 5), "TradePaired", 10.0)]
        self.assertIn("since Oct 30, 2026): +$253.50**", self.ledger().report("total"))

    def test_rows_are_only_counted_once(self):
        lg = self.ledger()
        lg.report("total")
        self.now += CACHE_S + 1
        self.assertIn("+$243.50**", lg.report("total"))

    def test_many_commands_read_tradovate_once(self):
        lg = self.ledger()
        for p in ("day", "week", "month", "total"):
            lg.report(p)
        self.assertEqual(self.b.reads, 1)

    def test_broken_last_line_is_skipped(self):
        self.ledger().report("total")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"id": 99, "da')
        self.assertIn("+$243.50**", self.ledger().report("total"))

    def test_tradovate_failure_reaches_the_caller(self):
        lg = self.ledger()
        with mock.patch.object(self.b, "cash_log", side_effect=BrokerError("down")), self.assertRaises(BrokerError):
            lg.report("day")


try:
    import discord
except ImportError:
    discord = None


class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.direct = None

    async def defer(self):
        self.deferred = True

    async def send_message(self, text, ephemeral=False):
        self.direct = (text, ephemeral)


class FakeFollowup:
    def __init__(self):
        self.sent = None

    async def send(self, text):
        self.sent = text


class FakeInteraction:
    def __init__(self, channel_id):
        self.channel_id = channel_id
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@unittest.skipIf(discord is None, "discord.py is not installed here")
class CommandsTest(unittest.TestCase):
    CHANNEL = 111

    def tree(self, ledger):
        from bot.discord_commands import build_tree
        self.guild = discord.Object(222)
        return build_tree(discord.Client(intents=discord.Intents(guilds=True)), ledger, self.guild, self.CHANNEL)

    def run_cmd(self, ledger, name, channel):
        i = FakeInteraction(channel)
        asyncio.run(self.tree(ledger).get_command(name, guild=self.guild).callback(i))
        return i

    def test_the_four_commands_exist_on_the_server(self):
        tree = self.tree(mock.Mock())
        self.assertEqual({c.name for c in tree.get_commands(guild=self.guild)}, {"day", "week", "month", "total"})

    def test_each_command_asks_for_its_own_period(self):
        for name in ("day", "week", "month", "total"):
            lg = mock.Mock()
            lg.report.return_value = "report"
            i = self.run_cmd(lg, name, self.CHANNEL)
            lg.report.assert_called_once_with(name)
            self.assertTrue(i.response.deferred)
            self.assertEqual(i.followup.sent, "report")

    def test_other_channels_get_no_numbers(self):
        lg = mock.Mock()
        i = self.run_cmd(lg, "total", 999)
        lg.report.assert_not_called()
        self.assertEqual(i.response.direct, (f"Use this in <#{self.CHANNEL}>.", True))

    def test_tradovate_failure_gets_a_plain_answer(self):
        lg = mock.Mock()
        lg.report.side_effect = BrokerError("down")
        self.assertIn("Could not reach Tradovate", self.run_cmd(lg, "day", self.CHANNEL).followup.sent)


if __name__ == "__main__":
    unittest.main()
