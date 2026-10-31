import builtins
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta, timezone
import time
import asyncio
import re
import os
import aiohttp
from utils import load, save, ok, err, info, get_level, total_xp_for_level, xp_for_level, rate_limit_wait, doc_lock

# ── Scam-deletion suppression ─────────────────────────────────────────────────
# logger.py checks these to avoid double-logging scam message deletions.
_scam_deleted: dict[int, float] = {}   # msg_id → monotonic time of deletion
_SCAM_DELETED_TTL = 12.0


def _mark_scam_deleted(msg_id: int) -> None:
    """Record that msg_id was deleted by anti-scam (call BEFORE awaiting delete)."""
    _scam_deleted[msg_id] = time.monotonic()
    cutoff = time.monotonic() - _SCAM_DELETED_TTL
    stale = [k for k, v in _scam_deleted.items() if v < cutoff]
    for k in stale:
        del _scam_deleted[k]


def is_scam_deleted(msg_id: int) -> bool:
    """Return True if this message was recently deleted by anti-scam."""
    ts = _scam_deleted.get(msg_id)
    if ts is None:
        return False
    if time.monotonic() - ts > _SCAM_DELETED_TTL:
        _scam_deleted.pop(msg_id, None)
        return False
    return True

# ── Helpers ────────────────────────────────────────────────
def get_mc(guild_id):
    return load('mod_config.json').get(str(guild_id), {})

async def log_action(bot, guild, title, description, color=None):
    mc    = get_mc(guild.id)
    if not mc.get('logging'): return
    ch_id = mc.get('log_channel')
    if not ch_id: return
    ch = bot.get_channel(int(ch_id))
    if not ch: return
    try:
        punch_hex = mc.get('punch_color', '#ed4245').replace('#', '')
        embed_color = int(punch_hex, 16)
    except:
        embed_color = 0xed4245
    em = discord.Embed(title=title, description=description,
                       color=embed_color, timestamp=datetime.now(timezone.utc))
    em.set_footer(text="Mod Log")
    await ch.send(embed=em)

async def send_ghost(member, guild, message):
    if not get_mc(guild.id).get('ghost_msg'): return
    try:
        em = discord.Embed(
            title=f"📨 Moderation Notice — {guild.name}",
            description=message,
            color=0xed4245
        )
        em.set_thumbnail(url=guild.icon.url if guild.icon else None)
        await member.send(embed=em)
    except: pass

async def check_auto_punch(ctx, member, warnings):
    mc = get_mc(ctx.guild.id)
    if not mc.get('auto_punch'): return
    threshold = mc.get('ap_threshold', 3)
    if warnings < threshold: return
    action   = mc.get('ap_action', 'timeout')
    duration = mc.get('ap_duration', 60)
    reason   = f"Auto-punishment: {warnings} warnings reached"
    try:
        if action == 'timeout':
            await member.timeout(timedelta(minutes=duration), reason=reason)
            desc = f"muted for **{duration} min**"
        elif action == 'kick':
            await rate_limit_wait("ban_kick_member", member.guild.id)
            await member.kick(reason=reason)
            desc = "**kicked**"
        elif action == 'ban':
            await rate_limit_wait("ban_kick_member", member.guild.id)
            await member.ban(reason=reason)
            desc = "**banned**"
        else:
            return
        await ctx.send(embed=discord.Embed(
            title="🤖 Auto-Punishment Triggered",
            description=f"{member.mention} was automatically {desc} after reaching **{warnings} warnings**!",
            color=0xff6b35
        ))
        await log_action(ctx.bot, ctx.guild,
            "🤖 Auto-Punishment", f"**User:** {member.mention}\n**Action:** {action}\n**Warnings:** {warnings}")
    except Exception as e:
        print(f"[AutoPunch] Error: {e}")

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tempban_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self.restore_tempbans.start()

    def cog_unload(self):
        self.restore_tempbans.cancel()
        for t in self._tempban_tasks.values():
            t.cancel()

    @tasks.loop(count=1)
    async def restore_tempbans(self):
        """Re-schedule any tempbans that survived a bot restart, and
        immediately unban anyone whose ban already expired while offline."""
        await self.bot.wait_until_ready()
        # RACE FIX (two-phase): Phase 1 — decide + commit synchronously under the
        # document lock (no awaits), so a /tempban issued while we restore can
        # never be silently erased by our later save. Phase 2 — network unbans.
        expired = []  # (gid, uid) to unban in phase 2
        with doc_lock('tempbans.json'):
            tb_data = load('tempbans.json')
            now = time.time()
            restored = 0
            for gid_str, entries in list(tb_data.items()):
                gid = int(gid_str)
                for uid_str, info_entry in list(entries.items()):
                    uid = int(uid_str)
                    remaining = info_entry['unban_at'] - now
                    if remaining <= 0:
                        expired.append((gid, uid))
                        entries.pop(uid_str, None)
                    else:
                        self._schedule_unban(gid, uid, remaining)
                        restored += 1
            save('tempbans.json', tb_data)
        for gid, uid in expired:
            guild = self.bot.get_guild(gid)
            if guild:
                try:
                    await guild.unban(discord.Object(id=uid), reason="Tempban expired (during downtime)")
                except Exception:
                    pass
        if restored:
            print(f"[Moderation] Restored {restored} pending tempban(s)")

    # ── /ban ──────────────────────────────────────────────
    @commands.command(name="ban", help="Ban a member from the server")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member,
                  reason: str = "No reason provided", delete_days: int = 0):
        delete_days = max(0, min(7, delete_days))
        try:
            await rate_limit_wait("ban_kick_member", ctx.guild.id)
            await member.ban(reason=reason, delete_message_days=delete_days)
        except discord.Forbidden:
            return await ctx.send(embed=err("I don't have permission to ban that member."))
        except discord.HTTPException as e:
            return await ctx.send(embed=err(f"Ban failed: {e}"))
        mc  = get_mc(ctx.guild.id)
        col = int(mc.get('punch_color','#ed4245').replace('#',''), 16)
        em  = discord.Embed(title="🔨 Banned", color=col)
        em.add_field(name="User",   value=f"{member.mention} (`{member}`)")
        em.add_field(name="Reason", value=reason)
        if delete_days: em.add_field(name="Messages Deleted", value=f"{delete_days} day(s)")
        em.set_footer(text=f"By {ctx.author}")
        em.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=em)
        await send_ghost(member, ctx.guild, f"You were **banned** from **{ctx.guild.name}**.\nReason: {reason}")
        await log_action(self.bot, ctx.guild, "🔨 Member Banned",
            f"**User:** {member.mention} (`{member.id}`)\n**By:** {ctx.author.mention}\n**Reason:** {reason}")

    # ── /unban ────────────────────────────────────────────
    @commands.command(name="unban", help="Unban a user by their ID")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: str, reason: str = "No reason provided"):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await ctx.guild.unban(user, reason=reason)
            await ctx.send(embed=ok("Unbanned", f"**{user}** has been unbanned.\nReason: {reason}"))
            await log_action(self.bot, ctx.guild, "✅ Member Unbanned",
                f"**User:** {user} (`{user_id}`)\n**By:** {ctx.author.mention}\n**Reason:** {reason}")
        except discord.NotFound:
            await ctx.send(embed=err("User not found or not banned!"))
        except ValueError:
            await ctx.send(embed=err("Invalid user ID!"))

    # ── /softban ──────────────────────────────────────────
    @commands.command(name="softban", help="Ban then immediately unban (deletes messages, user can rejoin)")
    @commands.has_permissions(ban_members=True)
    async def softban(self, ctx: commands.Context, member: discord.Member,
                      reason: str = "No reason provided", delete_days: int = 1):
        try:
            await rate_limit_wait("ban_kick_member", ctx.guild.id)
            await member.ban(reason=f"[Softban] {reason}", delete_message_days=min(7, delete_days))
            await ctx.guild.unban(member, reason="Softban unban")
        except discord.Forbidden:
            return await ctx.send(embed=err("I don't have permission to softban that member."))
        except discord.HTTPException as e:
            return await ctx.send(embed=err(f"Softban failed: {e}"))
        em = discord.Embed(title="🪃 Softbanned", description=f"{member.mention} was softbanned.\n**Reason:** {reason}", color=0xff9800)
        em.set_footer(text=f"By {ctx.author}")
        await ctx.send(embed=em)
        await send_ghost(member, ctx.guild, f"You were **softbanned** (messages deleted, you may rejoin).\nReason: {reason}")
        await log_action(self.bot, ctx.guild, "🪃 Member Softbanned",
            f"**User:** {member.mention}\n**By:** {ctx.author.mention}\n**Reason:** {reason}")

    # ── /tempban ──────────────────────────────────────────
    @commands.command(name="tempban", help="Temporarily ban a member")
    @commands.has_permissions(ban_members=True)
    async def tempban(self, ctx: commands.Context, member: discord.Member,
                      hours: int = 24, reason: str = "No reason provided"):
        try:
            await rate_limit_wait("ban_kick_member", ctx.guild.id)
            await member.ban(reason=f"[Tempban {hours}h] {reason}", delete_message_days=0)
        except discord.Forbidden:
            return await ctx.send(embed=err("I don't have permission to ban that member."))
        except discord.HTTPException as e:
            return await ctx.send(embed=err(f"Tempban failed: {e}"))
        em = discord.Embed(title="⏳ Temp Banned", color=0xff6b35)
        em.add_field(name="User",     value=member.mention)
        em.add_field(name="Duration", value=f"{hours} hour(s)")
        em.add_field(name="Reason",   value=reason, inline=False)
        em.set_footer(text=f"By {ctx.author}")
        await ctx.send(embed=em)
        await send_ghost(member, ctx.guild, f"You were **temporarily banned** for {hours}h.\nReason: {reason}")
        await log_action(self.bot, ctx.guild, "⏳ Temp Ban",
            f"**User:** {member.mention}\n**Duration:** {hours}h\n**By:** {ctx.author.mention}\n**Reason:** {reason}")

        # Persist so a bot restart doesn't permanently strand this as a real ban
        # RACE FIX: locked RMW — dashboard api_members_tempban writes this file
        # from Flask threads, and a parallel /tempban must not drop this entry.
        with doc_lock('tempbans.json'):
            tb_data = load('tempbans.json')
            tb_data.setdefault(str(ctx.guild.id), {})[str(member.id)] = {
                'unban_at': time.time() + hours * 3600,
                'reason':   reason,
            }
            save('tempbans.json', tb_data)

        self._schedule_unban(ctx.guild.id, member.id, hours * 3600)

    # ── /untempban ────────────────────────────────────────
    @commands.command(name="untempban", help="End an active tempban early")
    @commands.has_permissions(ban_members=True)
    async def untempban(self, ctx: commands.Context, user_id: str):
        gid = ctx.guild.id
        try:
            uid = int(user_id)
        except ValueError:
            return await ctx.send(
                embed=err("That doesn't look like a valid user ID."))

        # RACE FIX (two-phase): remove from storage under the lock FIRST,
        # then do the network unban — a concurrent /tempban can no longer be
        # erased by this handler's save.
        with doc_lock('tempbans.json'):
            tb_data = load('tempbans.json')
            entry = tb_data.get(str(gid), {}).get(str(uid))
            if entry:
                tb_data.get(str(gid), {}).pop(str(uid), None)
                save('tempbans.json', tb_data)
        if not entry:
            return await ctx.send(
                embed=err("That user doesn't have an active tempban tracked by this bot."))

        task = self._tempban_tasks.pop((gid, uid), None)
        if task:
            task.cancel()

        try:
            await ctx.guild.unban(discord.Object(id=uid), reason=f"Tempban ended early by {ctx.author}")
        except Exception:
            pass

        await ctx.send(
            embed=ok("Tempban Ended", f"<@{uid}> has been unbanned early."))

    def _schedule_unban(self, gid: int, uid: int, delay: float):
        task = self.bot.loop.create_task(self._unban_after(gid, uid, delay))
        self._tempban_tasks[(gid, uid)] = task

    async def _unban_after(self, gid: int, uid: int, delay: float):
        try:
            await asyncio.sleep(delay)
            guild = self.bot.get_guild(gid)
            if guild:
                try:
                    user_obj = discord.Object(id=uid)
                    await guild.unban(user_obj, reason="Tempban expired")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            self._tempban_tasks.pop((gid, uid), None)
            # RACE FIX: locked cleanup (dashboard may write concurrently).
            with doc_lock('tempbans.json'):
                tb_data = load('tempbans.json')
                tb_data.get(str(gid), {}).pop(str(uid), None)
                save('tempbans.json', tb_data)

    # ── /kick ─────────────────────────────────────────────
    @commands.command(name="kick", help="Kick a member")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, reason: str = "No reason provided"):
        try:
            await rate_limit_wait("ban_kick_member", ctx.guild.id)
            await member.kick(reason=reason)
        except discord.Forbidden:
            return await ctx.send(embed=err("I don't have permission to kick that member."))
        except discord.HTTPException as e:
            return await ctx.send(embed=err(f"Kick failed: {e}"))
        mc  = get_mc(ctx.guild.id)
        col = int(mc.get('punch_color','#ed4245').replace('#',''), 16)
        em  = discord.Embed(title="👢 Kicked", description=f"{member.mention} was kicked!\n**Reason:** {reason}", color=col)
        em.set_footer(text=f"By {ctx.author}")
        await ctx.send(embed=em)
        await send_ghost(member, ctx.guild, f"You were **kicked** from **{ctx.guild.name}**.\nReason: {reason}")
        await log_action(self.bot, ctx.guild, "👢 Member Kicked",
            f"**User:** {member.mention}\n**By:** {ctx.author.mention}\n**Reason:** {reason}")

    # ── /mute ─────────────────────────────────────────────
    @commands.command(name="mute", help="Timeout a member")
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx: commands.Context, member: discord.Member,
                   minutes: int = 10, reason: str = "No reason provided"):
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        em = discord.Embed(title="🔇 Muted",
            description=f"{member.mention} muted for **{minutes} min**!\n**Reason:** {reason}",
            color=discord.Color.greyple())
        em.set_footer(text=f"By {ctx.author}")
        await ctx.send(embed=em)
        await send_ghost(member, ctx.guild, f"You were **muted** for {minutes} minutes.\nReason: {reason}")
        await log_action(self.bot, ctx.guild, "🔇 Member Muted",
            f"**User:** {member.mention}\n**Duration:** {minutes} min\n**By:** {ctx.author.mention}\n**Reason:** {reason}")

    # ── /unmute ───────────────────────────────────────────
    @commands.command(name="unmute", help="Remove timeout from a member")
    @commands.has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        await member.timeout(None)
        await ctx.send(embed=ok("🔊 Unmuted", f"{member.mention} has been unmuted!"))
        await log_action(self.bot, ctx.guild, "🔊 Member Unmuted",
            f"**User:** {member.mention}\n**By:** {ctx.author.mention}")

    # ── /warn ─────────────────────────────────────────────
    @commands.command(name="warn", help="Warn a member")
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, reason: str = "No reason provided"):
        # RACE FIX: locked RMW — parallel /warn and _check_scam auto-warns
        # must not drop each other's append.
        with doc_lock('warnings.json'):
            w = load('warnings.json')
            gid, uid = str(ctx.guild.id), str(member.id)
            w.setdefault(gid, {}).setdefault(uid, []).append({
                'reason': reason, 'time': str(datetime.now(timezone.utc))[:10], 'by': str(ctx.author)
            })
            save('warnings.json', w)
            count = len(w[gid][uid])
        em = discord.Embed(title=f"⚠️ Warning #{count}",
            description=f"{member.mention} received warning #{count}.\n**Reason:** {reason}",
            color=discord.Color.yellow())
        em.set_footer(text=f"By {ctx.author}")
        await ctx.send(embed=em)
        await send_ghost(member, ctx.guild, f"You received **Warning #{count}**.\nReason: {reason}")
        await log_action(self.bot, ctx.guild, f"⚠️ Warning #{count}",
            f"**User:** {member.mention}\n**By:** {ctx.author.mention}\n**Reason:** {reason}")
        if hasattr(builtins, 'refresh_bot_cache'): builtins.refresh_bot_cache()
        await check_auto_punch(ctx, member, count)

    # ── /warnings ─────────────────────────────────────────
    @commands.command(name="warnings", help="View warnings for a member")
    async def warnings(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        w = load('warnings.json').get(str(ctx.guild.id), {}).get(str(member.id), [])
        em = discord.Embed(title=f"⚠️ Warnings — {member.display_name}", color=discord.Color.yellow())
        em.set_thumbnail(url=member.display_avatar.url)
        em.description = "No warnings! 😇" if not w else ""
        for i, x in enumerate(w, 1):
            em.add_field(name=f"#{i} — {x.get('time','?')} | By {x.get('by','?')}", value=x['reason'], inline=False)
        await ctx.send(embed=em)

    # ── /clearwarns ───────────────────────────────────────
    @commands.command(name="clearwarns", help="Clear warnings for a member")
    @commands.has_permissions(manage_messages=True)
    async def clearwarns(self, ctx: commands.Context, member: discord.Member, count: int = 0):
        gid, uid = str(ctx.guild.id), str(member.id)
        with doc_lock('warnings.json'):
            w = load('warnings.json')
            warns = w.get(gid, {}).get(uid, [])
            if not warns:
                removed = []
            elif count > 0:
                removed = warns[-count:]
                w[gid][uid] = warns[:-count]
                save('warnings.json', w)
            else:
                removed = warns
                w[gid][uid] = []
                save('warnings.json', w)
        if not removed:
            return await ctx.send(embed=err("No warnings found!"))
        await ctx.send(embed=ok(f"Cleared {len(removed)} warning(s) for {member.display_name}"))

    # ── /clear ────────────────────────────────────────────
    @commands.command(name="clear", help="Delete messages from a channel")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, amount: int = 10, member: discord.Member = None):
        if member:
            def check(m): return m.author == member
            deleted = await ctx.channel.purge(limit=amount * 5, check=check, bulk=True)
            deleted = deleted[:amount]
        else:
            deleted = await ctx.channel.purge(limit=amount)
        await ctx.send(embed=ok(f"Deleted {len(deleted)} messages!"))
        await log_action(self.bot, ctx.guild, "🗑️ Messages Purged",
            f"**{len(deleted)} messages** in {ctx.channel.mention}\n**By:** {ctx.author.mention}"
            + (f"\n**From:** {member.mention}" if member else ""))

    # ── /slowmode ─────────────────────────────────────────
    @commands.command(name="slowmode", help="Set slowmode in a channel")
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: int, channel: discord.TextChannel = None):
        ch = channel or ctx.channel
        await ch.edit(slowmode_delay=max(0, min(21600, seconds)))
        if seconds == 0:
            await ctx.send(embed=ok("⚡ Slowmode Disabled", f"Removed slowmode in {ch.mention}"))
        else:
            await ctx.send(embed=ok("🐢 Slowmode Set", f"**{seconds}s** slowmode in {ch.mention}"))
        await log_action(self.bot, ctx.guild, "🐢 Slowmode Changed",
            f"**Channel:** {ch.mention}\n**Delay:** {seconds}s\n**By:** {ctx.author.mention}")

    # ── /lock ─────────────────────────────────────────────
    @commands.command(name="lock", help="Lock a channel so members can't send messages")
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: discord.TextChannel = None, reason: str = "No reason provided"):
        ch = channel or ctx.channel
        overwrite = ch.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await ch.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=reason)
        em = discord.Embed(title="🔒 Channel Locked",
            description=f"{ch.mention} has been locked.\n**Reason:** {reason}",
            color=discord.Color.red())
        await ctx.send(embed=em)
        await log_action(self.bot, ctx.guild, "🔒 Channel Locked",
            f"**Channel:** {ch.mention}\n**By:** {ctx.author.mention}\n**Reason:** {reason}")

    # ── /unlock ───────────────────────────────────────────
    @commands.command(name="unlock", help="Unlock a locked channel")
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: discord.TextChannel = None):
        ch = channel or ctx.channel
        overwrite = ch.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await ch.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        em = discord.Embed(title="🔓 Channel Unlocked",
            description=f"{ch.mention} is now unlocked.", color=discord.Color.green())
        await ctx.send(embed=em)
        await log_action(self.bot, ctx.guild, "🔓 Channel Unlocked",
            f"**Channel:** {ch.mention}\n**By:** {ctx.author.mention}")

    # ── /hide / /unhide ───────────────────────────────────
    @commands.command(name="hide", help="Hide a channel from regular members")
    @commands.has_permissions(manage_channels=True)
    async def hide(self, ctx: commands.Context, channel: discord.TextChannel = None, reason: str = "No reason"):
        ch = channel or ctx.channel
        overwrite = ch.overwrites_for(ctx.guild.default_role)
        overwrite.view_channel = False
        await ch.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=reason)
        await ctx.send(embed=ok("👁️ Channel Hidden", f"{ch.mention} is now hidden."))
        await log_action(self.bot, ctx.guild, "👁️ Channel Hidden",
            f"**Channel:** {ch.mention}\n**By:** {ctx.author.mention}\n**Reason:** {reason}")

    @commands.command(name="unhide", help="Unhide a hidden channel")
    @commands.has_permissions(manage_channels=True)
    async def unhide(self, ctx: commands.Context, channel: discord.TextChannel = None):
        ch = channel or ctx.channel
        overwrite = ch.overwrites_for(ctx.guild.default_role)
        overwrite.view_channel = None
        await ch.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(embed=ok("👁️ Channel Visible", f"{ch.mention} is now visible."))

    # ── /nick ─────────────────────────────────────────────
    @commands.command(name="nick", help="Change a member's nickname")
    @commands.has_permissions(manage_nicknames=True)
    async def nick(self, ctx: commands.Context, member: discord.Member, nickname: str = None):
        old = member.display_name
        await rate_limit_wait("modify_guild_member", ctx.guild.id)
        await member.edit(nick=nickname)
        if nickname:
            await ctx.send(embed=ok("✏️ Nickname Changed", f"**{old}** → **{nickname}**"))
        else:
            await ctx.send(embed=ok("✏️ Nickname Reset", f"**{member.name}**'s nickname was reset."))
        await log_action(self.bot, ctx.guild, "✏️ Nickname Changed",
            f"**User:** {member.mention}\n**Old:** {old}\n**New:** {nickname or 'Reset'}\n**By:** {ctx.author.mention}")

    # ── /note ─────────────────────────────────────────────
    @commands.command(name="note", help="Add a private moderator note about a user")
    @commands.has_permissions(manage_messages=True)
    async def note(self, ctx: commands.Context, member: discord.Member, note: str):
        # RACE FIX: locked RMW — parallel /note commands must not drop each
        # other's append, and _check_scam also appends to notes-like files.
        with doc_lock('notes.json'):
            notes = load('notes.json')
            gid, uid = str(ctx.guild.id), str(member.id)
            notes.setdefault(gid, {}).setdefault(uid, []).append({
                'note': note, 'by': str(ctx.author), 'time': str(datetime.now(timezone.utc))[:16]
            })
            save('notes.json', notes)
            count = len(notes[gid][uid])
        await ctx.send(
            embed=ok(f"📝 Note Added (#{count})", f"Note about {member.mention}:\n> {note}"))

    @commands.command(name="notes", help="View moderator notes about a user")
    @commands.has_permissions(manage_messages=True)
    async def notes(self, ctx: commands.Context, member: discord.Member):
        notes = load('notes.json').get(str(ctx.guild.id), {}).get(str(member.id), [])
        em = discord.Embed(title=f"📝 Notes — {member.display_name}", color=discord.Color.blue())
        em.set_thumbnail(url=member.display_avatar.url)
        if not notes:
            em.description = "No notes for this user."
        for i, n in enumerate(notes, 1):
            em.add_field(name=f"#{i} — {n.get('time','?')} by {n.get('by','?')}", value=n['note'], inline=False)
        await ctx.send(embed=em)

    # ── /announce ─────────────────────────────────────────
    @commands.command(name="announce", help="Send an announcement embed")
    @commands.has_permissions(manage_messages=True)
    async def announce(self, ctx: commands.Context,
                       title: str, message: str,
                       channel: discord.TextChannel = None,
                       color: str = "#5865f2",
                       ping_role: discord.Role = None):
        ch = channel or ctx.channel
        try:
            col = int(color.replace('#', ''), 16)
        except:
            col = 0x5865f2
        em = discord.Embed(title=title, description=message, color=col, timestamp=datetime.now(timezone.utc))
        em.set_footer(text=f"{ctx.guild.name}", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        ping = f"{ping_role.mention} " if ping_role else ""
        await ch.send(content=ping if ping else None, embed=em)
        await ctx.send(embed=ok("📢 Announced!", f"Sent to {ch.mention}"))

    # ── /role ─────────────────────────────────────────────
    @commands.command(name="role", help="Add or remove a role from a member")
    @commands.has_permissions(manage_roles=True)
    async def role(self, ctx: commands.Context, action: str, member: discord.Member, role: discord.Role):
        if action == 'add':
            await rate_limit_wait("add_guild_member_role", ctx.guild.id)
            await member.add_roles(role)
            await ctx.send(embed=ok("✅ Role Added", f"Added {role.mention} to {member.mention}"))
        elif action == 'remove':
            await rate_limit_wait("add_guild_member_role", ctx.guild.id)
            await member.remove_roles(role)
            await ctx.send(embed=ok("✅ Role Removed", f"Removed {role.mention} from {member.mention}"))
        else:
            await ctx.send(embed=err("Use `add` or `remove`"))
        await log_action(self.bot, ctx.guild, f"🎭 Role {action.capitalize()}ed",
            f"**User:** {member.mention}\n**Role:** {role.mention}\n**By:** {ctx.author.mention}")

    # ── /massrole ─────────────────────────────────────────
    @commands.command(name="massrole", help="Add or remove a role from ALL members")
    @commands.has_permissions(administrator=True)
    async def massrole(self, ctx: commands.Context, action: str, role: discord.Role):
        count = 0
        for member in ctx.guild.members:
            if member.bot: continue
            try:
                if action == 'add' and role not in member.roles:
                    await rate_limit_wait("add_guild_member_role", ctx.guild.id)
                    await member.add_roles(role)
                    count += 1
                elif action == 'remove' and role in member.roles:
                    await rate_limit_wait("add_guild_member_role", ctx.guild.id)
                    await member.remove_roles(role)
                    count += 1
            except: pass
        await ctx.send(embed=ok(f"Mass Role — {action.capitalize()}",
            f"{role.mention} {action}ed for **{count}** members."))
        await log_action(self.bot, ctx.guild, f"🎭 Mass Role {action.capitalize()}",
            f"**Role:** {role.mention}\n**Affected:** {count} members\n**By:** {ctx.author.mention}")

    # ── /setlogchannel ────────────────────────────────────
    @commands.command(name="setlogchannel", help="Set the mod log channel")
    @commands.has_permissions(administrator=True)
    async def setlogchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        # RACE FIX: locked RMW (dashboard api_moderation_save writes this doc).
        with doc_lock('mod_config.json'):
            cfg = load('mod_config.json')
            cfg.setdefault(str(ctx.guild.id), {})['log_channel'] = channel.id
            cfg[str(ctx.guild.id)]['logging'] = True
            save('mod_config.json', cfg)
        await ctx.send(embed=ok("✅ Log Channel Set!", f"Mod logs → {channel.mention}"))

    # ── /serverinfo ───────────────────────────────────────
    @commands.command(name="serverinfo", help="Show server information")
    async def serverinfo(self, ctx: commands.Context):
        g  = ctx.guild
        bots   = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        em = discord.Embed(title=f"ℹ️ {g.name}", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
        if g.icon: em.set_thumbnail(url=g.icon.url)
        em.add_field(name="👑 Owner",     value=g.owner.mention)
        em.add_field(name="👥 Members",   value=f"{humans} humans, {bots} bots")
        em.add_field(name="📅 Created",   value=g.created_at.strftime("%d/%m/%Y"))
        em.add_field(name="💬 Channels",  value=f"{len(g.text_channels)} text, {len(g.voice_channels)} voice")
        em.add_field(name="🎭 Roles",     value=len(g.roles))
        em.add_field(name="😀 Emojis",    value=len(g.emojis))
        em.add_field(name="🆙 Boost Lvl", value=g.premium_tier)
        em.add_field(name="🔒 Verif.",    value=str(g.verification_level).capitalize())
        await ctx.send(embed=em)

    # ── /userinfo ─────────────────────────────────────────
    @commands.command(name="userinfo", help="Show detailed info about a member")
    async def userinfo(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        gid = str(ctx.guild.id)
        uid = str(member.id)

        warnings = load('warnings.json').get(gid, {}).get(uid, [])
        notes    = load('notes.json').get(gid, {}).get(uid, [])

        # XP & level
        lvl_data = load('levels.json').get(gid, {}).get(uid, {"xp": 0})
        xp = lvl_data.get("xp", 0)
        level = get_level(xp)
        cur_xp = xp - total_xp_for_level(level)
        needed_xp = xp_for_level(level)
        fill = round((cur_xp / needed_xp) * 10) if needed_xp else 0
        progress_bar = "█" * fill + "░" * (10 - fill)

        # Roles
        roles = [r for r in reversed(member.roles) if r.name != "@everyone"]
        roles_str = " ".join(r.mention for r in roles[:10]) or "*No roles*"
        if len(roles) > 10:
            roles_str += f" *+{len(roles)-10} more*"

        # Dates
        now = datetime.now(timezone.utc)
        account_age = (now - member.created_at).days
        server_age  = (now - member.joined_at).days if member.joined_at else 0

        # Join position
        sorted_members = sorted(ctx.guild.members, key=lambda m: m.joined_at or now)
        join_pos = next((i+1 for i, m in enumerate(sorted_members) if m.id == member.id), "?")

        # Status
        status_map = {discord.Status.online: "🟢 Online", discord.Status.idle: "🟡 Idle",
                      discord.Status.dnd: "🔴 Do Not Disturb", discord.Status.offline: "⚫ Offline"}

        # Badges
        flags = member.public_flags
        badges = []
        if flags.staff:                  badges.append("👨‍💼 Discord Staff")
        if flags.partner:                badges.append("🤝 Partner")
        if flags.hypesquad:              badges.append("🏠 HypeSquad")
        if flags.bug_hunter:             badges.append("🐛 Bug Hunter")
        if flags.early_supporter:        badges.append("⭐ Early Supporter")
        if flags.verified_bot_developer: badges.append("🤖 Bot Dev")
        if member.premium_since:         badges.append("💜 Server Booster")

        color = member.color if member.color.value else discord.Color.blurple()
        em = discord.Embed(title=f"👤 {member.display_name}", color=color,
                           timestamp=datetime.now(timezone.utc))
        em.set_thumbnail(url=member.display_avatar.url)

        em.add_field(name="🏷️ Tag",     value=f"`{member}`",      inline=True)
        em.add_field(name="🆔 ID",      value=f"`{member.id}`",   inline=True)
        em.add_field(name="📡 Status",  value=status_map.get(member.status, "⚫ Offline"), inline=True)

        em.add_field(name="📅 Account Created",
                     value=f"<t:{int(member.created_at.timestamp())}:D>\n*{account_age:,} days ago*", inline=True)
        em.add_field(name="📥 Joined Server",
                     value=f"<t:{int(member.joined_at.timestamp())}:D>\n*{server_age:,} days ago*" if member.joined_at else "?", inline=True)
        em.add_field(name="📊 Join Position", value=f"`#{join_pos}`", inline=True)

        em.add_field(name=f"🎭 Roles [{len(roles)}]", value=roles_str, inline=False)

        em.add_field(name="⭐ Level & XP",
                     value=f"Level **{level}** — `{xp:,}` total XP\n`[{progress_bar}]` {cur_xp}/{needed_xp}", inline=True)
        em.add_field(name="⚠️ Warnings",
                     value=f"`{len(warnings)}` warning(s)" + (f"\n📝 `{len(notes)}` mod note(s)" if notes else ""), inline=True)

        if badges:
            em.add_field(name="🏅 Badges", value="  ".join(badges), inline=False)

        em.set_footer(text=f"{'🤖 Bot' if member.bot else f'Requested by {ctx.author}'}",
                      icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=em)

    # ── /setwelcome / /setautorole ─────────────────────────
    @commands.command(name="setwelcome", help="Set the welcome channel")
    @commands.has_permissions(administrator=True)
    async def setwelcome(self, ctx: commands.Context, channel: discord.TextChannel):
        # RACE FIX: locked RMW (dashboard writes config.json from Flask threads).
        with doc_lock('config.json'):
            cfg = load('config.json')
            cfg.setdefault(str(ctx.guild.id), {})['welcome_channel'] = channel.id
            save('config.json', cfg)
        await ctx.send(embed=ok("Welcome Channel Set!", f"Welcome messages → {channel.mention}"))

    @commands.command(name="setautorole", help="Set the auto-role for new members")
    @commands.has_permissions(administrator=True)
    async def setautorole(self, ctx: commands.Context, role: discord.Role):
        # RACE FIX: locked RMW (dashboard writes config.json from Flask threads).
        with doc_lock('config.json'):
            cfg = load('config.json')
            cfg.setdefault(str(ctx.guild.id), {})['auto_role'] = role.id
            save('config.json', cfg)
        await ctx.send(embed=ok("Auto-role Set!", f"New members get {role.mention}"))

    # ── on_member_join ────────────────────────────────────
    # ── Anti-Scam detection (MrBeast crypto/giveaway type scams) ──
    # Scoring: each pattern that matches = 1 point. 2+ points = scam action.
    # Patterns are deliberately broad — real messages hit 5-10 of these.
    _SCAM_PATTERNS = [
        # Fake celebrity + casino/crypto keywords
        re.compile(r'mrbeast', re.I),
        re.compile(r'mr\.?\s*beast', re.I),
        # Known scam casino/crypto domains
        re.compile(r'\b(vyro|fuazwin|fuzawin|fuzewin|rollbit|bc\.game|roobet|betfury|duelbits|stake\.com)\b', re.I),
        # "pleased to announce" opener — nearly exclusive to these scams
        re.compile(r'i am pleased to announce', re.I),
        # Casino game launch announcement
        re.compile(r'(launch|launching).{0,40}(crypto|casino|coin|token)', re.I | re.S),
        # "giving away" + amount + registration
        re.compile(r'giving.{0,60}away.{0,60}(register|sign.?up|join)', re.I | re.S),
        # Withdrawal success (screenshot description)
        re.compile(r'withdrawal.{0,20}success', re.I),
        # Withdraw the bonus
        re.compile(r'withdraw.{0,30}bonus', re.I | re.S),
        # Claim your reward
        re.compile(r'claim.{0,30}reward', re.I | re.S),
        # Promo code + gambling keyword
        re.compile(r'promo.{0,10}code', re.I),
        # "bonus immediately"
        re.compile(r'bonus.{0,30}immediately', re.I | re.S),
        # Free money for registering
        re.compile(r'(everyone who register|everyone who sign)', re.I),
        # Casino + giveaway combo
        re.compile(r'casino.{0,100}(giveaway|giving away|register|sign.?up)', re.I | re.S),
        # "receive your" + bonus/prize
        re.compile(r'receive your.{0,30}(bonus|prize|reward|usdt|crypto)', re.I | re.S),
        # Suspicious "go to:" + URL pattern typical of scam instructions
        re.compile(r'go to\s*:.{0,80}(\.com|\.gg|\.io|\.net)', re.I | re.S),
        # Withdrawal screenshot with crypto amount — "Withdrawal of $5600 ... USDT"
        re.compile(r'withdrawal.{0,80}(usdt|btc|eth|bnb|\$[\d ,]{3,})', re.I | re.S),
        # "Was Successfully" in context of a financial transfer
        re.compile(r'was successfully.{0,60}(wallet|transfer|usdt|btc|eth|\$[\d ,]+)', re.I | re.S),
        # Large USDT / crypto payout shown on screen
        re.compile(r'\+\s*[\d ,]{3,}\s*(usdt|btc|eth|bnb)', re.I),
    ]

    # ── OCR.space image text extraction ──────────────────────────────────────
    _OCR_KEY     = os.environ.get("OCR_SPACE_KEY", "")
    _OCR_B64_URL = "https://api.ocr.space/parse/image"   # /parse/base64 → 404
    _OCR_WARNED  = False

    def _ocr_key_ready(self) -> str | None:
        """Return the OCR key, or None (and warn once) if unconfigured."""
        key = self._OCR_KEY.strip()
        if not key:
            if not Moderation._OCR_WARNED:
                Moderation._OCR_WARNED = True
                print(
                    "[AntiScam] WARNING: OCR_SPACE_KEY env var is not set. "
                    "Image-based scam detection is DISABLED. "
                    "Get a free key at https://ocr.space/ocrapi and add it as a secret."
                )
            return None
        return key

    async def _ocr_attachment(
        self,
        session: aiohttp.ClientSession,
        attachment: discord.Attachment,
    ) -> str:
        """Download an attachment and OCR it via the OCR.space base64 API.

        WHY base64 and not the /parse/imageurl endpoint:
        Discord CDN attachment URLs are signed tokens accessible only from the
        bot's IP/session. OCR.space fetches from its own servers and cannot reach
        those URLs — /parse/imageurl always returns empty for Discord files.
        Downloading the bytes ourselves and sending them as base64 sidesteps the
        problem entirely.
        """
        import base64 as _b64

        key = self._ocr_key_ready()
        if key is None:
            return ""

        # 1. Download the image bytes (discord.py handles auth automatically)
        try:
            image_bytes = await attachment.read()
        except Exception as exc:
            print(f"[AntiScam] Could not download {attachment.filename}: {exc}")
            return ""

        # 2. Build data-URI that OCR.space expects
        mime = attachment.content_type or "image/png"
        data_uri = f"data:{mime};base64,{_b64.b64encode(image_bytes).decode()}"

        # 3. Send to OCR.space base64 endpoint
        try:
            async with session.post(
                self._OCR_B64_URL,
                data={
                    "apikey":            key,
                    "base64Image":       data_uri,
                    "language":          "eng",
                    "isOverlayRequired": "false",
                    "detectOrientation": "true",
                    "scale":             "true",
                    "OCREngine":         "2",   # better for screenshots / dark UIs
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 403:
                    if not Moderation._OCR_WARNED:
                        Moderation._OCR_WARNED = True
                        print("[AntiScam] WARNING: OCR.space returned 403 — "
                              "check OCR_SPACE_KEY or monthly quota.")
                    return ""
                if resp.status != 200:
                    print(f"[AntiScam] OCR.space HTTP {resp.status} for {attachment.filename}")
                    return ""
                data = await resp.json()
                if data.get("IsErroredOnProcessing"):
                    err = data.get("ErrorMessage") or data.get("ErrorDetails", "unknown")
                    print(f"[AntiScam] OCR.space error: {err}")
                    return ""
                results = data.get("ParsedResults") or []
                texts = [r.get("ParsedText", "") for r in results if r.get("ParsedText")]
                return "\n".join(texts)
        except Exception as exc:
            print(f"[AntiScam] OCR.space request failed: {exc}")
            return ""

    async def _check_scam(self, message: discord.Message) -> bool:
        """Returns True if the message looks like a scam and was deleted."""
        mc = get_mc(message.guild.id)
        if not mc.get('anti_scam', False):
            return False
        if message.author.bot:
            return False
        if message.author.guild_permissions.administrator:
            return False

        text = message.content or ""
        text_hits = sum(1 for p in self._SCAM_PATTERNS if p.search(text))

        # ── Scan image attachments via OCR if anti_scam is on ────────────────
        image_ocr_text = ""
        image_hits = 0
        attachments = [a for a in message.attachments
                       if a.content_type and a.content_type.startswith("image/")]
        if attachments and mc.get('anti_scam', False):
            async with aiohttp.ClientSession() as session:
                for attachment in attachments[:3]:   # max 3 images per message
                    ocr_text = await self._ocr_attachment(session, attachment)
                    if ocr_text:
                        image_ocr_text += "\n" + ocr_text
                        image_hits += sum(1 for p in self._SCAM_PATTERNS if p.search(ocr_text))

        total_hits = text_hits + image_hits

        # Threshold logic:
        #   • 2+ hits total (text + image combined) → always act
        #   • Image-only message (no text) with 1+ image hit → act  (screenshot spam
        #     with no caption rarely has innocent explanations)
        image_only = not text.strip() and attachments
        if total_hits < 2 and not (image_only and image_hits >= 1):
            return False

        source = []
        if text_hits >= 2:
            source.append(f"text ({text_hits} hits)")
        if image_hits >= 2:
            source.append(f"image OCR ({image_hits} hits)")
        elif image_hits == 1 and text_hits >= 1:
            source.append(f"text+image combo ({total_hits} total hits)")
        source_str = " + ".join(source) if source else f"{total_hits} total hits"

        # Mark BEFORE awaiting delete so logger.on_message_delete skips it
        _mark_scam_deleted(message.id)
        try:
            await rate_limit_wait("delete_message", message.channel.id)
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        # DM the user
        try:
            await message.author.send(embed=discord.Embed(
                title="⚠️ Message Removed — Scam Detected",
                description=(
                    f"Your message in **{message.guild.name}** was automatically removed "
                    f"because it matched patterns associated with fake giveaway/crypto scams "
                    f"(e.g. fake MrBeast promotions, withdrawal success screenshots, "
                    f"crypto casino sign-up links).\n\n"
                    f"If this was a mistake, please contact a server admin."
                ),
                color=0xed4245,
            ))
        except discord.Forbidden:
            pass

        # Log it
        preview = (text[:200] + ("\n[+image OCR text detected]" if image_ocr_text else "")).strip()
        await log_action(
            self.bot, message.guild,
            "🚨 Scam Message Deleted",
            f"**User:** {message.author.mention} (`{message.author.id}`)\n"
            f"**Channel:** {message.channel.mention}\n"
            f"**Detected via:** {source_str}\n"
            f"**Content preview:** ```{preview[:300]}```",
        )

        # Auto-warn
        uid = str(message.author.id)
        gid = str(message.guild.id)
        # RACE FIX: locked RMW (same doc as /warn — parallel writers).
        with doc_lock('warnings.json'):
            warnings_data = load('warnings.json')
            warnings_data.setdefault(gid, {}).setdefault(uid, []).append({
                "reason": "Auto: Scam message detected and removed",
                "ts": int(time.time()),
                "mod": str(self.bot.user),
            })
            save('warnings.json', warnings_data)

        # Brief public notice (auto-deletes after 8s)
        try:
            notice = await message.channel.send(embed=discord.Embed(
                description=f"🚨 A message by {message.author.mention} was removed — **scam content detected**.\n"
                             f"Fake giveaway/crypto messages are not allowed here.",
                color=0xed4245,
            ))
            await asyncio.sleep(8)
            await notice.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        mc = get_mc(message.guild.id)

        # ── Anti-scam check ────────────────────────────────
        if await self._check_scam(message):
            return  # already deleted, no further processing

        # ── Invite blocker ─────────────────────────────────
        if mc.get('block_invites') and re.search(r"discord\.gg/\S+|discord\.com/invite/\S+", message.content, re.I):
            if not message.author.guild_permissions.administrator:
                try:
                    await rate_limit_wait("delete_message", message.channel.id)
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
                await log_action(self.bot, message.guild, "🔗 Invite Blocked",
                                  f"**User:** {message.author.mention}\n**Channel:** {message.channel.mention}")
                return

        # ── Word filter (banned_words) ──────────────────────
        if mc.get('automod_enabled'):
            raw_bw = mc.get('banned_words', "")
            banned = [w.strip().lower() for w in raw_bw.split(',') if w.strip()]
            low = message.content.lower()
            if any(bw in low for bw in banned):
                if not message.author.guild_permissions.administrator:
                    try:
                        await rate_limit_wait("delete_message", message.channel.id)
                        await message.delete()
                    except (discord.Forbidden, discord.NotFound):
                        pass
                    await log_action(self.bot, message.guild, "🤬 Banned Word Detected",
                                      f"**User:** {message.author.mention}\n**Channel:** {message.channel.mention}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # Auto-role only — welcome message is handled by cogs/welcomer.py
        cfg = load('config.json').get(str(member.guild.id), {})
        role_id = cfg.get('auto_role')
        if role_id:
            role = member.guild.get_role(int(role_id))
            if role:
                try:
                    await rate_limit_wait("add_guild_member_role", member.guild.id)
                    await member.add_roles(role)
                except (discord.Forbidden, discord.HTTPException):
                    pass

    # ── Bot Identity Customization ──────────────────────────────
    @commands.command(name="setbotavatar", help="Change the bot's Discord profile picture")
    @commands.has_permissions(manage_guild=True)
    async def setbotavatar(self, ctx: commands.Context, url: str = None):
        """Change the bot's avatar by uploading an image attachment or providing a direct image URL."""
        if ctx.message.attachments:
            url = ctx.message.attachments[0].url

        if not url:
            return await ctx.send(embed=err("Please attach an image or provide an image URL!\nExample: `!setbotavatar https://example.com/image.png`"))

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        return await ctx.send(embed=err(f"Failed to download image (HTTP {resp.status})."))
                    img_data = await resp.read()
            except Exception as e:
                return await ctx.send(embed=err(f"Error downloading image: {e}"))

        try:
            await self.bot.user.edit(avatar=img_data)
            await ctx.send(embed=ok("Bot Avatar Updated!", "The bot's profile picture has been changed successfully! ✨"))
        except discord.HTTPException as e:
            await ctx.send(embed=err(f"Discord API error: {e}\n*(Note: Discord limits avatar changes to 2 updates per 10 minutes)*"))
        except Exception as e:
            await ctx.send(embed=err(f"Failed to update avatar: {e}"))

    @commands.command(name="setbotname", help="Change the bot's username (Admin)")
    @commands.has_permissions(manage_guild=True)
    async def setbotname(self, ctx: commands.Context, *, new_name: str):
        """Change the bot's global username."""
        new_name = new_name.strip()
        if not new_name:
            return await ctx.send(embed=err("Please provide a new name for the bot!"))
        try:
            await self.bot.user.edit(username=new_name)
            await ctx.send(embed=ok("Bot Name Updated!", f"The bot's username has been changed to **{new_name}**! ✨"))
        except Exception as e:
            await ctx.send(embed=err(f"Failed to change username: {e}"))

async def setup(bot):
    await bot.add_cog(Moderation(bot))
