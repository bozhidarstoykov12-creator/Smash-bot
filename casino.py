"""Casino — 18 gambling games with a shared, race-safe economy layer.

Every game follows the two-phase pattern (PLAN Part IV):
  Phase 1 — atomic read-modify-write under doc_lock('economy.json'), NO awaits.
  Phase 2 — all network work (embeds, buttons, animations) OUTSIDE the lock.

The lock key is the FILENAME 'economy.json', never per-guild.
"""
import asyncio
import copy
import random
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils import (load, save, ok, err, info, doc_lock, get_guild_cfg,
                   game_reward, quest_progress)
from i18n import t

DEFAULT_CFG = {
    'currency_name': 'SK',
    'currency_emoji': '🪙',
    'balances': {},
}


# ══════════════════════════════════════════════════════════════════════
#  LOCAL ECONOMY HELPERS (self-contained; mirror cogs/economy.py)
# ══════════════════════════════════════════════════════════════════════

def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ensure_guild(data, gid):
    gid = str(gid)
    if gid not in data:
        data[gid] = copy.deepcopy(DEFAULT_CFG)
    else:
        for k, v in DEFAULT_CFG.items():
            if k not in data[gid]:
                data[gid][k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
    return data[gid]


def _ensure_user(cfg, uid, name=None):
    uid = str(uid)
    balances = cfg.setdefault('balances', {})
    if uid not in balances:
        balances[uid] = {'balance': 0, 'inventory': {}, 'xp_boost_until': 0, 'name': name or ''}
    if name:
        balances[uid]['name'] = name
    return balances[uid]


def _currency(gid):
    cfg = load('economy.json').get(str(gid), {})
    return cfg.get('currency_name', 'SK'), cfg.get('currency_emoji', '🪙')


def _balance(gid, uid):
    return int(load('economy.json').get(str(gid), {}).get('balances', {}).get(str(uid), {}).get('balance', 0))


def _settle(gid, uid, bet, payout, name=None):
    """Atomically deduct `bet` and add `payout`. Tracks daily gambling loss.
    Returns (ok, net, balance). Never awaits."""
    gid, uid = str(gid), str(uid)
    with doc_lock('economy.json'):
        data = load('economy.json')
        cfg = _ensure_guild(data, gid)
        u = _ensure_user(cfg, uid, name)
        bal = int(u.get('balance', 0))
        if bal < bet:
            return False, 0, bal
        u['balance'] = bal - bet + payout
        g = u.setdefault('gambling', {})
        today = _today()
        if g.get('day') != today:
            g['day'] = today
            g['daily_loss'] = 0
        net = payout - bet
        if net < 0:
            g['daily_loss'] = int(g.get('daily_loss', 0)) + (-net)
        g['last_bet'] = time.time()
        save('economy.json', data)
        return True, net, int(u['balance'])


async def _bet_guard(interaction, bet):
    """Validate a bet against range / funds / daily-loss-limit / channel.
    Returns (gid, uid, cfg, currency, emoji) or None (already responded)."""
    gid = str(interaction.guild.id)
    uid = str(interaction.user.id)
    cfg = get_guild_cfg(gid, "economy")
    currency, emoji = _currency(gid)
    ch = cfg.get("casino_channel")
    if ch and str(interaction.channel_id) != str(ch):
        await interaction.response.send_message(
            embed=err(t(gid, 'casino.wrong_channel', channel=f"<#{ch}>")), ephemeral=True)
        return None
    min_bet = int(cfg.get("casino_min_bet", 10))
    max_bet = int(cfg.get("casino_max_bet", 10000))
    if bet < min_bet or bet > max_bet:
        await interaction.response.send_message(
            embed=err(t(gid, 'casino.bet_range', min=min_bet, max=max_bet)), ephemeral=True)
        return None
    if _balance(gid, uid) < bet:
        await interaction.response.send_message(
            embed=err(t(gid, 'casino.no_funds', currency=currency)), ephemeral=True)
        return None
    limit = int(cfg.get("casino_daily_loss_limit", 0))
    if limit > 0:
        u = load('economy.json').get(gid, {}).get('balances', {}).get(uid, {})
        g = u.get('gambling', {})
        if g.get('day') == _today() and int(g.get('daily_loss', 0)) >= limit:
            await interaction.response.send_message(
                embed=err(t(gid, 'casino.daily_limit')), ephemeral=True)
            return None
    return gid, uid, cfg, currency, emoji


def _new_deck():
    deck = []
    for _ in range(4):
        for v in [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]:
            deck.append(v)
    random.shuffle(deck)
    return deck


def _hand_total(cards):
    total = sum(cards)
    aces = sum(1 for c in cards if c == 11)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


# ══════════════════════════════════════════════════════════════════════
#  INTERACTIVE VIEWS
# ══════════════════════════════════════════════════════════════════════

class BlackjackView(discord.ui.View):
    def __init__(self, gid, uid, bet, name, currency, emoji):
        super().__init__(timeout=90)
        self.gid, self.uid, self.bet, self.name = gid, uid, bet, name
        self.currency, self.emoji = currency, emoji
        self.deck = _new_deck()
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.finished = False

    def _embed(self, note=None):
        p = _hand_total(self.player)
        d = _hand_total(self.dealer)
        em = discord.Embed(title=t(self.gid, 'casino.bet_title', game="Blackjack"),
                           color=discord.Color.dark_green())
        em.add_field(name="Your hand", value=f"{self.player} = **{p}**", inline=False)
        em.add_field(name="Dealer", value=f"{self.dealer} = **{d}**", inline=False)
        em.add_field(name="Bet", value=f"{self.bet} {self.currency} {self.emoji}", inline=False)
        if note:
            em.description = note
        return em

    async def _finish(self, interaction, note):
        self.finished = True
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(embed=self._embed(note), view=self)
        self.stop()

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.green)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        self.player.append(self.deck.pop())
        if _hand_total(self.player) > 21:
            _settle(self.gid, self.uid, self.bet, 0, self.name)
            return await self._finish(interaction, t(self.gid, 'casino.busted', amount=self.bet, currency=self.currency))
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.red)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        while _hand_total(self.dealer) < 17:
            self.dealer.append(self.deck.pop())
        p, d = _hand_total(self.player), _hand_total(self.dealer)
        if d > 21 or p > d:
            payout = self.bet * 2
            _settle(self.gid, self.uid, self.bet, payout, self.name)
            note = t(self.gid, 'casino.win', amount=payout - self.bet, currency=self.currency, emoji=self.emoji)
        elif p == d:
            _settle(self.gid, self.uid, self.bet, self.bet, self.name)
            note = "🤝 Push — your bet is returned."
        else:
            _settle(self.gid, self.uid, self.bet, 0, self.name)
            note = t(self.gid, 'casino.lose', amount=self.bet, currency=self.currency)
        await self._finish(interaction, note)

    async def on_timeout(self):
        if not self.finished:
            _settle(self.gid, self.uid, self.bet, 0, self.name)


class HiloView(discord.ui.View):
    def __init__(self, gid, uid, bet, name, currency, emoji):
        super().__init__(timeout=90)
        self.gid, self.uid, self.bet, self.name = gid, uid, bet, name
        self.currency, self.emoji = currency, emoji
        self.card = random.randint(1, 13)
        self.mult = 1.0
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'casino.bet_title', game="Hi-Lo"),
                           color=discord.Color.teal())
        em.add_field(name="Current card", value=f"**{self.card}**", inline=True)
        em.add_field(name="Multiplier", value=f"x{self.mult:.2f}", inline=True)
        em.add_field(name="Potential", value=f"{int(self.bet * self.mult)} {self.currency}", inline=True)
        if note:
            em.description = note
        return em

    async def _cashout(self, interaction, note=None):
        self.finished = True
        for c in self.children:
            c.disabled = True
        payout = int(self.bet * self.mult)
        _settle(self.gid, self.uid, self.bet, payout, self.name)
        note = note or t(self.gid, 'casino.cashed_out', amount=payout, currency=self.currency, mult=f"{self.mult:.2f}")
        await interaction.response.edit_message(embed=self._embed(note), view=self)
        self.stop()

    async def _guess(self, interaction, higher):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        nxt = random.randint(1, 13)
        win = (nxt > self.card) if higher else (nxt < self.card)
        if nxt == self.card:
            win = False
        if not win:
            self.finished = True
            for c in self.children:
                c.disabled = True
            _settle(self.gid, self.uid, self.bet, 0, self.name)
            await interaction.response.edit_message(
                embed=self._embed(t(self.gid, 'casino.lose', amount=self.bet, currency=self.currency)), view=self)
            return self.stop()
        self.card = nxt
        self.mult *= 1.9
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Higher ⬆️", style=discord.ButtonStyle.green)
    async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._guess(interaction, True)

    @discord.ui.button(label="Lower ⬇️", style=discord.ButtonStyle.blurple)
    async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._guess(interaction, False)

    @discord.ui.button(label="Cash Out 💰", style=discord.ButtonStyle.gray)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        await self._cashout(interaction)

    async def on_timeout(self):
        if not self.finished:
            _settle(self.gid, self.uid, self.bet, 0, self.name)


class CrashView(discord.ui.View):
    def __init__(self, gid, uid, bet, name, currency, emoji):
        super().__init__(timeout=60)
        self.gid, self.uid, self.bet, self.name = gid, uid, bet, name
        self.currency, self.emoji = currency, emoji
        self.crash_at = round(random.uniform(1.0, 10.0), 2)
        self.mult = 1.0
        self.finished = False
        self.task = None

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'casino.bet_title', game="Crash"),
                           color=discord.Color.orange())
        em.add_field(name="Multiplier", value=f"x{self.mult:.2f}", inline=True)
        em.add_field(name="Potential", value=f"{int(self.bet * self.mult)} {self.currency}", inline=True)
        if note:
            em.description = note
        return em

    async def start(self, interaction):
        await interaction.response.send_message(embed=self._embed(t(self.gid, 'casino.playing')), view=self)
        self.message = await interaction.original_response()
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            while self.mult < self.crash_at and not self.finished:
                await asyncio.sleep(1.0)
                self.mult = round(self.mult * 1.35, 2)
                if self.finished:
                    return
                if self.mult >= self.crash_at:
                    break
                try:
                    await self.message.edit(embed=self._embed())
                except discord.HTTPException:
                    pass
            if not self.finished:
                self.finished = True
                for c in self.children:
                    c.disabled = True
                _settle(self.gid, self.uid, self.bet, 0, self.name)
                try:
                    await self.message.edit(
                        embed=self._embed(t(self.gid, 'casino.busted', amount=self.bet, currency=self.currency)),
                        view=self)
                except discord.HTTPException:
                    pass
        except asyncio.CancelledError:
            pass

    @discord.ui.button(label="Cash Out 💰", style=discord.ButtonStyle.green)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return await interaction.response.send_message("Too late!", ephemeral=True)
        self.finished = True
        if self.task:
            self.task.cancel()
        for c in self.children:
            c.disabled = True
        payout = int(self.bet * self.mult)
        _settle(self.gid, self.uid, self.bet, payout, self.name)
        await interaction.response.edit_message(
            embed=self._embed(t(self.gid, 'casino.cashed_out', amount=payout, currency=self.currency, mult=f"{self.mult:.2f}")),
            view=self)
        self.stop()

    async def on_timeout(self):
        if not self.finished:
            self.finished = True
            if self.task:
                self.task.cancel()
            _settle(self.gid, self.uid, self.bet, 0, self.name)


class MinesButton(discord.ui.Button):
    def __init__(self, idx, view):
        super().__init__(label="❓", style=discord.ButtonStyle.secondary, row=idx // 5)
        self.idx = idx
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        await self.view_ref.reveal(interaction, self.idx)


class MinesView(discord.ui.View):
    def __init__(self, gid, uid, bet, name, currency, emoji, mines=3):
        super().__init__(timeout=120)
        self.gid, self.uid, self.bet, self.name = gid, uid, bet, name
        self.currency, self.emoji = currency, emoji
        self.mine_positions = set(random.sample(range(25), mines))
        self.revealed = set()
        self.mult = 1.0
        self.finished = False
        for i in range(25):
            self.add_item(MinesButton(i, self))

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'casino.bet_title', game="Mines"),
                           color=discord.Color.dark_red())
        em.add_field(name="Safe tiles", value=str(len(self.revealed)), inline=True)
        em.add_field(name="Multiplier", value=f"x{self.mult:.2f}", inline=True)
        em.add_field(name="Potential", value=f"{int(self.bet * self.mult)} {self.currency}", inline=True)
        if note:
            em.description = note
        return em

    async def reveal(self, interaction, idx):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished or idx in self.revealed:
            return await interaction.response.send_message("Already revealed.", ephemeral=True)
        btn = next(c for c in self.children if getattr(c, 'idx', -1) == idx)
        if idx in self.mine_positions:
            self.finished = True
            for c in self.children:
                c.disabled = True
                if getattr(c, 'idx', -1) in self.mine_positions:
                    c.label = "💥"
            _settle(self.gid, self.uid, self.bet, 0, self.name)
            return await interaction.response.edit_message(
                embed=self._embed(t(self.gid, 'casino.busted', amount=self.bet, currency=self.currency)), view=self)
        self.revealed.add(idx)
        btn.label = "💎"
        btn.disabled = True
        self.mult = round(self.mult * 1.25, 2)
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Cash Out 💰", style=discord.ButtonStyle.green, row=5)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        for c in self.children:
            c.disabled = True
        payout = int(self.bet * self.mult)
        _settle(self.gid, self.uid, self.bet, payout, self.name)
        await interaction.response.edit_message(
            embed=self._embed(t(self.gid, 'casino.cashed_out', amount=payout, currency=self.currency, mult=f"{self.mult:.2f}")),
            view=self)
        self.stop()

    async def on_timeout(self):
        if not self.finished:
            self.finished = True
            _settle(self.gid, self.uid, self.bet, 0, self.name)


class TowerView(discord.ui.View):
    def __init__(self, gid, uid, bet, name, currency, emoji):
        super().__init__(timeout=120)
        self.gid, self.uid, self.bet, self.name = gid, uid, bet, name
        self.currency, self.emoji = currency, emoji
        self.floor = 0
        self.mult = 1.0
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'casino.bet_title', game="Tower"),
                           color=discord.Color.purple())
        em.add_field(name="Floor", value=str(self.floor), inline=True)
        em.add_field(name="Multiplier", value=f"x{self.mult:.2f}", inline=True)
        em.add_field(name="Potential", value=f"{int(self.bet * self.mult)} {self.currency}", inline=True)
        if note:
            em.description = note
        return em

    @discord.ui.button(label="Climb ⬆️", style=discord.ButtonStyle.green)
    async def climb(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        if random.random() < 0.30:
            self.finished = True
            for c in self.children:
                c.disabled = True
            _settle(self.gid, self.uid, self.bet, 0, self.name)
            return await interaction.response.edit_message(
                embed=self._embed(t(self.gid, 'casino.busted', amount=self.bet, currency=self.currency)), view=self)
        self.floor += 1
        self.mult = round(self.mult * 1.5, 2)
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Cash Out 💰", style=discord.ButtonStyle.gray)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        for c in self.children:
            c.disabled = True
        payout = int(self.bet * self.mult)
        _settle(self.gid, self.uid, self.bet, payout, self.name)
        await interaction.response.edit_message(
            embed=self._embed(t(self.gid, 'casino.cashed_out', amount=payout, currency=self.currency, mult=f"{self.mult:.2f}")),
            view=self)
        self.stop()

    async def on_timeout(self):
        if not self.finished:
            self.finished = True
            _settle(self.gid, self.uid, self.bet, 0, self.name)


class StreakView(discord.ui.View):
    def __init__(self, gid, uid, bet, name, currency, emoji):
        super().__init__(timeout=90)
        self.gid, self.uid, self.bet, self.name = gid, uid, bet, name
        self.currency, self.emoji = currency, emoji
        self.streak = 0
        self.mult = 1.0
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'casino.bet_title', game="Streak"),
                           color=discord.Color.gold())
        em.add_field(name="Streak", value=str(self.streak), inline=True)
        em.add_field(name="Multiplier", value=f"x{self.mult:.2f}", inline=True)
        em.add_field(name="Potential", value=f"{int(self.bet * self.mult)} {self.currency}", inline=True)
        if note:
            em.description = note
        return em

    async def _flip(self, interaction, call):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        result = random.choice(["heads", "tails"])
        if result != call:
            self.finished = True
            for c in self.children:
                c.disabled = True
            _settle(self.gid, self.uid, self.bet, 0, self.name)
            return await interaction.response.edit_message(
                embed=self._embed(t(self.gid, 'casino.lose', amount=self.bet, currency=self.currency)), view=self)
        self.streak += 1
        self.mult = round(self.mult * 1.9, 2)
        await interaction.response.edit_message(embed=self._embed(f"🪙 {result.title()}!"), view=self)

    @discord.ui.button(label="Heads", style=discord.ButtonStyle.blurple)
    async def heads(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, "heads")

    @discord.ui.button(label="Tails", style=discord.ButtonStyle.blurple)
    async def tails(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, "tails")

    @discord.ui.button(label="Cash Out 💰", style=discord.ButtonStyle.green)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        for c in self.children:
            c.disabled = True
        payout = int(self.bet * self.mult)
        _settle(self.gid, self.uid, self.bet, payout, self.name)
        await interaction.response.edit_message(
            embed=self._embed(t(self.gid, 'casino.cashed_out', amount=payout, currency=self.currency, mult=f"{self.mult:.2f}")),
            view=self)
        self.stop()

    async def on_timeout(self):
        if not self.finished:
            self.finished = True
            _settle(self.gid, self.uid, self.bet, 0, self.name)


# ══════════════════════════════════════════════════════════════════════
#  COG
# ══════════════════════════════════════════════════════════════════════

class Casino(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    casino = app_commands.Group(name="casino", description="Casino games — bet your currency")

    # ── Instant games ───────────────────────────────────────────────────

    @casino.command(name="coinflip", description="Bet on a coin flip (2x)")
    @app_commands.describe(bet="Amount to bet", call="Heads or tails")
    @app_commands.choices(call=[
        app_commands.Choice(name="Heads", value="heads"),
        app_commands.Choice(name="Tails", value="tails"),
    ])
    async def coinflip(self, interaction: discord.Interaction, bet: int, call: app_commands.Choice[str]):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        result = random.choice(["heads", "tails"])
        win = result == call.value
        payout = bet * 2 if win else 0
        _settle(gid, uid, bet, payout, interaction.user.name)
        if win:
            desc = t(gid, 'casino.win', amount=bet, currency=currency, emoji=emoji)
        else:
            desc = t(gid, 'casino.lose', amount=bet, currency=currency)
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Coinflip"),
                           description=f"🪙 **{result.title()}**\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="slots", description="Spin the slot machine")
    @app_commands.describe(bet="Amount to bet")
    async def slots(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        reels = ["🍒", "🍋", "🔔", "💎", "7️⃣", "🍀"]
        spin = [random.choice(reels) for _ in range(3)]
        if spin[0] == spin[1] == spin[2]:
            mult = {"💎": 20, "7️⃣": 10, "🔔": 6, "🍀": 5, "🍋": 4, "🍒": 3}[spin[0]]
        elif spin[0] == spin[1] or spin[1] == spin[2] or spin[0] == spin[2]:
            mult = 2
        else:
            mult = 0
        payout = bet * mult
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if mult > 1
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Slots"),
                           description=f"| {' | '.join(spin)} |\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="dice", description="Roll two dice and bet on the total")
    @app_commands.describe(bet="Amount to bet", choice="Over 7, under 7, or exactly 7")
    @app_commands.choices(choice=[
        app_commands.Choice(name="Over 7 (2x)", value="over"),
        app_commands.Choice(name="Under 7 (2x)", value="under"),
        app_commands.Choice(name="Exactly 7 (5x)", value="seven"),
    ])
    async def dice(self, interaction: discord.Interaction, bet: int, choice: app_commands.Choice[str]):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        d1, d2 = random.randint(1, 6), random.randint(1, 6)
        total = d1 + d2
        if choice.value == "over":
            win, mult = total > 7, 2
        elif choice.value == "under":
            win, mult = total < 7, 2
        else:
            win, mult = total == 7, 5
        payout = bet * mult if win else 0
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if win
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Dice"),
                           description=f"🎲 {d1} + {d2} = **{total}**\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="roulette", description="Bet on a roulette spin")
    @app_commands.describe(bet="Amount to bet", choice="Red, black, green, or a number 0-36")
    async def roulette(self, interaction: discord.Interaction, bet: int, choice: str):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        choice = choice.strip().lower()
        number = random.randint(0, 36)
        reds = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        if number == 0:
            color = "green"
        elif number in reds:
            color = "red"
        else:
            color = "black"
        mult = 0
        if choice in ("red", "black", "green") and choice == color:
            mult = 36 if color == "green" else 2
        elif choice.isdigit() and int(choice) == number:
            mult = 36
        payout = bet * mult
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if mult > 0
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Roulette"),
                           description=f"🎡 **{number} {color}**\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="keno", description="Pick numbers and hope they are drawn")
    @app_commands.describe(bet="Amount to bet", numbers="Comma-separated numbers 1-40 (e.g. 3,7,12)")
    async def keno(self, interaction: discord.Interaction, bet: int, numbers: str):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        try:
            picks = {int(x) for x in numbers.replace(" ", "").split(",") if x}
        except ValueError:
            return await interaction.response.send_message(embed=err("❌ Invalid numbers."), ephemeral=True)
        if not picks or len(picks) > 10 or any(n < 1 or n > 40 for n in picks):
            return await interaction.response.send_message(embed=err("❌ Pick 1-10 numbers between 1 and 40."), ephemeral=True)
        drawn = set(random.sample(range(1, 41), 10))
        hits = len(picks & drawn)
        mult = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4, 5: 8, 6: 15, 7: 30, 8: 60, 9: 120, 10: 250}.get(hits, 0)
        payout = bet * mult
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if mult > 1
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Keno"),
                           description=f"Your picks: {sorted(picks)}\nDrawn: {sorted(drawn)}\nHits: **{hits}**\n{desc}",
                           color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="wheel", description="Spin the prize wheel")
    @app_commands.describe(bet="Amount to bet")
    async def wheel(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        segments = [0, 0, 0, 1.5, 1.5, 2, 2, 3, 5, 10]
        mult = random.choice(segments)
        payout = int(bet * mult)
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if mult > 1
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Wheel"),
                           description=f"🎡 Landed on **x{mult}**\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="scratch", description="Buy a scratch card")
    @app_commands.describe(bet="Amount to bet")
    async def scratch(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        symbols = ["🍒", "💎", "7️⃣", "🔔", "⭐"]
        card = [random.choice(symbols) for _ in range(6)]
        counts = {s: card.count(s) for s in symbols}
        best = max(counts.values())
        mult = {1: 0, 2: 1, 3: 3, 4: 8, 5: 20, 6: 50}.get(best, 0)
        payout = bet * mult
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if mult > 1
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Scratch"),
                           description=f"🎫 {' '.join(card)}\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="limbo", description="Pick a target multiplier — win if the roll beats it")
    @app_commands.describe(bet="Amount to bet", target="Target multiplier (e.g. 2.5)")
    async def limbo(self, interaction: discord.Interaction, bet: int, target: float):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        if target < 1.01 or target > 100:
            return await interaction.response.send_message(embed=err("❌ Target must be between 1.01 and 100."), ephemeral=True)
        roll = round(random.uniform(1.0, 100.0), 2)
        win = roll >= target
        payout = int(bet * target) if win else 0
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if win
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Limbo"),
                           description=f"Rolled **x{roll}** (target x{target})\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="plinko", description="Drop a ball down the plinko board")
    @app_commands.describe(bet="Amount to bet")
    async def plinko(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        slots = [10, 3, 1.5, 0.5, 0.2, 0.5, 1.5, 3, 10]
        idx = sum(random.choice([-1, 1]) for _ in range(8)) + 4
        idx = max(0, min(len(slots) - 1, idx))
        mult = slots[idx]
        payout = int(bet * mult)
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if mult > 1
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Plinko"),
                           description=f"🔵 Landed in slot **x{mult}**\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="race", description="Bet on a racer")
    @app_commands.describe(bet="Amount to bet", racer="Racer number 1-5")
    async def race(self, interaction: discord.Interaction, bet: int, racer: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        if racer < 1 or racer > 5:
            return await interaction.response.send_message(embed=err("❌ Pick a racer between 1 and 5."), ephemeral=True)
        winner = random.randint(1, 5)
        win = winner == racer
        payout = bet * 5 if win else 0
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if win
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Race"),
                           description=f"🏁 Racer **{winner}** wins!\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    @casino.command(name="bossraid", description="Join a raid on the boss for a chance at loot")
    @app_commands.describe(bet="Amount to bet")
    async def bossraid(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        roll = random.random()
        if roll < 0.05:
            mult = 10
        elif roll < 0.25:
            mult = 3
        elif roll < 0.55:
            mult = 1.5
        else:
            mult = 0
        payout = int(bet * mult)
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if mult > 1
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Boss Raid"),
                           description=f"⚔️ The boss dropped **x{mult}** loot!\n{desc}", color=discord.Color.dark_red())
        await interaction.response.send_message(embed=em)

    @casino.command(name="predict", description="Predict a number between 1 and 10")
    @app_commands.describe(bet="Amount to bet", number="Your prediction 1-10")
    async def predict(self, interaction: discord.Interaction, bet: int, number: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        if number < 1 or number > 10:
            return await interaction.response.send_message(embed=err("❌ Pick a number between 1 and 10."), ephemeral=True)
        result = random.randint(1, 10)
        win = result == number
        payout = bet * 9 if win else 0
        _settle(gid, uid, bet, payout, interaction.user.name)
        desc = (t(gid, 'casino.win', amount=payout - bet, currency=currency, emoji=emoji) if win
                else t(gid, 'casino.lose', amount=bet, currency=currency))
        em = discord.Embed(title=t(gid, 'casino.bet_title', game="Predict"),
                           description=f"🔮 The number was **{result}**\n{desc}", color=discord.Color.gold())
        await interaction.response.send_message(embed=em)

    # ── Interactive games ───────────────────────────────────────────────

    @casino.command(name="blackjack", description="Play blackjack against the dealer")
    @app_commands.describe(bet="Amount to bet")
    async def blackjack(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        view = BlackjackView(gid, uid, bet, interaction.user.name, currency, emoji)
        await interaction.response.send_message(embed=view._embed(), view=view)

    @casino.command(name="hilo", description="Guess if the next card is higher or lower")
    @app_commands.describe(bet="Amount to bet")
    async def hilo(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        view = HiloView(gid, uid, bet, interaction.user.name, currency, emoji)
        await interaction.response.send_message(embed=view._embed(), view=view)

    @casino.command(name="crash", description="Cash out before the multiplier crashes")
    @app_commands.describe(bet="Amount to bet")
    async def crash(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        view = CrashView(gid, uid, bet, interaction.user.name, currency, emoji)
        await view.start(interaction)

    @casino.command(name="mines", description="Reveal safe tiles and cash out before hitting a mine")
    @app_commands.describe(bet="Amount to bet", mines="Number of mines (1-10)")
    async def mines(self, interaction: discord.Interaction, bet: int, mines: int = 3):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        mines = max(1, min(10, mines))
        view = MinesView(gid, uid, bet, interaction.user.name, currency, emoji, mines)
        await interaction.response.send_message(embed=view._embed(), view=view)

    @casino.command(name="tower", description="Climb the tower and cash out before you fall")
    @app_commands.describe(bet="Amount to bet")
    async def tower(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        view = TowerView(gid, uid, bet, interaction.user.name, currency, emoji)
        await interaction.response.send_message(embed=view._embed(), view=view)

    @casino.command(name="streak", description="Guess coin flips in a row to grow your multiplier")
    @app_commands.describe(bet="Amount to bet")
    async def streak(self, interaction: discord.Interaction, bet: int):
        g = await _bet_guard(interaction, bet)
        if not g:
            return
        gid, uid, cfg, currency, emoji = g
        view = StreakView(gid, uid, bet, interaction.user.name, currency, emoji)
        await interaction.response.send_message(embed=view._embed(), view=view)


async def setup(bot):
    await bot.add_cog(Casino(bot))
