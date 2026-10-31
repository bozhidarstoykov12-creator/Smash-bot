"""AI Games — 6 Gemini/OpenAI-powered word & thinking games.

Games: /wordchain /connections /taboo /riddle /twentyq /debate

Uses the guild's configured AI provider/key (same as cogs/ai_assistant.py).
Rewards go through game_reward() (honours the guild daily cap).
"""
import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils import load, save, ok, err, info, doc_lock, get_guild_cfg, game_reward, quest_progress
from i18n import t
from ai_guard import ask_ai, DEFAULT_MODELS, PROVIDER_KEY_FIELD


def _currency(gid):
    cfg = load('economy.json').get(str(gid), {})
    return cfg.get('currency_name', 'SK'), cfg.get('currency_emoji', '🪙')


def _enabled(gid, key):
    return bool(get_guild_cfg(gid, "games").get(key, True))


def _ai_cfg(gid):
    cfg = load("config.json").get(str(gid), {})
    provider = cfg.get("ai_provider", "gemini")
    key_field = PROVIDER_KEY_FIELD.get(provider, "gemini_api_key")
    return provider, cfg.get(key_field), cfg.get("ai_model") or DEFAULT_MODELS.get(provider)


async def _ai(gid, prompt, system=None):
    provider, key, model = _ai_cfg(gid)
    if not key:
        return None
    try:
        return await ask_ai(prompt, system=system, api_key=key, provider=provider, model=model,
                            max_output_tokens=512)
    except Exception:
        return None


async def _reward(interaction, gid, uid, amount, reason):
    granted = game_reward(gid, uid, amount, reason=reason)
    quest_progress(gid, uid, "game_played", 1)
    if granted > 0:
        currency, emoji = _currency(gid)
        try:
            await interaction.followup.send(embed=info(t(gid, 'game.reward', amount=granted, currency=currency, emoji=emoji)))
        except discord.HTTPException:
            pass


class AnswerModal(discord.ui.Modal):
    def __init__(self, title, label, on_submit):
        super().__init__(title=title[:45])
        self.on_submit = on_submit
        self.answer = discord.ui.TextInput(label=label[:45], style=discord.TextStyle.short, max_length=200)
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        await self.on_submit(interaction, self.answer.value)


# ══════════════════════════════════════════════════════════════════════
#  WORD CHAIN
# ══════════════════════════════════════════════════════════════════════

class WordChainView(discord.ui.View):
    def __init__(self, gid, uid, start_word):
        super().__init__(timeout=180)
        self.gid, self.uid = gid, uid
        self.last_word = start_word
        self.used = {start_word.lower()}
        self.score = 0
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'ai.wordchain_title'),
                           description=t(self.gid, 'ai.wordchain_start', word=self.last_word,
                                          letter=self.last_word[-1].upper()),
                           color=discord.Color.teal())
        em.add_field(name="Score", value=str(self.score), inline=True)
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    @discord.ui.button(label="Submit a word", style=discord.ButtonStyle.green)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return

        async def on_submit(inter, value):
            word = value.strip().lower()
            if not word.isalpha() or len(word) < 2:
                return await inter.response.send_message(embed=err(t(self.gid, 'ai.wordchain_bad')), ephemeral=True)
            if word[0] != self.last_word[-1] or word in self.used:
                return await inter.response.send_message(embed=err(t(self.gid, 'ai.wordchain_bad')), ephemeral=True)
            # Validate the word exists via AI.
            verdict = await _ai(self.gid, f"Is '{word}' a real English word? Reply only YES or NO.")
            if verdict and "no" in verdict.strip().lower()[:4]:
                return await inter.response.send_message(embed=err(t(self.gid, 'ai.wordchain_bad')), ephemeral=True)
            self.used.add(word)
            self.last_word = word
            self.score += 1
            await inter.response.defer()
            await interaction.edit_original_response(embed=self._embed(), view=self)

        await interaction.response.send_modal(AnswerModal("Word Chain", "Your word", on_submit))

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.gray)
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        self.finished = True
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(embed=self._embed("Chain ended!"), view=self)
        self.stop()
        await _reward(interaction, self.gid, self.uid, self.score * 15, "wordchain")


# ══════════════════════════════════════════════════════════════════════
#  CONNECTIONS
# ══════════════════════════════════════════════════════════════════════

class ConnectionsView(discord.ui.View):
    def __init__(self, gid, uid, words, answer):
        super().__init__(timeout=180)
        self.gid, self.uid = gid, uid
        self.words = words
        self.answer = answer
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'ai.connections_title'),
                           description="What connects these words?\n\n" + " • ".join(f"**{w}**" for w in self.words),
                           color=discord.Color.purple())
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    @discord.ui.button(label="Guess the connection", style=discord.ButtonStyle.green)
    async def guess(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return

        async def on_submit(inter, value):
            verdict = await _ai(
                self.gid,
                f"The words are: {', '.join(self.words)}. The intended connection is: '{self.answer}'. "
                f"The player guessed: '{value}'. Is the player's guess correct or close enough? Reply only YES or NO.")
            correct = bool(verdict and "yes" in verdict.strip().lower()[:4])
            self.finished = True
            for c in self.children:
                c.disabled = True
            note = f"✅ Correct! The connection was: **{self.answer}**" if correct else \
                   f"❌ Not quite. The connection was: **{self.answer}**"
            await inter.response.defer()
            await interaction.edit_original_response(embed=self._embed(note), view=self)
            self.stop()
            if correct:
                await _reward(interaction, self.gid, self.uid, 80, "connections")

        await interaction.response.send_modal(AnswerModal("Connections", "Your guess", on_submit))


# ══════════════════════════════════════════════════════════════════════
#  TABOO
# ══════════════════════════════════════════════════════════════════════

class TabooView(discord.ui.View):
    def __init__(self, gid, uid, word, forbidden):
        super().__init__(timeout=180)
        self.gid, self.uid = gid, uid
        self.word = word
        self.forbidden = forbidden
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'ai.taboo_title'),
                           description=f"Describe **{self.word}** without using:\n"
                                       + ", ".join(f"`{w}`" for w in self.forbidden),
                           color=discord.Color.dark_orange())
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    @discord.ui.button(label="Submit description", style=discord.ButtonStyle.green)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return

        async def on_submit(inter, value):
            low = value.lower()
            if any(f.lower() in low for f in self.forbidden) or self.word.lower() in low:
                return await inter.response.send_message(embed=err("❌ You used a forbidden word!"), ephemeral=True)
            verdict = await _ai(
                self.gid,
                f"Target word: '{self.word}'. Forbidden words: {self.forbidden}. "
                f"Player's description: '{value}'. Does the description clearly convey the target word "
                f"without using any forbidden word? Reply only YES or NO.")
            good = bool(verdict and "yes" in verdict.strip().lower()[:4])
            self.finished = True
            for c in self.children:
                c.disabled = True
            note = "✅ Great description!" if good else "❌ The description wasn't clear enough."
            await inter.response.defer()
            await interaction.edit_original_response(embed=self._embed(note), view=self)
            self.stop()
            if good:
                await _reward(interaction, self.gid, self.uid, 60, "taboo")

        await interaction.response.send_modal(AnswerModal("Taboo", "Your description", on_submit))


# ══════════════════════════════════════════════════════════════════════
#  RIDDLE
# ══════════════════════════════════════════════════════════════════════

class RiddleView(discord.ui.View):
    def __init__(self, gid, uid, riddle, answer):
        super().__init__(timeout=180)
        self.gid, self.uid = gid, uid
        self.riddle = riddle
        self.answer = answer
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'ai.riddle_title'), description=self.riddle,
                           color=discord.Color.dark_purple())
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    @discord.ui.button(label="Answer", style=discord.ButtonStyle.green)
    async def answer_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return

        async def on_submit(inter, value):
            verdict = await _ai(
                self.gid,
                f"Riddle: '{self.riddle}'. Correct answer: '{self.answer}'. "
                f"Player answered: '{value}'. Is the player correct or close enough? Reply only YES or NO.")
            correct = bool(verdict and "yes" in verdict.strip().lower()[:4])
            self.finished = True
            for c in self.children:
                c.disabled = True
            note = f"✅ Correct! Answer: **{self.answer}**" if correct else f"❌ The answer was **{self.answer}**"
            await inter.response.defer()
            await interaction.edit_original_response(embed=self._embed(note), view=self)
            self.stop()
            if correct:
                await _reward(interaction, self.gid, self.uid, 70, "riddle")

        await interaction.response.send_modal(AnswerModal("Riddle", "Your answer", on_submit))


# ══════════════════════════════════════════════════════════════════════
#  20 QUESTIONS
# ══════════════════════════════════════════════════════════════════════

class TwentyQView(discord.ui.View):
    def __init__(self, gid, uid, secret):
        super().__init__(timeout=300)
        self.gid, self.uid = gid, uid
        self.secret = secret
        self.questions = 0
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'ai.twentyq_title'),
                           description=f"I'm thinking of something. Ask yes/no questions!\n"
                                       f"Questions used: **{self.questions}/20**",
                           color=discord.Color.blue())
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    @discord.ui.button(label="Ask a question", style=discord.ButtonStyle.blurple)
    async def ask(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return

        async def on_submit(inter, value):
            self.questions += 1
            reply = await _ai(
                self.gid,
                f"You are thinking of: '{self.secret}'. The player asks: '{value}'. "
                f"Answer only YES or NO (or 'You got it!' if they guessed correctly).")
            reply = (reply or "YES").strip()
            if "got it" in reply.lower() or self.secret.lower() in value.lower():
                self.finished = True
                for c in self.children:
                    c.disabled = True
                await inter.response.defer()
                await interaction.edit_original_response(
                    embed=self._embed(f"🎉 You got it! It was **{self.secret}**."), view=self)
                self.stop()
                await _reward(interaction, self.gid, self.uid, max(20, (20 - self.questions) * 10), "twentyq")
                return
            if self.questions >= 20:
                self.finished = True
                for c in self.children:
                    c.disabled = True
                await inter.response.defer()
                await interaction.edit_original_response(
                    embed=self._embed(f"⏰ Out of questions! It was **{self.secret}**."), view=self)
                self.stop()
                return
            await inter.response.defer()
            await interaction.edit_original_response(embed=self._embed(f"**Q:** {value}\n**A:** {reply}"), view=self)

        await interaction.response.send_modal(AnswerModal("20 Questions", "Your question", on_submit))


# ══════════════════════════════════════════════════════════════════════
#  DEBATE
# ══════════════════════════════════════════════════════════════════════

class DebateView(discord.ui.View):
    def __init__(self, gid, uid, topic, side):
        super().__init__(timeout=300)
        self.gid, self.uid = gid, uid
        self.topic = topic
        self.side = side
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'ai.debate_title'),
                           description=f"**Topic:** {self.topic}\n**Your side:** {self.side}\n\n"
                                       f"Make your argument — the AI will judge it.",
                           color=discord.Color.dark_teal())
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    @discord.ui.button(label="Make your argument", style=discord.ButtonStyle.green)
    async def argue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return

        async def on_submit(inter, value):
            verdict = await _ai(
                self.gid,
                f"Debate topic: '{self.topic}'. The player argues the '{self.side}' side: '{value}'. "
                f"Score their argument from 0 to 10 and give one sentence of feedback. "
                f"Format: 'SCORE: X — feedback'.")
            verdict = (verdict or "SCORE: 5 — No verdict available.").strip()
            score = 5
            for token in verdict.replace("—", " ").replace("-", " ").split():
                if token.isdigit():
                    score = max(0, min(10, int(token)))
                    break
            self.finished = True
            for c in self.children:
                c.disabled = True
            await inter.response.defer()
            await interaction.edit_original_response(embed=self._embed(f"🧑‍⚖️ {verdict}"), view=self)
            self.stop()
            await _reward(interaction, self.gid, self.uid, score * 10, "debate")

        await interaction.response.send_modal(AnswerModal("Debate", "Your argument", on_submit))


# ══════════════════════════════════════════════════════════════════════
#  COG
# ══════════════════════════════════════════════════════════════════════

class AIGames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _require_ai(self, interaction, gid):
        provider, key, model = _ai_cfg(gid)
        if not key:
            await interaction.response.send_message(
                embed=err("❌ This server has not configured an AI API key. "
                          "An admin can add one in the Server Dashboard → Smash Bot AI."), ephemeral=True)
            return False
        return True

    @app_commands.command(name="wordchain", description="Build a chain of words, each starting with the last letter")
    async def wordchain(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "wordchain_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        if not await self._require_ai(interaction, gid):
            return
        await interaction.response.defer()
        start = await _ai(gid, "Give me one common English noun to start a word chain. Reply with only the word.")
        start = (start or "apple").strip().split()[0].strip(".,!?").lower()
        view = WordChainView(gid, str(interaction.user.id), start)
        await interaction.followup.send(embed=view._embed(), view=view)

    @app_commands.command(name="connections", description="Guess what connects a set of words")
    async def connections(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "connections_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        if not await self._require_ai(interaction, gid):
            return
        await interaction.response.defer()
        raw = await _ai(gid, "Create a 'Connections' puzzle: give 4 words that share a common connection, "
                             "then the connection itself. Format exactly: 'WORDS: a, b, c, d | ANSWER: the connection'.")
        words, answer = ["apple", "banana", "cherry", "date"], "fruits"
        if raw and "WORDS:" in raw and "ANSWER:" in raw:
            try:
                wpart = raw.split("WORDS:")[1].split("|")[0]
                words = [w.strip() for w in wpart.split(",")][:4]
                answer = raw.split("ANSWER:")[1].strip()
            except Exception:
                pass
        view = ConnectionsView(gid, str(interaction.user.id), words, answer)
        await interaction.followup.send(embed=view._embed(), view=view)

    @app_commands.command(name="taboo", description="Describe a word without using the forbidden words")
    async def taboo(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "taboo_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        if not await self._require_ai(interaction, gid):
            return
        await interaction.response.defer()
        raw = await _ai(gid, "Give a Taboo game card: one target word and 3 forbidden words. "
                             "Format exactly: 'WORD: x | FORBIDDEN: a, b, c'.")
        word, forbidden = "ocean", ["water", "blue", "fish"]
        if raw and "WORD:" in raw and "FORBIDDEN:" in raw:
            try:
                word = raw.split("WORD:")[1].split("|")[0].strip()
                forbidden = [w.strip() for w in raw.split("FORBIDDEN:")[1].split(",")][:3]
            except Exception:
                pass
        view = TabooView(gid, str(interaction.user.id), word, forbidden)
        await interaction.followup.send(embed=view._embed(), view=view)

    @app_commands.command(name="riddle", description="Solve an AI-generated riddle")
    async def riddle(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "riddle_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        if not await self._require_ai(interaction, gid):
            return
        await interaction.response.defer()
        raw = await _ai(gid, "Give a short riddle and its answer. Format exactly: 'RIDDLE: ... | ANSWER: ...'.")
        riddle, answer = "What has keys but no locks?", "a piano"
        if raw and "RIDDLE:" in raw and "ANSWER:" in raw:
            try:
                riddle = raw.split("RIDDLE:")[1].split("|")[0].strip()
                answer = raw.split("ANSWER:")[1].strip()
            except Exception:
                pass
        view = RiddleView(gid, str(interaction.user.id), riddle, answer)
        await interaction.followup.send(embed=view._embed(), view=view)

    @app_commands.command(name="twentyq", description="Play 20 Questions against the AI")
    async def twentyq(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "twentyq_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        if not await self._require_ai(interaction, gid):
            return
        await interaction.response.defer()
        secret = await _ai(gid, "Think of one common object, animal, or place for a game of 20 Questions. "
                                "Reply with only the secret answer.")
        secret = (secret or "a bicycle").strip().split("\n")[0]
        view = TwentyQView(gid, str(interaction.user.id), secret)
        await interaction.followup.send(embed=view._embed(), view=view)

    @app_commands.command(name="debate", description="Argue a topic and get scored by the AI")
    @app_commands.describe(side="Which side to argue (e.g. 'for' or 'against')")
    async def debate(self, interaction: discord.Interaction, side: str = "for"):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "debate_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        if not await self._require_ai(interaction, gid):
            return
        await interaction.response.defer()
        topic = await _ai(gid, "Give one interesting, safe debate topic as a single sentence. Reply with only the topic.")
        topic = (topic or "Should social media be regulated?").strip().split("\n")[0]
        view = DebateView(gid, str(interaction.user.id), topic, side)
        await interaction.followup.send(embed=view._embed(), view=view)


async def setup(bot):
    await bot.add_cog(AIGames(bot))
