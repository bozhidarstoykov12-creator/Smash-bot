import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import asyncio
import io
import os

from utils import load

_BOT_DIR  = os.path.dirname(os.path.abspath(__file__ + "/.."))
FONT_BOLD = os.path.join(_BOT_DIR, "arialbd.ttf")
FONT_REG  = os.path.join(_BOT_DIR, "arial.ttf")


def hex_to_rgb(hex_str: str, fallback=(99, 102, 241)) -> tuple:
    try:
        h = hex_str.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


# Dashboard-editable banner settings live under:
#   config.json -> <gid> -> welcomer -> banner -> { *_color, title, subtitle, ... }
# Mirrors the rank-card settings pattern (dedicated dashboard page + colors).
_BANNER_DEFAULTS = {
    'bg_color':     '#0d0e12',
    'accent_color': '#6366f1',
    'text_color':   '#ffffff',
    'sub_color':    '#b5bac1',
    'title':          'Welcome, {user.name}!',
    'subtitle':       'Member #{member_count} \u2022 {server.name}',
    'leave_title':    'Goodbye, {user.name}',
    'leave_subtitle': 'We now have {member_count} members',
}


def get_banner_cfg(gid: str) -> dict:
    b = load('config.json').get(str(gid), {}).get('welcomer', {}).get('banner', {})
    out = dict(_BANNER_DEFAULTS)
    out.update({k: v for k, v in b.items() if v not in (None, '')})
    return out


def _apply_placeholders(text: str, member, guild) -> str:
    return (text.replace('{user.name}', member.display_name)
                .replace('{user.mention}', member.display_name)
                .replace('{server.name}', guild.name)
                .replace('{member_count}', str(guild.member_count)))


def circle_crop(img: Image.Image, size: int) -> Image.Image:
    img  = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out


def _draw_centered(draw, text, font, cx, y, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((cx - w // 2, y), text, font=font, fill=fill)


def build_banner_sync(av_bytes: bytes, title: str, subtitle: str, colors: dict) -> io.BytesIO:
    """Sync image builder — safe to run in an executor."""
    W, H = 1000, 340
    CX = W // 2

    bg     = hex_to_rgb(colors['bg_color'],     (13, 14, 18))
    accent = hex_to_rgb(colors['accent_color'], (99, 102, 241))
    text   = hex_to_rgb(colors['text_color'],   (255, 255, 255))
    sub    = hex_to_rgb(colors['sub_color'],    (181, 186, 193))

    card = Image.new("RGBA", (W, H), bg + (255,))
    draw = ImageDraw.Draw(card)

    # Top + bottom accent bars for a finished "banner" look.
    draw.rectangle([0, 0, W, 6], fill=accent + (255,))
    draw.rectangle([0, H - 6, W, H], fill=accent + (255,))

    # Avatar (centered, with an accent ring).
    AV = 150
    try:
        av_img = Image.open(io.BytesIO(av_bytes))
    except Exception:
        av_img = Image.new("RGB", (256, 256), (78, 80, 88))
    av_circle = circle_crop(av_img, AV)
    ring_sz = AV + 12
    ring = Image.new("RGBA", (ring_sz, ring_sz), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, ring_sz, ring_sz), fill=accent + (255,))
    av_x = CX - ring_sz // 2
    av_y = 36
    card.paste(ring, (av_x, av_y), ring)
    card.paste(av_circle, (av_x + 6, av_y + 6), av_circle)

    # Fonts.
    try:
        f_title = ImageFont.truetype(FONT_BOLD, 44)
        f_sub   = ImageFont.truetype(FONT_REG,  26)
    except Exception:
        f_title = f_sub = ImageFont.load_default()

    # Clamp overly long text so it never overflows the canvas.
    title = title if len(title) <= 40 else title[:39] + "\u2026"
    subtitle = subtitle if len(subtitle) <= 54 else subtitle[:53] + "\u2026"

    _draw_centered(draw, title, f_title, CX, av_y + ring_sz + 24, text)
    _draw_centered(draw, subtitle, f_sub, CX, av_y + ring_sz + 80, sub)

    buf = io.BytesIO()
    card.convert("RGB").save(buf, "PNG", optimize=True)
    buf.seek(0)
    return buf


async def generate_banner_file(member, guild, kind: str = "welcome") -> discord.File | None:
    """Build a welcome/leave banner PNG as a discord.File, or None on failure.

    kind: 'welcome' or 'leave'.
    """
    try:
        colors = get_banner_cfg(str(guild.id))
        if kind == "leave":
            title = _apply_placeholders(colors['leave_title'], member, guild)
            subtitle = _apply_placeholders(colors['leave_subtitle'], member, guild)
            fname = "goodbye.png"
        else:
            title = _apply_placeholders(colors['title'], member, guild)
            subtitle = _apply_placeholders(colors['subtitle'], member, guild)
            fname = "welcome.png"

        av_url = member.display_avatar.replace(format="png", size=256).url
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(av_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    av_bytes = await r.read()
        except Exception:
            fallback = Image.new("RGB", (256, 256), (78, 80, 88))
            buf = io.BytesIO()
            fallback.save(buf, "PNG")
            av_bytes = buf.getvalue()

        loop = asyncio.get_running_loop()
        buf = await loop.run_in_executor(None, build_banner_sync, av_bytes, title, subtitle, colors)
        return discord.File(buf, filename=fname)
    except Exception as e:
        print(f"[WelcomeBanner] generate failed: {e}")
        return None


class WelcomeBanner(commands.Cog):
    """Image builder for welcome/leave banners. The WelcomerLeave cog calls
    generate_banner_file(); this cog mainly exists so the helper lives in a
    loadable module and can be reused from the dashboard test button."""

    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(WelcomeBanner(bot))
