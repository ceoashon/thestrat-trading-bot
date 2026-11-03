#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 cloud server. Run as root:  bash setup_server.sh
set -euo pipefail
timedatectl set-timezone America/New_York
apt-get update -y && apt-get install -y python3-venv git curl
id bot >/dev/null 2>&1 || useradd -m -s /bin/bash bot
sudo -u bot bash -c '
  cd ~ && [ -d thestrat-trading-bot ] || git clone https://github.com/ceoashon/thestrat-trading-bot.git
  cd thestrat-trading-bot && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-bot.txt
  [ -f config.toml ] || cp config.example.toml config.toml'
cp /home/bot/thestrat-trading-bot/deploy/strat-bot.service /etc/systemd/system/
systemctl daemon-reload
# cloudflared (the HTTPS address TradingView posts to)
curl -fsSL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
dpkg -i /tmp/cloudflared.deb
echo
echo "Next: edit /home/bot/thestrat-trading-bot/config.toml, then:  systemctl enable --now strat-bot"
