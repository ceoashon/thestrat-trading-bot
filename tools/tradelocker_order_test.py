"""One supervised order test on the TradeLocker demo account, 0.01 lot ($1 a point on NAS100). Run it as user bot on the server:

    .venv/bin/python tools/tradelocker_order_test.py config-hs.toml

1. A buy stop entry 30 points above the price, with a stop loss: read its status, move its stop loss, cancel it.
2. A market buy with a stop loss 20 points away: read the position, move the stop loss to 15 points, close it.
Prints TradeLocker's own status words and the bot's reading of them, so bot/tradelocker.py can be checked against the real
replies. Never prints the login. Closes everything it opened, even when a step fails.
"""
from __future__ import annotations

import logging
import sys
import time

from bot.config import load_config
from bot.tradelocker import TradeLockerBroker

QTY = 0.01


def show(b: TradeLockerBroker, what: str) -> None:
    b._changed()
    for r in b._rows(what):
        print(f"   {what}: " + ", ".join(f"{k}={r.get(k)}" for k in ("id", "status", "type", "side", "qty", "filledQty", "price",
                                                                          "stopPrice", "avgPrice", "positionId", "stopLoss",
                                                                          "stopLossId", "openPrice") if k in r))


def main(path: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    b = TradeLockerBroker(load_config(path))
    b.connect()
    bid, ask = b._quote()
    print(f"price {bid}/{ask}, balance {b.account_balance()}, net position {b.net_position()}")
    if b.net_position():
        sys.exit("a position is already open on this account: stopping, nothing placed")
    try:
        print("\n1. buy stop entry 30 points above, stop loss 50 below the entry")
        entry = round(ask + 30, 2)
        eid, sid = b.place_entry("long", QTY, entry, round(entry - 50, 2))
        time.sleep(1.2)
        print(f"   entry status {b.order_status(eid)}, stop status {b.order_status(sid)}")
        show(b, "orders")
        print(f"   move its stop loss to 40 below: {b.modify_stop(sid, QTY, round(entry - 40, 2))}")
        time.sleep(1.2)
        show(b, "orders")
        print(f"   cancel: {b.cancel(sid)}")
        time.sleep(1.2)
        b._changed()
        print(f"   entry status {b.order_status(eid)}, stop status {b.order_status(sid)}")
        show(b, "orders")

        print("\n2. market buy, stop loss 20 points below")
        bid, ask = b._quote()
        got = b.place_late_entry("long", QTY, round(ask + 5, 2), round(bid - 20, 2))
        print(f"   late entry gave {got}")
        if got:
            eid, sid = got
            time.sleep(1.2)
            b._changed()
            print(f"   entry status {b.order_status(eid)}, stop status {b.order_status(sid)}, net {b.net_position()}, "
                  f"fill price {b.fill_price(eid)}")
            show(b, "positions")
            bid, ask = b._quote()
            print(f"   move stop loss to 15 below the bid: {b.modify_stop(sid, QTY, round(bid - 15, 2))}")
            time.sleep(1.2)
            show(b, "positions")
            b.flatten()
            time.sleep(1.5)
            b._changed()
            print(f"   after close: stop status {b.order_status(sid)}, net {b.net_position()}, exit price {b.fill_price(sid)}, "
                  f"exit time {b.fill_time(sid)}")
            show(b, "ordersHistory")
    finally:
        b._changed()
        b.cancel_all()
        b.flatten()
        time.sleep(1.2)
        b._changed()
        print(f"\nend: net position {b.net_position()}, open orders {len(b._rows('orders'))}, balance {b.account_balance()}")


if __name__ == "__main__":
    main(*sys.argv[1:])
