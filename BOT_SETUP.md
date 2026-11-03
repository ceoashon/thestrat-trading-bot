# TheStrat Execution Bot: setup

TradingView (TheStrat Trader V2) → webhook → this bot on the GMK K11 → Tradovate (demo).

**How it enters on the break:** when a setup candle closes, the bot puts a **buy stop 1 tick above its high** (or a sell
stop 1 tick under its low) at Tradovate, with the **protective stop 1 tick beyond the other side** attached. The exchange
fills it the moment price trades through, so nothing depends on the bot's speed. At **every candle close** the stop moves
to that candle's low − 1 tick (long) or high + 1 tick (short), only in your favor, until it is hit.

| Situation | What the bot does |
|---|---|
| Setup candle closes (the indicator arms a long and/or short) | Rests the stop entry + attached stop |
| Next candle breaks the other side first (it would be a 3) | Cancels that entry right away (the indicator's own rule) |
| Next candle closes without the break | Cancels the entry, re-arms if the new candle is a setup |
| In a trade, a candle closes | Trails the stop to that candle's low − 1 tick / high + 1 tick |
| Inside bar arms both a long and a short | Both rest; the one that fills cancels the other |
| Alert arrives more than 15 s after the close | Does not arm (no chasing) |
| Outside your window, after max trades or max daily loss | Does not arm |
| Flatten time (15:55 ET) | Cancels everything and closes the position |
| Position without its stop, or a position the bot did not open | Flattens it |

## 1. The indicator (5 min)
1. Open TheStrat Trader V2 in the Pine editor. Paste all of `pine/autotrader_webhook.pine` right above the last line
   `// ===== END OF SCRIPT =====`. Save.
2. In the indicator settings, section **14. Auto-Trader Webhook**: switch **Send Bot Messages** ON and set a **Secret**
   (any password, no quote marks).

## 2. Tradovate API key (10 min)
In Tradovate: **Application Settings → API Access → Generate API Key**. Write down the **cid** and **secret**. If the
button is missing, the account needs the API Access add-on. The bot uses the **demo** server until you change `env`.

## 3. The bot on the K11 (15 min)
1. Install Python 3.12 from python.org (tick "Add python.exe to PATH").
2. Copy this repository to the K11, open a terminal in it, then run:
   ```bash
   pip install -r requirements-bot.txt
   ```
3. Copy `config.example.toml` to `config.toml` and fill in your Tradovate login, `cid`, `sec`, the same `secret` as the
   indicator, the contract (`symbol = "MNQZ6"`) and your risk settings. Leave `dry_run = true` for the first run.
4. Start it:
   ```bash
   python run_bot.py
   ```
5. In a second terminal, fake a setup and watch the first terminal:
   ```bash
   python tools/send_test.py bar --high 28402 --low 28380 --long 3
   ```
   ```bash
   python tools/send_test.py cancel --side long
   ```

## 4. A public URL for TradingView (10 min)
TradingView only sends webhooks to ports 80 and 443, so the bot needs an HTTPS address. Install `cloudflared` and run:
```bash
cloudflared tunnel --url http://localhost:8080
```
It prints an `https://….trycloudflare.com` address. Your webhook URL is that address + `/webhook`. This quick URL
changes each time cloudflared restarts; for a permanent one use a named Cloudflare tunnel (needs a domain) or an ngrok
free static domain.

## 5. The TradingView alert (2 min)
On the **5 minute MNQ1! (or NQ1!) chart** with the indicator: **Create Alert → Condition: TheStrat Trader V2 → Any
alert() function call → Expiration: open-ended → Notifications: Webhook URL** = your URL + `/webhook`.
Recreate the alert every time you change an indicator setting (an alert keeps the settings it was made with).

## 6. Go live on demo
1. Set `dry_run = false` in `config.toml` and restart the bot. The log should say `connected to Tradovate demo`.
2. Run the test from step 3.5 once: the stop entry and its stop must show up as working orders in Tradovate (demo).
   Cancel them with the `cancel` test.
3. Let it trade 1 MNQ. Compare each fill and each stop move with the chart: entry = setup high/low ± 1 tick, every stop =
   the last closed candle's low − 1 tick / high + 1 tick.
4. Make it start by itself: Task Scheduler → Create Task → trigger **At startup** → action `start_bot.bat` → tick
   **Run whether user is logged on or not**. Do the same for cloudflared (or install it as a service:
   `cloudflared service install`). Set Windows power to never sleep, and Windows Update active hours to cover your window.

## Notes
- `bot.log` has every alert, order, fill, trail and skip. `http://localhost:8080/health?key=<secret>` shows the state.
- Do not trade the bot's contract by hand on the same account while it runs (it flattens positions it did not open,
  see `flatten_unknown_positions`).
- Roll `symbol` to the next contract month before expiry (MNQZ6 → MNQH7).
- What the bot copies from the indicator: the setups, the continuity, broadening formation and "Setups to Take" filters
  (all evaluated at the setup candle's close, as the setup-ahead label does). The bot's own settings decide the window,
  size and daily limits. A "Setups to Take" choice that keeps only +FLIP setups is not applied before the trigger.
- Tests: `python -m unittest discover -s tests -v`
