import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import aiohttp
import random
from datetime import date
from utils import load, save, err, ok, get_daily_last_run, set_daily_last_run, clear_daily_last_run, doc_lock

RIDDLE_API_URL = "https://riddles-api.vercel.app/random"


async def fetch_random_riddle() -> tuple:
    """Fetch a random riddle from the free riddles-api.vercel.app API.
    Returns (riddle_text, answer_text) or ("", "") on failure.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(RIDDLE_API_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                riddle = data.get("riddle", "").strip()
                answer = data.get("answer", "").strip()
                return riddle, answer
    return "", ""


class ROTD(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rotd_scheduler.start()

    def cog_unload(self):
        self.rotd_scheduler.cancel()

    def get_config(self, gid):
        return load('config.json').get(str(gid), {}).get('rotd_settings', {})

    def get_community_riddles(self, gid):
        return load('community_rotd.json').get(str(gid), [])

    async def get_content(self, gid: str) -> tuple:
        """Priority: community-submitted riddles -> free riddle API -> static fallback.
        Returns (riddle_text, answer_text)
        """
        # RACE FIX: pop + persist must be atomic (parallel get_content calls).
        with doc_lock('community_rotd.json'):
            community = self.get_community_riddles(gid)
            if community:
                idx = random.randrange(len(community))
                entry = community.pop(idx)
                all_data = load('community_rotd.json')
                all_data[gid] = community
                save('community_rotd.json', all_data)
                return entry.get('riddle', ''), entry.get('answer', '')

        try:
            riddle, answer = await fetch_random_riddle()
            if riddle:
                return riddle, answer
        except Exception as e:
            print(f"[ROTD] API error: {e}")

        return ("I have keys but no locks, space but no rooms — you can enter but "
                 "not go inside. What am I?", "A keyboard")

    async def run_rotd(self, guild):
        gid   = str(guild.id)
        m_cfg = self.get_config(guild.id)
        if not m_cfg.get('enabled', True): return

        channel = guild.get_channel(int(m_cfg.get('channel_id', 0))) if m_cfg.get('channel_id') else None
        if not channel: return

        today = date.today().isoformat()
        if get_daily_last_run("rotd", gid) == today:
            return

        riddle, answer = await self.get_content(gid)

        color_map = {"blue": discord.Color.blue(), "red": discord.Color.red(),
                     "orange": discord.Color.orange(), "purple": discord.Color.purple()}
        embed = discord.Embed(
            title="🧩 Riddle Of The Day",
            description=m_cfg.get('announcement_message', f"> {riddle}").replace("{content}", riddle),
            color=color_map.get("purple", discord.Color.purple())
        )
        embed.set_footer(text="💡 Reply with your guess! The answer drops in the thread tomorrow.")

        pings = "".join([f"<@&{rid}>" for rid in m_cfg.get('mentioned_roles', [])])
        msg   = await channel.send(content=pings if pings else None, embed=embed)
        set_daily_last_run("rotd", gid, today)

        try:
            thread = await msg.create_thread(
                name=m_cfg.get('thread_name', "🧩 Guess the Riddle!"),
                auto_archive_duration=int(m_cfg.get('archive_duration', 1440))
            )
            if int(m_cfg.get('slowmode', 0)) > 0:
                await thread.edit(slowmode_delay=int(m_cfg.get('slowmode', 0)))
            # Post the answer inside the thread right away, spoiler-tagged,
            # so curious members can check it without spoiling it for others
            # scrolling through the main channel.
            if answer:
                await thread.send(f"🔎 **Answer:** ||{answer}||")
        except Exception:
            pass

    @tasks.loop(hours=24)
    async def rotd_scheduler(self):
        for g in self.bot.guilds:
            await self.run_rotd(g)
            await asyncio.sleep(2)

    @rotd_scheduler.before_loop
    async def before_rotd(self):
        await self.bot.wait_until_ready()
        print("⏳ ROTD task is staggering... waiting 45 seconds on startup.")
        await asyncio.sleep(45)

    @app_commands.command(name="trigger_rotd", description="Test ROTD instantly (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def trigger_rotd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild.id)
        clear_daily_last_run("rotd", gid)
        await self.run_rotd(interaction.guild)
        await interaction.followup.send("✅ ROTD Posted!", ephemeral=True)

    @app_commands.command(name="rotd_submit", description="Submit a riddle for a future Riddle of the Day!")
    @app_commands.describe(riddle="The riddle question", answer="The answer to your riddle")
    async def rotd_submit(self, interaction: discord.Interaction, riddle: str, answer: str):
        gid = str(interaction.guild.id)
        if len(riddle) > 500 or len(answer) > 200:
            return await interaction.response.send_message(
                embed=err("Keep the riddle under 500 characters and the answer under 200."), ephemeral=True)

        # RACE FIX: locked append (parallel submissions are separate tasks).
        with doc_lock('community_rotd.json'):
            data = load('community_rotd.json')
            data.setdefault(gid, [])
            data[gid].append({"riddle": riddle, "answer": answer})
            save('community_rotd.json', data)

        await interaction.response.send_message(
            embed=ok("Riddle Submitted!", f"Your riddle has been added to the queue! ({len(data[gid])} pending)"),
            ephemeral=True
        )

    @app_commands.command(name="rotd_queue", description="See how many community riddles are pending (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def rotd_queue(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        community = self.get_community_riddles(gid)
        await interaction.response.send_message(
            embed=ok("ROTD Queue", f"**{len(community)}** community-submitted riddles pending."),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(ROTD(bot))
