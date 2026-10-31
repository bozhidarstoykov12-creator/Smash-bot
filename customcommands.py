import discord
from discord.ext import commands
import time
from utils import load, save, is_module_enabled

CUSTOM_CMDS_FILE = 'custom_commands.json'
COOLDOWN_SECONDS = 5

# Trigger words already used by real bot ! commands — these can never be
# overridden by a custom command, so the two systems can't collide.
RESERVED_NAMES = {
    'ban', 'unban', 'softban', 'tempban', 'untempban', 'kick', 'mute', 'unmute', 'warn', 'warnings',
    'clearwarns', 'clear', 'slowmode', 'lock', 'unlock', 'hide', 'unhide', 'nick', 'note', 'notes',
    'announce', 'role', 'massrole', 'setlogchannel', 'serverinfo', 'userinfo', 'setwelcome', 'setautorole',
    'rps', 'tictactoe', 'connect4', 'wordle', 'hangman', 'trivia', 'guess', 'scramble', 'minesweeper',
    'memory', 'hilo', 'blackjack', 'slots', 'quickmath', 'emojiquiz', 'typerace', '8ball', 'coinflip',
    'roll', 'joke', 'avatar', 'poll', 'wouldyourather', 'tcreate', 'tjoin', 'tleave', 'tstart', 'tbracket',
    'tresult', 'tadd', 'tremove', 'tend', 'tplayers', 'tmaps', 'help',
}

# key: "gid:uid:trigger" -> last-used timestamp
_cooldowns: dict[str, float] = {}


def get_commands(gid: str) -> dict:
    return load(CUSTOM_CMDS_FILE).get(gid, {})


def _get_prefix(gid: str) -> str:
    return load('config.json').get(gid, {}).get('command_prefix', '!')


def is_reserved(trigger: str) -> bool:
    return trigger.lower() in RESERVED_NAMES


class CustomCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        gid = str(message.guild.id)
        if not is_module_enabled(gid, 'custom_commands'):
            return
        prefix = _get_prefix(gid)
        content = message.content
        if not content.startswith(prefix):
            return

        rest = content[len(prefix):].strip()
        if not rest:
            return
        trigger = rest.split()[0].lower()
        if is_reserved(trigger):
            return  # a real bot command, not ours to handle

        entry = get_commands(gid).get(trigger)
        if not entry:
            return

        key = f"{gid}:{message.author.id}:{trigger}"
        now = time.time()
        if now - _cooldowns.get(key, 0) < COOLDOWN_SECONDS:
            return
        _cooldowns[key] = now

        response = (entry.get('response', '')
                    .replace('{user.mention}', message.author.mention)
                    .replace('{user.name}', message.author.name)
                    .replace('{server.name}', message.guild.name))
        if not response:
            return
        try:
            await message.channel.send(response[:2000])
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(CustomCommands(bot))
