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


class Broker(Protocol):
    def place_entry(self, side: str, qty: int, entry_px: float, stop_px: float, limit_px: float | None = None) -> tuple[int, int]:
        """Rests a stop entry with its protective stop attached (OSO). Returns (entry order id, stop order id)."""

    def order_status(self, order_id: int) -> str: ...

    def cancel(self, order_id: int) -> bool:
        """True when the cancel was accepted (False when the order already filled or is gone)."""

    def modify_stop(self, order_id: int, qty: int, stop_px: float) -> bool: ...

    def net_position(self) -> int: ...

    def cancel_all(self) -> None: ...

    def flatten(self) -> None: ...

    def realized_pnl_since(self, since_ms: int) -> float: ...


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
