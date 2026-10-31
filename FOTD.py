import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import aiohttp
import random
from datetime import date
from utils import load, save, err, ok, get_daily_last_run, set_daily_last_run, clear_daily_last_run, doc_lock

FACTS_API_URL = "https://uselessfacts.jsph.pl/api/v2/facts/random?language=en"


async def fetch_random_fact() -> str:
    """Fetch a random fact from the free uselessfacts API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(FACTS_API_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("fact", "").strip()
    return ""


class FOTD(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.fotd_scheduler.start()

    def cog_unload(self):
        self.fotd_scheduler.cancel()

    def get_config(self, gid):
        return load('config.json').get(str(gid), {}).get('fotd_settings', {})

    def get_community_facts(self, gid):
        return load('community_fotd.json').get(str(gid), [])

    async def get_content(self, gid: str) -> str:
        """Priority: community-submitted facts -> free public API -> static fallback."""
        # RACE FIX: pop + persist must be atomic — two parallel get_content
        # calls must not both return the same fact or drop each other's write.
        with doc_lock('community_fotd.json'):
            community = self.get_community_facts(gid)
            if community:
                idx = random.randrange(len(community))
                fact = community.pop(idx)
                all_data = load('community_fotd.json')
                all_data[gid] = community
                save('community_fotd.json', all_data)
                return fact

        try:
            fact = await fetch_random_fact()
            if fact:
                return fact
        except Exception as e:
            print(f"[FOTD] API error: {e}")

        return "Smash Karts was created by Tall Team and has been played by millions of players worldwide!"

    async def run_fotd(self, guild):
        gid   = str(guild.id)
        m_cfg = self.get_config(guild.id)
        if not m_cfg.get('enabled', True): return

        channel = guild.get_channel(int(m_cfg.get('channel_id', 0))) if m_cfg.get('channel_id') else None
        if not channel: return

        today = date.today().isoformat()
        if get_daily_last_run("fotd", gid) == today:
            return

        content = await self.get_content(gid)

        color_map = {"blue": discord.Color.blue(), "red": discord.Color.red(),
                     "orange": discord.Color.orange(), "purple": discord.Color.purple()}
        embed = discord.Embed(
            title="📚 Fact Of The Day",
            description=m_cfg.get('announcement_message', f"> {content}").replace("{content}", content),
            color=color_map.get("blue", discord.Color.blue())
        )

        pings = "".join([f"<@&{rid}>" for rid in m_cfg.get('mentioned_roles', [])])
        msg   = await channel.send(content=pings if pings else None, embed=embed)
        set_daily_last_run("fotd", gid, today)

        try:
            thread = await msg.create_thread(
                name=m_cfg.get('thread_name', "💬 Discussion"),
                auto_archive_duration=int(m_cfg.get('archive_duration', 1440))
            )
            if int(m_cfg.get('slowmode', 0)) > 0:
                await thread.edit(slowmode_delay=int(m_cfg.get('slowmode', 0)))
        except: pass

    @tasks.loop(hours=24)
    async def fotd_scheduler(self):
        for g in self.bot.guilds:
            await self.run_fotd(g)
            await asyncio.sleep(2)

    @fotd_scheduler.before_loop
    async def before_fotd(self):
        await self.bot.wait_until_ready()
        print("⏳ FOTD task is staggering... waiting 5 seconds on startup.")
        await asyncio.sleep(5)

    @app_commands.command(name="trigger_fotd", description="Test FOTD instantly (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def trigger_fotd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild.id)
        clear_daily_last_run("fotd", gid)
        await self.run_fotd(interaction.guild)
        await interaction.followup.send("✅ FOTD Posted!", ephemeral=True)

    @app_commands.command(name="fotd_submit", description="Submit a fact for a future Fact of the Day!")
    @app_commands.describe(fact="The fact you want to submit")
    async def fotd_submit(self, interaction: discord.Interaction, fact: str):
        gid = str(interaction.guild.id)
        if len(fact) > 500:
            return await interaction.response.send_message(
                embed=err("Your fact is too long! Keep it under 500 characters."), ephemeral=True)

        # RACE FIX: locked append (parallel submissions are separate tasks).
        with doc_lock('community_fotd.json'):
            data = load('community_fotd.json')
            data.setdefault(gid, [])
            data[gid].append(fact)
            save('community_fotd.json', data)

        await interaction.response.send_message(
            embed=ok("Fact Submitted!", f"Your fact has been added to the queue! ({len(data[gid])} pending)"),
            ephemeral=True
        )

    @app_commands.command(name="fotd_queue", description="See how many community facts are pending (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def fotd_queue(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        community = self.get_community_facts(gid)
        await interaction.response.send_message(
            embed=ok("FOTD Queue", f"**{len(community)}** community-submitted facts pending."),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(FOTD(bot))
