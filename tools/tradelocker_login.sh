#!/bin/bash
# Saves your TradeLocker login on the bot server, for the bot and tools/tradelocker_check.py. Run it on your Mac:
#   bash tools/tradelocker_login.sh
# The password is typed hidden. It goes straight to the server over your SSH key and is never saved on this Mac.
set -e
read -r -p "TradeLocker email: " EMAIL
read -r -s -p "TradeLocker password (hidden, nothing shows as you type): " PASS; echo
read -r -p "TradeLocker server name (shown on TradeLocker's login screen or in the account details): " SERVER
q() { local s=${1//\\/\\\\}; printf '"%s"' "${s//\"/\\\"}"; }   # a TOML string, quote marks and backslashes escaped
printf '[tradelocker]\nenv = "demo"\nemail = %s\npassword = %s\nserver = %s\n' "$(q "$EMAIL")" "$(q "$PASS")" "$(q "$SERVER")" |
  ssh root@137.220.57.161 'umask 077; f=/home/bot/thestrat-trading-bot/tradelocker.toml; cat > $f && chown bot:bot $f && echo "Saved on the server. Tell Claude it is there."'
unset PASS
