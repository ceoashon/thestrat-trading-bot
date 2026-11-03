"""Sends a fake indicator message to the running bot, to test it without waiting for a real setup.

    python tools/send_test.py bar --high 28410 --low 28380 --long 3      # a candle closed and armed a long (Failed 2-2)
    python tools/send_test.py bar --high 28430 --low 28405               # next candle closed, no setup (cancel / trail)
    python tools/send_test.py cancel --side long                         # the low broke first, cancel the long
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["bar", "cancel"])
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--high", type=float, default=0)
    ap.add_argument("--low", type=float, default=0)
    ap.add_argument("--long", type=int, default=0, help="long signal id (0 = none)")
    ap.add_argument("--short", type=int, default=0, help="short signal id (0 = none)")
    ap.add_argument("--side", choices=["long", "short"], default="long")
    ap.add_argument("--sym", default="")
    args = ap.parse_args()
    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)
    sv, ins = cfg.get("server", {}), cfg.get("instrument", {})
    tf_ms = int(ins.get("timeframe", "5")) * 60_000
    # A test candle that "closed" one second ago, so the bot's late-alert check lets it arm. Its open time is kept in a
    # file next to the config, so a later "cancel" test points at it.
    memo = os.path.join(os.path.dirname(os.path.abspath(args.config)), ".last_test_bar")
    msg = {"key": sv.get("secret", ""), "ev": args.kind, "sym": args.sym or ins.get("tv_tickers", ["MNQ1!"])[0],
           "tf": str(ins.get("timeframe", "5"))}
    if args.kind == "bar":
        tc = int(time.time() * 1000) - 1000
        t = tc - tf_ms
        with open(memo, "w") as f:
            f.write(str(t))
        msg.update({"t": t, "tc": tc, "o": args.low, "h": args.high, "l": args.low, "c": args.high,
                    "L": args.long, "S": args.short, "Ln": "test long" if args.long else "", "Sn": "test short" if args.short else ""})
    else:
        with open(memo) as f:
            msg.update({"side": args.side, "t": int(f.read())})
    url = f"http://{sv.get('host', '127.0.0.1')}:{sv.get('port', 8080)}/webhook"
    req = urllib.request.Request(url, data=json.dumps(msg).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        print(r.status, r.read().decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
