# TheStrat Execution Bot: setup

TradingView (TheStrat Automation) → webhook → this bot on the GMK K11 → Tradovate (demo).

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

## 1. The indicator: TheStrat Automation (10 min)
`pine/TheStratAutomation.pine` is a separate copy of TheStrat Trader V2, renamed **TheStrat Automation**. Every signal,
filter, drawing and table works the same. Only the alerts are restructured for the bot. It stays an **indicator** (not a
strategy), so alerts work the way you already use them.

1. TradingView → **Pine Editor** → **Open → New blank indicator**. Select everything in the editor and delete it.
2. Paste the whole content of `pine/TheStratAutomation.pine`. Click **Save** and name it `TheStrat Automation`.
3. Click **Add to chart** on a **5 minute MNQ1! (or NQ1!) chart**. Your TheStrat Trader V2 stays as it is; you can keep
   both on the chart or remove the old one.
4. Open the indicator settings (double-click its name on the chart), section **13. Alerts: Bot and Manual**:
   - **Alerts: Send To** = **Bot only** (the default).
   - **Bot: Secret** = your own password, letters and numbers only. The bot's `config.toml` must have the same `secret`.
5. Leave the other settings as you trade them. Two the bot assumes: **6. Stop Placement = Setup candle (C1)** and
   **Stop Buffer = 1** (they are the defaults). Click **OK**.

What it sends (section 14 of the script): at every candle close one message with the candle's high/low and the setups
it arms for the next candle, using exactly the filters the chart uses (signals, continuity with the flip, broadening
formation A+, Setups to Take, +FLIP, stop size, trading hours). During the next candle, a cancel message if the other
side of the setup candle breaks first.

**Check it before the bot exists (5 min):** open webhook.site in a browser; it shows a unique URL. Use that URL in the
alert below, and use a throwaway secret for this test (webhook.site pages are public). Within 5 minutes you see a `bar`
message at each candle close. After the test, change the secret and point the alert at the bot.

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
TradingView must have **real-time CME data** on your account (without it CME futures are 10 minutes late and the bot
skips every setup as too old).

On the 5 minute chart with TheStrat Automation: **Alert** (clock icon) → **Create alert**:
- **Condition:** `TheStrat Automation` → **Any alert() function call** (not "Price", and not one of the named conditions).
- **Expiration:** open-ended (or the longest your plan allows).
- **Alert name:** for example `Strat bot MNQ 5m`. The message box is not used: the indicator writes the message.
- **Notifications** tab: tick **Webhook URL** and paste your bot URL + `/webhook`. Untick notifications you do not want
  (the messages are JSON, not readable text; your readable notifications come from the bot's Discord messages).
- **Create.**

Recreate the alert every time you change an indicator setting or the bot URL (an alert keeps the settings it was made
with). Keep only one bot alert per chart and symbol.

## 6. Go live on demo
1. Set `dry_run = false` in `config.toml` and restart the bot. The log should say `connected to Tradovate demo`.
2. Run the test from step 3.5 once: the stop entry and its stop must show up as working orders in Tradovate (demo).
   Cancel them with the `cancel` test.
3. Let it trade 1 MNQ. Compare each fill and each stop move with the chart: entry = setup high/low ± 1 tick, every stop =
   the last closed candle's low − 1 tick / high + 1 tick.
4. Make it start by itself: Task Scheduler → Create Task → trigger **At startup** → action `start_bot.bat` → tick
   **Run whether user is logged on or not**. Do the same for cloudflared (or install it as a service:
   `cloudflared service install`). Set Windows power to never sleep, and Windows Update active hours to cover your window.

## 7. Discord notifications (5 min)
1. In Discord, open the channel: **Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**.
2. Paste it into `config.toml` under `[notify]` as `discord_webhook`, and restart the bot. You get a "Bot started" message.

What you get: 🟢 bot started, 📌 setup armed (entry, stop, risk in $), ✅ filled (real fill price), ↗️ stop moved
(and how much it locks in), 🏁 trade closed (points, $ and today's P&L), 🔚 flatten time, ⏸️ setup not taken (daily
limits, late alert), ⚠️ safety flattens or a stop that could not move, ❗ errors. Turn `armed`, `cancels` and `trail` on
or off in `[notify]`.

**Knowing when the bot is DOWN:** a stopped bot cannot send a message, so a free outside check does it. Make a check at
healthchecks.io (period 1 minute, grace 5 minutes), connect its Discord integration to your channel, and paste the
check's ping URL as `heartbeat_url`. The bot pings it every minute; if the pings stop, healthchecks.io posts to Discord.

## 8. Run it in the cloud (no home computer needed)
TradingView alerts already run on TradingView's servers. Put the bot on a small cloud server and the whole chain runs
with your computer off: TradingView servers → cloud server → Tradovate.

1. Rent the smallest Linux server (Ubuntu 24.04, 1 CPU, 1 GB RAM, about $5/month at DigitalOcean, Vultr, Linode or
   Hetzner). Pick a US region (Chicago if offered, close to CME).
2. Log in to it (the provider shows the command, like `ssh root@<server-ip>`) and run the setup script:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/ceoashon/thestrat-trading-bot/main/deploy/setup_server.sh -o setup_server.sh && bash setup_server.sh
   ```
   If the repository is private, copy the folder up instead (`scp -r thestrat-trading-bot root@<server-ip>:/home/bot/`),
   then run `bash /home/bot/thestrat-trading-bot/deploy/setup_server.sh`.
3. Fill in `/home/bot/thestrat-trading-bot/config.toml` (`nano` works), then start the bot. It restarts on its own after a
   crash or a reboot:
   ```bash
   systemctl enable --now strat-bot
   ```
4. The HTTPS address for TradingView. For a permanent one, use a Cloudflare named tunnel (needs a domain in a free
   Cloudflare account): `cloudflared tunnel login`, `cloudflared tunnel create strat-bot`,
   `cloudflared tunnel route dns strat-bot bot.<yourdomain>`, then run it as a service with
   `cloudflared service install` after pointing its config at `http://localhost:8080`. Your webhook URL is
   `https://bot.<yourdomain>/webhook`. To test today without a domain, the quick tunnel from step 4 works on the server too.
5. Update the TradingView alert's webhook URL to the new address. Then **stop the bot on the K11**: two bots on one
   account would both place orders.

Watch it: `journalctl -u strat-bot -f`. Update it: `cd /home/bot/thestrat-trading-bot && sudo -u bot git pull && systemctl restart strat-bot`.

**If the bot or the server goes down in the middle of a trade:** the entry's stop loss is already at Tradovate, so the
position stays protected. It only stops trailing until the bot is back (the heartbeat tells you).

## Notes
- `bot.log` has every alert, order, fill, trail and skip. `http://localhost:8080/health?key=<secret>` shows the state.
- Do not trade the bot's contract by hand on the same account while it runs (it flattens positions it did not open,
  see `flatten_unknown_positions`).
- Roll `symbol` to the next contract month before expiry (MNQZ6 → MNQH7).
- What the bot copies from the indicator: the setups and every filter (continuity with the flip, broadening formation,
  Setups to Take with +FLIP, A+ Needs a Continuity FLIP, stop size, trading hours), all judged at the setup candle's
  close. The bot's own settings decide the window, size, daily limits and the flatten time. The bot always trails to the
  last closed candle (the indicator's Trail from entry); other Exit Styles only change what the chart draws.
- Tests: `python -m unittest discover -s tests -v`
