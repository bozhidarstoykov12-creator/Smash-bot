import discord
from discord.ext import commands
from discord import app_commands
import time
from utils import load, save, ok, err, info, doc_lock

INVITES_FILE = 'invites.json'


def _get_cfg(gid: str) -> dict:
    cfg = load('config.json').get(gid, {})
    inv = cfg.get('invites', {})
    return {
        'enabled':             inv.get('enabled', False),
        'log_channel':         inv.get('log_channel', ''),
        'log_message':         inv.get('log_message',
            "🎉 {user.mention} joined using **{inviter.name}**'s invite! They now have **{count}** invite(s)."),
        'fake_threshold_hours': inv.get('fake_threshold_hours', 24),
        'count_fake':          inv.get('count_fake', False),
    }


def _load() -> dict:
    return load(INVITES_FILE)


def _guild_bucket(data: dict, gid: str) -> dict:
    return data.setdefault(gid, {"inviters": {}, "members": {}})


def get_totals(gid: str, uid: str) -> dict:
    g = _load().get(gid, {}).get('inviters', {}).get(uid, {})
    regular = g.get('regular', 0)
    bonus = g.get('bonus', 0)
    left = g.get('left', 0)
    fake = g.get('fake', 0)
    total = regular + bonus - left
    return {'regular': regular, 'bonus': bonus, 'left': left, 'fake': fake, 'total': total, 'name': g.get('name')}


class Invites(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # gid -> {code: uses}
        self.invite_cache: dict[str, dict[str, int]] = {}

    async def _cache_guild_invites(self, guild: discord.Guild):
        gid = str(guild.id)
        try:
            invites = await guild.invites()
            self.invite_cache[gid] = {inv.code: (inv.uses or 0) for inv in invites}
        except discord.Forbidden:
            self.invite_cache[gid] = {}
        except Exception as e:
            print(f"[Invites] Could not cache invites for {guild.id}: {e}")
            self.invite_cache[gid] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._cache_guild_invites(guild)
        print(f"✅ Invite cache warmed for {len(self.bot.guilds)} guild(s)")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self._cache_guild_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        gid = str(invite.guild.id)
        self.invite_cache.setdefault(gid, {})[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        gid = str(invite.guild.id)
        self.invite_cache.get(gid, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        gid = str(guild.id)

        old_cache = dict(self.invite_cache.get(gid, {}))
        try:
            new_invites = await guild.invites()
        except discord.Forbidden:
            return  # Bot lacks Manage Server — can't attribute invites here.
        except Exception as e:
            print(f"[Invites] Could not fetch invites for {gid}: {e}")
            return

        new_cache = {inv.code: (inv.uses or 0) for inv in new_invites}
        used_invite = None
        for inv in new_invites:
            if (inv.uses or 0) > old_cache.get(inv.code, 0):
                used_invite = inv
                break
        self.invite_cache[gid] = new_cache

        inviter = used_invite.inviter if (used_invite and used_invite.inviter) else None
        code = used_invite.code if used_invite else None

        cfg = _get_cfg(gid)
        now = time.time()
        is_fake = False

        # RACE FIX (two-phase): joins can arrive in bursts (mass joins) and each
        # on_member_join is its own asyncio task — the whole counter update +
        # member record write must be one locked critical section, with the
        # announcement network call AFTER the lock.
        with doc_lock(INVITES_FILE):
            data = _load()
            g = _guild_bucket(data, gid)

            inv_id = None
            if inviter:
                is_fake = (now - member.created_at.timestamp()) < (cfg['fake_threshold_hours'] * 3600)
                inv_id = str(inviter.id)
                inv_entry = g['inviters'].setdefault(inv_id, {"name": inviter.name, "regular": 0, "left": 0, "fake": 0, "bonus": 0})
                inv_entry['name'] = inviter.name
                if is_fake and not cfg['count_fake']:
                    inv_entry['fake'] += 1
                else:
                    inv_entry['regular'] += 1

            g['members'][str(member.id)] = {
                "inviter_id": inv_id,
                "inviter_name": inviter.name if inviter else "Vanity/Unknown",
                "code": code,
                "joined_at": now,
                "left": False,
                "fake": is_fake,
            }
            save(INVITES_FILE, data)

        if cfg['enabled'] and cfg['log_channel'] and str(cfg['log_channel']).isdigit():
            channel = guild.get_channel(int(cfg['log_channel']))
            if channel:
                totals = get_totals(gid, inv_id) if inv_id else {'total': 0}
                inviter_name = inviter.name if inviter else "a Vanity URL / unknown invite"
                inviter_mention = inviter.mention if inviter else inviter_name
                fake_tag = " ⚠️ *(flagged as a likely fake/alt invite)*" if is_fake and inviter else ""
                msg = (cfg['log_message']
                       .replace('{user.mention}', member.mention)
                       .replace('{user.name}', member.name)
                       .replace('{inviter.mention}', inviter_mention)
                       .replace('{inviter.name}', inviter_name)
                       .replace('{count}', str(totals['total']))
                       .replace('{server.name}', guild.name))
                try:
                    await channel.send(msg + fake_tag)
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        gid = str(member.guild.id)
        # RACE FIX (two-phase): decide & save inside the lock, return outside.
        with doc_lock(INVITES_FILE):
            data = _load()
            g = _guild_bucket(data, gid)
            mem_entry = g['members'].get(str(member.id))
            skip = not mem_entry or mem_entry.get('left')
            if not skip:
                mem_entry['left'] = True
                inv_id = mem_entry.get('inviter_id')
                if inv_id and not mem_entry.get('fake'):
                    inv_entry = g['inviters'].setdefault(inv_id, {"name": "Unknown", "regular": 0, "left": 0, "fake": 0, "bonus": 0})
                    inv_entry['left'] += 1
                save(INVITES_FILE, data)
        if skip:
            return

    # ══════════════════════════════════════════════════════
    #  SLASH COMMANDS
    # ══════════════════════════════════════════════════════
    invites = app_commands.Group(name="invites", description="Invite tracker commands")

    @invites.command(name="stats", description="Check how many people someone has invited")
    @app_commands.describe(member="Whose invite stats to check (defaults to you)")
    async def stats(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        gid = str(interaction.guild.id)
        totals = get_totals(gid, str(member.id))

        em = info(f"📨 Invite Stats — {member.display_name}")
        em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(name="✅ Total (usable)", value=f"**{totals['total']}**", inline=True)
        em.add_field(name="Regular", value=str(totals['regular']), inline=True)
        em.add_field(name="Bonus", value=str(totals['bonus']), inline=True)
        em.add_field(name="Left after joining", value=str(totals['left']), inline=True)
        em.add_field(name="Fake / flagged", value=str(totals['fake']), inline=True)
        await interaction.response.send_message(embed=em)

    @invites.command(name="leaderboard", description="Show the top inviters in this server")
    async def leaderboard(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        inviters = _load().get(gid, {}).get('inviters', {})
        ranked = sorted(
            inviters.items(),
            key=lambda kv: kv[1].get('regular', 0) + kv[1].get('bonus', 0) - kv[1].get('left', 0),
            reverse=True
        )[:10]

        if not ranked:
            return await interaction.response.send_message(embed=info("📨 Invite Leaderboard", "No tracked invites yet."))

        medals = ['🥇', '🥈', '🥉']
        lines = []
        for i, (uid, e) in enumerate(ranked):
            total = e.get('regular', 0) + e.get('bonus', 0) - e.get('left', 0)
            rank = medals[i] if i < 3 else f"**{i+1}.**"
            lines.append(f"{rank} **{e.get('name', f'User {uid}')}** — {total} invite(s)")

        await interaction.response.send_message(embed=info("📨 Invite Leaderboard", "\n".join(lines)))

    @invites.command(name="add", description="Add bonus invites to a member")
    @app_commands.describe(member="Who to give bonus invites to", amount="How many bonus invites to add")
    @app_commands.default_permissions(manage_guild=True)
    async def add(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        gid = str(interaction.guild.id)
        # RACE FIX: locked RMW — parallel /invites add/remove and join/leave
        # events must not overwrite each other's counter updates.
        with doc_lock(INVITES_FILE):
            data = _load()
            g = _guild_bucket(data, gid)
            entry = g['inviters'].setdefault(str(member.id), {"name": member.name, "regular": 0, "left": 0, "fake": 0, "bonus": 0})
            entry['name'] = member.name
            entry['bonus'] = entry.get('bonus', 0) + amount
            save(INVITES_FILE, data)
            bonus_after = entry['bonus']
        totals = get_totals(gid, str(member.id))
        await interaction.response.send_message(embed=ok(f"Updated {member.display_name}'s invites", f"Bonus is now **{bonus_after}** — total: **{totals['total']}**"))

    @invites.command(name="remove", description="Remove bonus invites from a member")
    @app_commands.describe(member="Whose bonus invites to remove", amount="How many bonus invites to subtract")
    @app_commands.default_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        gid = str(interaction.guild.id)
        # RACE FIX: locked RMW — parallel /invites add/remove and join/leave
        # events must not overwrite each other's counter updates.
        with doc_lock(INVITES_FILE):
            data = _load()
            g = _guild_bucket(data, gid)
            entry = g['inviters'].setdefault(str(member.id), {"name": member.name, "regular": 0, "left": 0, "fake": 0, "bonus": 0})
            entry['name'] = member.name
            entry['bonus'] = entry.get('bonus', 0) - amount
            save(INVITES_FILE, data)
            bonus_after = entry['bonus']
        totals = get_totals(gid, str(member.id))
        await interaction.response.send_message(embed=ok(f"Updated {member.display_name}'s invites", f"Bonus is now **{bonus_after}** — total: **{totals['total']}**"))


async def setup(bot):
    await bot.add_cog(Invites(bot))
