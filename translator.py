"""
Translator Cog for SmashBot
===========================
Provides text translation via:
1. /translate <text> <target_language>
2. Right-click on any message -> Apps -> 'Translate Message'
3. Reacting to any message with a flag emoji (🇬🇧, 🇧🇬, 🇩🇪, 🇪🇸, 🇫🇷, 🇷🇺, etc.)
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import urllib.parse
from i18n import LANGUAGES, FLAG_TO_LANG, DEFAULT_LANG, get_language
from utils import safe_interaction_send

# Supported target languages for slash command choices
TRANSLATE_TARGETS = [
    app_commands.Choice(name="🇬🇧 English", value="en"),
    app_commands.Choice(name="🇧🇬 Български (Bulgarian)", value="bg"),
    app_commands.Choice(name="🇩🇪 Deutsch (German)", value="de"),
    app_commands.Choice(name="🇪🇸 Español (Spanish)", value="es"),
    app_commands.Choice(name="🇫🇷 Français (French)", value="fr"),
    app_commands.Choice(name="🇷🇺 Русский (Russian)", value="ru"),
    app_commands.Choice(name="🇮🇹 Italiano (Italian)", value="it"),
    app_commands.Choice(name="🇹🇷 Türkçe (Turkish)", value="tr"),
    app_commands.Choice(name="🇵🇱 Polski (Polish)", value="pl"),
    app_commands.Choice(name="🇺🇦 Українська (Ukrainian)", value="uk"),
    app_commands.Choice(name="🇬🇷 Ελληνικά (Greek)", value="el"),
    app_commands.Choice(name="🇷🇴 Română (Romanian)", value="ro"),
]


async def fetch_translation(text: str, target_lang: str) -> tuple[str, str]:
    """
    Translates text to target_lang using a fast, free Google Translate endpoint.
    Returns (translated_text, detected_source_lang).
    """
    if not text or not text.strip():
        return "", "unknown"

    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=auto&tl={urllib.parse.quote(target_lang)}&dt=t&q={urllib.parse.quote(text)}"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as response:
                if response.status == 200:
                    data = await response.json()
                    translated_parts = []
                    if data and isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                        for item in data[0]:
                            if item and len(item) > 0 and item[0]:
                                translated_parts.append(item[0])
                        detected_src = data[2] if len(data) > 2 and isinstance(data[2], str) else "auto"
                        return "".join(translated_parts), detected_src
    except Exception as e:
        print(f"[Translator] Error fetching translation: {e}")

    return text, "unknown"


class Translator(commands.Cog):
    """🌐 Multi-language translation & Flag reaction translating."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register context menu message command
        self.ctx_menu = app_commands.ContextMenu(
            name="Translate Message",
            callback=self.context_translate,
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    # ── Slash command: /translate ─────────────────────────────────────────────

    @app_commands.command(name="translate", description="🌐 Translate text into another language")
    @app_commands.describe(
        text="The text you want to translate",
        language="Target language to translate into"
    )
    @app_commands.choices(language=TRANSLATE_TARGETS)
    async def translate(self, interaction: discord.Interaction, text: str, language: str = "en"):
        await interaction.response.defer(ephemeral=False)

        translated, detected = await fetch_translation(text, language)
        target_name = LANGUAGES.get(language, {}).get("name", language.upper())
        target_flag = LANGUAGES.get(language, {}).get("flag", "🌐")

        embed = discord.Embed(
            color=discord.Color.from_rgb(66, 133, 244),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=f"Translation → {target_flag} {target_name}",
            icon_url="https://cdn-icons-png.flaticon.com/512/3898/3898082.png"
        )
        embed.add_field(name=f"Original ({detected.upper()})", value=text[:1024], inline=False)
        embed.add_field(name=f"Translated ({target_flag} {target_name})", value=translated[:1024], inline=False)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

        await interaction.followup.send(embed=embed)

    # ── Context menu: Right Click -> Apps -> Translate Message ────────────────

    async def context_translate(self, interaction: discord.Interaction, message: discord.Message):
        if not message.content:
            await safe_interaction_send(interaction, "❌ This message contains no text to translate.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild_lang = get_language(interaction.guild_id) if interaction.guild else DEFAULT_LANG
        translated, detected = await fetch_translation(message.content, guild_lang)
        target_name = LANGUAGES.get(guild_lang, {}).get("name", guild_lang.upper())
        target_flag = LANGUAGES.get(guild_lang, {}).get("flag", "🌐")

        embed = discord.Embed(
            color=discord.Color.from_rgb(66, 133, 244),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=f"Translation → {target_flag} {target_name}",
            icon_url="https://cdn-icons-png.flaticon.com/512/3898/3898082.png"
        )
        embed.add_field(name="Original", value=message.content[:1024], inline=False)
        embed.add_field(name=f"Translated ({target_flag})", value=translated[:1024], inline=False)
        embed.set_footer(text=f"Author: {message.author.display_name}", icon_url=message.author.display_avatar.url)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Event: Reaction with Flag Emoji ───────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Translates a message when someone reacts with a flag emoji."""
        if payload.user_id == self.bot.user.id:
            return

        emoji_str = str(payload.emoji.name)
        target_lang = FLAG_TO_LANG.get(emoji_str)
        if not target_lang:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        if not message.content or len(message.content.strip()) == 0:
            return

        translated, detected = await fetch_translation(message.content, target_lang)
        if not translated or translated.strip().lower() == message.content.strip().lower():
            return

        target_flag = emoji_str
        target_name = LANGUAGES.get(target_lang, {}).get("name", target_lang.upper())

        embed = discord.Embed(
            description=translated[:2048],
            color=discord.Color.from_rgb(66, 133, 244),
        )
        embed.set_author(
            name=f"{target_flag} Translation ({detected.upper()} → {target_name})",
            icon_url="https://cdn-icons-png.flaticon.com/512/3898/3898082.png"
        )
        embed.set_footer(text=f"Original by {message.author.display_name}")

        try:
            # Send as thread reply or channel reply referencing the message
            await message.reply(embed=embed, mention_author=False)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Translator(bot))
    print("✅ Translator cog loaded")
