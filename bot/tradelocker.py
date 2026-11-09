"""TradeLocker (for example GatesFX CFDs) as the engine's broker, through TradeLocker's public REST API.

The login (email, password, server) is read from its own file, tradelocker.toml, which tools/tradelocker_login.sh writes.
Only the demo side is used: a login file for live is refused.

TradeLocker differs from Tradovate in ways the engine never sees:
- The protective stop is not its own order until the entry fills; it then belongs to the position. So place_entry returns a
  stand-in stop id, the entry's id made negative, and every call on it looks up the entry's position: Working while the
  position is open with a stop loss, Filled once it is closed, the entry's own status before it fills.
- Orders, positions and fills come back as plain lists, named by the columns in /trade/config.
- Reads of orders, history and positions are allowed once a second, so reads are spaced out and reused for a moment.
- Sizes are lots in 0.01 steps, sent as text.
"""
from __future__ import annotations

import logging
import time
import tomllib

import requests

from .broker import LIVE_STATUSES, BrokerError, PriceAlreadyPast
from .config import Config

log = logging.getLogger("tradelocker")

BASE = "https://demo.tradelocker.com/backend-api"
# how long a market order (a late entry) gets to fill before it counts as missed
LATE_WAIT_S = 3.0
# a read of these is reused this long, then refreshed; any order change refreshes them at once
CACHE_S = 1.6
SLOW = ("orders", "ordersHistory", "positions")   # routes allowed once a second
SLOW_GAP_S = 1.5
MIN_GAP_S = 0.25                                   # between any two requests: TradeLocker refuses quick bursts
BUSY_WAITS_S = (2.0, 4.0, 8.0)                     # the waits before each new try after "too many requests"


def _status(s: str | None) -> str:
    """TradeLocker's order status in the engine's words (Tradovate's)."""
    v = (s or "").lower()
    if "partial" in v:
        return "Working"
    if "fill" in v:
        return "Filled"
    if "cancel" in v:
        return "Canceled"
    if "reject" in v:
        return "Rejected"
    if "expire" in v:
        return "Expired"
    return "Working" if v else "Unknown"


def _ms(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class TradeLockerBroker:
    def __init__(self, cfg: Config, http: requests.Session | None = None, sleep=time.sleep, clock=time.monotonic) -> None:
        self.cfg = cfg
        self.http = http or requests.Session()
        self.sleep, self.clock = sleep, clock
        self.cols: dict[str, list[str]] = {}
        self.last_call: dict[str, float] = {}
        self.cache: dict[str, tuple[float, list]] = {}
        self.acc_id = self.acc_num = ""
        self.inst = self.route_trade = self.route_info = 0

    # ---------------------------------------------------------------- connection
    def _login(self) -> None:
        with open(self.cfg.tl_login_file, "rb") as f:
            c = tomllib.load(f)["tradelocker"]
        if c.get("env", "demo") != "demo":
            raise BrokerError("the TradeLocker login file is for live. The bot only trades the demo side; ask before changing that")
        r = self.http.post(f"{BASE}/auth/jwt/token", json={"email": c["email"], "password": c["password"], "server": c["server"]}, timeout=20)
        if r.status_code >= 400:
            raise BrokerError(f"TradeLocker refused the login ({r.status_code}): {r.text[:200]}")
        self.http.headers["Authorization"] = f"Bearer {r.json()['accessToken']}"

    def connect(self) -> None:
        self._login()
        accounts = self._call("GET", "/auth/jwt/all-accounts", acc=False).get("accounts", [])
        want = str(self.cfg.tl_account)
        a = next((x for x in accounts if str(x.get("id")) == want), None)
        if not want or a is None:
            raise BrokerError(f"TradeLocker account {want!r} not found, these exist: {[x.get('id') for x in accounts]}")
        self.acc_id, self.acc_num = str(a["id"]), str(a["accNum"])
        cfg = self._call("GET", "/trade/config").get("d", {})
        for k in ("ordersConfig", "ordersHistoryConfig", "positionsConfig", "accountDetailsConfig"):
            self.cols[k] = [x["id"] for x in cfg.get(k, {}).get("columns", [])]
        ins = self._call("GET", f"/trade/accounts/{self.acc_id}/instruments").get("d", {}).get("instruments", [])
        i = next((x for x in ins if x.get("name") == self.cfg.symbol), None)
        if i is None:
            raise BrokerError(f"symbol {self.cfg.symbol!r} not on TradeLocker account {self.acc_id}")
        self.inst = int(i["tradableInstrumentId"])
        routes = {x.get("type"): int(x["id"]) for x in i.get("routes", [])}
        self.route_trade, self.route_info = routes["TRADE"], routes.get("INFO", routes["TRADE"])
        log.info("connected to TradeLocker demo, account %s (accNum %s), %s (id %s)", self.acc_id, self.acc_num, self.cfg.symbol, self.inst)

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None, acc: bool = True, again: bool = True,
              busy: int = 0) -> dict:
        name = path.rstrip("/").split("/")[-1]
        gap = SLOW_GAP_S if method == "GET" and name in SLOW else MIN_GAP_S   # once a second on the slow routes
        last = max(self.last_call.get(name, -9.0) if gap == SLOW_GAP_S else -9.0, self.last_call.get("", -9.0))
        wait = gap - (self.clock() - last)
        if wait > 0:
            self.sleep(wait)
        self.last_call[""] = self.clock()
        if gap == SLOW_GAP_S:
            self.last_call[name] = self.clock()
        headers = {"accNum": self.acc_num} if acc and self.acc_num else {}
        try:
            r = self.http.request(method, BASE + path, params=params, json=body, headers=headers, timeout=20)
        except requests.RequestException as e:
            raise BrokerError(f"TradeLocker did not answer {method} {path}: {e}") from e
        if r.status_code == 401 and again:                     # the session ran out: log in again, once
            self._login()
            return self._call(method, path, params, body, acc, again=False, busy=busy)
        if r.status_code == 429 and busy < len(BUSY_WAITS_S):  # too many requests: wait longer each time, then try again
            log.warning("TradeLocker said too many requests on %s %s, trying again in %ss", method, path, BUSY_WAITS_S[busy])
            self.sleep(BUSY_WAITS_S[busy])
            return self._call(method, path, params, body, acc, again, busy + 1)
        if r.status_code >= 400:
            raise BrokerError(f"TradeLocker {method} {path} failed ({r.status_code}): {r.text[:300]}")
        try:
            return r.json() if r.text else {}
        except ValueError as e:
            raise BrokerError(f"TradeLocker sent a reply that is not JSON for {path}") from e

    # ---------------------------------------------------------------- reads
    def _rows(self, what: str) -> list[dict]:
        """Open orders, order history or positions on this symbol, as dicts, reused for CACHE_S."""
        hit = self.cache.get(what)
        if hit and self.clock() - hit[0] < CACHE_S:
            return hit[1]
        d = self._call("GET", f"/trade/accounts/{self.acc_id}/{what}", params={"tradableInstrumentId": self.inst}).get("d", {})
        names = self.cols["positionsConfig" if what == "positions" else what + "Config"]
        rows = [dict(zip(names, v)) for v in d.get(what, []) or []]
        rows = [x for x in rows if str(x.get("tradableInstrumentId")) == str(self.inst)]
        self.cache[what] = (self.clock(), rows)
        return rows

    def _changed(self) -> None:
        self.cache.clear()

    def _order(self, order_id: int) -> dict | None:
        oid = str(order_id)
        for what in ("orders", "ordersHistory"):
            row = next((x for x in self._rows(what) if str(x.get("id")) == oid), None)
            if row:
                return row
        return None

    def _position_of(self, entry_id: int) -> tuple[dict | None, dict | None]:
        """The entry order and its open position (None once the position is closed or before the entry fills)."""
        o = self._order(entry_id)
        if not o or not o.get("positionId") or _status(o.get("status")) != "Filled":
            return o, None
        pid = str(o["positionId"])
        return o, next((p for p in self._rows("positions") if str(p.get("id")) == pid), None)

    def _closing(self, entry_id: int) -> dict | None:
        """The order that closed the entry's position: its stop loss, or the bot's market close."""
        o = self._order(entry_id)
        if not o or not o.get("positionId"):
            return None
        rows = [x for x in self._rows("ordersHistory") if str(x.get("positionId")) == str(o["positionId"])
                and str(x.get("id")) != str(entry_id) and _status(x.get("status")) == "Filled"]
        return max(rows, key=lambda x: _ms(x.get("lastModified")) or 0, default=None)

    def _quote(self) -> tuple[float, float]:
        d = self._call("GET", "/trade/quotes", params={"routeId": self.route_info, "tradableInstrumentId": self.inst}).get("d", {})
        if not d.get("ap") or not d.get("bp"):
            raise BrokerError("TradeLocker gave no price for the symbol (market closed?)")
        return float(d["bp"]), float(d["ap"])

    def _state(self) -> dict:
        d = self._call("GET", f"/trade/accounts/{self.acc_id}/state").get("d", {})
        return dict(zip(self.cols["accountDetailsConfig"], d.get("accountDetailsData", [])))

    # ---------------------------------------------------------------- the engine's Broker
    def _place(self, side: str, qty: float, kind: str, stop_px: float, entry_px: float | None = None) -> int:
        body = {"price": None, "qty": f"{qty:.2f}", "routeId": self.route_trade, "side": "buy" if side == "long" else "sell",
                "validity": "IOC" if kind == "market" else "GTC", "tradableInstrumentId": str(self.inst), "type": kind,
                "stopPrice": entry_px, "stopLoss": stop_px, "stopLossType": "absolute"}
        d = self._call("POST", f"/trade/accounts/{self.acc_id}/orders", body=body)
        self._changed()
        oid = (d.get("d") or {}).get("orderId")
        if not oid:
            msg = d.get("errmsg") or d.get("message") or str(d)[:200]
            raise BrokerError(f"TradeLocker rejected the {kind} order: {msg}")
        return int(oid)

    def place_entry(self, side, qty, entry_px, stop_px, limit_px=None, symbol=None):
        bid, ask = self._quote()
        if (side == "long" and ask >= entry_px) or (side == "short" and bid <= entry_px):
            raise PriceAlreadyPast(f"the market ({bid}/{ask}) is already past the entry {entry_px}")
        far = self.cfg.max_gap_pts
        if far and abs((bid + ask) / 2 - entry_px) > far:
            raise BrokerError(f"the entry {entry_px} is {abs((bid + ask) / 2 - entry_px):.1f} points from TradeLocker's price "
                              f"{(bid + ask) / 2:.2f} (max {far}). Is the alert on the wrong chart?")
        eid = self._place(side, qty, "stop", stop_px, entry_px)
        log.info("stop entry %s %s at %s, stop loss %s: order %s", side, qty, entry_px, stop_px, eid)
        return eid, -eid

    def place_late_entry(self, side, qty, limit_px, stop_px, symbol=None):
        bid, ask = self._quote()
        if (side == "long" and ask > limit_px) or (side == "short" and bid < limit_px):
            return None                                        # already further than the cap: no trade
        eid = self._place(side, qty, "market", stop_px)
        end = self.clock() + LATE_WAIT_S
        while True:
            self._changed()
            st = self.order_status(eid)
            if st == "Filled":
                return eid, -eid
            if st not in LIVE_STATUSES or self.clock() > end:
                if st in LIVE_STATUSES:
                    self.cancel(eid)
                return None
            self.sleep(0.5)

    def order_status(self, order_id):
        if order_id < 0:                                       # the stand-in stop of an entry
            o, p = self._position_of(-order_id)
            if o is None:
                return "Unknown"
            st = _status(o.get("status"))
            if st in LIVE_STATUSES:
                return "Suspended"                             # like a bracket stop waiting for its entry
            if st != "Filled":
                return st
            if p is None:
                return "Filled"                                # the position is closed
            return "Working" if str(p.get("stopLossId") or "0") not in ("", "0", "None") else "Missing"
        o = self._order(order_id)
        return _status(o.get("status")) if o else "Unknown"

    def cancel(self, order_id):
        eid = abs(order_id)
        o = self._order(eid)
        if order_id < 0 and (o is None or _status(o.get("status")) != "Filled"):
            order_id = eid                                     # before the fill, the stop goes with its entry
        elif order_id < 0:
            return False                                       # a live position's stop is never removed
        if o is not None and _status(o.get("status")) not in LIVE_STATUSES:
            return _status(o.get("status")) == "Canceled"
        try:
            d = self._call("DELETE", f"/trade/orders/{order_id}")
        except BrokerError as e:
            log.warning("cancel %s refused: %s", order_id, e)
            return False
        finally:
            self._changed()
        return d.get("s") == "ok"

    def modify_stop(self, order_id, qty, stop_px):
        o, p = self._position_of(abs(order_id))
        try:
            if p is not None:
                d = self._call("PATCH", f"/trade/positions/{p['id']}", body={"stopLoss": stop_px})
            elif o is not None and _status(o.get("status")) in LIVE_STATUSES:
                d = self._call("PATCH", f"/trade/orders/{o['id']}", body={"stopLoss": stop_px, "stopLossType": "absolute"})
            else:
                return False
        except BrokerError as e:
            log.warning("could not move the stop to %s: %s", stop_px, e)
            return False
        finally:
            self._changed()
        return d.get("s") == "ok"

    def net_position(self):
        net = sum(float(p.get("qty") or 0) * (1 if p.get("side") == "buy" else -1) for p in self._rows("positions"))
        return round(net, 8)

    def cancel_all(self):
        try:
            self._call("DELETE", f"/trade/accounts/{self.acc_id}/orders", params={"tradableInstrumentId": self.inst})
        finally:
            self._changed()

    def flatten(self):
        if not self._rows("positions"):
            return
        try:
            self._call("DELETE", f"/trade/accounts/{self.acc_id}/positions", params={"tradableInstrumentId": self.inst})
        finally:
            self._changed()

    def realized_pnl_since(self, since_ms):
        return float(self._state().get("todayNet") or 0.0)     # the broker's own trading day

    def fill_price(self, order_id):
        o = self._closing(-order_id) if order_id < 0 else self._order(order_id)
        if not o or _status(o.get("status")) != "Filled":
            return None
        return float(o.get("avgPrice") or 0) or None

    def filled_qty(self, order_id):
        o = self._order(abs(order_id))
        return float(o.get("filledQty") or 0) if o else 0

    def account_balance(self):
        v = self._state().get("balance")
        if v is None:
            raise BrokerError("TradeLocker did not give the account balance")
        return float(v)

    def cash_snapshot(self):
        s = self._state()
        return {"netLiq": float(s.get("balance") or 0) + float(s.get("openNetPnL") or 0), "openPnL": float(s.get("openNetPnL") or 0)}

    def cash_log(self):
        return []                                              # TradeLocker has no cash log route; the P&L commands stay off

    def fill_time(self, order_id):
        o = self._closing(-order_id) if order_id < 0 else self._order(order_id)
        return _ms(o.get("lastModified")) if o and _status(o.get("status")) == "Filled" else None

    def order_fees(self, order_id):
        return 0.0                                             # not given per order; the spread is the cost on these accounts

    def last_fill(self):
        rows = [x for x in self._rows("ordersHistory") if _status(x.get("status")) == "Filled"]
        x = max(rows, key=lambda r: _ms(r.get("lastModified")) or 0, default=None)
        return {"order_id": int(x["id"]), "price": float(x.get("avgPrice") or 0), "ms": _ms(x.get("lastModified"))} if x else None
