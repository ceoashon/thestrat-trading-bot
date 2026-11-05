"""What the engine needs from a broker, and a dry-run broker that only logs."""
from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger("broker")

# Order statuses that mean the order is still on the book (or about to be). A bracket stop waits as Suspended until its
# entry fills, then it is Working.
LIVE_STATUSES = {"Working", "Suspended", "PendingNew", "PendingReplace", "PendingCancel"}


class BrokerError(Exception):
    pass


class PriceAlreadyPast(BrokerError):
    """The broker refused a stop entry because the market had already gone through its price."""


class Broker(Protocol):
    def place_entry(self, side: str, qty: int, entry_px: float, stop_px: float, limit_px: float | None = None) -> tuple[int, int]:
        """Rests a stop entry with its protective stop attached (OSO). Returns (entry order id, stop order id)."""

    def place_late_entry(self, side: str, qty: int, limit_px: float, stop_px: float) -> tuple[int, int] | None:
        """One try to enter at limit_px or better, with the protective stop attached. The whole qty fills at once or
        nothing does. Returns (entry order id, stop order id), or None when nothing filled."""

    def order_status(self, order_id: int) -> str: ...

    def cancel(self, order_id: int) -> bool:
        """True when the cancel was accepted (False when the order already filled or is gone)."""

    def modify_stop(self, order_id: int, qty: int, stop_px: float) -> bool: ...

    def net_position(self) -> int: ...

    def cancel_all(self) -> None: ...

    def flatten(self) -> None: ...

    def realized_pnl_since(self, since_ms: int) -> float: ...

    def fill_price(self, order_id: int) -> float | None:
        """The average fill price of an order, or None when unknown."""

    def filled_qty(self, order_id: int) -> int:
        """How many contracts of an order have filled."""

    def account_balance(self) -> float:
        """The account's balance in dollars. Raises BrokerError when it cannot be read."""

    def fill_time(self, order_id: int) -> int | None:
        """When an order last filled, in milliseconds, or None when unknown."""

    def order_fees(self, order_id: int) -> float:
        """The fees charged on an order's fills, in dollars."""

    def last_fill(self) -> dict | None:
        """The newest fill on this contract, as {"order_id", "price", "ms"}, or None."""


class DryRunBroker:
    """Logs every order instead of sending it. Entries never fill, so the bot arms, cancels and re-arms."""

    def __init__(self) -> None:
        self.next_id = 1
        self.status: dict[int, str] = {}

    def place_entry(self, side, qty, entry_px, stop_px, limit_px=None):
        eid, sid = self.next_id, self.next_id + 1
        self.next_id += 2
        self.status[eid] = "Working"
        self.status[sid] = "Suspended"
        log.info("[DRY RUN] %s %d %s %s%s, protective stop %s (ids %d/%d)", "BUY" if side == "long" else "SELL",
                 qty, "stop-limit" if limit_px else "stop", entry_px, f" limit {limit_px}" if limit_px else "", stop_px, eid, sid)
        return eid, sid

    def place_late_entry(self, side, qty, limit_px, stop_px):
        log.info("[DRY RUN] late entry %s %d limit %s, protective stop %s: not filled", "BUY" if side == "long" else "SELL",
                 qty, limit_px, stop_px)
        return None

    def order_status(self, order_id):
        return self.status.get(order_id, "Unknown")

    def cancel(self, order_id):
        if self.status.get(order_id) in LIVE_STATUSES:
            self.status[order_id] = "Canceled"
            log.info("[DRY RUN] cancel order %d", order_id)
            return True
        return False

    def modify_stop(self, order_id, qty, stop_px):
        log.info("[DRY RUN] move stop %d to %s", order_id, stop_px)
        return True

    def net_position(self):
        return 0

    def cancel_all(self):
        for i, s in self.status.items():
            if s in LIVE_STATUSES:
                self.status[i] = "Canceled"

    def flatten(self):
        log.info("[DRY RUN] flatten")

    def realized_pnl_since(self, since_ms):
        return 0.0

    def fill_price(self, order_id):
        return None

    def filled_qty(self, order_id):
        return 0

    def account_balance(self):
        return 50000.0

    def fill_time(self, order_id):
        return None

    def order_fees(self, order_id):
        return 0.0

    def last_fill(self):
        return None
