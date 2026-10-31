"""Community — 3 community features.

Features: /badges /pixel /caption

Hazard-free community content. Rewards go through game_reward() (honours the
guild daily cap). All state mutations happen under doc_lock() and never await
inside a lock (two-phase pattern).
"""
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils import (load, save, ok, err, info, doc_lock, get_guild_cfg,
                   game_reward, quest_progress, get_badges)
from i18n import t


def _currency(gid):
    cfg = load('economy.json').get(str(gid), {})
    return cfg.get('currency_name', 'SK'), cfg.get('currency_emoji', '\U0001FA99')


def _enabled(gid, key):
    return bool(get_guild_cfg(gid, "games").get(key, True))


BADGE_NAMES = {
    "first_game": "\U0001F3AE First Game",
    "gamer_10": "\U0001F3AE Gamer x10",
    "gamer_100": "\U0001F3AE Gamer x100",
    "lucky": "\U0001F340 Lucky",
    "rich": "\U0001F4B0 Rich",
    "fisher": "\U0001F3A3 Fisher",
    "gardener": "\U0001F331 Gardener",
    "adventurer": "\u2694\uFE0F Adventurer",
    "duelist": "\U0001F93A Duelist",
    "lottery_winner": "\U0001F3AB Lottery Winner",
}

PIXEL_SIZE = 8
PIXEL_COOLDOWN = 30


def _pixel_get(gid):
    with doc_lock('pixelcanvas.json'):
        data = load('pixelcanvas.json')
        return data.setdefault(str(gid), {"grid": {}, "cooldowns": {}})


def _pixel_set(gid, canvas):
    with doc_lock('pixelcanvas.json'):
        data = load('pixelcanvas.json')
        data[str(gid)] = canvas
        save('pixelcanvas.json', data)


def _render_pixel(canvas):
    grid = canvas.get("grid", {})
    rows = []
    for y in range(PIXEL_SIZE):
        row = []
        for x in range(PIXEL_SIZE):
            row.append(grid.get(f"{x},{y}", "\u2B1B"))
        rows.append("".join(row))
    return "\n".join(rows)


class PixelModal(discord.ui.Modal, title="Place a Pixel"):
    x = discord.ui.TextInput(label="X (0-7)", max_length=1)
    y = discord.ui.TextInput(label="Y (0-7)", max_length=1)
    color = discord.ui.TextInput(label="Color emoji", max_length=8, default="\U0001F7E5")

    def __init__(self, gid, uid):
        super().__init__()
        self.gid, self.uid = gid, uid

    async def on_submit(self, interaction: discord.Interaction):
        try:
            x, y = int(str(self.x)), int(str(self.y))
        except ValueError:
            return await interaction.response.send_message("Coordinates must be numbers.", ephemeral=True)
        if not (0 <= x < PIXEL_SIZE and 0 <= y < PIXEL_SIZE):
            return await interaction.response.send_message("Coordinates out of range.", ephemeral=True)
        canvas = _pixel_get(self.gid)
        last = canvas.get("cooldowns", {}).get(self.uid, 0)
        remaining = PIXEL_COOLDOWN - (time.time() - last)
        if remaining > 0:
            return await interaction.response.send_message(
                embed=err(t(self.gid, 'community.pixel_cooldown', time=f"{int(remaining)}s")), ephemeral=True)
        canvas.setdefault("grid", {})[f"{x},{y}"] = str(self.color)
        canvas.setdefault("cooldowns", {})[self.uid] = time.time()
        _pixel_set(self.gid, canvas)
        await interaction.response.send_message(
            embed=ok(t(self.gid, 'community.pixel_placed', x=x, y=y)), ephemeral=True)


class CaptionModal(discord.ui.Modal, title="Caption Contest"):
    caption = discord.ui.TextInput(label="Your caption", max_length=200, style=discord.TextStyle.paragraph)

    def __init__(self, gid, uid):
        super().__init__()
        self.gid, self.uid = gid, uid

    async def on_submit(self, interaction: discord.Interaction):
        with doc_lock('captions.json'):
            data = load('captions.json')
            guild = data.setdefault(self.gid, {"entries": {}, "votes": {}})
            guild.setdefault("entries", {})[self.uid] = str(self.caption)
            save('captions.json', data)
        await interaction.response.send_message("Caption submitted! \U0001F4F8", ephemeral=True)


class CaptionView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=180)
        self.gid, self.uid = gid, uid

    @discord.ui.button(label="Submit Caption", style=discord.ButtonStyle.green)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CaptionModal(self.gid, str(interaction.user.id)))


class Community(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="badges", description="View your earned badges")
    @app_commands.describe(member="Member to view (defaults to you)")
    async def badges(self, interaction: discord.Interaction, member: discord.Member = None):
        gid = str(interaction.guild.id)
        target = member or interaction.user
        owned = get_badges(gid, target.id)
        if not owned:
            return await interaction.response.send_message(
                embed=info(t(gid, 'community.badges_title'), t(gid, 'community.badges_none')))
        lines = [f"{BADGE_NAMES.get(b, b)}" for b in owned]
        await interaction.response.send_message(
            embed=info(t(gid, 'community.badges_title'), f"**{target.display_name}**\n" + "\n".join(lines)))

    @app_commands.command(name="pixel", description="Place a pixel on the shared canvas")
    async def pixel(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "pixel_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        canvas = _pixel_get(gid)
        view = discord.ui.View(timeout=120)
        btn = discord.ui.Button(label="Place Pixel", style=discord.ButtonStyle.green)

        async def cb(inter: discord.Interaction):
            await inter.response.send_modal(PixelModal(gid, str(inter.user.id)))

        btn.callback = cb
        view.add_item(btn)
        await interaction.response.send_message(
            embed=info(t(gid, 'community.pixel_title'), _render_pixel(canvas)), view=view)

    @app_commands.command(name="caption", description="Start a caption contest")
    async def caption(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "caption_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = CaptionView(gid, str(interaction.user.id))
        await interaction.response.send_message(
            embed=info(t(gid, 'community.caption_title'),
                       "Submit your best caption! The host picks the winner."), view=view)


async def setup(bot):
    await bot.add_cog(Community(bot))
