import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from utils import load, save, ok, err, info, doc_lock
from cogs.economy import add_balance, get_cfg as get_eco_cfg

STREAKS_FILE = 'streaks.json'
CLAIM_COOLDOWN_HOURS = 20  # must wait at least this long since the last claim


def get_cfg(gid: str) -> dict:
    cfg = load('config.json').get(gid, {})
    d = cfg.get('daily_reward', {})
    return {
        'enabled': d.get('enabled', True),
        'base_xp': int(d.get('base_xp', 20)),
        'base_coins': int(d.get('base_coins', 15)),
        'streak_bonus_per_day': int(d.get('streak_bonus_per_day', 2)),
        'max_streak_bonus_days': int(d.get('max_streak_bonus_days', 15)),
        'grace_hours': int(d.get('grace_hours', 48)),  # miss more than this since your last claim and the streak resets
    }


def get_streak(gid: str, uid: str) -> dict:
    return load(STREAKS_FILE).get(gid, {}).get(uid, {'streak': 0, 'best_streak': 0, 'last_claim': 0})


class Streaks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="daily", description="Claim your daily XP & currency reward, and build your streak!")
    async def daily(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        uid = str(interaction.user.id)
        cfg = get_cfg(gid)
        if not cfg['enabled']:
            return await interaction.response.send_message(embed=err("Daily rewards are disabled on this server."), ephemeral=True)

        # RACE FIX (two-phase): the whole check-and-claim cycle on streaks.json
        # is one atomic critical section — two parallel /daily clicks can no
        # longer both pass the cooldown check and double-claim, and the
        # dashboard's api_daily_streaks_save can't clobber this write.
        with doc_lock(STREAKS_FILE):
            data = load(STREAKS_FILE)
            g = data.setdefault(gid, {})
            entry = g.setdefault(uid, {'streak': 0, 'best_streak': 0, 'last_claim': 0})

            now = datetime.now(timezone.utc).timestamp()
            last = entry.get('last_claim', 0)
            hours_since = (now - last) / 3600 if last else None
            already_claimed = hours_since is not None and hours_since < CLAIM_COOLDOWN_HOURS
            if already_claimed:
                remaining = CLAIM_COOLDOWN_HOURS - hours_since
                h, m = int(remaining), int((remaining % 1) * 60)
                cd_msg = f"You've already claimed today! Come back in **{h}h {m}m**."
            else:
                streak_reset = hours_since is not None and hours_since > cfg['grace_hours']
                if streak_reset:
                    entry['streak'] = 0

                entry['streak'] += 1
                entry['best_streak'] = max(entry['best_streak'], entry['streak'])
                entry['last_claim'] = now
                save(STREAKS_FILE, data)

        if already_claimed:
            return await interaction.response.send_message(embed=err(cd_msg), ephemeral=True)

        bonus_days = min(entry['streak'] - 1, cfg['max_streak_bonus_days'])
        xp_gain = cfg['base_xp'] + bonus_days * cfg['streak_bonus_per_day']
        coin_gain = cfg['base_coins'] + bonus_days * cfg['streak_bonus_per_day']

        # Award XP (mirrors leveling.py's levels.json shape directly).
        # RACE FIX: locked against the dashboard's api_levels_save.
        with doc_lock('levels.json'):
            lvl_data = load('levels.json')
            lvl_data.setdefault(gid, {})
            user_lvl = lvl_data[gid].setdefault(uid, {"xp": 0, "name": interaction.user.name})
            user_lvl['xp'] = user_lvl.get('xp', 0) + xp_gain
            user_lvl['name'] = interaction.user.name
            save('levels.json', lvl_data)

        eco_cfg = get_eco_cfg(gid)
        new_balance = add_balance(gid, interaction.user.id, coin_gain, name=interaction.user.name)

        streak_emoji = "🔥" if entry['streak'] >= 3 else "✨"
        desc = (
            f"+**{xp_gain} XP** and +**{coin_gain} {eco_cfg['currency_name']}** {eco_cfg['currency_emoji']}\n"
            f"New balance: **{new_balance} {eco_cfg['currency_name']}**\n"
            f"Best streak: **{entry['best_streak']}** day(s)"
        )
        if streak_reset:
            desc += "\n⚠️ You missed too many days — your streak was reset before this claim."

        await interaction.response.send_message(embed=ok(f"{streak_emoji} Daily Claimed! ({entry['streak']}-day streak)", desc))

    @app_commands.command(name="streak", description="Check your (or someone else's) daily streak")
    @app_commands.describe(member="Whose streak to check (defaults to you)")
    async def streak(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        gid = str(interaction.guild.id)
        entry = load(STREAKS_FILE).get(gid, {}).get(str(member.id))
        if not entry or not entry.get('streak'):
            return await interaction.response.send_message(
                embed=info("🔥 No Streak Yet", f"{member.mention} hasn't claimed a daily reward yet."), ephemeral=True)
        await interaction.response.send_message(embed=info(
            f"🔥 {member.display_name}'s Streak",
            f"Current streak: **{entry['streak']}** day(s)\nBest streak: **{entry['best_streak']}** day(s)"
        ))


async def setup(bot):
    await bot.add_cog(Streaks(bot))
