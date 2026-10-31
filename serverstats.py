import discord
from discord.ext import commands, tasks
from discord import app_commands

from utils import load, save, ok, err, doc_lock

SERVERSTATS_FILE = 'serverstats.json'

# The stat types the counter channels can display. order matters for setup.
# key -> (default label template, how to compute the number from a guild)
STAT_TYPES = ['members', 'humans', 'bots', 'online', 'boosts', 'roles', 'channels']

DEFAULT_LABELS = {
    'members':  '\U0001F465 Members: {count}',
    'humans':   '\U0001F9CD Humans: {count}',
    'bots':     '\U0001F916 Bots: {count}',
    'online':   '\U0001F7E2 Online: {count}',
    'boosts':   '\U0001F680 Boosts: {count}',
    'roles':    '\U0001F3AD Roles: {count}',
    'channels': '\U0001F4C1 Channels: {count}',
}

DEFAULT_UPDATE_MINUTES = 5
# NOTE: Discord only allows ~2 channel renames per 10 min PER channel. The
# ticker checks this often, but a channel is only renamed when its number
# actually changes, so most ticks are no-ops. If counts change every minute
# you may hit Discord rate limits (429) and some renames will be delayed.
MIN_UPDATE_MINUTES = 1


def default_stat(stat_key, enabled=False):
    return {'enabled': enabled, 'label': DEFAULT_LABELS[stat_key], 'channel_id': ''}


def default_config():
    return {
        'enabled': False,
        'category_id': '',
        'update_minutes': DEFAULT_UPDATE_MINUTES,
        'stats': {k: default_stat(k, enabled=(k == 'members')) for k in STAT_TYPES},
    }


def get_cfg(gid: str) -> dict:
    """Return a normalized server-stats config for this guild (never None)."""
    raw = load(SERVERSTATS_FILE).get(str(gid), {}) or {}
    cfg = default_config()
    if isinstance(raw, dict):
        cfg['enabled'] = bool(raw.get('enabled', False))
        cfg['category_id'] = str(raw.get('category_id', '') or '')
        try:
            cfg['update_minutes'] = max(MIN_UPDATE_MINUTES, int(raw.get('update_minutes', DEFAULT_UPDATE_MINUTES)))
        except (TypeError, ValueError):
            cfg['update_minutes'] = DEFAULT_UPDATE_MINUTES
        stats = raw.get('stats', {}) or {}
        for k in STAT_TYPES:
            s = stats.get(k, {}) or {}
            cfg['stats'][k] = {
                'enabled': bool(s.get('enabled', cfg['stats'][k]['enabled'])),
                'label': str(s.get('label') or DEFAULT_LABELS[k]),
                'channel_id': str(s.get('channel_id', '') or ''),
            }
    return cfg


def save_cfg(gid: str, cfg: dict) -> None:
    with doc_lock(SERVERSTATS_FILE):
        data = load(SERVERSTATS_FILE)
        if not isinstance(data, dict):
            data = {}
        data[str(gid)] = cfg
        save(SERVERSTATS_FILE, data)


def compute_stat(guild: discord.Guild, stat_key: str) -> int:
    if stat_key == 'members':
        return guild.member_count or len(guild.members)
    if stat_key == 'bots':
        return sum(1 for m in guild.members if m.bot)
    if stat_key == 'humans':
        bots = sum(1 for m in guild.members if m.bot)
        return (guild.member_count or len(guild.members)) - bots
    if stat_key == 'online':
        return sum(
            1 for m in guild.members
            if not m.bot and m.status != discord.Status.offline
        )
    if stat_key == 'boosts':
        return guild.premium_subscription_count or 0
    if stat_key == 'roles':
        return max(0, len(guild.roles) - 1)  # exclude @everyone
    if stat_key == 'channels':
        # Count real channels only: exclude categories AND the counter
        # channels this cog creates (otherwise the number is inflated).
        managed = set()
        try:
            _cfg = get_cfg(str(guild.id))
            for _s in _cfg['stats'].values():
                _cid = str(_s.get('channel_id', '') or '')
                if _cid.isdigit():
                    managed.add(int(_cid))
        except Exception:
            pass
        return sum(
            1 for c in guild.channels
            if not isinstance(c, discord.CategoryChannel) and c.id not in managed
        )
    return 0


def format_label(label: str, count: int) -> str:
    text = (label or '{count}').replace('{count}', str(count))
    return text[:100]  # Discord channel-name hard limit


class ServerStats(commands.Cog):
    """Live server-stat counter channels. Shows member/bot/boost/etc. counts
    as locked voice channels that auto-update. Only moderators (Manage Server)
    can set these up or change them."""

    def __init__(self, bot):
        self.bot = bot
        self.stats_ticker.start()

    def cog_unload(self):
        self.stats_ticker.cancel()

    # ------------------------------------------------------------------ #
    #  Core update logic
    # ------------------------------------------------------------------ #
    async def update_guild(self, guild: discord.Guild) -> int:
        """Refresh every enabled counter channel for one guild.
        Returns how many channels were updated."""
        gid = str(guild.id)
        cfg = get_cfg(gid)
        if not cfg['enabled']:
            return 0
        updated = 0
        for stat_key in STAT_TYPES:
            stat = cfg['stats'][stat_key]
            if not stat['enabled'] or not str(stat['channel_id']).isdigit():
                continue
            channel = guild.get_channel(int(stat['channel_id']))
            if channel is None:
                continue
            new_name = format_label(stat['label'], compute_stat(guild, stat_key))
            if channel.name == new_name:
                continue
            try:
                await channel.edit(name=new_name, reason='Server stats auto-update')
                updated += 1
            except discord.HTTPException as e:
                print(f'[ServerStats] rename failed in {gid}/{stat_key}: {e}')
        return updated

    @tasks.loop(minutes=MIN_UPDATE_MINUTES)
    async def stats_ticker(self):
        for guild in list(self.bot.guilds):
            try:
                await self.update_guild(guild)
            except Exception as e:
                print(f'[ServerStats] ticker error in {guild.id}: {e}')

    @stats_ticker.before_loop
    async def before_ticker(self):
        await self.bot.wait_until_ready()

    # Keep channels fresh as people join / leave, without waiting for the loop.
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.update_guild(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self.update_guild(member.guild)

    # ------------------------------------------------------------------ #
    #  Slash commands (moderators only)
    # ------------------------------------------------------------------ #
    serverstats = app_commands.Group(
        name='serverstats',
        description='Live server-stat counter channels (moderators only)',
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @serverstats.command(name='setup', description='Create the live stat counter channels')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me = guild.me
        if not me.guild_permissions.manage_channels:
            await interaction.followup.send(
                embed=err('I need the **Manage Channels** permission to create the stat channels.'),
                ephemeral=True,
            )
            return

        gid = str(guild.id)
        cfg = get_cfg(gid)

        # Locked so members can SEE the counts but cannot connect/join.
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True),
            me: discord.PermissionOverwrite(connect=True, manage_channels=True, view_channel=True),
        }

        # Reuse an existing category if still valid, else create one.
        category = guild.get_channel(int(cfg['category_id'])) if str(cfg['category_id']).isdigit() else None
        if not isinstance(category, discord.CategoryChannel):
            category = await guild.create_category(
                name='\U0001F4CA Server Stats', overwrites=overwrites,
                reason='Server stats setup',
            )
            cfg['category_id'] = str(category.id)

        created = 0
        for stat_key in STAT_TYPES:
            stat = cfg['stats'][stat_key]
            if not stat['enabled']:
                continue
            # Skip if a valid channel already exists for this stat.
            existing = guild.get_channel(int(stat['channel_id'])) if str(stat['channel_id']).isdigit() else None
            if isinstance(existing, discord.VoiceChannel):
                continue
            name = format_label(stat['label'], compute_stat(guild, stat_key))
            vc = await guild.create_voice_channel(
                name=name, category=category, overwrites=overwrites,
                reason='Server stats setup',
            )
            stat['channel_id'] = str(vc.id)
            created += 1

        cfg['enabled'] = True
        save_cfg(gid, cfg)
        import builtins
        if hasattr(builtins, 'refresh_bot_cache'):
            builtins.refresh_bot_cache()
        await self.update_guild(guild)

        await interaction.followup.send(
            embed=ok(
                'Server Stats ready',
                f'Created **{created}** new counter channel(s). They refresh every '
                f'**{cfg["update_minutes"]} min** and whenever a member joins or leaves.\n'
                'Members can see them but cannot join them. Edit everything from the '
                'dashboard → **\U0001F4CA Server Stats**.',
            ),
            ephemeral=True,
        )

    @serverstats.command(name='refresh', description='Update the stat channels right now')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cfg = get_cfg(str(interaction.guild.id))
        if not cfg['enabled']:
            await interaction.followup.send(
                embed=err('Server stats are not set up yet. Run `/serverstats setup` first.'),
                ephemeral=True,
            )
            return
        updated = await self.update_guild(interaction.guild)
        await interaction.followup.send(
            embed=ok('Refreshed', f'Updated **{updated}** channel(s).'),
            ephemeral=True,
        )

    @serverstats.command(name='disable', description='Pause stat updates (keeps the channels)')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        cfg = get_cfg(gid)
        cfg['enabled'] = False
        save_cfg(gid, cfg)
        import builtins
        if hasattr(builtins, 'refresh_bot_cache'):
            builtins.refresh_bot_cache()
        await interaction.response.send_message(
            embed=ok('Paused', 'Stat channels will stop auto-updating. '
                     'Re-enable from the dashboard or run `/serverstats setup` again.'),
            ephemeral=True,
        )

    @serverstats.command(name='remove', description='Delete the stat channels and the category')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        gid = str(guild.id)
        cfg = get_cfg(gid)
        removed = 0
        for stat_key in STAT_TYPES:
            cid = cfg['stats'][stat_key]['channel_id']
            ch = guild.get_channel(int(cid)) if str(cid).isdigit() else None
            if ch is not None:
                try:
                    await ch.delete(reason='Server stats removed')
                    removed += 1
                except discord.HTTPException:
                    pass
            cfg['stats'][stat_key]['channel_id'] = ''
        cat = guild.get_channel(int(cfg['category_id'])) if str(cfg['category_id']).isdigit() else None
        if isinstance(cat, discord.CategoryChannel) and not cat.channels:
            try:
                await cat.delete(reason='Server stats removed')
            except discord.HTTPException:
                pass
        cfg['category_id'] = ''
        cfg['enabled'] = False
        save_cfg(gid, cfg)
        import builtins
        if hasattr(builtins, 'refresh_bot_cache'):
            builtins.refresh_bot_cache()
        await interaction.followup.send(
            embed=ok('Removed', f'Deleted **{removed}** counter channel(s).'),
            ephemeral=True,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            msg = 'Only moderators with **Manage Server** can use server-stats commands.'
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=err(msg), ephemeral=True)
                else:
                    await interaction.response.send_message(embed=err(msg), ephemeral=True)
            except discord.HTTPException:
                pass


async def setup(bot):
    await bot.add_cog(ServerStats(bot))
