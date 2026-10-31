import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import random
import time
import re
from utils import load, save, ok, err, doc_lock

def parse_duration(text: str) -> int | None:
    pattern = re.findall(r'(\d+)\s*([smhd])', text.lower())
    if not pattern:
        return None
    total = 0
    for amount, unit in pattern:
        total += int(amount) * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]
    return total if total > 0 else None

def format_duration(seconds: int) -> str:
    parts = []
    if seconds >= 86400: parts.append(f"{seconds // 86400}d"); seconds %= 86400
    if seconds >= 3600:  parts.append(f"{seconds // 3600}h");  seconds %= 3600
    if seconds >= 60:    parts.append(f"{seconds // 60}m");    seconds %= 60
    if seconds:          parts.append(f"{seconds}s")
    return " ".join(parts) or "0s"

GIVEAWAY_EMOJI = "🎉"

class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tasks: dict[int, asyncio.Task] = {}
        self.restore_giveaways.start()

    def cog_unload(self):
        self.restore_giveaways.cancel()
        for t in self._tasks.values():
            t.cancel()

    # ── /giveaway start ───────────────────────────────────
    @app_commands.command(name="giveaway", description="Start a giveaway")
    @app_commands.describe(
        duration="Duration e.g. 10m, 2h, 1d",
        winners="Number of winners (1–20)",
        prize="What is being given away",
        channel="Channel to post in (leave empty for current)"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def giveaway(self, interaction: discord.Interaction,
                       duration: str, prize: str,
                       winners: int = 1,
                       channel: discord.TextChannel = None):
        seconds = parse_duration(duration)
        if not seconds:
            return await interaction.response.send_message(
                embed=err("Invalid duration! Use `10m`, `2h`, `1d`, etc."), ephemeral=True)
        if not (1 <= winners <= 20):
            return await interaction.response.send_message(
                embed=err("Winners must be between 1 and 20."), ephemeral=True)
        if seconds > 30 * 86400:
            return await interaction.response.send_message(
                embed=err("Maximum giveaway duration is **30 days**."), ephemeral=True)

        ch = channel or interaction.channel
        end_time = int(time.time()) + seconds
        human = format_duration(seconds)

        em = self._build_embed(prize, winners, end_time, interaction.user, ongoing=True)
        await interaction.response.send_message(f"✅ Giveaway started in {ch.mention}!", ephemeral=True)
        msg = await ch.send(content=f"# {GIVEAWAY_EMOJI} GIVEAWAY {GIVEAWAY_EMOJI}", embed=em)
        await msg.add_reaction(GIVEAWAY_EMOJI)

        # Save — RACE FIX: locked RMW. Two admins creating giveaways at the
        # same time must not overwrite each other's new entry.
        with doc_lock('giveaways.json'):
            data = load('giveaways.json')
            gid = str(interaction.guild.id)
            data.setdefault(gid, {})[str(msg.id)] = {
                'channel_id': ch.id,
                'message_id': msg.id,
                'prize':      prize,
                'winners':    winners,
                'end_time':   end_time,
                'host_id':    interaction.user.id,
                'ended':      False
            }
            save('giveaways.json', data)
        self._schedule(gid, str(msg.id), data[gid][str(msg.id)])

    def _build_embed(self, prize, winners, end_time, host, ongoing=True, winner_mentions=None):
        color = discord.Color.gold() if ongoing else discord.Color.green()
        em = discord.Embed(
            title=f"🎁 {prize}",
            color=color
        )
        if ongoing:
            em.description = (
                f"React with {GIVEAWAY_EMOJI} to enter!\n\n"
                f"**Ends:** <t:{end_time}:R> (<t:{end_time}:F>)\n"
                f"**Winners:** {winners}\n"
                f"**Hosted by:** {host.mention}"
            )
        else:
            if winner_mentions:
                em.description = (
                    f"**Winners:** {', '.join(winner_mentions)}\n"
                    f"**Hosted by:** {host.mention if hasattr(host, 'mention') else f'<@{host}>'}\n\n"
                    f"Congratulations! 🎊"
                )
            else:
                em.description = (
                    f"No valid entrants — no winners.\n"
                    f"**Hosted by:** {host.mention if hasattr(host, 'mention') else f'<@{host}>'}"
                )
            em.title = f"🎁 {prize} (Ended)"
        return em

    def _schedule(self, gid: str, mid: str, entry: dict):
        # Guard against double-scheduling the same giveaway (e.g. a fast
        # crash-restart loop calling restore_giveaways twice in quick succession)
        existing = self._tasks.get(int(mid))
        if existing and not existing.done():
            return
        delay = max(0, entry['end_time'] - time.time())
        task = self.bot.loop.create_task(self._end_after(gid, mid, entry, delay))
        self._tasks[int(mid)] = task

    async def _end_after(self, gid: str, mid: str, entry: dict, delay: float):
        try:
            await asyncio.sleep(delay)
            await self._end_giveaway(gid, mid, entry)
        except asyncio.CancelledError:
            pass
        finally:
            self._tasks.pop(int(mid), None)

    async def _end_giveaway(self, gid: str, mid: str, entry: dict, reroll=False):
        # Idempotency guard: re-read from disk in case another path (e.g. a
        # second restore_giveaways pass, or /gend racing the natural timer)
        # already marked this giveaway as ended.
        # RACE FIX: atomically claim the "end" under the document lock — a
        # concurrent /gend or natural-timer duplicate can no longer double-fire
        # winners. We mark it ended BEFORE the network phase; if we crash the
        # flag stays set (safe: worst case one unannounced winner, no doubles).
        with doc_lock('giveaways.json'):
            fresh = load('giveaways.json')
            if not reroll and fresh.get(gid, {}).get(mid, {}).get('ended'):
                return
            if gid not in fresh:
                fresh[gid] = {}
            if mid not in fresh[gid]:
                fresh[gid][mid] = dict(entry)
            fresh[gid][mid]['ended'] = True
            save('giveaways.json', fresh)

        ch = self.bot.get_channel(entry['channel_id'])
        if not ch:
            return
        try:
            msg = await ch.fetch_message(entry['message_id'])
        except Exception:
            return

        # Collect entrants from reaction
        reaction = discord.utils.get(msg.reactions, emoji=GIVEAWAY_EMOJI)
        entrants = []
        if reaction:
            async for user in reaction.users():
                if not user.bot:
                    entrants.append(user)

        num_winners = entry['winners']
        host_id = entry['host_id']
        prize = entry['prize']

        if entrants:
            chosen = random.sample(entrants, min(num_winners, len(entrants)))
            winner_mentions = [w.mention for w in chosen]
        else:
            chosen = []
            winner_mentions = None

        host = self.bot.get_user(host_id) or host_id
        em = self._build_embed(prize, num_winners, entry['end_time'], host,
                                ongoing=False, winner_mentions=winner_mentions)
        await msg.edit(content=f"# 🎊 GIVEAWAY ENDED 🎊", embed=em)

        if chosen:
            mentions = " ".join(w.mention for w in chosen)
            action = "Re-rolled winners" if reroll else "Congratulations"
            await ch.send(
                f"🎉 **{action}!** {mentions} won **{prize}**!\n"
                f"> *Hosted by <@{host_id}>*"
            )
        else:
            await ch.send(f"😔 No valid entrants for **{prize}**.")

        # (already marked ended atomically before the network phase)

    # ── /greroll ──────────────────────────────────────────
    @app_commands.command(name="greroll", description="Re-roll winners for an ended giveaway")
    @app_commands.describe(message_id="The message ID of the giveaway")
    @app_commands.default_permissions(manage_guild=True)
    async def greroll(self, interaction: discord.Interaction, message_id: str):
        gid = str(interaction.guild.id)
        data = load('giveaways.json')
        entry = data.get(gid, {}).get(message_id)
        if not entry:
            return await interaction.response.send_message(
                embed=err("Giveaway not found! Make sure you copied the message ID."), ephemeral=True)
        if not entry.get('ended'):
            return await interaction.response.send_message(
                embed=err("This giveaway hasn't ended yet!"), ephemeral=True)

        await interaction.response.send_message(embed=ok("Re-rolling…", "Picking new winners!"), ephemeral=True)
        await self._end_giveaway(gid, message_id, entry, reroll=True)

    # ── /gend ─────────────────────────────────────────────
    @app_commands.command(name="gend", description="End a giveaway early")
    @app_commands.describe(message_id="The message ID of the giveaway")
    @app_commands.default_permissions(manage_guild=True)
    async def gend(self, interaction: discord.Interaction, message_id: str):
        gid = str(interaction.guild.id)
        data = load('giveaways.json')
        entry = data.get(gid, {}).get(message_id)
        if not entry or entry.get('ended'):
            return await interaction.response.send_message(
                embed=err("Giveaway not found or already ended!"), ephemeral=True)

        task = self._tasks.pop(int(message_id), None)
        if task:
            task.cancel()

        await interaction.response.send_message(embed=ok("Ending giveaway…"), ephemeral=True)
        await self._end_giveaway(gid, message_id, entry)

    # ── Restore on restart ────────────────────────────────
    @tasks.loop(count=1)
    async def restore_giveaways(self):
        data = load('giveaways.json')
        now = time.time()
        count = 0
        for gid, giveaways in data.items():
            for mid, entry in giveaways.items():
                if not entry.get('ended'):
                    if entry['end_time'] <= now:
                        await self._end_giveaway(gid, mid, entry)
                    else:
                        self._schedule(gid, mid, entry)
                        count += 1
        if count:
            print(f"[Giveaways] Restored {count} active giveaway(s)")

    @restore_giveaways.before_loop
    async def before_restore(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
