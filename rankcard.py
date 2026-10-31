import discord
from discord.ext import commands
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import asyncio
import io
import os
from utils import load, err, xp_for_level, total_xp_for_level, get_level, xp_progress, is_module_enabled

_BOT_DIR  = os.path.dirname(os.path.abspath(__file__ + "/.."))
FONT_BOLD = os.path.join(_BOT_DIR, "arialbd.ttf")
FONT_REG  = os.path.join(_BOT_DIR, "arial.ttf")



def hex_to_rgb(hex_str: str, fallback=(88, 101, 242)) -> tuple:
    try:
        h = hex_str.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback

# Keys under profile.colors.* -> (legacy flat key, default hex, default rgb)
_RC_COLOR_MAP = {
    'bg':     ('bg_color',     '#0d0e12', (13,  14,  18)),
    'panel':  ('panel_color',  '#161922', (22,  25,  34)),
    'accent': ('accent_color', '#6366f1', (99,  102, 241)),
    'text':   ('text_color',   '#f4f5f7', (244, 245, 247)),
    'bar_bg': ('bar_bg_color', '#1b1f2a', (27,  31,  42)),
}


def get_user_rc_cfg(uid: str) -> dict | None:
    """Personal rank card colors set on the dashboard (/me). None = use server theme.

    Reads the v1 ``user_profiles.json`` schema written by the dashboard::

        <user_id>: {profile: {rankcard_enabled: bool, colors: {...}}, meta: {...}}

    The pre-v1 flat shape (``{rankcard_enabled: ..., bg_color: ...}``) is still
    accepted so existing files keep working without a migration step.
    """
    try:
        entry = load('user_profiles.json').get(str(uid), {})
    except Exception:
        return None
    if not isinstance(entry, dict):
        return None

    nested = entry.get('profile')
    prof = nested if isinstance(nested, dict) else entry
    if not prof.get('rankcard_enabled'):
        return None

    colors = prof.get('colors')
    if not isinstance(colors, dict):
        colors = {}

    out = {}
    for key, (legacy_key, default_hex, default_rgb) in _RC_COLOR_MAP.items():
        raw = colors.get(key)
        if raw is None:
            raw = prof.get(legacy_key, default_hex)
        out[key] = hex_to_rgb(raw, default_rgb)
    return out

def get_guild_rc_cfg(gid: str) -> dict:
    cfg = load('config.json').get(gid, {}).get('rankcard', {})
    return {
        'bg':     hex_to_rgb(cfg.get('bg_color',     '#1e1f22'), (30,  31,  34)),
        'panel':  hex_to_rgb(cfg.get('panel_color',  '#2b2d31'), (43,  45,  49)),
        'accent': hex_to_rgb(cfg.get('accent_color', '#5865f2'), (88,  101, 242)),
        'text':   hex_to_rgb(cfg.get('text_color',   '#f2f3f5'), (242, 243, 245)),
        'bar_bg': hex_to_rgb(cfg.get('bar_bg_color', '#313338'), (49,  51,  56)),
    }

# ── Drawing helpers (all sync) ─────────────────────────────
def circle_crop(img: Image.Image, size: int) -> Image.Image:
    img  = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out

def gradient_bar(width: int, height: int, color: tuple) -> Image.Image:
    bar = Image.new("RGBA", (width, height))
    r, g, b = color
    for x in range(width):
        t  = x / max(width - 1, 1)
        px = (int(r * (0.55 + 0.45 * t)),
              int(g * (0.55 + 0.45 * t)),
              int(b * (0.55 + 0.45 * t)), 255)
        for y in range(height):
            bar.putpixel((x, y), px)
    return bar

# ── Card builder (sync — safe for run_in_executor) ─────────
def build_rank_card_sync(
    av_bytes: bytes,
    username: str,
    rank,
    xp: int,
    level: int,
    xp_cur: int,
    xp_needed: int,
    colors: dict,
) -> io.BytesIO:
    W, H    = 940, 270
    PAD     = 26
    AV_SIZE = 186

    bg     = colors['bg']     + (255,)
    panel  = colors['panel']  + (255,)
    accent = colors['accent']
    text   = colors['text']
    bar_bg = colors['bar_bg'] + (255,)
    sub    = (181, 186, 193)

    card = Image.new("RGBA", (W, H), bg)
    draw = ImageDraw.Draw(card)

    # Inner panel
    draw.rounded_rectangle([PAD, PAD, W - PAD, H - PAD], radius=18, fill=panel)

    # Accent stripe
    draw.rounded_rectangle([PAD, PAD, PAD + 7, H - PAD], radius=4, fill=accent + (255,))

    # ── Avatar ────────────────────────────────────────────
    try:
        av_img = Image.open(io.BytesIO(av_bytes))
    except Exception:
        av_img = Image.new("RGB", (256, 256), (78, 80, 88))

    av_circle = circle_crop(av_img, AV_SIZE)
    ring_sz   = AV_SIZE + 10
    ring      = Image.new("RGBA", (ring_sz, ring_sz), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, ring_sz, ring_sz), fill=accent + (255,))
    av_x = PAD + 24
    av_y = (H - ring_sz) // 2
    card.paste(ring, (av_x, av_y), ring)
    card.paste(av_circle, (av_x + 5, av_y + 5), av_circle)

    # ── Fonts ─────────────────────────────────────────────
    try:
        f_name  = ImageFont.truetype(FONT_BOLD, 34)
        f_sub   = ImageFont.truetype(FONT_REG,  16)
        f_xp    = ImageFont.truetype(FONT_REG,  14)
        f_lvl   = ImageFont.truetype(FONT_BOLD, 46)
        f_lvllb = ImageFont.truetype(FONT_REG,  13)
    except Exception:
        f_name = f_sub = f_xp = f_lvl = f_lvllb = ImageFont.load_default()

    txt_x = av_x + AV_SIZE + 32

    # ── Username ──────────────────────────────────────────
    display = username if len(username) <= 22 else username[:21] + "…"
    draw.text((txt_x, 44), display, font=f_name, fill=text)

    # ── Rank ─────────────────────────────────────────────
    rank_str = f"#{rank}" if rank else "N/A"
    draw.text((txt_x, 90), "SERVER RANK", font=f_xp, fill=sub)
    draw.text((txt_x + 126, 87), rank_str, font=f_sub, fill=accent)

    # ── Progress bar ──────────────────────────────────────
    bar_x = txt_x
    bar_y = 122
    bar_w = W - txt_x - PAD - 16
    bar_h = 28
    bar_r = 14

    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=bar_r, fill=bar_bg)

    progress = xp_cur / max(xp_needed, 1)
    fill_w   = max(bar_r * 2, int(bar_w * progress))
    grad     = gradient_bar(fill_w, bar_h, accent)
    mask     = Image.new("L", (fill_w, bar_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, fill_w, bar_h), radius=bar_r, fill=255)
    card.paste(grad, (bar_x, bar_y), mask)

    # ── XP text ───────────────────────────────────────────
    xp_left  = f"{xp_cur:,} / {xp_needed:,} XP"
    xp_right = f"Total: {xp:,} XP"
    draw.text((bar_x, bar_y + bar_h + 8), xp_left,  font=f_xp, fill=sub)
    rb = draw.textbbox((0, 0), xp_right, font=f_xp)
    draw.text((bar_x + bar_w - (rb[2] - rb[0]), bar_y + bar_h + 8), xp_right, font=f_xp, fill=sub)

    # ── Level badge ───────────────────────────────────────
    lvl_str  = str(level)
    lb_bbox  = draw.textbbox((0, 0), lvl_str, font=f_lvl)
    lbl_bbox = draw.textbbox((0, 0), "LEVEL",  font=f_lvllb)
    lw = max(lb_bbox[2] - lb_bbox[0], lbl_bbox[2] - lbl_bbox[0])
    bx = W - PAD - 18 - lw
    draw.text((bx + (lw - (lbl_bbox[2] - lbl_bbox[0])) // 2, 46), "LEVEL", font=f_lvllb, fill=sub)
    draw.text((bx + (lw - (lb_bbox[2]  - lb_bbox[0]))  // 2, 62), lvl_str,  font=f_lvl,   fill=accent)

    buf = io.BytesIO()
    card.convert("RGB").save(buf, "PNG", optimize=True)
    buf.seek(0)
    return buf

async def generate_rank_card_file(member) -> discord.File | None:
    try:
        gid = str(member.guild.id)
        uid = str(member.id)
        lvl_data = load('levels.json').get(gid, {})
        user_data = lvl_data.get(uid, {})
        xp = user_data.get('xp', 0) if isinstance(user_data, dict) else int(user_data)
        level, xp_cur, xp_needed = xp_progress(xp)
        colors = get_user_rc_cfg(uid) or get_guild_rc_cfg(gid)
        sorted_users = sorted(
            lvl_data.items(),
            key=lambda x: x[1].get('xp', 0) if isinstance(x[1], dict) else x[1],
            reverse=True
        )
        rank = next((i + 1 for i, (u, _) in enumerate(sorted_users) if u == uid), None)

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
        card_buf = await loop.run_in_executor(
            None,
            build_rank_card_sync,
            av_bytes, member.display_name, rank,
            xp, level, xp_cur, xp_needed, colors
        )
        return discord.File(card_buf, filename="rankcard.png")
    except Exception:
        return None


# ── Cog ───────────────────────────────────────────────────
class RankCard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="rankcard", description="Show your (or someone else's) rank card image")
    @app_commands.describe(member="Member to show (leave empty for yourself)")
    async def rankcard(self, interaction: discord.Interaction,
                       member: discord.Member = None):
        await interaction.response.defer()

        target = member or interaction.user
        gid    = str(interaction.guild.id)
        if not is_module_enabled(gid, 'rankcard'):
            return await interaction.followup.send(embed=err("Rank Card module is disabled on this server."), ephemeral=True)

        file = await generate_rank_card_file(target)
        if not file:
            return await interaction.followup.send(embed=err("Could not generate rank card right now."), ephemeral=True)

        accent_rgb = get_guild_rc_cfg(gid)['accent']
        em   = discord.Embed(color=discord.Color.from_rgb(*accent_rgb))
        em.set_image(url="attachment://rankcard.png")
        suffix = "your" if target == interaction.user else f"{target.display_name}'s"
        em.set_footer(text=f"Showing {suffix} rank in {interaction.guild.name}")

        await interaction.followup.send(embed=em, file=file)


async def setup(bot):
    await bot.add_cog(RankCard(bot))
