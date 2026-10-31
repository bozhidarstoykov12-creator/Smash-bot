import discord
from discord.ext import commands
import logging
import asyncio
from datetime import datetime, timezone
from utils import load, save, log_audit_event, doc_lock

FORMS_FILE = 'forms.json'
logger = logging.getLogger('forms')

def get_forms_data(gid: str = None):
    data = load(FORMS_FILE)
    if not isinstance(data, dict):
        data = {}
    if gid:
        return data.get(str(gid), {})
    return data

def save_forms_data(gid: str, guild_data: dict):
    # RACE FIX: shared with the Flask dashboard (api_forms_save).
    with doc_lock(FORMS_FILE):
        all_data = get_forms_data()
        all_data[str(gid)] = guild_data
        save(FORMS_FILE, all_data)


class ApplicationModal(discord.ui.Modal):
    def __init__(self, form_id: str, form_title: str, questions: list, review_channel_id: int):
        super().__init__(title=form_title[:45])
        self.form_id = form_id
        self.review_channel_id = review_channel_id
        self.question_items = questions[:5]  # Discord modal limit is 5 inputs
        self.inputs = []

        for i, q in enumerate(self.question_items):
            style = discord.TextStyle.paragraph if q.get('type') == 'paragraph' else discord.TextStyle.short
            ti = discord.ui.TextInput(
                label=q.get('label', f"Question {i+1}")[:45],
                placeholder=q.get('placeholder', '')[:100],
                required=q.get('required', True),
                style=style,
                max_length=q.get('max_length', 1000)
            )
            self.inputs.append((q.get('label', f"Question {i+1}"), ti))
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ This can only be submitted within a server.", ephemeral=True)
            return

        review_chan = guild.get_channel(self.review_channel_id)
        if not review_chan:
            await interaction.response.send_message("❌ Review channel is not configured correctly.", ephemeral=True)
            return

        await interaction.response.send_message("✅ Вашата кандидатура беше изпратена успешно за преглед!", ephemeral=True)

        # 1. Header message in review channel
        header_em = discord.Embed(
            title=f"📋 Нова кандидатура: {self.title}",
            description=f"**Кандидат:** {interaction.user.mention} (`{interaction.user.name}`)\n**Потребителско ID:** `{interaction.user.id}`\n**Дата:** <t:{int(datetime.now(timezone.utc).timestamp())}:F>",
            color=discord.Color.blurple()
        )
        header_em.set_thumbnail(url=interaction.user.display_avatar.url)
        await review_chan.send(embed=header_em)

        # 2. Send each question and answer one by one as requested!
        for idx, (q_label, ti) in enumerate(self.inputs, start=1):
            q_em = discord.Embed(
                title=f"Въпрос {idx}: {q_label}",
                description=ti.value.strip() or "*(няма отговор)*",
                color=discord.Color.dark_embed()
            )
            await review_chan.send(embed=q_em)
            await asyncio.sleep(0.3)  # clean sequential pacing

        # 3. Decision control buttons
        view = ReviewActionView(applicant_id=interaction.user.id, form_id=self.form_id)
        decision_em = discord.Embed(
            title="⚖️ Модераторско решение",
            description="Прегледайте отговорите по-горе и изберете действие за този кандидат:",
            color=discord.Color.gold()
        )
        await review_chan.send(embed=decision_em, view=view)


class ReviewActionView(discord.ui.View):
    def __init__(self, applicant_id: int, form_id: str):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.form_id = form_id

    @discord.ui.button(label="Одобри (Approve)", style=discord.ButtonStyle.success, emoji="✅", custom_id="app_btn_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = guild.get_member(self.applicant_id)
        
        # Check form reward role
        fdata = get_forms_data(str(guild.id)).get('forms', {}).get(self.form_id, {})
        reward_role_id = fdata.get('accepted_role_id')
        role_note = ""
        if reward_role_id and member:
            r = guild.get_role(int(reward_role_id))
            if r and guild.me.top_role.position > r.position:
                await member.add_roles(r, reason="Application Approved")
                role_note = f"\nДадена роля: **{r.name}**"

        # Notify applicant
        if member:
            try:
                await member.send(f"🎉 Здравейте! Вашата кандидатура за **{fdata.get('title', 'Екип')}** в **{guild.name}** беше **ОДОБРЕНА** от {interaction.user.mention}!{role_note}")
            except Exception:
                pass

        # Disable buttons
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"✅ **Одобрено** от {interaction.user.mention} на <t:{int(datetime.now(timezone.utc).timestamp())}:R>.{role_note}", view=self)

    @discord.ui.button(label="Отхвърли (Deny)", style=discord.ButtonStyle.danger, emoji="❌", custom_id="app_btn_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = guild.get_member(self.applicant_id)
        fdata = get_forms_data(str(guild.id)).get('forms', {}).get(self.form_id, {})

        if member:
            try:
                await member.send(f"ℹ️ Здравейте. Вашата кандидатура за **{fdata.get('title', 'Екип')}** в **{guild.name}** беше **ОТХВЪРЛЕНА**.")
            except Exception:
                pass

        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"❌ **Отхвърлено** от {interaction.user.mention} на <t:{int(datetime.now(timezone.utc).timestamp())}:R>.", view=self)


class FormTriggerButton(discord.ui.Button):
    def __init__(self, form_id: str, label: str, emoji: str = None, style: discord.ButtonStyle = discord.ButtonStyle.primary):
        super().__init__(label=label, emoji=emoji, style=style, custom_id=f"form_open_{form_id}")
        self.form_id = form_id

    async def callback(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        fdata = get_forms_data(gid).get('forms', {}).get(self.form_id)
        if not fdata:
            await interaction.response.send_message("❌ Този формуляр вече не е активен.", ephemeral=True)
            return

        modal = ApplicationModal(
            form_id=self.form_id,
            form_title=fdata.get('title', 'Application'),
            questions=fdata.get('questions', []),
            review_channel_id=int(fdata.get('review_channel_id', 0))
        )
        await interaction.response.send_modal(modal)


class FormTriggerView(discord.ui.View):
    def __init__(self, form_id: str, label: str, emoji: str = None, style: discord.ButtonStyle = discord.ButtonStyle.primary):
        super().__init__(timeout=None)
        self.add_item(FormTriggerButton(form_id, label, emoji, style))


class FormsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        all_data = get_forms_data()
        count = 0
        for gid, gdata in all_data.items():
            forms = gdata.get('forms', {})
            for fid, f in forms.items():
                try:
                    style_map = {
                        'primary': discord.ButtonStyle.primary,
                        'secondary': discord.ButtonStyle.secondary,
                        'success': discord.ButtonStyle.success,
                        'danger': discord.ButtonStyle.danger
                    }
                    st = style_map.get(f.get('btn_style', 'primary'), discord.ButtonStyle.primary)
                    view = FormTriggerView(fid, f.get('btn_label', 'Кандидатствай'), f.get('btn_emoji') or None, st)
                    self.bot.add_view(view)
                    count += 1
                except Exception as e:
                    logger.error(f"Error registering form view {fid}: {e}")
        logger.info(f"Loaded {count} persistent Form views.")


async def setup(bot):
    await bot.add_cog(FormsCog(bot))
