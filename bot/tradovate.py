"""Tradovate REST client (demo or live). Only REST is used: the bot checks orders and the position when an alert arrives,
and the stop entries and stops rest at the exchange, so no market data subscription is needed."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime

import requests

from .broker import LIVE_STATUSES, BrokerError, PriceAlreadyPast
from .config import Config

log = logging.getLogger("tradovate")

URLS = {"demo": "https://demo.tradovateapi.com/v1", "live": "https://live.tradovateapi.com/v1"}
# Tradovate accepts an order first and can reject it a moment later, so place_entry waits this long and looks again.
REJECT_CHECK_S = 0.5
# How long place_late_entry waits for its order to fill or be canceled.
LATE_POLL_S = 0.25
LATE_POLLS = 8


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class TradovateBroker:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.base = URLS[cfg.env]
        self.http = requests.Session()
        # A stable device id, so Tradovate sees the same device every time the bot logs in.
        self.device_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "strat-bot-" + cfg.username))
        self.token: str | None = None
        self.expires = 0.0
        self.account_id: int | None = None
        self.account_spec = ""
        self.contract_id: int | None = None
        self.contract_values: dict[int, float] = {}

    # ---------------------------------------------------------------- session
    def connect(self) -> None:
        self._login()
        accounts = self._call("GET", "/account/list") or []
        if not accounts:
            raise BrokerError("no accounts on this login")
        acct = next((a for a in accounts if a.get("name") == self.cfg.account_name), None) if self.cfg.account_name else accounts[0]
        if acct is None:
            raise BrokerError(f"account {self.cfg.account_name!r} not found, these exist: {[a.get('name') for a in accounts]}")
        self.account_id, self.account_spec = acct["id"], acct["name"]
        c = self._call("GET", "/contract/find", name=self.cfg.symbol)
        if not c or "id" not in c:
            raise BrokerError(f"contract {self.cfg.symbol} not found (check the month code, for example MNQZ6)")
        self.contract_id = c["id"]
        self.contract_values = {c["id"]: self.cfg.point_value}
        if self.cfg.big_symbol:
            big = self._call("GET", "/contract/find", name=self.cfg.big_symbol)
            if not big or "id" not in big:
                raise BrokerError(f"contract {self.cfg.big_symbol} not found (check the month code, for example NQZ6)")
            self.contract_values[big["id"]] = self.cfg.value_of(self.cfg.big_symbol)
        log.info("connected to Tradovate %s, account %s (%s), contract %s (%s)", self.cfg.env, self.account_spec,
                 self.account_id, self.cfg.symbol, self.contract_id)

    def _login(self) -> None:
        body = {"name": self.cfg.username, "password": self.cfg.password, "appId": self.cfg.app_id,
                "appVersion": self.cfg.app_version, "cid": self.cfg.cid, "sec": self.cfg.sec, "deviceId": self.device_id}
        for _ in range(6):
            r = self.http.post(self.base + "/auth/accesstokenrequest", json=body, timeout=20)
            d = r.json() if r.text else {}
            if d.get("p-ticket"):
                if d.get("p-captcha"):
                    raise BrokerError("Tradovate asks for a captcha on this login. Log in once on the Tradovate website "
                                      "from this computer, wait about an hour, then start the bot again.")
                wait = int(d.get("p-time", 5))
                log.warning("Tradovate asked the bot to wait %ss before logging in again", wait)
                time.sleep(wait)
                body["p-ticket"] = d["p-ticket"]
                continue
            if "accessToken" not in d:
                raise BrokerError("Tradovate login failed: " + str(d.get("errorText") or d or r.status_code))
            self.token, self.expires = d["accessToken"], _ts(d["expirationTime"])
            log.info("logged in to Tradovate, the token is good until %s", d["expirationTime"])
            return
        raise BrokerError("Tradovate login: too many waits")

    def _ensure_token(self) -> None:
        if self.token and self.expires - time.time() > 600:
            return
        if self.token:
            try:
                r = self.http.get(self.base + "/auth/renewaccesstoken", headers=self._headers(), timeout=20)
                d = r.json() if r.text else {}
                if "accessToken" in d:
                    self.token, self.expires = d["accessToken"], _ts(d["expirationTime"])
                    log.info("renewed the Tradovate token")
                    return
            except (requests.RequestException, ValueError) as e:
                log.warning("token renewal failed (%s), logging in again", e)
        self._login()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def _call(self, method: str, path: str, body: dict | None = None, retry: bool = True, **params):
        """One REST call. retry=False for calls that place orders: a timeout there may still have placed the order,
        so sending it again could double it."""
        self._ensure_token()
        tries = 3 if retry else 1
        for attempt in range(tries):
            try:
                r = self.http.request(method, self.base + path, headers=self._headers(), json=body,
                                      params=params or None, timeout=15)
            except requests.RequestException as e:
                if attempt == tries - 1:
                    raise BrokerError(f"{path}: {e}") from e
                time.sleep(1 + attempt)
                continue
            if r.status_code == 401 and attempt < tries - 1:
                self._login()
                continue
            if r.status_code == 429 and attempt < tries - 1:
                time.sleep(2 + 2 * attempt)
                continue
            if r.status_code >= 400:
                raise BrokerError(f"{path}: HTTP {r.status_code} {r.text[:300]}")
            return r.json() if r.text else None
        raise BrokerError(f"{path}: no answer")

    # ---------------------------------------------------------------- orders
    def place_entry(self, side, qty, entry_px, stop_px, limit_px=None, symbol=None):
        buy = side == "long"
        body = {
            "accountSpec": self.account_spec, "accountId": self.account_id, "action": "Buy" if buy else "Sell",
            "symbol": symbol or self.cfg.symbol, "orderQty": qty, "orderType": "StopLimit" if limit_px else "Stop",
            "stopPrice": entry_px, "timeInForce": "Day", "isAutomated": True,
            "bracket1": {"action": "Sell" if buy else "Buy", "orderType": "Stop", "stopPrice": stop_px, "timeInForce": "GTC"},
        }
        if limit_px:
            body["price"] = limit_px
        d = self._call("POST", "/order/placeoso", body, retry=False) or {}
        if d.get("failureReason") or "orderId" not in d:
            raise BrokerError(f"entry rejected: {d.get('failureReason')} {d.get('failureText', '')}".strip())
        if not d.get("oso1Id"):
            # never leave an entry without its stop
            self.cancel(d["orderId"])
            raise BrokerError("the entry was placed without its stop, so it was canceled")
        self._raise_if_rejected(d["orderId"], d["oso1Id"])
        return d["orderId"], d["oso1Id"]

    def _raise_if_rejected(self, order_id, stop_id):
        time.sleep(REJECT_CHECK_S)
        try:
            status = self.order_status(order_id)
        except BrokerError as e:
            # the order may be live, so it must stay tracked
            log.warning("could not check order %s after placing it: %s", order_id, e)
            return
        if status != "Rejected":
            return
        self.cancel(stop_id)
        reason = self._reject_reason(order_id)
        raise (PriceAlreadyPast if "InvalidPrice" in reason else BrokerError)(f"rejected by Tradovate. {reason}")

    def place_late_entry(self, side, qty, limit_px, stop_px, symbol=None):
        buy = side == "long"
        body = {
            "accountSpec": self.account_spec, "accountId": self.account_id, "action": "Buy" if buy else "Sell",
            "symbol": symbol or self.cfg.symbol, "orderQty": qty, "orderType": "Limit", "price": limit_px, "timeInForce": "FOK",
            "isAutomated": True,
            "bracket1": {"action": "Sell" if buy else "Buy", "orderType": "Stop", "stopPrice": stop_px, "timeInForce": "GTC"},
        }
        d = self._call("POST", "/order/placeoso", body, retry=False) or {}
        if d.get("failureReason") or "orderId" not in d:
            raise BrokerError(f"late entry rejected: {d.get('failureReason')} {d.get('failureText', '')}".strip())
        order_id, stop_id = d["orderId"], d.get("oso1Id")
        status = self._settled_status(order_id)
        if status in LIVE_STATUSES:
            # an FOK order should never stay on the book, so make sure it does not
            if self.cancel(order_id):
                status = "Canceled"
            else:
                status = self._settled_status(order_id)
        if status == "Unknown":
            # it may have filled, so the next candle must still check it
            log.warning("could not read late entry %s, the next candle checks it", order_id)
            if stop_id:
                return order_id, stop_id
            raise BrokerError(f"could not read late entry {order_id}, and it has no stop. Check Tradovate")
        if status != "Filled":
            if stop_id:
                self.cancel(stop_id)
            if status == "Rejected":
                raise BrokerError(f"late entry rejected by Tradovate. {self._reject_reason(order_id)}")
            return None
        # filled: never stay in a position without its stop
        time.sleep(REJECT_CHECK_S)
        stop_status = self.order_status(stop_id) if stop_id else "missing"
        if stop_status not in LIVE_STATUSES:
            self.flatten()
            raise BrokerError(f"the late entry filled but its stop was {stop_status}, so the position was closed")
        return order_id, stop_id

    def _settled_status(self, order_id):
        status = "Unknown"
        for _ in range(LATE_POLLS):
            try:
                status = self.order_status(order_id)
            except BrokerError as e:
                log.warning("could not read order %s: %s", order_id, e)
                status = "Unknown"
            if status not in LIVE_STATUSES and status != "Unknown":
                break
            time.sleep(LATE_POLL_S)
        return status

    def _reject_reason(self, order_id):
        try:
            rows = self._call("GET", "/commandReport/deps", masterid=order_id) or []
        except BrokerError as e:
            log.warning("could not read why order %s was rejected: %s", order_id, e)
            return "The reason could not be read"
        for r in rows:
            if r.get("rejectReason"):
                hint = "The price had probably already passed this level. " if r["rejectReason"] == "InvalidPrice" else ""
                return f"{hint}Tradovate says: {r['rejectReason']}, {r.get('text', '')}".rstrip(", ")
        return "No reason was given"

    def order_status(self, order_id):
        d = self._call("GET", "/order/item", id=order_id) or {}
        return d.get("ordStatus", "Unknown")

    def cancel(self, order_id):
        try:
            d = self._call("POST", "/order/cancelorder", {"orderId": order_id, "isAutomated": True}) or {}
        except BrokerError as e:
            log.warning("cancel %s failed: %s", order_id, e)
            return False
        return not d.get("failureReason")

    def modify_stop(self, order_id, qty, stop_px):
        try:
            d = self._call("POST", "/order/modifyorder", {"orderId": order_id, "orderQty": qty, "orderType": "Stop",
                                                          "stopPrice": stop_px, "timeInForce": "GTC", "isAutomated": True}) or {}
        except BrokerError as e:
            log.warning("modify %s failed: %s", order_id, e)
            return False
        if d.get("failureReason"):
            log.warning("modify %s rejected: %s %s", order_id, d.get("failureReason"), d.get("failureText", ""))
            return False
        return True

    def _positions(self):
        rows = self._call("GET", "/position/list") or []
        return [p for p in rows if p.get("accountId") == self.account_id and p.get("contractId") in self.contract_values]

    def net_position(self):
        # the bot holds one trade at a time, so only one of its contracts is ever open
        return int(sum(p.get("netPos", 0) for p in self._positions()))

    def cancel_all(self):
        rows = self._call("GET", "/order/list") or []
        for o in rows:
            if o.get("accountId") == self.account_id and o.get("contractId") in self.contract_values and o.get("ordStatus") in LIVE_STATUSES:
                self.cancel(o["id"])

    def flatten(self):
        for p in self._positions():
            if not p.get("netPos"):
                continue
            d = self._call("POST", "/order/liquidateposition", {"accountId": self.account_id, "contractId": p["contractId"],
                                                                  "admin": False}, retry=False) or {}
            if d.get("failureReason"):
                raise BrokerError(f"flatten rejected: {d.get('failureReason')} {d.get('failureText', '')}")

    def realized_pnl_since(self, since_ms):
        """Sells minus buys of the bot's contracts since since_ms, in dollars. Exact while the bot is flat (it only asks then)."""
        rows = self._call("GET", "/fill/list") or []
        cash = 0.0
        for f in rows:
            value = self.contract_values.get(f.get("contractId"))
            if value is None or not f.get("active", True):
                continue
            if _ts(f["timestamp"]) * 1000 < since_ms:
                continue
            cash += (1 if f.get("action") == "Sell" else -1) * f["price"] * f["qty"] * value
        return cash

    def filled_qty(self, order_id):
        rows = self._call("GET", "/fill/deps", masterid=order_id) or []
        return sum(f["qty"] for f in rows if f.get("active", True))

    def fill_time(self, order_id):
        rows = [f for f in (self._call("GET", "/fill/deps", masterid=order_id) or []) if f.get("active", True)]
        return int(max(_ts(f["timestamp"]) for f in rows) * 1000) if rows else None

    def order_fees(self, order_id):
        total = 0.0
        for f in self._call("GET", "/fill/deps", masterid=order_id) or []:
            if f.get("active", True):
                d = self._call("GET", "/fillFee/item", id=f["id"]) or {}
                total += sum(float(v) for k, v in d.items() if k.endswith(("Fee", "commission")) and isinstance(v, (int, float)))
        return total

    def last_fill(self):
        rows = [f for f in (self._call("GET", "/fill/list") or []) if f.get("contractId") in self.contract_values and f.get("active", True)]
        if not rows:
            return None
        f = max(rows, key=lambda x: _ts(x["timestamp"]))
        return {"order_id": f["orderId"], "price": f["price"], "ms": int(_ts(f["timestamp"]) * 1000)}

    def account_balance(self):
        d = self._call("POST", "/cashBalance/getcashbalancesnapshot", {"accountId": self.account_id}) or {}
        if d.get("netLiq") is None:
            raise BrokerError(f"could not read the account balance: {d.get('errorText', 'no balance in the answer')}")
        return float(d["netLiq"])

    def cash_snapshot(self):
        d = self._call("POST", "/cashBalance/getcashbalancesnapshot", {"accountId": self.account_id}) or {}
        if d.get("netLiq") is None:
            raise BrokerError(f"could not read the account balance: {d.get('errorText', 'no balance in the answer')}")
        return d

    def cash_log(self):
        return self._call("GET", "/cashBalanceLog/deps", masterid=self.account_id) or []

    def fill_price(self, order_id):
        try:
            rows = self._call("GET", "/fill/deps", masterid=order_id) or []
        except BrokerError as e:
            log.warning("could not read the fills of order %s: %s", order_id, e)
            return None
        qty = sum(f["qty"] for f in rows if f.get("active", True))
        return sum(f["price"] * f["qty"] for f in rows if f.get("active", True)) / qty if qty else None
