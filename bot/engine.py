"""The trade rules.

At every candle close (a "bar" message from the indicator):
  1. Entries armed on the last candle that did not fill are canceled (the trigger candle closed without the break).
  2. The position is checked. A filled entry becomes the open trade.
  3. In a trade: the stop moves to this candle's low minus the buffer (long) or high plus the buffer (short), only in
     the trade's favor.
  4. Flat: every setup the indicator armed gets a stop entry at the break (setup high + offset for a long, low - offset
     for a short) with its protective stop at the other side of the setup candle (OSO, so the stop exists the moment
     the entry fills).
A "cancel" message during the candle cancels a side once the other side of the setup candle broke first.
A timer flattens everything at the flatten time.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time as _time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .broker import LIVE_STATUSES, Broker, BrokerError, PriceAlreadyPast
from .config import Config
from .journal import NullJournal, build_trade
from .notify import NullNotifier

log = logging.getLogger("engine")

# The indicator's signal order (TheStrat Trader V2, sigS / sigL): when only one side may be armed, the earlier one wins.
RANK = [13, 1, 2, 3, 4, 17, 19, 5, 21, 22, 23, 16, 14, 11, 15, 18, 20, 9, 10, 12, 6, 7, 8]


def _dir(side: str) -> int:
    return 1 if side == "long" else -1


class Engine:
    # how long a market close gets to fill before its price is read for the journal
    FLAT_WAIT_S = 1.0

    def __init__(self, cfg: Config, broker: Broker, state_path: str | None = None, clock=None, notifier=None, journal=None) -> None:
        self.cfg = cfg
        self.b = broker
        self.n = notifier or NullNotifier()
        self.j = journal or NullJournal()
        self.path = state_path
        self.clock = clock or _time.time
        self.tz = ZoneInfo(cfg.timezone)
        self.lock = threading.RLock()
        self.s = self._load()

    # ---------------------------------------------------------------- state
    @staticmethod
    def _blank() -> dict:
        return {"last_bar_t": 0, "armed": {}, "trade": None, "day": "", "trades_today": 0, "flattened_day": ""}

    def _load(self) -> dict:
        s = self._blank()
        if self.path and os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                s.update(json.load(f))
            log.info("loaded state: armed %s, trade %s", list(s["armed"]), s["trade"])
        return s

    def _save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.s, f, indent=1)
        os.replace(tmp, self.path)

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.s)

    # ---------------------------------------------------------------- time
    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), self.tz)

    def _day_start(self, now: datetime) -> datetime:
        r = self.cfg.day_reset
        start = now.replace(hour=r.hour, minute=r.minute, second=0, microsecond=0)
        return start if now >= start else start - timedelta(days=1)

    def _roll_day(self, now: datetime) -> None:
        key = self._day_start(now).isoformat()
        if key != self.s["day"]:
            self.s["day"] = key
            self.s["trades_today"] = 0
            log.info("new trading day %s", key)

    def _in_window(self, now: datetime) -> bool:
        t, a, b = now.time(), self.cfg.start, self.cfg.end
        return a <= t < b if a <= b else (t >= a or t < b)

    def _past_flatten(self, now: datetime) -> bool:
        fa, r, t = self.cfg.flatten_at, self.cfg.day_reset, now.time()
        if fa is None:
            return False
        return fa <= t < r if fa < r else (t >= fa or t < r)

    def _usd(self, pts: float, qty: int) -> str:
        v = pts * self.cfg.point_value * qty
        return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"

    def _today_pnl(self) -> str:
        try:
            v = self.b.realized_pnl_since(int(self._day_start(self._now()).timestamp() * 1000))
        except BrokerError:
            return "n/a"
        return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"

    def _closed(self, side: str, entry: float, exit_px: float, how: str, qty: int) -> None:
        pts = (exit_px - entry) * _dir(side)
        self.n.send(f"🏁 **CLOSED {side.upper()}** {self.cfg.symbol} ({how}) | entry {entry} → exit {exit_px} | "
                    f"{pts:+.2f} pts x{qty} = **{self._usd(pts, qty)}** | today {self._today_pnl()}, {self.s['trades_today']} trade(s)")

    def _journal_close(self, rec: dict, side: str, exit_px: float, exit_id: int, how: str) -> None:
        """Hands a finished trade to the journal. A journal problem never touches the trading."""
        try:
            now_ms = int(self.clock() * 1000)
            entry_ms = self.b.fill_time(rec["entry_id"]) or now_ms
            exit_ms = self.b.fill_time(exit_id) or now_ms
            try:
                fees = self.b.order_fees(rec["entry_id"]) + self.b.order_fees(exit_id)
            except BrokerError as e:
                log.warning("could not read the fees of the trade: %s", e)
                fees = None
            self.j.log(build_trade(self.cfg, rec, side, exit_px, entry_ms, exit_ms, fees, how))
        except Exception as e:
            log.warning("could not log the trade to the journal: %s", e)
            self.n.send(f"⚠️ Could not log the {side} trade to the journal: {e}")

    def _journal_flat(self, tr: dict, how: str) -> None:
        """A trade the bot closed at market: its exit is the newest fill on the contract."""
        try:
            _time.sleep(self.FLAT_WAIT_S)
            last = self.b.last_fill()
        except BrokerError as e:
            last = None
            log.warning("could not read the closing fill: %s", e)
        if not last or last["order_id"] == tr["entry_id"]:
            log.warning("the closing fill of the %s trade was not found, so it was not logged to the journal", tr["side"])
            self.n.send(f"⚠️ The {tr['side']} trade was closed, but its closing price could not be read, so it was not logged to the journal.")
            return
        self._journal_close(tr, tr["side"], last["price"], last["order_id"], how)

    def _px(self, x: float) -> float:
        tk = self.cfg.tick_size
        return round(round(x / tk) * tk, 8)

    # ---------------------------------------------------------------- messages
    def handle(self, ev: dict) -> None:
        if self.cfg.tv_tickers and ev.get("sym") not in self.cfg.tv_tickers:
            log.warning("ignored a message from chart %s (not in tv_tickers %s)", ev.get("sym"), self.cfg.tv_tickers)
            return
        if str(ev.get("tf")) != self.cfg.timeframe:
            log.warning("ignored a message from timeframe %s (the bot trades %s)", ev.get("tf"), self.cfg.timeframe)
            return
        kind = ev.get("ev")
        if kind == "bar":
            self.on_bar(ev)
        elif kind == "cancel":
            self.on_cancel(ev)
        else:
            log.warning("unknown message type %r", kind)
        self.on_timer()

    def startup(self) -> None:
        """Make the saved state agree with the account before taking new messages."""
        with self.lock:
            self._roll_day(self._now())
            filled = self._resolve_armed()
            self._sync_trade(self.b.net_position(), filled)
            self._save()
            log.info("ready: trade %s, %d trades today", self.s["trade"], self.s["trades_today"])

    def on_bar(self, ev: dict) -> None:
        with self.lock:
            t = int(ev["t"])
            if t <= self.s["last_bar_t"]:
                log.info("candle %s already handled, ignored", t)
                return
            self.s["last_bar_t"] = t
            now = self._now()
            self._roll_day(now)
            h, l = float(ev["h"]), float(ev["l"])
            log.info("candle closed: H %s L %s | long setup %s %s | short setup %s %s", h, l, ev.get("L", 0),
                     ev.get("Ln", ""), ev.get("S", 0), ev.get("Sn", ""))
            filled = self._resolve_armed()
            pos = self.b.net_position()
            self._sync_trade(pos, filled)
            if self.s["trade"]:
                self._note_best(h, l)
                self._trail(h, l)
            elif pos == 0:
                self._arm(ev, now, h, l)
            self._save()

    def on_cancel(self, ev: dict) -> None:
        with self.lock:
            side = ev.get("side")
            a = self.s["armed"].get(side)
            if not a or int(ev.get("t", 0)) != a["setup_t"]:
                log.info("cancel %s: nothing armed for that candle", side)
                return
            if self.b.cancel(a["entry_id"]):
                self.b.cancel(a["stop_id"])
                del self.s["armed"][side]
                log.info("CANCELED %s entry %s: the other side of the setup candle broke first", side, a["entry"])
                if self.cfg.notify_cancels:
                    self.n.send(f"✖️ Canceled {side} entry {a['entry']}: the other side of the setup candle broke first")
            else:
                log.info("cancel %s refused (it may have filled), checked at the candle close", side)
            self._save()

    def on_timer(self) -> None:
        with self.lock:
            now = self._now()
            self._roll_day(now)
            if self._past_flatten(now) and self.s["flattened_day"] != self.s["day"]:
                self.s["flattened_day"] = self.s["day"]
                for a in self.s["armed"].values():
                    self.b.cancel(a["entry_id"])
                    self.b.cancel(a["stop_id"])
                self.s["armed"] = {}
                if self.s["trade"] or self.b.net_position() != 0:
                    log.info("FLATTEN TIME %s: closing the position", self.cfg.flatten_at)
                    self._flat_all()
                    if self.s["trade"]:
                        self._journal_flat(self.s["trade"], "flatten time")
                    self.n.send(f"🔚 Flatten time {self.cfg.flatten_at:%H:%M}: position closed at market, orders canceled | "
                                f"today {self._today_pnl()}")
                self.s["trade"] = None
                self._save()

    # ---------------------------------------------------------------- steps
    def _resolve_armed(self) -> list[tuple[str, dict]]:
        filled = []
        for side, a in list(self.s["armed"].items()):
            st = self.b.order_status(a["entry_id"])
            if st in LIVE_STATUSES:
                if self.b.cancel(a["entry_id"]):
                    got = self.b.filled_qty(a["entry_id"])
                    if not got:
                        self.b.cancel(a["stop_id"])
                        log.info("%s entry %s did not trigger, canceled", side, a["entry"])
                        if self.cfg.notify_cancels:
                            self.n.send(f"✖️ {side.capitalize()} entry {a['entry']} did not trigger, canceled")
                        continue
                    # part of it filled before the cancel: that part is the trade, and it keeps its stop
                    log.warning("%s entry %s filled only %d of %s", side, a["entry"], got, a.get("qty"))
                    a["qty"] = got
                    st = "Filled"
                else:
                    st = self.b.order_status(a["entry_id"])
            if st == "Filled":
                a["fill"] = self.b.fill_price(a["entry_id"]) or a["entry"]
                filled.append((side, a))
                self.s["trades_today"] += 1
                self.n.send(f"✅ **FILLED {side.upper()}** {self.cfg.symbol} x{a.get('qty', self.cfg.qty)} at {a['fill']} ({a.get('sig', '')}) | "
                            f"stop {a['stop']} | trade {self.s['trades_today']} today")
                log.info("FILLED %s at the break of %s (%s), trade %d today", side, a["entry"], a.get("sig", ""),
                         self.s["trades_today"])
            elif st not in ("Canceled", "Expired"):
                log.warning("%s entry %s ended as %s", side, a["entry_id"], st)
                self.n.send(f"⚠️ {side.capitalize()} entry at {a['entry']} ended as {st}, so it never became a trade")
        self.s["armed"] = {}
        return filled

    def _sync_trade(self, pos: int, filled: list[tuple[str, dict]]) -> None:
        want = (pos > 0) - (pos < 0)
        tr = self.s["trade"]
        if tr is None:
            for side, a in filled:
                if _dir(side) == want:
                    tr = {k: a[k] for k in ("entry", "stop", "entry_id", "stop_id", "setup_t", "sig", "fill")}
                    tr["side"] = side
                    tr["qty"] = a.get("qty", self.cfg.qty)
                    tr["first_stop"] = a["stop"]
                    tr["late"] = a.get("late", False)
                    log.info("IN TRADE %s from %s, stop %s", side, tr["entry"], tr["stop"])
            if filled and want == 0:
                log.info("the entry filled and the stop was hit inside the same candle")
                for side, a in filled:
                    self._closed(side, a["fill"], self.b.fill_price(a["stop_id"]) or a["stop"], "stopped out in the entry candle",
                                 a.get("qty", self.cfg.qty))
                    self._journal_close(a, side, self.b.fill_price(a["stop_id"]) or a["stop"], a["stop_id"], "stopped out in the entry candle")
        if tr is not None:
            if want == 0:
                log.info("TRADE CLOSED %s: stopped out (last stop %s)", tr["side"], tr["stop"])
                self._closed(tr["side"], tr.get("fill", tr["entry"]), self.b.fill_price(tr["stop_id"]) or tr["stop"], "stop hit",
                             tr.get("qty", self.cfg.qty))
                self._journal_close(tr, tr["side"], self.b.fill_price(tr["stop_id"]) or tr["stop"], tr["stop_id"], "stop hit")
                tr = None
            elif want != _dir(tr["side"]) or abs(pos) > tr.get("qty", self.cfg.qty):
                log.error("the position (%s) does not match the %s trade: flattening", pos, tr["side"])
                self._flat_all()
                self._journal_flat(tr, "the position did not match the trade, so the bot closed it")
                self.n.send(f"⚠️ The position ({pos}) did not match the {tr['side']} trade, so the bot flattened it. Check Tradovate.")
                tr = None
            else:
                st = self.b.order_status(tr["stop_id"])
                if st not in LIVE_STATUSES:
                    log.error("the position is open but its stop order is %s: flattening", st)
                    self._flat_all()
                    self._journal_flat(tr, f"its stop order was {st}, so the bot closed it")
                    self.n.send(f"⚠️ The position was open but its stop order was {st}, so the bot flattened it. Check Tradovate.")
                    tr = None
        elif want != 0:
            if self.cfg.flatten_unknown_positions:
                log.error("position %s that the bot is not tracking: flattening", pos)
                self._flat_all()
                self.n.send(f"⚠️ Found a position ({pos}) the bot did not open on {self.cfg.symbol}, so it flattened it.")
            else:
                log.error("position %s that the bot is not tracking: left alone (flatten_unknown_positions = false)", pos)
                self.n.send(f"⚠️ Found a position ({pos}) the bot did not open on {self.cfg.symbol}. Left alone, it has no bot stop.")
        self.s["trade"] = tr

    def _note_best(self, h: float, l: float) -> None:
        tr = self.s["trade"]
        px = h if tr["side"] == "long" else l
        if tr.get("best") is None or (px - tr["best"]) * _dir(tr["side"]) > 0:
            tr["best"] = px

    def _trail(self, h: float, l: float) -> None:
        tr = self.s["trade"]
        buf = self.cfg.stop_buffer_ticks * self.cfg.tick_size
        new = self._px(l - buf) if tr["side"] == "long" else self._px(h + buf)
        if (new - tr["stop"]) * _dir(tr["side"]) <= 0:
            return
        qty = tr.get("qty", self.cfg.qty)
        if self.b.modify_stop(tr["stop_id"], qty, new):
            log.info("TRAIL %s stop %s -> %s", tr["side"], tr["stop"], new)
            if self.cfg.notify_trail:
                locked = (new - tr.get("fill", tr["entry"])) * _dir(tr["side"])
                self.n.send(f"↗️ Stop moved {tr['stop']} → {new} | locks {locked:+.2f} pts ({self._usd(locked, qty)})")
            tr["stop"] = new
        else:
            log.warning("could not move the stop to %s, it stays at %s", new, tr["stop"])
            self.n.send(f"⚠️ Could not move the stop to {new}. It stays at {tr['stop']} at Tradovate.")

    def _block_reason(self, ev: dict, now: datetime) -> str:
        tc = int(ev.get("tc") or 0)
        if tc:
            age = self.clock() - tc / 1000.0
            if age > self.cfg.max_alert_age_s:
                return f"the alert arrived {age:.0f}s after the candle closed (max {self.cfg.max_alert_age_s:.0f}s)"
        if self._past_flatten(now) or self.s["flattened_day"] == self.s["day"]:
            return "after the flatten time"
        if not self._in_window(now):
            return f"outside the trading window {self.cfg.start:%H:%M}-{self.cfg.end:%H:%M}"
        if self.cfg.max_trades_per_day and self.s["trades_today"] >= self.cfg.max_trades_per_day:
            return f"{self.s['trades_today']} trades today (max {self.cfg.max_trades_per_day})"
        if self.cfg.max_daily_loss > 0:
            try:
                pnl = self.b.realized_pnl_since(int(self._day_start(now).timestamp() * 1000))
            except BrokerError as e:
                return f"could not read today's P&L ({e})"
            if pnl <= -self.cfg.max_daily_loss:
                return f"daily loss limit hit ({pnl:.2f} of -{self.cfg.max_daily_loss:.2f})"
        return ""

    def _arm(self, ev: dict, now: datetime, h: float, l: float) -> None:
        sigs = {"long": int(ev.get("L") or 0), "short": int(ev.get("S") or 0)}
        if self.cfg.setups:
            for s in sigs:
                if sigs[s] > 0 and sigs[s] not in self.cfg.setups:
                    log.info("%s setup %d skipped: it is not in your setups list", s, sigs[s])
                    sigs[s] = 0
        sides = [s for s in ("long", "short") if sigs[s] > 0 and self.cfg.direction in ("both", s)]
        if not sides:
            return
        why = self._block_reason(ev, now)
        if why:
            log.info("setup %s not armed: %s", "/".join(sides), why)
            if not why.startswith(("outside the trading window", "after the flatten time")):
                self.n.send(f"⏸️ {'/'.join(sides).capitalize()} setup not taken: {why}")
            return
        if len(sides) == 2 and not self.cfg.both_sides:
            rk = lambda s: RANK.index(sigs[s]) if sigs[s] in RANK else 99
            sides = [min(sides, key=rk)]
        tk = self.cfg.tick_size
        for side in sides:
            d = _dir(side)
            entry = self._px((h if d == 1 else l) + d * self.cfg.entry_offset_ticks * tk)
            stop = self._px((l if d == 1 else h) - d * self.cfg.stop_buffer_ticks * tk)
            risk_ticks = round(abs(entry - stop) / tk)
            if self.cfg.max_stop_ticks and risk_ticks > self.cfg.max_stop_ticks:
                log.info("%s setup skipped: stop %d ticks is above the max %d", side, risk_ticks, self.cfg.max_stop_ticks)
                continue
            if self.cfg.min_stop_ticks and risk_ticks < self.cfg.min_stop_ticks:
                log.info("%s setup skipped: stop %d ticks is below the min %d", side, risk_ticks, self.cfg.min_stop_ticks)
                continue
            limit = self._px(entry + d * self.cfg.stop_limit_ticks * tk) if self.cfg.entry_type == "StopLimit" else None
            name = ev.get("Ln" if d == 1 else "Sn", "") or str(sigs[side])
            try:
                qty = self._contracts(abs(entry - stop))
            except BrokerError as e:
                log.error("%s entry at %s not placed: %s", side, entry, e)
                self.n.send(f"⚠️ {side.capitalize()} entry at {entry} was not placed: {e}")
                continue
            if qty < 1:
                log.info("%s setup skipped: one contract risks more than %s%% of the account", side, self.cfg.risk_pct)
                self.n.send(f"⏸️ {side.capitalize()} {name} not taken: one contract would risk "
                            f"{self._usd(abs(entry - stop), 1)}, more than {self.cfg.risk_pct}% of the account")
                continue
            late = False
            try:
                eid, sid = self.b.place_entry(side, qty, entry, stop, limit)
            except PriceAlreadyPast as e:
                done = self._enter_late(side, entry, stop, name, e)
                if done is None:
                    continue
                eid, sid, qty = done
                late = True
            except BrokerError as e:
                log.error("%s entry at %s not placed: %s", side, entry, e)
                self.n.send(f"⚠️ {side.capitalize()} entry at {entry} was not placed: {e}")
                continue
            self.s["armed"][side] = {"entry_id": eid, "stop_id": sid, "entry": entry, "stop": stop,
                                     "setup_t": int(ev["t"]), "sig": name, "qty": qty, "late": late}
            if late:
                continue
            log.info("ARMED %s %s: %s stop at %s, protective stop %s (%d ticks risk)", side.upper(), name,
                     "buy" if d == 1 else "sell", entry, stop, risk_ticks)
            if self.cfg.notify_armed:
                self.n.send(f"📌 **ARMED {side.upper()}** {self.cfg.symbol} x{qty} | {name} | {'buy' if d == 1 else 'sell'} stop {entry} | "
                            f"stop loss {stop} ({risk_ticks} ticks, risk {self._usd(-abs(entry - stop), qty)})")

    def _contracts(self, risk_pts: float) -> int:
        """Contracts for a trade. With risk_pct on: the most that risk no more than that percent of the balance, up to max_qty."""
        if self.cfg.risk_pct <= 0:
            return self.cfg.qty
        qty = int(self.b.account_balance() * self.cfg.risk_pct / 100 / (risk_pts * self.cfg.point_value))
        return min(qty, self.cfg.max_qty) if self.cfg.max_qty else qty

    def _enter_late(self, side: str, entry: float, stop: float, name: str, why: Exception) -> tuple[int, int, int] | None:
        """The market passed the entry before the order arrived: try once, no further than late_entry_ticks past it."""
        cap = self.cfg.late_entry_ticks
        if cap <= 0:
            log.error("%s entry at %s not placed: %s", side, entry, why)
            self.n.send(f"⚠️ {side.capitalize()} entry at {entry} was not placed: {why}")
            return None
        limit = self._px(entry + _dir(side) * cap * self.cfg.tick_size)
        try:
            qty = self._contracts(abs(limit - stop))
            ids = self.b.place_late_entry(side, qty, limit, stop) if qty >= 1 else None
        except BrokerError as e:
            log.error("%s late entry at %s failed: %s", side, limit, e)
            self.n.send(f"⚠️ {side.capitalize()} late entry at {limit} failed: {e}")
            return None
        if ids is None:
            log.info("%s %s missed: the market was more than %d ticks past %s", side, name, cap, entry)
            self.n.send(f"⏭️ {side.capitalize()} {name} missed: the market was more than {cap} ticks past {entry} when the order "
                        f"arrived, so no trade")
            return None
        fill = self.b.fill_price(ids[0])
        log.info("LATE ENTRY %s %s: the market had passed %s, filled at %s, protective stop %s", side.upper(), name, entry, fill, stop)
        self.n.send(f"⚡ **LATE ENTRY {side.upper()}** {self.cfg.symbol} x{qty} | {name} | the market had passed {entry} | filled at "
                    f"{fill if fill is not None else 'unknown'} | stop loss {stop}")
        return ids[0], ids[1], qty

    def _flat_all(self) -> None:
        try:
            self.b.cancel_all()
        finally:
            self.b.flatten()
