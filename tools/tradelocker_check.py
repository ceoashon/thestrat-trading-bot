"""Checks that the bot can reach TradeLocker, and reads what it needs to trade there. Places no orders.

    .venv/bin/python tools/tradelocker_check.py [tradelocker.toml]

Logs in with the login saved by tools/tradelocker_login.sh, then lists the accounts on it, finds the Nasdaq 100 and gold
symbols on each account, prints their specifications (lot sizes, tick size and value, trading hours, swaps when given),
and the request limits. Never prints the email, password or tokens.
"""
from __future__ import annotations

import json
import sys
import tomllib

import requests

BASES = {"demo": "https://demo.tradelocker.com/backend-api", "live": "https://live.tradelocker.com/backend-api"}
WANT = ("NAS100", "US100", "USTEC", "NDX", "NQ", "XAUUSD", "GOLD")


def main(path: str = "tradelocker.toml") -> None:
    with open(path, "rb") as f:
        c = tomllib.load(f)["tradelocker"]
    base = BASES[c.get("env", "demo")]
    http = requests.Session()
    r = http.post(f"{base}/auth/jwt/token", json={"email": c["email"], "password": c["password"], "server": c["server"]}, timeout=20)
    if r.status_code >= 400:
        sys.exit(f"login refused ({r.status_code}): {r.text[:300]}\nCheck the email, password and server name in {path}.")
    http.headers["Authorization"] = f"Bearer {r.json()['accessToken']}"
    print(f"logged in to TradeLocker {c.get('env', 'demo')}, server {c['server']}")
    accounts = http.get(f"{base}/auth/jwt/all-accounts", timeout=20).json().get("accounts", [])
    print(f"\n{len(accounts)} account(s):")
    for a in accounts:
        print("  ", {k: a.get(k) for k in ("id", "accNum", "name", "currency", "accountBalance", "status")})
    for a in accounts:
        h = {"accNum": str(a["accNum"])}
        r = http.get(f"{base}/trade/accounts/{a['id']}/instruments", headers=h, timeout=20)
        if r.status_code >= 400:
            print(f"\naccount {a['id']}: instruments not readable ({r.status_code}): {r.text[:200]}")
            continue
        ins = r.json().get("d", {}).get("instruments", [])
        print(f"\naccount {a['id']}: {len(ins)} symbols")
        for i in ins:
            if any(w in str(i.get("name", "")).upper().replace("/", "").replace(".", "") for w in WANT):
                routes = {x.get("type"): x.get("id") for x in i.get("routes", [])}
                print(f"  {i.get('name')}: id {i.get('tradableInstrumentId')}, routes {routes}")
                info = http.get(f"{base}/trade/instruments/{i['tradableInstrumentId']}", params={"routeId": routes.get("INFO") or routes.get("TRADE")},
                                headers=h, timeout=20)
                if info.status_code < 400:
                    print("     " + json.dumps(info.json().get("d", info.json()))[:1500])
        r = http.get(f"{base}/trade/config", headers=h, timeout=20)
        if r.status_code < 400:
            limits = r.json().get("d", {}).get("limits")
            print(f"\nrequest limits: {json.dumps(limits)[:1500]}")
        break   # the symbols and limits are the same on every demo account


if __name__ == "__main__":
    main(*sys.argv[1:])
