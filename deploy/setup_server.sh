#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 cloud server. Run as root:  bash setup_server.sh
# Expects the bot folder at /home/bot/thestrat-trading-bot (copied up with scp); clones it from GitHub if it is missing.
set -euo pipefail
APP=/home/bot/thestrat-trading-bot
timedatectl set-timezone America/New_York
# a new server often runs its own updates for the first minutes: wait for them instead of failing
APT="apt-get -o DPkg::Lock::Timeout=600"
$APT update -y && $APT install -y python3-venv git curl ufw
id bot >/dev/null 2>&1 || useradd -m -s /bin/bash bot
[ -d "$APP" ] || sudo -u bot git clone -b "${BRANCH:-claude/new-session-8apn01}" https://github.com/ceoashon/thestrat-trading-bot.git "$APP"
chown -R bot:bot "$APP"
sudo -u bot bash -c "cd '$APP' && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-bot.txt && ([ -f config.toml ] || cp config.example.toml config.toml) && chmod 600 config.toml"
sudo -u bot "$APP/.venv/bin/python" -m unittest discover -s "$APP/tests" -q
# cloudflared: the HTTPS address TradingView posts to (outbound only, so no inbound port is opened)
# (amd64 or arm64, whichever this server is)
curl -fsSL -o /tmp/cloudflared.deb "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$(dpkg --print-architecture).deb"
dpkg -i /tmp/cloudflared.deb
cp "$APP/deploy/strat-bot.service" "$APP/deploy/strat-tunnel.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now strat-tunnel
# firewall: only SSH comes in
ufw allow OpenSSH && ufw --force enable
# wait up to a minute for the tunnel to report its address
URL=""
for _ in $(seq 30); do
  URL=$(journalctl -u strat-tunnel --no-pager | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)
  [ -n "$URL" ] && break
  sleep 2
done
echo
echo "Tunnel address (the TradingView webhook is this + /webhook):  ${URL:-not ready yet, see: journalctl -u strat-tunnel}"
echo "Next: edit $APP/config.toml (nano), then:  systemctl enable --now strat-bot"
