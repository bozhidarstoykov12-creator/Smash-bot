"""RPG — 5 progression games.

Games: /fish /pet /garden /adventure /quest

Hazard-free progression content. Rewards go through game_reward() (honours the
guild daily cap). All state mutations happen under doc_lock() and never await
inside a lock (two-phase pattern).
"""
import asyncio
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils import (load, save, ok, err, info, doc_lock, get_guild_cfg,
                   game_reward, quest_progress, QUEST_DEFS, _today)
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
#  FISHING
# ══════════════════════════════════════════════════════════════════════════

FISH_TABLE = [
    ("\U0001F41F Minnow", 5, 40),
    ("\U0001F41B Boot", 1, 10),
    ("\U0001F420 Salmon", 25, 30),
    ("\U0001F41A Clownfish", 40, 15),
    ("\U0001F988 Shark", 120, 8),
    ("\U0001F42C Dolphin", 200, 4),
    ("\U0001F9DC Mermaid", 500, 1),
]


def _roll_fish():
    total = sum(w for _, _, w in FISH_TABLE)
    r = random.uniform(0, total)
    acc = 0
    for name, val, w in FISH_TABLE:
        acc += w
        if r <= acc:
            return name, val
    return FISH_TABLE[0][0], FISH_TABLE[0][1]


class FishReelView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=15)
        self.gid, self.uid = gid, uid
        self.reeled = False

    @discord.ui.button(label="REEL!", style=discord.ButtonStyle.green)
    async def reel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("Not your line.", ephemeral=True)
        if self.reeled:
            return
        self.reeled = True
        button.disabled = True
        fish, value = _roll_fish()
        currency, emoji = _currency(self.gid)
        await interaction.response.edit_message(
            embed=ok(t(self.gid, 'rpg.fish_title'),
                     t(self.gid, 'rpg.fish_caught', fish=fish, value=value, currency=currency)),
            view=self)
        self.stop()
        await _reward(interaction, self.gid, self.uid, value, "fish")


# ══════════════════════════════════════════════════════════════════════════
#  PET
# ══════════════════════════════════════════════════════════════════════════

PET_TYPES = ["\U0001F415 Dog", "\U0001F408 Cat", "\U0001F430 Rabbit", "\U0001F98A Fox",
             "\U0001F427 Penguin", "\U0001F438 Frog", "\U0001F984 Unicorn"]


def _pet_get(gid, uid):
    with doc_lock('pets.json'):
        data = load('pets.json')
        guild = data.setdefault(str(gid), {})
        return guild.get(str(uid))


def _pet_set(gid, uid, pet):
    with doc_lock('pets.json'):
        data = load('pets.json')
        guild = data.setdefault(str(gid), {})
        guild[str(uid)] = pet
        save('pets.json', data)


# ══════════════════════════════════════════════════════════════════════════
#  GARDEN
# ══════════════════════════════════════════════════════════════════════════

GARDEN_PLOTS = 6
GARDEN_CROPS = {
    "carrot": ("\U0001F955 Carrot", 60, 20),
    "tomato": ("\U0001F345 Tomato", 120, 35),
    "corn": ("\U0001F33D Corn", 180, 50),
    "pumpkin": ("\U0001F383 Pumpkin", 300, 90),
}


def _garden_get(gid, uid):
    with doc_lock('gardens.json'):
        data = load('gardens.json')
        guild = data.setdefault(str(gid), {})
        return guild.get(str(uid), {"plots": [None] * GARDEN_PLOTS})


def _garden_set(gid, uid, garden):
    with doc_lock('gardens.json'):
        data = load('gardens.json')
        guild = data.setdefault(str(gid), {})
        guild[str(uid)] = garden
        save('gardens.json', data)


# ══════════════════════════════════════════════════════════════════════════
#  ADVENTURE
# ══════════════════════════════════════════════════════════════════════════

ADVENTURE_EVENTS = [
    ("You find a treasure chest!", 150),
    ("You slay a goblin and loot its pouch.", 80),
    ("You discover a hidden gem.", 120),
    ("You help a villager and earn a reward.", 60),
    ("You get ambushed and lose some coins.", -40),
    ("You fall into a trap!", -30),
    ("You find nothing of value.", 0),
    ("You rescue a merchant who pays you.", 100),
]


# ══════════════════════════════════════════════════════════════════════════
#  COG
# ══════════════════════════════════════════════════════════════════════════

class RPG(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── FISHING ──────────────────────────────────────────────────────────
    @app_commands.command(name="fish", description="Go fishing \u2014 reel in your catch!")
    async def fish(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "fish_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = FishReelView(gid, str(interaction.user.id))
        await interaction.response.send_message(
            embed=info(t(gid, 'rpg.fish_title'), t(gid, 'rpg.fish_cast')), view=view)
        await asyncio.sleep(random.uniform(2.0, 5.0))
        if view.reeled:
            return
        try:
            await interaction.edit_original_response(
                embed=info(t(gid, 'rpg.fish_title'), t(gid, 'rpg.fish_tension')), view=view)
        except discord.HTTPException:
            pass

    # ── PET ──────────────────────────────────────────────────────────────
    @app_commands.command(name="pet", description="Adopt or check on your pet")
    @app_commands.describe(action="adopt, feed, or play")
    @app_commands.choices(action=[
        app_commands.Choice(name="adopt", value="adopt"),
        app_commands.Choice(name="feed", value="feed"),
        app_commands.Choice(name="play", value="play"),
    ])
    async def pet(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        if not _enabled(gid, "pet_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        pet = _pet_get(gid, uid)
        if action.value == "adopt":
            if pet:
                return await interaction.response.send_message(
                    embed=err("You already have a pet!"), ephemeral=True)
            kind = random.choice(PET_TYPES)
            pet = {"type": kind, "hunger": 100, "happiness": 100, "level": 1, "xp": 0, "last_fed": 0}
            _pet_set(gid, uid, pet)
            return await interaction.response.send_message(
                embed=ok(t(gid, 'rpg.pet_title'), f"You adopted a **{kind}**! \U0001F389"))
        if not pet:
            return await interaction.response.send_message(
                embed=err("You don't have a pet yet. Use `/pet adopt`."), ephemeral=True)
        if action.value == "feed":
            pet["hunger"] = min(100, pet.get("hunger", 0) + 30)
            pet["last_fed"] = int(time.time())
            _pet_set(gid, uid, pet)
            return await interaction.response.send_message(
                embed=ok(t(gid, 'rpg.pet_title'), f"Your **{pet['type']}** is fed! Hunger: {pet['hunger']}%"))
        # play
        pet["happiness"] = min(100, pet.get("happiness", 0) + 20)
        pet["xp"] = pet.get("xp", 0) + 10
        if pet["xp"] >= pet.get("level", 1) * 100:
            pet["level"] = pet.get("level", 1) + 1
            pet["xp"] = 0
        _pet_set(gid, uid, pet)
        await interaction.response.send_message(
            embed=ok(t(gid, 'rpg.pet_title'),
                     f"You played with your **{pet['type']}**! Happiness: {pet['happiness']}%, Level: {pet['level']}"))
        await _reward(interaction, gid, uid, 15, "pet")

    # ── GARDEN ───────────────────────────────────────────────────────────
    @app_commands.command(name="garden", description="Plant and harvest crops")
    @app_commands.describe(action="view, plant, or harvest", crop="crop to plant")
    @app_commands.choices(action=[
        app_commands.Choice(name="view", value="view"),
        app_commands.Choice(name="plant", value="plant"),
        app_commands.Choice(name="harvest", value="harvest"),
    ], crop=[
        app_commands.Choice(name="carrot", value="carrot"),
        app_commands.Choice(name="tomato", value="tomato"),
        app_commands.Choice(name="corn", value="corn"),
        app_commands.Choice(name="pumpkin", value="pumpkin"),
    ])
    async def garden(self, interaction: discord.Interaction, action: app_commands.Choice[str],
                     crop: app_commands.Choice[str] = None):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        if not _enabled(gid, "garden_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        garden = _garden_get(gid, uid)
        plots = garden.setdefault("plots", [None] * GARDEN_PLOTS)
        if action.value == "view":
            lines = []
            for i, p in enumerate(plots):
                if not p:
                    lines.append(f"**{i+1}.** \U0001F7EB Empty")
                else:
                    ready = time.time() >= p["ready_at"]
                    name = GARDEN_CROPS[p["crop"]][0]
                    status = "\u2705 Ready" if ready else "\U0001F331 Growing"
                    lines.append(f"**{i+1}.** {name} {status}")
            return await interaction.response.send_message(
                embed=info(t(gid, 'rpg.garden_title'), "\n".join(lines)))
        if action.value == "plant":
            if crop is None:
                return await interaction.response.send_message(embed=err("Pick a crop to plant."), ephemeral=True)
            empty = next((i for i, p in enumerate(plots) if not p), None)
            if empty is None:
                return await interaction.response.send_message(embed=err("Your garden is full!"), ephemeral=True)
            name, grow, _ = GARDEN_CROPS[crop.value]
            plots[empty] = {"crop": crop.value, "ready_at": time.time() + grow}
            _garden_set(gid, uid, garden)
            return await interaction.response.send_message(
                embed=ok(t(gid, 'rpg.garden_title'), f"Planted **{name}** in plot {empty+1}."))
        # harvest
        harvested = 0
        for i, p in enumerate(plots):
            if p and time.time() >= p["ready_at"]:
                _, _, value = GARDEN_CROPS[p["crop"]]
                harvested += value
                plots[i] = None
        if harvested == 0:
            return await interaction.response.send_message(embed=err("Nothing is ready to harvest."), ephemeral=True)
        _garden_set(gid, uid, garden)
        currency, emoji = _currency(gid)
        await interaction.response.send_message(
            embed=ok(t(gid, 'rpg.garden_title'), f"Harvested crops worth **{harvested} {currency}**!"))
        await _reward(interaction, gid, uid, harvested, "garden")

    # ── ADVENTURE ────────────────────────────────────────────────────────
    @app_commands.command(name="adventure", description="Go on a short adventure")
    async def adventure(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        if not _enabled(gid, "adventure_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        text, delta = random.choice(ADVENTURE_EVENTS)
        currency, emoji = _currency(gid)
        if delta > 0:
            await interaction.response.send_message(
                embed=ok(t(gid, 'rpg.adventure_title'), f"{text}\n+**{delta} {currency}**"))
            await _reward(interaction, gid, uid, delta, "adventure")
        elif delta < 0:
            # Losses are capped so players never go negative from a game.
            with doc_lock('economy.json'):
                data = load('economy.json')
                u = data.setdefault(gid, {}).setdefault('balances', {}).setdefault(
                    uid, {'balance': 0, 'inventory': {}, 'xp_boost_until': 0, 'name': ''})
                loss = min(int(u.get('balance', 0)), -delta)
                u['balance'] = int(u.get('balance', 0)) - loss
                save('economy.json', data)
            await interaction.response.send_message(
                embed=info(t(gid, 'rpg.adventure_title'), f"{text}\n-**{loss} {currency}**"))
        else:
            await interaction.response.send_message(
                embed=info(t(gid, 'rpg.adventure_title'), text))
        quest_progress(gid, uid, "game_played", 1)

    # ── QUESTS ───────────────────────────────────────────────────────────
    @app_commands.command(name="quest", description="View your daily quests")
    async def quest(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        with doc_lock('quests.json'):
            data = load('quests.json')
            guild = data.get(gid, {})
            today = _today()
            if guild.get('day') != today:
                user_state = {}
            else:
                user_state = guild.get('users', {}).get(uid, {})
        lines = []
        for q in QUEST_DEFS:
            st = user_state.get(q['id'], {"progress": 0, "done": False})
            mark = "\u2705" if st.get("done") else "\u2B1C"
            lines.append(f"{mark} **{q['en']}** \u2014 {st.get('progress', 0)}/{q['target']} (reward {q['reward']})")
        await interaction.response.send_message(
            embed=info(t(gid, 'rpg.quest_title'), "\n".join(lines)))


async def setup(bot):
    await bot.add_cog(RPG(bot))
