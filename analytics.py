import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
from utils import load, save, doc_lock

ANALYTICS_FILE = 'analytics.json'
RETENTION_DAYS = 120

# In-memory counters, flushed to disk periodically — keeps on_message on the
# hot path free of filesystem I/O (same pattern as deadchat.py's _last_seen).
_buffer: dict[str, dict] = {}


def _today() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _bump(gid: str, key: str, hour: int | None = None):
    b = _buffer.setdefault(gid, {'messages': 0, 'joins': 0, 'leaves': 0, 'hourly': {}})
    b[key] += 1
    if hour is not None:
        b['hourly'][hour] = b['hourly'].get(hour, 0) + 1


def _flush():
    if not _buffer:
        return
    # RACE FIX: serialize the merge+save so two flushes (loop + cog_unload)
    # or a future dashboard writer can never double-count or drop counts.
    with doc_lock(ANALYTICS_FILE):
        data = load(ANALYTICS_FILE)
        today = _today()
        for gid, b in _buffer.items():
            g = data.setdefault(gid, {'daily': {}, 'hourly': {}})
            day = g['daily'].setdefault(today, {'messages': 0, 'joins': 0, 'leaves': 0})
            day['messages'] += b['messages']
            day['joins'] += b['joins']
            day['leaves'] += b['leaves']
            for hour, count in b['hourly'].items():
                hk = str(hour)
                g['hourly'][hk] = g['hourly'].get(hk, 0) + count

            # Trim old days so the file doesn't grow forever
            cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime('%Y-%m-%d')
            g['daily'] = {d: v for d, v in g['daily'].items() if d >= cutoff}
        save(ANALYTICS_FILE, data)
        _buffer.clear()


def get_analytics(gid: str) -> dict:
    data = load(ANALYTICS_FILE)
    return data.get(gid, {'daily': {}, 'hourly': {}})


class Analytics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.flush_loop.start()

    def cog_unload(self):
        self.flush_loop.cancel()
        _flush()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        hour = datetime.now(timezone.utc).hour
        _bump(str(message.guild.id), 'messages', hour)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        _bump(str(member.guild.id), 'joins')

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        _bump(str(member.guild.id), 'leaves')

    @tasks.loop(minutes=5)
    async def flush_loop(self):
        _flush()

    @flush_loop.before_loop
    async def before_flush(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Analytics(bot))
