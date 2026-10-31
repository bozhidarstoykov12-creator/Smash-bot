"""Lottery — buy tickets, weekly Friday draw at 20:00 UTC.

Data file: lottery.json
  { "<gid>": { "pool": int, "tickets": { "<uid>": int }, "last_draw": ts } }

Two-phase pattern: all read-modify-write under doc_lock('lottery.json')
(and doc_lock('economy.json') for payouts), never awaiting inside a lock.
"""
import random
import time
from datetime import datetime, time as dt_time, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import load, save, ok, err, info, doc_lock, get_guild_cfg, game_reward
from i18n import t


def _currency(gid):
    cfg = load('economy.json').get(str(gid), {})
    return cfg.get('currency_name', 'SK'), cfg.get('currency_emoji', '🪙')


def _ensure_guild(data, gid):
    gid = str(gid)
    if gid not in data:
        data[gid] = {"pool": 0, "tickets": {}, "last_draw": 0}
    return data[gid]


def _balance(gid, uid):
    return int(load('economy.json').get(str(gid), {}).get('balances', {}).get(str(uid), {}).get('balance', 0))


def _spend(gid, uid, amount, name=None):
    """Atomically deduct `amount` from a wallet. Returns True on success."""
    gid, uid = str(gid), str(uid)
    with doc_lock('economy.json'):
        data = load('economy.json')
        gcfg = data.setdefault(gid, {'balances': {}})
        balances = gcfg.setdefault('balances', {})
        u = balances.setdefault(uid, {'balance': 0, 'inventory': {}, 'xp_boost_until': 0, 'name': name or ''})
        if int(u.get('balance', 0)) < amount:
            return False
        u['balance'] = int(u.get('balance', 0)) - amount
        save('economy.json', data)
        return True


class Lottery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.friday_draw.start()

    def cog_unload(self):
        self.friday_draw.cancel()

    lottery = app_commands.Group(name="lottery", description="Buy lottery tickets and check the jackpot")

    @lottery.command(name="buy", description="Buy one or more lottery tickets")
    @app_commands.describe(count="How many tickets to buy (default 1)")
    async def buy(self, interaction: discord.Interaction, count: int = 1):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_guild_cfg(gid, "economy")
        currency, emoji = _currency(gid)
        price = int(cfg.get("lottery_ticket_price", 50))
        if count < 1 or count > 100:
            return await interaction.response.send_message(embed=err("❌ Buy between 1 and 100 tickets."), ephemeral=True)
        cost = price * count
        if _balance(gid, uid) < cost:
            return await interaction.response.send_message(
                embed=err(t(gid, 'casino.no_funds', currency=currency)), ephemeral=True)
        if not _spend(gid, uid, cost, interaction.user.name):
            return await interaction.response.send_message(
                embed=err(t(gid, 'casino.no_funds', currency=currency)), ephemeral=True)
        # Phase 1: atomic RMW under the lottery lock (no awaits).
        with doc_lock('lottery.json'):
            data = load('lottery.json')
            g = _ensure_guild(data, gid)
            g['tickets'][uid] = int(g['tickets'].get(uid, 0)) + count
            g['pool'] = int(g.get('pool', 0)) + cost
            save('lottery.json', data)
        await interaction.response.send_message(embed=ok(
            t(gid, 'lottery.title'),
            t(gid, 'lottery.bought', n=count, cost=cost, currency=currency)))

    @lottery.command(name="info", description="View the current jackpot and your tickets")
    async def info_cmd(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        currency, emoji = _currency(gid)
        g = load('lottery.json').get(gid, {})
        pool = int(g.get('pool', 0))
        mine = int(g.get('tickets', {}).get(uid, 0))
        await interaction.response.send_message(embed=info(
            t(gid, 'lottery.title'),
            t(gid, 'lottery.info', pool=pool, currency=currency, emoji=emoji, mine=mine)))

    @tasks.loop(time=dt_time(hour=20, minute=0, tzinfo=timezone.utc))
    async def friday_draw(self):
        """Every Friday at 20:00 UTC, draw a winner and pay out 90% of the pool."""
        await self.bot.wait_until_ready()
        if datetime.now(timezone.utc).weekday() != 4:  # 4 = Friday
            return
        with doc_lock('lottery.json'):
            data = load('lottery.json')
            draws = []
            for gid, g in data.items():
                if not isinstance(g, dict):
                    continue
                tickets = g.get('tickets', {})
                pool = int(g.get('pool', 0))
                if not tickets or pool <= 0:
                    continue
                entries = []
                for uid, n in tickets.items():
                    entries.extend([uid] * int(n))
                winner = random.choice(entries)
                cfg = get_guild_cfg(gid, "economy")
                payout = int(pool * float(cfg.get("lottery_payout_pct", 0.90)))
                draws.append((gid, winner, payout))
                g['tickets'] = {}
                g['pool'] = 0
                g['last_draw'] = time.time()
            save('lottery.json', data)
        # Phase 2: payouts + announcements outside the lock.
        for gid, winner, payout in draws:
            game_reward(gid, winner, payout, reason="lottery")
            currency, emoji = _currency(gid)
            guild = self.bot.get_guild(int(gid))
            if not guild:
                continue
            channel = guild.system_channel
            if channel is None:
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        channel = ch
                        break
            if channel:
                try:
                    await channel.send(embed=ok(
                        t(gid, 'lottery.title'),
                        t(gid, 'lottery.won', winner=f"<@{winner}>", amount=payout, currency=currency)))
                except discord.HTTPException:
                    pass


async def setup(bot):
    await bot.add_cog(Lottery(bot))
