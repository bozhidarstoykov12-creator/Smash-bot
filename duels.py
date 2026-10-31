"""Duels — challenge another member to a best-of-3 Rock-Paper-Scissors duel.

Data file: duels.json
  { "<gid>": { "<duel_id>": { challenger, opponent, bet, status, created } } }

Two-phase pattern: all read-modify-write under doc_lock('economy.json')
and doc_lock('duels.json'), never awaiting inside a lock.
"""
import asyncio
import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from utils import load, save, ok, err, doc_lock, get_guild_cfg, game_reward
from i18n import t

RPS_EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def _currency(gid):
    cfg = load('economy.json').get(str(gid), {})
    return cfg.get('currency_name', 'SK'), cfg.get('currency_emoji', '🪙')


def _balance(gid, uid):
    return int(load('economy.json').get(str(gid), {}).get('balances', {}).get(str(uid), {}).get('balance', 0))


def _escrow(gid, uid, amount, name=None):
    """Atomically deduct a stake (escrow). Returns True on success."""
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


class DuelView(discord.ui.View):
    def __init__(self, cog, gid, challenger, opponent, bet, currency, emoji):
        super().__init__(timeout=120)
        self.cog = cog
        self.gid = gid
        self.challenger = challenger
        self.opponent = opponent
        self.bet = bet
        self.currency = currency
        self.emoji = emoji
        self.scores = {challenger.id: 0, opponent.id: 0}
        self.round = 0
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'duel.title'), color=discord.Color.dark_red())
        em.add_field(name="Challenger", value=f"{self.challenger.mention} — {self.scores[self.challenger.id]}", inline=True)
        em.add_field(name="Opponent", value=f"{self.opponent.mention} — {self.scores[self.opponent.id]}", inline=True)
        em.add_field(name="Bet", value=f"{self.bet} {self.currency} {self.emoji}", inline=False)
        if note:
            em.description = note
        return em

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            return await interaction.response.send_message("Only the challenged member can accept.", ephemeral=True)
        if not _escrow(self.gid, self.opponent.id, self.bet, self.opponent.name):
            return await interaction.response.send_message(
                embed=err(t(self.gid, 'duel.no_funds', bet=self.bet, currency=self.currency)), ephemeral=True)
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            embed=self._embed("⚔️ Duel accepted! Best of 3 — both players pick in DMs."), view=self)
        await self.cog.run_duel(interaction, self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            return await interaction.response.send_message("Only the challenged member can decline.", ephemeral=True)
        self.finished = True
        for c in self.children:
            c.disabled = True
        # Refund the challenger's escrow.
        game_reward(self.gid, self.challenger.id, self.bet, reason="duel_refund")
        await interaction.response.edit_message(embed=self._embed(t(self.gid, 'duel.declined')), view=self)
        self.stop()

    async def on_timeout(self):
        if not self.finished:
            self.finished = True
            game_reward(self.gid, self.challenger.id, self.bet, reason="duel_refund")


class DuelPickView(discord.ui.View):
    def __init__(self, gid, uid, on_pick):
        super().__init__(timeout=60)
        self.gid, self.uid, self.on_pick = gid, uid, on_pick

    async def _pick(self, interaction, choice):
        if interaction.user.id != self.uid:
            return await interaction.response.send_message("This is not your pick.", ephemeral=True)
        await interaction.response.edit_message(content=f"You picked {RPS_EMOJI[choice]}.", view=None)
        await self.on_pick(choice)
        self.stop()

    @discord.ui.button(label="Rock", emoji="🪨")
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "rock")

    @discord.ui.button(label="Paper", emoji="📄")
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "paper")

    @discord.ui.button(label="Scissors", emoji="✂️")
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "scissors")


class Duels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="duel", description="Challenge another member to a best-of-3 RPS duel")
    @app_commands.describe(member="Who to challenge", bet="Amount each player stakes")
    async def duel(self, interaction: discord.Interaction, member: discord.Member, bet: int):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        currency, emoji = _currency(gid)
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message(embed=err("❌ You cannot duel yourself or a bot."), ephemeral=True)
        if bet <= 0:
            return await interaction.response.send_message(embed=err("❌ Enter a positive bet."), ephemeral=True)
        if _balance(gid, uid) < bet:
            return await interaction.response.send_message(
                embed=err(t(gid, 'duel.no_funds', bet=bet, currency=currency)), ephemeral=True)
        if _balance(gid, str(member.id)) < bet:
            return await interaction.response.send_message(
                embed=err(t(gid, 'duel.no_funds', bet=bet, currency=currency)), ephemeral=True)
        # Escrow the challenger's stake now.
        if not _escrow(gid, uid, bet, interaction.user.name):
            return await interaction.response.send_message(
                embed=err(t(gid, 'duel.no_funds', bet=bet, currency=currency)), ephemeral=True)
        # Record the duel (Phase 1, no awaits).
        duel_id = uuid.uuid4().hex[:12]
        with doc_lock('duels.json'):
            data = load('duels.json')
            g = data.setdefault(gid, {})
            g[duel_id] = {"challenger": uid, "opponent": str(member.id), "bet": bet,
                          "status": "pending", "created": time.time()}
            save('duels.json', data)
        view = DuelView(self, gid, interaction.user, member, bet, currency, emoji)
        await interaction.response.send_message(
            embed=view._embed(t(gid, 'duel.desc', challenger=interaction.user.mention,
                                opponent=member.mention, bet=bet, currency=currency)),
            view=view)

    async def run_duel(self, interaction, view: DuelView):
        """Best-of-3 RPS. Both players pick via ephemeral DM-style prompts."""
        gid = view.gid
        picks = {}

        async def collect(player):
            fut = asyncio.get_event_loop().create_future()

            async def on_pick(choice):
                if not fut.done():
                    fut.set_result(choice)

            try:
                await player.send("⚔️ Your duel turn — pick your move:", view=DuelPickView(gid, player.id, on_pick))
            except discord.Forbidden:
                if not fut.done():
                    fut.set_result(None)
            try:
                return await asyncio.wait_for(fut, timeout=60)
            except asyncio.TimeoutError:
                return None

        while view.scores[view.challenger.id] < 2 and view.scores[view.opponent.id] < 2:
            view.round += 1
            c_pick, o_pick = await asyncio.gather(collect(view.challenger), collect(view.opponent))
            if c_pick is None or o_pick is None:
                # Someone failed to pick — refund both and abort.
                game_reward(gid, view.challenger.id, view.bet, reason="duel_refund")
                game_reward(gid, view.opponent.id, view.bet, reason="duel_refund")
                view.finished = True
                try:
                    await interaction.followup.send(embed=err("❌ Duel aborted — a player did not pick in time. Bets refunded."))
                except discord.HTTPException:
                    pass
                return
            if c_pick == o_pick:
                note = f"Round {view.round}: both picked {RPS_EMOJI[c_pick]} — tie!"
            elif RPS_BEATS[c_pick] == o_pick:
                view.scores[view.challenger.id] += 1
                note = f"Round {view.round}: {RPS_EMOJI[c_pick]} beats {RPS_EMOJI[o_pick]} — {view.challenger.mention} scores!"
            else:
                view.scores[view.opponent.id] += 1
                note = f"Round {view.round}: {RPS_EMOJI[o_pick]} beats {RPS_EMOJI[c_pick]} — {view.opponent.mention} scores!"
            try:
                await interaction.edit_original_response(embed=view._embed(note), view=None)
            except discord.HTTPException:
                pass

        winner = view.challenger if view.scores[view.challenger.id] > view.scores[view.opponent.id] else view.opponent
        cfg = get_guild_cfg(gid, "economy")
        pool = view.bet * 2
        payout = int(pool * float(cfg.get("duel_payout_pct", 0.95)))
        game_reward(gid, winner.id, payout, reason="duel")
        view.finished = True
        try:
            await interaction.edit_original_response(
                embed=view._embed(t(gid, 'duel.won', winner=winner.mention, amount=payout, currency=view.currency)),
                view=None)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Duels(bot))
