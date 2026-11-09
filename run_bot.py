"""Starts the execution bot:  python run_bot.py --config config.toml"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from bot.broker import DryRunBroker
from bot.config import load_config
from bot.engine import Engine
from bot.journal import JournalLogger
from bot.notify import Notifier
from bot.pnl import PnlLedger
from bot.server import make_server


def start_commands(cfg, broker, notifier, log) -> None:
    """The P&L history and the Discord commands. Nothing here may ever stop the trading."""
    try:
        ledger = PnlLedger(broker, cfg.pnl_file, cfg.timezone, cfg.day_reset)
        threading.Thread(target=ledger.keep_saving, name="pnl", daemon=True).start()
        if cfg.discord_bot_token and cfg.discord_webhook:
            from bot.discord_commands import DiscordCommands
            DiscordCommands(cfg.discord_bot_token, cfg.discord_webhook, ledger, notifier).start()
    except Exception as e:
        log.warning("the Discord commands did not start: %s", e)


def main() -> int:
    ap = argparse.ArgumentParser(description="TheStrat execution bot (TradingView -> Tradovate)")
    ap.add_argument("--config", default="config.toml")
    args = ap.parse_args()
    cfg = load_config(args.config)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-9s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), RotatingFileHandler(cfg.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)
    log = logging.getLogger("bot")

    prefix = ("[DRY RUN] " if cfg.dry_run else "") + (f"[{cfg.notify_name}] " if cfg.notify_name else "")
    notifier = Notifier(cfg.discord_webhook, cfg.heartbeat_url, prefix=prefix)
    if cfg.dry_run:
        log.warning("DRY RUN: orders are only logged. Set dry_run = false in config.toml to send them to Tradovate.")
        broker = DryRunBroker()
    else:
        if cfg.broker == "tradelocker":
            from bot.tradelocker import TradeLockerBroker
            broker = TradeLockerBroker(cfg)
        else:
            from bot.tradovate import TradovateBroker
            broker = TradovateBroker(cfg)
        try:
            broker.connect()
        except Exception as e:
            notifier.send(f"❗ The bot could not connect to {cfg.broker.capitalize()}: {e}")
            time.sleep(5)  # give the message time to go out
            raise
        if cfg.env == "live":
            log.warning("LIVE ACCOUNT: real money")

    journal = None
    if cfg.journal_token and not cfg.dry_run:
        journal = JournalLogger(cfg.journal_url, cfg.journal_key, cfg.journal_token, cfg.journal_spool, notifier)
    engine = Engine(cfg, broker, cfg.state_file, notifier=notifier, journal=journal)
    engine.startup()
    size = f"{cfg.risk_pct:g}% risk" if cfg.risk_pct > 0 else f"x{cfg.qty}"
    notifier.send(f"🟢 Bot started on {socket.gethostname()}: {cfg.env} {cfg.symbol} {size}, {cfg.timeframe}m, "
                  f"window {cfg.start:%H:%M}-{cfg.end:%H:%M} ET, flatten {cfg.flatten_at:%H:%M}" if cfg.flatten_at else
                  f"🟢 Bot started on {socket.gethostname()}: {cfg.env} {cfg.symbol} {size}")
    if not cfg.dry_run:
        start_commands(cfg, broker, notifier, log)
    srv = make_server(cfg, engine)
    log.info("trading %s %s on %sm candles, window %s-%s %s, listening on http://%s:%d/webhook", cfg.symbol, size,
             cfg.timeframe, cfg.start.strftime("%H:%M"), cfg.end.strftime("%H:%M"), cfg.timezone, cfg.host, cfg.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("stopped by keyboard. Orders already at Tradovate stay there.")
        notifier.send("🔴 Bot stopped by hand. Orders already at Tradovate stay there.")
        time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
