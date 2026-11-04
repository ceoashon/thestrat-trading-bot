# Handoff: TheStrat execution bot (continue on the GMK K11)

Paste the prompt below into a new Claude Code session that runs on the K11 (Windows), opened in the folder where the
bot should live (for example `C:\stratbot`).

---

I'm building an automated futures trading bot and continuing the work on this computer (my GMK K11, Windows), which
will run the bot 24/7. You can run commands here. Read everything below before doing anything.

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
- Risk management and a trading window are set in the bot's config. Instrument: MNQ on demo (signals from the 5 minute
  NQ1! chart).

## Where the code is
GitHub repo `ceoashon/thestrat-trading-bot`, branch **`claude/elegant-mccarthy-5r2psq`** (not merged to main yet).
Clone that branch here if it is not already on this computer. Key files:
- `pine/TheStratAutomation.pine` – the indicator (already installed on TradingView, working). Section 13: "Alerts: Send
  To" (Bot only) and "Bot: Secret". Section 14: the JSON bot messages (`bar` at every candle close with o/h/l/c and the
  armed setups `L`/`S`; `cancel` during the next candle).
- `bot/` – the bot: `engine.py` (trade rules), `tradovate.py` (Tradovate REST client), `server.py` (webhook on
  `127.0.0.1:8080/webhook`), `notify.py` (Discord + heartbeat), `broker.py` (dry-run broker), `config.py`.
- `run_bot.py` (start), `config.example.toml` (copy to `config.toml`, git-ignored, holds the passwords),
  `tools/send_test.py` (fake alerts), `tests/test_engine.py` (17 tests), `start_bot.bat` (auto-restart loop),
  `deploy/` (Linux cloud server files, for later), `BOT_SETUP.md` (the full setup guide).

## What is done
- Phase 1 done: TheStrat Automation is on my 5 minute NQ1! chart with one alert ("Any alert() function call") pointed at
  webhook.site. Messages arrive about 1 second after each candle close as JSON. The bot parses them (tested).
- Bot code written and tested in the cloud: unit tests pass, dry-run end to end works (arm, cancel, both sides, cancel
  unfilled at the next close), Discord sending works against a stand-in.

## Not verified yet
- The Tradovate API calls have never run against Tradovate (no credentials were available). Endpoints used:
  `/auth/accesstokenrequest`, `/auth/renewaccesstoken`, `/account/list`, `/contract/find`, `/order/placeoso`,
  `/order/item`, `/order/cancelorder`, `/order/modifyorder`, `/order/liquidateposition`, `/position/list`,
  `/order/list`, `/fill/list`, `/fill/deps`. The first demo run is the test; fix field names if Tradovate disagrees.
- Unknown: whether Tradovate rejects a buy stop placed below the market (when the break already happened) or fills it.
  Test on demo on purpose.
- Tradovate API access may need the API Access add-on and a funded live account. My account's access is not confirmed.

## What to do now, in order (Phase 2)
1. Check Python 3.11+ on this computer (`python --version`); install Python 3.12 if missing (with "Add to PATH").
2. Get the repo branch onto this computer, then `pip install -r requirements-bot.txt`.
3. Run the tests: `python -m unittest discover -s tests -v`.
4. Help me create `config.toml` from `config.example.toml`: my new secret (I will type it; it must match the
   indicator's "Bot: Secret"), `symbol = "MNQZ6"`, `point_value = 2.0`, `tv_tickers` includes `"NQ1!"`,
   `dry_run = true`. For testing outside 09:30-11:30 ET, widen `start`/`end` temporarily and remind me to set them back.
   Do not print my passwords back to me or commit config.toml.
5. Start the bot (`python run_bot.py`), then send a fake setup with `tools/send_test.py` and confirm it logs ARMED.
6. Install cloudflared (`winget install --id Cloudflare.cloudflared`), run `cloudflared tunnel --url
   http://localhost:8080`, and give me the https address. I update the TradingView alert's webhook to that address +
   `/webhook` and recreate the alert with the new secret. Confirm real `candle closed` lines appear every 5 minutes.
7. When I have the Tradovate API key (cid and secret): fill in the Tradovate section, set `dry_run = false`, restart,
   confirm "connected to Tradovate demo", and check the first armed setup shows up as working orders in Tradovate.
8. Then: Discord webhook in `[notify]`, a Task Scheduler "At startup" task for `start_bot.bat` and for cloudflared,
   Windows power never sleeps, Windows Update active hours over my trading window.

Rules: do not change the indicator's signal or filter logic. Ask before placing anything on a live (non-demo) account.
Only one bot may run on the account at a time. Explain things simply; I am not a programmer.
