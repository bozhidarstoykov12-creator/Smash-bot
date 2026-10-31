"""
Voice Assistant Cog for SmashBot
================================
Lets the bot hold a *spoken* conversation inside a Discord voice channel:

  1. `/voice join` -> bot joins the caller's voice channel and starts listening.
  2. It continuously transcribes what people say (Groq Whisper).
  3. When it hears the wake word (default "hey smash bot", configurable in the
     dashboard), it plays a short activation chime and "wakes up".
  4. While awake it treats following speech as a question -> Groq chat ->
     speaks the answer back with Groq PlayAI TTS, all in the voice channel.
  5. After a short idle window it goes back to sleep until the wake word.

Voice *receiving* is not part of discord.py core, so this cog relies on the
`discord-ext-voice-recv` extension. If that package (or ffmpeg/PyNaCl) is not
installed, the cog still loads but the commands explain what is missing instead
of crashing the whole bot.
"""

import asyncio
import os
import re
import tempfile
import threading
import time
import wave
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import load
from ai_guard import (
    ask_ai,
    transcribe_audio,
    synthesize_speech,
    DEFAULT_MODELS,
    DEFAULT_STT_MODEL,
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
)

# discord-ext-voice-recv is optional; degrade gracefully if it's absent.
try:
    from discord.ext import voice_recv  # type: ignore
    VOICE_RECV_OK = True
except Exception:  # ImportError or any native-load failure
    voice_recv = None  # type: ignore
    VOICE_RECV_OK = False

# ---- Audio / behaviour constants ------------------------------------------
DISCORD_SR = 48000          # Discord delivers 48kHz 16-bit stereo PCM
DISCORD_CHANNELS = 2
DISCORD_SAMPLE_WIDTH = 2
BYTES_PER_SEC = DISCORD_SR * DISCORD_CHANNELS * DISCORD_SAMPLE_WIDTH

SILENCE_FLUSH_SEC = 0.9     # end an utterance after this much silence
MIN_SPEECH_SEC = 0.35       # ignore clips shorter than this (coughs/clicks)
MAX_SPEECH_SEC = 20.0       # hard cap on a single utterance
AWAKE_WINDOW_SEC = 18.0     # stay awake this long after the wake word / a reply
DEFAULT_WAKE_WORD = "hey smash bot"

_ACTIVATION_SOUND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "voice_activate.wav",
)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for wake-word match."""
    text = (text or "").lower()
    text = re.sub(r"[^0-9a-z\u0400-\u04ff ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _pcm_to_wav_bytes(pcm: bytes) -> bytes:
    """Wrap raw 48kHz 16-bit stereo PCM in a WAV container (in memory)."""
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(DISCORD_CHANNELS)
        w.setsampwidth(DISCORD_SAMPLE_WIDTH)
        w.setframerate(DISCORD_SR)
        w.writeframes(pcm)
    return buf.getvalue()


if VOICE_RECV_OK:

    class _BufferingSink(voice_recv.AudioSink):
        """Buffers decoded PCM per speaker, thread-safely.

        `write()` is invoked from the voice-recv worker thread, so all shared
        state is guarded by a lock. The cog's async poll loop drains finished
        utterances (see VoiceAssistant._poll_loop).
        """

        def __init__(self):
            super().__init__()
            self._lock = threading.Lock()
            # user_id -> {"buf": bytearray, "last": monotonic, "start": monotonic}
            self._users: dict[int, dict] = {}

        def wants_opus(self) -> bool:
            return False  # we want decoded PCM, not raw opus

        def write(self, user, data):
            if user is None or not getattr(data, "pcm", None):
                return
            now = time.monotonic()
            with self._lock:
                slot = self._users.get(user.id)
                if slot is None:
                    slot = {"buf": bytearray(), "last": now, "start": now}
                    self._users[user.id] = slot
                # cap runaway buffers
                if len(slot["buf"]) < int(MAX_SPEECH_SEC * BYTES_PER_SEC):
                    slot["buf"].extend(data.pcm)
                slot["last"] = now

        def drain_ready(self):
            """Return [(user_id, pcm_bytes), ...] for utterances that ended."""
            ready = []
            now = time.monotonic()
            with self._lock:
                for uid, slot in list(self._users.items()):
                    buf = slot["buf"]
                    if not buf:
                        continue
                    silent_for = now - slot["last"]
                    dur = len(buf) / BYTES_PER_SEC
                    too_long = dur >= MAX_SPEECH_SEC
                    if (silent_for >= SILENCE_FLUSH_SEC and dur >= MIN_SPEECH_SEC) or too_long:
                        ready.append((uid, bytes(buf)))
                        self._users.pop(uid, None)
                    elif silent_for >= SILENCE_FLUSH_SEC:
                        # too short to be speech — discard quietly
                        self._users.pop(uid, None)
            return ready

        def cleanup(self):
            with self._lock:
                self._users.clear()


class _GuildVoice:
    """Per-guild runtime state for the voice assistant."""

    def __init__(self):
        self.sink = None
        self.poll_task: Optional[asyncio.Task] = None
        self.play_lock = asyncio.Lock()
        self.speaking = False
        # user_id -> monotonic time until which that user stays "awake"
        self.awake_until: dict[int, float] = {}
        self.processing: set = set()


class VoiceAssistant(commands.Cog):
    """Spoken, in-voice-channel AI assistant."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._guilds: dict[int, _GuildVoice] = {}

    # ── config helpers ────────────────────────────────────────────────
    def _cfg(self, gid: int) -> dict:
        return load("config.json").get(str(gid), {})

    def _wake_word(self, cfg: dict) -> str:
        return _normalize(cfg.get("voice_wake_word") or DEFAULT_WAKE_WORD) or DEFAULT_WAKE_WORD

    def _enabled(self, cfg: dict) -> bool:
        return bool(cfg.get("voice_assistant_enabled", True))

    def _groq_key(self, cfg: dict) -> str:
        return (cfg.get("groq_api_key") or os.getenv("GROQ_API_KEY") or "").strip()

    def _state(self, gid: int) -> _GuildVoice:
        st = self._guilds.get(gid)
        if st is None:
            st = _GuildVoice()
            self._guilds[gid] = st
        return st

    # ── slash commands ────────────────────────────────────────────────
    voice = app_commands.Group(
        name="voice",
        description="Talk to Smash Bot by voice in a voice channel",
    )

    @voice.command(name="join", description="Join your voice channel and start listening")
    async def voice_join(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not VOICE_RECV_OK:
            return await interaction.followup.send(
                "❌ Voice listening isn't available on this host. "
                "The `discord-ext-voice-recv` package (and ffmpeg + PyNaCl) must be installed.",
                ephemeral=True,
            )
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            return await interaction.followup.send(
                "❌ Join a voice channel first, then run `/voice join`.", ephemeral=True)

        cfg = self._cfg(interaction.guild_id)
        if not self._enabled(cfg):
            return await interaction.followup.send(
                "❌ The voice assistant is disabled for this server. "
                "An admin can enable it in **Server Dashboard → Smash Bot AI → Voice Assistant**.",
                ephemeral=True,
            )
        if not self._groq_key(cfg):
            return await interaction.followup.send(
                "❌ Voice needs a Groq API key (used for speech-to-text and the voice reply). "
                "Add one in **Server Dashboard → Smash Bot AI → 🔑 Groq API Key**.",
                ephemeral=True,
            )

        channel = member.voice.channel
        vc = interaction.guild.voice_client
        try:
            if vc and vc.is_connected():
                if not isinstance(vc, voice_recv.VoiceRecvClient):
                    return await interaction.followup.send(
                        "❌ I'm already connected here for music/other audio. "
                        "Disconnect me from that first, then run `/voice join`.",
                        ephemeral=True,
                    )
                await vc.move_to(channel)
            else:
                vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Couldn't join the voice channel: `{type(e).__name__}`.", ephemeral=True)

        st = self._state(interaction.guild_id)
        st.sink = _BufferingSink()
        try:
            vc.listen(st.sink)
        except Exception as e:
            await vc.disconnect(force=True)
            return await interaction.followup.send(
                f"❌ Couldn't start listening: `{type(e).__name__}`.", ephemeral=True)

        if st.poll_task is None or st.poll_task.done():
            st.poll_task = asyncio.create_task(self._poll_loop(interaction.guild_id))

        wake = self._cfg(interaction.guild_id).get("voice_wake_word") or DEFAULT_WAKE_WORD
        await interaction.followup.send(
            f"🎙️ Listening in **{channel.name}**. Say **“{wake}”** to wake me, "
            "then just talk — I'll reply out loud. Use `/voice leave` when you're done.",
            ephemeral=True,
        )

    @voice.command(name="leave", description="Stop listening and leave the voice channel")
    async def voice_leave(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._teardown(interaction.guild_id)
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            await vc.disconnect(force=True)
            return await interaction.followup.send("👋 Left the voice channel.", ephemeral=True)
        await interaction.followup.send("I'm not in a voice channel.", ephemeral=True)

    @voice.command(name="status", description="Show the voice assistant status")
    async def voice_status(self, interaction: discord.Interaction):
        cfg = self._cfg(interaction.guild_id)
        vc = interaction.guild.voice_client
        connected = isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_connected() if VOICE_RECV_OK else False
        em = discord.Embed(title="🎙️ Voice Assistant", color=discord.Color.blurple())
        em.add_field(name="Enabled", value="✅ Yes" if self._enabled(cfg) else "❌ No", inline=True)
        em.add_field(name="Wake word", value=f"`{cfg.get('voice_wake_word') or DEFAULT_WAKE_WORD}`", inline=True)
        em.add_field(name="Connected", value=(f"✅ {vc.channel.name}" if connected else "❌ No"), inline=True)
        em.add_field(name="Groq key", value="✅ Set" if self._groq_key(cfg) else "❌ Missing", inline=True)
        em.add_field(name="Voice-receive lib", value="✅ Installed" if VOICE_RECV_OK else "❌ Not installed", inline=True)
        await interaction.response.send_message(embed=em, ephemeral=True)

    # ── core loop ─────────────────────────────────────────────────────
    async def _poll_loop(self, gid: int):
        """Drain finished utterances from the sink and process them."""
        st = self._state(gid)
        try:
            while True:
                await asyncio.sleep(0.3)
                guild = self.bot.get_guild(gid)
                vc = guild.voice_client if guild else None
                if not vc or not vc.is_connected() or st.sink is None:
                    break
                # Don't ingest our own speech (avoids the bot hearing itself).
                if st.speaking:
                    st.sink.drain_ready()  # discard whatever came in while talking
                    continue
                for uid, pcm in st.sink.drain_ready():
                    if uid in st.processing:
                        continue
                    st.processing.add(uid)
                    asyncio.create_task(self._process_utterance(gid, uid, pcm))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Voice] poll loop crashed for guild {gid}: {type(e).__name__}: {e}")

    async def _process_utterance(self, gid: int, uid: int, pcm: bytes):
        st = self._state(gid)
        try:
            cfg = self._cfg(gid)
            if not self._enabled(cfg):
                return
            key = self._groq_key(cfg)
            if not key:
                return

            wav = _pcm_to_wav_bytes(pcm)
            try:
                transcript = await transcribe_audio(wav, api_key=key, model=DEFAULT_STT_MODEL)
            except Exception as e:
                print(f"[Voice] STT failed: {type(e).__name__}: {e}")
                return
            norm = _normalize(transcript)
            if not norm:
                return

            wake = self._wake_word(cfg)
            now = time.monotonic()
            awake = st.awake_until.get(uid, 0) > now

            prompt = None
            if wake and wake in norm:
                # Wake word heard: chime, wake up, use anything said after it as
                # the question (normalized STT text is fine as an LLM prompt).
                after = norm.split(wake, 1)[1].strip()
                if not awake:
                    await self._play_activation(gid)
                st.awake_until[uid] = time.monotonic() + AWAKE_WINDOW_SEC
                prompt = after or None
            elif awake:
                prompt = transcript

            if not prompt:
                return

            st.awake_until[uid] = time.monotonic() + AWAKE_WINDOW_SEC
            await self._answer(gid, uid, prompt, cfg, key)
        finally:
            st.processing.discard(uid)

    async def _answer(self, gid: int, uid: int, prompt: str, cfg: dict, groq_key: str):
        provider = (cfg.get("ai_provider") or "groq").lower()
        model = cfg.get("ai_model") or DEFAULT_MODELS.get(provider)
        from ai_guard import PROVIDER_KEY_FIELD
        chat_key = cfg.get(PROVIDER_KEY_FIELD.get(provider, "groq_api_key")) or groq_key

        member = None
        guild = self.bot.get_guild(gid)
        if guild:
            member = guild.get_member(uid)
        speaker = member.display_name if member else "a user"

        system = (
            "You are Smash Bot, a friendly Discord assistant talking OUT LOUD in a "
            "voice channel. Keep answers short, natural and conversational — one to "
            "three sentences, no markdown, no code blocks, no emoji, no bullet lists, "
            "because your reply will be spoken by a text-to-speech voice. If the user "
            "speaks Bulgarian, reply in Bulgarian. You are speaking with " + speaker + "."
        )
        try:
            answer = await ask_ai(
                [prompt], system=system, history=None,
                api_key=chat_key, provider=provider, model=model,
                max_output_tokens=400,
            )
        except Exception as e:
            print(f"[Voice] chat failed: {type(e).__name__}: {e}")
            answer = "Sorry, I couldn't reach my brain just now. Try again in a moment."

        answer = re.sub(r"[*_`#>]", "", (answer or "")).strip()
        if not answer:
            return
        try:
            tts_voice = cfg.get("voice_tts_voice") or DEFAULT_TTS_VOICE
            audio = await synthesize_speech(answer, api_key=groq_key,
                                            model=DEFAULT_TTS_MODEL, voice=tts_voice,
                                            response_format="wav")
        except Exception as e:
            print(f"[Voice] TTS failed: {type(e).__name__}: {e}")
            return
        await self._play_bytes(gid, audio, suffix=".wav")

    # ── playback helpers ──────────────────────────────────────────────
    async def _play_activation(self, gid: int):
        if os.path.exists(_ACTIVATION_SOUND):
            await self._play_file(gid, _ACTIVATION_SOUND, cleanup=False)

    async def _play_bytes(self, gid: int, audio: bytes, suffix=".wav"):
        if not audio:
            return
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            await self._play_file(gid, path, cleanup=True)
        except Exception as e:
            print(f"[Voice] write temp audio failed: {type(e).__name__}: {e}")
            try:
                os.remove(path)
            except OSError:
                pass

    async def _play_file(self, gid: int, path: str, cleanup: bool):
        guild = self.bot.get_guild(gid)
        vc = guild.voice_client if guild else None
        if not vc or not vc.is_connected():
            if cleanup:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return
        st = self._state(gid)
        async with st.play_lock:
            done = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _after(err):
                if err:
                    print(f"[Voice] playback error: {err}")
                loop.call_soon_threadsafe(done.set)

            try:
                st.speaking = True
                if vc.is_playing():
                    vc.stop()
                vc.play(discord.FFmpegPCMAudio(path), after=_after)
                await asyncio.wait_for(done.wait(), timeout=60)
            except Exception as e:
                print(f"[Voice] play failed: {type(e).__name__}: {e}")
            finally:
                await asyncio.sleep(0.2)  # let the tail flush before re-listening
                st.speaking = False
                if cleanup:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    # ── lifecycle ─────────────────────────────────────────────────────
    async def _teardown(self, gid: int):
        st = self._guilds.get(gid)
        if not st:
            return
        if st.poll_task and not st.poll_task.done():
            st.poll_task.cancel()
        guild = self.bot.get_guild(gid)
        vc = guild.voice_client if guild else None
        if vc and VOICE_RECV_OK and isinstance(vc, voice_recv.VoiceRecvClient):
            try:
                vc.stop_listening()
            except Exception:
                pass
        st.sink = None
        st.poll_task = None
        st.awake_until.clear()
        st.processing.clear()
        st.speaking = False

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Auto-leave when the bot is left alone in the channel."""
        guild = member.guild
        vc = guild.voice_client
        if not vc or not vc.is_connected():
            return
        if not VOICE_RECV_OK or not isinstance(vc, voice_recv.VoiceRecvClient):
            return
        humans = [m for m in vc.channel.members if not m.bot]
        if not humans:
            await self._teardown(guild.id)
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceAssistant(bot))
    if VOICE_RECV_OK:
        print("✅ Voice Assistant cog loaded")
    else:
        print("⚠️ Voice Assistant cog loaded (discord-ext-voice-recv missing — /voice commands will explain)")
