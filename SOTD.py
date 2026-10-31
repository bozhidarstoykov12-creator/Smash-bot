import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import random
from datetime import date
from utils import load, save, err, ok, get_daily_last_run, set_daily_last_run, clear_daily_last_run, doc_lock

# Static fallback list — high-energy gaming songs (Phonk / EDM / Hip-Hop)
# Used only when community submissions run out. No AI, no external API needed.
FALLBACK_SONGS = [
    "**Metamorphosis** by *INTERWORLD* — Ultimate high-speed drifts and rocket kills.",
    "**Just Lose It** by *Eminem* — Old-school chaos energy for tight lobbies.",
    "**Mortals** by *Warriyo* — Smooth synth waves perfect for clutch plays.",
    "**Sahara** by *Hensonn* — Dark phonk for sneaky last-place comebacks.",
    "**Faded** by *Alan Walker* — Calm-to-intense build for ranked grinding.",
    "**Soulja Boy Tell 'Em** by *Soulja Boy* — Pure hype, pure chaos.",
    "**Heat Waves** by *Glass Animals* — Chill vibes between rounds.",
    "**Industry Baby** by *Lil Nas X* — Confidence boost before a 1v1.",
    "**Edge of Seventeen** (Phonk Remix) — Dark and aggressive drift energy.",
    "**Levitating** by *Dua Lipa* — Pure fun for casual lobbies.",
    "**Murder In My Mind** by *Kordhell* — Aggressive phonk for sweaty matches.",
    "**Bad Habit** by *Steve Lacy* — Smooth groove for relaxed sessions.",
    "**Take On Me** (Synthwave Remix) — Retro arcade energy.",
    "**Stay** by *The Kid LAROI & Justin Bieber* — Catchy hooks for grinding XP.",
    "**Type Beat — Slowed + Reverb** — Atmospheric vibes for night sessions.",
    "**One Kiss** by *Calvin Harris & Dua Lipa* — Upbeat EDM for fast rounds.",
    "**Knock You Down** by *Keys N Krates* — High-tempo bass for kart chaos.",
    "**Phonk House Mix 2026** — Underground beats, perfect drift rhythm.",
    "**Rasputin** (Majulah Remix) — Unexpectedly perfect for sharp turns.",
    "**Bring Me to Life** by *Evanescence* — Epic energy for boss-level matches.",
]


class SOTD(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sotd_scheduler.start()

    def cog_unload(self):
        self.sotd_scheduler.cancel()

    def get_config(self, gid):
        return load('config.json').get(str(gid), {}).get('sotd_settings', {})

    def get_community_songs(self, gid):
        return load('community_sotd.json').get(str(gid), [])

    def get_content(self, gid: str) -> str:
        """Priority: community-submitted songs -> static fallback list (no AI, no external API)."""
        # RACE FIX: pop + persist must be atomic (parallel get_content calls).
        with doc_lock('community_sotd.json'):
            community = self.get_community_songs(gid)
            if community:
                idx = random.randrange(len(community))
                song = community.pop(idx)
                all_data = load('community_sotd.json')
                all_data[gid] = community
                save('community_sotd.json', all_data)
                return song

        return random.choice(FALLBACK_SONGS)

    async def run_sotd(self, guild):
        gid   = str(guild.id)
        m_cfg = self.get_config(guild.id)
        if not m_cfg.get('enabled', True): return

        channel = guild.get_channel(int(m_cfg.get('channel_id', 0))) if m_cfg.get('channel_id') else None
        if not channel: return

        today = date.today().isoformat()
        if get_daily_last_run("sotd", gid) == today:
            return

        content = self.get_content(gid)

        color_map = {"blue": discord.Color.blue(), "red": discord.Color.red(),
                     "orange": discord.Color.orange(), "purple": discord.Color.purple()}
        embed = discord.Embed(
            title="🎵 Song Of The Day",
            description=m_cfg.get('announcement_message', f"> {content}").replace("{content}", content),
            color=color_map.get("purple", discord.Color.purple())
        )

        pings = "".join([f"<@&{rid}>" for rid in m_cfg.get('mentioned_roles', [])])
        msg   = await channel.send(content=pings if pings else None, embed=embed)
        set_daily_last_run("sotd", gid, today)

        try:
            thread = await msg.create_thread(
                name=m_cfg.get('thread_name', "💬 Discussion"),
                auto_archive_duration=int(m_cfg.get('archive_duration', 1440))
            )
            if int(m_cfg.get('slowmode', 0)) > 0:
                await thread.edit(slowmode_delay=int(m_cfg.get('slowmode', 0)))
        except: pass

    @tasks.loop(hours=24)
    async def sotd_scheduler(self):
        for g in self.bot.guilds:
            await self.run_sotd(g)
            await asyncio.sleep(2)

    @sotd_scheduler.before_loop
    async def before_sotd(self):
        await self.bot.wait_until_ready()
        print("⏳ SOTD task is staggering... waiting 65 seconds on startup.")
        await asyncio.sleep(65)

    @app_commands.command(name="trigger_sotd", description="Test SOTD instantly (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def trigger_sotd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild.id)
        clear_daily_last_run("sotd", gid)
        await self.run_sotd(interaction.guild)
        await interaction.followup.send("✅ SOTD Posted!", ephemeral=True)

    @app_commands.command(name="sotd_submit", description="Submit a song for a future Song of the Day!")
    @app_commands.describe(song="Format suggestion: 'Song Name by Artist - short vibe description'")
    async def sotd_submit(self, interaction: discord.Interaction, song: str):
        gid = str(interaction.guild.id)
        if len(song) > 500:
            return await interaction.response.send_message(
                embed=err("Your submission is too long! Keep it under 500 characters."), ephemeral=True)

        # RACE FIX: locked append (parallel submissions are separate tasks).
        with doc_lock('community_sotd.json'):
            data = load('community_sotd.json')
            data.setdefault(gid, [])
            data[gid].append(song)
            save('community_sotd.json', data)

        await interaction.response.send_message(
            embed=ok("Song Submitted!", f"Your song has been added to the queue! ({len(data[gid])} pending)"),
            ephemeral=True
        )

    @app_commands.command(name="sotd_queue", description="See how many community songs are pending (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def sotd_queue(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        community = self.get_community_songs(gid)
        await interaction.response.send_message(
            embed=ok("SOTD Queue", f"**{len(community)}** community-submitted songs pending."),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(SOTD(bot))
