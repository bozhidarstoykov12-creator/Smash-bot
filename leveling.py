import discord
from discord.ext import commands, tasks
from discord import app_commands
import time
import random
from utils import (
    load, save, ok, err, info,
    xp_for_level, total_xp_for_level, get_level, xp_progress,
    get_xp_settings, safe_channel_send, doc_lock,
)
from cogs.rankcard import generate_rank_card_file
from cogs.economy import get_cfg as get_eco_cfg, add_balance, get_xp_multiplier

def generate_bar(cur, total, length=12):
    filled = round((cur / total) * length) if total > 0 else 0
    return "█" * filled + "░" * (length - filled)

XP_CD = {}
VOICE_LAST_CHECK = {}


def _format_levelup_msg(raw: str, member: discord.Member, level: int) -> str:
    """Replace all supported placeholders in a level-up message template."""
    return (
        raw
        .replace('{user.mention}', member.mention)
        .replace('{user.name}', member.name)
        .replace('{server.name}', member.guild.name)
        .replace('{user}', member.mention)   # legacy alias
        .replace('{level}', str(level))
    )


class Leveling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_xp_ticker.start()

    def cog_unload(self):
        self.voice_xp_ticker.cancel()

    # ------------------------------------------------------------------
    # Config helpers — reads the FLAT keys that the dashboard writes
    # ------------------------------------------------------------------
    def get_guild_settings(self, gid: str) -> dict:
        # RACE FIX: default/migration patching is a read-modify-write on
        # config.json — a document the Flask dashboard writes from 16
        # endpoints. Hold the lock for the whole read->migrate->save cycle.
        with doc_lock('config.json'):
            """
            Return leveling settings for a guild.

            The dashboard writes these flat keys directly into config[gid]:
                enable_levelup_message  bool
                levelup_type            str  ("channel" | "dm" | "original")
                level_channel           str  (Discord channel ID or "")
                levelup_message         str  (message template)
                level_roles             dict {"<level>": "<role_id>"}
                leveling                dict {xp_min_msg, xp_max_msg, ...}

            Backward compatibility: if the old nested schema
            config[gid]["leveling"]["level_up_msg"] is present it is migrated
            once to the flat keys and removed.
            """
            cfg = load('config.json')
            guild_cfg = cfg.setdefault(gid, {})

            # --- one-time migration from old nested schema ---
            lvl_sub = guild_cfg.get('leveling', {})
            migrated = False
            if 'level_up_msg' in lvl_sub and 'levelup_message' not in guild_cfg:
                guild_cfg['levelup_message'] = lvl_sub.pop('level_up_msg')
                migrated = True
            if 'level_channel' in lvl_sub and 'level_channel' not in guild_cfg:
                guild_cfg['level_channel'] = lvl_sub.pop('level_channel')
                migrated = True
            if migrated:
                cfg[gid] = guild_cfg
                save('config.json', cfg)

            # Defaults for any key not yet set
            defaults = {
                'enable_levelup_message': True,
                'levelup_type': 'channel',
                'level_channel': '',
                'levelup_message': '🎉 GG {user.mention}! You just leveled up to **Level {level}**! 🚀',
                'level_roles': {},
            }
            for k, v in defaults.items():
                guild_cfg.setdefault(k, v)

            return guild_cfg

    def _is_module_enabled(self, gid: str) -> bool:
        """Check the guild-level master switch for the Leveling module."""
        cfg = load('config.json').get(gid, {})
        return cfg.get('leveling', {}).get('enabled', True)

    # ------------------------------------------------------------------
    # Core level-up handler
    # ------------------------------------------------------------------
    async def handle_level_up(
        self,
        member: discord.Member,
        old_level: int,
        new_level: int,
        default_channel=None,
    ):
        if new_level <= old_level:
            return

        gid = str(member.guild.id)
        settings = self.get_guild_settings(gid)

        # Economy reward on level-up
        eco_cfg = get_eco_cfg(gid)
        if eco_cfg.get('earn_levelup'):
            add_balance(gid, member.id, eco_cfg['earn_levelup'], name=member.name)

        # --- Level roles ---
        level_roles: dict = settings.get('level_roles', {})
        role_id = level_roles.get(str(new_level))
        if role_id:
            role = member.guild.get_role(int(role_id))
            if role:
                try:
                    await member.add_roles(role, reason=f"Level {new_level} reward")
                except discord.Forbidden:
                    pass

        # --- Level-up message ---
        if not settings.get('enable_levelup_message', True):
            return

        raw_msg = settings.get(
            'levelup_message',
            '🎉 GG {user.mention}! You just leveled up to **Level {new_level}**! 🚀',
        )
        msg = _format_levelup_msg(raw_msg, member, new_level)

        lvl_type = settings.get('levelup_type', 'channel')

        if lvl_type == 'dm':
            # Try to send via DM, fall back to channel on failure
            try:
                await member.send(msg)
                return
            except discord.HTTPException:
                pass  # fall through to channel delivery

        # Resolve target channel for "channel" and "original" (and DM fallback)
        if lvl_type in ('channel', 'dm'):
            target_channel_id = settings.get('level_channel', '')
            send_channel = None
            if target_channel_id and str(target_channel_id).isdigit():
                send_channel = member.guild.get_channel(int(target_channel_id))
            if not send_channel:
                send_channel = default_channel
        else:
            # "original" — always use the channel the message came from
            send_channel = default_channel

        if not send_channel:
            return

        try:
            card = None
            try:
                card = await generate_rank_card_file(member)
            except Exception as e:
                print(f"[Leveling] Could not generate rank card: {e}")

            if card:
                await safe_channel_send(send_channel, msg, file=card, label="Leveling")
            else:
                await safe_channel_send(send_channel, msg, label="Leveling")
        except Exception as e:
            print(f"[Leveling] Could not send level-up message: {e}")

    # ------------------------------------------------------------------
    # Message XP
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        gid = str(message.guild.id)

        # Module master switch
        if not self._is_module_enabled(gid):
            return

        uid = str(message.author.id)
        now = time.time()

        if gid not in XP_CD:
            XP_CD[gid] = {}

        # Per-guild cooldown (default 60 s)
        xp_cfg = get_xp_settings(gid)
        if now - XP_CD[gid].get(uid, 0) < xp_cfg['xp_cooldown']:
            return

        XP_CD[gid][uid] = now

        # RACE FIX (two-phase): the whole load -> award -> save cycle is now ONE
        # atomic critical section under the shared doc lock — a dashboard
        # api_levels_save running in a Flask thread can no longer overwrite
        # this message's XP, and parallel messages can't clobber each other.
        with doc_lock('levels.json'):
            lvl_data = load('levels.json')

            if gid not in lvl_data:
                lvl_data[gid] = {}
            if uid not in lvl_data[gid]:
                lvl_data[gid][uid] = {"xp": 0, "name": message.author.name}

            old_xp = lvl_data[gid][uid]["xp"]
            old_level = get_level(old_xp)

            # Per-guild XP range + multiplier (+ any active /economy XP boost)
            base_xp = random.randint(xp_cfg['xp_min_msg'], xp_cfg['xp_max_msg'])
            boost = get_xp_multiplier(gid, uid)
            gained = max(1, round(base_xp * xp_cfg['xp_rate'] * boost))
            lvl_data[gid][uid]["xp"] += gained
            lvl_data[gid][uid]["name"] = message.author.name

            new_level = get_level(lvl_data[gid][uid]["xp"])
            save('levels.json', lvl_data)

        # Network phase (outside the lock): level-up announcements only.
        await self.handle_level_up(
            message.author, old_level, new_level, default_channel=message.channel
        )

    # ------------------------------------------------------------------
    # Voice XP ticker
    # ------------------------------------------------------------------
    @tasks.loop(minutes=2)
    async def voice_xp_ticker(self):
        # ── RACE FIX (two-phase, mirrors counting.py) ──────────────────────
        # PHASE 1 — synchronous, atomic, no awaits: award XP + commit to disk.
        # PHASE 2 — network side-effects (level-up embeds) AFTER the commit,
        # so XP gained from messages during those awaits can never be lost.
        pending_levelups = []  # (member, old_level, new_level, default_channel)

        with doc_lock('levels.json'):
            lvl_data = load('levels.json')
            for guild in self.bot.guilds:
                gid = str(guild.id)

                # Module master switch
                if not self._is_module_enabled(gid):
                    continue

                if gid not in lvl_data:
                    lvl_data[gid] = {}

                xp_cfg = get_xp_settings(gid)
                cfg = load('config.json').get(gid, {})
                if not cfg.get('enable_voice_xp', True):
                    continue

                for voice_channel in guild.voice_channels:
                    members = [
                        m for m in voice_channel.members
                        if not m.bot
                        and m.voice
                        and not m.voice.self_deaf
                        and not m.voice.self_mute
                    ]

                    # Only award XP when there are at least 2 people in the channel
                    if len(members) < 2:
                        continue

                    for member in members:
                        uid = str(member.id)
                        if uid not in lvl_data[gid]:
                            lvl_data[gid][uid] = {"xp": 0, "name": member.name}

                        old_xp = lvl_data[gid][uid]["xp"]
                        old_level = get_level(old_xp)

                        base_xp = random.randint(xp_cfg['xp_min_voice'], xp_cfg['xp_max_voice'])
                        boost = get_xp_multiplier(gid, uid)
                        gained = max(1, round(base_xp * xp_cfg['xp_rate'] * boost))
                        lvl_data[gid][uid]["xp"] += gained
                        lvl_data[gid][uid]["name"] = member.name

                        new_level = get_level(lvl_data[gid][uid]["xp"])

                        if new_level > old_level:
                            default_chan = guild.system_channel or next(
                                (
                                    c for c in guild.text_channels
                                    if c.permissions_for(guild.me).send_messages
                                ),
                                None,
                            )
                            pending_levelups.append(
                                (member, old_level, new_level, default_chan)
                            )

            if pending_levelups:
                save('levels.json', lvl_data)

        # PHASE 2 — network only
        for member, old_level, new_level, default_chan in pending_levelups:
            await self.handle_level_up(
                member, old_level, new_level, default_channel=default_chan
            )

    @voice_xp_ticker.before_loop
    async def before_voice_xp(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="addxp", description="[Admin] Add or remove XP from a member")
    @app_commands.describe(member="Who to give/take XP from", amount="XP to add (use a negative number to remove)")
    @app_commands.default_permissions(manage_guild=True)
    async def addxp(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if member.bot:
            return await interaction.response.send_message(embed=err("You can't give XP to a bot!"), ephemeral=True)
        if amount == 0:
            return await interaction.response.send_message(embed=err("Amount can't be 0!"), ephemeral=True)

        gid = str(interaction.guild.id)
        uid = str(member.id)
        # RACE FIX: atomic RMW under the shared doc lock (dashboard edits
        # levels.json from Flask threads).
        with doc_lock('levels.json'):
            lvl_data = load('levels.json')
            if gid not in lvl_data: lvl_data[gid] = {}
            if uid not in lvl_data[gid]: lvl_data[gid][uid] = {"xp": 0, "name": member.name}

            old_xp = lvl_data[gid][uid]["xp"]
            old_level = get_level(old_xp)

            new_xp = max(0, old_xp + amount)
            lvl_data[gid][uid]["xp"] = new_xp
            lvl_data[gid][uid]["name"] = member.name
            save('levels.json', lvl_data)

        new_level = get_level(new_xp)

        await interaction.response.send_message(embed=ok(
            f"{'➕' if amount > 0 else '➖'} XP Updated",
            f"{member.mention} now has **{new_xp} XP** (Level {new_level})\n"
            f"{'Gained' if amount > 0 else 'Lost'} `{abs(amount)}` XP"
        ))

        if new_level > old_level:
            await self.handle_level_up(member, old_level, new_level, default_channel=interaction.channel)

    @app_commands.command(name="rank", description="Check your current level progress status")
    async def rank(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        gid = str(interaction.guild.id)
        uid = str(member.id)
        
        lvl_data = load('levels.json').get(gid, {}).get(uid, {"xp": 0})
        xp = lvl_data["xp"]
        level, cur_xp, needed_xp = xp_progress(xp)
        progress_bar = generate_bar(cur_xp, needed_xp)
        
        em = discord.Embed(title=f"📊 Rank Statistics for {member.name}", color=discord.Color.purple())
        em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(name="✨ Current Level", value=f"`Level {level}`", inline=True)
        em.add_field(name="📈 Total XP Collected", value=f"`{xp} XP`", inline=True)
        em.add_field(name="🎯 Next Level Progress", value=f"`{cur_xp} / {needed_xp} XP`", inline=False)
        em.add_field(name="Progress Bar", value=f"`[{progress_bar}]`", inline=False)
        
        await interaction.response.send_message(embed=em)

async def setup(bot):
    await bot.add_cog(Leveling(bot))
