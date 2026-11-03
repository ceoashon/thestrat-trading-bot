"""Discord notifications and a heartbeat. Both are sent from a background thread, so a slow Discord never delays an order.

Heartbeat: the bot pings a URL (for example a free healthchecks.io check) every minute. If the pings stop because the
bot, the server or its internet is down, that service sends you the alert (it can post to the same Discord channel)."""
from __future__ import annotations

import logging
import queue
import threading
import time

import requests

log = logging.getLogger("notify")


class Notifier:
    def __init__(self, discord_webhook: str = "", heartbeat_url: str = "", prefix: str = "") -> None:
        self.discord = discord_webhook.strip()
        self.heartbeat = heartbeat_url.strip()
        self.prefix = prefix
        self.q: queue.Queue = queue.Queue(maxsize=500)
        self.last_ping = 0.0
        self.http = requests.Session()
        if self.discord or self.heartbeat:
            threading.Thread(target=self._run, name="notify", daemon=True).start()

    def send(self, text: str) -> None:
        if self.discord:
            try:
                self.q.put_nowait(("discord", (self.prefix + text)[:1900]))
            except queue.Full:
                log.warning("notification queue full, dropped: %s", text)

    def ping(self) -> None:
        """Called often; pings the heartbeat URL at most once a minute."""
        if self.heartbeat and time.time() - self.last_ping >= 60:
            self.last_ping = time.time()
            try:
                self.q.put_nowait(("ping", ""))
            except queue.Full:
                pass

    def _run(self) -> None:
        while True:
            kind, text = self.q.get()
            try:
                if kind == "ping":
                    self.http.get(self.heartbeat, timeout=10)
                    continue
                for _ in range(3):
                    r = self.http.post(self.discord, json={"content": text}, timeout=10)
                    if r.status_code == 429:  # Discord rate limit: wait as told, then retry
                        time.sleep(float(r.json().get("retry_after", 2)))
                        continue
                    if r.status_code >= 400:
                        log.warning("Discord answered %s: %s", r.status_code, r.text[:200])
                    break
            except Exception as e:  # never let a notification problem stop the bot
                log.warning("notification failed: %s", e)


class NullNotifier:
    def send(self, text: str) -> None:
        pass

    def ping(self) -> None:
        pass
