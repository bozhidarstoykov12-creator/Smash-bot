import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import time
from utils import load, get_level

_start_time = time.time()

class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="stats", description="Show detailed server statistics")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild

        # Member counts
        total_members = guild.member_count
        online = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)
        bots = sum(1 for m in guild.members if m.bot)
        humans = total_members - bots

        # Channel counts
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)

        # Roles
        roles = len(guild.roles) - 1  # exclude @everyone

        # Emojis
        emojis = len(guild.emojis)

        # Boost info
        boost_level = guild.premium_tier
        boosters = guild.premium_subscription_count or 0

        # Server age
        created_at = guild.created_at
        age_days = (datetime.now(timezone.utc) - created_at).days

        # Bot uptime
        uptime_seconds = int(time.time() - _start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        # Top 5 XP earners
        lvl_data = load('levels.json').get(str(guild.id), {})
        sorted_users = sorted(lvl_data.items(), key=lambda x: x[1].get('xp', 0), reverse=True)[:5]

        leaderboard = ""
        medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣']
        for i, (uid, data) in enumerate(sorted_users):
            xp = data.get('xp', 0)
            level = get_level(xp)
            name = data.get('name', f'User {uid}')
            leaderboard += f"{medals[i]} **{name}** — Lv.{level} ({xp:,} XP)\n"

        if not leaderboard:
            leaderboard = "*No leveling data yet*"

        # Build embed
        em = discord.Embed(
            title=f"📊 {guild.name} — Server Stats",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )

        if guild.icon:
            em.set_thumbnail(url=guild.icon.url)

        em.add_field(
            name="👥 Members",
            value=f"**Total:** {total_members:,}\n**Humans:** {humans:,}\n**Online:** {online:,}\n**Bots:** {bots}",
            inline=True
        )
        em.add_field(
            name="💬 Channels",
            value=f"**Text:** {text_channels}\n**Voice:** {voice_channels}\n**Categories:** {categories}",
            inline=True
        )
        em.add_field(
            name="✨ Server Info",
            value=f"**Roles:** {roles}\n**Emojis:** {emojis}\n**Boost Level:** {boost_level} ({boosters} boosts)\n**Age:** {age_days:,} days",
            inline=True
        )
        em.add_field(
            name="🤖 Bot Uptime",
            value=f"`{uptime_str}`",
            inline=True
        )
        em.add_field(
            name="📅 Server Created",
            value=f"<t:{int(created_at.timestamp())}:D>",
            inline=True
        )
        em.add_field(
            name="🏆 Top XP Earners",
            value=leaderboard,
            inline=False
        )

        em.set_footer(text=f"Requested by {interaction.user}", icon_url=interaction.user.display_avatar.url)

        await interaction.followup.send(embed=em)

async def setup(bot):
    await bot.add_cog(Stats(bot))
