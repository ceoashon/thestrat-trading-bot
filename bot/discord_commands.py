"""The /day, /week, /month and /total commands in Discord.

The webhook can only post, so these use a small Discord bot. It connects out to Discord, so it needs no public address.
It finds the channel from the webhook and only answers there. It runs apart from the trading: if Discord is down or this
part fails, orders carry on and this keeps trying to reconnect."""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import discord
import requests
from discord import app_commands

from .pnl import PnlLedger

log = logging.getLogger("commands")

COMMANDS = {"day": "P&L for today's trading day", "week": "P&L for this week",
            "month": "P&L for this month", "total": "P&L for the whole account"}
READY = "✅ Discord commands are ready: /day /week /month /total"


def build_tree(client: discord.Client, ledger: PnlLedger, guild: discord.Object, channel_id: int) -> app_commands.CommandTree:
    tree = app_commands.CommandTree(client)

    async def answer(interaction: discord.Interaction, period: str) -> None:
        if interaction.channel_id != channel_id:
            await interaction.response.send_message(f"Use this in <#{channel_id}>.", ephemeral=True)
            return
        await interaction.response.defer()  # Discord only waits 3 seconds for a first answer
        try:
            text = await asyncio.to_thread(ledger.report, period)
        except Exception as e:
            log.warning("/%s failed: %s", period, e)
            text = "Could not reach Tradovate just now. Try again in a minute."
        await interaction.followup.send(text)

    def command(period: str):
        async def callback(interaction: discord.Interaction) -> None:
            await answer(interaction, period)
        return callback

    for name, about in COMMANDS.items():
        tree.add_command(app_commands.Command(name=name, description=about, callback=command(name)), guild=guild)
    return tree


class DiscordCommands:
    def __init__(self, token: str, webhook: str, ledger: PnlLedger, notifier) -> None:
        self.token = token
        self.webhook = webhook
        self.ledger = ledger
        self.n = notifier

    def start(self) -> None:
        threading.Thread(target=self._run, name="commands", daemon=True).start()

    def _run(self) -> None:
        logging.getLogger("discord").setLevel(logging.WARNING)
        while True:
            try:
                guild_id, channel_id = self._webhook_channel()
                asyncio.run(self._serve(guild_id, channel_id))
            except discord.LoginFailure:
                self.n.send("❗ Discord refused the bot token, so /day /week /month /total are off. Trading is not affected.")
                return
            except Exception as e:
                log.warning("Discord commands stopped (%s), trying again in a minute", e)
            time.sleep(60)

    def _webhook_channel(self) -> tuple[int, int]:
        try:
            r = requests.get(self.webhook, timeout=15)
            r.raise_for_status()
            info = r.json()
            return int(info["guild_id"]), int(info["channel_id"])
        except (requests.RequestException, ValueError, KeyError) as e:
            raise RuntimeError(f"could not read the webhook: {type(e).__name__}") from None  # its error text holds the secret address

    async def _serve(self, guild_id: int, channel_id: int) -> None:
        client = discord.Client(intents=discord.Intents(guilds=True))
        guild = discord.Object(guild_id)
        tree = build_tree(client, self.ledger, guild, channel_id)
        announced = False

        async def sync() -> None:
            nonlocal announced
            if not announced:
                await tree.sync(guild=guild)
                announced = True
                self.n.send(READY)

        @client.event
        async def on_ready() -> None:
            if client.get_guild(guild_id):
                await sync()
            elif not announced:
                self.n.send("🤖 One last step for /day /week /month /total. Add the bot to this server: "
                            f"https://discord.com/oauth2/authorize?client_id={client.application_id}&scope=bot+applications.commands&permissions=0")

        @client.event
        async def on_guild_join(g: discord.Guild) -> None:
            if g.id == guild_id:
                await sync()

        async with client:
            await client.start(self.token)
