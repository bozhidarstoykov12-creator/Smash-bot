import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from utils import load

def xp_for_level(level):
    return 5 * (level ** 2) + 50 * level + 100

def total_xp_for_level(level):
    return sum(xp_for_level(i) for i in range(level))

def get_level(xp):
    level = 0
    while xp >= total_xp_for_level(level + 1):
        level += 1
        if level > 500:
            break
    return level

MEDALS = ['🥇', '🥈', '🥉']

class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Show the server XP leaderboard")
    @app_commands.describe(page="Page number (10 users per page)")
    async def leaderboard(self, interaction: discord.Interaction, page: int = 1):
        await interaction.response.defer()

        gid = str(interaction.guild.id)
        lvl_data = load('levels.json').get(gid, {})

        if not lvl_data:
            return await interaction.followup.send(
                embed=discord.Embed(
                    description="📭 No XP data yet! Start chatting to earn XP.",
                    color=discord.Color.orange()
                )
            )

        sorted_users = sorted(lvl_data.items(), key=lambda x: x[1].get('xp', 0), reverse=True)
        total_pages = max(1, (len(sorted_users) + 9) // 10)
        page = max(1, min(page, total_pages))
        start = (page - 1) * 10
        page_users = sorted_users[start:start + 10]

        caller_id = str(interaction.user.id)
        caller_pos = next((i+1 for i, (uid, _) in enumerate(sorted_users) if uid == caller_id), None)
        caller_xp  = lvl_data.get(caller_id, {}).get('xp', 0)
        caller_lvl = get_level(caller_xp)

        em = discord.Embed(
            title=f"🏆 {interaction.guild.name} — XP Leaderboard",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        if interaction.guild.icon:
            em.set_thumbnail(url=interaction.guild.icon.url)

        rows = []
        for i, (uid, data) in enumerate(page_users):
            rank = start + i + 1
            xp   = data.get('xp', 0)
            lvl  = get_level(xp)
            name = data.get('name', f'User {uid}')
            medal = MEDALS[rank - 1] if rank <= 3 else f"`#{rank}`"
            rows.append(f"{medal} **{name}** — Lv.**{lvl}** · {xp:,} XP")

        em.description = "\n".join(rows)

        if caller_pos:
            em.add_field(
                name="📍 Your Position",
                value=f"Rank **#{caller_pos}** · Lv.**{caller_lvl}** · {caller_xp:,} XP",
                inline=False
            )

        em.set_footer(text=f"Page {page}/{total_pages} · {len(sorted_users)} total players")
        await interaction.followup.send(embed=em)


async def setup(bot):
    await bot.add_cog(Utility(bot))
