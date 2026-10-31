import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from utils import load, save, ok, err, info, safe_channel_send, rate_limit_wait, doc_lock

YOUTUBE_RSS_BY_ID     = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YOUTUBE_RSS_BY_USER   = "https://www.youtube.com/feeds/videos.xml?user={username}"

# ── URL → Channel ID resolver ──────────────────────────────
async def resolve_youtube_url(raw: str) -> tuple:
    """Returns (channel_id, display_name) or (None, error_msg)"""
    raw = raw.strip()

    # Already looks like a channel ID
    if re.match(r'^UC[a-zA-Z0-9_-]{22}$', raw):
        return raw, None

    # Normalize to full URL
    if raw.startswith('@'):
        url = f"https://www.youtube.com/{raw}"
    elif not raw.startswith('http'):
        url = f"https://www.youtube.com/@{raw}"
    else:
        url = raw

    # Try extracting channel ID directly from URL
    m = re.search(r'youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})', url)
    if m:
        return m.group(1), None

    # Try RSS by username (old-style)
    m_user = re.search(r'youtube\.com/user/([^/?&]+)', url)
    if m_user:
        username = m_user.group(1)
        rss_url  = YOUTUBE_RSS_BY_USER.format(username=username)
        cid = await _fetch_channel_id_from_rss(rss_url)
        if cid:
            return cid, None

    # Fetch the page and extract channelId from HTML/JS
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; bot)'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    return None, f"YouTube returned HTTP {resp.status}"
                text = await resp.text()

        # Look for channelId in page source
        patterns = [
            r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"',
            r'channel/(UC[a-zA-Z0-9_-]{22})',
            r'externalId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"',
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return m.group(1), None

        return None, "Could not extract Channel ID from that link. Try using the direct channel ID (UC...)."
    except Exception as e:
        return None, f"Error fetching page: {e}"

async def _fetch_channel_id_from_rss(rss_url: str) -> str | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(rss_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return None
                text = await r.text()
        ns = {'yt': 'http://www.youtube.com/xml/schemas/2015'}
        root = ET.fromstring(text)
        cid = root.find('yt:channelId', ns)
        return cid.text if cid is not None else None
    except:
        return None

def _parse_entry(entry, ns, root) -> dict | None:
    try:
        video_id_el = entry.find('yt:videoId', ns)
        title_el = entry.find('atom:title', ns)
        link_el = entry.find('atom:link', ns)
        if video_id_el is None or title_el is None or link_el is None:
            return None

        author_el = root.find('atom:author/atom:name', ns)
        published_el = entry.find('atom:published', ns)
        views_el = entry.find('media:group/media:community/media:statistics', ns)
        desc_el = entry.find('media:group/media:description', ns)
        video_id = video_id_el.text or ''
        views = views_el.attrib.get('views', '?') if views_el is not None else '?'
        description = (desc_el.text or '')[:200] + '...' if desc_el is not None and desc_el.text else ''
        return {
            'id': video_id,
            'title': title_el.text or '',
            'url': link_el.attrib.get('href', ''),
            'author': author_el.text if author_el is not None else 'Unknown',
            'thumbnail': f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            'published': published_el.text if published_el is not None else '',
            'views': views,
            'description': description,
        }
    except Exception:
        return None

# ── RSS video fetcher ──────────────────────────────────────
async def fetch_latest_video(channel_id: str) -> dict | None:
    url = YOUTUBE_RSS_BY_ID.format(channel_id=channel_id)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()
        ns = {
            'atom':  'http://www.w3.org/2005/Atom',
            'yt':    'http://www.youtube.com/xml/schemas/2015',
            'media': 'http://search.yahoo.com/mrss/'
        }
        root  = ET.fromstring(text)
        entry = root.find('atom:entry', ns)
        if entry is None:
            return None
        return _parse_entry(entry, ns, root)
    except Exception as e:
        print(f"[YouTube] Fetch error {channel_id}: {e}")
        return None

async def fetch_video_by_id(channel_id: str, video_id: str) -> dict | None:
    url = YOUTUBE_RSS_BY_ID.format(channel_id=channel_id)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()
        ns = {
            'atom':  'http://www.w3.org/2005/Atom',
            'yt':    'http://www.youtube.com/xml/schemas/2015',
            'media': 'http://search.yahoo.com/mrss/'
        }
        root = ET.fromstring(text)
        for entry in root.findall('atom:entry', ns):
            parsed = _parse_entry(entry, ns, root)
            if parsed and parsed.get('id') == video_id:
                return parsed
        return None
    except Exception as e:
        print(f"[YouTube] Fetch error {channel_id}: {e}")
        return None

def get_yt_cfg(gid: str) -> dict:
    return load('youtube.json').get(str(gid), {})

def save_yt_cfg(gid: str, cfg: dict):
    # RACE FIX: shared with the Flask dashboard (api_youtube_*).
    with doc_lock('youtube.json'):
        data = load('youtube.json')
        data[str(gid)] = cfg
        save('youtube.json', data)

class YouTube(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # One lock per (guild_id, yt_channel_id) — makes sure the scheduled
        # check_videos loop and a manual /youtube test (or dashboard "Test"
        # button, which calls send_test_alert) never run the fetch→post→save
        # sequence for the SAME channel at the same time. Without this, two
        # concurrent runs could both see the old last_video_id, both decide
        # "this is new", and both post the same video twice.
        self._channel_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.check_videos.start()

    def cog_unload(self):
        self.check_videos.cancel()

    async def post_video(self, guild: discord.Guild, channel_id: str, video: dict, ch_cfg: dict):
        discord_ch = guild.get_channel(int(ch_cfg.get('discord_channel', 0)))
        if not discord_ch:
            return

        msg_tpl  = ch_cfg.get('message', '🎬 **{author}** just uploaded a new video!\n**{title}**\n{url}')
        msg_text = (msg_tpl
            .replace('{author}',      video['author'])
            .replace('{title}',       video['title'])
            .replace('{url}',         video['url'])
            .replace('{views}',       str(video.get('views', '?')))
            .replace('{description}', video.get('description', ''))
            .replace('{channel}',     ch_cfg.get('name', video['author'])))
        # URL-ът ТРЯБВА да е в текста (не в embed) за да го разгъне Discord като плейър
        if video['url'] not in msg_text:
            msg_text = f"{msg_text}\n{video['url']}"

        ping = ''
        if ch_cfg.get('ping_role'):
            ping = f"<@&{ch_cfg['ping_role']}> "
        if ch_cfg.get('ping_everyone'):
            ping = "@everyone "

        # Пращаме БЕЗ embed=... — Discord ще auto-unfurl-не YouTube URL-а
        # като native плейър, който се пуска директно в чата.
        await safe_channel_send(
            discord_ch,
            content=(ping + msg_text) if ping else msg_text,
            allowed_mentions=discord.AllowedMentions(
                everyone=bool(ch_cfg.get('ping_everyone')),
                roles=True,
            ),
            label="YouTube alert",
        )

        gid = str(guild.id)
        cfg = get_yt_cfg(gid)
        cfg.setdefault('channels', {})
        if channel_id in cfg['channels']:
            ch_cfg.pop('live_post', None)  # no longer tracking for a live refresh
            cfg['channels'][channel_id] = ch_cfg
            save_yt_cfg(gid, cfg)
        return None


    @tasks.loop(minutes=20)
    async def check_videos(self):
        for guild in self.bot.guilds:
            try:
                gid = str(guild.id)
                cfg = get_yt_cfg(gid)
                channels = cfg.get('channels', {})
                if not channels:
                    continue

                # Guild-level enabled guard
                if not cfg.get('enabled', True):
                    print(f"[YouTube] Guild {guild.name} ({gid}): module disabled — skipping")
                    continue

                print(f"[YouTube] Checking guild '{guild.name}' ({gid}) — {len(channels)} channel(s) tracked")
                changed = False

                for yt_cid, ch_cfg in list(channels.items()):
                    ch_name = ch_cfg.get('name', yt_cid)
                    if not ch_cfg.get('enabled', True):
                        print(f"[YouTube]   Channel '{ch_name}' ({yt_cid}): skipped (enabled=False)")
                        continue

                    discord_ch_id = ch_cfg.get('discord_channel', 0)
                    discord_ch = guild.get_channel(int(discord_ch_id)) if discord_ch_id else None
                    if not discord_ch:
                        print(f"[YouTube]   Channel '{ch_name}' ({yt_cid}): Discord channel {discord_ch_id} not found (deleted?)")

                    try:
                        async with self._channel_locks[f"{gid}:{yt_cid}"]:
                            print(f"[YouTube]   Fetching RSS for '{ch_name}' ({yt_cid})...")
                            video = await fetch_latest_video(yt_cid)
                            if not video:
                                print(f"[YouTube]   Channel '{ch_name}': no video returned from RSS (HTTP error or empty feed)")
                                continue

                            last_id = ch_cfg.get('last_video_id')
                            print(f"[YouTube]   Channel '{ch_name}': last_video_id={last_id!r}, fetched={video['id']!r}")

                            if video['id'] != last_id:
                                if last_id is not None:
                                    print(f"[YouTube]   Channel '{ch_name}': new video detected → posting to #{discord_ch.name if discord_ch else '?'}")
                                    live_post = await self.post_video(guild, yt_cid, video, ch_cfg)
                                    if live_post:
                                        ch_cfg['live_post'] = live_post
                                else:
                                    print(f"[YouTube]   Channel '{ch_name}': first run — recording last_video_id without posting")
                                ch_cfg['last_video_id'] = video['id']
                                changed = True
                            else:
                                print(f"[YouTube]   Channel '{ch_name}': no new video")

                    except Exception as e:
                        # A single channel failing must never stop the whole loop
                        print(f"[YouTube]   Skipping '{ch_name}' ({yt_cid}) in guild {gid} after error: {e}")
                    await asyncio.sleep(1)

                if changed:
                    save_yt_cfg(gid, cfg)

            except Exception as e:
                # Guild-level safety net — a corrupt config must not kill other guilds
                print(f"[YouTube] Skipping guild {guild.id} after error: {e}")
            await asyncio.sleep(0.5)

    @check_videos.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @check_videos.error
    async def check_videos_error(self, error: Exception):
        """Final safety net for logging only. Note: discord.py's @tasks.loop
        still stops the loop after this runs for a non-network exception —
        the actual fix is the try/except blocks inside check_videos() above,
        which now catch everything before it can ever reach here. This just
        makes sure that IF something unexpected still slips through, it's
        logged instead of failing completely silently."""
        print(f"[YouTube] check_videos loop stopped due to unexpected error: {error}")

    # ══════════════════════════════════════════════════════
    #  SLASH COMMANDS
    # ══════════════════════════════════════════════════════
    yt = app_commands.Group(name="youtube", description="YouTube alert commands")

    @yt.command(name="add", description="Add a YouTube channel using its link or @handle")
    @app_commands.describe(
        youtube_url="Channel link, @handle, or Channel ID",
        discord_channel="Where to post alerts",
        name="Friendly name (optional)",
        ping_role="Role to ping (optional)"
    )
    @app_commands.default_permissions(administrator=True)
    async def add(self, interaction: discord.Interaction,
                  youtube_url: str,
                  discord_channel: discord.TextChannel,
                  name: str = "",
                  ping_role: discord.Role = None):
        await interaction.response.defer(ephemeral=True)

        channel_id, error = await resolve_youtube_url(youtube_url)
        if not channel_id:
            return await interaction.followup.send(
                embed=err(f"❌ {error}"), ephemeral=True)

        video = await fetch_latest_video(channel_id)
        if not video:
            return await interaction.followup.send(
                embed=err("❌ Found the channel but couldn't fetch videos. Try again."), ephemeral=True)

        gid = str(interaction.guild.id)
        cfg = get_yt_cfg(gid)
        cfg.setdefault('channels', {})
        cfg['channels'][channel_id] = {
            'name':           name or video['author'],
            'url':            youtube_url,
            'discord_channel': discord_channel.id,
            'ping_role':      str(ping_role.id) if ping_role else None,
            'ping_everyone':  False,
            'message':        '🎬 **{author}** just uploaded a new video!\n**{title}**\n{url}',
            'enabled':        True,
            'last_video_id':  video['id'],
            'live_post':      None,
        }
        save_yt_cfg(gid, cfg)

        em = ok("YouTube Alert Added! 🎬",
            f"📺 Channel: **{name or video['author']}**\n"
            f"📌 Alerts → {discord_channel.mention}\n"
            f"🎥 Latest: **{video['title']}**\n"
            f"🔍 Channel ID: `{channel_id}`")
        await interaction.followup.send(embed=em, ephemeral=True)

    @yt.command(name="remove", description="Remove a YouTube alert")
    @app_commands.describe(channel_id="YouTube Channel ID (use /youtube list to find it)")
    @app_commands.default_permissions(administrator=True)
    async def remove(self, interaction: discord.Interaction, channel_id: str):
        gid = str(interaction.guild.id)
        cfg = get_yt_cfg(gid)
        if channel_id in cfg.get('channels', {}):
            name = cfg['channels'][channel_id].get('name', channel_id)
            del cfg['channels'][channel_id]
            save_yt_cfg(gid, cfg)
            await interaction.response.send_message(embed=ok(f"Removed **{name}**"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=err("Channel not found! Use `/youtube list` to see IDs."), ephemeral=True)

    @yt.command(name="list", description="List all monitored YouTube channels")
    async def list_channels(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        cfg = get_yt_cfg(gid)
        channels = cfg.get('channels', {})
        if not channels:
            return await interaction.response.send_message(
                embed=info("📺 No YouTube Alerts", "Add one with `/youtube add <link>`"))

        em = discord.Embed(title="📺 YouTube Alerts", color=discord.Color.red())
        for cid, ch in channels.items():
            discord_ch = interaction.guild.get_channel(int(ch.get('discord_channel', 0)))
            status = "✅ Enabled" if ch.get('enabled', True) else "❌ Disabled"
            em.add_field(
                name=f"📺 {ch.get('name', cid)}",
                value=f"**ID:** `{cid}`\n**Status:** {status}\n**Alerts →** {discord_ch.mention if discord_ch else 'Not set'}",
                inline=False
            )
        await interaction.response.send_message(embed=em)

    async def send_test_alert(self, guild: discord.Guild, channel_id: str) -> None:
        """
        Dashboard-callable helper: posts a real alert for the latest video
        on the given YouTube channel_id, into whatever Discord channel is
        configured for it. Raises RuntimeError with a friendly message on
        any failure so the dashboard can surface it directly.
        """
        gid = str(guild.id)
        cfg = get_yt_cfg(gid)
        channels = cfg.get('channels', {})

        target_key = channel_id
        ch_cfg = channels.get(target_key)

        # Flexible lookup fallback (by lower case, URL substring, or name)
        if not ch_cfg:
            for k, v in channels.items():
                if k.lower() == channel_id.lower() or channel_id in k or k in channel_id or v.get('name', '').lower() == channel_id.lower():
                    target_key = k
                    ch_cfg = v
                    break

        if not ch_cfg:
            raise RuntimeError("That YouTube channel isn't configured for this server.")

        # Resolve channel ID if target_key is a URL or @handle
        fetch_id = target_key
        if not target_key.startswith('UC') or len(target_key) < 15:
            resolved_id, err_msg = await resolve_youtube_url(target_key)
            if resolved_id:
                fetch_id = resolved_id

        async with self._channel_locks[f"{gid}:{target_key}"]:
            video = await fetch_latest_video(fetch_id)
            if not video:
                raise RuntimeError("Could not fetch the latest video for that channel.")
            await self.post_video(guild, target_key, video, ch_cfg)

    @yt.command(name="test", description="Send a test alert for a channel")
    @app_commands.describe(channel_id="YouTube Channel ID")
    @app_commands.default_permissions(administrator=True)
    async def test(self, interaction: discord.Interaction, channel_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.send_test_alert(interaction.guild, channel_id)
        except RuntimeError as e:
            return await interaction.followup.send(embed=err(str(e)), ephemeral=True)
        await interaction.followup.send(embed=ok("✅ Test alert posted!"), ephemeral=True)

    @yt.command(name="toggle", description="Enable or disable a YouTube alert")
    @app_commands.describe(channel_id="YouTube Channel ID", enabled="True/False")
    @app_commands.default_permissions(administrator=True)
    async def toggle(self, interaction: discord.Interaction, channel_id: str, enabled: bool):
        gid = str(interaction.guild.id)
        cfg = get_yt_cfg(gid)
        if channel_id not in cfg.get('channels', {}):
            return await interaction.response.send_message(embed=err("Channel not found!"), ephemeral=True)
        cfg['channels'][channel_id]['enabled'] = enabled
        save_yt_cfg(gid, cfg)
        name = cfg['channels'][channel_id].get('name', channel_id)
        await interaction.response.send_message(
            embed=ok(f"{'Enabled ✅' if enabled else 'Disabled ❌'} — **{name}**"), ephemeral=True)

    @yt.command(name="setmessage", description="Customize the alert message for a channel")
    @app_commands.describe(
        channel_id="YouTube Channel ID",
        message="Use {author} {title} {url} {views} {channel} {description}"
    )
    @app_commands.default_permissions(administrator=True)
    async def setmessage(self, interaction: discord.Interaction, channel_id: str, message: str):
        gid = str(interaction.guild.id)
        cfg = get_yt_cfg(gid)
        if channel_id not in cfg.get('channels', {}):
            return await interaction.response.send_message(embed=err("Channel not found!"), ephemeral=True)
        cfg['channels'][channel_id]['message'] = message
        save_yt_cfg(gid, cfg)
        await interaction.response.send_message(embed=ok("Message Updated!", f"`{message}`"), ephemeral=True)

    @yt.command(name="latest", description="Show the latest video from a tracked channel")
    @app_commands.describe(channel_id="YouTube Channel ID")
    async def latest(self, interaction: discord.Interaction, channel_id: str):
        await interaction.response.defer()
        gid = str(interaction.guild.id)
        cfg = get_yt_cfg(gid)
        if channel_id not in cfg.get('channels', {}):
            return await interaction.followup.send(embed=err("Channel not tracked! Add it first."))
        video = await fetch_latest_video(channel_id)
        if not video:
            return await interaction.followup.send(embed=err("Could not fetch video!"))
        em = self.build_video_embed(video, cfg['channels'][channel_id], updated=False)
        await interaction.followup.send(embed=em)

async def setup(bot):
    await bot.add_cog(YouTube(bot))
