import discord
from discord.ext import commands
from discord import app_commands
import time
from utils import load, save, ok, err, info, rate_limit_wait, doc_lock

SUGGESTIONS_FILE = 'suggestions.json'


def get_cfg(gid: str) -> dict:
    cfg = load('config.json').get(gid, {})
    s = cfg.get('suggestions', {})
    return {
        'enabled': s.get('enabled', False),
        'channel': s.get('channel', ''),
    }


def get_suggestions(gid: str) -> dict:
    data = load(SUGGESTIONS_FILE)
    return data.setdefault(gid, {'next_id': 1, 'items': {}})


# ══════════════════════════════════════════════════════════
#  MODAL + PERSISTENT BUTTON PANEL
#  Members can submit either way:
#    • /suggest idea:"..."            — the classic slash command
#    • a "💡 Suggest" button that opens a pop-up form (modal)
#  An admin drops the button panel into a channel with /suggestpanel.
# ══════════════════════════════════════════════════════════

SUGGEST_BTN_CUSTOM_ID = "suggest_panel_btn"


class SuggestModal(discord.ui.Modal, title="Submit a Suggestion"):
    """Pop-up form shown when a member clicks the Suggest button."""

    idea = discord.ui.TextInput(
        label="What's your suggestion?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your idea for the server…",
        required=True,
        max_length=800,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Suggestions")
        if cog is None:
            return await interaction.response.send_message(
                embed=err("Suggestions are temporarily unavailable — try again in a moment."), ephemeral=True)
        await cog.submit_suggestion(interaction, str(self.idea.value))


class SuggestButtonView(discord.ui.View):
    """Persistent one-button panel. A single static custom_id means one
    add_view() call covers every panel and survives bot restarts."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Suggest", emoji="💡", style=discord.ButtonStyle.primary,
                       custom_id=SUGGEST_BTN_CUSTOM_ID)
    async def suggest_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SuggestModal())


class Suggestions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── shared submit logic, used by BOTH the slash command and the button modal ──
    async def submit_suggestion(self, interaction: discord.Interaction, idea: str):
        gid = str(interaction.guild.id)
        cfg = get_cfg(gid)

        # Defer early (ephemeral) so slow channel sends never trip the 3s limit.
        await interaction.response.defer(ephemeral=True)

        if not cfg['enabled'] or not str(cfg['channel']).isdigit():
            return await interaction.followup.send(
                embed=err("Suggestions aren't set up on this server yet — an admin needs to configure a channel on the dashboard."),
                ephemeral=True)

        channel = interaction.guild.get_channel(int(cfg['channel']))
        if not channel:
            return await interaction.followup.send(
                embed=err("The suggestions channel is missing — ask an admin to reconfigure it."), ephemeral=True)

        idea = (idea or "").strip()
        if not idea:
            return await interaction.followup.send(embed=err("Your suggestion can't be empty."), ephemeral=True)
        if len(idea) > 800:
            return await interaction.followup.send(embed=err("Keep your suggestion under 800 characters."), ephemeral=True)

        # RACE FIX: allocate the suggestion ID synchronously under the document
        # lock (before any network awaits) — parallel submits can no longer
        # collide on the same ID or drop each other's entries.
        with doc_lock(SUGGESTIONS_FILE):
            data = load(SUGGESTIONS_FILE)
            g = data.setdefault(gid, {'next_id': 1, 'items': {}})
            sid = str(g['next_id'])
            g['next_id'] += 1
            g['items'][sid] = {
                'text': idea,
                'author_id': str(interaction.user.id),
                'author_name': str(interaction.user),
                'message_id': None,
                'channel_id': channel.id,
                'status': 'pending',
                'created_ts': time.time(),
            }
            save(SUGGESTIONS_FILE, data)

        embed = discord.Embed(title=f"💡 Suggestion #{sid}", description=idea, color=discord.Color.blurple())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text="🕓 Pending review — vote with 👍 / 👎")

        await rate_limit_wait("send_message", channel.id)
        msg = await channel.send(embed=embed)
        try:
            await msg.add_reaction("👍")
            await msg.add_reaction("👎")
        except Exception:
            pass

        # Second sync write: attach the posted message id (still lock-guarded).
        with doc_lock(SUGGESTIONS_FILE):
            data = load(SUGGESTIONS_FILE)
            data.setdefault(gid, {'next_id': 1, 'items': {}})['items'].setdefault(sid, {})['message_id'] = msg.id
            save(SUGGESTIONS_FILE, data)

        await interaction.followup.send(
            embed=ok("Suggestion Submitted!", f"Your suggestion **#{sid}** has been posted in {channel.mention} for the community to vote on."),
            ephemeral=True)

    @app_commands.command(name="suggest", description="Submit a suggestion for the server")
    @app_commands.describe(idea="What's your suggestion?")
    async def suggest(self, interaction: discord.Interaction, idea: str):
        await self.submit_suggestion(interaction, idea)

    @app_commands.command(name="suggestpanel", description="Post a button members can click to submit a suggestion")
    @app_commands.describe(channel="Where to post the panel (defaults to this channel)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def suggestpanel(self, interaction: discord.Interaction,
                           channel: discord.TextChannel = None):
        target = channel or interaction.channel
        cfg = get_cfg(str(interaction.guild.id))

        embed = discord.Embed(
            title="💡 Got an idea?",
            description=("Click the button below to submit a suggestion for the server.\n"
                         "You can also use the **/suggest** command anytime."),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Suggestions are reviewed by the staff team.")

        try:
            await target.send(embed=embed, view=SuggestButtonView())
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=err(f"I can't post in {target.mention} — check my permissions there."), ephemeral=True)

        note = "" if (cfg['enabled'] and str(cfg['channel']).isdigit()) else \
            "\n\n⚠️ Heads up: suggestions aren't enabled yet. Turn them on and pick a channel on the dashboard, or the button will tell members it's not set up."
        await interaction.response.send_message(
            embed=ok("Panel Posted!", f"The suggestion button is now live in {target.mention}.{note}"),
            ephemeral=True)

    @suggestpanel.error
    async def suggestpanel_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                embed=err("You need the **Manage Server** permission to post a suggestion panel."), ephemeral=True)

    async def resolve(self, gid: str, sid: str, status: str, reviewer: str) -> bool:
        """Approve/deny a suggestion — updates storage and edits the live Discord message if possible."""
        # RACE FIX: locked RMW — dashboard reads/writes suggestions.json and a
        # parallel resolve can't drop another reviewer's change.
        with doc_lock(SUGGESTIONS_FILE):
            data = load(SUGGESTIONS_FILE)
            g = data.get(gid, {})
            item = g.get('items', {}).get(sid)
            if item:
                item['status'] = status
                item['reviewed_by'] = reviewer
                save(SUGGESTIONS_FILE, data)

        if not item:
            return False

        guild = self.bot.get_guild(int(gid))
        if guild:
            channel = guild.get_channel(item.get('channel_id'))
            if channel:
                try:
                    msg = await channel.fetch_message(item['message_id'])
                    embed = msg.embeds[0] if msg.embeds else discord.Embed(title=f"💡 Suggestion #{sid}", description=item['text'])
                    if status == 'approved':
                        embed.color = discord.Color.green()
                        embed.set_footer(text=f"✅ Approved by {reviewer}")
                    else:
                        embed.color = discord.Color.red()
                        embed.set_footer(text=f"❌ Denied by {reviewer}")
                    await msg.edit(embed=embed)
                except Exception:
                    pass
        return True


async def setup(bot):
    await bot.add_cog(Suggestions(bot))
    # Register the persistent button view once so the panel keeps working
    # across bot restarts (static custom_id, no per-message state needed).
    try:
        bot.add_view(SuggestButtonView())
    except Exception:
        pass
