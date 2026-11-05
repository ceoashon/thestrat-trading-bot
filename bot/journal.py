"""Logs finished trades to the Strat Trading Journal, filled in the way a person would fill out the trade form.

Trades wait in a file until the journal has them, so a slow or offline journal never delays the trading and a restart
loses nothing. The bot's secret can only add trades to its own journal (deploy/journal_bot_access.sql).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .config import Config

log = logging.getLogger("journal")

# The indicator's signal names and the journal's signal ids. Hammer / Shooter setups are tagged as the 2-2 and 3-2 family
# they belong to, and their full name stays in the notes.
SIGNALS = {
    "Failed 3-2-2 Reversal": "f322", "Failed 1-2-2 Reversal": "f122", "Failed 2-2 Reversal": "f22",
    "3-1-2 Reversal": "r312", "2-1-2 Reversal": "r212", "1-2-2 Reversal": "r122", "3-2-2 Reversal": "r322",
    "2-2 Reversal": "r22", "2-1-2 Continuation": "c212", "2-2 Continuation": "c22", "1-3 Nirvana": "nirv",
    "3-2 Continuation": "c32", "Pivot MG": "mg", "3-2 Reversal": "r32", "3-1-2 Continuation": "c312",
    "1-3-2 Reversal": "n32r", "Double Inside Reversal": "mibr", "Double Inside Continuation": "mibc",
    "Triple Inside Reversal": "mibr", "Triple Inside Continuation": "mibc",
}
INSIDE_BARS = {"3-1-2": "1", "2-1-2": "1", "Double Inside": "2", "Triple Inside": "3"}
# The continuity timeframes above each entry timeframe. An A+ setup has all of them aligned.
HTF_STACK = {
    "1": ["5m", "15m", "30m", "1h"],
    "5": ["15m", "30m", "1h"],
    "15": ["30m", "1h", "4h", "D"],
}


def parse_signal(sig: str) -> dict:
    """Splits the indicator's label, like 'Failed 2-2 Reversal A+ 1H LOW RECLAIM | 3 HIGHS +FLIP'."""
    name = next((n for n in sorted(SIGNALS, key=len, reverse=True) if sig.startswith(n)), "")
    rest = sig[len(name):].strip()
    grade = "A+" if rest.startswith("A+") else "B" if rest.split(" ")[:1] == ["B"] else ""
    flip = " + Flip" if "+FLIP" in rest else ""
    base = "Reclaim" if "RECLAIM" in rest else "Expansion" if "EXPANSION" in rest else ""
    ib = next((v for k, v in INSIDE_BARS.items() if name.startswith(k)), "")
    return {"signal": SIGNALS.get(name, ""), "grade": grade, "formation": base + flip if base else "", "ibCount": ib}


def _session(hour: int) -> str:
    return "London" if 3 <= hour < 8 else "NY" if 8 <= hour < 17 else "Asia"


def build_trade(cfg: Config, rec: dict, side: str, exit_px: float, entry_ms: int, exit_ms: int, fees: float | None, how: str) -> dict:
    """The journal's trade record for a finished trade. rec is the bot's trade or armed entry."""
    d = 1 if side == "long" else -1
    entry = rec.get("fill", rec["entry"])
    stop = rec.get("first_stop", rec["stop"])
    qty = rec.get("qty", cfg.qty)
    risk = abs(entry - stop)
    pts = (exit_px - entry) * d
    gross = pts * cfg.point_value * qty
    best = rec.get("best")
    when = datetime.fromtimestamp(entry_ms / 1000, ZoneInfo(cfg.timezone))
    sig = rec.get("sig", "")
    p = parse_signal(sig)
    notes = f"{sig}. Bot trade: planned entry {rec['entry']}, filled {entry}, exit {exit_px} ({how})."
    if rec.get("late"):
        notes += " Late entry: the market had passed the planned entry when the order arrived."
    if not p["formation"] and p["grade"] == "A+":
        notes += " This A+ tag has no matching journal formation, so that field is blank."
    return {
        "id": f"bot-{rec['entry_id']}", "seq": str(entry_ms), "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M"),
        "session": _session(when.hour), "direction": side.capitalize(), "signal": p["signal"], "entryTF": f"{cfg.timeframe}m",
        "grade": p["grade"], "htfAligned": HTF_STACK.get(cfg.timeframe, []) if p["grade"] == "A+" else [], "formation": p["formation"],
        "entry": str(entry), "stop": str(stop), "exit": str(exit_px), "best": "" if best is None else str(best), "worst": "",
        "stopPts": round(risk, 4), "maxR": round(max(0.0, (best - entry) * d / risk), 3) if best is not None and risk else None,
        "realizedR": round(pts / risk, 3) if risk else 0.0, "maeR": None, "holdMins": round((exit_ms - entry_ms) / 60000, 2),
        "emotion": "", "plan": "", "notes": notes, "link": "", "shotIds": [], "ibCount": p["ibCount"],
        "pnl": round(gross - (fees or 0.0), 2), "gross": round(gross, 2), "fees": None if fees is None else round(fees, 2),
        "riskUsd": round(risk * cfg.point_value * qty, 2), "qty": qty, "pts": round(pts, 4), "symbol": cfg.symbol,
        "imported": True, "review": False, "bot": True, "entryMs": entry_ms, "exitMs": exit_ms,
    }


class JournalLogger:
    RETRY_S = 60.0

    def __init__(self, url: str, key: str, token: str, spool: str, notifier=None, start: bool = True) -> None:
        self.endpoint = url.rstrip("/") + "/rest/v1/rpc/bot_log_trade"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.token = token
        self.spool = spool
        self.n = notifier
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.warned = False
        self.http = requests.Session()
        if start:
            threading.Thread(target=self._run, name="journal", daemon=True).start()

    def log(self, trade: dict) -> None:
        with self.lock:
            with open(self.spool, "a", encoding="utf-8") as f:
                f.write(json.dumps(trade) + "\n")
        self.wake.set()

    def _lines(self) -> list[str]:
        if not os.path.exists(self.spool):
            return []
        with open(self.spool, encoding="utf-8") as f:
            return [x for x in f.read().splitlines() if x.strip()]

    def _drop_first(self) -> None:
        with self.lock:
            rest = self._lines()[1:]
            tmp = self.spool + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(x + "\n" for x in rest)
            os.replace(tmp, self.spool)

    def _send(self, line: str) -> int:
        r = self.http.post(self.endpoint, headers=self.headers, json={"p_token": self.token, "p_trade": json.loads(line)}, timeout=15)
        if r.status_code >= 400:
            log.warning("the journal answered %s: %s", r.status_code, r.text[:200])
        return r.status_code

    def _say(self, text: str) -> None:
        if self.n:
            self.n.send(text)

    def flush(self) -> None:
        """Sends what is waiting, oldest first. Stops at the first failure and keeps the rest for the next try."""
        while True:
            lines = self._lines()
            if not lines:
                return
            try:
                status = self._send(lines[0])
            except requests.RequestException as e:
                log.warning("could not reach the journal: %s", e)
                status = 0
            if status == 400:
                # the journal refused this trade, and sending it again cannot change that
                with self.lock:
                    with open(self.spool + ".rejected", "a", encoding="utf-8") as f:
                        f.write(lines[0] + "\n")
                self._drop_first()
                self._say("⚠️ The journal refused a trade, so it was not logged. It is saved in journal_queue.jsonl.rejected on the server.")
                continue
            if status == 0 or status >= 400:
                if not self.warned:
                    self.warned = True
                    self._say("⚠️ Could not log a trade to the journal. The bot keeps trading and keeps trying to log it.")
                return
            self._drop_first()
            log.info("trade logged to the journal")
            if self.warned:
                self.warned = False
                self._say("📓 The journal is back, and the waiting trades were logged.")

    def _run(self) -> None:
        while True:
            try:
                self.flush()
            except Exception as e:  # never let a journal problem stop the bot
                log.warning("journal logging failed: %s", e)
            self.wake.wait(self.RETRY_S)
            self.wake.clear()


class NullJournal:
    def log(self, trade: dict) -> None:
        pass
