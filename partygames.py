"""Party Games — 6 social / multiplayer games.

Games: /mafia /spyfall /twotruths /mostlikely /neverhave /battleship

These are hazard-free social games. Rewards go through game_reward() (honours
the guild daily cap). All state mutations happen under doc_lock() and never
await inside a lock (two-phase pattern).
"""
import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils import load, save, ok, err, info, doc_lock, get_guild_cfg, game_reward, quest_progress
from i18n import t


def _currency(gid):
    cfg = load('economy.json').get(str(gid), {})
    return cfg.get('currency_name', 'SK'), cfg.get('currency_emoji', '\U0001FA99')


def _enabled(gid, key):
    return bool(get_guild_cfg(gid, "games").get(key, True))


async def _reward(interaction, gid, uid, amount, reason):
    granted = game_reward(gid, uid, amount, reason=reason)
    quest_progress(gid, uid, "game_played", 1)
    if granted > 0:
        currency, emoji = _currency(gid)
        try:
            await interaction.followup.send(embed=info(t(gid, 'game.reward', amount=granted, currency=currency, emoji=emoji)))
        except discord.HTTPException:
            pass


# ══════════════════════════════════════════════════════════════════════════
#  MAFIA
# ══════════════════════════════════════════════════════════════════════════

MAFIA_ROLES = {
    "mafia": "\U0001F52A Mafia",
    "doctor": "\U0001F489 Doctor",
    "detective": "\U0001F575\uFE0F Detective",
    "villager": "\U0001F464 Villager",
}


class MafiaView(discord.ui.View):
    def __init__(self, gid, host_id):
        super().__init__(timeout=60)
        self.gid = gid
        self.host_id = host_id
        self.players = []

    @discord.ui.button(label="Join", style=discord.ButtonStyle.green, emoji="\u2705")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.players:
            return await interaction.response.send_message("You already joined.", ephemeral=True)
        self.players.append(interaction.user.id)
        await interaction.response.send_message(f"Joined! ({len(self.players)} players)", ephemeral=True)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.blurple)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message("Only the host can start.", ephemeral=True)
        if len(self.players) < 4:
            return await interaction.response.send_message("Need at least 4 players.", ephemeral=True)
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        await _run_mafia(interaction, self.gid, self.players)


async def _run_mafia(interaction, gid, players):
    n = len(players)
    roles = ["mafia"] + ["doctor", "detective"] + ["villager"] * (n - 3)
    random.shuffle(roles)
    assignment = dict(zip(players, roles))
    # DM roles
    for uid, role in assignment.items():
        try:
            user = await interaction.client.fetch_user(uid)
            await user.send(embed=info(t(gid, 'party.mafia_title'),
                                       f"Your role: **{MAFIA_ROLES[role]}**"))
        except discord.HTTPException:
            pass
    await interaction.followup.send(embed=info(t(gid, 'party.mafia_title'),
                                               f"Roles sent via DM to {n} players. Discuss in chat, then vote!"))
    for uid in players:
        game_reward(gid, uid, 40, reason="mafia")
        quest_progress(gid, uid, "game_played", 1)


# ══════════════════════════════════════════════════════════════════════════
#  SPYFALL
# ══════════════════════════════════════════════════════════════════════════

SPY_LOCATIONS = ["Airplane", "Bank", "Beach", "Casino", "Circus", "Hospital",
                 "Hotel", "Movie Studio", "Ocean Liner", "Passenger Train",
                 "Polar Station", "Restaurant", "School", "Space Station", "Submarine"]


class SpyfallView(discord.ui.View):
    def __init__(self, gid, host_id):
        super().__init__(timeout=60)
        self.gid = gid
        self.host_id = host_id
        self.players = []

    @discord.ui.button(label="Join", style=discord.ButtonStyle.green, emoji="\u2705")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.players:
            return await interaction.response.send_message("You already joined.", ephemeral=True)
        self.players.append(interaction.user.id)
        await interaction.response.send_message(f"Joined! ({len(self.players)} players)", ephemeral=True)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.blurple)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message("Only the host can start.", ephemeral=True)
        if len(self.players) < 3:
            return await interaction.response.send_message("Need at least 3 players.", ephemeral=True)
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        location = random.choice(SPY_LOCATIONS)
        spy = random.choice(self.players)
        for uid in self.players:
            try:
                user = await interaction.client.fetch_user(uid)
                if uid == spy:
                    await user.send(embed=info(t(self.gid, 'party.spyfall_title'),
                                               "You are the **SPY**! Guess the location."))
                else:
                    await user.send(embed=info(t(self.gid, 'party.spyfall_title'),
                                               f"The location is **{location}**. Find the spy!"))
            except discord.HTTPException:
                pass
        await interaction.followup.send(embed=info(t(self.gid, 'party.spyfall_title'),
                                                   "Roles sent via DM. Ask questions and find the spy!"))
        for uid in self.players:
            game_reward(self.gid, uid, 40, reason="spyfall")
            quest_progress(self.gid, uid, "game_played", 1)


# ══════════════════════════════════════════════════════════════════════════
#  TWO TRUTHS & A LIE
# ══════════════════════════════════════════════════════════════════════════

class TwoTruthsModal(discord.ui.Modal, title="Two Truths & a Lie"):
    truth1 = discord.ui.TextInput(label="Truth 1", max_length=100)
    truth2 = discord.ui.TextInput(label="Truth 2", max_length=100)
    lie = discord.ui.TextInput(label="Lie", max_length=100)

    def __init__(self, view):
        super().__init__()
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.statements = [str(self.truth1), str(self.truth2), str(self.lie)]
        random.shuffle(self.view_ref.statements)
        self.view_ref.lie_index = self.view_ref.statements.index(str(self.lie))
        await interaction.response.send_message("Submitted! Others can now guess.", ephemeral=True)
        await self.view_ref.post(interaction)


class TwoTruthsView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=180)
        self.gid, self.uid = gid, uid
        self.statements = None
        self.lie_index = None
        self.guesses = {}

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.green)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        await interaction.response.send_modal(TwoTruthsModal(self))

    async def post(self, interaction):
        lines = "\n".join(f"**{i+1}.** {s}" for i, s in enumerate(self.statements))
        embed = info(t(self.gid, 'party.twotruths_title'),
                     f"Which is the lie?\n{lines}\n\nClick a number to guess!")
        self.clear_items()
        for i in range(3):
            b = discord.ui.Button(label=str(i + 1), style=discord.ButtonStyle.secondary)
            b.callback = self._make_guess(i)
            self.add_item(b)
        try:
            await interaction.followup.send(embed=embed, view=self)
        except discord.HTTPException:
            pass

    def _make_guess(self, idx):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id == int(self.uid):
                return await interaction.response.send_message("You can't guess your own!", ephemeral=True)
            self.guesses[interaction.user.id] = idx
            correct = idx == self.lie_index
            await interaction.response.send_message(
                "Correct!" if correct else "Wrong!", ephemeral=True)
            if correct:
                game_reward(self.gid, interaction.user.id, 25, reason="twotruths")
                quest_progress(self.gid, interaction.user.id, "game_played", 1)
        return cb


# ══════════════════════════════════════════════════════════════════════════
#  MOST LIKELY TO
# ══════════════════════════════════════════════════════════════════════════

MOST_LIKELY_PROMPTS = [
    "become famous", "sleep through an alarm", "win the lottery",
    "eat the last slice of pizza", "survive a zombie apocalypse",
    "become a millionaire", "forget their own birthday", "adopt 10 cats",
    "start a band", "become a meme", "travel the world", "write a book",
]


class MostLikelyView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=120)
        self.gid, self.uid = gid, uid
        self.prompt = random.choice(MOST_LIKELY_PROMPTS)
        self.votes = {}

    @discord.ui.button(label="Vote", style=discord.ButtonStyle.green)
    async def vote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"Who is most likely to **{self.prompt}**? Reply with @mention in chat!", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════
#  NEVER HAVE I EVER
# ══════════════════════════════════════════════════════════════════════════

NEVER_HAVE_PROMPTS = [
    "eaten food that fell on the floor", "sung in the shower",
    "pulled an all-nighter", "lied about my age", "cried at a movie",
    "forgotten someone's name mid-conversation", "talked to myself",
    "binge-watched a whole series in one day", "danced in public",
    "pretended to be sick to skip something",
]


class NeverHaveView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=120)
        self.gid, self.uid = gid, uid
        self.prompt = random.choice(NEVER_HAVE_PROMPTS)
        self.done = set()

    @discord.ui.button(label="I have \U0001F64B", style=discord.ButtonStyle.green)
    async def have(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.done.add(interaction.user.id)
        await interaction.response.send_message("Noted! \U0001F64B", ephemeral=True)

    @discord.ui.button(label="Never \U0001F645", style=discord.ButtonStyle.secondary)
    async def never(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Clean record! \U0001F645", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════
#  BATTLESHIP
# ══════════════════════════════════════════════════════════════════════════

BS_SIZE = 5
BS_SHIPS = [3, 2, 2]


def _bs_empty():
    return [[0] * BS_SIZE for _ in range(BS_SIZE)]


def _bs_place():
    grid = _bs_empty()
    for size in BS_SHIPS:
        for _ in range(100):
            horiz = random.choice([True, False])
            r = random.randint(0, BS_SIZE - 1)
            c = random.randint(0, BS_SIZE - 1)
            cells = []
            ok_place = True
            for i in range(size):
                rr, cc = (r, c + i) if horiz else (r + i, c)
                if rr >= BS_SIZE or cc >= BS_SIZE or grid[rr][cc]:
                    ok_place = False
                    break
                cells.append((rr, cc))
            if ok_place:
                for rr, cc in cells:
                    grid[rr][cc] = 1
                break
    return grid


def _bs_render(grid, shots):
    rows = []
    for r in range(BS_SIZE):
        row = []
        for c in range(BS_SIZE):
            if (r, c) in shots:
                row.append("\U0001F7E5" if grid[r][c] else "\u2B1C")
            else:
                row.append("\U0001F7E6")
        rows.append("".join(row))
    return "\n".join(rows)


class BattleshipView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=180)
        self.gid, self.uid = gid, uid
        self.grid = _bs_place()
        self.shots = set()
        self.hits = 0
        self.total = sum(BS_SHIPS)

    def _embed(self):
        return info(t(self.gid, 'party.battleship_title'),
                    f"{_bs_render(self.grid, self.shots)}\n\nHits: **{self.hits}/{self.total}**")

    async def _fire(self, interaction, r, c):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("Not your game.", ephemeral=True)
        if (r, c) in self.shots:
            return await interaction.response.send_message("Already fired there.", ephemeral=True)
        self.shots.add((r, c))
        if self.grid[r][c]:
            self.hits += 1
        if self.hits >= self.total:
            for ch in self.children:
                ch.disabled = True
            await interaction.response.edit_message(
                embed=ok(t(self.gid, 'party.battleship_title'), "You sank all ships! \U0001F3C6"), view=self)
            self.stop()
            await _reward(interaction, self.gid, self.uid, 60, "battleship")
        else:
            await interaction.response.edit_message(embed=self._embed(), view=self)


# ══════════════════════════════════════════════════════════════════════════
#  COG
# ══════════════════════════════════════════════════════════════════════════

class PartyGames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="mafia", description="Start a game of Mafia")
    async def mafia(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "mafia_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = MafiaView(gid, interaction.user.id)
        await interaction.response.send_message(
            embed=info(t(gid, 'party.mafia_title'), t(gid, 'party.mafia_join', sec=60)), view=view)

    @app_commands.command(name="spyfall", description="Start a game of Spyfall")
    async def spyfall(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "spyfall_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = SpyfallView(gid, interaction.user.id)
        await interaction.response.send_message(
            embed=info(t(gid, 'party.spyfall_title'), t(gid, 'party.mafia_join', sec=60)), view=view)

    @app_commands.command(name="twotruths", description="Play Two Truths & a Lie")
    async def twotruths(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "twotruths_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = TwoTruthsView(gid, str(interaction.user.id))
        await interaction.response.send_message(
            embed=info(t(gid, 'party.twotruths_title'), "Click Submit to enter your two truths and a lie."),
            view=view)

    @app_commands.command(name="mostlikely", description="Who is most likely to...?")
    async def mostlikely(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "mostlikely_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = MostLikelyView(gid, str(interaction.user.id))
        await interaction.response.send_message(
            embed=info(t(gid, 'party.mostlikely_title'), f"Who is most likely to **{view.prompt}**?"),
            view=view)

    @app_commands.command(name="neverhave", description="Never have I ever...")
    async def neverhave(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "neverhave_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = NeverHaveView(gid, str(interaction.user.id))
        await interaction.response.send_message(
            embed=info(t(gid, 'party.neverhave_title'), f"Never have I ever **{view.prompt}**"),
            view=view)

    @app_commands.command(name="battleship", description="Play solo Battleship \u2014 sink all the ships")
    async def battleship(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "battleship_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = BattleshipView(gid, str(interaction.user.id))
        for r in range(BS_SIZE):
            for c in range(BS_SIZE):
                b = discord.ui.Button(label="\u00b7", style=discord.ButtonStyle.secondary, row=r)
                b.callback = self._make_fire(view, r, c)
                view.add_item(b)
        await interaction.response.send_message(embed=view._embed(), view=view)

    def _make_fire(self, view, r, c):
        async def cb(interaction: discord.Interaction):
            await view._fire(interaction, r, c)
        return cb


async def setup(bot):
    await bot.add_cog(PartyGames(bot))
