# Handoff: TheStrat execution bot (set up the cloud server from my MacBook)

Paste the prompt below into a new Claude Code session that runs on my MacBook (Claude desktop app), opened in a folder
such as `~/stratbot`.

---

I'm building an automated futures trading bot. I decided to run it on a small **cloud server** (Ubuntu) so it trades
24/7 with my laptop closed. This session runs on my **MacBook**, which I use only to set up and control the server over
SSH. You can run commands on this Mac (including `ssh` and `scp` to the server). Read everything below before doing
anything.

## The goal
My TradingView indicator ("TheStrat Automation", a copy of my "TheStrat Trader V2") finds TheStrat setups (for example
a failed 2-2 reversal) filtered by timeframe continuity and broadening formation A+ rules. A Python bot takes those
trades automatically on my Tradovate **demo** account:
- **Entry on the break, never on the candle close.** When the setup candle closes, the bot rests a buy stop at the setup
  candle's high + 1 tick (a sell stop at its low - 1 tick for shorts), with the protective stop attached (OSO) at the
  other side of the setup candle, 1 tick beyond it. The exchange fills on the break.
- **Trailing stop:** at every candle close the stop moves to that candle's low - 1 tick (high + 1 tick for shorts),
  only in the trade's favor, until it is hit.
- If the next candle breaks the other side of the setup candle first, the entry is canceled. If it closes without
  the break, the entry is canceled.
- Risk management and the trading window are set in the bot's config. Instrument: MNQ on demo (signals from the 5
  minute NQ1! chart).

## Where the code is
GitHub repo `ceoashon/thestrat-trading-bot`, branch **`claude/new-session-8apn01`** (not merged to main yet).
Key files:
- `pine/TheStratAutomation.pine` – the indicator (already installed on TradingView, working). Section 13: "Alerts: Send
  To" (Bot only) and "Bot: Secret". Section 14: the JSON bot messages (`bar` at every candle close with o/h/l/c and the
  armed setups `L`/`S`; `cancel` during the next candle).
- `bot/` – the bot: `engine.py` (trade rules), `tradovate.py` (Tradovate REST client), `server.py` (webhook on
  `127.0.0.1:8080/webhook`), `notify.py` (Discord + heartbeat), `broker.py` (dry-run broker), `config.py`.
- `run_bot.py`, `config.example.toml` (copied to `config.toml` on the server, git-ignored, holds the passwords),
  `tools/send_test.py` (fake alerts), `tests/test_engine.py` (17 tests).
- `deploy/setup_server.sh` (one-time server setup: New York time zone, Python venv, tests, cloudflared, the
  `strat-bot` and `strat-tunnel` systemd services, firewall with only SSH open; prints the tunnel address),
  `deploy/strat-bot.service`, `deploy/strat-tunnel.service`.
- `BOT_SETUP.md` – the full guide (section 8 is the cloud server).

## What is done
- Phase 1 done: TheStrat Automation is on my 5 minute NQ1! chart with one alert ("Any alert() function call") pointed at
  webhook.site. JSON messages arrive about 1 second after each candle close. The bot parses them (tested).
- Bot code written and tested in a cloud session: unit tests pass, dry-run end to end works, Discord sending works
  against a stand-in.

## Not verified yet
- The Tradovate API calls have never run against Tradovate (no credentials were available). Endpoints used:
  `/auth/accesstokenrequest`, `/auth/renewaccesstoken`, `/account/list`, `/contract/find`, `/order/placeoso`,
  `/order/item`, `/order/cancelorder`, `/order/modifyorder`, `/order/liquidateposition`, `/position/list`,
  `/order/list`, `/fill/list`, `/fill/deps`. The first demo run is the test; fix field names if Tradovate disagrees.
- Unknown: whether Tradovate rejects a buy stop placed below the market (when the break already happened) or fills it.
- Tradovate API access may need the API Access add-on and a funded live account. My access is not confirmed.
- `deploy/setup_server.sh` has not run on a real server yet. Its Python part was tested on Ubuntu 24.04 in a cloud
  session (venv, install, 17 tests pass as the `bot` user, dry-run bot armed a fake setup from `tools/send_test.py`),
  and the cloudflared download was checked for both amd64 and arm64. Not tested there: the time zone, systemd
  services, firewall, and the quick tunnel itself.
- A login from a data-center IP may make Tradovate ask for a captcha once (the bot stops and says so; I then log in on
  the Tradovate website and restart it).

## What to do now, in order
1. On this Mac: check for an SSH key (`~/.ssh/id_ed25519.pub`); make one if missing (`ssh-keygen -t ed25519`) and show
   me the public key to paste into the cloud provider.
2. I rent the server (DigitalOcean, Vultr or Hetzner; Ubuntu 24.04; smallest size; US region, Chicago if offered) with
   that key, and give you its IP address.
3. Get the branch onto the Mac (`git clone -b claude/new-session-8apn01 https://github.com/ceoashon/thestrat-trading-bot.git`,
   or I download the ZIP of that branch), then copy it up:
   `scp -r <folder> root@SERVER_IP:/root/thestrat-trading-bot`.
4. On the server: `useradd -m bot && mv /root/thestrat-trading-bot /home/bot/ && bash /home/bot/thestrat-trading-bot/deploy/setup_server.sh`.
   Fix anything that fails. It prints the tunnel address.
5. Help me fill in `/home/bot/thestrat-trading-bot/config.toml` on the server: my new secret (I will type it; it must
   match the indicator's "Bot: Secret"), `symbol = "MNQZ6"`, `point_value = 2.0`, `tv_tickers` includes `"NQ1!"`,
   `dry_run = true`. For testing outside 09:30-11:30 ET, widen `start`/`end` temporarily and remind me to set them back.
   Do not print my passwords back to me, do not commit config.toml.
6. `systemctl enable --now strat-bot`, then a fake setup with `tools/send_test.py` (run from the bot folder with the
   venv python) and confirm `ARMED` in `journalctl -u strat-bot`.
7. I set the TradingView alert's webhook to the tunnel address + `/webhook` and recreate the alert with the new secret.
   Confirm real `candle closed` lines every 5 minutes.
8. When I have the Tradovate API key (cid and secret): fill in the Tradovate section, set `dry_run = false`,
   `systemctl restart strat-bot`, confirm "connected to Tradovate demo", and check the first armed setup shows up as
   working orders in Tradovate.
9. Then: Discord webhook in `[notify]`, a healthchecks.io heartbeat, and (later) a permanent address with a Cloudflare
   named tunnel and a cheap domain.

Rules: do not change the indicator's signal or filter logic. Ask before anything touches a live (non-demo) account.
Only one bot may run on the account at a time (my GMK K11 is not running one). Explain things simply; I am not a
programmer.
