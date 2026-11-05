"""The journal's trade record and the waiting file:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from bot.config import Config  # noqa: E402
from bot.journal import JournalLogger, build_trade, parse_signal  # noqa: E402


class ParseSignalTest(unittest.TestCase):
    def test_failed_2_reclaim_with_flip(self):
        p = parse_signal("Failed 2-2 Reversal A+ 1H OUTSIDE BAR LOW RECLAIM | 9 HIGHS +FLIP")
        self.assertEqual(p, {"signal": "f22", "grade": "A+", "formation": "Reclaim + Flip", "ibCount": ""})

    def test_expansion_without_flip(self):
        p = parse_signal("Failed 1-2-2 Reversal A+ EXPANSION")
        self.assertEqual((p["signal"], p["formation"]), ("f122", "Expansion"))

    def test_similar_names_do_not_mix(self):
        self.assertEqual(parse_signal("3-2 Reversal A+ EXPANSION")["signal"], "r32")
        self.assertEqual(parse_signal("3-2-2 Reversal A+ EXPANSION")["signal"], "r322")
        self.assertEqual(parse_signal("1-3-2 Reversal B")["signal"], "n32r")
        self.assertEqual(parse_signal("2-2 Reversal (Hammer / Shooter) A+ EXPANSION")["signal"], "r22")

    def test_inside_bar_setups_carry_their_count(self):
        self.assertEqual(parse_signal("3-1-2 Continuation A+ EXPANSION")["ibCount"], "1")
        self.assertEqual(parse_signal("Double Inside Reversal B")["ibCount"], "2")

    def test_grade_b(self):
        self.assertEqual(parse_signal("Failed 2-2 Reversal B")["grade"], "B")

    def test_a_reversal_tag_leaves_the_formation_blank(self):
        self.assertEqual(parse_signal("2-2 Reversal (Hammer / Shooter) A+ BOTTOM REVERSAL | 13 HIGHS")["formation"], "")


class BuildTradeTest(unittest.TestCase):
    ENTRY_MS = 1793840100000  # 2026-11-04 19:55:00 ET

    def trade(self, timeframe="1", **rec):
        base = {"entry_id": 7, "entry": 28521.75, "fill": 28521.5, "stop": 28530.0, "first_stop": 28547.75, "qty": 16,
                "sig": "Failed 1-2-2 Reversal A+ EXPANSION", "best": 28465.25}
        base.update(rec)
        cfg = Config(secret="x", timeframe=timeframe)
        return build_trade(cfg, base, "short", 28480.0, self.ENTRY_MS, self.ENTRY_MS + 300000, 30.08, "stop hit")

    def test_a_short_trade(self):
        t = self.trade()
        self.assertEqual((t["date"], t["time"], t["session"], t["direction"], t["entryTF"]), ("2026-11-04", "19:55", "Asia", "Short", "1m"))
        self.assertEqual((t["stopPts"], t["pts"], t["realizedR"], t["maxR"]), (26.25, 41.5, 1.581, 2.143))
        self.assertEqual((t["gross"], t["fees"], t["pnl"], t["riskUsd"]), (1328.0, 30.08, 1297.92, 840.0))
        self.assertEqual(t["holdMins"], 5.0)

    def test_an_nq_trade_uses_twenty_dollars_a_point(self):
        rec = {"entry_id": 7, "entry": 28521.75, "fill": 28521.5, "stop": 28530.0, "first_stop": 28547.75, "qty": 3,
               "sig": "Failed 1-2-2 Reversal A+ EXPANSION", "symbol": "NQZ6"}
        cfg = Config(secret="x", timeframe="1", big_symbol="NQZ6")
        t = build_trade(cfg, rec, "short", 28480.0, self.ENTRY_MS, self.ENTRY_MS + 60000, 10.0, "stop hit")
        self.assertEqual((t["symbol"], t["qty"], t["gross"], t["pnl"], t["riskUsd"]), ("NQZ6", 3, 2490.0, 2480.0, 1575.0))

    def test_a_plus_lists_the_whole_hierarchy(self):
        self.assertEqual(self.trade("1")["htfAligned"], ["5m", "15m", "30m", "1h"])
        self.assertEqual(self.trade("5")["htfAligned"], ["15m", "30m", "1h"])
        self.assertEqual(self.trade("15")["htfAligned"], ["30m", "1h", "4h", "D"])

    def test_grade_b_claims_no_alignment(self):
        self.assertEqual(self.trade(sig="Failed 1-2-2 Reversal B")["htfAligned"], [])

    def test_late_entry_is_noted(self):
        self.assertIn("Late entry", self.trade(late=True)["notes"])

    def test_no_best_price_stays_blank(self):
        t = self.trade(best=None)
        self.assertEqual((t["best"], t["maxR"]), ("", None))

    def test_the_record_is_plain_json(self):
        json.dumps(self.trade())


def answer(status):
    r = mock.Mock(status_code=status, text="x")
    return r


class SpoolTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.n = mock.Mock()
        self.spool = os.path.join(self.dir.name, "q.jsonl")
        self.j = JournalLogger("https://x.supabase.co", "key", "token", self.spool, self.n, start=False)
        self.j.http = mock.Mock()

    def waiting(self):
        return self.j._lines()

    def test_logged_trades_leave_the_file_in_order(self):
        self.j.http.post.return_value = answer(200)
        self.j.log({"id": "bot-1"})
        self.j.log({"id": "bot-2"})
        self.j.flush()
        self.assertEqual(self.waiting(), [])
        sent = [c.kwargs["json"]["p_trade"]["id"] for c in self.j.http.post.call_args_list]
        self.assertEqual(sent, ["bot-1", "bot-2"])
        self.assertEqual(self.j.http.post.call_args.kwargs["json"]["p_token"], "token")

    def test_an_outage_keeps_the_trades_and_warns_once(self):
        self.j.http.post.side_effect = requests.ConnectionError("down")
        self.j.log({"id": "bot-1"})
        self.j.flush()
        self.j.flush()
        self.assertEqual(len(self.waiting()), 1)
        self.assertEqual(self.n.send.call_count, 1)
        self.j.http.post.side_effect = None
        self.j.http.post.return_value = answer(200)
        self.j.flush()
        self.assertEqual(self.waiting(), [])
        self.assertIn("back", self.n.send.call_args.args[0])

    def test_a_server_error_is_retried(self):
        self.j.http.post.return_value = answer(503)
        self.j.log({"id": "bot-1"})
        self.j.flush()
        self.assertEqual(len(self.waiting()), 1)

    def test_a_refused_trade_is_set_aside_and_the_rest_go_on(self):
        self.j.http.post.side_effect = [answer(400), answer(200)]
        self.j.log({"id": "bot-bad"})
        self.j.log({"id": "bot-2"})
        self.j.flush()
        self.assertEqual(self.waiting(), [])
        with open(self.spool + ".rejected", encoding="utf-8") as f:
            self.assertIn("bot-bad", f.read())
        self.assertIn("refused", self.n.send.call_args.args[0])

    def test_waiting_trades_survive_a_restart(self):
        self.j.http.post.side_effect = requests.ConnectionError("down")
        self.j.log({"id": "bot-1"})
        self.j.flush()
        again = JournalLogger("https://x.supabase.co", "key", "token", self.spool, self.n, start=False)
        self.assertEqual(len(again._lines()), 1)


if __name__ == "__main__":
    unittest.main()
