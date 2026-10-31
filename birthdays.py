import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone
from utils import load, save, ok, err, info, safe_channel_send, rate_limit_wait, get_daily_last_run, set_daily_last_run, doc_lock

BIRTHDAYS_FILE = 'birthdays.json'


def get_cfg(gid: str) -> dict:
    cfg = load('config.json').get(gid, {})
    b = cfg.get('birthdays', {})
    return {
        'enabled': b.get('enabled', False),
        'channel': b.get('channel', ''),
        'message': b.get('message', "🎉 Happy Birthday {user.mention}! Hope your day is amazing! 🎂🥳"),
    }


def get_birthdays(gid: str) -> dict:
    return load(BIRTHDAYS_FILE).get(gid, {})


class Birthdays(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.birthday_ticker.start()

    def cog_unload(self):
        self.birthday_ticker.cancel()

    async def run_check(self, guild: discord.Guild):
        gid = str(guild.id)
        cfg = get_cfg(gid)
        if not cfg['enabled'] or not str(cfg['channel']).isdigit():
            return []
        channel = guild.get_channel(int(cfg['channel']))
        if not channel:
            return []

        today = datetime.now(timezone.utc)
        data = get_birthdays(gid)
        celebrants = []
        for uid, entry in data.items():
            if entry.get('month') == today.month and entry.get('day') == today.day:
                member = guild.get_member(int(uid))
                if member:
                    celebrants.append(member)

        for member in celebrants:
            msg = (cfg['message']
                   .replace('{user.mention}', member.mention)
                   .replace('{user.name}', member.name)
                   .replace('{server.name}', guild.name))
            await rate_limit_wait("send_message", channel.id)
            await safe_channel_send(channel, msg, label="Birthdays")
        return celebrants

    @tasks.loop(hours=6)
    async def birthday_ticker(self):
        today_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        for guild in self.bot.guilds:
            gid = str(guild.id)
            if get_daily_last_run('birthdays', gid) == today_iso:
                continue
            try:
                await self.run_check(guild)
            except Exception as e:
                print(f"[Birthdays] Error checking {gid}: {e}")
            set_daily_last_run('birthdays', gid, today_iso)

    @birthday_ticker.before_loop
    async def before_ticker(self):
        await self.bot.wait_until_ready()

    birthday = app_commands.Group(name="birthday", description="Birthday tracker commands")

    @birthday.command(name="set", description="Set your birthday (day & month only — no year is stored)")
    @app_commands.describe(month="Birth month (1-12)", day="Birth day (1-31)")
    async def set_birthday(self, interaction: discord.Interaction, month: app_commands.Range[int, 1, 12], day: app_commands.Range[int, 1, 31]):
        try:
            datetime(2024, month, day)  # 2024 is a leap year — validates Feb 29 too
        except ValueError:
            return await interaction.response.send_message(embed=err("That's not a real calendar date."), ephemeral=True)

        gid = str(interaction.guild.id)
        with doc_lock(BIRTHDAYS_FILE):
            data = load(BIRTHDAYS_FILE)
            data.setdefault(gid, {})[str(interaction.user.id)] = {'month': month, 'day': day, 'name': interaction.user.name}
            save(BIRTHDAYS_FILE, data)

        await interaction.response.send_message(
            embed=ok("Birthday Saved!", f"We'll celebrate you on **{month:02d}/{day:02d}** 🎂 (only the day & month are stored, no year/age)"),
            ephemeral=True)

    @birthday.command(name="remove", description="Remove your saved birthday")
    async def remove_birthday(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        with doc_lock(BIRTHDAYS_FILE):
            data = load(BIRTHDAYS_FILE)
            removed = data.get(gid, {}).pop(str(interaction.user.id), None) is not None
            if removed:
                save(BIRTHDAYS_FILE, data)
        if removed:
            await interaction.response.send_message(embed=ok("Removed", "Your birthday has been deleted."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=err("You don't have a birthday saved."), ephemeral=True)

    @birthday.command(name="view", description="Check a member's birthday (day & month only)")
    @app_commands.describe(member="Whose birthday to check (defaults to you)")
    async def view_birthday(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        gid = str(interaction.guild.id)
        entry = get_birthdays(gid).get(str(member.id))
        if not entry:
            return await interaction.response.send_message(
                embed=info("🎂 No Birthday Saved", f"{member.mention} hasn't set a birthday yet."), ephemeral=True)
        await interaction.response.send_message(embed=info("🎂 Birthday", f"{member.mention}'s birthday is **{entry['month']:02d}/{entry['day']:02d}**"))


async def setup(bot):
    await bot.add_cog(Birthdays(bot))
