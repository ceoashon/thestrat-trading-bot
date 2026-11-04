#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 cloud server. Run as root:  bash setup_server.sh
# Expects the bot folder at /home/bot/thestrat-trading-bot (copied up with scp); clones it from GitHub if it is missing.
set -euo pipefail
APP=/home/bot/thestrat-trading-bot
timedatectl set-timezone America/New_York
apt-get update -y && apt-get install -y python3-venv git curl ufw
id bot >/dev/null 2>&1 || useradd -m -s /bin/bash bot
[ -d "$APP" ] || sudo -u bot git clone -b "${BRANCH:-main}" https://github.com/ceoashon/thestrat-trading-bot.git "$APP"
chown -R bot:bot "$APP"
sudo -u bot bash -c "cd '$APP' && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-bot.txt && ([ -f config.toml ] || cp config.example.toml config.toml)"
sudo -u bot "$APP/.venv/bin/python" -m unittest discover -s "$APP/tests" -q
# cloudflared: the HTTPS address TradingView posts to (outbound only, so no inbound port is opened)
curl -fsSL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
dpkg -i /tmp/cloudflared.deb
cp "$APP/deploy/strat-bot.service" "$APP/deploy/strat-tunnel.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now strat-tunnel
# firewall: only SSH comes in
ufw allow OpenSSH && ufw --force enable
sleep 8
echo
echo "Tunnel address (the TradingView webhook is this + /webhook):"
journalctl -u strat-tunnel --no-pager | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true
echo "Next: edit $APP/config.toml (nano), then:  systemctl enable --now strat-bot"
