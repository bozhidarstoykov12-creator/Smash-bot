import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import time
import re
import itertools
from utils import load, save, ok, err, run_batched, doc_lock

# Process-wide monotonic counter — guarantees unique reminder IDs even if
# two /remind calls land in the exact same millisecond (previous timestamp-based
# ID could collide between different users and cause cross-user cancellation).
_id_counter = itertools.count(1)

def parse_duration(text: str) -> int | None:
    """Parse strings like '30m', '2h', '1d 6h', '90s' into seconds."""
    pattern = re.findall(r'(\d+)\s*([smhd])', text.lower())
    if not pattern:
        return None
    total = 0
    multipliers = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    for amount, unit in pattern:
        total += int(amount) * multipliers[unit]
    return total if total > 0 else None

def format_duration(seconds: int) -> str:
    parts = []
    if seconds >= 86400:
        parts.append(f"{seconds // 86400}d")
        seconds %= 86400
    if seconds >= 3600:
        parts.append(f"{seconds // 3600}h")
        seconds %= 3600
    if seconds >= 60:
        parts.append(f"{seconds // 60}m")
        seconds %= 60
    if seconds:
        parts.append(f"{seconds}s")
    return " ".join(parts) or "0s"


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._active: dict[int, asyncio.Task] = {}  # reminder_id -> Task
        self.check_reminders.start()

    def cog_unload(self):
        self.check_reminders.cancel()
        for task in self._active.values():
            task.cancel()

    # ── /remind ───────────────────────────────────────────
    @app_commands.command(name="remind", description="Set a personal reminder")
    @app_commands.describe(
        duration="When to remind you — e.g. 30m, 2h, 1d, 1h30m",
        message="What to remind you about"
    )
    async def remind(self, interaction: discord.Interaction, duration: str, message: str):
        seconds = parse_duration(duration)

        if seconds is None:
            return await interaction.response.send_message(
                embed=err("Invalid duration! Use formats like `30m`, `2h`, `1d`, `1h30m`, `90s`."),
                ephemeral=True
            )

        MAX = 30 * 86400  # 30 days
        if seconds > MAX:
            return await interaction.response.send_message(
                embed=err("Maximum reminder duration is **30 days**."), ephemeral=True
            )

        fire_at = int(time.time()) + seconds
        human   = format_duration(seconds)

        # Atomic read-modify-write: the dashboard edits reminders.json from its
        # own OS threads at the same time, so load -> validate -> append -> save
        # must be ONE locked critical section with zero awaits inside.
        with doc_lock('reminders.json'):
            data = load('reminders.json')
            uid  = str(interaction.user.id)
            if uid not in data:
                data[uid] = []

            # Limit 10 active reminders per user
            active = [r for r in data[uid] if r.get('fire_at', 0) > time.time()]
            too_many = len(active) >= 10
            if not too_many:
                rid = next(_id_counter)  # process-wide unique, never collides across users
                entry = {
                    'id':         rid,
                    'user_id':    interaction.user.id,
                    'channel_id': interaction.channel.id,
                    'guild_id':   interaction.guild.id if interaction.guild else None,
                    'message':    message,
                    'fire_at':    fire_at,
                    'created_at': int(time.time())
                }
                data[uid].append(entry)
                save('reminders.json', data)

        if too_many:
            return await interaction.response.send_message(
                embed=err("You already have **10 active reminders**. Wait for some to fire!"),
                ephemeral=True
            )

        # Schedule it immediately
        self._schedule(entry)

        em = discord.Embed(
            title="⏰ Reminder Set!",
            description=f"I'll remind you in **{human}**!\n\n> 📝 {message}",
            color=discord.Color.green()
        )
        em.add_field(name="⏱️ Fires at", value=f"<t:{fire_at}:F> (<t:{fire_at}:R>)", inline=False)
        em.set_footer(text=f"Reminder ID: #{rid}")
        await interaction.response.send_message(embed=em, ephemeral=True)

    # ── /reminders ────────────────────────────────────────
    @app_commands.command(name="reminders", description="List your active reminders")
    async def reminders_list(self, interaction: discord.Interaction):
        uid  = str(interaction.user.id)
        data = load('reminders.json').get(uid, [])
        now  = time.time()
        active = [r for r in data if r.get('fire_at', 0) > now]

        if not active:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    description="📭 You have no active reminders.\nUse `/remind` to set one!",
                    color=discord.Color.orange()
                ),
                ephemeral=True
            )

        active.sort(key=lambda r: r['fire_at'])
        em = discord.Embed(
            title=f"⏰ Your Reminders ({len(active)}/10)",
            color=discord.Color.blurple()
        )
        for r in active[:10]:
            fire = r['fire_at']
            em.add_field(
                name=f"#{r['id']} — <t:{fire}:R>",
                value=f"> {r['message']}\n📅 <t:{fire}:F>",
                inline=False
            )

        em.set_footer(text="Use /remindcancel <id> to cancel a reminder")
        await interaction.response.send_message(embed=em, ephemeral=True)

    # ── /remindcancel ─────────────────────────────────────
    @app_commands.command(name="remindcancel", description="Cancel an active reminder by ID")
    @app_commands.describe(reminder_id="The reminder ID shown in /reminders")
    async def remindcancel(self, interaction: discord.Interaction, reminder_id: int):
        uid  = str(interaction.user.id)
        # Atomic read-modify-write under the shared doc lock (dashboard writes
        # the same file from Flask threads).
        with doc_lock('reminders.json'):
            data = load('reminders.json')
            entries = data.get(uid, [])

            match = next((r for r in entries if r.get('id') == reminder_id), None)
            if match:
                data[uid] = [r for r in entries if r.get('id') != reminder_id]
                save('reminders.json', data)

        if not match:
            return await interaction.response.send_message(
                embed=err(f"No reminder with ID `#{reminder_id}` found."), ephemeral=True
            )

        # Cancel the live task if running
        task = self._active.pop(reminder_id, None)
        if task:
            task.cancel()

        await interaction.response.send_message(
            embed=ok("Reminder Cancelled", f"Reminder `#{reminder_id}` has been removed."),
            ephemeral=True
        )

    # ── Internal scheduling ───────────────────────────────
    def _schedule(self, entry: dict):
        rid = entry['id']
        delay = max(0, entry['fire_at'] - time.time())
        task = self.bot.loop.create_task(self._fire_after(entry, delay))
        self._active[rid] = task

    async def _fire_after(self, entry: dict, delay: float):
        try:
            await asyncio.sleep(delay)
            await self._fire(entry)
        except asyncio.CancelledError:
            pass
        finally:
            self._active.pop(entry['id'], None)

    async def _fire(self, entry: dict):
        uid     = entry['user_id']
        message = entry['message']
        fire_at = entry['fire_at']

        em = discord.Embed(
            title="⏰ Reminder!",
            description=f"> 📝 {message}",
            color=discord.Color.gold()
        )
        created = entry.get('created_at')
        if created:
            em.add_field(name="⏱️ Set", value=f"<t:{created}:R>", inline=True)
        em.set_footer(text="This reminder has fired — set a new one with /remind")

        # Try DM first, fall back to channel
        try:
            user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            await user.send(embed=em)
        except Exception:
            ch_id = entry.get('channel_id')
            if ch_id:
                ch = self.bot.get_channel(ch_id)
                if ch:
                    try:
                        await ch.send(f"<@{uid}>", embed=em)
                    except Exception:
                        pass

        # Clean from storage (atomic: dashboard may be writing concurrently)
        with doc_lock('reminders.json'):
            data = load('reminders.json')
            uid_str = str(uid)
            if uid_str in data:
                data[uid_str] = [r for r in data[uid_str] if r.get('id') != entry['id']]
                save('reminders.json', data)

    # ── Startup recovery: re-schedule any reminders that survived a restart ──
    @tasks.loop(count=1)
    async def check_reminders(self):
        global _id_counter
        # One atomic critical section: snapshot, ID-counter seeding, scheduling
        # and overdue collection all happen under the shared doc lock with zero
        # awaits (the dashboard edits reminders.json from Flask threads).
        with doc_lock('reminders.json'):
            data = load('reminders.json')
            now  = time.time()
            count = 0

            # Seed the counter past the highest ID we've ever persisted, so newly
            # created reminders this session can never collide with restored ones.
            max_existing_id = 0
            for entries in data.values():
                for entry in entries:
                    max_existing_id = max(max_existing_id, entry.get('id', 0))
            if max_existing_id:
                _id_counter = itertools.count(max_existing_id + 1)

            overdue = []
            for uid, entries in data.items():
                for entry in entries:
                    if entry.get('fire_at', 0) > now:
                        self._schedule(entry)
                        count += 1
                    else:
                        # Bot was offline when this was due — fire it now instead
                        # of silently dropping it, but don't schedule it via the
                        # normal per-entry task (that would fire all overdue
                        # reminders at once, in parallel).
                        overdue.append(entry)

        if count:
            print(f"[Reminders] Restored {count} pending reminder(s)")

        if overdue:
            print(f"[Reminders] {len(overdue)} overdue reminder(s) missed while offline — "
                  f"sending now in throttled batches.")
            # 5 DMs per batch with a short pause between batches keeps us well
            # under Discord's ~10 requests/10s per-route rate limit even if a
            # restart left dozens of reminders due at once.
            await run_batched(
                (lambda e=entry: self._fire(e) for entry in overdue),
                batch_size=5,
                batch_delay=2.0,
                label="Overdue reminders",
            )

    @check_reminders.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Reminders(bot))
