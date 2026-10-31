import discord
from discord.ext import commands, tasks
from discord import app_commands
import time
from datetime import datetime, timezone
from utils import load, save, ok, err, info, safe_channel_send, rate_limit_wait, doc_lock

ACTIVITY_FILE = 'deadchat_activity.json'

# In-memory "last seen" tracker — updated on every message for accuracy,
# flushed to disk periodically so a restart doesn't lose too much data.
# Kept off the hot path on purpose: on_message only ever touches this dict,
# never hits the filesystem (same reasoning as XP_CD in leveling.py).
_last_seen: dict[str, dict[str, float]] = {}


def _get_cfg(gid: str) -> dict:
    cfg = load('config.json').get(gid, {})
    dc = cfg.get('deadchat', {})
    return {
        'enabled':            dc.get('enabled', False),
        'channel':            dc.get('channel', ''),
        'inactivity_days':    dc.get('inactivity_days', 7),
        'cooldown_days':      dc.get('cooldown_days', 7),
        'ping_role':          dc.get('ping_role', ''),
        'exclude_roles':      dc.get('exclude_roles', ''),
        'message':            dc.get('message', "👋 {user.mention} — we haven't seen you chat in **{days}** days! Come say hi 💬"),
        'batch_size':         dc.get('batch_size', 8),
    }


def _parse_ids(text: str) -> list[int]:
    return [int(x.strip()) for x in (text or '').split(',') if x.strip().isdigit()]


def _load_activity() -> dict:
    return load(ACTIVITY_FILE)


def _flush_last_seen():
    """Merge the in-memory _last_seen dict into the persisted activity file."""
    if not _last_seen:
        return
    # RACE FIX: serialize merge+save (loop + cog_unload + dashboard writers).
    with doc_lock(ACTIVITY_FILE):
        data = _load_activity()
        for gid, users in _last_seen.items():
            g = data.setdefault(gid, {})
            for uid, ts in users.items():
                entry = g.setdefault(uid, {})
                # Only ever move last_message forward.
                if ts > entry.get('last_message', 0):
                    entry['last_message'] = ts
        save(ACTIVITY_FILE, data)


def get_last_activity(gid: str, member: discord.Member) -> float:
    """Best-known last-activity timestamp for a member: in-memory > persisted > join date."""
    uid = str(member.id)
    mem_ts = _last_seen.get(gid, {}).get(uid, 0)
    file_ts = _load_activity().get(gid, {}).get(uid, {}).get('last_message', 0)
    best = max(mem_ts, file_ts)
    if best:
        return best
    if member.joined_at:
        return member.joined_at.timestamp()
    return 0


class DeadChatReviewer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.flush_activity.start()
        self.deadchat_ticker.start()

    def cog_unload(self):
        self.flush_activity.cancel()
        self.deadchat_ticker.cancel()
        _flush_last_seen()

    # ── Track activity (hot path — memory only) ─────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        gid = str(message.guild.id)
        uid = str(message.author.id)
        _last_seen.setdefault(gid, {})[uid] = time.time()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # A fresh member isn't "dead chat" — start their clock at join time.
        gid = str(member.guild.id)
        _last_seen.setdefault(gid, {})[str(member.id)] = time.time()

    # ── Persist in-memory activity every few minutes ────────────────────
    @tasks.loop(minutes=10)
    async def flush_activity(self):
        _flush_last_seen()

    @flush_activity.before_loop
    async def before_flush(self):
        await self.bot.wait_until_ready()

    # ── Periodic inactivity check + ping ────────────────────────────────
    def _eligible_members(self, guild: discord.Guild, cfg: dict):
        ping_role_ids = _parse_ids(cfg['ping_role'])
        exclude_role_ids = set(_parse_ids(cfg['exclude_roles']))
        for member in guild.members:
            if member.bot:
                continue
            if exclude_role_ids and any(r.id in exclude_role_ids for r in member.roles):
                continue
            if ping_role_ids and not any(r.id in ping_role_ids for r in member.roles):
                continue
            yield member

    async def run_check(self, guild: discord.Guild, dry_run: bool = False):
        """Returns list of (member, days_inactive) that are inactive.
        If dry_run is False and a channel is configured, also pings them."""
        gid = str(guild.id)
        cfg = _get_cfg(gid)
        now = time.time()
        threshold = cfg['inactivity_days'] * 86400
        cooldown = cfg['cooldown_days'] * 86400

        activity = _load_activity().get(gid, {})
        inactive = []
        for member in self._eligible_members(guild, cfg):
            last_active = get_last_activity(gid, member)
            if last_active and now - last_active < threshold:
                continue
            days = int((now - last_active) / 86400) if last_active else cfg['inactivity_days']
            uid = str(member.id)
            last_pinged = activity.get(uid, {}).get('last_pinged', 0)
            if not dry_run and cooldown and now - last_pinged < cooldown:
                continue
            inactive.append((member, days))

        if dry_run or not inactive:
            return inactive

        channel_id = cfg['channel']
        if not channel_id or not str(channel_id).isdigit():
            return inactive
        channel = guild.get_channel(int(channel_id))
        if not channel:
            return inactive

        # RACE FIX (two-phase): Phase 1 — record last_pinged for everyone we
        # are about to ping, atomically under the lock (BEFORE any network
        # awaits). Phase 2 — send the pings. Previously the save happened after
        # the sends, so activity recorded by message handlers during the sends
        # was silently erased, causing members to be pinged twice.
        with doc_lock(ACTIVITY_FILE):
            data = _load_activity()
            g = data.setdefault(gid, {})
            for member, days in inactive:
                g.setdefault(str(member.id), {})['last_pinged'] = now
            save(ACTIVITY_FILE, data)
        batch_size = max(1, int(cfg['batch_size']))
        for i in range(0, len(inactive), batch_size):
            chunk = inactive[i:i + batch_size]
            lines = []
            for member, days in chunk:
                msg = (cfg['message']
                       .replace('{user.mention}', member.mention)
                       .replace('{user.name}', member.name)
                       .replace('{server.name}', guild.name)
                       .replace('{days}', str(days)))
                lines.append(msg)
            await rate_limit_wait("send_message", channel.id)
            await safe_channel_send(channel, "\n".join(lines), label="DeadChatReviewer")
        return inactive

    @tasks.loop(hours=6)
    async def deadchat_ticker(self):
        for guild in self.bot.guilds:
            cfg = _get_cfg(str(guild.id))
            if not cfg['enabled']:
                continue
            try:
                await self.run_check(guild)
            except Exception as e:
                print(f"[DeadChatReviewer] Error checking {guild.id}: {e}")

    @deadchat_ticker.before_loop
    async def before_ticker(self):
        await self.bot.wait_until_ready()

    # ══════════════════════════════════════════════════════
    #  SLASH COMMANDS
    # ══════════════════════════════════════════════════════
    deadchat = app_commands.Group(name="deadchat", description="Dead chat reviewer commands")

    @deadchat.command(name="check", description="Run an inactivity check now and ping inactive members")
    @app_commands.default_permissions(manage_guild=True)
    async def check(self, interaction: discord.Interaction):
        cfg = _get_cfg(str(interaction.guild.id))
        if not cfg['enabled']:
            return await interaction.response.send_message(embed=err("Dead Chat Reviewer is disabled. Enable it on the dashboard first."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        inactive = await self.run_check(interaction.guild)
        if not inactive:
            return await interaction.followup.send(embed=info("💤 Nothing to report", "No inactive members matched the current settings (or everyone's on cooldown)."))
        names = "\n".join(f"• {m.mention} — {d}d inactive" for m, d in inactive[:20])
        await interaction.followup.send(embed=ok(f"Pinged {len(inactive)} inactive member(s)", names))

    @deadchat.command(name="list", description="List inactive members without pinging them")
    @app_commands.default_permissions(manage_guild=True)
    async def list_inactive(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        inactive = await self.run_check(interaction.guild, dry_run=True)
        if not inactive:
            return await interaction.followup.send(embed=info("💤 Nobody's inactive", "Every checked member has been active within the configured window."))
        inactive.sort(key=lambda x: x[1], reverse=True)
        names = "\n".join(f"• {m.mention} — **{d}d** inactive" for m, d in inactive[:25])
        em = info(f"💤 {len(inactive)} Inactive Member(s)", names)
        await interaction.followup.send(embed=em)


async def setup(bot):
    await bot.add_cog(DeadChatReviewer(bot))
