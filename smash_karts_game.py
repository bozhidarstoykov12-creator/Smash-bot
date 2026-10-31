"""
Smash Karts Setup Game
======================
One channel per server. Setups spawn randomly there.
Type "smash" to catch one!

JSON layout (smash_karts_game.json):
{
  "guilds": {
    "<guild_id>": {
      "channel_id": 123456789,
      "spawn_min": 60,
      "spawn_max": 600
    }
  },
  "scores": {
    "<guild_id>": {
      "<user_id>": {
        "total": 0,
        "Fine": 0, "Rare": 0, ...
      }
    }
  }
}
"""

import discord
import random
import asyncio
import time
import os
from discord.ext import commands, tasks
from discord import app_commands
from utils import load, save, ok, err, info, doc_lock

# ── Constants ────────────────────────────────────────────────────────────────

SETUP_FILE = "smash_karts_game.json"

SETUP_TYPES: dict[str, int] = {
    "Fine":      1000,
    "Nice":       750,
    "Good":       500,
    "Rare":       350,
    "Wild":       275,
    "Gremlin":    230,
    "Epic":       200,
    "Sus":        175,
    "Brave":      150,
    "Legendary":   35,
    "Mythic":      25,
    "Ultimate":     3,
}
SETUP_NAMES   = list(SETUP_TYPES.keys())
SETUP_WEIGHTS = list(SETUP_TYPES.values())

RARITY_COLORS: dict[str, int] = {
    "Fine":      0xAAAAAA,
    "Nice":      0x57F287,
    "Good":      0x3498DB,
    "Rare":      0x9B59B6,
    "Wild":      0xE67E22,
    "Gremlin":   0xE74C3C,
    "Epic":      0xE91E8C,
    "Sus":       0x1ABC9C,
    "Brave":     0xF1C40F,
    "Legendary": 0xFF5722,
    "Mythic":    0xAD1457,
    "Ultimate":  0xFFD700,
}

IMAGE_DIR = "assets/images/setups"

# ── Helpers ──────────────────────────────────────────────────────────────────

def _load() -> dict:
    data = load(SETUP_FILE)
    data.setdefault("guilds", {})
    data.setdefault("scores", {})
    return data

def _save(data: dict) -> None:
    # RACE FIX: shared with the Flask dashboard (api_smash_* endpoints).
    with doc_lock(SETUP_FILE):
        save(SETUP_FILE, data)

def _guild_cfg(data: dict, guild_id: int) -> dict | None:
    """Return config dict for a guild, or None if not configured."""
    return data["guilds"].get(str(guild_id))

def _add_catch(data: dict, guild_id: int, user_id: int, setup_type: str) -> int:
    """Record a catch and return the user's new total."""
    gid, uid = str(guild_id), str(user_id)
    data["scores"].setdefault(gid, {})
    user = data["scores"][gid].setdefault(uid, {"total": 0})
    user["total"]    = user.get("total", 0) + 1
    user[setup_type] = user.get(setup_type, 0) + 1
    return user["total"]

def _image_path(setup_type: str) -> str | None:
    path = os.path.join(IMAGE_DIR, f"{setup_type.lower()}_setup.png")
    return path if os.path.exists(path) else None


# ── Per-channel runtime state ─────────────────────────────────────────────────
# channel_id → {
#   "active_msg_id": int | None,
#   "setup_type":    str | None,
#   "spawn_at":      float,
#   "catching":      bool,
#   "catchers":      set[int],
# }
_state: dict[int, dict] = {}

def _ensure_state(ch_id: int, spawn_at: float = 0.0) -> dict:
    if ch_id not in _state:
        _state[ch_id] = {
            "active_msg_id": None,
            "setup_type":    None,
            "spawn_at":      spawn_at or time.time() + random.uniform(60, 600),
            "catching":      False,
            "catchers":      set(),
        }
    return _state[ch_id]


# ── Cog ──────────────────────────────────────────────────────────────────────

class SmashKartsGame(commands.Cog):
    """Smash Karts Setup catching game — one channel per server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._spawn_loop.start()

    def cog_unload(self):
        self._spawn_loop.cancel()

    # ── Background spawn loop (runs every 20 s) ───────────────────────────

    @tasks.loop(seconds=20)
    async def _spawn_loop(self):
        data = _load()
        now  = time.time()

        for gid_str, cfg in list(data["guilds"].items()):
            ch_id = cfg.get("channel_id")
            if not ch_id:
                continue
            st = _ensure_state(ch_id)

            if st["active_msg_id"] or st["catching"]:
                continue

            if now >= st["spawn_at"]:
                await self._spawn(ch_id, cfg)

    @_spawn_loop.before_loop
    async def _before_loop(self):
        await self.bot.wait_until_ready()
        data = _load()
        for gid_str, cfg in data["guilds"].items():
            ch_id = cfg.get("channel_id")
            if not ch_id:
                continue
            spawn_min = cfg.get("spawn_min", 60)
            spawn_max = cfg.get("spawn_max", 600)
            _ensure_state(ch_id, time.time() + random.uniform(spawn_min, spawn_max))

    # ── Spawn helper ──────────────────────────────────────────────────────

    async def _spawn(self, ch_id: int, cfg: dict):
        channel = self.bot.get_channel(ch_id)
        if not channel:
            return

        setup_type = random.choices(SETUP_NAMES, weights=SETUP_WEIGHTS, k=1)[0]
        color      = RARITY_COLORS.get(setup_type, 0x5865F2)
        img_path   = _image_path(setup_type)

        embed = discord.Embed(
            title=f"🎮 {setup_type} Setup has appeared!",
            description='Type **smash** to catch it!',
            color=color,
        )

        file = None
        if img_path:
            fname = os.path.basename(img_path)
            file  = discord.File(img_path, filename=fname)
            embed.set_image(url=f"attachment://{fname}")

        try:
            if file:
                msg = await channel.send(embed=embed, file=file)
            else:
                msg = await channel.send(embed=embed)
        except Exception:
            return

        st = _ensure_state(ch_id)
        st["active_msg_id"] = msg.id
        st["setup_type"]    = setup_type
        st["catching"]      = False
        st["catchers"]      = set()

    # ── on_message — catching logic ───────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.content.strip().lower() != "smash":
            return

        data = _load()
        cfg  = _guild_cfg(data, message.guild.id)
        if not cfg:
            return  # guild not configured

        if message.channel.id != cfg.get("channel_id"):
            return  # wrong channel

        ch_id = message.channel.id
        st    = _ensure_state(ch_id)
        if not st["active_msg_id"]:
            return  # nothing spawned

        uid = message.author.id

        # ── First catcher ─────────────────────────────────────────────────
        if not st["catching"]:
            st["catching"] = True
            st["catchers"].add(uid)

            setup_type = st["setup_type"]
            color      = RARITY_COLORS.get(setup_type, 0x5865F2)

            total = _add_catch(data, message.guild.id, uid, setup_type)
            _save(data)

            embed = discord.Embed(
                title=f"✅ {message.author.display_name} caught a {setup_type} Setup!",
                description=f"You now have **{total}** total setup(s). Others have 5 seconds!",
                color=color,
            )
            await message.channel.send(embed=embed)

            await asyncio.sleep(5)
            await self._close_window(ch_id, cfg)

        # ── Late catcher (within 5-second window) ─────────────────────────
        elif st["catching"] and uid not in st["catchers"]:
            if len(st["catchers"]) >= 4:
                return  # max 3 extra catchers
            st["catchers"].add(uid)

            setup_type = st["setup_type"]
            color      = RARITY_COLORS.get(setup_type, 0x5865F2)

            total = _add_catch(data, message.guild.id, uid, setup_type)
            _save(data)

            embed = discord.Embed(
                title=f"⚡ {message.author.display_name} also caught a {setup_type} Setup!",
                description=f"You now have **{total}** total setup(s).",
                color=color,
            )
            await message.channel.send(embed=embed)

    async def _close_window(self, ch_id: int, cfg: dict):
        """Reset channel state and schedule next spawn."""
        st = _ensure_state(ch_id)
        spawn_min = cfg.get("spawn_min", 60)
        spawn_max = cfg.get("spawn_max", 600)

        st["active_msg_id"] = None
        st["setup_type"]    = None
        st["catching"]      = False
        st["catchers"]      = set()
        st["spawn_at"]      = time.time() + random.uniform(spawn_min, spawn_max)

    # ── Slash commands ─────────────────────────────────────────────────────

    @app_commands.command(
        name="smashsetup",
        description="(ADMIN) Set the channel for Smash Karts setups (replaces old one)",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        spawn_min="Minimum seconds between spawns (default 60)",
        spawn_max="Maximum seconds between spawns (default 600)",
    )
    async def smashsetup(
        self,
        interaction: discord.Interaction,
        spawn_min: int = 60,
        spawn_max: int = 600,
    ):
        gid   = interaction.guild_id
        ch_id = interaction.channel_id
        data  = _load()

        old_cfg = _guild_cfg(data, gid)
        replacing = ""
        if old_cfg and old_cfg.get("channel_id") != ch_id:
            old_ch_id = old_cfg["channel_id"]
            _state.pop(old_ch_id, None)
            replacing = f"\n*(Replaced the old channel <#{old_ch_id}>)*"

        data["guilds"][str(gid)] = {
            "channel_id": ch_id,
            "spawn_min":  max(10, spawn_min),
            "spawn_max":  max(spawn_min + 10, spawn_max),
        }
        _save(data)

        _ensure_state(
            ch_id,
            time.time() + random.uniform(spawn_min, spawn_max),
        )

        await interaction.response.send_message(
            embed=ok(
                "🎮 Smash Karts Game Activated!",
                f"Setups will spawn in this channel every **{spawn_min}–{spawn_max}** seconds.\n"
                f"Type **smash** when one appears to catch it!{replacing}",
            )
        )

    @app_commands.command(
        name="smashforget",
        description="(ADMIN) Disable Smash Karts game in this server",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def smashforget(self, interaction: discord.Interaction):
        gid  = interaction.guild_id
        data = _load()

        cfg = _guild_cfg(data, gid)
        if not cfg:
            await interaction.response.send_message(
                "❌ Smash Karts is not set up in this server.",
                ephemeral=True,
            )
            return

        ch_id = cfg.get("channel_id")
        if ch_id:
            _state.pop(ch_id, None)

        del data["guilds"][str(gid)]
        _save(data)

        await interaction.response.send_message(
            embed=ok("✅ Done", "Smash Karts game disabled for this server.")
        )

    @app_commands.command(
        name="smashspawn",
        description="(ADMIN) Force-spawn a setup in the configured channel",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def smashspawn(self, interaction: discord.Interaction):
        gid  = interaction.guild_id
        data = _load()

        cfg = _guild_cfg(data, gid)
        if not cfg:
            await interaction.response.send_message(
                "❌ Smash Karts is not set up. Use `/smashsetup` first.",
                ephemeral=True,
            )
            return

        ch_id = cfg["channel_id"]
        st    = _ensure_state(ch_id)

        if st["active_msg_id"] or st["catching"]:
            await interaction.response.send_message(
                "⚠️ There is already an active setup in the game channel!",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Spawning a setup in <#{ch_id}>…", ephemeral=True
        )
        await self._spawn(ch_id, cfg)

    @app_commands.command(
        name="smashstats",
        description="View your Smash Karts setup collection",
    )
    @app_commands.describe(member="Member to check (leave empty for yourself)")
    async def smashstats(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        target = member or interaction.user
        data   = _load()
        gid    = str(interaction.guild_id)
        uid    = str(target.id)

        user_data = data.get("scores", {}).get(gid, {}).get(uid)
        if not user_data or user_data.get("total", 0) == 0:
            await interaction.response.send_message(
                embed=info("📭 No setups yet", f"{target.display_name} hasn't caught any setups yet!"),
                ephemeral=True,
            )
            return

        total = user_data.get("total", 0)
        embed = discord.Embed(
            title=f"🎮 {target.display_name}'s Setup Collection",
            color=0x5865F2,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        lines = []
        for stype in SETUP_NAMES:
            count = user_data.get(stype, 0)
            if count > 0:
                lines.append(f"**{stype}**: {count:,}")

        embed.description = "\n".join(lines) if lines else "Nothing caught yet."
        embed.set_footer(text=f"Total setups caught: {total:,}")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="smashlb",
        description="Smash Karts leaderboard for this server",
    )
    async def smashlb(self, interaction: discord.Interaction):
        data        = _load()
        gid         = str(interaction.guild_id)
        guild_scores = data.get("scores", {}).get(gid, {})

        if not guild_scores:
            await interaction.response.send_message(
                embed=info("📭 Empty", "Nobody has caught any setups yet!"),
                ephemeral=True,
            )
            return

        sorted_users = sorted(
            guild_scores.items(),
            key=lambda x: x[1].get("total", 0),
            reverse=True,
        )[:10]

        embed  = discord.Embed(title="🏆 Smash Karts Leaderboard", color=0xFFD700)
        lines  = []
        medals = ["🥇", "🥈", "🥉"]

        for i, (uid, udata) in enumerate(sorted_users):
            member = interaction.guild.get_member(int(uid))
            name   = member.display_name if member else f"User {uid}"
            prefix = medals[i] if i < 3 else f"`{i+1}.`"
            lines.append(f"{prefix} **{name}** — {udata.get('total', 0):,} setups")

        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed)


# ── Setup ─────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(SmashKartsGame(bot))
