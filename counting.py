import discord
import ast
import operator as op
from discord.ext import commands
from discord import app_commands
from utils import load, save, ok, err, info, medal, doc_lock
from cogs.economy import get_cfg as get_eco_cfg, add_balance, consume_item
from i18n import t

# ── SAFE MATH EXPRESSION PARSER ─────────────────────────
ALLOWED_OPERATORS = {
    ast.Add: op.add, 
    ast.Sub: op.sub, 
    ast.Mult: op.mul,
    ast.Div: op.truediv, 
    ast.Pow: op.pow, 
    ast.USub: op.neg,
    ast.UAdd: op.pos
}

def safe_math_eval(expr):
    """
    Safely evaluates math expressions without risk of code execution (RCE).
    Returns a number (int/float) or None if the expression is invalid or contains non-math text.
    """
    try:
        node = ast.parse(expr, mode='eval').body
        def _eval(n):
            if isinstance(n, ast.Constant): # Numbers (e.g. 5, 3.14)
                return n.value
            elif isinstance(n, ast.BinOp): # Binary operations (e.g. +, -, *, /, **)
                return ALLOWED_OPERATORS[type(n.op)](_eval(n.left), _eval(n.right))
            elif isinstance(n, ast.UnaryOp): # Unary operators (e.g. -5)
                return ALLOWED_OPERATORS[type(n.op)](_eval(n.operand))
            else:
                raise ValueError("Disallowed operation")
        return _eval(node)
    except:
        return None

# ── END OF PARSER ───────────────────────────────────────

COUNTING_DEFAULTS = {
    "channel": None, "enabled": False, "count": 0,
    "last_user": None, "high_score": 0,
    "allow_same_user": False, "shame_role": False,
    "shame_role_name": "💀 Count Ruiner",
    "delete_invalid": False,
    "goal": None, "user_stats": {}
}

def _patch_defaults(d: dict) -> dict:
    """Fill in any keys missing from old guild records."""
    for key, default in COUNTING_DEFAULTS.items():
        if key not in d:
            d[key] = default
    return d

def get_cd(gid):
    # RACE FIX: default-patching is a read-modify-write on a dashboard-shared
    # document — hold the lock across the whole cycle (RLock is re-entrant, so
    # callers already holding locked('counting.json') nest safely).
    with doc_lock('counting.json'):
        data = load('counting.json')
        gid = str(gid)
        if gid not in data:
            data[gid] = dict(COUNTING_DEFAULTS)
        else:
            _patch_defaults(data[gid])
        save('counting.json', data)
        return data, gid

class Counting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _confirm_count(self, msg, emoji, *, reply_fallback=False):
        """Add a confirmation reaction to a counting message.

        Some users block the bot. Discord then refuses to let the bot react
        to their messages, returning 403 with error code 90001 'Reaction
        blocked' — which is why those users never got the ✅ tick. We can't
        force a reaction in that case (it's a hard Discord limitation), so
        when `reply_fallback` is set we post a tiny, no-ping reply instead,
        so the counter still gets visible confirmation. Sending a channel
        message is allowed even when the author has blocked the bot.
        """
        try:
            await msg.add_reaction(emoji)
            return True
        except discord.HTTPException as exc:
            # 90001 = Reaction blocked (author blocked the bot);
            # Forbidden (50013) = missing Add Reactions permission.
            blocked = getattr(exc, 'code', None) == 90001 or isinstance(exc, discord.Forbidden)
        except Exception:
            return False
        if reply_fallback and blocked:
            try:
                await msg.reply(emoji, mention_author=False)
            except Exception:
                pass
        return False

    async def apply_shame(self, msg, d):
        if not d.get('shame_role'): return
        name = d.get('shame_role_name', '💀 Count Ruiner')
        role = discord.utils.get(msg.guild.roles, name=name)
        if not role:
            try: role = await msg.guild.create_role(name=name, color=discord.Color.dark_red())
            except: return
        try: await msg.author.add_roles(role)
        except: pass

    async def count_fail(self, msg, d, uid, title, desc):
        """Network side-effects for a counting fail.

        RACE FIX: all state mutations (high score, fail stat, count reset)
        are now committed to disk by the caller *inside* the locked phase
        (see on_message), BEFORE any await runs. This method only performs
        Discord API calls (reaction + embed + shame role) — safe to take
        arbitrarily long without corrupting the count.
        """
        gid = str(msg.guild.id)
        em = discord.Embed(title=f"\U0001F480 {title}", color=discord.Color.red())
        em.description = desc
        em.add_field(name=t(gid, 'count.reached'),    value=f"**{d['reached']}**")
        em.add_field(name=t(gid, 'count.high_score'), value=f"**{d['high_score']}**")
        em.set_footer(text=t(gid, 'count.next_is_one'))
        em.set_thumbnail(url=msg.author.display_avatar.url)
        try: await msg.add_reaction("\U0001F480")
        except: pass
        await msg.channel.send(embed=em)
        await self.apply_shame(msg, d)
    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.author.bot or not msg.guild: return
        gid = str(msg.guild.id)

        # ════════════════════════════════════════════════════════════════════
        # PHASE 1 — STATE (synchronous, atomic, ZERO awaits)
        # ════════════════════════════════════════════════════════════════════
        # discord.py dispatches every on_message as its own asyncio task, so
        # rapid counting (8, 9, 10, 11 within a second) used to run several
        # handlers in parallel against stale snapshots: load() at the top,
        # save() only at the very end — after awaits. Result: false
        # "Counted Twice in a Row!" verdicts and a wiped count even when
        # players counted perfectly (see sim_race.py for the reproduced
        # incident). The entire read-validate-mutate-write cycle now runs
        # under a shared document lock with no awaits, so every message sees
        # the *committed* state of the previous one.
        with doc_lock('counting.json'):
            data = load('counting.json')
            if gid not in data: return
            d = data[gid]

            if not d.get('enabled') or not d.get('channel'): return
            try:
                if msg.channel.id != int(d['channel']): return
            except ValueError: return

            uid = str(msg.author.id)
            _patch_defaults(d)

            delete_invalid = bool(d.get('delete_invalid', False))

            # Shame role check (sync role lookup, no API call)
            if d.get('shame_role', False):
                shame_name = d.get('shame_role_name', '💀 Count Ruiner')
                if discord.utils.get(msg.author.roles, name=shame_name):
                    # shamed user: not allowed to count (delete only if configured)
                    phase2 = ('delete',) if delete_invalid else None
                    # (no state change — no save needed)
                else:
                    phase2 = self._validate_and_commit(msg, d, uid, gid, delete_invalid)
            else:
                phase2 = self._validate_and_commit(msg, d, uid, gid, delete_invalid)

            # Single durable commit *inside* the lock, before any network I/O
            save('counting.json', data)

        # ════════════════════════════════════════════════════════════════════
        # PHASE 2 — NETWORK SIDE EFFECTS (awaits allowed; state untouched)
        # ════════════════════════════════════════════════════════════════════
        if not phase2: return
        action = phase2[0]

        if action == 'delete':
            try: await msg.delete()
            except: pass
            return

        if action == 'save_used':
            try: await msg.add_reaction("🛡️")
            except: pass
            em = discord.Embed(title=t(gid, 'count.save_used_title'), color=discord.Color.green())
            em.description = t(gid, 'count.save_used_desc', mention=msg.author.mention, title=phase2[1])
            em.set_footer(text=t(gid, 'count.save_footer'))
            await msg.channel.send(embed=em)
            return

        if action == 'fail':
            kind, title, desc, reached, high_score = phase2[1], phase2[2], phase2[3], phase2[4], phase2[5]
            if delete_invalid:
                try: await msg.delete()
                except: pass
            await self.count_fail(msg, {'reached': reached, 'high_score': high_score,
                                         'shame_role': d.get('shame_role'),
                                         'shame_role_name': d.get('shame_role_name')}, uid, title, desc)
            return

        if action == 'ok':
            number = phase2[1]
            eco_cfg = get_eco_cfg(gid)  # read-only view (sync)
            if eco_cfg.get('earn_counting'):
                add_balance(gid, uid, eco_cfg['earn_counting'], name=msg.author.name)

            if number == d.get('goal'):
                try: await msg.add_reaction("🏆")
                except: pass
                await msg.channel.send(embed=discord.Embed(title=t(gid, 'count.goal_title'), description=t(gid, 'count.goal_desc', number=number), color=discord.Color.gold()))
            elif number % 100 == 0:
                try: await msg.add_reaction("🎉")
                except: pass
                await msg.channel.send(embed=discord.Embed(title=t(gid, 'count.milestone_title', number=number), description=t(gid, 'count.milestone_desc', number=number), color=discord.Color.gold()))
            elif number % 50 == 0:
                await self._confirm_count(msg, "🔥", reply_fallback=True)
            else:
                await self._confirm_count(msg, "✅", reply_fallback=True)

    # ── Synchronous validation + state commit, called ONLY under doc_lock ──
    # Returns a phase-2 instruction tuple (or None for "ignore silently").
    # NEVER awaits anything — that is the entire point.
    def _validate_and_commit(self, msg, d, uid, gid, delete_invalid):
        # Strip spaces to help the math parser ("2 + 3" -> "2+3")
        content = msg.content.strip().replace(' ', '')
        result = safe_math_eval(content)

        # None = plain text / malformed math — not a counting attempt
        if result is None:
            return ('delete',) if delete_invalid else None

        # Cast to int (divisions like 4/2 return 2.0); reject 1.8, 2.5...
        try:
            number = int(result)
            if result != number:
                return ('delete',) if delete_invalid else None
        except (ValueError, TypeError, OverflowError):
            return ('delete',) if delete_invalid else None

        d['user_stats'].setdefault(uid, {'correct': 0, 'fails': 0})
        expected = d['count'] + 1

        # Same user twice in a row?
        if not d.get('allow_same_user', False) and d['last_user'] == uid:
            title = t(gid, 'count.twice_title')
            # "Counting Save" economy item absorbs one fail (sync call)
            if consume_item(gid, uid, 'count_save'):
                d['last_user'] = None  # the same user may count again next
                return ('save_used', title)
            reached = d['count']
            if reached > d['high_score']: d['high_score'] = reached
            d['user_stats'][uid]['fails'] += 1
            d['count'] = 0
            d['last_user'] = None
            desc = t(gid, 'count.twice_desc', mention=msg.author.mention)
            return ('fail', 'twice', title, desc, reached, d['high_score'])

        # Wrong number?
        if number != expected:
            title = t(gid, 'count.wrong_title')
            if consume_item(gid, uid, 'count_save'):
                d['last_user'] = None
                return ('save_used', title)
            reached = d['count']
            if reached > d['high_score']: d['high_score'] = reached
            d['user_stats'][uid]['fails'] += 1
            d['count'] = 0
            d['last_user'] = None
            desc = t(gid, 'count.wrong_desc', mention=msg.author.mention,
                     content=msg.content, number=number, expected=expected)
            return ('fail', 'wrong', title, desc, reached, d['high_score'])

        # All checks passed — commit the new count immediately
        d['count'] = number
        d['last_user'] = uid
        d['user_stats'][uid]['correct'] += 1
        if number > d['high_score']: d['high_score'] = number
        return ('ok', number)

    # ══════════════════════════════════════════════════════
    #  SLASH COMMANDS
    # ══════════════════════════════════════════════════════
    counting = app_commands.Group(name="counting", description="Counting game commands")

    @counting.command(name="setchannel", description="Set the counting channel")
    @app_commands.describe(channel="The channel for counting")
    @app_commands.default_permissions(manage_channels=True)
    async def setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        # RACE FIX: atomic RMW under the shared doc lock (dashboard writes
        # counting.json from Flask threads).
        with doc_lock('counting.json'):
            data, gid = get_cd(interaction.guild.id)
            data[gid]['channel'] = channel.id
            data[gid]['enabled'] = True
            save('counting.json', data)
        await interaction.response.send_message(embed=ok("Counting Channel Set!", f"Counting in {channel.mention} — now **enabled**!"))

    @counting.command(name="enable", description="Enable the counting game")
    @app_commands.default_permissions(manage_channels=True)
    async def enable(self, interaction: discord.Interaction):
        # RACE FIX: two-phase — decide & save inside the lock, message outside.
        with doc_lock('counting.json'):
            data, gid = get_cd(interaction.guild.id)
            has_channel = bool(data[gid]['channel'])
            if has_channel:
                data[gid]['enabled'] = True
                save('counting.json', data)
        if not has_channel:
            return await interaction.response.send_message(embed=err("Set a channel first: `/counting setchannel`"))
        await interaction.response.send_message(embed=ok("Counting Enabled!"))

    @counting.command(name="disable", description="Disable the counting game")
    @app_commands.default_permissions(manage_channels=True)
    async def disable(self, interaction: discord.Interaction):
        with doc_lock('counting.json'):
            data, gid = get_cd(interaction.guild.id)
            data[gid]['enabled'] = False
            save('counting.json', data)
        await interaction.response.send_message(embed=ok("Counting Disabled."))

    @counting.command(name="shamerole", description="Manage the shame role")
    @app_commands.describe(action="on / off / name / remove", name="New name for the role (if action=name)")
    @app_commands.default_permissions(manage_roles=True)
    async def shamerole(self, interaction: discord.Interaction, action: str, name: str = None):
        # RACE FIX (two-phase): all document mutations happen inside ONE locked
        # critical section with zero awaits; Discord network calls happen after.
        with doc_lock('counting.json'):
            data, gid = get_cd(interaction.guild.id)
            resp = None  # (kind, text) decided inside the lock
            if action == 'on':
                data[gid]['shame_role'] = True
                save('counting.json', data)
                resp = ('ok', f"Shame Role On!|Ruiners get **{data[gid]['shame_role_name']}** and will be muted from counting!")
            elif action == 'off':
                data[gid]['shame_role'] = False
                save('counting.json', data)
                resp = ('ok', "Shame Role Off.|")
            elif action == 'name':
                if not name:
                    resp = ('err', "Provide a name!")
                else:
                    data[gid]['shame_role_name'] = name
                    save('counting.json', data)
                    resp = ('ok', f"Renamed!|Now called **{name}**")
            elif action == 'remove':
                resp = ('ok', 'STAGE_REMOVE')
            else:
                resp = ('err', "Use: `on`, `off`, `name`, `remove`")

        if resp[0] == 'err':
            return await interaction.response.send_message(embed=err(resp[1]))
        if resp[1] == 'STAGE_REMOVE':
            # Network phase (outside the lock): find the role and strip it.
            role = discord.utils.get(interaction.guild.roles, name=data[gid]['shame_role_name'])
            if role:
                count = 0
                for member in interaction.guild.members:
                    if role in member.roles:
                        await member.remove_roles(role)
                        count += 1
                return await interaction.response.send_message(embed=ok("Cleared!", f"Removed from **{count}** members."))
            return await interaction.response.send_message(embed=err("Role not found!"))
        title, _, desc = resp[1].partition('|')
        await interaction.response.send_message(embed=ok(title, desc or None))

    @counting.command(name="allowsameuser", description="Allow the same user to count twice in a row")
    @app_commands.describe(toggle="on or off")
    @app_commands.default_permissions(manage_channels=True)
    async def allowsameuser(self, interaction: discord.Interaction, toggle: str):
        # RACE FIX: atomic RMW under the shared doc lock.
        with doc_lock('counting.json'):
            data, gid = get_cd(interaction.guild.id)
            val = toggle.lower() in ('on','true','yes')
            data[gid]['allow_same_user'] = val
            save('counting.json', data)
        await interaction.response.send_message(embed=ok(f"Same User {'Allowed ✅' if val else 'Blocked ❌'}"))

    @counting.command(name="goal", description="Set a counting goal")
    @app_commands.describe(number="The goal number")
    @app_commands.default_permissions(manage_channels=True)
    async def goal(self, interaction: discord.Interaction, number: int):
        if number < 1: return await interaction.response.send_message(embed=err("Goal must be positive!"))
        with doc_lock('counting.json'):
            data, gid = get_cd(interaction.guild.id)
            data[gid]['goal'] = number
            save('counting.json', data)
        await interaction.response.send_message(embed=ok(f"Goal Set: {number}!", "The server will celebrate when you reach it! 🎉"))

    @counting.command(name="reset", description="Reset the count to 0")
    @app_commands.default_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction):
        # RACE FIX: read old value AND write inside one locked critical
        # section — a concurrent counting message can't interleave.
        with doc_lock('counting.json'):
            data, gid = get_cd(interaction.guild.id)
            old = data[gid]['count']
            data[gid]['count'] = 0
            data[gid]['last_user'] = None
            save('counting.json', data)
        await interaction.response.send_message(embed=ok("Count Reset!", f"Reset from **{old}** to **0**."))

    @counting.command(name="info", description="Show counting info")
    async def c_info(self, interaction: discord.Interaction):
        data, gid = get_cd(interaction.guild.id)
        d = data[gid]
        ch = self.bot.get_channel(int(d['channel'])) if d['channel'] else None
        em = discord.Embed(title="🔢 Counting Info", color=discord.Color.blue())
        em.add_field(name="📍 Channel",    value=ch.mention if ch else "Not set")
        em.add_field(name="🔢 Current",    value=f"**{d['count']}**")
        em.add_field(name="🏆 High Score", value=f"**{d['high_score']}**")
        em.add_field(name="🎯 Goal",       value=f"**{d['goal']}**" if d['goal'] else "None")
        em.add_field(name="✅ Enabled",    value="Yes" if d['enabled'] else "No")
        em.add_field(name="🧹 Clean Chat",  value="Yes (Delete Invalid)" if d.get('delete_invalid') else "No")
        em.add_field(name="💀 Shame Role", value=f"On ({d['shame_role_name']})" if d['shame_role'] else "Off")
        await interaction.response.send_message(embed=em)

    @counting.command(name="stats", description="View counting stats for a user")
    @app_commands.describe(member="Who to check")
    async def c_stats(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        data, gid = get_cd(interaction.guild.id)
        s = data[gid]['user_stats'].get(str(member.id), {'correct': 0, 'fails': 0})
        total = s['correct'] + s['fails']
        acc = round((s['correct'] / total) * 100) if total > 0 else 0
        em = discord.Embed(title=f"📊 Counting Stats — {member.display_name}", color=discord.Color.purple())
        em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(name="✅ Correct",  value=s['correct'])
        em.add_field(name="💀 Fails",    value=s['fails'])
        em.add_field(name="🎯 Accuracy", value=f"{acc}%")
        await interaction.response.send_message(embed=em)

    @counting.command(name="lb", description="Counting leaderboard")
    async def c_lb(self, interaction: discord.Interaction):
        data, gid = get_cd(interaction.guild.id)
        stats = data[gid]['user_stats']
        if not stats: return await interaction.response.send_message(embed=err("No data yet!"))
        sorted_users = sorted(stats.items(), key=lambda x: x[1]['correct'], reverse=True)[:10]
        em = discord.Embed(title="🏆 Counting Leaderboard", color=discord.Color.gold())
        desc = ""
        for i, (uid, s) in enumerate(sorted_users):
            user = self.bot.get_user(int(uid))
            name = user.display_name if user else f"User …{uid[-4:]}"
            desc += f"{medal(i)} **{name}** — {s['correct']} ✅ | {s['fails']} 💀\n"
        em.description = desc
        await interaction.response.send_message(embed=em)

    @counting.command(name="fails", description="Hall of shame — who ruined the count the most")
    async def c_fails(self, interaction: discord.Interaction):
        data, gid = get_cd(interaction.guild.id)
        stats = data[gid]['user_stats']
        if not stats: return await interaction.response.send_message(embed=err("No data yet!"))
        sorted_users = sorted(stats.items(), key=lambda x: x[1]['fails'], reverse=True)[:10]
        em = discord.Embed(title="💀 Hall of Shame", color=discord.Color.red())
        desc = ""
        emojis = ['💀','☠️','🩻']
        for i, (uid, s) in enumerate(sorted_users):
            user = self.bot.get_user(int(uid))
            name = user.display_name if user else f"User …{uid[-4:]}"
            e = emojis[i] if i < 3 else f"**{i+1}.**"
            desc += f"{e} **{name}** — {s['fails']} ruin(s)\n"
        em.description = desc
        await interaction.response.send_message(embed=em)

async def setup(bot):
    await bot.add_cog(Counting(bot))