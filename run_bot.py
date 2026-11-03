"""Starts the execution bot:  python run_bot.py --config config.toml"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from bot.broker import DryRunBroker
from bot.config import load_config
from bot.engine import Engine
from bot.server import make_server


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

    if cfg.dry_run:
        log.warning("DRY RUN: orders are only logged. Set dry_run = false in config.toml to send them to Tradovate.")
        broker = DryRunBroker()
    else:
        from bot.tradovate import TradovateBroker
        broker = TradovateBroker(cfg)
        broker.connect()
        if cfg.env == "live":
            log.warning("LIVE ACCOUNT: real money")

    engine = Engine(cfg, broker, cfg.state_file)
    engine.startup()
    srv = make_server(cfg, engine)
    log.info("trading %s x%d on %sm candles, window %s-%s %s, listening on http://%s:%d/webhook", cfg.symbol, cfg.qty,
             cfg.timeframe, cfg.start.strftime("%H:%M"), cfg.end.strftime("%H:%M"), cfg.timezone, cfg.host, cfg.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("stopped by keyboard. Orders already at Tradovate stay there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
