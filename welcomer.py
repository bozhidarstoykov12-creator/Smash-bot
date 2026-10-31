import discord
from discord.ext import commands
import json
import os
from utils import load, save

class WelcomerLeave(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def load_config(self):
        return load('config.json')

    def save_config(self, config):
        save('config.json', config)

    # ══════════════════════════════════════════════════════════
    #  EVENT LISTENERS
    # ══════════════════════════════════════════════════════════
    
    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        cfg = self.load_config().get(str(guild.id), {}).get('welcomer', {})
        
        # 1. CHECK: Is the main module enabled in the dashboard?
        if not cfg.get('enabled', False):
            return

        # 2. CHECK: Is the welcome sub-module enabled?
        if not cfg.get('welcome_enabled', False):
            return

        # AUTO-ROLE
        if cfg.get('autorole_enabled', False):
            role_ids = [int(r.strip()) for r in cfg.get('autorole_roles', '').split(',') if r.strip().isdigit()]
            for r_id in role_ids:
                role = guild.get_role(r_id)
                if role:
                    try: await member.add_roles(role)
                    except discord.Forbidden: pass

        # DIRECT MESSAGE (DM)
        if cfg.get('dm_enabled', False):
            dm_msg = cfg.get('dm_message', '').replace('{user.name}', member.name).replace('{server.name}', guild.name).replace('{member_count}', str(guild.member_count))
            if dm_msg:
                try:
                    await member.send(dm_msg)
                except discord.HTTPException:
                    # Covers Forbidden (403) AND the more common case — error 50007
                    # "Cannot send messages to this user" (DMs closed / no mutual server
                    # visibility yet / bot blocked) — that one is a plain HTTPException,
                    # NOT discord.Forbidden, so it must be caught here too. Either way,
                    # a failed DM should never stop the channel welcome message below.
                    pass

        # CHANNEL MESSAGE / EMBED
        channel_id = cfg.get('channel')
        if channel_id and str(channel_id).isdigit():
            channel = guild.get_channel(int(channel_id))
            if channel:
                raw_msg = cfg.get('message', 'Welcome {user.mention}!')
                formatted_msg = raw_msg.replace('{user.mention}', member.mention).replace('{user.name}', member.name).replace('{server.name}', guild.name).replace('{member_count}', str(guild.member_count))

                # Optional image banner (configured on the dashboard, like the
                # rank card). Generated on the fly with the member's avatar.
                banner_file = None
                if cfg.get('banner', {}).get('enabled', False):
                    try:
                        from cogs.welcome_banner import generate_banner_file
                        banner_file = await generate_banner_file(member, guild, kind='welcome')
                    except Exception:
                        banner_file = None

                if cfg.get('embed_enabled', False):
                    color_hex = cfg.get('embed_color', '#5865f2').replace('#', '')
                    color = int(color_hex, 16) if all(c in '0123456789abcdefABCDEF' for c in color_hex) else 0x5865f2
                    title = cfg.get('embed_title', '👋 New Member!').replace('{user.name}', member.name).replace('{server.name}', guild.name)
                    embed = discord.Embed(title=title, description=formatted_msg, color=color)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    if banner_file is not None:
                        embed.set_image(url="attachment://welcome.png")
                        await channel.send(embed=embed, file=banner_file)
                    else:
                        await channel.send(embed=embed)
                else:
                    if banner_file is not None:
                        await channel.send(content=formatted_msg, file=banner_file)
                    else:
                        await channel.send(formatted_msg)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild = member.guild
        cfg = self.load_config().get(str(guild.id), {}).get('welcomer', {})
        
        # 1. CHECK: Is the main module enabled in the dashboard?
        if not cfg.get('enabled', False):
            return

        # 2. CHECK: Is the leave sub-module enabled?
        if not cfg.get('leave_enabled', False):
            return

        # LEAVE MESSAGE IN CHANNEL
        channel_id = cfg.get('leave_channel')
        if channel_id and str(channel_id).isdigit():
            channel = guild.get_channel(int(channel_id))
            if channel:
                raw_msg = cfg.get('leave_message', '')
                formatted_msg = raw_msg.replace('{user.name}', member.name).replace('{server.name}', guild.name).replace('{member_count}', str(guild.member_count))

                # Optional leave banner (dashboard-configured, same as welcome).
                banner_file = None
                if cfg.get('banner', {}).get('leave_enabled', False):
                    try:
                        from cogs.welcome_banner import generate_banner_file
                        banner_file = await generate_banner_file(member, guild, kind='leave')
                    except Exception:
                        banner_file = None

                if cfg.get('leave_embed_enabled', False):
                    color_hex = cfg.get('leave_embed_color', '#f23f43').replace('#', '')
                    color = int(color_hex, 16) if all(c in '0123456789abcdefABCDEF' for c in color_hex) else 0xf23f43
                    title = cfg.get('leave_embed_title', '😢 Member Left').replace('{user.name}', member.name).replace('{server.name}', guild.name)
                    embed = discord.Embed(title=title, description=formatted_msg, color=color)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    if banner_file is not None:
                        embed.set_image(url="attachment://goodbye.png")
                        await channel.send(embed=embed, file=banner_file)
                    else:
                        await channel.send(embed=embed)
                else:
                    if banner_file is not None:
                        await channel.send(content=formatted_msg or None, file=banner_file)
                    else:
                        await channel.send(formatted_msg)

async def setup(bot):
    await bot.add_cog(WelcomerLeave(bot))
