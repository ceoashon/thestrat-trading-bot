"""Read-only: the open orders, positions and last few history rows on the bot's TradeLocker account. Places nothing.

    .venv/bin/python tools/tradelocker_state.py config-hs.toml
"""
from __future__ import annotations

import sys

from bot.config import load_config
from bot.tradelocker import TradeLockerBroker

KEYS = ("id", "status", "type", "side", "qty", "filledQty", "stopPrice", "avgPrice", "stopLoss", "stopLossId", "openPrice",
        "positionId", "lastModified")


def main(path: str) -> None:
    b = TradeLockerBroker(load_config(path))
    b.connect()
    print("price", b._quote(), "balance", b.account_balance())
    for what in ("orders", "positions", "ordersHistory"):
        rows = b._rows(what)
        print(what, len(rows))
        for r in rows[-8:]:
            print("  ", {k: r.get(k) for k in KEYS if k in r})


if __name__ == "__main__":
    main(*sys.argv[1:])
