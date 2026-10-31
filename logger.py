"""
Logger Cog — SMASH BOT
=======================
Logs server events to the configured mod-log channel.

Covered events:
  • Message deleted
  • Message edited
  • Member joined         (with account-age warning for new accounts)
  • Member left           (with roles they held)
  • Member updated        (nickname, roles added/removed)
  • Voice state changes   (join / leave / move)
  • Channel created / deleted
  • Member banned / unbanned

All categories can be individually toggled with /logsettings.
Settings are stored alongside the rest of mod config in mod_config.json.

Keys used in mod_config.json per guild:
  log_channel        (int)  — channel ID (set by /setlogchannel in moderation.py)
  logging            (bool) — master switch
  log_message_delete (bool) — default True
  log_message_edit   (bool) — default True
  log_member_join    (bool) — default True
  log_member_leave   (bool) — default True
  log_member_update  (bool) — default True
  log_voice          (bool) — default True
  log_channels       (bool) — default False
  log_bans           (bool) — default True
"""

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from utils import load, save, ok, doc_lock


# ── Helpers ────────────────────────────────────────────────────────────────────
def _mc(guild_id) -> dict:
    return load("mod_config.json").get(str(guild_id), {})


def _enabled(mc: dict, category: str) -> bool:
    """Return True unless the category is explicitly disabled.
    Categories default to ON so they work as soon as a log channel is set.
    Exception: 'channels' defaults OFF (very noisy).
    """
    default = False if category == "channels" else True
    return mc.get(f"log_{category}", default)


async def _send(bot: commands.Bot, guild: discord.Guild, embed: discord.Embed):
    """Send an embed to the guild's log channel if logging is enabled."""
    mc = _mc(guild.id)
    if not mc.get("logging"):
        return
    ch_id = mc.get("log_channel")
    if not ch_id:
        return
    ch = bot.get_channel(int(ch_id))
    if not ch:
        return
    try:
        await ch.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


# ── Cog ────────────────────────────────────────────────────────────────────────
class Logger(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════════════════════
    #  Message events
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        # Skip messages deleted by AutoMod or Anti-Scam (they log their own entries)
        try:
            from cogs.automod import is_deleted_by_automod
            if is_deleted_by_automod(message.id):
                return
        except ImportError:
            pass
        try:
            from cogs.moderation import is_scam_deleted
            if is_scam_deleted(message.id):
                return
        except ImportError:
            pass
        mc = _mc(message.guild.id)
        if not _enabled(mc, "message_delete"):
            return

        em = discord.Embed(
            title="🗑️ Message Deleted",
            color=0xed4245,
            timestamp=datetime.now(timezone.utc),
        )
        em.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        em.add_field(name="Author",  value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        em.add_field(name="Channel", value=message.channel.mention, inline=True)

        if message.content:
            # Truncate to Discord embed field limit
            content = message.content[:1000]
            if len(message.content) > 1000:
                content += f"\n*[…{len(message.content) - 1000} more chars]*"
            em.add_field(name="Content", value=content, inline=False)

        if message.attachments:
            names = "\n".join(a.filename for a in message.attachments)
            em.add_field(name=f"Attachments ({len(message.attachments)})", value=names[:512], inline=False)

        em.set_footer(text=f"Message ID: {message.id}")
        await _send(self.bot, message.guild, em)

    # ──────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot:
            return
        if before.content == after.content:
            return  # pin / embed-unfurl / etc. — not an actual edit
        mc = _mc(before.guild.id)
        if not _enabled(mc, "message_edit"):
            return

        em = discord.Embed(
            title="✏️ Message Edited",
            color=0xfaa61a,
            timestamp=datetime.now(timezone.utc),
        )
        em.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        em.add_field(name="Author",  value=f"{before.author.mention} (`{before.author.id}`)", inline=True)
        em.add_field(name="Channel", value=before.channel.mention, inline=True)
        em.add_field(name="Jump",    value=f"[View message]({after.jump_url})", inline=True)

        em.add_field(name="Before", value=(before.content[:512] or "*empty*"), inline=False)
        em.add_field(name="After",  value=(after.content[:512]  or "*empty*"), inline=False)

        em.set_footer(text=f"Message ID: {before.id}")
        await _send(self.bot, before.guild, em)

    # ══════════════════════════════════════════════════════════════════════════
    #  Member events
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        mc = _mc(member.guild.id)
        if not _enabled(mc, "member_join"):
            return

        now         = datetime.now(timezone.utc)
        account_age = (now - member.created_at).days
        age_flag    = "  ⚠️ **New account!**" if account_age < 7 else ""

        em = discord.Embed(
            title="📥 Member Joined",
            color=0x57f287,
            timestamp=now,
        )
        em.set_author(name=str(member), icon_url=member.display_avatar.url)
        em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(name="User",    value=f"{member.mention}\n`{member.id}`", inline=True)
        em.add_field(name="Members", value=str(member.guild.member_count),     inline=True)
        em.add_field(
            name="Account created",
            value=f"<t:{int(member.created_at.timestamp())}:R> ({account_age} days ago){age_flag}",
            inline=False,
        )
        await _send(self.bot, member.guild, em)

    # ──────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        mc = _mc(member.guild.id)
        if not _enabled(mc, "member_leave"):
            return

        roles = [r for r in member.roles if r.name != "@everyone"]
        roles_str = " ".join(r.mention for r in roles) or "*None*"
        if len(roles_str) > 1024:
            roles_str = roles_str[:1020] + "…"

        em = discord.Embed(
            title="📤 Member Left",
            color=0xed4245,
            timestamp=datetime.now(timezone.utc),
        )
        em.set_author(name=str(member), icon_url=member.display_avatar.url)
        em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(name="User",    value=f"{member.mention}\n`{member.id}`", inline=True)
        em.add_field(name="Members", value=str(member.guild.member_count),     inline=True)
        em.add_field(name=f"Roles held ({len(roles)})", value=roles_str,       inline=False)
        await _send(self.bot, member.guild, em)

    # ──────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        mc = _mc(before.guild.id)
        if not _enabled(mc, "member_update"):
            return

        fields: list[tuple[str, str]] = []

        # Nickname
        if before.nick != after.nick:
            b = before.nick or "*None*"
            a = after.nick  or "*None*"
            fields.append(("Nickname", f"{b} → {a}"))

        # Roles added
        added   = set(after.roles)   - set(before.roles)
        removed = set(before.roles)  - set(after.roles)
        for r in added:
            if r.name != "@everyone":
                fields.append(("Role added",   r.mention))
        for r in removed:
            if r.name != "@everyone":
                fields.append(("Role removed", r.mention))

        if not fields:
            return

        em = discord.Embed(
            title="🔄 Member Updated",
            color=0x5865f2,
            timestamp=datetime.now(timezone.utc),
        )
        em.set_author(name=str(after), icon_url=after.display_avatar.url)
        em.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
        for label, value in fields:
            em.add_field(name=label, value=value, inline=True)
        await _send(self.bot, before.guild, em)

    # ══════════════════════════════════════════════════════════════════════════
    #  Voice events
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return
        if before.channel == after.channel:
            return  # mute / deaf toggle only — skip

        mc = _mc(member.guild.id)
        if not _enabled(mc, "voice"):
            return

        if before.channel is None:
            action, color = "Joined", 0x57f287
            desc = f"{member.mention} joined **{after.channel.name}**"
        elif after.channel is None:
            action, color = "Left", 0xed4245
            desc = f"{member.mention} left **{before.channel.name}**"
        else:
            action, color = "Moved", 0xfaa61a
            desc = (
                f"{member.mention} moved\n"
                f"**{before.channel.name}** → **{after.channel.name}**"
            )

        em = discord.Embed(
            title=f"🎙️ Voice — {action}",
            description=desc,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        em.set_author(name=str(member), icon_url=member.display_avatar.url)
        await _send(self.bot, member.guild, em)

    # ══════════════════════════════════════════════════════════════════════════
    #  Channel events
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        mc = _mc(channel.guild.id)
        if not _enabled(mc, "channels"):
            return

        em = discord.Embed(
            title="➕ Channel Created",
            color=0x57f287,
            timestamp=datetime.now(timezone.utc),
        )
        ch_type = str(channel.type).replace("_", " ").title()
        em.add_field(name="Name",     value=getattr(channel, "mention", f"#{channel.name}"), inline=True)
        em.add_field(name="Type",     value=ch_type,                                          inline=True)
        em.add_field(name="Category", value=channel.category.name if channel.category else "*None*", inline=True)
        em.set_footer(text=f"Channel ID: {channel.id}")
        await _send(self.bot, channel.guild, em)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        mc = _mc(channel.guild.id)
        if not _enabled(mc, "channels"):
            return

        em = discord.Embed(
            title="➖ Channel Deleted",
            color=0xed4245,
            timestamp=datetime.now(timezone.utc),
        )
        ch_type = str(channel.type).replace("_", " ").title()
        em.add_field(name="Name",     value=f"#{channel.name}", inline=True)
        em.add_field(name="Type",     value=ch_type,            inline=True)
        em.add_field(name="Category", value=channel.category.name if channel.category else "*None*", inline=True)
        em.set_footer(text=f"Channel ID: {channel.id}")
        await _send(self.bot, channel.guild, em)

    # ══════════════════════════════════════════════════════════════════════════
    #  Ban / Unban events
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        mc = _mc(guild.id)
        if not _enabled(mc, "bans"):
            return

        em = discord.Embed(
            title="🔨 Member Banned",
            color=0xed4245,
            timestamp=datetime.now(timezone.utc),
        )
        em.set_author(name=str(user), icon_url=user.display_avatar.url)
        em.add_field(name="User", value=f"{user.mention} (`{user.id}`)")
        em.set_thumbnail(url=user.display_avatar.url)
        await _send(self.bot, guild, em)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        mc = _mc(guild.id)
        if not _enabled(mc, "bans"):
            return

        em = discord.Embed(
            title="✅ Member Unbanned",
            color=0x57f287,
            timestamp=datetime.now(timezone.utc),
        )
        em.set_author(name=str(user), icon_url=user.display_avatar.url)
        em.add_field(name="User", value=f"{user.mention} (`{user.id}`)")
        em.set_thumbnail(url=user.display_avatar.url)
        await _send(self.bot, guild, em)

    # ══════════════════════════════════════════════════════════════════════════
    #  /logsettings — toggle individual log categories
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(
        name="logsettings",
        description="Toggle which server events get logged in the mod-log channel",
    )
    @app_commands.describe(
        category="Which event category to configure",
        enabled="Enable or disable logging for that category",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="Message deleted",           value="message_delete"),
        app_commands.Choice(name="Message edited",            value="message_edit"),
        app_commands.Choice(name="Member joined",             value="member_join"),
        app_commands.Choice(name="Member left",               value="member_leave"),
        app_commands.Choice(name="Member updated (nick/role)",value="member_update"),
        app_commands.Choice(name="Voice channel activity",    value="voice"),
        app_commands.Choice(name="Channel created/deleted",   value="channels"),
        app_commands.Choice(name="Bans & unbans",             value="bans"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def logsettings(
        self,
        interaction: discord.Interaction,
        category: str,
        enabled: bool,
    ):
        # RACE FIX: locked RMW (dashboard api_moderation_save writes mod_config).
        with doc_lock("mod_config.json"):
            cfg = load("mod_config.json")
            gid = str(interaction.guild.id)
            cfg.setdefault(gid, {})[f"log_{category}"] = enabled
            save("mod_config.json", cfg)
        status = "✅ Enabled" if enabled else "❌ Disabled"
        # Human-readable label
        labels = {
            "message_delete": "Message deleted",
            "message_edit":   "Message edited",
            "member_join":    "Member joined",
            "member_leave":   "Member left",
            "member_update":  "Member updated",
            "voice":          "Voice activity",
            "channels":       "Channel create/delete",
            "bans":           "Bans & unbans",
        }
        label = labels.get(category, category)
        await interaction.response.send_message(
            embed=ok("Log Setting Updated", f"**{label}** → {status}"),
            ephemeral=True,
        )

    @app_commands.command(
        name="logstatus",
        description="Show which log categories are currently enabled",
    )
    @app_commands.default_permissions(administrator=True)
    async def logstatus(self, interaction: discord.Interaction):
        mc  = _mc(interaction.guild.id)
        ch_id = mc.get("log_channel")
        ch    = self.bot.get_channel(int(ch_id)) if ch_id else None
        master = mc.get("logging", False)

        cats = {
            "message_delete": "Message deleted",
            "message_edit":   "Message edited",
            "member_join":    "Member joined",
            "member_leave":   "Member left",
            "member_update":  "Member updated (nick/roles)",
            "voice":          "Voice activity",
            "channels":       "Channel create/delete",
            "bans":           "Bans & unbans",
        }
        lines = [
            f"{'✅' if _enabled(mc, k) else '❌'}  {label}"
            for k, label in cats.items()
        ]

        em = discord.Embed(
            title="📋 Log Settings",
            color=0x5865f2,
            timestamp=datetime.now(timezone.utc),
        )
        em.add_field(
            name="Log Channel",
            value=ch.mention if ch else "❌ Not set — use `/setlogchannel`",
            inline=False,
        )
        em.add_field(
            name="Master switch",
            value="✅ Logging ON" if master else "❌ Logging OFF",
            inline=False,
        )
        em.add_field(name="Categories", value="\n".join(lines), inline=False)
        em.set_footer(text=interaction.guild.name)
        await interaction.response.send_message(embed=em, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Logger(bot))
