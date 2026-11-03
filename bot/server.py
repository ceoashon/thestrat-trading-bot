"""The webhook TradingView posts to. It answers at once (TradingView waits only a few seconds) and hands each message
to one worker thread, so messages are handled one at a time and in order."""
from __future__ import annotations

import hmac
import json
import logging
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import Config
from .engine import Engine

log = logging.getLogger("server")


def parse_alert(raw: bytes, secret: str) -> dict | None:
    """The bot message in an alert body, or None. The indicator's other alerts (plain text) are ignored."""
    try:
        ev = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        log.debug("ignored a non-JSON alert: %.80s", raw)
        return None
    if not isinstance(ev, dict) or "ev" not in ev:
        return None
    if not hmac.compare_digest(str(ev.get("key", "")), secret):
        log.warning("rejected a message with a wrong secret")
        return None
    ev.pop("key", None)
    return ev


def make_server(cfg: Config, engine: Engine) -> ThreadingHTTPServer:
    q: queue.Queue = queue.Queue()
    last_err = [0.0]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if urlparse(self.path).path != "/webhook":
                self.send_error(404)
                return
            n = min(int(self.headers.get("Content-Length") or 0), 65536)
            raw = self.rfile.read(n)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            ev = parse_alert(raw, cfg.secret)
            if ev:
                q.put(ev)

        def do_GET(self):
            u = urlparse(self.path)
            key = parse_qs(u.query).get("key", [""])[0]
            if u.path != "/health" or not hmac.compare_digest(key, cfg.secret):
                self.send_error(404)
                return
            body = json.dumps(engine.snapshot(), indent=1).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

    def worker():
        while True:
            try:
                ev = q.get(timeout=5)
            except queue.Empty:
                ev = None
            try:
                if ev is None:
                    engine.on_timer()
                else:
                    engine.handle(ev)
                engine.n.ping()
            except Exception as e:
                log.exception("error while handling %s", ev)
                if time.time() - last_err[0] > 300:  # at most one error message every 5 minutes
                    last_err[0] = time.time()
                    engine.n.send(f"❗ Bot error: {type(e).__name__}: {e}. Check bot.log. Orders already at Tradovate stay there.")

    threading.Thread(target=worker, name="engine", daemon=True).start()
    return ThreadingHTTPServer((cfg.host, cfg.port), Handler)
