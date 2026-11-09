import json
import os
import sys
import tempfile
import unittest
from datetime import time as dtime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bot.broker import PriceAlreadyPast, BrokerError  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.engine import Engine  # noqa: E402
from bot.tradelocker import BASE, TradeLockerBroker  # noqa: E402

OCOLS = ["id", "tradableInstrumentId", "routeId", "qty", "side", "type", "status", "filledQty", "avgPrice", "price", "stopPrice", "validity",
         "expireDate", "createdDate", "lastModified", "isOpen", "positionId", "stopLoss", "stopLossType", "takeProfit", "takeProfitType", "strategyId"]
PCOLS = ["id", "tradableInstrumentId", "routeId", "side", "qty", "avgPrice", "stopLossId", "takeProfitId", "openDate", "unrealizedPl", "strategyId"]
SCOLS = ["balance", "projectedBalance", "openNetPnL", "todayNet"]
INST = 13645


class Resp:
    def __init__(self, code, data):
        self.status_code, self._d = code, data
        self.text = json.dumps(data)

    def json(self):
        return self._d


class FakeTL:
    """Enough of TradeLocker's REST API to trade: orders become positions with their stop loss attached."""

    def __init__(self):
        self.headers = {}
        self.orders, self.history, self.positions = {}, {}, {}
        self.next = 500
        self.bid, self.ask = 26987.0, 26988.0
        self.balance = 5000.0
        self.calls = []
        self.expire_once = False
        self.busy = 0                                  # this many "too many requests" replies first
        self.logins = 0
        self.t = 1_000

    def _o(self, **kw):
        row = {c: None for c in OCOLS}
        row.update(tradableInstrumentId=INST, lastModified=self.t, **kw)
        return row

    def fill(self, eid, px):
        o = self.orders.pop(eid)
        pid = self.next = self.next + 1
        slid = self.next = self.next + 1
        o.update(status="Filled", filledQty=o["qty"], avgPrice=px, positionId=pid, lastModified=self.t)
        self.history[eid] = o
        self.positions[pid] = {"id": pid, "tradableInstrumentId": INST, "routeId": 1, "side": o["side"], "qty": o["qty"], "avgPrice": px,
                               "stopLossId": slid, "takeProfitId": 0, "openDate": self.t, "unrealizedPl": 0, "strategyId": None}
        self.sl = {pid: (slid, o["stopLoss"])}
        return pid

    def stop_hit(self, pid, px):
        p = self.positions.pop(pid)
        slid, _ = self.sl[pid]
        self.t += 1
        self.history[slid] = self._o(id=slid, qty=p["qty"], side="sell" if p["side"] == "buy" else "buy", type="stop", status="Filled",
                                     filledQty=p["qty"], avgPrice=px, positionId=pid)

    def post(self, url, json=None, timeout=None):
        self.logins += 1
        return Resp(200, {"accessToken": f"tok{self.logins}", "refreshToken": "r"})

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        path = url[len(BASE):]
        self.calls.append((method, path, json))
        if self.expire_once:
            self.expire_once = False
            return Resp(401, {"s": "error"})
        if self.busy:
            self.busy -= 1
            return Resp(429, {"s": "error"})
        if path == "/auth/jwt/all-accounts":
            return Resp(200, {"accounts": [{"id": "2328686", "accNum": "1"}, {"id": "2604648", "accNum": "3"}]})
        if path == "/trade/config":
            cfg = {"ordersConfig": OCOLS, "ordersHistoryConfig": OCOLS, "positionsConfig": PCOLS, "accountDetailsConfig": SCOLS}
            return Resp(200, {"d": {k: {"columns": [{"id": c} for c in v]} for k, v in cfg.items()}})
        if path.endswith("/instruments"):
            return Resp(200, {"d": {"instruments": [{"name": "NAS100.R", "tradableInstrumentId": INST,
                                                     "routes": [{"type": "TRADE", "id": 1482858}, {"type": "INFO", "id": 1250823}]}]}})
        if path == "/trade/quotes":
            return Resp(200, {"s": "ok", "d": {"ap": self.ask, "bp": self.bid}})
        if path.endswith("/state"):
            return Resp(200, {"d": {"accountDetailsData": [self.balance, self.balance, 0.0, 0.0]}})
        if path.endswith("/orders") and method == "POST":
            oid = self.next = self.next + 1
            j = json
            row = self._o(id=oid, qty=j["qty"], side=j["side"], type=j["type"], status="New", filledQty=0, stopPrice=j["stopPrice"],
                          validity=j["validity"], isOpen=True, stopLoss=j["stopLoss"], stopLossType=j["stopLossType"])
            self.orders[oid] = row
            if j["type"] == "market":
                self.fill(oid, self.ask if j["side"] == "buy" else self.bid)
            return Resp(200, {"s": "ok", "d": {"orderId": str(oid)}})
        if path.endswith("/orders") and method == "GET":
            return Resp(200, {"d": {"orders": [[o[c] for c in OCOLS] for o in self.orders.values()]}})
        if path.endswith("/ordersHistory"):
            return Resp(200, {"d": {"ordersHistory": [[o[c] for c in OCOLS] for o in self.history.values()]}})
        if path.endswith("/positions") and method == "GET":
            return Resp(200, {"d": {"positions": [[p[c] for c in PCOLS] for p in self.positions.values()]}})
        if path.startswith("/trade/orders/") and method == "DELETE":
            oid = int(path.split("/")[-1])
            if oid not in self.orders:
                return Resp(400, {"s": "error", "errmsg": "no such order"})
            o = self.orders.pop(oid)
            o["status"] = "Cancelled"
            self.history[oid] = o
            return Resp(200, {"s": "ok"})
        if path.startswith("/trade/positions/") and method == "PATCH":
            pid = int(path.split("/")[-1])
            slid, _ = self.sl[pid]
            self.sl[pid] = (slid, json["stopLoss"])
            return Resp(200, {"s": "ok"})
        if path.endswith("/positions") and method == "DELETE":
            for pid in list(self.positions):
                self.stop_hit(pid, self.bid)
            return Resp(200, {"s": "ok"})
        if path.endswith("/orders") and method == "DELETE":
            for oid in list(self.orders):
                self.history[oid] = dict(self.orders.pop(oid), status="Cancelled")
            return Resp(200, {"s": "ok"})
        return Resp(404, {"s": "error", "path": path})


class Clock:
    def __init__(self):
        self.t = 100.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 2))
        self.t += s


def login_file(env="demo"):
    f = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
    f.write(f'[tradelocker]\nenv = "{env}"\nemail = "a@b.c"\npassword = "p"\nserver = "GATESFX"\n')
    f.close()
    return f.name


def cfg(**kw):
    c = Config(secret="x", broker="tradelocker", tl_login_file=login_file(), tl_account="2604648", symbol="NAS100.R", tv_tickers=["NAS100"],
               timeframe="15", tick_size=0.01, point_value=100.0, lot_step=0.01, qty=0.01, max_qty=10.0, risk_pct=2.0, entry_offset_ticks=25,
               stop_buffer_ticks=25, late_entry_ticks=250, max_gap_pts=50, start=dtime(0, 0), end=dtime(23, 59), max_trades_per_day=0)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.tl, self.clock = FakeTL(), Clock()
        self.b = TradeLockerBroker(cfg(), http=self.tl, sleep=self.clock.sleep, clock=self.clock)
        self.b.connect()

    def test_connects_to_the_named_account_and_symbol(self):
        self.assertEqual((self.b.acc_id, self.b.acc_num, self.b.inst, self.b.route_trade), ("2604648", "3", INST, 1482858))

    def test_refuses_an_unknown_account_and_a_live_login(self):
        with self.assertRaises(BrokerError):
            TradeLockerBroker(cfg(tl_account="999"), http=FakeTL()).connect()
        with self.assertRaises(BrokerError):
            TradeLockerBroker(cfg(tl_login_file=login_file("live")), http=FakeTL()).connect()

    def test_a_trade_from_entry_to_stop(self):
        eid, sid = self.b.place_entry("long", 0.03, 27000.25, 26965.0)
        body = self.tl.calls[-1][2]
        self.assertEqual((body["type"], body["stopPrice"], body["stopLoss"], body["stopLossType"], body["qty"], body["validity"]),
                         ("stop", 27000.25, 26965.0, "absolute", "0.03", "GTC"))
        self.assertEqual(sid, -eid)
        self.assertEqual((self.b.order_status(eid), self.b.order_status(sid)), ("Working", "Suspended"))
        pid = self.tl.fill(eid, 27000.5)
        self.b._changed()
        self.assertEqual((self.b.order_status(eid), self.b.order_status(sid), self.b.net_position()), ("Filled", "Working", 0.03))
        self.assertEqual(self.b.fill_price(eid), 27000.5)
        self.assertTrue(self.b.modify_stop(sid, 0.03, 26990.0))
        self.assertEqual(self.tl.sl[pid][1], 26990.0)
        self.assertFalse(self.b.cancel(sid))                 # a live position's stop is never removed
        self.tl.stop_hit(pid, 26989.75)
        self.b._changed()
        self.assertEqual((self.b.order_status(sid), self.b.net_position(), self.b.fill_price(sid)), ("Filled", 0, 26989.75))
        self.assertIsNotNone(self.b.fill_time(sid))

    def test_a_position_without_its_stop_is_not_working(self):
        eid, sid = self.b.place_entry("short", 0.02, 26970.0, 27000.0)
        pid = self.tl.fill(eid, 26969.75)
        self.tl.positions[pid]["stopLossId"] = 0
        self.b._changed()
        self.assertEqual(self.b.order_status(sid), "Missing")

    def test_cancel_before_the_fill(self):
        eid, sid = self.b.place_entry("long", 0.01, 27000.25, 26965.0)
        self.assertTrue(self.b.cancel(eid))
        self.assertTrue(self.b.cancel(sid))
        self.assertEqual(self.b.order_status(sid), "Canceled")

    def test_price_already_past_and_the_wrong_chart(self):
        with self.assertRaises(PriceAlreadyPast):
            self.b.place_entry("long", 0.01, 26987.5, 26950.0)
        with self.assertRaises(PriceAlreadyPast):
            self.b.place_entry("short", 0.01, 26987.5, 27020.0)
        with self.assertRaises(BrokerError):                 # an NQ futures level ~100 points away
            self.b.place_entry("long", 0.01, 27090.0, 27050.0)

    def test_late_entry_inside_and_past_the_cap(self):
        self.assertIsNone(self.b.place_late_entry("long", 0.01, 26987.5, 26950.0))   # ask 26988 is past the cap
        eid, sid = self.b.place_late_entry("long", 0.01, 26989.0, 26950.0)
        self.assertEqual((self.b.order_status(eid), self.b.order_status(sid)), ("Filled", "Working"))

    def test_slow_routes_wait_a_second(self):
        self.b.net_position()
        self.b._changed()
        self.b.net_position()
        self.assertTrue(any(s >= 1.0 for s in self.clock.slept))

    def test_too_many_requests_waits_longer_each_time(self):
        eid, _ = self.b.place_entry("long", 0.01, 27000.0, 26950.0)
        self.clock.slept.clear()
        self.tl.busy = 3
        self.assertTrue(self.b.cancel(eid))
        self.assertEqual([s for s in self.clock.slept if s >= 2], [2.0, 4.0, 8.0])
        self.tl.busy = 4
        with self.assertRaises(BrokerError):
            self.b.account_balance()

    def test_logs_in_again_when_the_session_ends(self):
        n = self.tl.logins
        self.tl.expire_once = True
        self.assertEqual(self.b.account_balance(), 5000.0)
        self.assertEqual(self.tl.logins, n + 1)


class EngineOnTradeLockerTest(unittest.TestCase):
    """The whole bot on the fake TradeLocker: fractional lots, the stop trailing on the position, the stop hit."""

    def test_a_cfd_trade(self):
        tl, clock = FakeTL(), Clock()
        c = cfg()
        b = TradeLockerBroker(c, http=tl, sleep=clock.sleep, clock=clock)
        b.connect()
        now = [0.0]
        e = Engine(c, b, clock=lambda: now[0])
        e.FLAT_WAIT_S = 0

        def bar(t_s, hi, lo, L=0):
            now[0] = t_s + 900 + 1
            e.handle({"ev": "bar", "sym": "NAS100", "tf": "15", "t": t_s * 1000, "tc": (t_s + 900) * 1000, "h": hi, "l": lo, "L": L, "S": 0})

        t0 = 1_794_060_000                                   # a Monday morning, New York
        bar(t0, 27000.0, 26965.0, L=4)                       # a 3-1-2 long: buy stop 0.25 above the high, stop 0.25 under the low
        a = e.s["armed"]["long"]
        # 2% of $5,000 = $100; risk 35.5 points x $100 a lot -> 0.028 lots, rounded down to 0.02
        self.assertEqual((a["entry"], a["stop"], a["qty"]), (27000.25, 26964.75, 0.02))
        pid = tl.fill(a["entry_id"], 27000.5)
        bar(t0 + 900, 27040.0, 26995.0)
        tr = e.s["trade"]
        self.assertEqual((tr["side"], tr["stop"]), ("long", 26994.75))
        self.assertEqual(tl.sl[pid][1], 26994.75)            # the stop moved on the position at TradeLocker
        tl.stop_hit(pid, 26994.5)
        b._changed()
        bar(t0 + 1800, 27010.0, 26990.0)
        self.assertIsNone(e.s["trade"])


if __name__ == "__main__":
    unittest.main()
