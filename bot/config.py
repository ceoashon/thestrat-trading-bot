"""Loads config.toml. Secrets can also come from environment variables so they never sit in a file:
TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_CID, TRADOVATE_SEC, BOT_SECRET, DISCORD_WEBHOOK, DISCORD_BOT_TOKEN."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import time as dtime


def _hm(s: str | None) -> dtime | None:
    if not s:
        return None
    h, m = s.strip().split(":")
    return dtime(int(h), int(m))


@dataclass
class Config:
    # Tradovate
    env: str = "demo"
    username: str = ""
    password: str = ""
    app_id: str = "StratBot"
    app_version: str = "1.0"
    cid: int = 0
    sec: str = ""
    account_name: str = ""
    # which broker: "tradovate" or "tradelocker" (TradeLocker's demo side only, for CFDs such as GatesFX's NAS100.R)
    broker: str = "tradovate"
    tl_login_file: str = "tradelocker.toml"   # written by tools/tradelocker_login.sh, never committed
    tl_account: str = ""                       # the TradeLocker account id, like 2604648
    # instrument
    symbol: str = "MNQZ6"
    tv_tickers: list[str] = field(default_factory=lambda: ["MNQ1!", "NQ1!"])
    timeframe: str = "5"
    backup_timeframe: str = ""   # a second alert's timeframe, used only to trail a trade while the bot's own candles are missing. "" = off
    candle_grace_s: float = 60   # a candle counts as missing this many seconds after it should have closed
    tick_size: float = 0.25
    point_value: float = 2.0
    big_symbol: str = ""
    big_ratio: int = 10
    big_from: int = 20
    # orders
    qty: float = 1
    lot_step: float = 1        # the size step: 1 contract on futures, 0.01 lot on CFDs. Sizes round down to it
    max_gap_pts: float = 0     # refuse an entry this many points or more from the broker's price (an alert on the wrong chart). 0 = off
    entry_offset_ticks: int = 1
    stop_buffer_ticks: int = 1
    entry_type: str = "Stop"
    stop_limit_ticks: int = 4
    late_entry_ticks: int = 0
    # risk
    direction: str = "both"
    both_sides: bool = True
    risk_pct: float = 0.0
    max_qty: int = 100
    setups: list[int] = field(default_factory=list)
    max_trades_per_day: int = 3
    max_daily_loss: float = 0.0
    max_stop_ticks: int = 0
    min_stop_ticks: int = 0
    flatten_unknown_positions: bool = True
    # session
    timezone: str = "America/New_York"
    start: dtime = dtime(9, 30)
    end: dtime = dtime(11, 30)
    flatten_at: dtime | None = dtime(15, 55)
    day_reset: dtime = dtime(18, 0)
    # server
    host: str = "127.0.0.1"
    port: int = 8080
    secret: str = "change-me"
    max_alert_age_s: float = 15.0
    dry_run: bool = True
    state_file: str = "bot_state.json"
    log_file: str = "bot.log"
    # notifications
    discord_webhook: str = ""
    discord_bot_token: str = ""
    pnl_file: str = "pnl_history.jsonl"
    heartbeat_url: str = ""
    notify_armed: bool = True
    notify_cancels: bool = False
    notify_trail: bool = True
    notify_name: str = ""     # put in front of every Discord message, to tell bots apart in one channel
    # trade journal
    journal_url: str = ""
    journal_key: str = ""
    journal_token: str = ""
    journal_spool: str = "journal_queue.jsonl"

    def value_of(self, symbol: str) -> float:
        """Dollars per point of one contract of the symbol."""
        return self.point_value * self.big_ratio if symbol == self.big_symbol else self.point_value


def load_config(path: str) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    tv = raw.get("tradovate", {})
    ins = raw.get("instrument", {})
    od = raw.get("orders", {})
    rk = raw.get("risk", {})
    ss = raw.get("session", {})
    sv = raw.get("server", {})
    nt = raw.get("notify", {})
    jr = raw.get("journal", {})
    d = Config()
    c = Config(
        env=tv.get("env", d.env),
        username=os.environ.get("TRADOVATE_USERNAME", tv.get("username", d.username)),
        password=os.environ.get("TRADOVATE_PASSWORD", tv.get("password", d.password)),
        app_id=tv.get("app_id", d.app_id),
        app_version=tv.get("app_version", d.app_version),
        cid=int(os.environ.get("TRADOVATE_CID", tv.get("cid", d.cid))),
        sec=os.environ.get("TRADOVATE_SEC", tv.get("sec", d.sec)),
        account_name=tv.get("account_name", d.account_name),
        broker=raw.get("broker", {}).get("name", d.broker),
        tl_login_file=raw.get("tradelocker", {}).get("login_file", d.tl_login_file),
        tl_account=str(raw.get("tradelocker", {}).get("account", d.tl_account)),
        symbol=ins.get("symbol", d.symbol),
        tv_tickers=list(ins.get("tv_tickers", d.tv_tickers)),
        timeframe=str(ins.get("timeframe", d.timeframe)),
        backup_timeframe=str(ins.get("backup_timeframe", d.backup_timeframe)),
        candle_grace_s=float(ins.get("candle_grace_s", d.candle_grace_s)),
        tick_size=float(ins.get("tick_size", d.tick_size)),
        point_value=float(ins.get("point_value", d.point_value)),
        big_symbol=ins.get("big_symbol", d.big_symbol),
        big_ratio=int(ins.get("big_ratio", d.big_ratio)),
        big_from=int(ins.get("big_from", d.big_from)),
        lot_step=float(od.get("lot_step", d.lot_step)),
        qty=float(od.get("qty", d.qty)),
        max_gap_pts=float(ins.get("max_gap_pts", d.max_gap_pts)),
        entry_offset_ticks=int(od.get("entry_offset_ticks", d.entry_offset_ticks)),
        stop_buffer_ticks=int(od.get("stop_buffer_ticks", d.stop_buffer_ticks)),
        entry_type=od.get("entry_type", d.entry_type),
        stop_limit_ticks=int(od.get("stop_limit_ticks", d.stop_limit_ticks)),
        late_entry_ticks=int(od.get("late_entry_ticks", d.late_entry_ticks)),
        direction=rk.get("direction", d.direction),
        both_sides=bool(rk.get("both_sides", d.both_sides)),
        risk_pct=float(rk.get("risk_pct", d.risk_pct)),
        max_qty=float(rk.get("max_qty", d.max_qty)),
        setups=[int(x) for x in rk.get("setups", d.setups)],
        max_trades_per_day=int(rk.get("max_trades_per_day", d.max_trades_per_day)),
        max_daily_loss=float(rk.get("max_daily_loss", d.max_daily_loss)),
        max_stop_ticks=int(rk.get("max_stop_ticks", d.max_stop_ticks)),
        min_stop_ticks=int(rk.get("min_stop_ticks", d.min_stop_ticks)),
        flatten_unknown_positions=bool(rk.get("flatten_unknown_positions", d.flatten_unknown_positions)),
        timezone=ss.get("timezone", d.timezone),
        start=_hm(ss.get("start")) or d.start,
        end=_hm(ss.get("end")) or d.end,
        flatten_at=_hm(ss.get("flatten_at", "15:55")),
        day_reset=_hm(ss.get("day_reset")) or d.day_reset,
        host=sv.get("host", d.host),
        port=int(sv.get("port", d.port)),
        secret=os.environ.get("BOT_SECRET", sv.get("secret", d.secret)),
        max_alert_age_s=float(sv.get("max_alert_age_s", d.max_alert_age_s)),
        dry_run=bool(sv.get("dry_run", d.dry_run)),
        state_file=sv.get("state_file", d.state_file),
        log_file=sv.get("log_file", d.log_file),
        discord_webhook=os.environ.get("DISCORD_WEBHOOK", nt.get("discord_webhook", d.discord_webhook)),
        discord_bot_token=os.environ.get("DISCORD_BOT_TOKEN", nt.get("discord_bot_token", d.discord_bot_token)),
        pnl_file=nt.get("pnl_file", d.pnl_file),
        heartbeat_url=nt.get("heartbeat_url", d.heartbeat_url),
        notify_armed=bool(nt.get("armed", d.notify_armed)),
        notify_cancels=bool(nt.get("cancels", d.notify_cancels)),
        notify_trail=bool(nt.get("trail", d.notify_trail)),
        notify_name=str(nt.get("name", d.notify_name)),
        journal_url=jr.get("url", d.journal_url),
        journal_key=jr.get("key", d.journal_key),
        journal_token=os.environ.get("JOURNAL_TOKEN", jr.get("token", d.journal_token)),
        journal_spool=jr.get("spool", d.journal_spool),
    )
    if c.lot_step == 1:                    # whole contracts stay whole numbers
        c.qty, c.max_qty = int(c.qty), int(c.max_qty)
    if c.broker not in ("tradovate", "tradelocker"):
        raise ValueError("broker.name must be 'tradovate' or 'tradelocker'")
    if c.env not in ("demo", "live"):
        raise ValueError("tradovate.env must be 'demo' or 'live'")
    if c.direction not in ("both", "long", "short"):
        raise ValueError("risk.direction must be 'both', 'long' or 'short'")
    if c.entry_type not in ("Stop", "StopLimit"):
        raise ValueError("orders.entry_type must be 'Stop' or 'StopLimit'")
    if c.backup_timeframe:
        a, b = c.timeframe, c.backup_timeframe
        if not (a.isdigit() and b.isdigit()) or a == b or max(int(a), int(b)) % min(int(a), int(b)):
            raise ValueError("instrument.backup_timeframe must be minutes, not the bot's timeframe, and a whole multiple "
                             "or a whole part of it (5 for a 15 minute bot, 5 for a 1 minute bot)")
    if c.secret in ("", "change-me"):
        raise ValueError("set server.secret (or BOT_SECRET) to your own password, the same one as in the indicator")
    return c
