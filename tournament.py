import discord
from discord.ext import commands, tasks
import os
import random
import math
import time
from datetime import datetime, timezone
from utils import load, save, ok, err, rate_limit_wait, doc_lock, yield_to_user_facing

# ── Server-wide tournament settings ─────────────────────────────────────────
TOURNAMENT_SETTINGS_DEFAULTS = {
    'ref_role_id':        None,
    'results_channel_id': None,
    'bracket_channel_id': None,
    'threads_channel_id': None,
    'threads_enabled':    False,
}

def get_tournament_settings(gid: str) -> dict:
    # RACE FIX: default-patching is a read-modify-write on a dashboard-shared
    # document — hold the lock for the whole cycle.
    with doc_lock('tournament_settings.json'):
        data = load('tournament_settings.json')
        gid = str(gid)
        if gid not in data:
            data[gid] = dict(TOURNAMENT_SETTINGS_DEFAULTS)
            save('tournament_settings.json', data)
        else:
            changed = False
            for k, v in TOURNAMENT_SETTINGS_DEFAULTS.items():
                if k not in data[gid]:
                    data[gid][k] = v
                    changed = True
            if changed:
                save('tournament_settings.json', data)
        return data[gid]

def save_tournament_settings(gid: str, settings: dict):
    # RACE FIX: shared with the Flask dashboard (api_tournaments_settings_save).
    with doc_lock('tournament_settings.json'):
        data = load('tournament_settings.json')
        data[str(gid)] = settings
        save('tournament_settings.json', data)

# ── Smash Karts Arenas, Weapons & Modifiers ──────────────────────────────────
STANDARD_MAPS = [
    "The Battle Box", "Skate City", "Kartuga", "Toxic Towers", "The Sand Box",
    "Boneyard Basin", "SnowparkCTF", "Skate Park", "Snow Shrine", "Snowpark",
    "Space Stations", "Space Station", "Lava Pit CTF", "Lava Pit",
    "Graveyard CTF", "Graveyard", "Smash Fort CTF",
    "Sky Arena: Temples", "Sky Arena: Tunnels", "Sky Arena: Showdown",
    "Sky Arena: Pinball", "Sky Arena: Dropzone", "Smash Fort",
    "Slick 'n Slide", "The Old Graveyard", "Steky's Speedway",
    "The Gravel Pit", "Smash Island",
]

CTF_MAPS = [
    "The Battle Box", "Skate City", "Toxic Towers", "SnowparkCTF",
    "Space Stations", "Lava Pit CTF", "Graveyard CTF", "Smash Fort CTF",
    "Sky Arena: Temples", "Sky Arena: Tunnels", "Sky Arena: Showdown",
    "Smash Island",
]

ALL_MAPS = sorted(set(STANDARD_MAPS) | set(CTF_MAPS))

ALL_WEAPONS = [
    "Rockets", "Invincibility", "Machine Gun", "Mines", "Fake Lootbox",
    "Nuke", "Bullets", "Cannons", "Lob-Grenade", "Spikys", "Snowball Blaster",
]

ALL_MODIFIERS = ["Tiny Kart", "Hardcore"]
TEAM_SIZES = ["1v1", "2v2", "3v3", "4v4", "5v5"]
SCORE_TARGETS = [1, 6, 10, 20]
MAP_IMAGE_URLS: dict[str, str] = {}

PREDICTIONS_FILE = 'predictions.json'

def _pred_get(gid: str, name: str):
    data = load(PREDICTIONS_FILE)
    g = data.setdefault(str(gid), {})
    t = g.setdefault(name, {'enabled': True, 'locked': False, 'picks': {}})
    return data, t

def _pred_save(data: dict):
    # RACE FIX: predictions are edited from dashboard Flask threads too.
    with doc_lock(PREDICTIONS_FILE):
        save(PREDICTIONS_FILE, data)

def _pred_autolock(gid: str, name: str):
    """Locks predictions as soon as the first match result is reported."""
    data, pred = _pred_get(gid, name)
    if not pred.get('locked'):
        pred['locked'] = True
        _pred_save(data)

# ── Migration & Data Layer ───────────────────────────────────────────────────

def _migrate_tournament(t: dict) -> dict:
    if 'entries' in t:
        t.setdefault('max_subs', 1)
        for e in t.get('entries', []):
            e.setdefault('subs', [])
            e.setdefault('region', '')
            e.setdefault('role_id', None)
        t.setdefault('scores', {})
        t.setdefault('forfeits', {})
        return t

    old_players = t.pop('players', [])
    t['entries'] = [{'team_name': p['name'], 'members': [p], 'subs': [], 'region': '', 'role_id': None} for p in old_players]
    t['max_entries'] = t.pop('max_players', 8)
    t.setdefault('team_size', 1)
    t.setdefault('max_subs', 1)
    t.setdefault('bracket_type', 'single_elim')
    t.setdefault('mode', 'ffa')
    t.setdefault('score_target', 10)
    t.setdefault('time_limit', 10)
    t.setdefault('captures_to_win', 3)
    t.setdefault('map_pool', [])
    t.setdefault('bots_enabled', False)
    t.setdefault('weapons_enabled', True)
    t.setdefault('weapons', list(ALL_WEAPONS))
    t.setdefault('modifiers', [])
    t.setdefault('match_maps', {})
    t.setdefault('standings', {})
    t.setdefault('repeat_every_days', 0)
    t.setdefault('next_run_at', None)
    t.setdefault('repeat_spawned', False)
    t.setdefault('ended_at', None)
    t.setdefault('match_threads', {})
    t.setdefault('bracket_message_id', None)
    t.setdefault('scores', {})
    t.setdefault('forfeits', {})
    return t

def get_tournaments(gid: str) -> dict:
    tours = load('tournaments.json').get(str(gid), {})
    changed = False
    for name, t in tours.items():
        before = dict(t)
        t = _migrate_tournament(t)
        tours[name] = t
        if t != before:
            changed = True
    if changed:
        save_tournaments(gid, tours)
    return tours

def save_tournaments(gid: str, data: dict):
    # RACE FIX: shared with the Flask dashboard (tournament endpoints).
    with doc_lock('tournaments.json'):
        all_data = load('tournaments.json')
        all_data[str(gid)] = data
        save('tournaments.json', all_data)

def get_active(gid: str) -> tuple[str | None, dict | None]:
    tours = get_tournaments(gid)
    for name, t in tours.items():
        if t.get('status') in ('open', 'running'):
            return name, t
    return None, None

def find_entry(entries: list[dict], team_name: str) -> dict | None:
    team_name_lower = team_name.lower()
    return next((e for e in entries if e['team_name'].lower() == team_name_lower), None)

def find_entry_by_member(entries: list[dict], uid: str) -> dict | None:
    for e in entries:
        if any(m['uid'] == str(uid) for m in e['members']):
            return e
    return None

def find_entry_by_sub(entries: list[dict], uid: str) -> dict | None:
    for e in entries:
        if any(s['uid'] == str(uid) for s in e.get('subs', [])):
            return e
    return None

def _mem_mention(m: dict) -> str:
    """Render a roster member: a real Discord mention when we resolved their
    user ID, otherwise the plain name they were registered under."""
    uid = m.get('uid')
    if uid:
        return f"<@{uid}>"
    return m.get('name') or "Unknown"


def entry_label(e: dict | None) -> str:
    if not e:
        return "*TBD*"
    if len(e['members']) == 1:
        m = e['members'][0]
        return f"{m['name']} ({_mem_mention(m)})"
    mentions = ", ".join(_mem_mention(m) for m in e['members'])
    return f"**{e['team_name']}** ({mentions})"

def entry_is_full(e: dict, team_size: int) -> bool:
    return len(e['members']) >= team_size

def make_bracket(entries: list[dict]) -> list[list[tuple]]:
    shuffled = entries[:]
    random.shuffle(shuffled)
    size = 2 ** math.ceil(math.log2(max(len(shuffled), 2)))
    padded = shuffled + [None] * (size - len(shuffled))

    rounds = []
    current = [(padded[i], padded[i + 1]) for i in range(0, len(padded), 2)]
    rounds.append(current)

    while len(current) > 1:
        current = [(None, None)] * (len(current) // 2)
        rounds.append(current)

    return rounds

def _settings_summary(t: dict) -> str:
    mode_label = "🚩 Capture The Flag" if t.get('mode') == 'ctf' else "🎯 Free For All"
    team_sz = t.get('team_size', 1)
    max_s = t.get('max_subs', 1)
    parts = [f"**Format:** {team_sz}v{team_sz} (Max {max_s} Sub)", f"**Mode:** {mode_label}"]
    if t.get('mode') == 'ctf':
        parts.append(f"**Time Limit:** {t.get('time_limit', 10)} min")
        parts.append(f"**Captures to Win:** {t.get('captures_to_win', 3)}")
    else:
        parts.append(f"**Score Target:** {t.get('score_target', 10)}")
    pool = t.get('map_pool', [])
    parts.append(f"**Map Pool:** {', '.join(pool) if pool else '*Any map*'}")

    weapons = t.get('weapons', list(ALL_WEAPONS))
    if not t.get('weapons_enabled', True) or not weapons:
        parts.append("**Weapons:** Off (Racing Only)")
    elif len(weapons) == len(ALL_WEAPONS):
        parts.append("**Weapons:** All On")
    else:
        parts.append(f"**Weapons:** {', '.join(weapons)}")

    extras = []
    if t.get('bots_enabled'):
        extras.append("Bots Allowed")
    extras.extend(t.get('modifiers', []))
    if extras:
        parts.append(f"**Modifiers:** {', '.join(extras)}")
    return "\n".join(parts)

def bracket_embed(name: str, t: dict) -> discord.Embed:
    rounds = t.get('bracket', [])
    status = t.get('status', 'open')
    color = {'open': discord.Color.blurple(),
             'running': discord.Color.gold(),
             'done': discord.Color.green()}.get(status, discord.Color.default())

    em = discord.Embed(
        title=f"🏎️ Smash Karts Tournament — {name}",
        color=color,
        timestamp=datetime.now(timezone.utc)
    )

    if not rounds:
        entries = t.get('entries', [])
        team_size = t.get('team_size', 1)
        roster = []
        for e in entries:
            full_tag = "✅" if entry_is_full(e, team_size) else f"⏳ {len(e['members'])}/{team_size}"
            sub_count = len(e.get('subs', []))
            sub_tag = f" (+{sub_count} sub)" if sub_count > 0 else ""
            roster.append(f"• **{e['team_name']}** {full_tag}{sub_tag}")
        em.description = (
            f"**Status:** 🟢 Registration Open\n"
            f"**Entries:** {len(entries)}/{t.get('max_entries', 8)}\n\n"
            f"{_settings_summary(t)}\n\n"
            + ("\n".join(roster) if roster else "*No entries yet — use buttons below or `!tjoin` to enter!*")
        )
        return em

    round_names = ["Round of " + str(len(rounds[0]) * 2),
                   "Quarter-Finals", "Semi-Finals", "Final", "Champion"]

    lines = [_settings_summary(t), ""]
    match_maps = t.get('match_maps', {})
    scores = t.get('scores', {})
    forfeits = t.get('forfeits', {})

    for r_idx, matches in enumerate(rounds):
        r_name = round_names[r_idx] if r_idx < len(round_names) else f"Round {r_idx + 1}"
        lines.append(f"**── {r_name} ──**")
        for m_idx, (p1, p2) in enumerate(matches):
            key = f"{r_idx}-{m_idx}"
            winner_uid_key = t.get('results', {}).get(key)
            match_score = scores.get(key, {}).get('score', '')
            forfeit_reason = forfeits.get(key)
            score_str = f" `[{match_score}]`" if match_score else ""
            forfeit_str = f" *(Forfeit: {forfeit_reason})*" if forfeit_reason else ""
            map_tag = f" *(🗺️ {match_maps[key]})*" if key in match_maps else ""

            if p1 is None and p2 is None:
                lines.append(f"  `Match {m_idx+1}:` *TBD*")
            elif p2 is None:
                lines.append(f"  `Match {m_idx+1}:` **{p1['team_name']}** ← BYE ✅")
            else:
                if winner_uid_key:
                    loser = p2 if winner_uid_key == p1['team_name'] else p1
                    winner = p1 if winner_uid_key == p1['team_name'] else p2
                    lines.append(f"  `Match {m_idx+1}:` ~~{loser['team_name']}~~ vs **{winner['team_name']}**{score_str}{forfeit_str} 🏆")
                else:
                    lines.append(f"  `Match {m_idx+1}:` {p1['team_name']} vs {p2['team_name']}{map_tag}")
        lines.append("")

    em.description = "\n".join(lines)

    if status == 'done':
        champion = t.get('champion')
        if champion:
            em.add_field(name="🏆 Champion", value=f"{entry_label(champion)} 🎉", inline=False)

    em.set_footer(text=f"{len(t.get('entries', []))} entries · Use /tresult to report match winners")
    return em

def advance_bracket(t: dict) -> dict:
    rounds = t.setdefault('bracket', [])
    results = t.setdefault('results', {})

    for r_idx in range(len(rounds) - 1):
        matches = rounds[r_idx]
        winners_this_round = []
        all_done = True
        for m_idx, (p1, p2) in enumerate(matches):
            key = f"{r_idx}-{m_idx}"
            w_name = results.get(key)
            if p2 is None and p1 is not None:
                if key not in results:
                    results[key] = p1['team_name']
                w_name = p1['team_name']
            if w_name:
                winner = p1 if p1 and p1['team_name'] == w_name else p2
                winners_this_round.append(winner)
            else:
                all_done = False

        if all_done and winners_this_round:
            rounds[r_idx + 1] = [
                (winners_this_round[i], winners_this_round[i + 1] if i + 1 < len(winners_this_round) else None)
                for i in range(0, len(winners_this_round), 2)
            ]

    if len(rounds) > 0:
        final_r = len(rounds) - 1
        final_matches = rounds[final_r]
        if len(final_matches) == 1:
            key = f"{final_r}-0"
            if key in results:
                champ_name = results[key]
                p1, p2 = final_matches[0]
                champion = p1 if p1 and p1['team_name'] == champ_name else p2
                t['champion'] = champion
                t['status'] = 'done'

    t['bracket'] = rounds
    t['results'] = results
    return t

def _assign_match_maps(t: dict, round_idx: int):
    pool = t.get('map_pool') or (CTF_MAPS if t.get('mode') == 'ctf' else STANDARD_MAPS)
    match_maps = t.setdefault('match_maps', {})
    rounds = t.get('bracket', [])
    if round_idx >= len(rounds):
        return
    for m_idx, (p1, p2) in enumerate(rounds[round_idx]):
        key = f"{round_idx}-{m_idx}"
        if p1 and p2 and key not in match_maps and pool:
            match_maps[key] = random.choice(pool)

async def announce_round_matches(bot: discord.Client, channel: discord.abc.Messageable,
                                  name: str, t: dict, round_idx: int):
    rounds = t.get('bracket', [])
    if round_idx >= len(rounds):
        return
    _assign_match_maps(t, round_idx)
    match_maps = t.get('match_maps', {})

    matches = rounds[round_idx]
    lines = [f"📢 **{name} — Round {round_idx + 1} matchups are live!**", _settings_summary(t), ""]
    any_ready = False
    for m_idx, (p1, p2) in enumerate(matches):
        if p1 and p2:
            any_ready = True
            key = f"{round_idx}-{m_idx}"
            map_tag = f" — 🗺️ **{match_maps[key]}**" if key in match_maps else ""
            p1_mentions = " ".join(_mem_mention(m) for m in p1['members'])
            p2_mentions = " ".join(_mem_mention(m) for m in p2['members'])
            lines.append(f"`Match {m_idx+1}:` **{p1['team_name']}** ({p1_mentions}) 🆚 **{p2['team_name']}** ({p2_mentions}){map_tag}")
    if any_ready:
        await channel.send("\n".join(lines))

    guild = getattr(channel, 'guild', None)
    if guild:
        await create_match_threads(bot, guild, name, t, round_idx)
        await sync_bracket_channel(bot, guild, name, t)

async def _is_ref_or_admin(ctx: commands.Context) -> bool:
    if ctx.author.guild_permissions.manage_guild:
        return True
    settings = get_tournament_settings(str(ctx.guild.id))
    role_id = settings.get('ref_role_id')
    if role_id and any(str(r.id) == str(role_id) for r in getattr(ctx.author, 'roles', [])):
        return True
    return False

def mark_tournament_finished(t: dict):
    now = int(time.time())
    t['ended_at'] = now
    if t.get('repeat_every_days', 0) > 0:
        t['next_run_at'] = now + t['repeat_every_days'] * 86400

def _next_recurring_name(tours: dict, base_name: str) -> str:
    import re
    m = re.match(r'^(.*) #(\d+)$', base_name)
    root, n = (m.group(1), int(m.group(2)) + 1) if m else (base_name, 2)
    candidate = f"{root} #{n}"
    while candidate in tours:
        n += 1
        candidate = f"{root} #{n}"
    return candidate

def _clone_tournament_for_repeat(old_t: dict, host_id: int) -> dict:
    return {
        'status':            'open',
        'max_entries':       old_t.get('max_entries', 8),
        'team_size':         old_t.get('team_size', 1),
        'max_subs':          old_t.get('max_subs', 1),
        'mode':              old_t.get('mode', 'ffa'),
        'score_target':      old_t.get('score_target', 10),
        'time_limit':        old_t.get('time_limit', 10),
        'captures_to_win':   old_t.get('captures_to_win', 3),
        'map_pool':          list(old_t.get('map_pool', [])),
        'bots_enabled':      old_t.get('bots_enabled', False),
        'weapons_enabled':   old_t.get('weapons_enabled', True),
        'weapons':           list(old_t.get('weapons', ALL_WEAPONS)),
        'modifiers':         list(old_t.get('modifiers', [])),
        'repeat_every_days': old_t.get('repeat_every_days', 0),
        'entries':           [],
        'bracket':           [],
        'results':           {},
        'scores':            {},
        'forfeits':          {},
        'match_maps':        {},
        'match_threads':     {},
        'bracket_message_id': None,
        'standings':         {},
        'champion':          None,
        'host_id':           old_t.get('host_id', host_id),
        'created_at':        int(time.time()),
        'next_run_at':       None,
        'repeat_spawned':    False,
        'ended_at':          None,
    }

async def post_match_result_announcement(bot: discord.Client, guild: discord.Guild, name: str, t: dict,
                                          winning_team: dict, losing_team: dict, r_idx: int, m_idx: int,
                                          score: str = "", reason: str = ""):
    settings = get_tournament_settings(str(guild.id))
    channel_id = settings.get('results_channel_id')
    if not channel_id:
        return
    try:
        channel = guild.get_channel(int(channel_id)) or bot.get_channel(int(channel_id))
        if not channel:
            return
        score_desc = f" *(Score: {score})*" if score else ""
        reason_desc = f"\n⚠️ **Reason:** {reason}" if reason else ""
        em = discord.Embed(
            title=f"✅ Match Result — {name}",
            description=f"**Round {r_idx + 1}, Match {m_idx + 1}**\n\n🏆 **{winning_team['team_name']}** defeated **{losing_team['team_name']}**{score_desc}{reason_desc}",
            color=discord.Color.green(),
        )
        if t.get('status') == 'done' and t.get('champion'):
            em.add_field(name="👑 Champion", value=entry_label(t['champion']), inline=False)
        await rate_limit_wait("send_message", channel.id)
        await channel.send(embed=em)
    except Exception as e:
        print(f"[Tournament] Results channel post failed: {e}")

async def sync_bracket_channel(bot: discord.Client, guild: discord.Guild, name: str, t: dict):
    settings = get_tournament_settings(str(guild.id))
    channel_id = settings.get('bracket_channel_id')
    if not channel_id:
        return
    try:
        channel = guild.get_channel(int(channel_id)) or bot.get_channel(int(channel_id))
        if not channel:
            return
        em = bracket_embed(name, t)
        msg_id = t.get('bracket_message_id')
        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(embed=em)
                return
            except (discord.NotFound, discord.Forbidden):
                pass
        await rate_limit_wait("send_message", channel.id)
        new_msg = await channel.send(embed=em)
        t['bracket_message_id'] = new_msg.id
    except Exception as e:
        print(f"[Tournament] Bracket channel sync failed: {e}")

async def create_match_threads(bot: discord.Client, guild: discord.Guild, name: str, t: dict, round_idx: int):
    settings = get_tournament_settings(str(guild.id))
    if not settings.get('threads_enabled') or not settings.get('threads_channel_id'):
        return
    channel_id = settings['threads_channel_id']
    rounds = t.get('bracket', [])
    if round_idx >= len(rounds):
        return
    try:
        channel = guild.get_channel(int(channel_id)) or bot.get_channel(int(channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            return
    except Exception:
        return

    match_threads = t.setdefault('match_threads', {})
    for m_idx, (p1, p2) in enumerate(rounds[round_idx]):
        if not (p1 and p2):
            continue
        key = f"{round_idx}-{m_idx}"
        if key in match_threads:
            continue
        try:
            await rate_limit_wait("send_message", channel.id)
            thread = await channel.create_thread(
                name=f"R{round_idx + 1}M{m_idx + 1}: {p1['team_name']} vs {p2['team_name']}"[:100],
                type=discord.ChannelType.public_thread,
            )
            match_threads[key] = thread.id
            mentions = " ".join(_mem_mention(m) for m in p1['members'] + p2['members'])
            await thread.send(f"🏎️ **{p1['team_name']}** vs **{p2['team_name']}** — good luck! {mentions}")
        except Exception as e:
            print(f"[Tournament] Thread creation failed for {key}: {e}")

# ── Interactive Button Registration Panel ─────────────────────────────────────

import re as _re


def _parse_member_lines(guild, text: str) -> list[dict]:
    """Turn a multiline members text field into a list of member dicts.

    Each non-empty line becomes one member. We try to resolve <@id>, <@!id> or
    a raw numeric ID to a real guild member (storing a real uid + display name).
    Anything else is kept as a plain name with uid=None. Duplicate lines are
    ignored.
    """
    members: list[dict] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        uid = None
        name = line
        m = _re.match(r'^<@!?(\d+)>$', line)
        if m:
            uid = m.group(1)
        elif line.isdigit():
            uid = line
        if uid:
            member = guild.get_member(int(uid)) if guild else None
            name = member.display_name if member else f"User {uid}"
        key = uid or name.lower()
        if key in seen:
            continue
        seen.add(key)
        members.append({'name': name, 'uid': uid})
    return members


async def _ensure_team_role(guild, team_name: str):
    """Create (or reuse) a role named after the team. Returns the role, or None
    if the bot cannot manage roles."""
    if guild is None:
        return None
    existing = discord.utils.get(guild.roles, name=team_name)
    if existing:
        return existing
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return None
    try:
        return await guild.create_role(
            name=team_name, mentionable=True,
            reason="Smash Karts tournament team",
        )
    except Exception as e:
        print(f"[Tournament] Role creation failed for {team_name}: {e}")
        return None


async def _assign_team_role(guild, role, members: list[dict]):
    if not role or guild is None:
        return
    for m in members:
        uid = m.get('uid')
        if not uid:
            continue
        member = guild.get_member(int(uid))
        if member is None:
            continue
        try:
            await member.add_roles(role, reason="Smash Karts tournament team")
        except Exception as e:
            print(f"[Tournament] Failed to assign role to {uid}: {e}")


async def _remove_team_role(guild, entry: dict):
    """Delete the Discord role tied to an entry, if any."""
    if guild is None:
        return
    role = None
    role_id = entry.get('role_id')
    if role_id:
        role = guild.get_role(int(role_id))
    if role is None:
        role = discord.utils.get(guild.roles, name=entry.get('team_name', ''))
    if role is None:
        return
    try:
        await role.delete(reason="Smash Karts tournament team removed")
    except Exception as e:
        print(f"[Tournament] Failed to delete role for {entry.get('team_name')}: {e}")


def _build_confirm_embed(title: str, region: str, members: list[dict], subs: list[dict], team_size: int, is_team: bool) -> discord.Embed:
    em = discord.Embed(title="📋 Confirm Your Registration", color=discord.Color.gold())
    if is_team:
        em.add_field(name="Team Name", value=title, inline=True)
    else:
        em.add_field(name="Player", value=title, inline=True)
    em.add_field(name="Region", value=region or "—", inline=True)
    mem_str = "\n".join(f"• {_mem_mention(m)} ({m['name']})" for m in members) or "—"
    em.add_field(name=f"Members ({len(members)}/{team_size})", value=mem_str, inline=False)
    if subs:
        sub_str = "\n".join(f"• {_mem_mention(s)} ({s['name']})" for s in subs)
        em.add_field(name=f"Substitutes ({len(subs)})", value=sub_str, inline=False)
    em.set_footer(text="Press Confirm to register, or Cancel to discard.")
    return em


class TournamentConfirmView(discord.ui.View):
    """Shown after a registration modal is submitted: Confirm to save, Cancel to
    discard. On Confirm we save the entry, create one team role (team modes
    only), assign it to all members, and post a public 'Team Registered' embed."""

    def __init__(self, bot, gid: str, tour_name: str, entry: dict, team_size: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.gid = gid
        self.tour_name = tour_name
        self.entry = entry
        self.team_size = team_size

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        gid = self.gid
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await interaction.followup.send(embed=err("Registration is no longer open."), ephemeral=True)
        entries = t.get('entries', [])
        if find_entry(entries, self.entry['team_name']):
            return await interaction.followup.send(embed=err(f"A team named **{self.entry['team_name']}** was just registered. Please try again."), ephemeral=True)
        if len(entries) >= t['max_entries']:
            return await interaction.followup.send(embed=err("Tournament just filled up!"), ephemeral=True)

        role = None
        if self.team_size > 1:
            role = await _ensure_team_role(interaction.guild, self.entry['team_name'])
            if role:
                self.entry['role_id'] = str(role.id)
                await _assign_team_role(interaction.guild, role, self.entry['members'] + self.entry.get('subs', []))

        entries.append(self.entry)
        t['entries'] = entries
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, interaction.guild, name, t)

        for child in self.children:
            child.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except Exception:
            pass
        await interaction.followup.send(embed=ok("Registration Confirmed", f"You're all set for **{name}**!"), ephemeral=True)

        if self.team_size > 1:
            mentions = " ".join(_mem_mention(m) for m in self.entry['members'])
            pub = discord.Embed(
                title="✅ Team Registered!",
                description=f"**{self.entry['team_name']}** has joined **{name}**!",
                color=discord.Color.green(),
            )
            pub.add_field(name="Region", value=self.entry.get('region') or "—", inline=True)
            pub.add_field(name=f"Members ({len(self.entry['members'])})", value=mentions or "—", inline=False)
            if self.entry.get('subs'):
                pub.add_field(name="Substitutes", value=" ".join(_mem_mention(s) for s in self.entry['subs']), inline=False)
            if role:
                pub.add_field(name="Team Role", value=role.mention, inline=True)
            try:
                await interaction.channel.send(embed=pub)
            except Exception as e:
                print(f"[Tournament] Public announce failed: {e}")

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=err("Registration cancelled. Nothing was saved."), view=self)


class TournamentSubModal(discord.ui.Modal, title="🔁 Join as Substitute"):
    team_name = discord.ui.TextInput(
        label="Team Name",
        placeholder="Which team do you want to substitute for?",
        required=True, max_length=32,
    )

    def __init__(self, bot, gid: str, tour_name: str):
        super().__init__()
        self.bot = bot
        self.gid = gid
        self.tour_name = tour_name

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = self.gid
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await interaction.followup.send(embed=err("Registration is currently closed!"), ephemeral=True)
        max_subs = t.get('max_subs', 1)
        if max_subs <= 0:
            return await interaction.followup.send(embed=err("This tournament does not allow substitutes."), ephemeral=True)
        entries = t.get('entries', [])
        uid = str(interaction.user.id)
        if find_entry_by_member(entries, uid) or find_entry_by_sub(entries, uid):
            return await interaction.followup.send(embed=err("You are already registered in this tournament!"), ephemeral=True)
        tname = self.team_name.value.strip()
        existing = find_entry(entries, tname)
        if not existing:
            return await interaction.followup.send(embed=err(f"Team **{tname}** does not exist."), ephemeral=True)
        subs = existing.setdefault('subs', [])
        if len(subs) >= max_subs:
            return await interaction.followup.send(embed=err(f"Team **{tname}** already has the maximum substitutes ({max_subs}/{max_subs})."), ephemeral=True)
        display = interaction.user.display_name
        subs.append({'name': display, 'uid': uid})
        role = None
        if existing.get('role_id'):
            role = interaction.guild.get_role(int(existing['role_id']))
        if role is None:
            role = discord.utils.get(interaction.guild.roles, name=tname)
        if role:
            await _assign_team_role(interaction.guild, role, [{'name': display, 'uid': uid}])
        t['entries'] = entries
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)
        await sync_bracket_channel(self.bot, interaction.guild, name, t)
        await interaction.followup.send(embed=ok("Joined as Substitute", f"You joined **{tname}** as a substitute! ({len(subs)}/{max_subs})"), ephemeral=True)


class TournamentSignupModal(discord.ui.Modal, title="🏎️ Tournament Registration"):
    def __init__(self, bot, gid: str, tour_name: str, team_size: int, max_subs: int = 1):
        title = "🏎️ Tournament Registration" if team_size == 1 else "🏎️ Team Registration"
        super().__init__(title=title)
        self.bot = bot
        self.gid = gid
        self.tour_name = tour_name
        self.team_size = team_size
        self.max_subs = max_subs
        self.f_name = None
        self.f_team = None
        self.f_members = None
        self.f_subs = None

        if team_size == 1:
            # 1v1 — In-Game Name + Region
            self.f_name = discord.ui.TextInput(
                label="In-Game Name",
                placeholder="Your Smash Karts display name",
                required=True, max_length=32,
            )
            self.f_region = discord.ui.TextInput(
                label="Region",
                placeholder="e.g. NA, EU, SA, ASIA, OCE",
                required=True, max_length=32,
            )
            self.add_item(self.f_name)
            self.add_item(self.f_region)
        else:
            # Team modes — Team Name + Region + Members (+ optional Subs)
            self.f_team = discord.ui.TextInput(
                label="Team Name",
                placeholder="Your team name",
                required=True, max_length=32,
            )
            self.f_region = discord.ui.TextInput(
                label="Region",
                placeholder="e.g. NA, EU, SA, ASIA, OCE",
                required=True, max_length=32,
            )
            self.f_members = discord.ui.TextInput(
                label=f"Members (one per line, up to {team_size})",
                placeholder="@player1\n@player2\n... or paste user IDs / names",
                style=discord.TextStyle.paragraph,
                required=True, max_length=1000,
            )
            self.add_item(self.f_team)
            self.add_item(self.f_region)
            self.add_item(self.f_members)
            if max_subs > 0:
                self.f_subs = discord.ui.TextInput(
                    label=f"Substitutes (optional, up to {max_subs})",
                    placeholder="One per line — @sub1\n@sub2 ...",
                    style=discord.TextStyle.paragraph,
                    required=False, max_length=1000,
                )
                self.add_item(self.f_subs)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = self.gid
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await interaction.followup.send(embed=err("Registration is currently closed!"), ephemeral=True)

        team_size = t.get('team_size', 1)
        max_subs = t.get('max_subs', 1)
        entries = t.get('entries', [])
        uid = str(interaction.user.id)

        # ── 1v1 ──────────────────────────────────────────────
        if team_size == 1:
            if find_entry_by_member(entries, uid) or find_entry_by_sub(entries, uid):
                return await interaction.followup.send(embed=err("You are already registered for this tournament!"), ephemeral=True)
            if len(entries) >= t['max_entries']:
                return await interaction.followup.send(embed=err(f"Tournament is full! ({t['max_entries']}/{t['max_entries']})"), ephemeral=True)
            display = self.f_name.value.strip() or interaction.user.display_name
            region = self.f_region.value.strip()
            members = [{'name': display, 'uid': uid}]
            entry = {'team_name': display, 'members': members, 'subs': [], 'region': region, 'role_id': None}
            summary = _build_confirm_embed(display, region, members, [], team_size, is_team=False)
            view = TournamentConfirmView(self.bot, gid, name, entry, team_size)
            return await interaction.followup.send(embed=summary, view=view, ephemeral=True)

        # ── Team modes ───────────────────────────────────────
        tname = self.f_team.value.strip()
        region = self.f_region.value.strip()
        if find_entry(entries, tname):
            return await interaction.followup.send(embed=err(f"A team named **{tname}** is already registered!"), ephemeral=True)
        if len(entries) >= t['max_entries']:
            return await interaction.followup.send(embed=err(f"Tournament is full! ({t['max_entries']}/{t['max_entries']} teams)"), ephemeral=True)

        members = _parse_member_lines(interaction.guild, self.f_members.value)
        # Make sure the registrant is on the roster
        if not any(m.get('uid') == uid for m in members):
            members.insert(0, {'name': interaction.user.display_name, 'uid': uid})
        if not members:
            return await interaction.followup.send(embed=err("Please list at least one team member."), ephemeral=True)
        if len(members) > team_size:
            return await interaction.followup.send(embed=err(f"Too many members — this is a {team_size}v{team_size} tournament (you listed {len(members)})."), ephemeral=True)

        subs = []
        if self.f_subs is not None and self.f_subs.value.strip():
            subs = _parse_member_lines(interaction.guild, self.f_subs.value)
            if len(subs) > max_subs:
                return await interaction.followup.send(embed=err(f"Too many substitutes — max is {max_subs} (you listed {len(subs)})."), ephemeral=True)

        clash = []
        for m in members + subs:
            mu = m.get('uid')
            if mu and (find_entry_by_member(entries, mu) or find_entry_by_sub(entries, mu)):
                clash.append(m['name'])
        if clash:
            return await interaction.followup.send(embed=err("Already registered elsewhere: " + ", ".join(clash)), ephemeral=True)

        entry = {'team_name': tname, 'members': members, 'subs': subs, 'region': region, 'role_id': None}
        summary = _build_confirm_embed(tname, region, members, subs, team_size, is_team=True)
        view = TournamentConfirmView(self.bot, gid, name, entry, team_size)
        await interaction.followup.send(embed=summary, view=view, ephemeral=True)

class TournamentSignupView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="🟢 Join Tournament", style=discord.ButtonStyle.success, custom_id="tourney_join_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = str(interaction.guild_id)
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await interaction.response.send_message(embed=err("No open tournament registration right now!"), ephemeral=True)
        modal = TournamentSignupModal(self.bot, gid, name, t.get('team_size', 1), t.get('max_subs', 1))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔁 Join as Sub", style=discord.ButtonStyle.secondary, custom_id="tourney_sub_btn")
    async def sub_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = str(interaction.guild_id)
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await interaction.response.send_message(embed=err("No open tournament registration right now!"), ephemeral=True)
        if t.get('max_subs', 1) <= 0:
            return await interaction.response.send_message(embed=err("This tournament does not allow substitutes."), ephemeral=True)
        modal = TournamentSubModal(self.bot, gid, name)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔴 Leave Team", style=discord.ButtonStyle.danger, custom_id="tourney_leave_btn")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await interaction.followup.send(embed=err("No open tournament registration to leave."), ephemeral=True)

        uid = str(interaction.user.id)
        entries = t.get('entries', [])
        removed = False
        team_name = ""

        for e in entries:
            m_before = len(e['members'])
            e['members'] = [m for m in e['members'] if m.get('uid') != uid]
            s_before = len(e.get('subs', []))
            e['subs'] = [s for s in e.get('subs', []) if s.get('uid') != uid]
            if len(e['members']) < m_before or len(e.get('subs', [])) < s_before:
                removed = True
                team_name = e['team_name']
                # Take the team role off the departing member
                role = None
                if e.get('role_id'):
                    role = interaction.guild.get_role(int(e['role_id']))
                if role and interaction.user:
                    try:
                        await interaction.user.remove_roles(role, reason="Left tournament team")
                    except Exception:
                        pass
                if not e['members'] and not e.get('subs'):
                    await _remove_team_role(interaction.guild, e)
                    entries.remove(e)
                break

        if not removed:
            return await interaction.followup.send(embed=err("You are not registered in any team for this tournament."), ephemeral=True)

        t['entries'] = entries
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, interaction.guild, name, t)
        await interaction.followup.send(embed=ok("Left Tournament", f"You've been removed from team **{team_name}**."), ephemeral=True)

    @discord.ui.button(label="📋 View Roster", style=discord.ButtonStyle.primary, custom_id="tourney_roster_btn")
    async def roster_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = str(interaction.guild_id)
        name, t = get_active(gid)
        if not t:
            return await interaction.response.send_message(embed=err("No active tournament."), ephemeral=True)

        entries = t.get('entries', [])
        team_size = t.get('team_size', 1)
        max_subs = t.get('max_subs', 1)

        lines = []
        for i, e in enumerate(entries):
            tag = "✅" if entry_is_full(e, team_size) else f"⏳ {len(e['members'])}/{team_size}"
            members = ", ".join(_mem_mention(m) for m in e['members'])
            subs_str = ""
            if e.get('subs'):
                subs_str = " | **Subs:** " + ", ".join(_mem_mention(s) for s in e['subs'])
            lines.append(f"`{i+1}.` **{e['team_name']}** {tag} — {members}{subs_str}")

        em = discord.Embed(
            title=f"🏎️ {name} — Tournament Roster ({len(entries)}/{t['max_entries']})",
            description="\n".join(lines) or "*No teams registered yet*",
            color=discord.Color.blurple()
        )
        em.set_footer(text=f"Status: {t['status'].capitalize()} | Format: {team_size}v{team_size} (Max {max_subs} Sub)")
        await interaction.response.send_message(embed=em, ephemeral=True)

# ── COG CLASS ─────────────────────────────────────────────────────────────────

class Tournament(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_recurring.start()

    async def cog_load(self):
        self.bot.add_view(TournamentSignupView(self.bot))

    def cog_unload(self):
        self.check_recurring.cancel()

    @tasks.loop(hours=6)
    async def check_recurring(self):
        await yield_to_user_facing()
        now = int(time.time())
        for guild in self.bot.guilds:
            gid = str(guild.id)
            try:
                if get_active(gid)[0]:
                    continue
                tours = get_tournaments(gid)
                due = [
                    (nm, tt) for nm, tt in tours.items()
                    if tt.get('status') in ('done', 'cancelled')
                    and tt.get('repeat_every_days', 0) > 0
                    and tt.get('next_run_at') and tt['next_run_at'] <= now
                    and not tt.get('repeat_spawned')
                ]
                if not due:
                    continue
                due.sort(key=lambda x: x[1].get('ended_at', 0), reverse=True)
                old_name, old_t = due[0]
                old_t['repeat_spawned'] = True

                new_name = _next_recurring_name(tours, old_name)
                new_t = _clone_tournament_for_repeat(old_t, guild.owner_id if guild.owner_id else 0)
                tours[new_name] = new_t
                save_tournaments(gid, tours)

                settings = get_tournament_settings(gid)
                channel = None
                announce_id = settings.get('results_channel_id') or settings.get('bracket_channel_id')
                if announce_id:
                    channel = guild.get_channel(int(announce_id))
                if channel:
                    await rate_limit_wait("send_message", channel.id)
                    await channel.send(embed=ok(
                        "🔁 New Tournament Cycle Started!",
                        f"**{new_name}** is now open for registration — same rules as **{old_name}**.\n"
                        f"Join using the registration buttons or `!tjoin`!"
                    ), view=TournamentSignupView(self.bot))
            except Exception as e:
                print(f"[Tournament] Recurring check failed for guild {gid}: {e}")

    @check_recurring.before_loop
    async def before_check_recurring(self):
        await self.bot.wait_until_ready()

    # ── /tcreate ──────────────────────────────────────────
    @commands.command(name="tcreate", help="Create a new Smash Karts tournament")
    @commands.has_permissions(manage_guild=True)
    async def tcreate(self, ctx: commands.Context,
                      name: str, max_entries: int = 8, team_size: str = "1v1",
                      max_subs: int = 1, mode: str = "ffa", score_target: int = 10,
                      time_limit: int = 10, captures_to_win: int = 3,
                      map_pool: str = "", bots_enabled: bool = False,
                      weapons_enabled: bool = True, repeat_every_days: int = 0):
        gid = str(ctx.guild.id)

        if max_entries not in (4, 8, 16, 32):
            return await ctx.send(embed=err("Max entries must be 4, 8, 16, or 32."))

        active_name, _ = get_active(gid)
        if active_name:
            return await ctx.send(embed=err(f"There's already an active tournament: **{active_name}**!\nEnd it first with `{ctx.prefix}tend`."))

        tours = get_tournaments(gid)
        if name in tours:
            return await ctx.send(embed=err(f"A tournament named **{name}** already exists."))

        team_size_int = int(team_size[0])

        valid_map_set = CTF_MAPS if mode == 'ctf' else STANDARD_MAPS
        chosen_maps = []
        if map_pool.strip():
            requested = [m.strip() for m in map_pool.split(',') if m.strip()]
            unknown = [m for m in requested if m.lower() not in {vm.lower() for vm in valid_map_set}]
            if unknown:
                available = ", ".join(valid_map_set)
                return await ctx.send(embed=err(f"Unknown map(s) for {mode.upper()} mode: {', '.join(unknown)}.\nAvailable maps: {available}"))
            chosen_maps = requested

        tours[name] = {
            'status':          'open',
            'max_entries':     max_entries,
            'team_size':       team_size_int,
            'max_subs':        max(0, max_subs),
            'mode':            mode,
            'score_target':    score_target,
            'time_limit':      time_limit,
            'captures_to_win': captures_to_win,
            'map_pool':        chosen_maps,
            'bots_enabled':    bots_enabled,
            'weapons_enabled': weapons_enabled,
            'repeat_every_days': max(0, repeat_every_days),
            'entries':         [],
            'bracket':         [],
            'results':         {},
            'scores':          {},
            'forfeits':        {},
            'match_maps':      {},
            'champion':        None,
            'host_id':         ctx.author.id,
            'created_at':      int(datetime.now(timezone.utc).timestamp())
        }
        save_tournaments(gid, tours)

        em = discord.Embed(
            title="🏎️ Smash Karts Tournament Created!",
            description=(
                f"**Name:** {name}\n"
                f"**Host:** {ctx.author.mention}\n\n"
                f"{_settings_summary(tours[name])}\n\n"
                f"Click the buttons below to register, or type `!tjoin <team_name>`!\n"
                f"Start with `!tstart` when teams are filled."
            ),
            color=discord.Color.blurple()
        )
        em.set_footer(text="Registration is now open 🟢")
        await ctx.send(embed=em, view=TournamentSignupView(self.bot))

    # ── /tpanel ───────────────────────────────────────────
    @commands.command(name="tpanel", help="Post interactive tournament registration panel")
    @commands.has_permissions(manage_guild=True)
    async def tpanel(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t:
            return await ctx.send(embed=err("No active tournament found. Create one first with `!tcreate`!"))

        em = bracket_embed(name, t)
        await ctx.send(embed=em, view=TournamentSignupView(self.bot))

    # ── /tjoin ────────────────────────────────────────────
    @commands.command(name="tjoin", help="Join the current Smash Karts tournament")
    async def tjoin(self, ctx: commands.Context, team_name: str = None, player_name: str = None):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)

        if not t:
            return await ctx.send(embed=err(f"No open tournament right now! An admin can create one with `{ctx.prefix}tcreate`."))
        if t['status'] != 'open':
            return await ctx.send(embed=err("This tournament has already started — registration is closed."))

        uid = str(ctx.author.id)
        display = player_name or ctx.author.display_name
        team_size = t.get('team_size', 1)
        entries = t.get('entries', [])

        if find_entry_by_member(entries, uid) or find_entry_by_sub(entries, uid):
            return await ctx.send(embed=err("You're already registered in a team for this tournament!"))

        if team_size == 1:
            team_name = team_name or display
        elif not team_name:
            return await ctx.send(embed=err(f"This is a **{team_size}v{team_size}** tournament — please provide a `team_name`!"))

        existing = find_entry(entries, team_name)
        if existing:
            if entry_is_full(existing, team_size):
                return await ctx.send(embed=err(f"Team **{team_name}** is already full ({team_size}/{team_size}). Try joining as a sub using `!tsub` or the button!"))
            existing['members'].append({'name': display, 'uid': uid})
            msg = f"**{display}** joined team **{team_name}**! ({len(existing['members'])}/{team_size})"
        else:
            if len(entries) >= t['max_entries']:
                return await ctx.send(embed=err(f"Tournament is full! ({t['max_entries']}/{t['max_entries']} teams)"))
            entries.append({'team_name': team_name, 'members': [{'name': display, 'uid': uid}], 'subs': []})
            msg = f"**{display}** created/joined team **{team_name}**! (1/{team_size})"

        t['entries'] = entries
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, ctx.guild, name, t)
        await ctx.send(embed=ok("You're In!", f"{msg}\n**Teams registered:** {len(entries)}/{t['max_entries']}"))

    # ── /tsub ─────────────────────────────────────────────
    @commands.command(name="tsub", help="Join a team as a substitute player")
    async def tsub(self, ctx: commands.Context, team_name: str, player_name: str = None):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await ctx.send(embed=err("Registration is closed."))

        uid = str(ctx.author.id)
        display = player_name or ctx.author.display_name
        max_subs = t.get('max_subs', 1)
        entries = t.get('entries', [])

        if find_entry_by_member(entries, uid) or find_entry_by_sub(entries, uid):
            return await ctx.send(embed=err("You are already registered in a team!"))

        existing = find_entry(entries, team_name)
        if not existing:
            return await ctx.send(embed=err(f"Team **{team_name}** does not exist."))

        subs = existing.setdefault('subs', [])
        if len(subs) >= max_subs:
            return await ctx.send(embed=err(f"Team **{team_name}** already has max substitutes ({max_subs}/{max_subs})."))

        subs.append({'name': display, 'uid': uid})
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, ctx.guild, name, t)
        await ctx.send(embed=ok("Joined as Substitute", f"**{display}** joined team **{team_name}** as a Substitute! ({len(subs)}/{max_subs})"))

    # ── /tswap ────────────────(Swap Active Member with Sub)──
    @commands.command(name="tswap", help="Swap an inactive team member with a substitute player")
    async def tswap(self, ctx: commands.Context, active_member: discord.Member, sub_member: discord.Member):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t:
            return await ctx.send(embed=err("No active tournament."))

        active_uid = str(active_member.id)
        sub_uid = str(sub_member.id)
        entries = t.get('entries', [])

        team = find_entry_by_member(entries, active_uid)
        if not team:
            return await ctx.send(embed=err(f"{active_member.mention} is not in any team's active roster."))

        subs = team.get('subs', [])
        sub_entry = next((s for s in subs if s['uid'] == sub_uid), None)
        if not sub_entry:
            return await ctx.send(embed=err(f"{sub_member.mention} is not a registered substitute for team **{team['team_name']}**."))

        # Perform the swap
        active_entry = next(m for m in team['members'] if m['uid'] == active_uid)
        team['members'].remove(active_entry)
        team['subs'].remove(sub_entry)

        team['members'].append(sub_entry)
        team['subs'].append(active_entry)

        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, ctx.guild, name, t)
        await ctx.send(embed=ok("Roster Swapped!", f"🔁 Swapped active player {active_member.mention} with substitute {sub_member.mention} on team **{team['team_name']}**."))

    # ── /tleave ───────────────────────────────────────────
    @commands.command(name="tleave", help="Leave your team (registration only)")
    async def tleave(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)

        if not t or t['status'] != 'open':
            return await ctx.send(embed=err("No open tournament to leave."))

        uid = str(ctx.author.id)
        entries = t.get('entries', [])
        removed = False
        team_name = ""

        for e in entries:
            m_b = len(e['members'])
            e['members'] = [m for m in e['members'] if m.get('uid') != uid]
            s_b = len(e.get('subs', []))
            e['subs'] = [s for s in e.get('subs', []) if s.get('uid') != uid]
            if len(e['members']) < m_b or len(e.get('subs', [])) < s_b:
                removed = True
                team_name = e['team_name']
                role = None
                if e.get('role_id'):
                    role = ctx.guild.get_role(int(e['role_id']))
                if role:
                    try:
                        await ctx.author.remove_roles(role, reason="Left tournament team")
                    except Exception:
                        pass
                if not e['members'] and not e.get('subs'):
                    await _remove_team_role(ctx.guild, e)
                    entries.remove(e)
                break

        if not removed:
            return await ctx.send(embed=err("You're not registered in this tournament."))

        t['entries'] = entries
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, ctx.guild, name, t)
        await ctx.send(embed=ok("Left Tournament", f"You've been removed from team **{team_name}**."))

    # ── /tstart ───────────────────────────────────────────
    @commands.command(name="tstart", help="Start the tournament and generate the bracket")
    @commands.has_permissions(manage_guild=True)
    async def tstart(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)

        if not t:
            return await ctx.send(embed=err(f"No open tournament! Create one with `{ctx.prefix}tcreate`."))
        if t['status'] != 'open':
            return await ctx.send(embed=err("Tournament already started."))

        entries = t.get('entries', [])
        team_size = t.get('team_size', 1)

        if len(entries) < 2:
            return await ctx.send(embed=err("Need at least **2 teams** to start!"))

        incomplete = [e for e in entries if not entry_is_full(e, team_size)]
        if incomplete:
            names = ", ".join(f"{e['team_name']} ({len(e['members'])}/{team_size})" for e in incomplete)
            return await ctx.send(embed=err(f"Some teams aren't full yet: {names}\nWait for them to fill up or remove them before starting."))

        t['bracket'] = make_bracket(entries)
        t['status'] = 'running'
        t = advance_bracket(t)

        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        em = bracket_embed(name, t)
        em.title = f"🏎️ Tournament Started — {name}"
        await ctx.send(content="🚀 **The bracket is set! Let the races begin!** 🏁", embed=em)

        await announce_round_matches(self.bot, ctx.channel, name, t, 0)
        await sync_bracket_channel(self.bot, ctx.guild, name, t)
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

    # ── /tbracket ─────────────────────────────────────────
    @commands.command(name="tbracket", help="Show the current tournament bracket")
    async def tbracket(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t:
            return await ctx.send(embed=err("No active tournament found."))
        await ctx.send(embed=bracket_embed(name, t))

    # ── /tresult ──────────────────────────────────────────
    @commands.command(name="tresult", help="Report the winner and score of a match")
    @commands.check(_is_ref_or_admin)
    async def tresult(self, ctx: commands.Context,
                      round_number: int, match_number: int, winner: discord.Member, score: str = ""):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)

        if not t or t['status'] != 'running':
            return await ctx.send(embed=err("No running tournament found."))

        r_idx = round_number - 1
        m_idx = match_number - 1
        rounds = t.get('bracket', [])

        if r_idx < 0 or r_idx >= len(rounds):
            return await ctx.send(embed=err(f"Invalid round number. This tournament has {len(rounds)} round(s)."))

        matches = rounds[r_idx]
        if m_idx < 0 or m_idx >= len(matches):
            return await ctx.send(embed=err(f"Invalid match number. Round {round_number} has {len(matches)} match(es)."))

        p1, p2 = matches[m_idx]
        winner_uid = str(winner.id)

        winning_team = None
        if p1 and (any(m['uid'] == winner_uid for m in p1['members']) or any(s['uid'] == winner_uid for s in p1.get('subs', []))):
            winning_team = p1
        elif p2 and (any(m['uid'] == winner_uid for m in p2['members']) or any(s['uid'] == winner_uid for s in p2.get('subs', []))):
            winning_team = p2

        if not winning_team:
            return await ctx.send(embed=err(f"{winner.mention} is not part of either team in match {match_number} of round {round_number}.\nTeams: **{entry_label(p1)}** vs **{entry_label(p2)}**"))

        key = f"{r_idx}-{m_idx}"
        if key in t.get('results', {}):
            prev_name = t['results'][key]
            return await ctx.send(embed=err(f"Result for this match already recorded: **{prev_name}** won."))

        t.setdefault('results', {})[key] = winning_team['team_name']
        t.setdefault('scores', {})[key] = {'winner': winning_team['team_name'], 'score': score, 'reason': None}
        _pred_autolock(gid, name)

        t = advance_bracket(t)
        if t.get('status') == 'done':
            mark_tournament_finished(t)

        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        losing_team = p2 if winning_team is p1 else p1
        em = bracket_embed(name, t)

        champion_msg = ""
        if t.get('status') == 'done':
            champ = t['champion']
            champion_msg = f"\n\n🏆 {entry_label(champ)} **is the Smash Karts Champion!** 🏆"

        score_tag = f" `[{score}]`" if score else ""
        await ctx.send(content=f"✅ **{winning_team['team_name']}** defeated **{losing_team['team_name']}**{score_tag}!{champion_msg}", embed=em)
        await post_match_result_announcement(self.bot, ctx.guild, name, t, winning_team, losing_team, r_idx, m_idx, score=score)
        await sync_bracket_channel(self.bot, ctx.guild, name, t)

        if t.get('status') != 'done':
            for r in range(r_idx + 1, len(t.get('bracket', []))):
                matches_r = t['bracket'][r]
                if all(p1r and p2r for p1r, p2r in matches_r):
                    await announce_round_matches(self.bot, ctx.channel, name, t, r)
                    tours = get_tournaments(gid)
                    tours[name] = t
                    save_tournaments(gid, tours)
                    break

    # ── /tforfeit ─────────────────────────────────────────
    @commands.command(name="tforfeit", help="Forfeit a match with reason and score")
    @commands.check(_is_ref_or_admin)
    async def tforfeit(self, ctx: commands.Context, round_number: int, match_number: int, winning_team_name: str, score: str = "W-0", *, reason: str = "Opponent forfeit / inactive"):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)

        if not t or t['status'] != 'running':
            return await ctx.send(embed=err("No running tournament found."))

        r_idx = round_number - 1
        m_idx = match_number - 1
        rounds = t.get('bracket', [])

        if r_idx < 0 or r_idx >= len(rounds):
            return await ctx.send(embed=err(f"Invalid round number. Tournament has {len(rounds)} round(s)."))

        matches = rounds[r_idx]
        if m_idx < 0 or m_idx >= len(matches):
            return await ctx.send(embed=err(f"Invalid match number. Round {round_number} has {len(matches)} match(es)."))

        p1, p2 = matches[m_idx]
        if not p1 or not p2:
            return await ctx.send(embed=err("This match is not ready yet."))

        winning_team = None
        if p1['team_name'].lower() == winning_team_name.lower():
            winning_team = p1
        elif p2['team_name'].lower() == winning_team_name.lower():
            winning_team = p2

        if not winning_team:
            return await ctx.send(embed=err(f"Team **{winning_team_name}** is not in match {match_number}. Teams: **{p1['team_name']}** vs **{p2['team_name']}**."))

        key = f"{r_idx}-{m_idx}"
        t.setdefault('results', {})[key] = winning_team['team_name']
        t.setdefault('scores', {})[key] = {'winner': winning_team['team_name'], 'score': score, 'reason': reason}
        t.setdefault('forfeits', {})[key] = reason
        _pred_autolock(gid, name)

        t = advance_bracket(t)
        if t.get('status') == 'done':
            mark_tournament_finished(t)

        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        losing_team = p2 if winning_team is p1 else p1
        em = bracket_embed(name, t)

        await ctx.send(content=f"⚠️ **Forfeit match {match_number}!** **{winning_team['team_name']}** awarded win `{score}` vs **{losing_team['team_name']}** (Reason: {reason}).", embed=em)
        await post_match_result_announcement(self.bot, ctx.guild, name, t, winning_team, losing_team, r_idx, m_idx, score=score, reason=f"Forfeit ({reason})")
        await sync_bracket_channel(self.bot, ctx.guild, name, t)

    # ── /tpredict ─────────────────────────────────────────
    @commands.command(name="tpredict", help="Open the Fan Predictions web page")
    async def tpredict(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t:
            return await ctx.send(embed=err("No active tournament right now!"))

        domain = os.environ.get("DISCORD_REDIRECT_URI", "http://node2.vantahost.cloud:2013/callback").replace('/callback', '')
        if not domain or not domain.startswith('http'):
            domain = "http://node2.vantahost.cloud:2013"
        predict_url = f"{domain}/predict/{gid}"

        em = discord.Embed(
            title=f"🔮 Fan Predictions — {name}",
            description=(
                f"Predict match winners for **{name}** on the Fan Predictions website!\n\n"
                f"👉 **[Click Here to Open Fan Predictions Page]({predict_url})**\n\n"
                f"• Pick the winning team for each match in the interactive bracket tree\n"
                f"• View live forecasts from other members in real-time\n"
                f"• Earn points and climb the Prediction Leaderboard!"
            ),
            color=discord.Color.purple()
        )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🌐 Open Predictions Website", url=predict_url, style=discord.ButtonStyle.link))
        await ctx.send(embed=em, view=view)

    # ── /tpredictionlead ──────────────────────────────────
    @commands.command(name="tpredictionlead", help="View fan prediction leaderboard")
    async def tpredictionlead(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t:
            return await ctx.send(embed=err("No active tournament."))

        data, pred = _pred_get(gid, name)
        picks = pred.get('picks', {})
        results = t.get('results', {})
        scores = t.get('scores', {})

        if not picks:
            return await ctx.send(embed=err("No fan predictions placed yet!"))

        scores_list = []
        for uid, user_data in picks.items():
            pts = 0
            correct = 0
            exact_scores = 0
            for key, user_pick in user_data.get('picks', {}).items():
                real_winner = results.get(key)
                if not real_winner:
                    continue
                pred_win = user_pick.get('winner') if isinstance(user_pick, dict) else user_pick
                if pred_win and pred_win.lower() == real_winner.lower():
                    pts += 3
                    correct += 1
                    pred_sc = user_pick.get('score', '') if isinstance(user_pick, dict) else ''
                    real_sc = scores.get(key, {}).get('score', '')
                    if pred_sc and real_sc and pred_sc.strip() == real_sc.strip():
                        pts += 2
                        exact_scores += 1
            scores_list.append((user_data.get('name', f"<@{uid}>"), pts, correct, exact_scores))

        scores_list.sort(key=lambda x: x[1], reverse=True)

        lines = []
        for i, (uname, pts, corr, exact) in enumerate(scores_list):
            med = ["🥇", "🥈", "🥉"][i] if i < 3 else f"`{i+1}.`"
            lines.append(f"{med} **{uname}** — **{pts} pts** ({corr} correct, {exact} exact score)")

        em = discord.Embed(
            title=f"🎯 Fan Prediction Leaderboard — {name}",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        em.set_footer(text="3 pts for correct winner + 2 bonus pts for exact match score!")
        await ctx.send(embed=em)

    # ── /tadd ─────────────────────────────────────────────
    @commands.command(name="tadd", help="Add a Discord member to a team (admin)")
    @commands.has_permissions(manage_guild=True)
    async def tadd(self, ctx: commands.Context, member: discord.Member, team_name: str = None, player_name: str = None):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await ctx.send(embed=err("No open tournament to add players to."))

        uid = str(member.id)
        display = player_name or member.display_name
        team_size = t.get('team_size', 1)
        entries = t.get('entries', [])

        if find_entry_by_member(entries, uid) or find_entry_by_sub(entries, uid):
            return await ctx.send(embed=err(f"{member.mention} is already registered in this tournament!"))

        if team_size == 1:
            team_name = team_name or display
        elif not team_name:
            return await ctx.send(embed=err(f"This is a **{team_size}v{team_size}** tournament — please provide a `team_name`."))

        existing = find_entry(entries, team_name)
        if existing:
            if entry_is_full(existing, team_size):
                return await ctx.send(embed=err(f"Team **{team_name}** is already full ({team_size}/{team_size})."))
            existing['members'].append({'name': display, 'uid': uid})
        else:
            if len(entries) >= t['max_entries']:
                return await ctx.send(embed=err(f"Tournament is full! ({t['max_entries']}/{t['max_entries']} teams)"))
            entries.append({'team_name': team_name, 'members': [{'name': display, 'uid': uid}], 'subs': []})

        t['entries'] = entries
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, ctx.guild, name, t)
        await ctx.send(embed=ok("Player Added!", f"{member.mention} added to **{team_name}** as **{display}**."))

    # ── /tremove ──────────────────────────────────────────
    @commands.command(name="tremove", help="Remove a Discord member from their team (admin)")
    @commands.has_permissions(manage_guild=True)
    async def tremove(self, ctx: commands.Context, member: discord.Member):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await ctx.send(embed=err("Players can only be removed while registration is still open."))

        uid = str(member.id)
        entries = t.get('entries', [])
        team = find_entry_by_member(entries, uid) or find_entry_by_sub(entries, uid)
        if not team:
            return await ctx.send(embed=err(f"{member.mention} is not registered in this tournament."))

        team['members'] = [m for m in team['members'] if m.get('uid') != uid]
        team['subs'] = [s for s in team.get('subs', []) if s.get('uid') != uid]

        # Take the team role off the removed member
        role = None
        if team.get('role_id'):
            role = ctx.guild.get_role(int(team['role_id']))
        if role:
            try:
                await member.remove_roles(role, reason="Removed from tournament team")
            except Exception:
                pass

        if not team['members'] and not team['subs']:
            await _remove_team_role(ctx.guild, team)
            entries.remove(team)

        t['entries'] = entries
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, ctx.guild, name, t)
        await ctx.send(embed=ok("Player Removed", f"{member.mention} has been removed from team **{team['team_name']}**."))

    # ── /tremoveteam ──────────────────────────
    @commands.command(name="tremoveteam", help="Remove an entire team from the tournament (admin)")
    @commands.has_permissions(manage_guild=True)
    async def tremoveteam(self, ctx: commands.Context, *, team_name: str):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t or t['status'] != 'open':
            return await ctx.send(embed=err("Teams can only be removed while registration is still open."))

        entries = t.get('entries', [])
        team = find_entry(entries, team_name)
        if not team:
            return await ctx.send(embed=err(f"No team named **{team_name}** is registered."))

        # Strip the role from everyone, then delete the role.
        role = None
        if team.get('role_id'):
            role = ctx.guild.get_role(int(team['role_id']))
        if role is None:
            role = discord.utils.get(ctx.guild.roles, name=team['team_name'])
        if role:
            for m in team['members'] + team.get('subs', []):
                mu = m.get('uid')
                if not mu:
                    continue
                mem = ctx.guild.get_member(int(mu))
                if mem:
                    try:
                        await mem.remove_roles(role, reason="Team removed from tournament")
                    except Exception:
                        pass
        await _remove_team_role(ctx.guild, team)

        entries.remove(team)
        t['entries'] = entries
        tours = get_tournaments(gid)
        tours[name] = t
        save_tournaments(gid, tours)

        await sync_bracket_channel(self.bot, ctx.guild, name, t)
        await ctx.send(embed=ok("Team Removed", f"Team **{team['team_name']}** has been removed from the tournament."))

    # ── /tend ─────────────────────────────────────────────
    @commands.command(name="tend", help="End/cancel the current tournament")
    @commands.has_permissions(manage_guild=True)
    async def tend(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t:
            return await ctx.send(embed=err("No active tournament to end."))

        tours = get_tournaments(gid)
        # Clean up any team roles created for this tournament.
        for e in tours[name].get('entries', []):
            await _remove_team_role(ctx.guild, e)
        tours[name]['status'] = 'cancelled'
        mark_tournament_finished(tours[name])
        save_tournaments(gid, tours)

        await ctx.send(embed=ok("Tournament Ended", f"**{name}** has been cancelled/ended."))

    # ── /tplayers ─────────────────────────────────────────
    @commands.command(name="tplayers", help="List registered teams in the current tournament")
    async def tplayers(self, ctx: commands.Context):
        gid = str(ctx.guild.id)
        name, t = get_active(gid)
        if not t:
            return await ctx.send(embed=err("No active tournament."))

        entries = t.get('entries', [])
        team_size = t.get('team_size', 1)
        max_subs = t.get('max_subs', 1)

        lines = []
        for i, e in enumerate(entries):
            tag = "✅" if entry_is_full(e, team_size) else f"⏳ {len(e['members'])}/{team_size}"
            members = ", ".join(_mem_mention(m) for m in e['members'])
            subs_str = ""
            if e.get('subs'):
                subs_str = " | **Subs:** " + ", ".join(_mem_mention(s) for s in e['subs'])
            lines.append(f"`{i+1}.` **{e['team_name']}** {tag} — {members}{subs_str}")

        em = discord.Embed(
            title=f"🏎️ {name} — Teams ({len(entries)}/{t['max_entries']})",
            description="\n".join(lines) or "*No teams yet*",
            color=discord.Color.blurple()
        )
        em.set_footer(text=f"Status: {t['status'].capitalize()} | Format: {team_size}v{team_size} (Max {max_subs} Subs) | Join with /tjoin")
        await ctx.send(embed=em)

    # ── /tmaps ────────────────────────────────────────────
    @commands.command(name="tmaps", help="List available Smash Karts maps")
    async def tmaps(self, ctx: commands.Context, mode: str = "ffa"):
        maps = CTF_MAPS if mode == "ctf" else STANDARD_MAPS
        em = discord.Embed(
            title=f"🗺️ Available Maps — {'CTF' if mode=='ctf' else 'FFA / Score Target'}",
            description="\n".join(f"• {m}" for m in maps),
            color=discord.Color.teal()
        )
        em.set_footer(text="Use these names (comma-separated) in /tcreate's map_pool option")
        await ctx.send(embed=em)

async def setup(bot):
    await bot.add_cog(Tournament(bot))
