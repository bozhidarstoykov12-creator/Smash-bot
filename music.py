"""
Music Cog for SmashBot
======================
High-performance voice playback supporting YouTube search, YouTube URLs/playlists,
and Spotify tracks/albums/playlists.
Features:
- Slash commands + prefix aliases (!play, !p, !skip, !s, !pause, !np, !queue, !vol, !dc)
- Loop modes: Off / Track Loop / Queue Loop
- Queue pagination and shuffle
- Progress bar for Now Playing
- Web dashboard configurable settings (DJ Role, Default Volume, Max Queue, Max Duration, Idle Timeout)
- Auto-disconnect on inactivity / empty voice channel
"""

import asyncio
import copy
import json
import os
import random
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

try:
    import yt_dlp
    YT_DLP_OK = True
except ImportError:
    YT_DLP_OK = False

try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    SPOTIFY_OK = True
except ImportError:
    SPOTIFY_OK = False


# ═════════════════════════════════════════════════════════════════════════════
#  CONFIG & CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

MAX_QUEUE_SIZE = 200
MAX_DURATION   = 180 * 60   # 3 hours max per track
IDLE_TIMEOUT   = 180        # 3 minutes idle before auto-disconnect

# Базова конфигурация за yt-dlp без директни 'Cookie' хедъри (избягва Deprecation предупрежденията)
YTDLP_OPTS = {
    'format':             'bestaudio/best',
    'noplaylist':         False,
    'quiet':              True,
    'no_warnings':        True,
    'default_search':     'ytsearch',
    'source_address':     '0.0.0.0',
    'extract_flat':       False,
    'skip_download':      True,
    'ignoreerrors':       False,
    'cachedir':           False,
    'extractor_args': {
        'youtube': {
            # Мобилните клиенти заобикалят уеб бисквитките и consent екраните
            'player_client': ['android', 'ios', 'mweb'],
            'skip': ['webpage', 'configs'],
        }
    },
}

# Ако файлът cookies.txt съществува, yt-dlp го зарежда по безопасния начин
if os.path.exists('cookies.txt') and os.path.getsize('cookies.txt') > 0:
    YTDLP_OPTS['cookiefile'] = 'cookies.txt'


def _extract_direct_audio_url(info_dict: dict) -> str:
    """Helper to extract direct audio URL from yt-dlp info dict."""
    if not isinstance(info_dict, dict):
        return ''
    url = info_dict.get('url', '')
    if url:
        return url
    formats = info_dict.get('formats') or []
    audio_formats = [
        f for f in formats 
        if f.get('url') and (f.get('vcodec') == 'none' or f.get('acodec') not in (None, 'none'))
    ]
    if audio_formats:
        best = max(audio_formats, key=lambda f: f.get('abr') or f.get('tbr') or 0)
        return best.get('url', '')
    return info_dict.get('webpage_url', '')


FFMPEG_OPTS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options':        '-vn -bufsize 64k',
}

SPOTIFY_PATTERN = re.compile(
    r'https?://open\.spotify\.com/(track|playlist|album)/([A-Za-z0-9]+)'
)
YOUTUBE_PATTERN = re.compile(
    r'https?://(www\.)?(youtube\.com|youtu\.be)/'
)

COLOR_MUSIC  = 0x1DB954   # Spotify Green
COLOR_ERROR  = 0xED4245
COLOR_INFO   = 0x5865F2

MUSIC_CONFIG_FILE = 'music_config.json'

MUSIC_CFG_DEFAULTS = {
    'enabled':        True,
    'dj_role':        '',
    'default_volume': 50,
    'max_queue':      200,
    'max_duration':   180,   # minutes
    'idle_timeout':   3,     # minutes
}


def _load_guild_music_cfg(gid: str) -> dict:
    """Loads music configuration for a guild from music_config.json / SQLite."""
    try:
        from utils import load
        raw = load(MUSIC_CONFIG_FILE).get(str(gid), {})
    except Exception:
        raw = {}
    cfg = dict(MUSIC_CFG_DEFAULTS)
    cfg.update(raw)
    cfg['max_duration_secs'] = cfg['max_duration'] * 60
    cfg['idle_timeout_secs'] = cfg['idle_timeout'] * 60
    return cfg


# ═════════════════════════════════════════════════════════════════════════════
#  TRACK (Data Class)
# ═════════════════════════════════════════════════════════════════════════════

class Track:
    """Represents a single track in the queue."""

    __slots__ = ('title', 'url', 'webpage_url', 'duration', 'thumbnail',
                 'requester', 'uploader', 'added_at')

    def __init__(self, data: dict, requester: discord.Member):
        self.title       = data.get('title', 'Unknown Title')
        self.url         = _extract_direct_audio_url(data)
        self.webpage_url = data.get('webpage_url') or data.get('url', '')
        self.duration    = data.get('duration') or 0
        self.thumbnail   = data.get('thumbnail') or (data.get('thumbnails', [{}])[0].get('url', '') if data.get('thumbnails') else '')
        self.uploader    = data.get('uploader', 'Unknown Artist')
        self.requester   = requester
        self.added_at    = time.time()

    def duration_str(self) -> str:
        secs = int(self.duration)
        if secs <= 0:
            return '◈ Live'
        h, r = divmod(secs, 3600)
        m, s = divmod(r, 60)
        return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

    def progress_bar(self, current_secs: float, width: int = 15) -> str:
        if self.duration <= 0:
            return '🔴 LIVE'
        ratio = min(current_secs / self.duration, 1.0)
        filled = int(ratio * width)
        bar = '▓' * filled + '░' * (width - filled)
        return f'`{bar}` {self._fmt(current_secs)} / {self.duration_str()}'

    @staticmethod
    def _fmt(secs: float) -> str:
        secs = int(secs)
        h, r = divmod(secs, 3600)
        m, s = divmod(r, 60)
        return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


# ═════════════════════════════════════════════════════════════════════════════
#  GUILD STATE
# ═════════════════════════════════════════════════════════════════════════════

class GuildMusicState:
    """Holds the active music playback state for a single guild."""

    def __init__(self):
        self.queue:         list[Track]              = []
        self.current:       Optional[Track]          = None
        self.voice:         Optional[discord.VoiceClient] = None
        self.text_channel:  Optional[discord.TextChannel] = None
        self.volume:        float                    = 0.5
        self.loop:          bool                     = False
        self.loop_queue:    bool                     = False
        self._idle_task:    Optional[asyncio.Task]   = None
        self._np_message:   Optional[discord.Message] = None
        self._start_time:   float                    = 0.0

    def add(self, track: Track) -> int:
        self.queue.append(track)
        return len(self.queue)

    def add_front(self, track: Track):
        self.queue.insert(0, track)

    def next(self) -> Optional[Track]:
        if self.loop and self.current:
            return copy.copy(self.current)
        if self.loop_queue and self.current:
            self.queue.append(copy.copy(self.current))
        return self.queue.pop(0) if self.queue else None

    def shuffle(self):
        random.shuffle(self.queue)

    def remove(self, idx: int) -> Optional[Track]:
        if 1 <= idx <= len(self.queue):
            return self.queue.pop(idx - 1)
        return None

    def clear(self):
        self.queue.clear()

    def is_playing(self) -> bool:
        return self.voice is not None and self.voice.is_playing()

    def is_paused(self) -> bool:
        return self.voice is not None and self.voice.is_paused()

    def elapsed(self) -> float:
        if self._start_time <= 0:
            return 0.0
        return time.monotonic() - self._start_time


# ═════════════════════════════════════════════════════════════════════════════
#  HELPER: YT-DLP wrapper
# ═════════════════════════════════════════════════════════════════════════════

async def ytdlp_search(query: str, loop: asyncio.AbstractEventLoop,
                        playlist: bool = True) -> list[dict]:
    """
    Search and extract tracks using yt-dlp with multi-stage fallback:
    1. Опит с базовите настройки (и cookies.txt, ако съществува)
    2. Fallback БЕЗ cookies.txt (ако файлът е невалиден или блокиран)
    3. Fallback само с мобилен клиент (Android)
    """
    def _extract_with_opts(opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if not info:
                return []
            if 'entries' in info:
                return [e for e in info['entries'] if e]
            return [info]

    def _blocking():
        base_opts = copy.deepcopy(YTDLP_OPTS)
        base_opts['noplaylist'] = not playlist

        # ── Опит 1: Базова конфигурация ──────────────────────────────────────────
        try:
            entries = _extract_with_opts(base_opts)
            if entries:
                return entries
        except Exception as e:
            print(f"[Music] Опит 1 за '{query}' неуспешен: {e}")

        # ── Опит 2: Премахване на cookies.txt (при повредени/изтекли бисквитки) ────
        if 'cookiefile' in base_opts:
            print(f"[Music] Опит 2: Премахване на cookies.txt за '{query}'...")
            no_cookie_opts = copy.deepcopy(base_opts)
            no_cookie_opts.pop('cookiefile', None)
            try:
                entries = _extract_with_opts(no_cookie_opts)
                if entries:
                    return entries
            except Exception as e:
                print(f"[Music] Опит 2 за '{query}' неуспешен: {e}")

        # ── Опит 3: Чист мобилен клиент (Android) ────────────────────────────────
        print(f"[Music] Опит 3: Принудителен Android клиент за '{query}'...")
        mobile_opts = copy.deepcopy(base_opts)
        mobile_opts.pop('cookiefile', None)
        mobile_opts['extractor_args'] = {
            'youtube': {
                'player_client': ['android'],
            }
        }
        try:
            entries = _extract_with_opts(mobile_opts)
            if entries:
                return entries
        except Exception as e:
            print(f"[Music] Опит 3 за '{query}' неуспешен: {e}")

        return []

    return await loop.run_in_executor(None, _blocking)


# ═════════════════════════════════════════════════════════════════════════════
#  HELPER: Spotify → YouTube
# ═════════════════════════════════════════════════════════════════════════════

def get_spotify_client() -> Optional['spotipy.Spotify']:
    if not SPOTIFY_OK:
        return None
    client_id     = os.environ.get('SPOTIFY_CLIENT_ID')
    client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET')
    if not client_id or not client_secret:
        return None
    try:
        auth = SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        return spotipy.Spotify(auth_manager=auth)
    except Exception:
        return None


async def spotify_to_queries(url: str, sp: 'spotipy.Spotify',
                              loop: asyncio.AbstractEventLoop) -> list[str]:
    match = SPOTIFY_PATTERN.match(url)
    if not match:
        return []
    kind, sid = match.group(1), match.group(2)

    def _blocking():
        queries = []
        try:
            if kind == 'track':
                data = sp.track(sid)
                artists = ', '.join(a['name'] for a in data['artists'])
                queries.append(f"{artists} - {data['name']}")
            elif kind == 'playlist':
                results = sp.playlist_tracks(sid)
                while results:
                    for item in results['items']:
                        tr = item.get('track')
                        if tr:
                            artists = ', '.join(a['name'] for a in tr['artists'])
                            queries.append(f"{artists} - {tr['name']}")
                    results = sp.next(results) if results['next'] else None
            elif kind == 'album':
                results = sp.album_tracks(sid)
                album_info = sp.album(sid)
                album_artists = ', '.join(a['name'] for a in album_info['artists'])
                while results:
                    for tr in results['items']:
                        track_artists = ', '.join(a['name'] for a in tr['artists']) or album_artists
                        queries.append(f"{track_artists} - {tr['name']}")
                    results = sp.next(results) if results['next'] else None
        except Exception:
            pass
        return queries

    return await loop.run_in_executor(None, _blocking)


# ═════════════════════════════════════════════════════════════════════════════
#  EMBEDS
# ═════════════════════════════════════════════════════════════════════════════

def embed_now_playing(state: GuildMusicState) -> discord.Embed:
    tr = state.current
    if not tr:
        return discord.Embed(description='Nothing is currently playing.', color=COLOR_INFO)

    embed = discord.Embed(
        title='🎵 Now Playing',
        description=f'**[{tr.title}]({tr.webpage_url})**',
        color=COLOR_MUSIC,
    )
    embed.add_field(
        name='⏱ Progress',
        value=tr.progress_bar(state.elapsed()),
        inline=False,
    )
    embed.add_field(name='🎤 Artist / Channel', value=tr.uploader, inline=True)
    embed.add_field(name='🔊 Volume', value=f'{int(state.volume*100)}%', inline=True)
    loop_val = '🔂 Track Loop' if state.loop else ('🔁 Queue Loop' if state.loop_queue else '➡ Off')
    embed.add_field(name='↩ Loop', value=loop_val, inline=True)
    if state.queue:
        nxt = state.queue[0]
        embed.add_field(name='⏭ Up Next', value=f'`{nxt.title}`', inline=False)
    if tr.thumbnail:
        embed.set_thumbnail(url=tr.thumbnail)
    embed.set_footer(text=f'Requested by {tr.requester.display_name}',
                     icon_url=tr.requester.display_avatar.url)
    return embed


def embed_queue(state: GuildMusicState, page: int = 1) -> discord.Embed:
    PER_PAGE = 10
    total    = len(state.queue)
    pages    = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page     = max(1, min(page, pages))
    start    = (page - 1) * PER_PAGE
    end      = start + PER_PAGE

    embed = discord.Embed(title='📋 Music Queue', color=COLOR_MUSIC)

    if state.current:
        embed.add_field(
            name='🎵 Now Playing',
            value=f'**{state.current.title}** `{state.current.duration_str()}`',
            inline=False,
        )

    if not state.queue:
        embed.add_field(name='Queue is empty', value='Add more tracks using `/play`!', inline=False)
    else:
        lines = []
        for i, tr in enumerate(state.queue[start:end], start=start + 1):
            lines.append(f'`{i}.` [{tr.title}]({tr.webpage_url}) `{tr.duration_str()}`'
                         f' — {tr.requester.mention}')
        embed.add_field(name=f'Page {page}/{pages}  ({total} tracks)',
                        value='\n'.join(lines), inline=False)

    total_secs = sum(t.duration for t in state.queue)
    h, r = divmod(total_secs, 3600)
    m, s = divmod(r, 60)
    duration_str = f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'
    embed.set_footer(text=f'Total Queue Duration: {duration_str}')
    return embed


def embed_added(track: Track, position: int) -> discord.Embed:
    embed = discord.Embed(
        description=f'✅ Added to queue: **[{track.title}]({track.webpage_url})**',
        color=COLOR_MUSIC,
    )
    embed.add_field(name='Position', value=f'#{position}', inline=True)
    embed.add_field(name='Duration', value=track.duration_str(), inline=True)
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    embed.set_footer(text=f'Requested by {track.requester.display_name}',
                     icon_url=track.requester.display_avatar.url)
    return embed


# ═════════════════════════════════════════════════════════════════════════════
#  COG
# ═════════════════════════════════════════════════════════════════════════════

class Music(commands.Cog):
    """🎵 Music module — High-quality YouTube & Spotify audio streaming."""

    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        self._states: dict[int, GuildMusicState] = {}
        self._sp      = get_spotify_client()

        if not YT_DLP_OK:
            print('⚠️ [Music] yt-dlp is not installed! Run: pip install yt-dlp')
        if not SPOTIFY_OK or not self._sp:
            print('ℹ️ [Music] Spotify support is disabled (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set).')

    def _state(self, gid: int) -> GuildMusicState:
        if gid not in self._states:
            self._states[gid] = GuildMusicState()
        return self._states[gid]

    def _guild_cfg(self, gid: int) -> dict:
        return _load_guild_music_cfg(str(gid))

    def _check_enabled(self, interaction: discord.Interaction) -> bool:
        cfg = self._guild_cfg(interaction.guild_id)
        return cfg.get('enabled', True)

    def _check_dj(self, interaction: discord.Interaction) -> bool:
        cfg     = self._guild_cfg(interaction.guild_id)
        dj_role = str(cfg.get('dj_role', '')).strip()
        if not dj_role:
            return True
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            return False
        if member.guild_permissions.administrator:
            return True
        return any(str(r.id) == dj_role for r in member.roles)

    def _in_same_voice(self, interaction: discord.Interaction) -> bool:
        state = self._state(interaction.guild_id)
        if not state.voice:
            return True
        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice:
            return False
        return member.voice.channel.id == state.voice.channel.id

    async def _ensure_voice(self, interaction: discord.Interaction) -> Optional[GuildMusicState]:
        if not self._check_enabled(interaction):
            await interaction.followup.send(
                embed=discord.Embed(
                    description='❌ The music module is currently disabled for this server.',
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return None

        if not self._check_dj(interaction):
            await interaction.followup.send(
                embed=discord.Embed(
                    description='❌ You need the DJ role to use music commands!',
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return None

        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice:
            await interaction.followup.send(
                embed=discord.Embed(
                    description='❌ You must be connected to a voice channel first!',
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return None

        state = self._state(interaction.guild_id)

        if state.voice and state.voice.is_connected():
            if state.voice.channel.id != member.voice.channel.id:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f'❌ I am already active in {state.voice.channel.mention}!',
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return None
        else:
            try:
                state.voice = await member.voice.channel.connect(self_deaf=True)
            except discord.ClientException:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description='❌ Unable to connect to the voice channel.',
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return None

        state.text_channel = interaction.channel
        self._cancel_idle(state)
        cfg = self._guild_cfg(interaction.guild_id)
        state.volume = cfg['default_volume'] / 100
        return state

    # ── Playback ──────────────────────────────────────────────────────────────

    def _play_track(self, state: GuildMusicState, track: Track):
        if not track.url:
            print(f'❌ [Music] Track has no valid stream URL: {track.title}')
            coro = self._advance(state)
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            return
        try:
            source = discord.FFmpegPCMAudio(track.url, **FFMPEG_OPTS)
            source = discord.PCMVolumeTransformer(source, volume=state.volume)
            state.voice.play(
                source,
                after=lambda e: self._after_play(state, e),
            )
            state._start_time = time.monotonic()
        except Exception as e:
            print(f'❌ [Music] Error starting playback for {track.title}: {e}')
            coro = self._advance(state)
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    def _after_play(self, state: GuildMusicState, error):
        if error:
            print(f'❌ [Music] Playback error: {error}')
        coro = self._advance(state)
        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    async def _advance(self, state: GuildMusicState):
        state.current = state.next()
        if state.current is None:
            state._start_time = 0.0
            self._schedule_idle(state)
            if state.text_channel:
                try:
                    await state.text_channel.send(
                        embed=discord.Embed(
                            description='✅ Queue concluded! Add more tracks with `/play`.',
                            color=COLOR_INFO,
                        )
                    )
                except Exception:
                    pass
            return

        self._play_track(state, state.current)
        if state.text_channel:
            try:
                embed = embed_now_playing(state)
                msg = await state.text_channel.send(embed=embed)
                if state._np_message:
                    try:
                        await state._np_message.delete()
                    except Exception:
                        pass
                state._np_message = msg
            except Exception:
                pass

    # ── Idle / Auto-disconnect ────────────────────────────────────────────────

    def _schedule_idle(self, state: GuildMusicState):
        self._cancel_idle(state)
        state._idle_task = asyncio.create_task(self._idle_disconnect(state))

    def _cancel_idle(self, state: GuildMusicState):
        if state._idle_task and not state._idle_task.done():
            state._idle_task.cancel()
        state._idle_task = None

    async def _idle_disconnect(self, state: GuildMusicState):
        await asyncio.sleep(IDLE_TIMEOUT)
        if state.voice and state.voice.is_connected():
            if state.text_channel:
                try:
                    await state.text_channel.send(
                        embed=discord.Embed(
                            description=f'💤 Disconnected from voice after {IDLE_TIMEOUT//60} minutes of inactivity.',
                            color=COLOR_INFO,
                        )
                    )
                except Exception:
                    pass
            await state.voice.disconnect(force=True)
            state.voice    = None
            state.current  = None
            state.queue.clear()

    # ── Slash Command: /play ──────────────────────────────────────────────────

    @app_commands.command(name='play', description='🎵 Play a track or playlist from YouTube / Spotify')
    @app_commands.describe(query='Search term, YouTube URL/Playlist, or Spotify URL')
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        if not YT_DLP_OK:
            await interaction.followup.send(
                embed=discord.Embed(description='❌ yt-dlp is not installed!', color=COLOR_ERROR)
            )
            return

        state = await self._ensure_voice(interaction)
        if not state:
            return

        cfg = self._guild_cfg(interaction.guild_id)
        max_duration = cfg['max_duration_secs']
        max_queue = cfg['max_queue']

        # ── Spotify ────────────────────────────────────────────────────────
        is_spotify = SPOTIFY_PATTERN.match(query)
        if is_spotify:
            if not self._sp:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=(
                            '❌ Spotify support is disabled. Set `SPOTIFY_CLIENT_ID` and '
                            '`SPOTIFY_CLIENT_SECRET` in `.env`.'
                        ),
                        color=COLOR_ERROR,
                    )
                )
                return
            await interaction.followup.send(
                embed=discord.Embed(
                    description='🔍 Fetching Spotify metadata...', color=COLOR_INFO
                )
            )
            queries = await spotify_to_queries(query, self._sp, self.bot.loop)
            if not queries:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description='❌ No tracks found on Spotify.', color=COLOR_ERROR
                    )
                )
                return

            loaded = 0
            for q in queries:
                if len(state.queue) >= max_queue and state.current:
                    break
                results = await ytdlp_search(q, self.bot.loop, playlist=False)
                if results:
                    d = results[0]
                    if d.get('duration', 0) > max_duration:
                        continue
                    track = Track(d, interaction.user)
                    state.add(track)
                    loaded += 1

            if loaded == 0:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description='❌ Could not find YouTube audio streams for these tracks.',
                        color=COLOR_ERROR,
                    )
                )
                return

            await interaction.followup.send(
                embed=discord.Embed(
                    description=f'✅ Added **{loaded}** track(s) from Spotify to the queue!',
                    color=COLOR_MUSIC,
                )
            )
            if not state.is_playing() and not state.is_paused():
                state.current = state.next()
                if state.current:
                    self._play_track(state, state.current)
            return

        # ── YouTube Search / Direct URL ────────────────────────────────────
        if not YOUTUBE_PATTERN.match(query) and not query.startswith('http'):
            search_query = f'ytsearch:{query}'
        else:
            search_query = query

        await interaction.followup.send(
            embed=discord.Embed(description=f'🔍 Searching: `{query}`...', color=COLOR_INFO)
        )
        results = await ytdlp_search(search_query, self.bot.loop, playlist=True)

        if not results:
            await interaction.followup.send(
                embed=discord.Embed(description='❌ No results found!', color=COLOR_ERROR)
            )
            return

        results = [r for r in results if (r.get('duration') or 0) <= max_duration or r.get('duration') == 0]

        if not results:
            await interaction.followup.send(
                embed=discord.Embed(
                    description=f'❌ All results exceed maximum track duration limit ({max_duration//60} mins).',
                    color=COLOR_ERROR,
                )
            )
            return

        if len(results) > 1:
            added = 0
            for d in results:
                if len(state.queue) >= max_queue:
                    break
                track = Track(d, interaction.user)
                state.add(track)
                added += 1
            await interaction.followup.send(
                embed=discord.Embed(
                    description=f'✅ Playlist added: **{added}** tracks queued!',
                    color=COLOR_MUSIC,
                )
            )
        else:
            track = Track(results[0], interaction.user)
            if state.is_playing() or state.is_paused():
                pos = state.add(track)
                await interaction.followup.send(embed=embed_added(track, pos))
            else:
                state.current = track
                self._play_track(state, track)
                await interaction.followup.send(embed=embed_now_playing(state))
                return

        if not state.is_playing() and not state.is_paused():
            state.current = state.next()
            if state.current:
                self._play_track(state, state.current)

    # ── Slash Command: /skip ──────────────────────────────────────────────────

    @app_commands.command(name='skip', description='⏭ Skip to the next track')
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        if not state.is_playing() and not state.is_paused():
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ Nothing is currently playing!', color=COLOR_ERROR),
                ephemeral=True,
            )
        state.loop = False
        state.voice.stop()
        await interaction.response.send_message(
            embed=discord.Embed(description='⏭ Skipped!', color=COLOR_MUSIC)
        )

    # ── Slash Command: /stop ──────────────────────────────────────────────────

    @app_commands.command(name='stop', description='⏹ Stop playback and clear the queue')
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        state.queue.clear()
        state.loop       = False
        state.loop_queue = False
        state.current    = None
        if state.voice and state.voice.is_playing():
            state.voice.stop()
        await interaction.response.send_message(
            embed=discord.Embed(description='⏹ Playback stopped and queue cleared!', color=COLOR_MUSIC)
        )

    # ── Slash Command: /pause ─────────────────────────────────────────────────

    @app_commands.command(name='pause', description='⏸ Pause or resume playback')
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        if state.is_playing():
            state.voice.pause()
            await interaction.response.send_message(
                embed=discord.Embed(description='⏸ Playback paused', color=COLOR_MUSIC)
            )
        elif state.is_paused():
            state.voice.resume()
            await interaction.response.send_message(
                embed=discord.Embed(description='▶️ Playback resumed', color=COLOR_MUSIC)
            )
        else:
            await interaction.response.send_message(
                embed=discord.Embed(description='❌ Nothing is currently playing!', color=COLOR_ERROR),
                ephemeral=True,
            )

    # ── Slash Command: /nowplaying ────────────────────────────────────────────

    @app_commands.command(name='nowplaying', description='🎵 Show currently playing track')
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction):
        state = self._state(interaction.guild_id)
        if not state.current:
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ Nothing is currently playing!', color=COLOR_ERROR),
                ephemeral=True,
            )
        await interaction.response.send_message(embed=embed_now_playing(state))

    # ── Slash Command: /queue ─────────────────────────────────────────────────

    @app_commands.command(name='queue', description='📋 View the music queue')
    @app_commands.describe(page='Queue page number')
    @app_commands.guild_only()
    async def queue_cmd(self, interaction: discord.Interaction, page: int = 1):
        state = self._state(interaction.guild_id)
        await interaction.response.send_message(embed=embed_queue(state, page))

    # ── Slash Command: /volume ────────────────────────────────────────────────

    @app_commands.command(name='volume', description='🔊 Set playback volume (0-100)')
    @app_commands.describe(level='Volume percentage (0-100)')
    @app_commands.guild_only()
    async def volume(self, interaction: discord.Interaction, level: int):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        if not 0 <= level <= 100:
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ Volume level must be between 0 and 100.', color=COLOR_ERROR),
                ephemeral=True,
            )
        state.volume = level / 100
        if state.voice and state.voice.source:
            state.voice.source.volume = state.volume
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f'🔊 Volume set to **{level}%**',
                color=COLOR_MUSIC,
            )
        )

    # ── Slash Command: /loop ──────────────────────────────────────────────────

    @app_commands.command(name='loop', description='↩ Cycle loop mode: Off / Track Loop / Queue Loop')
    @app_commands.guild_only()
    async def loop_cmd(self, interaction: discord.Interaction):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        if not state.loop and not state.loop_queue:
            state.loop = True
            mode = '🔂 Track Loop Enabled'
        elif state.loop and not state.loop_queue:
            state.loop       = False
            state.loop_queue = True
            mode = '🔁 Queue Loop Enabled'
        else:
            state.loop       = False
            state.loop_queue = False
            mode = '➡ Loop Disabled'

        await interaction.response.send_message(
            embed=discord.Embed(description=f'{mode}', color=COLOR_MUSIC)
        )

    # ── Slash Command: /shuffle ───────────────────────────────────────────────

    @app_commands.command(name='shuffle', description='🔀 Shuffle the music queue')
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        if not state.queue:
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ The queue is empty!', color=COLOR_ERROR),
                ephemeral=True,
            )
        state.shuffle()
        await interaction.response.send_message(
            embed=discord.Embed(description='🔀 Queue shuffled!', color=COLOR_MUSIC)
        )

    # ── Slash Command: /remove ────────────────────────────────────────────────

    @app_commands.command(name='remove', description='🗑 Remove a specific track from queue')
    @app_commands.describe(position='Track position in queue (1-based index)')
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction, position: int):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        removed = state.remove(position)
        if removed:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f'🗑 Removed: **{removed.title}**',
                    color=COLOR_MUSIC,
                )
            )
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f'❌ Invalid position `{position}`. See `/queue` for numbers.',
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )

    # ── Slash Command: /clear ─────────────────────────────────────────────────

    @app_commands.command(name='clear', description='🧹 Clear all upcoming tracks in the queue')
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        count = len(state.queue)
        state.clear()
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f'🧹 Cleared **{count}** track(s) from the queue.',
                color=COLOR_MUSIC,
            )
        )

    # ── Slash Command: /disconnect ────────────────────────────────────────────

    @app_commands.command(name='disconnect', description='👋 Disconnect the bot from the voice channel')
    @app_commands.guild_only()
    async def disconnect(self, interaction: discord.Interaction):
        state = self._state(interaction.guild_id)
        if not self._in_same_voice(interaction):
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ You must be in the same voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        if not state.voice or not state.voice.is_connected():
            return await interaction.response.send_message(
                embed=discord.Embed(description='❌ Not connected to any voice channel!', color=COLOR_ERROR),
                ephemeral=True,
            )
        state.queue.clear()
        state.current    = None
        state.loop       = False
        state.loop_queue = False
        self._cancel_idle(state)
        await state.voice.disconnect(force=True)
        state.voice = None
        await interaction.response.send_message(
            embed=discord.Embed(description='👋 Disconnected!', color=COLOR_MUSIC)
        )

    # ── Prefix Commands (Aliases) ─────────────────────────────────────────────

    @commands.command(name='play', aliases=['p'])
    async def play_prefix(self, ctx: commands.Context, *, query: str):
        """Play a track or playlist from YouTube or Spotify."""
        await ctx.typing()
        await self._play_prefix_impl(ctx, query)

    async def _play_prefix_impl(self, ctx: commands.Context, query: str):
        if not YT_DLP_OK:
            return await ctx.reply('❌ yt-dlp is not installed!')

        member = ctx.author
        if not member.voice:
            return await ctx.reply('❌ You must be in a voice channel!')

        state = self._state(ctx.guild.id)
        if state.voice and state.voice.is_connected():
            if state.voice.channel.id != member.voice.channel.id:
                return await ctx.reply(f'❌ Already connected to {state.voice.channel.mention}!')
        else:
            state.voice = await member.voice.channel.connect(self_deaf=True)

        state.text_channel = ctx.channel
        self._cancel_idle(state)

        is_spotify = SPOTIFY_PATTERN.match(query)
        if is_spotify:
            if not self._sp:
                return await ctx.reply('❌ Spotify support is disabled.')
            msg = await ctx.reply('🔍 Fetching Spotify metadata...')
            queries = await spotify_to_queries(query, self._sp, self.bot.loop)
            if not queries:
                return await msg.edit(content='❌ No tracks found on Spotify.')
            loaded = 0
            for q in queries:
                if len(state.queue) >= MAX_QUEUE_SIZE:
                    break
                results = await ytdlp_search(q, self.bot.loop, playlist=False)
                if results:
                    d = results[0]
                    if d.get('duration', 0) <= MAX_DURATION:
                        state.add(Track(d, member))
                        loaded += 1
            await msg.edit(content=f'✅ Added **{loaded}** track(s) from Spotify!')
        else:
            if not YOUTUBE_PATTERN.match(query) and not query.startswith('http'):
                search_query = f'ytsearch:{query}'
            else:
                search_query = query
            msg = await ctx.reply(f'🔍 Searching: `{query}`...')
            results = await ytdlp_search(search_query, self.bot.loop, playlist=True)
            results = [r for r in results if (r.get('duration') or 0) <= MAX_DURATION or r.get('duration') == 0]
            if not results:
                return await msg.edit(content='❌ No results found!')
            if len(results) > 1:
                added = 0
                for d in results:
                    if len(state.queue) >= MAX_QUEUE_SIZE:
                        break
                    state.add(Track(d, member))
                    added += 1
                await msg.edit(content=f'✅ Added playlist with **{added}** tracks!')
            else:
                track = Track(results[0], member)
                if state.is_playing() or state.is_paused():
                    pos = state.add(track)
                    await msg.edit(content=f'✅ Added to queue at **#{pos}**: {track.title}')
                else:
                    state.current = track
                    self._play_track(state, track)
                    await msg.edit(content=None, embed=embed_now_playing(state))
                    return

        if not state.is_playing() and not state.is_paused():
            state.current = state.next()
            if state.current:
                self._play_track(state, state.current)

    @commands.command(name='skip', aliases=['s'])
    async def skip_prefix(self, ctx: commands.Context):
        state = self._state(ctx.guild.id)
        if not state.is_playing() and not state.is_paused():
            return await ctx.reply('❌ Nothing is currently playing!')
        state.loop = False
        state.voice.stop()
        await ctx.reply('⏭ Skipped!')

    @commands.command(name='stop')
    async def stop_prefix(self, ctx: commands.Context):
        state = self._state(ctx.guild.id)
        state.queue.clear()
        state.loop       = False
        state.loop_queue = False
        state.current    = None
        if state.voice and state.voice.is_playing():
            state.voice.stop()
        await ctx.reply('⏹ Stopped and queue cleared!')

    @commands.command(name='pause', aliases=['resume'])
    async def pause_prefix(self, ctx: commands.Context):
        state = self._state(ctx.guild.id)
        if state.is_playing():
            state.voice.pause()
            await ctx.reply('⏸ Paused')
        elif state.is_paused():
            state.voice.resume()
            await ctx.reply('▶️ Resumed')
        else:
            await ctx.reply('❌ Nothing is currently playing!')

    @commands.command(name='np', aliases=['nowplaying'])
    async def nowplaying_prefix(self, ctx: commands.Context):
        state = self._state(ctx.guild.id)
        if not state.current:
            return await ctx.reply('❌ Nothing is currently playing!')
        await ctx.send(embed=embed_now_playing(state))

    @commands.command(name='queue', aliases=['q'])
    async def queue_prefix(self, ctx: commands.Context, page: int = 1):
        state = self._state(ctx.guild.id)
        await ctx.send(embed=embed_queue(state, page))

    @commands.command(name='volume', aliases=['vol'])
    async def volume_prefix(self, ctx: commands.Context, level: int):
        state = self._state(ctx.guild.id)
        if not 0 <= level <= 100:
            return await ctx.reply('❌ Volume must be between 0 and 100.')
        state.volume = level / 100
        if state.voice and state.voice.source:
            state.voice.source.volume = state.volume
        await ctx.reply(f'🔊 Volume set to **{level}%**')

    @commands.command(name='dc', aliases=['leave', 'disconnect'])
    async def disconnect_prefix(self, ctx: commands.Context):
        state = self._state(ctx.guild.id)
        if not state.voice or not state.voice.is_connected():
            return await ctx.reply('❌ Not connected to a voice channel!')
        state.queue.clear()
        state.current = None
        self._cancel_idle(state)
        await state.voice.disconnect(force=True)
        state.voice = None
        await ctx.reply('👋 Disconnected!')

    # ── Listener ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return
        state = self._states.get(member.guild.id)
        if not state or not state.voice or not state.voice.is_connected():
            return
        channel = state.voice.channel
        humans = [m for m in channel.members if not m.bot]
        if not humans:
            self._schedule_idle(state)


# ═════════════════════════════════════════════════════════════════════════════
#  SETUP
# ═════════════════════════════════════════════════════════════════════════════

async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
    print('✅ Music cog loaded')