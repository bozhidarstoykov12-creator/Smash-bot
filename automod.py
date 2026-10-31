"""
AutoMod Cog — SMASH BOT
========================
Configurable automatic moderation:
  • Anti-spam        — deletes / times-out users who flood messages
  • Anti-caps        — deletes all-caps messages
  • Mass-mention     — deletes messages that ping too many users
  • Emoji spam       — deletes messages with excessive emojis
  • Repeated message — deletes copy-paste spam
  • Anti-raid        — locks the server when many users join at once
  • Link filter      — blocks URLs with an optional domain whitelist

All settings are per-guild and stored in automod_config.json.
Members with Manage Messages (or higher) are always exempt.
"""

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
import re
import asyncio
import time
from collections import defaultdict, deque
from urllib.parse import urlparse
from PIL import Image
import aiohttp
from utils import load, save, ok, err, rate_limit_wait, doc_lock

# ── Storage ────────────────────────────────────────────────────────────────────
AUTOMOD_FILE = "automod_config.json"

DEFAULT: dict = {
    "spam":     {"enabled": False, "threshold": 5,  "seconds": 5,  "action": "delete"},
    "caps":     {"enabled": False, "threshold": 70, "min_length": 15},
    "mentions": {"enabled": False, "max": 5},
    "emojis":   {"enabled": False, "max": 15},
    "repeated": {"enabled": False, "count": 3,  "seconds": 10},
    "raid":     {"enabled": False, "joins": 10, "seconds": 30},
    "links":    {"enabled": False, "whitelist": []},
    "scam_images": {"enabled": False, "action": "delete", "ocr": True, "keywords": [], "hashes": []},
}

SCAM_HASH_THRESHOLD = 24
SCAM_IMAGE_MAX_SIZE = 8 * 1024 * 1024
SCAM_IMAGE_LIMIT = 2
SCAM_OCR_URL = "https://api.ocr.space/parse/image"
OCR_KEY = os.getenv("_OCR_KEY")
DEFAULT_SCAM_HASHES = [
    {
        "ahash": "fffffff8380d380c30003800000020003f003ffc1e001f009ff8dc0030003ff8",
        "dhash": "3d990f748f669c06bd98ad98a7e1b693bd878616cf808786c3aecf5a96d204ce",
    },
    {
        "ahash": "ffff007f00010000000000007c007fe07fe07fc07fc8ff80ff06ff00fe00fc00",
        "dhash": "40008000800282048a468a433e2002300378006d126723d31d910193830f7f33",
    },
]
SCAM_TEXT_WEIGHTS = [
    ("withdrawal", 4),
    ("withdraw", 3),
    ("promo code", 4),
    ("promo", 2),
    ("giveaway", 2),
    ("airdrop", 3),
    ("crypto", 2),
    ("usdt", 4),
    ("casino", 3),
    ("wallet", 2),
    ("claim", 2),
    ("deposit", 2),
    ("register", 2),
    ("bonus", 2),
    ("fuzawin", 4),
    ("fuzaw", 4),
    ("mrbeast", 4),
]

# In-memory per-guild trackers (reset on bot restart — acceptable)
_spam_tracker:   dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
_repeat_tracker: dict[str, dict[str, list]]  = defaultdict(lambda: defaultdict(list))
_raid_tracker:   dict[str, deque]            = defaultdict(deque)

# Regex helpers
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF]"       # most emoji blocks
    r"|[\u2600-\u27BF]"              # misc symbols & dingbats
    r"|<a?:[a-zA-Z0-9_]+:\d+>",     # custom Discord emojis
    re.UNICODE,
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _load_cfg(guild_id: str) -> dict:
    stored = load(AUTOMOD_FILE).get(str(guild_id), {})
    merged: dict = {}
    for key, defaults in DEFAULT.items():
        merged[key] = {}
        for d_key, d_val in defaults.items():
            if isinstance(d_val, list):
                merged[key][d_key] = list(d_val)
            elif isinstance(d_val, dict):
                merged[key][d_key] = dict(d_val)
            else:
                merged[key][d_key] = d_val
        merged[key].update(stored.get(key, {}))
    return merged


def _save_cfg(guild_id: str, data: dict):
    # RACE FIX: called from BOTH bot commands and Flask dashboard threads
    # (api_automod_save) — must take the shared document lock. RLock is
    # re-entrant, so the dashboard's outer locked() nest is safe.
    with doc_lock(AUTOMOD_FILE):
        cfg = load(AUTOMOD_FILE)
        cfg[str(guild_id)] = data
        save(AUTOMOD_FILE, cfg)


def _exempt(member: discord.Member) -> bool:
    """Return True for bots or members who can manage messages."""
    return member.bot or member.guild_permissions.manage_messages


def _normalize_host(raw: str) -> str:
    """Extract and normalize a hostname from a URL string.

    Handles both schemed (https://…) and bare (www.example.com/path) URLs.
    Strips exactly the "www." prefix (not any leading set of chars).
    """
    # urlparse needs a scheme to extract .hostname reliably
    if not raw.lower().startswith(("http://", "https://")):
        raw = "http://" + raw
    try:
        host = urlparse(raw).hostname or ""
    except Exception:
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]   # exact prefix strip — NOT lstrip()
    return host


def _url_allowed(url: str, whitelist: list[str]) -> bool:
    """Return True if the URL's hostname exactly matches or is a subdomain of
    a whitelisted domain.

    Matching rules (whitelist = ["youtube.com", "discord.gg"]):
        "https://youtube.com/watch"        → ALLOWED  (exact)
        "https://www.youtube.com/watch"    → ALLOWED  (www. stripped)
        "https://music.youtube.com"        → ALLOWED  (subdomain)
        "https://evil-youtube.com/bad"     → BLOCKED  (not a subdomain)
        "https://evil.youtube.com.bad.tld" → BLOCKED  (not a subdomain)
        "www.discord.gg/invite"            → ALLOWED  (bare URL, no scheme)
    """
    if not whitelist:
        return False  # empty whitelist = block everything
    host = _normalize_host(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in whitelist)


def _normalize_text(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _normalize_keyword(keyword: str) -> str:
    return _normalize_text(keyword)


def _image_attachment(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.startswith("image/"):
        return True
    name = (att.filename or "").lower()
    return name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"))


def _hamming_distance_hex(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _average_hash(image: Image.Image) -> str:
    img = image.convert("L").resize((16, 16), Image.Resampling.LANCZOS)
    pixels = list(img.getdata())
    mean = sum(pixels) / len(pixels)
    value = 0
    for pixel in pixels:
        value = (value << 1) | (1 if pixel > mean else 0)
    return f"{value:064x}"


def _difference_hash(image: Image.Image) -> str:
    img = image.convert("L").resize((17, 16), Image.Resampling.LANCZOS)
    pixels = img.load()
    value = 0
    for y in range(16):
        for x in range(16):
            value = (value << 1) | (1 if pixels[x, y] > pixels[x + 1, y] else 0)
    return f"{value:064x}"


def compute_scam_image_hashes(image_bytes: bytes) -> dict[str, str]:
    with Image.open(BytesIO(image_bytes)) as img:
        return {"ahash": _average_hash(img), "dhash": _difference_hash(img)}


def scam_hash_matches(candidate: dict[str, str], blocklist: list[dict[str, str]] | None = None) -> tuple[bool, int, int]:
    entries = list(DEFAULT_SCAM_HASHES) + list(blocklist or [])
    cand_a = candidate.get("ahash", "")
    cand_d = candidate.get("dhash", "")
    best_a = 256
    best_d = 256
    for entry in entries:
        da = _hamming_distance_hex(cand_a, entry["ahash"]) if entry.get("ahash") else 256
        dd = _hamming_distance_hex(cand_d, entry["dhash"]) if entry.get("dhash") else 256
        best_a = min(best_a, da)
        best_d = min(best_d, dd)
        if da <= SCAM_HASH_THRESHOLD or dd <= SCAM_HASH_THRESHOLD:
            return True, da, dd
    return False, best_a, best_d


def _scam_text_score(text: str, extra_keywords: list[str] | None = None) -> int:
    norm = _normalize_text(text)
    score = 0
    if not norm:
        return 0
    if _URL_RE.search(text):
        score += 1
    if re.search(r"free\s*\$", norm):
        score += 3
    for phrase, weight in SCAM_TEXT_WEIGHTS:
        if phrase in norm:
            score += weight
    for keyword in extra_keywords or []:
        keyword_norm = _normalize_keyword(keyword)
        if keyword_norm and keyword_norm in norm:
            score += 3
    return score


async def _ocr_image_text(image_bytes: bytes) -> str:
    if not OCR_KEY:
        return ""
    try:
        form = aiohttp.FormData()
        form.add_field("apikey", OCR_KEY)
        form.add_field("OCREngine", "2")
        form.add_field("language", "eng")
        form.add_field("file", image_bytes, filename="image.png", content_type="application/octet-stream")
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(SCAM_OCR_URL, data=form) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json(content_type=None)
    except Exception:
        return ""

    text_parts = []
    for item in data.get("ParsedResults", []) or []:
        parsed = item.get("ParsedText")
        if parsed:
            text_parts.append(parsed)
    return "\n".join(text_parts)


async def _compute_image_hashes(image_bytes: bytes) -> dict[str, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, compute_scam_image_hashes, image_bytes)


# Module-level suppression set: message IDs deleted by AutoMod.
# logger.py reads this to avoid double-logging automod deletions.
_automod_deleted: dict[int, float] = {}   # msg_id → monotonic time of deletion
_DELETED_TTL = 12.0                        # seconds before the entry expires


def mark_deleted_by_automod(msg_id: int) -> None:
    """Record that msg_id was deleted by AutoMod (call BEFORE awaiting the delete)."""
    _automod_deleted[msg_id] = time.monotonic()
    cutoff = time.monotonic() - _DELETED_TTL
    stale = [k for k, v in _automod_deleted.items() if v < cutoff]
    for k in stale:
        del _automod_deleted[k]


def is_deleted_by_automod(msg_id: int) -> bool:
    """Return True if this message was recently deleted by AutoMod."""
    ts = _automod_deleted.get(msg_id)
    if ts is None:
        return False
    if time.monotonic() - ts > _DELETED_TTL:
        _automod_deleted.pop(msg_id, None)
        return False
    return True


async def _log(bot, guild: discord.Guild, title: str, description: str):
    """Send an AutoMod action embed to the guild's log channel.
    Reads the same mod_config.json / log_channel key as moderation.py so
    admins only need to configure one channel for all mod events.
    """
    mc = load("mod_config.json").get(str(guild.id), {})
    if not mc.get("logging"):
        return
    ch_id = mc.get("log_channel")
    if not ch_id:
        return
    ch = bot.get_channel(int(ch_id))
    if not ch:
        return
    em = discord.Embed(
        title=title,
        description=description,
        color=0xff6b00,
        timestamp=datetime.now(timezone.utc),
    )
    em.set_footer(text="AutoMod")
    try:
        await ch.send(embed=em)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def _punish(bot, message: discord.Message, reason: str, action: str = "delete"):
    """Delete the offending message and optionally timeout the author."""
    # Mark BEFORE awaiting the delete so logger.on_message_delete skips it
    mark_deleted_by_automod(message.id)
    try:
        await rate_limit_wait("delete_message", message.channel.id)
        await message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass

    if action == "timeout":
        try:
            until = discord.utils.utcnow() + timedelta(minutes=5)
            await message.author.timeout(until, reason=f"AutoMod: {reason}")
        except (discord.Forbidden, discord.HTTPException):
            pass

    preview = message.content[:200] or "*[no text]*"
    await _log(
        bot, message.guild,
        f"🛡️ AutoMod — {reason}",
        f"**User:** {message.author.mention} (`{message.author.id}`)\n"
        f"**Channel:** {message.channel.mention}\n"
        f"**Content:** {preview}",
    )

    # Brief ephemeral-style notice (auto-deletes after 8s)
    try:
        notice = await message.channel.send(
            embed=discord.Embed(
                description=f"⚠️ {message.author.mention}, your message was removed — **{reason}**.",
                color=0xffa500,
            )
        )
        await asyncio.sleep(8)
        await rate_limit_wait("delete_message", notice.channel.id)
        await notice.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


# ── Cog ────────────────────────────────────────────────────────────────────────
class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── on_message ─────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or _exempt(message.author):
            return

        gid = str(message.guild.id)
        uid = str(message.author.id)
        cfg = _load_cfg(gid)
        now = time.monotonic()

        # ── 1. Anti-spam ───────────────────────────────────────────────────────
        if cfg["spam"]["enabled"]:
            threshold = cfg["spam"]["threshold"]
            window    = cfg["spam"]["seconds"]
            action    = cfg["spam"].get("action", "delete")
            bucket    = _spam_tracker[gid][uid]
            bucket.append(now)
            while bucket and bucket[0] < now - window:
                bucket.popleft()
            if len(bucket) >= threshold:
                bucket.clear()
                await _punish(self.bot, message, "Spam", action)
                return

        # ── 2. Anti-caps ───────────────────────────────────────────────────────
        if cfg["caps"]["enabled"] and message.content:
            letters = [c for c in message.content if c.isalpha()]
            min_len = cfg["caps"]["min_length"]
            if len(letters) >= min_len:
                pct = sum(1 for c in letters if c.isupper()) / len(letters) * 100
                if pct >= cfg["caps"]["threshold"]:
                    await _punish(self.bot, message, "Excessive Caps")
                    return

        # ── 3. Mass mention ────────────────────────────────────────────────────
        if cfg["mentions"]["enabled"]:
            total = len(message.mentions) + len(message.role_mentions)
            if total > cfg["mentions"]["max"]:
                await _punish(self.bot, message, "Mass Mention")
                return

        # ── 4. Emoji spam ──────────────────────────────────────────────────────
        if cfg["emojis"]["enabled"] and message.content:
            count = len(_EMOJI_RE.findall(message.content))
            if count > cfg["emojis"]["max"]:
                await _punish(self.bot, message, "Emoji Spam")
                return

        # ── 5. Repeated message ────────────────────────────────────────────────
        if cfg["repeated"]["enabled"] and message.content:
            reps    = cfg["repeated"]
            history = _repeat_tracker[gid][uid]
            key     = message.content.strip().lower()
            history.append((now, key))
            cutoff  = now - reps["seconds"]
            history[:] = [(t, c) for t, c in history if t > cutoff]
            same = sum(1 for _, c in history if c == key)
            if same >= reps["count"]:
                _repeat_tracker[gid][uid].clear()
                await _punish(self.bot, message, "Repeated Message")
                return

        # ── 6. Link filter ─────────────────────────────────────────────────────
        if cfg["links"]["enabled"] and message.content:
            # Normalise whitelist entries the same way _url_allowed() normalises hosts
            raw_wl = cfg["links"].get("whitelist", [])
            whitelist = [
                (w[4:] if w.lower().startswith("www.") else w).lower()
                for w in raw_wl
            ]
            urls = _URL_RE.findall(message.content)
            blocked = [u for u in urls if not _url_allowed(u, whitelist)]
            if blocked:
                await _punish(self.bot, message, "Blocked Link")
                return

        # ── 7. Scam image filter ───────────────────────────────────────────
        if cfg.get("scam_images", {}).get("enabled"):
            scam_cfg = cfg["scam_images"]
            image_attachments = [att for att in message.attachments if _image_attachment(att)]
            if image_attachments:
                extra_keywords = scam_cfg.get("keywords", [])
                content_score = _scam_text_score(message.content or "", extra_keywords)
                if content_score >= 3:
                    await _punish(self.bot, message, "Scam Image", scam_cfg.get("action", "delete"))
                    return

                for att in image_attachments[:SCAM_IMAGE_LIMIT]:
                    try:
                        if att.size and att.size > SCAM_IMAGE_MAX_SIZE:
                            continue
                        data = await att.read()
                        if not data:
                            continue
                        hashes = await _compute_image_hashes(data)
                        matched, _, _ = scam_hash_matches(hashes, scam_cfg.get("hashes", []))
                        if matched:
                            await _punish(self.bot, message, "Scam Image", scam_cfg.get("action", "delete"))
                            return
                        if scam_cfg.get("ocr", True):
                            ocr_text = await _ocr_image_text(data)
                            if _scam_text_score(message.content or "", extra_keywords) + _scam_text_score(ocr_text, extra_keywords) >= 3:
                                await _punish(self.bot, message, "Scam Image", scam_cfg.get("action", "delete"))
                                return
                    except Exception:
                        continue

    # ── on_member_join (anti-raid) ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        gid = str(member.guild.id)
        cfg = _load_cfg(gid)
        if not cfg["raid"]["enabled"]:
            return

        now    = time.monotonic()
        bucket = _raid_tracker[gid]
        bucket.append(now)
        limit = now - cfg["raid"]["seconds"]
        while bucket and bucket[0] < limit:
            bucket.popleft()

        threshold = cfg["raid"]["joins"]
        if len(bucket) >= threshold:
            bucket.clear()
            locked = 0
            for ch in member.guild.text_channels:
                try:
                    ow = ch.overwrites_for(member.guild.default_role)
                    ow.send_messages = False
                    await ch.set_permissions(member.guild.default_role, overwrite=ow,
                                             reason="AutoMod: Anti-Raid triggered")
                    locked += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass

            await _log(
                self.bot, member.guild,
                "🚨 Anti-Raid — Server Locked",
                f"**Trigger:** {threshold} joins in {cfg['raid']['seconds']}s\n"
                f"**Locked channels:** {locked}\n"
                f"⚠️ Manually re-enable send permissions or use `/unlock` when safe.",
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  Slash commands  —  /automod  group
    # ══════════════════════════════════════════════════════════════════════════
    automod_group = app_commands.Group(
        name="automod",
        description="Configure automatic moderation",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ── /automod status ────────────────────────────────────────────────────────
    @automod_group.command(name="status", description="Show all AutoMod settings for this server")
    async def am_status(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)

        rows = {
            "spam":     f"Spam — {cfg['spam']['threshold']} msgs in {cfg['spam']['seconds']}s → *{cfg['spam']['action']}*",
            "caps":     f"Caps — >{cfg['caps']['threshold']}% caps in ≥{cfg['caps']['min_length']} letters",
            "mentions": f"Mass Mention — >{cfg['mentions']['max']} mentions",
            "emojis":   f"Emoji Spam — >{cfg['emojis']['max']} emojis",
            "repeated": f"Repeated Msg — {cfg['repeated']['count']}× same in {cfg['repeated']['seconds']}s",
            "raid":     f"Anti-Raid — {cfg['raid']['joins']} joins in {cfg['raid']['seconds']}s → *lock server*",
            "links":    f"Link Filter — whitelist: {', '.join(cfg['links']['whitelist']) or 'none (block all)'}",
            "scam_images": f"Scam Images — {('enabled' if cfg['scam_images']['enabled'] else 'disabled')}, action: {cfg['scam_images']['action']}, OCR: {'on' if cfg['scam_images'].get('ocr', True) else 'off'}, hashes: {len(cfg['scam_images'].get('hashes', []))}",
        }
        lines = [
            f"{'✅' if cfg[k]['enabled'] else '❌'}  {label}"
            for k, label in rows.items()
        ]
        em = discord.Embed(
            title="🛡️ AutoMod — Current Settings",
            description="\n".join(lines),
            color=0x5865f2,
            timestamp=datetime.now(timezone.utc),
        )
        em.set_footer(text=interaction.guild.name)
        await interaction.response.send_message(embed=em, ephemeral=True)

    # ── /automod spam ─────────────────────────────────────────────────────────
    @automod_group.command(name="spam", description="Punish users who send too many messages in a short time")
    @app_commands.describe(
        enabled="Turn on or off",
        threshold="Number of messages that triggers the action (default: 5)",
        seconds="Time window in seconds (default: 5)",
        action="delete (remove messages) or timeout (5-min timeout)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="delete", value="delete"),
        app_commands.Choice(name="timeout (5 min)", value="timeout"),
    ])
    async def am_spam(self, interaction: discord.Interaction,
                      enabled: bool, threshold: int = 5, seconds: int = 5, action: str = "delete"):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)
        cfg["spam"].update({"enabled": enabled, "threshold": threshold, "seconds": seconds, "action": action})
        _save_cfg(gid, cfg)
        s = "✅ Enabled" if enabled else "❌ Disabled"
        await interaction.response.send_message(embed=ok(f"Spam Filter — {s}",
            f"Trigger: **{threshold}** messages in **{seconds}s** → action: **{action}**"), ephemeral=True)

    # ── /automod caps ─────────────────────────────────────────────────────────
    @automod_group.command(name="caps", description="Delete messages with too many capital letters")
    @app_commands.describe(
        enabled="Turn on or off",
        threshold="Percentage of capital letters that triggers deletion (default: 70)",
        min_length="Minimum number of letters before the rule applies (default: 15)",
    )
    async def am_caps(self, interaction: discord.Interaction,
                      enabled: bool, threshold: int = 70, min_length: int = 15):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)
        cfg["caps"].update({"enabled": enabled, "threshold": threshold, "min_length": min_length})
        _save_cfg(gid, cfg)
        s = "✅ Enabled" if enabled else "❌ Disabled"
        await interaction.response.send_message(embed=ok(f"Caps Filter — {s}",
            f"Trigger: >{threshold}% caps in messages with ≥{min_length} letters"), ephemeral=True)

    # ── /automod mentions ─────────────────────────────────────────────────────
    @automod_group.command(name="mentions", description="Delete messages that mention too many users or roles")
    @app_commands.describe(enabled="Turn on or off", max_mentions="Max allowed mentions per message (default: 5)")
    async def am_mentions(self, interaction: discord.Interaction, enabled: bool, max_mentions: int = 5):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)
        cfg["mentions"].update({"enabled": enabled, "max": max_mentions})
        _save_cfg(gid, cfg)
        s = "✅ Enabled" if enabled else "❌ Disabled"
        await interaction.response.send_message(embed=ok(f"Mass Mention Filter — {s}",
            f"Deletes messages with more than **{max_mentions}** user/role mentions"), ephemeral=True)

    # ── /automod emojis ───────────────────────────────────────────────────────
    @automod_group.command(name="emojis", description="Delete messages packed with emojis")
    @app_commands.describe(enabled="Turn on or off", max_emojis="Max emojis per message (default: 15)")
    async def am_emojis(self, interaction: discord.Interaction, enabled: bool, max_emojis: int = 15):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)
        cfg["emojis"].update({"enabled": enabled, "max": max_emojis})
        _save_cfg(gid, cfg)
        s = "✅ Enabled" if enabled else "❌ Disabled"
        await interaction.response.send_message(embed=ok(f"Emoji Spam Filter — {s}",
            f"Deletes messages with more than **{max_emojis}** emojis"), ephemeral=True)

    # ── /automod repeated ─────────────────────────────────────────────────────
    @automod_group.command(name="repeated", description="Delete copy-paste spam (same message sent repeatedly)")
    @app_commands.describe(
        enabled="Turn on or off",
        count="How many repeats before deletion (default: 3)",
        seconds="Time window (default: 10)",
    )
    async def am_repeated(self, interaction: discord.Interaction,
                          enabled: bool, count: int = 3, seconds: int = 10):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)
        cfg["repeated"].update({"enabled": enabled, "count": count, "seconds": seconds})
        _save_cfg(gid, cfg)
        s = "✅ Enabled" if enabled else "❌ Disabled"
        await interaction.response.send_message(embed=ok(f"Repeated Message Filter — {s}",
            f"Trigger: same message **{count}×** in **{seconds}s**"), ephemeral=True)

    # ── /automod antiraid ─────────────────────────────────────────────────────
    @automod_group.command(name="antiraid", description="Lock the server when a wave of users joins at once")
    @app_commands.describe(
        enabled="Turn on or off",
        joins="Number of joins that triggers the lock (default: 10)",
        seconds="Time window in seconds (default: 30)",
    )
    async def am_antiraid(self, interaction: discord.Interaction,
                          enabled: bool, joins: int = 10, seconds: int = 30):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)
        cfg["raid"].update({"enabled": enabled, "joins": joins, "seconds": seconds})
        _save_cfg(gid, cfg)
        s = "✅ Enabled" if enabled else "❌ Disabled"
        await interaction.response.send_message(embed=ok(f"Anti-Raid — {s}",
            f"Trigger: **{joins}** joins in **{seconds}s** → all text channels locked"), ephemeral=True)

    # ── /automod links ────────────────────────────────────────────────────────
    @automod_group.command(name="links", description="Block all links, with an optional domain whitelist")
    @app_commands.describe(
        enabled="Turn on or off",
        whitelist="Comma-separated allowed domains, e.g. discord.gg,youtube.com (leave blank to block all)",
    )
    async def am_links(self, interaction: discord.Interaction, enabled: bool, whitelist: str = ""):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)
        domains = [d.strip().lower() for d in whitelist.split(",") if d.strip()]
        cfg["links"].update({"enabled": enabled, "whitelist": domains})
        _save_cfg(gid, cfg)
        s    = "✅ Enabled" if enabled else "❌ Disabled"
        wl   = ", ".join(f"`{d}`" for d in domains) or "*none — all links blocked*"
        await interaction.response.send_message(embed=ok(f"Link Filter — {s}",
            f"Allowed domains: {wl}"), ephemeral=True)

    # ── /automod scamimages ───────────────────────────────────────────────
    @automod_group.command(name="scamimages", description="Detect scam images using hashes and OCR")
    @app_commands.describe(
        enabled="Turn on or off",
        action="delete (remove message) or timeout (5-min timeout)",
        ocr="Use OCR.space text detection in addition to hash matching",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="delete", value="delete"),
        app_commands.Choice(name="timeout (5 min)", value="timeout"),
    ])
    async def am_scamimages(self, interaction: discord.Interaction, enabled: bool, action: str = "delete", ocr: bool = True):
        gid = str(interaction.guild.id)
        cfg = _load_cfg(gid)
        cfg["scam_images"].update({"enabled": enabled, "action": action, "ocr": ocr})
        cfg["scam_images"].setdefault("keywords", [])
        cfg["scam_images"].setdefault("hashes", [])
        _save_cfg(gid, cfg)
        s = "✅ Enabled" if enabled else "❌ Disabled"
        await interaction.response.send_message(
            embed=ok(
                f"Scam Image Filter — {s}",
                f"Action: **{action}** · OCR: **{'on' if ocr else 'off'}**",
            ),
            ephemeral=True,
        )

    # ── /automod blockimage ───────────────────────────────────────────────
    @automod_group.command(name="blockimage", description="Add an image hash to the scam image blocklist")
    @app_commands.describe(image="Upload or pick an image to block")
    async def am_blockimage(self, interaction: discord.Interaction, image: discord.Attachment):
        gid = str(interaction.guild.id)
        if not _image_attachment(image):
            return await interaction.response.send_message(embed=err("Please upload an image attachment."), ephemeral=True)
        try:
            data = await image.read()
            if not data:
                return await interaction.response.send_message(embed=err("Could not read the image attachment."), ephemeral=True)
            hashes = await _compute_image_hashes(data)
        except Exception:
            return await interaction.response.send_message(embed=err("Failed to process that image."), ephemeral=True)

        cfg = _load_cfg(gid)
        scam_cfg = cfg["scam_images"]
        scam_cfg.setdefault("hashes", [])
        scam_cfg.setdefault("keywords", [])
        new_entry = {"ahash": hashes["ahash"], "dhash": hashes["dhash"]}
        existing = {(item.get("ahash"), item.get("dhash")) for item in scam_cfg["hashes"]}
        if (new_entry["ahash"], new_entry["dhash"]) not in existing:
            scam_cfg["hashes"].append(new_entry)
            _save_cfg(gid, cfg)
            return await interaction.response.send_message(
                embed=ok("Scam Image Blocked", f"Stored hash for `{image.filename}`.\n\n`aHash`: `{hashes['ahash']}`\n`dHash`: `{hashes['dhash']}`"),
                ephemeral=True,
            )

        await interaction.response.send_message(
            embed=ok("Scam Image Blocked", f"That image hash is already in the blocklist.\n\n`aHash`: `{hashes['ahash']}`\n`dHash`: `{hashes['dhash']}`"),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
