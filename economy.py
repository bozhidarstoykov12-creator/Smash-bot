import discord
from discord.ext import commands, tasks
from discord import app_commands
import copy
import random
import time
from datetime import datetime, time as dt_time, timezone
from utils import (load, save, ok, err, info, medal, rate_limit_wait, doc_lock,
                   game_reward, get_guild_cfg, quest_progress)
from i18n import t

# ══════════════════════════════════════════════════════════
#  DATA LAYER — economy.json
#  { "<gid>": { currency_name, currency_emoji, earn_counting,
#               earn_levelup, earn_command, command_earn_cooldown,
#               shop: [ {id,name,price,type,desc,role_id?,duration_minutes?} ],
#               balances: { "<uid>": {balance, inventory:{item_id:qty}, xp_boost_until, name} } } }
# ══════════════════════════════════════════════════════════

DEFAULT_CFG = {
    'currency_name': 'SK',
    'currency_emoji': '🪙',
    'earn_counting': 5,
    'earn_levelup': 25,
    'earn_command': 1,
    'command_earn_cooldown': 60,
    'shop': [],
    'balances': {},
}


def _ensure_guild(data, gid):
    gid = str(gid)
    if gid not in data:
        data[gid] = copy.deepcopy(DEFAULT_CFG)
    else:
        for k, v in DEFAULT_CFG.items():
            if k not in data[gid]:
                data[gid][k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
    return data[gid]


def get_cfg(gid):
    """Public: read-only view of a guild's economy config (creates defaults if missing)."""
    with doc_lock('economy.json'):
        data = load('economy.json')
        cfg = _ensure_guild(data, gid)
        save('economy.json', data)
        return cfg


def _ensure_user(cfg, uid, name=None):
    uid = str(uid)
    balances = cfg.setdefault('balances', {})
    if uid not in balances:
        balances[uid] = {'balance': 0, 'inventory': {}, 'xp_boost_until': 0, 'name': name or ''}
    if name:
        balances[uid]['name'] = name
    return balances[uid]


def get_balance(gid, uid):
    return get_cfg(gid).get('balances', {}).get(str(uid), {}).get('balance', 0)


def add_balance(gid, uid, amount, name=None):
    """Add (or, with a negative amount, subtract) currency. Returns the new balance."""
    with doc_lock('economy.json'):
        data = load('economy.json')
        cfg = _ensure_guild(data, gid)
        u = _ensure_user(cfg, uid, name)
        u['balance'] = max(0, u['balance'] + amount)
        save('economy.json', data)
        return u['balance']


def transfer_balance(gid, from_uid, to_uid, amount):
    """Returns (ok: bool, error: str|None)."""
    with doc_lock('economy.json'):
        data = load('economy.json')
        cfg = _ensure_guild(data, gid)
        sender = _ensure_user(cfg, from_uid)
        if sender['balance'] < amount:
            return False, "insufficient"
        recipient = _ensure_user(cfg, to_uid)
        sender['balance'] -= amount
        recipient['balance'] += amount
        save('economy.json', data)
        return True, None


def has_item(gid, uid, item_id):
    return get_cfg(gid).get('balances', {}).get(str(uid), {}).get('inventory', {}).get(item_id, 0) > 0


def add_item(gid, uid, item_id, qty=1):
    with doc_lock('economy.json'):
        data = load('economy.json')
        cfg = _ensure_guild(data, gid)
        u = _ensure_user(cfg, uid)
        inv = u.setdefault('inventory', {})
        inv[item_id] = inv.get(item_id, 0) + qty
        save('economy.json', data)


def consume_item(gid, uid, item_id):
    """Consume one copy of item_id if the user has it. Returns True if consumed."""
    with doc_lock('economy.json'):
        data = load('economy.json')
        cfg = _ensure_guild(data, gid)
        u = _ensure_user(cfg, uid)
        inv = u.setdefault('inventory', {})
        if inv.get(item_id, 0) > 0:
            inv[item_id] -= 1
            if inv[item_id] <= 0:
                del inv[item_id]
            save('economy.json', data)
            return True
        return False
    return False


def activate_xp_boost(gid, uid, minutes):
    # RACE FIX: dashboard api_economy_save writes economy.json from Flask
    # threads; this RMW must hold the shared document lock.
    with doc_lock('economy.json'):
        data = load('economy.json')
        cfg = _ensure_guild(data, gid)
        u = _ensure_user(cfg, uid)
        now = time.time()
        base = max(now, u.get('xp_boost_until', 0))
        u['xp_boost_until'] = base + minutes * 60
        save('economy.json', data)
        return u['xp_boost_until']


def get_xp_multiplier(gid, uid):
    """2.0x while an XP boost is active, otherwise 1.0x. Used by leveling.py."""
    u = get_cfg(gid).get('balances', {}).get(str(uid), {})
    return 2.0 if u.get('xp_boost_until', 0) > time.time() else 1.0


# ══════════════════════════════════════════════════════════════════════
#  ECONOMY 2.0 — bank, cooldowns and gambling bookkeeping
#  All helpers below perform a full read-modify-write under
#  doc_lock('economy.json') and NEVER await inside the lock (two-phase
#  pattern — see PLAN Part IV).
# ══════════════════════════════════════════════════════════════════════

def _user(gid, uid, name=None):
    """Return a live reference to the user's balance dict (caller must hold
    doc_lock('economy.json'))."""
    data = load('economy.json')
    cfg = _ensure_guild(data, gid)
    return data, _ensure_user(cfg, uid, name)


def get_bank(gid, uid):
    return get_cfg(gid).get('balances', {}).get(str(uid), {}).get('bank', 0)


def bank_transfer(gid, uid, amount, direction, name=None):
    """Atomically move `amount` between wallet and bank.
    direction='deposit'  → wallet → bank
    direction='withdraw' → bank   → wallet
    Returns (ok: bool, wallet: int, bank: int)."""
    gid, uid = str(gid), str(uid)
    with doc_lock('economy.json'):
        data, u = _user(gid, uid, name)
        wallet = int(u.get('balance', 0))
        bank = int(u.get('bank', 0))
        if direction == 'deposit':
            if wallet < amount:
                return False, wallet, bank
            u['balance'] = wallet - amount
            u['bank'] = bank + amount
        else:
            if bank < amount:
                return False, wallet, bank
            u['bank'] = bank - amount
            u['balance'] = wallet + amount
        save('economy.json', data)
        return True, int(u['balance']), int(u['bank'])


def cooldown_remaining(gid, uid, field, seconds):
    """Return remaining cooldown seconds for a timestamp field, or 0 if ready."""
    u = get_cfg(gid).get('balances', {}).get(str(uid), {})
    last = float(u.get(field, 0) or 0)
    return max(0.0, seconds - (time.time() - last))


def _fmt_duration(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _currency(gid):
    cfg = get_cfg(gid)
    return cfg.get('currency_name', 'SK'), cfg.get('currency_emoji', '🪙')


# ══════════════════════════════════════════════════════════
#  COG
# ══════════════════════════════════════════════════════════

class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cmd_earn_cd: dict[tuple, float] = {}  # (gid, uid) -> last earn timestamp
        self.monthly_tax.start()
        self.weekly_rewards.start()

    def cog_unload(self):
        self.monthly_tax.cancel()
        self.weekly_rewards.cancel()

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        """Generic 'earn SK by using commands' hook — fires after ANY successful
        slash command in a guild, gated by a per-user cooldown so people can't
        farm currency by spamming trivial commands."""
        if not interaction.guild:
            return
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_cfg(gid)
        amount = cfg.get('earn_command', 0)
        if amount <= 0:
            return
        cooldown = cfg.get('command_earn_cooldown', 60)
        key = (gid, uid)
        now = time.time()
        if now - self._cmd_earn_cd.get(key, 0) < cooldown:
            return
        self._cmd_earn_cd[key] = now
        add_balance(gid, uid, amount, name=interaction.user.name)

    economy = app_commands.Group(name="economy", description="SK currency commands")

    @economy.command(name="balance", description="Check your (or someone else's) currency balance")
    @app_commands.describe(member="Whose balance to check")
    async def balance(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        gid = interaction.guild.id
        cfg = get_cfg(gid)
        bal = get_balance(gid, member.id)
        em = discord.Embed(
            title=t(gid, 'eco.balance_title', emoji=cfg['currency_emoji'], currency=cfg['currency_name']),
            description=t(gid, 'eco.balance_desc', mention=member.mention, amount=bal,
                           currency=cfg['currency_name'], emoji=cfg['currency_emoji']),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=em)

    @economy.command(name="pay", description="Give some of your currency to another member")
    @app_commands.describe(member="Who to pay", amount="How much to send")
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        gid = str(interaction.guild.id)
        if amount <= 0:
            return await interaction.response.send_message(embed=err(t(gid, 'eco.pay_bad_amount')), ephemeral=True)
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message(embed=err(t(gid, 'eco.pay_self_or_bot')), ephemeral=True)
        cfg = get_cfg(gid)
        success, _ = transfer_balance(gid, interaction.user.id, member.id, amount)
        if not success:
            return await interaction.response.send_message(
                embed=err(t(gid, 'eco.pay_no_funds', currency=cfg['currency_name'])), ephemeral=True)
        await interaction.response.send_message(embed=ok(
            t(gid, 'eco.pay_title', amount=amount, currency=cfg['currency_name']),
            t(gid, 'eco.pay_desc', sender=interaction.user.mention, recipient=member.mention)))

    @economy.command(name="shop", description="View the server's shop")
    async def shop(self, interaction: discord.Interaction):
        gid = interaction.guild.id
        cfg = get_cfg(gid)
        items = cfg.get('shop', [])
        if not items:
            return await interaction.response.send_message(
                embed=info(t(gid, 'eco.shop_empty_title'), t(gid, 'eco.shop_empty_desc')))
        em = discord.Embed(title=t(gid, 'eco.shop_title', currency=cfg['currency_name']), color=discord.Color.blurple())
        for it in items:
            em.add_field(
                name=f"{it['name']} — {it['price']} {cfg['currency_emoji']}",
                value=it.get('desc') or "No description",
                inline=False,
            )
        em.set_footer(text=t(gid, 'eco.shop_footer'))
        await interaction.response.send_message(embed=em)

    @economy.command(name="buy", description="Buy an item from the shop")
    @app_commands.describe(item="The item's name (see /economy shop)")
    async def buy(self, interaction: discord.Interaction, item: str):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_cfg(gid)
        match = next(
            (it for it in cfg.get('shop', []) if it['name'].lower() == item.lower() or it.get('id') == item),
            None,
        )
        if not match:
            return await interaction.response.send_message(embed=err(t(gid, 'eco.item_not_found')), ephemeral=True)

        bal = get_balance(gid, uid)
        if bal < match['price']:
            return await interaction.response.send_message(
                embed=err(t(gid, 'eco.not_enough_funds', price=match['price'], emoji=cfg['currency_emoji'], balance=bal)),
                ephemeral=True)

        add_balance(gid, uid, -match['price'], name=interaction.user.name)

        effect = match.get('type', 'custom')
        extra = t(gid, 'eco.effect_default')
        if effect == 'counting_save':
            # counting.py always checks for the fixed key 'count_save', regardless
            # of which shop entry (and random id) was actually purchased.
            add_item(gid, uid, 'count_save', 1)
            extra = t(gid, 'eco.effect_save')
        elif effect == 'xp_boost':
            minutes = int(match.get('duration_minutes', 60))
            activate_xp_boost(gid, uid, minutes)
            extra = t(gid, 'eco.effect_boost', minutes=minutes)
        elif effect == 'role':
            role_id = match.get('role_id')
            role = interaction.guild.get_role(int(role_id)) if role_id else None
            if role:
                try:
                    await rate_limit_wait("add_guild_member_role", interaction.guild.id)
                    await interaction.user.add_roles(role)
                    extra = t(gid, 'eco.effect_role', role=role.mention)
                except discord.Forbidden:
                    extra = t(gid, 'eco.effect_role_forbidden')
                except discord.HTTPException:
                    extra = t(gid, 'eco.effect_role_error')
            else:
                extra = t(gid, 'eco.effect_role_missing')

        await interaction.response.send_message(embed=ok(t(gid, 'eco.purchased_title', item=match['name']), extra))

    @economy.command(name="inventory", description="View your purchased items")
    @app_commands.describe(member="Whose inventory to check")
    async def inventory(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        gid = interaction.guild.id
        cfg = get_cfg(gid)
        u = cfg.get('balances', {}).get(str(member.id), {})
        inv = u.get('inventory', {})
        boost_until = u.get('xp_boost_until', 0)

        lines = []
        for item_id, qty in inv.items():
            if item_id == 'count_save':
                name = "🛡️ Counting Save"
            else:
                match = next((it for it in cfg.get('shop', []) if it['id'] == item_id), None)
                name = match['name'] if match else item_id
            lines.append(f"**{name}** ×{qty}")
        if boost_until > time.time():
            mins_left = int((boost_until - time.time()) / 60) + 1
            lines.append(t(gid, 'eco.xp_boost_line', mins=mins_left))
        if not lines:
            lines = [t(gid, 'eco.inventory_empty')]

        em = discord.Embed(
            title=t(gid, 'eco.inventory_title', name=member.display_name),
            description="\n".join(lines),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=em)

    @economy.command(name="add", description="[Admin] Add or remove currency from a member")
    @app_commands.describe(member="Who to give/take currency from", amount="Amount to add (use a negative number to remove)")
    async def add(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        gid = str(interaction.guild.id)
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(embed=err(t(gid, 'eco.add_no_perms')), ephemeral=True)
        if member.bot:
            return await interaction.response.send_message(embed=err(t(gid, 'eco.add_no_bot')), ephemeral=True)
        if amount == 0:
            return await interaction.response.send_message(embed=err(t(gid, 'eco.add_no_zero')), ephemeral=True)

        cfg = get_cfg(gid)
        new_bal = add_balance(gid, member.id, amount, name=member.name)

        await interaction.response.send_message(embed=ok(
            t(gid, 'eco.add_title', sign='➕' if amount > 0 else '➖', currency=cfg['currency_name']),
            t(gid, 'eco.add_desc', mention=member.mention, new_bal=new_bal, currency=cfg['currency_name'],
              emoji=cfg['currency_emoji'],
              verb=t(gid, 'eco.add_added') if amount > 0 else t(gid, 'eco.add_removed'),
              amount=abs(amount))
        ))

    @economy.command(name="leaderboard", description="Top currency holders in this server")
    async def leaderboard(self, interaction: discord.Interaction):
        gid = interaction.guild.id
        cfg = get_cfg(gid)
        balances = cfg.get('balances', {})
        top = sorted(balances.items(), key=lambda kv: kv[1].get('balance', 0), reverse=True)[:10]
        if not top:
            return await interaction.response.send_message(embed=err(t(gid, 'eco.no_data')))
        em = discord.Embed(title=t(gid, 'eco.leaderboard_title', currency=cfg['currency_name']), color=discord.Color.gold())
        desc = ""
        for i, (uid, u) in enumerate(top):
            user = self.bot.get_user(int(uid))
            name = user.display_name if user else (u.get('name') or f"User …{uid[-4:]}")
            desc += f"{medal(i)} **{name}** — {u.get('balance', 0)} {cfg['currency_emoji']}\n"
        em.description = desc
        await interaction.response.send_message(embed=em)

    # ── Economy 2.0 — top-level slash commands ──────────────────────────

    @app_commands.command(name="work", description="Work a shift to earn some currency")
    async def work(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_guild_cfg(gid, "economy")
        currency, emoji = _currency(gid)
        cd = int(cfg.get("work_cooldown", 3600))
        remaining = cooldown_remaining(gid, uid, "last_work", cd)
        if remaining > 0:
            return await interaction.response.send_message(
                embed=err(t(gid, 'eco2.cooldown', time=_fmt_duration(remaining))), ephemeral=True)

        amount = random.randint(int(cfg.get("work_min", 20)), int(cfg.get("work_max", 150)))
        jackpot = random.random() < float(cfg.get("work_jackpot_chance", 0.05))
        if jackpot:
            amount *= 3
        # Phase 1: atomic RMW under the lock (no awaits).
        with doc_lock('economy.json'):
            data, u = _user(gid, uid, interaction.user.name)
            u['last_work'] = time.time()
            save('economy.json', data)
        granted = game_reward(gid, uid, amount, reason="work")
        quest_progress(gid, uid, "earn", granted)
        # Phase 2: network work outside the lock.
        desc = t(gid, 'eco2.work_desc', amount=granted, currency=currency, emoji=emoji)
        if jackpot:
            desc += "\n" + t(gid, 'eco2.work_jackpot')
        await interaction.response.send_message(embed=ok(t(gid, 'eco2.work_title'), desc))

    @app_commands.command(name="rob", description="Attempt to rob another member's wallet")
    @app_commands.describe(member="Who to rob")
    async def rob(self, interaction: discord.Interaction, member: discord.Member):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_guild_cfg(gid, "economy")
        currency, emoji = _currency(gid)
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message(embed=err(t(gid, 'eco2.rob_self')), ephemeral=True)
        cd = int(cfg.get("rob_cooldown", 7200))
        remaining = cooldown_remaining(gid, uid, "last_rob", cd)
        if remaining > 0:
            return await interaction.response.send_message(
                embed=err(t(gid, 'eco2.cooldown', time=_fmt_duration(remaining))), ephemeral=True)

        fee = int(cfg.get("rob_fee", 25))
        min_target = int(cfg.get("rob_min_target", 50))
        success_rate = float(cfg.get("rob_success", 0.55))
        steal_pct = float(cfg.get("rob_steal_pct", 0.25))
        refund_pct = float(cfg.get("rob_fail_refund_pct", 0.10))
        tid = str(member.id)

        # Phase 1: atomic RMW under the lock (no awaits).
        with doc_lock('economy.json'):
            data = load('economy.json')
            gcfg = _ensure_guild(data, gid)
            robber = _ensure_user(gcfg, uid, interaction.user.name)
            target = _ensure_user(gcfg, tid, member.name)
            if robber.get('last_rob_target') == tid:
                error = t(gid, 'eco2.rob_same')
            elif int(robber.get('balance', 0)) < fee:
                error = t(gid, 'eco2.rob_fee', fee=fee, currency=currency)
            elif int(target.get('balance', 0)) < min_target:
                error = t(gid, 'eco2.rob_poor', min=min_target)
            else:
                error = None
                robber['balance'] = int(robber.get('balance', 0)) - fee  # fee lost either way
                robber['last_rob'] = time.time()
                robber['last_rob_target'] = tid
                if random.random() < success_rate:
                    stolen = int(int(target.get('balance', 0)) * steal_pct)
                    target['balance'] = int(target.get('balance', 0)) - stolen
                    robber['balance'] = int(robber.get('balance', 0)) + stolen
                    outcome = ('success', stolen)
                else:
                    comp = int(fee * refund_pct)
                    target['balance'] = int(target.get('balance', 0)) + comp
                    outcome = ('fail', comp)
                save('economy.json', data)

        if error is not None:
            return await interaction.response.send_message(embed=err(error), ephemeral=True)

        # Phase 2: network work outside the lock.
        if outcome[0] == 'success':
            desc = t(gid, 'eco2.rob_success', target=member.mention, amount=outcome[1], currency=currency, emoji=emoji)
        else:
            desc = t(gid, 'eco2.rob_fail', target=member.mention, amount=outcome[1], currency=currency)
        await interaction.response.send_message(embed=ok(t(gid, 'eco2.rob_title'), desc))

    @app_commands.command(name="crime", description="Commit a crime for a big payout — or a fine")
    async def crime(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_guild_cfg(gid, "economy")
        currency, emoji = _currency(gid)
        cd = int(cfg.get("crime_cooldown", 10800))
        remaining = cooldown_remaining(gid, uid, "last_crime", cd)
        if remaining > 0:
            return await interaction.response.send_message(
                embed=err(t(gid, 'eco2.cooldown', time=_fmt_duration(remaining))), ephemeral=True)

        success_rate = float(cfg.get("crime_success", 0.60))
        # Phase 1: atomic RMW under the lock (no awaits).
        with doc_lock('economy.json'):
            data, u = _user(gid, uid, interaction.user.name)
            u['last_crime'] = time.time()
            if random.random() < success_rate:
                amount = random.randint(int(cfg.get("crime_min", 100)), int(cfg.get("crime_max", 400)))
                outcome = ('success', amount)
            else:
                if random.random() < 0.5:
                    fine = random.randint(int(cfg.get("crime_fine_min", 50)), int(cfg.get("crime_fine_max", 150)))
                    u['balance'] = max(0, int(u.get('balance', 0)) - fine)
                    outcome = ('fine', fine)
                else:
                    u['last_crime'] = time.time() + cd  # jail: cooldown doubled
                    outcome = ('jail', 0)
            save('economy.json', data)

        # Phase 2: network work outside the lock.
        if outcome[0] == 'success':
            granted = game_reward(gid, uid, outcome[1], reason="crime")
            quest_progress(gid, uid, "earn", granted)
            desc = t(gid, 'eco2.crime_success', amount=granted, currency=currency, emoji=emoji)
        elif outcome[0] == 'fine':
            desc = t(gid, 'eco2.crime_fail', amount=outcome[1], currency=currency)
        else:
            desc = t(gid, 'eco2.crime_jail')
        await interaction.response.send_message(embed=ok(t(gid, 'eco2.crime_title'), desc))

    @app_commands.command(name="beg", description="Beg for a few coins")
    async def beg(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_guild_cfg(gid, "economy")
        currency, emoji = _currency(gid)
        cd = int(cfg.get("beg_cooldown", 1800))
        remaining = cooldown_remaining(gid, uid, "last_beg", cd)
        if remaining > 0:
            return await interaction.response.send_message(
                embed=err(t(gid, 'eco2.cooldown', time=_fmt_duration(remaining))), ephemeral=True)

        amount = random.randint(int(cfg.get("beg_min", 1)), int(cfg.get("beg_max", 15)))
        jackpot = random.random() < float(cfg.get("beg_jackpot_chance", 0.01))
        if jackpot:
            amount = int(cfg.get("beg_jackpot", 100))
        # Phase 1: atomic RMW under the lock (no awaits).
        with doc_lock('economy.json'):
            data, u = _user(gid, uid, interaction.user.name)
            u['last_beg'] = time.time()
            save('economy.json', data)
        granted = game_reward(gid, uid, amount, reason="beg")
        quest_progress(gid, uid, "earn", granted)
        # Phase 2: network work outside the lock.
        if jackpot:
            desc = t(gid, 'eco2.beg_jackpot', amount=granted, currency=currency, emoji=emoji)
        else:
            desc = t(gid, 'eco2.beg_desc', amount=granted, currency=currency, emoji=emoji)
        await interaction.response.send_message(embed=ok(t(gid, 'eco2.beg_title'), desc))

    @app_commands.command(name="deposit", description="Move currency from your wallet into the bank")
    @app_commands.describe(amount="How much to deposit")
    async def deposit(self, interaction: discord.Interaction, amount: int):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        currency, emoji = _currency(gid)
        if amount <= 0:
            return await interaction.response.send_message(embed=err(t(gid, 'eco2.bad_amount')), ephemeral=True)
        okk, wallet, bank = bank_transfer(gid, uid, amount, 'deposit', name=interaction.user.name)
        if not okk:
            return await interaction.response.send_message(embed=err(t(gid, 'eco2.bank_no_funds')), ephemeral=True)
        await interaction.response.send_message(embed=ok(
            t(gid, 'eco2.deposit_title'),
            t(gid, 'eco2.deposit_desc', amount=amount, currency=currency, bank=bank)))

    @app_commands.command(name="withdraw", description="Move currency from the bank back into your wallet")
    @app_commands.describe(amount="How much to withdraw")
    async def withdraw(self, interaction: discord.Interaction, amount: int):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        currency, emoji = _currency(gid)
        if amount <= 0:
            return await interaction.response.send_message(embed=err(t(gid, 'eco2.bad_amount')), ephemeral=True)
        okk, wallet, bank = bank_transfer(gid, uid, amount, 'withdraw', name=interaction.user.name)
        if not okk:
            return await interaction.response.send_message(embed=err(t(gid, 'eco2.bank_no_funds')), ephemeral=True)
        await interaction.response.send_message(embed=ok(
            t(gid, 'eco2.withdraw_title'),
            t(gid, 'eco2.withdraw_desc', amount=amount, currency=currency, wallet=wallet)))

    @app_commands.command(name="bank", description="View your wallet, bank balance and next monthly fee")
    async def bank(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_guild_cfg(gid, "economy")
        currency, emoji = _currency(gid)
        u = get_cfg(gid).get('balances', {}).get(uid, {})
        wallet = int(u.get('balance', 0))
        bank = int(u.get('bank', 0))
        fee = int(cfg.get("bank_fee", 1))
        await interaction.response.send_message(embed=info(
            t(gid, 'eco2.bank_title'),
            t(gid, 'eco2.bank_desc', wallet=wallet, bank=bank, currency=currency, emoji=emoji, fee=fee)))

    # ── Background loops ────────────────────────────────────────────────

    @tasks.loop(time=dt_time(hour=12, minute=0, tzinfo=timezone.utc))
    async def monthly_tax(self):
        """On the 1st of each month at 12:00 UTC, charge every bank account a
        flat fee (never below zero) and optionally pay interest."""
        await self.bot.wait_until_ready()
        if datetime.now(timezone.utc).day != 1:
            return
        with doc_lock('economy.json'):
            data = load('economy.json')
            for gid, gcfg in data.items():
                if not isinstance(gcfg, dict):
                    continue
                ecfg = get_guild_cfg(gid, "economy")
                fee = int(ecfg.get("bank_fee", 1))
                interest = bool(ecfg.get("bank_interest_enabled", False))
                rate = float(ecfg.get("bank_interest_rate", 0.005))
                for uid, u in gcfg.get('balances', {}).items():
                    bank = int(u.get('bank', 0))
                    if bank <= 0:
                        continue
                    bank = max(0, bank - min(bank, fee))
                    if interest:
                        bank += int(bank * rate)
                    u['bank'] = bank
            save('economy.json', data)

    @tasks.loop(time=dt_time(hour=12, minute=0, tzinfo=timezone.utc))
    async def weekly_rewards(self):
        """Every Saturday at 12:00 UTC, reward the top 3 wallet holders."""
        await self.bot.wait_until_ready()
        if datetime.now(timezone.utc).weekday() != 5:  # 5 = Saturday
            return
        with doc_lock('economy.json'):
            data = load('economy.json')
            for gid, gcfg in data.items():
                if not isinstance(gcfg, dict):
                    continue
                ecfg = get_guild_cfg(gid, "economy")
                rewards = [int(ecfg.get("weekly_reward_1", 500)),
                           int(ecfg.get("weekly_reward_2", 250)),
                           int(ecfg.get("weekly_reward_3", 100))]
                balances = gcfg.get('balances', {})
                top = sorted(balances.items(), key=lambda kv: int(kv[1].get('balance', 0)), reverse=True)[:3]
                for i, (uid, u) in enumerate(top):
                    u['balance'] = int(u.get('balance', 0)) + rewards[i]
            save('economy.json', data)


async def setup(bot):
    await bot.add_cog(Economy(bot))
