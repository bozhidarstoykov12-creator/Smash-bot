import discord
from discord.ext import commands, tasks
import asyncio
import aiohttp
import time
import json
import logging
from datetime import datetime
from utils import load, save, is_module_enabled, doc_lock

FLOWS_FILE = 'command_flows.json'
CUSTOM_CMDS_FILE = 'custom_commands.json'

logger = logging.getLogger('command_flow')

def get_flow_data(gid: str) -> dict:
    data = load(FLOWS_FILE).get(gid, {})
    if not data:
        data = {
            "commands": {},
            "timed_events": {},
            "variables": {},
            "secrets": {},
            "logs": []
        }
    return data

def save_flow_data(gid: str, data: dict):
    # RACE FIX: flow executions (async tasks) and the timed-events loop all
    # rewrite this document — serialize every RMW through the doc lock.
    with doc_lock(FLOWS_FILE):
        all_data = load(FLOWS_FILE)
        all_data[gid] = data
        save(FLOWS_FILE, all_data)

def log_flow_error(gid: str, command_name: str, block_name: str, error_msg: str):
    data = get_flow_data(gid)
    logs = data.get('logs', [])
    logs.insert(0, {
        "timestamp": int(time.time()),
        "time_str": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "command": command_name,
        "block": block_name,
        "error": error_msg
    })
    data['logs'] = logs[:100]
    save_flow_data(gid, data)

class FlowInterpreter:
    def __init__(self, bot, guild: discord.Guild, user: discord.Member, channel: discord.TextChannel = None, variables: dict = None, secrets: dict = None):
        self.bot = bot
        self.guild = guild
        self.user = user
        self.channel = channel
        self.gid = str(guild.id) if guild else ''
        self.variables = variables or {}
        self.secrets = secrets or {}
        self.local_vars = {}

    def replace_vars(self, text: str) -> str:
        if not text:
            return ""
        res = str(text)
        res = res.replace('{user.mention}', self.user.mention if self.user else '')
        res = res.replace('{user.name}', self.user.name if self.user else '')
        res = res.replace('{user.id}', str(self.user.id) if self.user else '')
        res = res.replace('{server.name}', self.guild.name if self.guild else '')
        res = res.replace('{server.id}', str(self.guild.id) if self.guild else '')
        res = res.replace('{channel.mention}', self.channel.mention if self.channel else '')
        res = res.replace('{channel.name}', self.channel.name if self.channel else '')
        
        for k, v in self.variables.items():
            res = res.replace(f"{{var.{k}}}", str(v))
            res = res.replace(f"{{{k}}}", str(v))

        for k, v in self.local_vars.items():
            res = res.replace(f"{{local.{k}}}", str(v))

        for k, v in self.secrets.items():
            res = res.replace(f"{{secret.{k}}}", str(v))

        return res

    async def execute(self, flow: dict, initial_block_id=None):
        nodes = {n['id']: n for n in flow.get('nodes', [])}
        connections = flow.get('connections', [])
        
        # Build adjacency mapping: (from_id, branch) -> to_id
        adj = {}
        for conn in connections:
            frm = conn.get('from')
            to = conn.get('to')
            branch = conn.get('branch') or 'default'
            if frm and to:
                adj.setdefault(frm, {})[branch] = to

        # Find starting node
        current_id = initial_block_id
        if not current_id:
            for n in flow.get('nodes', []):
                if n.get('type', '').startswith('trigger') or n.get('category') == 'options' or n.get('type') == 'error_handler':
                    current_id = n['id']
                    break
        if not current_id and flow.get('nodes'):
            current_id = flow['nodes'][0]['id']

        visited = set()
        while current_id and current_id in nodes:
            if current_id in visited:
                break
            visited.add(current_id)
            node = nodes[current_id]
            branch_taken = 'default'
            try:
                result = await self.execute_node(node)
                if isinstance(result, bool):
                    branch_taken = 'true' if result else 'false'
            except Exception as e:
                err_text = str(e)
                log_flow_error(self.gid, flow.get('name', 'Unknown'), node.get('title', node.get('type', 'Block')), err_text)
                break

            branches = adj.get(current_id, {})
            current_id = branches.get(branch_taken) or branches.get('default')

    async def execute_node(self, node: dict):
        ntype = node.get('type', '')
        props = node.get('props', {})

        # Messages
        if ntype in ('send_message', 'embed_reply'):
            msg_text = self.replace_vars(props.get('content', ''))
            embed_title = self.replace_vars(props.get('embed_title', ''))
            embed_desc = self.replace_vars(props.get('embed_description', ''))
            color = props.get('embed_color', 0x5865F2)
            if isinstance(color, str) and color.startswith('#'):
                try: color = int(color[1:], 16)
                except: color = 0x5865F2

            embed = None
            if embed_title or embed_desc:
                embed = discord.Embed(title=embed_title, description=embed_desc, color=color)

            target_chan = self.channel
            if props.get('channel_id'):
                c = self.guild.get_channel(int(props['channel_id']))
                if c: target_chan = c

            if target_chan:
                await target_chan.send(content=msg_text if msg_text else None, embed=embed)

        elif ntype == 'send_dm':
            msg_text = self.replace_vars(props.get('content', ''))
            if self.user:
                await self.user.send(content=msg_text)

        elif ntype == 'react_to_message':
            emoji = props.get('emoji', '👍')
            if self.channel and hasattr(self.channel, 'last_message') and self.channel.last_message:
                await self.channel.last_message.add_reaction(emoji)

        # Conditions
        elif ntype == 'check_has_role':
            role_id = props.get('role_id')
            if self.user and role_id:
                has_it = any(r.id == int(role_id) for r in getattr(self.user, 'roles', []))
                return has_it
            return False

        elif ntype == 'check_variable':
            vname = props.get('var_name', '')
            val = self.variables.get(vname, self.local_vars.get(vname, ''))
            target = props.get('target_value', '')
            op = props.get('operator', '==')
            if op == '==': return str(val) == str(target)
            elif op == '!=': return str(val) != str(target)
            return False

        # Variables
        elif ntype == 'set_variable':
            vname = props.get('var_name', '').strip()
            vval = self.replace_vars(props.get('var_value', ''))
            if vname:
                self.local_vars[vname] = vval
                if props.get('scope') == 'global':
                    fdata = get_flow_data(self.gid)
                    fdata.setdefault('variables', {})[vname] = vval
                    save_flow_data(self.gid, fdata)

        # API
        elif ntype == 'api_request':
            url = self.replace_vars(props.get('url', ''))
            method = props.get('method', 'GET').upper()
            if url:
                async with aiohttp.ClientSession() as session:
                    async with session.request(method, url, timeout=10) as resp:
                        text_resp = await resp.text()
                        save_as = props.get('save_response_var')
                        if save_as:
                            self.local_vars[save_as] = text_resp

        # Moderation
        elif ntype == 'kick_member':
            target_id = props.get('user_id')
            member = self.guild.get_member(int(target_id)) if target_id else self.user
            if member and member != self.guild.owner:
                reason = self.replace_vars(props.get('reason', 'Kicked via Command Builder'))
                await member.kick(reason=reason)

        elif ntype == 'ban_member':
            target_id = props.get('user_id')
            member = self.guild.get_member(int(target_id)) if target_id else self.user
            if member and member != self.guild.owner:
                reason = self.replace_vars(props.get('reason', 'Banned via Command Builder'))
                await member.ban(reason=reason)

        elif ntype == 'timeout_member':
            mins = int(props.get('minutes', 5))
            target_id = props.get('user_id')
            member = self.guild.get_member(int(target_id)) if target_id else self.user
            if member and hasattr(member, 'timeout'):
                from datetime import timedelta
                await member.timeout(timedelta(minutes=mins), reason="Command Builder Timeout")

        elif ntype == 'add_roles':
            target_id = props.get('user_id')
            member = self.guild.get_member(int(target_id)) if target_id else self.user
            role_id = props.get('role_id')
            if member and role_id:
                role = self.guild.get_role(int(role_id))
                if role:
                    await member.add_roles(role)

        # Delays
        elif ntype == 'wait_action':
            secs = min(int(props.get('seconds', 1)), 60)
            await asyncio.sleep(secs)

        return None


class CommandFlowCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.timed_events_loop.start()

    def cog_unload(self):
        self.timed_events_loop.cancel()

    @tasks.loop(minutes=1)
    async def timed_events_loop(self):
        all_data = load(FLOWS_FILE)
        now = int(time.time())
        for gid, gdata in all_data.items():
            guild = self.bot.get_guild(int(gid)) if gid.isdigit() else None
            if not guild:
                continue
            events = gdata.get('timed_events', {})
            for eid, event in events.items():
                if not event.get('enabled', False):
                    continue
                interval = int(event.get('interval_minutes', 60)) * 60
                last_run = event.get('last_run', 0)
                if now - last_run >= interval:
                    # RACE FIX: the old code saved the STALE all_data snapshot
                    # loaded at loop start — clobbering concurrent flow
                    # executions and dashboard edits. Re-read fresh under the
                    # doc lock and mark last_run atomically.
                    with doc_lock(FLOWS_FILE):
                        fresh = load(FLOWS_FILE)
                        fresh_g = fresh.get(gid, {})
                        run_event = fresh_g.get('timed_events', {}).get(eid)
                        if run_event is None or not run_event.get('enabled', False):
                            continue
                        run_event['last_run'] = now
                        save(FLOWS_FILE, fresh)
                        run_vars = fresh_g.get('variables', {})
                        run_secrets = fresh_g.get('secrets', {})
                    channel = guild.text_channels[0] if guild.text_channels else None
                    if run_event.get('channel_id'):
                        c = guild.get_channel(int(run_event['channel_id']))
                        if c: channel = c
                    interpreter = FlowInterpreter(
                        bot=self.bot,
                        guild=guild,
                        user=guild.me,
                        channel=channel,
                        variables=run_vars,
                        secrets=run_secrets
                    )
                    asyncio.create_task(interpreter.execute(run_event))

    @timed_events_loop.before_loop
    async def before_timed_events(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        gid = str(message.guild.id)
        prefix = load('config.json').get(gid, {}).get('command_prefix', '!')
        content = message.content
        if not content.startswith(prefix):
            return
        rest = content[len(prefix):].strip()
        if not rest:
            return
        trigger = rest.split()[0].lower()

        fdata = get_flow_data(gid)
        commands_dict = fdata.get('commands', {})
        matched_cmd = None
        for cid, cmd in commands_dict.items():
            if cmd.get('enabled', True) and (cmd.get('trigger', '').lower() == trigger or cmd.get('name', '').lower() == trigger):
                matched_cmd = cmd
                break

        if matched_cmd:
            interpreter = FlowInterpreter(
                bot=self.bot,
                guild=message.guild,
                user=message.author,
                channel=message.channel,
                variables=fdata.get('variables', {}),
                secrets=fdata.get('secrets', {})
            )
            await interpreter.execute(matched_cmd)


    async def _trigger_event_flow(self, guild: discord.Guild, user: discord.Member, trigger_type: str, extra_vars: dict = None):
        if not guild:
            return
        gid = str(guild.id)
        fdata = get_flow_data(gid)
        commands_dict = fdata.get('commands', {})
        for cid, cmd in commands_dict.items():
            if not cmd.get('enabled', True):
                continue
            # Match node trigger type
            nodes = cmd.get('nodes', [])
            has_trigger = any(n.get('type') == trigger_type for n in nodes)
            if has_trigger:
                chan = guild.system_channel or (guild.text_channels[0] if guild.text_channels else None)
                merged_vars = dict(fdata.get('variables', {}))
                if extra_vars:
                    merged_vars.update(extra_vars)
                interpreter = FlowInterpreter(
                    bot=self.bot,
                    guild=guild,
                    user=user,
                    channel=chan,
                    variables=merged_vars,
                    secrets=fdata.get('secrets', {})
                )
                asyncio.create_task(interpreter.execute(cmd))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._trigger_event_flow(member.guild, member, 'on_member_join')

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._trigger_event_flow(member.guild, member, 'on_member_remove')

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild and message.author and not message.author.bot:
            await self._trigger_event_flow(message.guild, message.author, 'on_message_delete', {'deleted_content': message.content})

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.guild and not before.premium_since and after.premium_since:
            # Server boosted!
            await self._trigger_event_flow(after.guild, after, 'on_server_boost')


async def setup(bot):
    await bot.add_cog(CommandFlowCog(bot))
