import discord
from discord.ext import commands
import logging
from utils import load, save, doc_lock

RR_FILE = 'reaction_roles.json'
logger = logging.getLogger('reaction_roles')

def get_rr_data(gid: str = None):
    data = load(RR_FILE)
    if not isinstance(data, dict):
        data = {}
    if gid:
        return data.get(str(gid), {})
    return data

def save_rr_data(gid: str, guild_data: dict):
    # RACE FIX: shared with the Flask dashboard (api_reaction_roles_save).
    with doc_lock(RR_FILE):
        all_data = get_rr_data()
        all_data[str(gid)] = guild_data
        save(RR_FILE, all_data)


class RoleButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, emoji: str = None, style: discord.ButtonStyle = discord.ButtonStyle.secondary, mode: str = "toggle"):
        custom_id = f"rr_role_{role_id}_{mode}"
        super().__init__(style=style, label=label, emoji=emoji if emoji else None, custom_id=custom_id)
        self.role_id = role_id
        self.mode = mode  # toggle, add, remove

    async def _reply(self, interaction: discord.Interaction, msg: str):
        # Always answer the interaction so the user never sees "This interaction failed".
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.error(f"RR could not reply to interaction: {e}")

    async def callback(self, interaction: discord.Interaction):
        # Acknowledge immediately so slow role edits never time out (3s limit).
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        guild = interaction.guild
        if not guild:
            await self._reply(interaction, "❌ This can only be used in a server.")
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            member = guild.get_member(member.id) or member

        role = guild.get_role(self.role_id)
        if not role:
            await self._reply(interaction, "❌ This role no longer exists. Please ask an admin to re-create the panel.")
            return

        # The bot needs the Manage Roles permission at all.
        if not guild.me.guild_permissions.manage_roles:
            await self._reply(interaction, "❌ The bot is missing the **Manage Roles** permission. Ask an admin to enable it.")
            logger.warning(f"RR: missing Manage Roles permission in guild {guild.id}")
            return

        # The target role must be BELOW the bot's highest role.
        if guild.me.top_role.position <= role.position:
            await self._reply(interaction, f"❌ The bot cannot manage **{role.name}** because that role is higher than (or equal to) the bot's top role. Move the bot's role above it in Server Settings → Roles.")
            logger.warning(f"RR: role hierarchy too low for role {role.id} in guild {guild.id}")
            return

        try:
            if role in member.roles:
                if self.mode in ("toggle", "remove"):
                    await member.remove_roles(role, reason="Reaction Role Button")
                    await self._reply(interaction, f"🗑️ Removed **{role.name}** role from you.")
                else:
                    await self._reply(interaction, f"ℹ️ You already have the **{role.name}** role.")
            else:
                if self.mode in ("toggle", "add"):
                    await member.add_roles(role, reason="Reaction Role Button")
                    await self._reply(interaction, f"✅ Added **{role.name}** role to you!")
                else:
                    await self._reply(interaction, f"ℹ️ You do not have the **{role.name}** role.")
        except discord.Forbidden:
            await self._reply(interaction, f"❌ The bot is not allowed to change **{role.name}** (missing permission or role is above the bot).")
            logger.warning(f"RR: Forbidden editing role {role.id} in guild {guild.id}")
        except Exception as e:
            await self._reply(interaction, f"❌ Failed to update role: {e}")
            logger.error(f"RR: error editing role {role.id} in guild {guild.id}: {e}")


class DynamicRoleView(discord.ui.View):
    def __init__(self, buttons_cfg: list):
        super().__init__(timeout=None)
        style_map = {
            'primary': discord.ButtonStyle.primary,
            'secondary': discord.ButtonStyle.secondary,
            'success': discord.ButtonStyle.success,
            'danger': discord.ButtonStyle.danger
        }
        for btn in buttons_cfg:
            r_id = int(btn['role_id'])
            lbl = btn.get('label') or 'Role'
            em = btn.get('emoji') or None
            st = style_map.get(btn.get('style', 'secondary'), discord.ButtonStyle.secondary)
            mode = btn.get('mode', 'toggle')
            self.add_item(RoleButton(role_id=r_id, label=lbl, emoji=em, style=st, mode=mode))


class ReactionRolesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # Register persistent views for all saved panels across all guilds
        all_data = get_rr_data()
        count = 0
        for gid, gdata in all_data.items():
            panels = gdata.get('panels', {})
            for pid, pdata in panels.items():
                buttons = pdata.get('buttons', [])
                if buttons:
                    try:
                        view = DynamicRoleView(buttons)
                        self.bot.add_view(view)
                        count += 1
                    except Exception as e:
                        logger.error(f"Error loading RR persistent view {pid}: {e}")
        logger.info(f"Loaded {count} persistent Reaction Role views.")


async def setup(bot):
    await bot.add_cog(ReactionRolesCog(bot))
