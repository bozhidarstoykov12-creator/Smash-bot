import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from utils import load, save, ok, err, info, medal

class Story(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Message listener ───────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.author.bot or not msg.guild: return
        sd = load('story.json')
        gid = str(msg.guild.id)
        if gid not in sd: return
        d = sd[gid]
        if not d.get('enabled') or not d.get('channel') or msg.channel.id != d['channel']: return

        uid = str(msg.author.id)
        content = msg.content.strip()
        d['user_stats'].setdefault(uid, {'words': 0, 'violations': 0})

        is_one_word = (
            len(content.split()) == 1
            and '\n' not in content
            and len(content) >= 1
            and not content.startswith('/')
        )

        # Violation: more than one word
        if not is_one_word:
            d['user_stats'][uid]['violations'] += 1
            sd[gid] = d
            save('story.json', sd)
            try: await msg.delete()
            except: pass
            em = discord.Embed(title="❌ One Word Only!", description=f"{msg.author.mention} you can only send **one word** at a time!", color=discord.Color.red())
            em.set_footer(text=f"Violations: {d['user_stats'][uid]['violations']}")
            w = await msg.channel.send(embed=em)
            await asyncio.sleep(5)
            await w.delete()
            return

        # Violation: same user twice in a row
        if d.get('last_user') == uid:
            d['user_stats'][uid]['violations'] += 1
            sd[gid] = d
            save('story.json', sd)
            try: await msg.delete()
            except: pass
            em = discord.Embed(title="❌ Wait for Someone Else!", description=f"{msg.author.mention} you can't send **two words in a row**!", color=discord.Color.red())
            em.set_footer(text=f"Violations: {d['user_stats'][uid]['violations']}")
            w = await msg.channel.send(embed=em)
            await asyncio.sleep(5)
            await w.delete()
            return

        # ✅ Valid word
        d.setdefault('words', [])
        d['words'].append(content)
        d['last_user'] = uid
        d['user_stats'][uid]['words'] += 1
        sd[gid] = d
        save('story.json', sd)

        wc = len(d['words'])
        await msg.add_reaction("✅")
        if wc % 100 == 0:
            await msg.channel.send(embed=discord.Embed(title=f"🎉 {wc} Words!", description=f"The story is **{wc} words** long! Keep going! 📖", color=discord.Color.gold()))
        elif wc % 50 == 0:
            await msg.add_reaction("🔥")

    # ══════════════════════════════════════════════════════
    #  SLASH COMMANDS — /story group
    # ══════════════════════════════════════════════════════
    story = app_commands.Group(name="story", description="One Word Story commands")

    @story.command(name="setchannel", description="Set the One Word Story channel")
    @app_commands.describe(channel="The channel for the story")
    @app_commands.default_permissions(manage_channels=True)
    async def setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        data = load('story.json')
        gid = str(interaction.guild.id)
        data.setdefault(gid, {})
        data[gid].update({'channel': channel.id, 'enabled': True,
                          'words': data[gid].get('words', []),
                          'last_user': data[gid].get('last_user'),
                          'user_stats': data[gid].get('user_stats', {})})
        save('story.json', data)
        await interaction.response.send_message(embed=ok("Story Channel Set!", f"One Word Story → {channel.mention}"))
        await channel.send(embed=discord.Embed(title="📖 One Word Story — START!", description="Build a story together, **one word at a time**! 🖊️", color=discord.Color.purple()))

    @story.command(name="enable", description="Enable the story")
    @app_commands.default_permissions(manage_channels=True)
    async def enable(self, interaction: discord.Interaction):
        data = load('story.json')
        gid = str(interaction.guild.id)
        if not data.get(gid, {}).get('channel'):
            return await interaction.response.send_message(embed=err("Set a channel first: `/story setchannel`"))
        data[gid]['enabled'] = True
        save('story.json', data)
        await interaction.response.send_message(embed=ok("Story Enabled!"))

    @story.command(name="disable", description="Disable the story")
    @app_commands.default_permissions(manage_channels=True)
    async def disable(self, interaction: discord.Interaction):
        data = load('story.json')
        gid = str(interaction.guild.id)
        data.setdefault(gid, {})['enabled'] = False
        save('story.json', data)
        await interaction.response.send_message(embed=ok("Story Disabled."))

    @story.command(name="show", description="Show the current story")
    async def show(self, interaction: discord.Interaction):
        data = load('story.json')
        gid = str(interaction.guild.id)
        words = data.get(gid, {}).get('words', [])
        if not words: return await interaction.response.send_message(embed=err("The story hasn't started yet!"))
        chunks = [' '.join(words[i:i+60]) for i in range(0, len(words), 60)]
        em = discord.Embed(title=f"📖 The Story So Far... ({len(words)} words)", color=discord.Color.purple())
        for i, chunk in enumerate(chunks):
            em.add_field(name=f"Part {i+1}" if len(chunks) > 1 else "\u200b", value=chunk, inline=False)
        em.set_footer(text="Keep adding words!")
        await interaction.response.send_message(embed=em)

    @story.command(name="reset", description="Reset the story (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction):
        data = load('story.json')
        gid = str(interaction.guild.id)
        old = len(data.get(gid, {}).get('words', []))
        data.setdefault(gid, {})
        data[gid]['words'] = []
        data[gid]['last_user'] = None
        save('story.json', data)
        await interaction.response.send_message(embed=ok("Story Reset!", f"Old story was **{old} words**. New one can begin! 🖊️"))

    @story.command(name="stats", description="View story stats for a user")
    @app_commands.describe(member="Who to check")
    async def stats(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        data = load('story.json')
        gid = str(interaction.guild.id)
        s = data.get(gid, {}).get('user_stats', {}).get(str(member.id), {'words': 0, 'violations': 0})
        total = len(data.get(gid, {}).get('words', []))
        em = discord.Embed(title=f"📊 Story Stats — {member.display_name}", color=discord.Color.purple())
        em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(name="📝 Words",      value=s['words'])
        em.add_field(name="⚠️ Violations", value=s['violations'])
        if total > 0:
            em.add_field(name="📊 Contribution", value=f"{round(s['words']/total*100,1)}%")
        await interaction.response.send_message(embed=em)

    @story.command(name="lb", description="Story leaderboard")
    async def lb(self, interaction: discord.Interaction):
        data = load('story.json')
        gid = str(interaction.guild.id)
        stats = data.get(gid, {}).get('user_stats', {})
        if not stats: return await interaction.response.send_message(embed=err("No story data yet!"))
        sorted_users = sorted(stats.items(), key=lambda x: x[1]['words'], reverse=True)[:10]
        em = discord.Embed(title="📖 Story Leaderboard", color=discord.Color.purple())
        desc = ""
        for i, (uid, s) in enumerate(sorted_users):
            user = self.bot.get_user(int(uid))
            name = user.display_name if user else f"User …{uid[-4:]}"
            desc += f"{medal(i)} **{name}** — {s['words']} words\n"
        em.description = desc
        await interaction.response.send_message(embed=em)

    @story.command(name="info", description="Story channel info")
    async def s_info(self, interaction: discord.Interaction):
        data = load('story.json')
        gid = str(interaction.guild.id)
        d = data.get(gid, {})
        ch = self.bot.get_channel(d.get('channel'))
        words = d.get('words', [])
        em = discord.Embed(title="📖 Story Info", color=discord.Color.purple())
        em.add_field(name="📍 Channel", value=ch.mention if ch else "Not set")
        em.add_field(name="✅ Enabled", value="Yes" if d.get('enabled') else "No")
        em.add_field(name="📝 Words",   value=len(words))
        if words:
            em.add_field(name="📜 Last 10 words", value=f"*...{' '.join(words[-10:])}*", inline=False)
        await interaction.response.send_message(embed=em)

async def setup(bot):
    await bot.add_cog(Story(bot))
