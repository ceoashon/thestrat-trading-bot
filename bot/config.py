"""Loads config.toml. Secrets can also come from environment variables so they never sit in a file:
TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_CID, TRADOVATE_SEC, BOT_SECRET, DISCORD_WEBHOOK."""
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
    # instrument
    symbol: str = "MNQZ6"
    tv_tickers: list[str] = field(default_factory=lambda: ["MNQ1!", "NQ1!"])
    timeframe: str = "5"
    tick_size: float = 0.25
    point_value: float = 2.0
    # orders
    qty: int = 1
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
    heartbeat_url: str = ""
    notify_armed: bool = True
    notify_cancels: bool = False
    notify_trail: bool = True
    # trade journal
    journal_url: str = ""
    journal_key: str = ""
    journal_token: str = ""
    journal_spool: str = "journal_queue.jsonl"


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
        symbol=ins.get("symbol", d.symbol),
        tv_tickers=list(ins.get("tv_tickers", d.tv_tickers)),
        timeframe=str(ins.get("timeframe", d.timeframe)),
        tick_size=float(ins.get("tick_size", d.tick_size)),
        point_value=float(ins.get("point_value", d.point_value)),
        qty=int(od.get("qty", d.qty)),
        entry_offset_ticks=int(od.get("entry_offset_ticks", d.entry_offset_ticks)),
        stop_buffer_ticks=int(od.get("stop_buffer_ticks", d.stop_buffer_ticks)),
        entry_type=od.get("entry_type", d.entry_type),
        stop_limit_ticks=int(od.get("stop_limit_ticks", d.stop_limit_ticks)),
        late_entry_ticks=int(od.get("late_entry_ticks", d.late_entry_ticks)),
        direction=rk.get("direction", d.direction),
        both_sides=bool(rk.get("both_sides", d.both_sides)),
        risk_pct=float(rk.get("risk_pct", d.risk_pct)),
        max_qty=int(rk.get("max_qty", d.max_qty)),
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
        heartbeat_url=nt.get("heartbeat_url", d.heartbeat_url),
        notify_armed=bool(nt.get("armed", d.notify_armed)),
        notify_cancels=bool(nt.get("cancels", d.notify_cancels)),
        notify_trail=bool(nt.get("trail", d.notify_trail)),
        journal_url=jr.get("url", d.journal_url),
        journal_key=jr.get("key", d.journal_key),
        journal_token=os.environ.get("JOURNAL_TOKEN", jr.get("token", d.journal_token)),
        journal_spool=jr.get("spool", d.journal_spool),
    )
    if c.env not in ("demo", "live"):
        raise ValueError("tradovate.env must be 'demo' or 'live'")
    if c.direction not in ("both", "long", "short"):
        raise ValueError("risk.direction must be 'both', 'long' or 'short'")
    if c.entry_type not in ("Stop", "StopLimit"):
        raise ValueError("orders.entry_type must be 'Stop' or 'StopLimit'")
    if c.secret in ("", "change-me"):
        raise ValueError("set server.secret (or BOT_SECRET) to your own password, the same one as in the indicator")
    return c
