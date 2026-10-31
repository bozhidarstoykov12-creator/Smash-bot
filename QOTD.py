import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import aiohttp
import random
import html
from datetime import date
from utils import load, save, err, ok, get_daily_last_run, set_daily_last_run, clear_daily_last_run, doc_lock

TRIVIA_API_URL = "https://opentdb.com/api.php?amount=1&type=multiple"


async def fetch_trivia_question() -> str:
    """Fetch a random trivia question from the free OpenTDB API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(TRIVIA_API_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if results:
                    q = results[0]
                    question = html.unescape(q.get("question", ""))
                    category = html.unescape(q.get("category", ""))
                    return f"**[{category}]** {question}"
    return ""


class QOTD(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.qotd_scheduler.start()

    def cog_unload(self):
        self.qotd_scheduler.cancel()

    def get_config(self, gid):
        return load('config.json').get(str(gid), {}).get('qotd_settings', {})

    def get_community_questions(self, gid):
        return load('community_qotd.json').get(str(gid), [])

    async def get_content(self, gid: str) -> str:
        """Priority: community-submitted questions -> free trivia API -> static fallback."""
        # RACE FIX: pop + persist must be atomic (parallel get_content calls).
        with doc_lock('community_qotd.json'):
            community = self.get_community_questions(gid)
            if community:
                idx = random.randrange(len(community))
                question = community.pop(idx)
                all_data = load('community_qotd.json')
                all_data[gid] = community
                save('community_qotd.json', all_data)
                return question

        try:
            question = await fetch_trivia_question()
            if question:
                return question
        except Exception as e:
            print(f"[QOTD] API error: {e}")

        return "What is your absolute favorite weapon in Smash Karts?"

    async def run_qotd(self, guild):
        gid   = str(guild.id)
        m_cfg = self.get_config(guild.id)
        if not m_cfg.get('enabled', True): return

        channel = guild.get_channel(int(m_cfg.get('channel_id', 0))) if m_cfg.get('channel_id') else None
        if not channel: return

        today = date.today().isoformat()
        if get_daily_last_run("qotd", gid) == today:
            return

        content = await self.get_content(gid)

        color_map = {"blue": discord.Color.blue(), "red": discord.Color.red(),
                     "orange": discord.Color.orange(), "purple": discord.Color.purple()}
        embed = discord.Embed(
            title="❓ Question Of The Day",
            description=m_cfg.get('announcement_message', f"> {content}").replace("{content}", content),
            color=color_map.get("red", discord.Color.red())
        )

        pings = "".join([f"<@&{rid}>" for rid in m_cfg.get('mentioned_roles', [])])
        msg   = await channel.send(content=pings if pings else None, embed=embed)
        set_daily_last_run("qotd", gid, today)

        try:
            thread = await msg.create_thread(
                name=m_cfg.get('thread_name', "💬 Discussion"),
                auto_archive_duration=int(m_cfg.get('archive_duration', 1440))
            )
            if int(m_cfg.get('slowmode', 0)) > 0:
                await thread.edit(slowmode_delay=int(m_cfg.get('slowmode', 0)))
        except: pass

    @tasks.loop(hours=24)
    async def qotd_scheduler(self):
        for g in self.bot.guilds:
            await self.run_qotd(g)
            await asyncio.sleep(2)

    @qotd_scheduler.before_loop
    async def before_qotd(self):
        await self.bot.wait_until_ready()
        print("⏳ QOTD task is staggering... waiting 25 seconds on startup.")
        await asyncio.sleep(25)

    @app_commands.command(name="trigger_qotd", description="Test QOTD instantly (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def trigger_qotd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild.id)
        clear_daily_last_run("qotd", gid)
        await self.run_qotd(interaction.guild)
        await interaction.followup.send("✅ QOTD Posted!", ephemeral=True)

    @app_commands.command(name="qotd_submit", description="Submit a question for a future Question of the Day!")
    @app_commands.describe(question="The question you want to submit")
    async def qotd_submit(self, interaction: discord.Interaction, question: str):
        gid = str(interaction.guild.id)
        if len(question) > 500:
            return await interaction.response.send_message(
                embed=err("Your question is too long! Keep it under 500 characters."), ephemeral=True)

        # RACE FIX: locked append (parallel submissions are separate tasks).
        with doc_lock('community_qotd.json'):
            data = load('community_qotd.json')
            data.setdefault(gid, [])
            data[gid].append(question)
            save('community_qotd.json', data)

        await interaction.response.send_message(
            embed=ok("Question Submitted!", f"Your question has been added to the queue! ({len(data[gid])} pending)"),
            ephemeral=True
        )

    @app_commands.command(name="qotd_queue", description="See how many community questions are pending (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def qotd_queue(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        community = self.get_community_questions(gid)
        await interaction.response.send_message(
            embed=ok("QOTD Queue", f"**{len(community)}** community-submitted questions pending."),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(QOTD(bot))
