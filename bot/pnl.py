"""The account's profit and loss for the Discord commands, from Tradovate's own record of every cash change.

Tradovate trims its history, so each change is also saved to a file. Weeks, months and the total stay right after
Tradovate has dropped the older rows. Fees are included, deposits and withdrawals are not."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterable
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from .broker import Broker

log = logging.getLogger("pnl")

NOT_PNL = {"Deposit", "Withdrawal", "NewSession"}
TRADE = "TradePaired"
CACHE_S = 5.0  # answers within this long reuse the last read, so many commands cannot crowd Tradovate while it trades
SAVE_EVERY_S = 3600.0


def trading_day(now: datetime, reset: dtime) -> date:
    """The trading date right now. The evening session counts as the next day, and the weekend shows Friday."""
    d = now.date() + timedelta(days=1) if now.time() >= reset else now.date()
    return d - timedelta(days=max(d.weekday() - 4, 0))


def _money(v: float) -> str:
    v = round(v, 2) + 0.0  # no "-$0.00"
    return f"{'-' if v < 0 else '+' if v > 0 else ''}${abs(v):,.2f}"


class PnlLedger:
    def __init__(self, broker: Broker, path: str, timezone: str = "America/New_York", reset: dtime = dtime(18, 0), clock=time.time) -> None:
        self.b = broker
        self.path = path
        self.tz = ZoneInfo(timezone)
        self.reset = reset
        self.clock = clock
        self.lock = threading.Lock()
        self.rows: dict[int, dict] = {}
        self.snap: dict = {}
        self.read_at: float | None = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    r = self._parse(line)
                    if r:
                        self.rows[r["id"]] = r

    @staticmethod
    def _parse(line: str) -> dict | None:
        try:
            return json.loads(line)
        except ValueError:  # a half written last line after a power cut
            log.warning("skipped an unreadable line in the P&L file")
            return None

    def refresh(self) -> None:
        with self.lock:
            if self.read_at is not None and self.clock() - self.read_at < CACHE_S:
                return
            new = []
            for r in self.b.cash_log():
                if r["id"] in self.rows or r.get("cashChangeType") in NOT_PNL or not r.get("delta"):
                    continue
                td = r["tradeDate"]
                new.append({"id": r["id"], "day": f"{td['year']:04d}-{td['month']:02d}-{td['day']:02d}",
                            "kind": r["cashChangeType"], "delta": r["delta"]})
            if new:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write("".join(json.dumps(r) + "\n" for r in new))
                self.rows.update((r["id"], r) for r in new)
            self.snap = self.b.cash_snapshot()
            self.read_at = self.clock()

    def keep_saving(self) -> None:
        """Runs in its own thread: saves the history even if nobody asks for a while."""
        while True:
            try:
                self.refresh()
            except Exception as e:
                log.warning("could not read the cash history: %s", e)
            time.sleep(SAVE_EVERY_S)

    def _span(self, period: str, today: date) -> tuple[str, str | None, str | None]:
        """The label and the first and last day of a period. Weeks run Monday to Friday, so Sunday evening is in the new week."""
        if period == "day":
            return f"Today ({today:%a %b} {today.day})", today.isoformat(), today.isoformat()
        if period == "week":
            monday = today - timedelta(days=today.weekday())
            friday = monday + timedelta(days=4)
            return f"This week ({monday:%b} {monday.day} to {friday:%b} {friday.day})", monday.isoformat(), None
        if period == "month":
            return f"This month ({today:%B})", today.replace(day=1).isoformat(), None
        return "Total", None, None

    @staticmethod
    def _sums(rows: Iterable[dict]) -> tuple[float, float]:
        net = fees = 0.0
        for r in rows:
            net += r["delta"]
            if r["kind"] != TRADE:
                fees -= r["delta"]
        return net, fees

    def report(self, period: str) -> str:
        self.refresh()
        today = trading_day(datetime.fromtimestamp(self.clock(), self.tz), self.reset)
        label, first, last = self._span(period, today)
        with self.lock:
            snap = self.snap
            rows = [r for r in self.rows.values() if (first is None or first <= r["day"]) and (last is None or r["day"] <= last)]
            days = [r["day"] for r in self.rows.values()]
        if first is None and days:
            start = date.fromisoformat(min(days))
            label = f"Total (since {start:%b} {start.day}, {start.year})"
        net, fees = self._sums(rows)
        lines = [f"**{label}: {_money(net)}**", f"After ${fees:,.2f} in fees | Balance ${snap['netLiq']:,.2f}"]
        if snap.get("openPnL"):
            lines.append(f"Open trade right now: {_money(snap['openPnL'])}, added once it closes")
        return "\n".join(lines)
