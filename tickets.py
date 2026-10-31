import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import re
import time
import uuid
import io
from datetime import datetime, timezone

def _push_ticket_update(gid, event, payload):
    """Best-effort live-dashboard push — tickets must work even if the
    dashboard/WebSocket layer is unavailable for any reason."""
    try:
        from dashboard import push_update
        push_update(gid, event, payload)
    except Exception as e:
        print(f"[Tickets] Live dashboard push failed (ticket still works fine): {e}")

TICKETS_FILE = "tickets.json"

# ══════════════════════════════════════════════════════════
#  DATA LAYER
# ══════════════════════════════════════════════════════════

def _load() -> dict:
    from utils import load
    return load(TICKETS_FILE)


def _save(data: dict):
    from utils import save
    save(TICKETS_FILE, data)


def get_guild_data(gid: str) -> dict:
    """Return (and lazily create) the tickets blob for a guild."""
    data = _load()
    g = data.setdefault(gid, {})
    g.setdefault("manager_roles", [])
    g.setdefault("embeds_color", "#f45142")
    g.setdefault("cooldown_seconds", 10)
    g.setdefault("panels", {})            # name -> panel config
    g.setdefault("transcript_setups", []) # list of {affected_panels, mode, theme, permissions}
    g.setdefault("limit_setups", [])      # list of {affected_panels, max_open, max_active, max_open_user, max_active_user}
    g.setdefault("automation_setups", []) # list of {affected_panels, action_on_leave, auto_close, auto_delete, ...}
    g.setdefault("create_command_enabled", False)
    g.setdefault("tickets", {})           # ticket_id -> ticket record
    g.setdefault("panel_counts", {})      # panel name -> int (sequential per-panel counter)
    g.setdefault("total_count", 0)
    g.setdefault("commands", {           # toggle each /ticket subcommand on/off
        "setup": True, "create": True, "close": True, "reopen": True,
        "delete": True, "add": True, "remove": True, "automation": True,
        "update_counts": True,
    })
    return g


def save_guild_data(gid: str, g: dict):
    data = _load()
    data[gid] = g
    _save(data)


def new_ticket_id() -> str:
    return uuid.uuid4().hex[:10]


# ══════════════════════════════════════════════════════════
#  PLACEHOLDER FORMATTING
# ══════════════════════════════════════════════════════════

def format_panel_count(panel_count: int, spec: str | None) -> str:
    """Handle {panel_count} / {panel_count[N]} zero-padding syntax."""
    if spec:
        try:
            digits = int(spec)
            return str(panel_count).zfill(digits)
        except ValueError:
            pass
    return str(panel_count)


_VAR_PATTERN = re.compile(r"\{(\w+)(?:\[(\d+)\])?\}")


def format_vars(text: str, *, user: discord.Member | discord.User | None = None,
                 server: discord.Guild | None = None, panel_count: int = 0,
                 total_count: int = 0, ticket_name: str = "", ticket_id: str = "",
                 ping: str = "", controls: str = "", form: str = "") -> str:
    if not text:
        return ""

    def _sub(m: re.Match) -> str:
        key, digits = m.group(1), m.group(2)
        if key == "user" and user is not None:
            return user.mention
        if key == "user_name" and user is not None:
            return user.name
        if key == "user_id" and user is not None:
            return str(user.id)
        if key == "server" and server is not None:
            return server.name
        if key == "panel_count":
            return format_panel_count(panel_count, digits)
        if key == "total_count":
            return format_panel_count(total_count, digits)
        if key == "controls":
            return controls
        if key == "form":
            return form
        if key == "ticket":
            return f"#{ticket_name}" if ticket_name else ""
        if key == "ticket_name":
            return ticket_name
        if key == "ticket_id":
            return ticket_id
        if key == "ping":
            return ping
        return m.group(0)  # leave unknown placeholders untouched

    return _VAR_PATTERN.sub(_sub, text)


def safe_channel_name(text: str) -> str:
    """Discord channel names: lowercase, no spaces (dashes instead), limited charset, <=100 chars."""
    text = text.lower().strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9\-_]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return (text or "ticket")[:90]


def parse_color(hex_str: str, default: int = 0xF45142) -> int:
    try:
        return int((hex_str or "").lstrip("#"), 16)
    except (ValueError, TypeError):
        return default


def parse_id_list(raw) -> list[int]:
    if isinstance(raw, list):
        items = raw
    else:
        items = re.split(r"[,\s]+", str(raw or ""))
    out = []
    for item in items:
        item = str(item).strip().strip("<@&#!>")
        if item.isdigit():
            out.append(int(item))
    return out


# ══════════════════════════════════════════════════════════
#  PANEL LOOKUP HELPERS
# ══════════════════════════════════════════════════════════

def find_panel_for_ticket(g: dict, ticket: dict) -> dict | None:
    return g.get("panels", {}).get(ticket.get("panel_name", ""))


def find_setup_for_panel(setups: list[dict], panel_name: str) -> dict | None:
    """First setup whose affected_panels list includes panel_name (or is empty = applies to all)."""
    for s in setups:
        affected = s.get("affected_panels") or []
        if not affected or panel_name in affected:
            return s
    return None


# ══════════════════════════════════════════════════════════
#  MODALS
# ══════════════════════════════════════════════════════════

class TicketFormModal(discord.ui.Modal):
    """Dynamic form shown before ticket creation, if the panel has 'send_form' enabled."""

    def __init__(self, cog: "Tickets", panel_name: str, panel: dict):
        super().__init__(title=panel.get("form_title", "Create a Ticket")[:45])
        self.cog = cog
        self.panel_name = panel_name
        self.panel = panel
        self.fields: list[discord.ui.TextInput] = []
        questions = panel.get("form_questions") or [{"label": "What do you need help with?", "style": "short"}]
        for q in questions[:5]:  # Discord modals cap at 5 components
            style = discord.TextStyle.paragraph if q.get("style") == "paragraph" else discord.TextStyle.short
            ti = discord.ui.TextInput(
                label=q.get("label", "Question")[:45],
                style=style,
                required=q.get("required", True),
                max_length=1000,
                placeholder=q.get("placeholder", "")[:100] or None,
            )
            self.fields.append(ti)
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        answers = {ti.label: ti.value for ti in self.fields}
        await self.cog.create_ticket(interaction, self.panel_name, self.panel, form_answers=answers)


# ══════════════════════════════════════════════════════════
#  PERSISTENT VIEWS
# ══════════════════════════════════════════════════════════

class PanelButtonView(discord.ui.View):
    """One persistent button per panel. custom_id encodes the panel name so it
    survives bot restarts without needing to re-register per-message state."""

    def __init__(self, cog: "Tickets", panel_name: str, panel: dict):
        super().__init__(timeout=None)
        label = panel.get("button_label", "Create a Ticket")[:80]
        color_map = {
            "Blue": discord.ButtonStyle.primary, "Grey": discord.ButtonStyle.secondary,
            "Gray": discord.ButtonStyle.secondary, "Green": discord.ButtonStyle.success,
            "Red": discord.ButtonStyle.danger,
        }
        style = color_map.get(panel.get("button_color", "Blue"), discord.ButtonStyle.primary)
        emoji = panel.get("emoji") or None
        btn = discord.ui.Button(
            label=label, style=style, emoji=emoji,
            custom_id=f"ticket_panel_btn::{panel_name}",
        )
        btn.callback = self._on_click
        self.add_item(btn)
        self.panel_name = panel_name

    async def _on_click(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.get_cog("Tickets")
        await cog.handle_panel_trigger(interaction, self.panel_name)


class PanelSelectView(discord.ui.View):
    """Select-menu layout variant — one option per connected panel."""

    def __init__(self, cog: "Tickets", panel_names: list[str], g: dict, base_panel_name: str):
        super().__init__(timeout=None)
        base_panel = g["panels"].get(base_panel_name, {})
        options = []
        for pname in panel_names[:25]:
            p = g["panels"].get(pname, {})
            options.append(discord.SelectOption(
                label=p.get("button_label", pname)[:100],
                value=pname,
                emoji=p.get("emoji") or None,
            ))
        sel = discord.ui.Select(
            placeholder=base_panel.get("button_label", "Create a Ticket")[:100],
            options=options,
            custom_id=f"ticket_panel_select::{base_panel_name}",
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.get_cog("Tickets")
        chosen = interaction.data["values"][0]
        await cog.handle_panel_trigger(interaction, chosen)


class TicketControlView(discord.ui.View):
    """Posted inside every ticket channel: Close / Close-with-reason / Reopen / Delete controls."""

    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger,
                        emoji="🔒", custom_id="ticket_ctrl::close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Tickets = interaction.client.get_cog("Tickets")
        await cog.close_ticket(interaction, reason="Closed via control panel")


class ClosedTicketControlView(discord.ui.View):
    """Posted after a ticket is closed (Two-Step Close): Reopen / Delete."""

    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)

    @discord.ui.button(label="Reopen Ticket", style=discord.ButtonStyle.success,
                        emoji="🔓", custom_id="ticket_ctrl::reopen")
    async def reopen_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Tickets = interaction.client.get_cog("Tickets")
        await cog.reopen_ticket(interaction)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.danger,
                        emoji="🗑️", custom_id="ticket_ctrl::delete")
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Tickets = interaction.client.get_cog("Tickets")
        await cog.delete_ticket(interaction)


# ══════════════════════════════════════════════════════════
#  MAIN COG
# ══════════════════════════════════════════════════════════

class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}  # (guild_id, user_id) -> last_create_ts
        self.automation_loop.start()

    def cog_unload(self):
        self.automation_loop.cancel()

    async def cog_load(self):
        # Re-register persistent views for every existing panel/control so
        # buttons keep working across restarts, without needing per-message data.
        data = _load()
        for gid, g in data.items():
            for pname, panel in g.get("panels", {}).items():
                try:
                    if panel.get("layout") == "select":
                        connected = [pname] + (panel.get("connected_panels") or [])
                        self.bot.add_view(PanelSelectView(self, connected, g, pname))
                    else:
                        self.bot.add_view(PanelButtonView(self, pname, panel))
                except Exception:
                    pass
        self.bot.add_view(TicketControlView(self))
        self.bot.add_view(ClosedTicketControlView(self))

    # ── Permission helpers ──────────────────────────────────

    def is_manager(self, member: discord.Member, g: dict) -> bool:
        if member.guild_permissions.administrator:
            return True
        role_ids = set(parse_id_list(g.get("manager_roles", [])))
        return any(r.id in role_ids for r in member.roles)

    def is_support(self, member: discord.Member, panel: dict) -> bool:
        if member.guild_permissions.administrator:
            return True
        role_ids = set(parse_id_list(panel.get("support_roles", [])))
        return any(r.id in role_ids for r in member.roles)

    # ── Panel trigger (button or select chosen) ─────────────

    async def handle_panel_trigger(self, interaction: discord.Interaction, panel_name: str):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        panel = g["panels"].get(panel_name)
        if not panel:
            await interaction.response.send_message("⚠️ This panel no longer exists.", ephemeral=True)
            return

        member = interaction.user
        blacklisted = set(parse_id_list(panel.get("blacklisted_roles", [])))
        if any(r.id in blacklisted for r in member.roles):
            await interaction.response.send_message(
                "🚫 You're not allowed to create a ticket from this panel.", ephemeral=True)
            return

        # Cooldown check
        cooldown = max(10, int(g.get("cooldown_seconds", 10)))
        key = (interaction.guild_id, member.id)
        now = time.time()
        last = self._cooldowns.get(key, 0)
        if now - last < cooldown:
            wait = int(cooldown - (now - last))
            await interaction.response.send_message(
                f"⏳ Please wait {wait}s before creating another ticket.", ephemeral=True)
            return

        # Limits check
        limit_err = self._check_limits(g, panel_name, member.id)
        if limit_err:
            await interaction.response.send_message(f"🚫 {limit_err}", ephemeral=True)
            return

        if panel.get("send_form"):
            await interaction.response.send_modal(TicketFormModal(self, panel_name, panel))
            return

        await self.create_ticket(interaction, panel_name, panel)

    def _check_limits(self, g: dict, panel_name: str, user_id: int) -> str | None:
        setup = find_setup_for_panel(g.get("limit_setups", []), panel_name)
        if not setup:
            return None
        tickets = g.get("tickets", {})
        panel_open = [t for t in tickets.values() if t["panel_name"] == panel_name and t["status"] == "open"]
        panel_active = [t for t in tickets.values() if t["panel_name"] == panel_name and t["status"] in ("open", "closed")]
        user_open = [t for t in panel_open if t["creator_id"] == user_id]
        user_active = [t for t in panel_active if t["creator_id"] == user_id]

        max_open = int(setup.get("max_open", 0) or 0)
        max_active = int(setup.get("max_active", 0) or 0)
        max_open_user = int(setup.get("max_open_user", 0) or 0)
        max_active_user = int(setup.get("max_active_user", 0) or 0)

        if max_open and len(panel_open) >= max_open:
            return "This panel has reached its maximum number of open tickets."
        if max_active and len(panel_active) >= max_active:
            return "This panel has reached its maximum number of active tickets."
        if max_open_user and len(user_open) >= max_open_user:
            return "You already have the maximum number of open tickets for this panel."
        if max_active_user and len(user_active) >= max_active_user:
            return "You already have the maximum number of active tickets for this panel."
        return None

    # ── Ticket creation ──────────────────────────────────────

    async def create_ticket(self, interaction: discord.Interaction, panel_name: str, panel: dict,
                             form_answers: dict | None = None):
        guild = interaction.guild
        member = interaction.user
        gid = str(guild.id)
        g = get_guild_data(gid)

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        self._cooldowns[(guild.id, member.id)] = time.time()

        # Counters
        g["panel_counts"][panel_name] = g["panel_counts"].get(panel_name, 0) + 1
        g["total_count"] = g.get("total_count", 0) + 1
        panel_count = g["panel_counts"][panel_name]
        total_count = g["total_count"]

        ticket_id = new_ticket_id()
        raw_name = panel.get("ticket_name", "ticket-{panel_count[4]}")
        ticket_name = format_vars(raw_name, user=member, server=guild,
                                   panel_count=panel_count, total_count=total_count)
        channel_name = safe_channel_name(ticket_name)

        open_cat_id = panel.get("open_category")
        category = guild.get_channel(int(open_cat_id)) if str(open_cat_id).isdigit() else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                 read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                   manage_channels=True, read_message_history=True),
        }
        for rid in parse_id_list(panel.get("support_roles", [])):
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True)

        try:
            channel = await guild.create_text_channel(
                name=channel_name, category=category, overwrites=overwrites,
                topic=f"Ticket for {member} ({member.id}) | panel:{panel_name} | id:{ticket_id}",
                reason=f"Ticket created by {member} via panel '{panel_name}'",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to create channels here. Contact a server admin.", ephemeral=True)
            return

        record = {
            "id": ticket_id, "panel_name": panel_name, "channel_id": channel.id,
            "creator_id": member.id, "status": "open", "created_at": int(time.time()),
            "closed_at": None, "ticket_name": ticket_name, "form_answers": form_answers or {},
            "messages": [],  # transcript log: [{author, author_id, content, ts, attachments}]
        }
        g["tickets"][ticket_id] = record
        save_guild_data(gid, g)
        _push_ticket_update(gid, 'ticket_created', {
            'id': ticket_id, 'panel_name': panel_name, 'channel_id': channel.id,
            'creator_name': str(member), 'channel_name': channel.name,
        })

        # Build the {controls} and {form} placeholder content
        controls_text = "Use the buttons below to manage this ticket."
        form_text = ""
        if form_answers:
            form_text = "\n".join(f"**{q}:** {a}" for q, a in form_answers.items())

        support_roles = parse_id_list(panel.get("support_roles", []))
        ping_text = " ".join(f"<@&{rid}>" for rid in support_roles) if panel.get("ping_support") else ""

        raw_msg = panel.get("creation_message",
                             "{user}, welcome to your ticket, the staff team will reach to you shortly.\n\n{form}\n\n{controls}")
        msg_text = format_vars(raw_msg, user=member, server=guild, panel_count=panel_count,
                                total_count=total_count, ticket_name=ticket_name, ticket_id=ticket_id,
                                ping=ping_text, controls=controls_text, form=form_text)

        view = TicketControlView(self)
        content_ping = ping_text if panel.get("ping_support") else None

        if panel.get("creation_embed_enabled", True):
            embed = discord.Embed(
                title=format_vars(panel.get("creation_embed_title", "{user_name}'s Ticket"),
                                   user=member, server=guild),
                description=msg_text,
                color=parse_color(panel.get("creation_embed_color") or g.get("embeds_color", "#f45142")),
            )
            if panel.get("creation_embed_thumbnail"):
                embed.set_thumbnail(url=panel["creation_embed_thumbnail"])
            if panel.get("creation_embed_image"):
                embed.set_image(url=panel["creation_embed_image"])
            if panel.get("creation_embed_footer"):
                embed.set_footer(text=panel["creation_embed_footer"])
            sent = await channel.send(content=content_ping, embed=embed, view=view)
        else:
            sent = await channel.send(content=f"{content_ping or ''}\n{msg_text}".strip(), view=view)

        if g.get("dm_ticket_creator"):
            try:
                await member.send(f"🎫 Your ticket was created: {channel.mention} in **{guild.name}**.")
            except discord.Forbidden:
                pass

        await self._log_event(guild, g, "Ticket Created",
                               f"{member.mention} created {channel.mention} via panel **{panel_name}**.")

        await interaction.followup.send(f"✅ Your ticket has been created: {channel.mention}", ephemeral=True)

    # ── Close / Reopen / Delete ──────────────────────────────

    def _ticket_by_channel(self, g: dict, channel_id: int) -> dict | None:
        for t in g.get("tickets", {}).values():
            if t["channel_id"] == channel_id:
                return t
        return None

    async def close_ticket(self, interaction: discord.Interaction, reason: str = ""):
        guild = interaction.guild
        gid = str(guild.id)
        g = get_guild_data(gid)
        ticket = self._ticket_by_channel(g, interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("⚠️ This isn't a ticket channel.", ephemeral=True)
            return
        panel = g["panels"].get(ticket["panel_name"], {})
        if not (self.is_manager(interaction.user, g) or self.is_support(interaction.user, panel)
                or interaction.user.id == ticket["creator_id"]):
            await interaction.response.send_message("🚫 You can't close this ticket.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer()

        channel = interaction.channel
        two_step = panel.get("two_step_close", True)
        ticket["status"] = "closed"
        ticket["closed_at"] = int(time.time())

        # Generate AI Summary of the ticket conversation if possible
        ai_summary = ""
        msgs = ticket.get("messages", [])
        if len(msgs) >= 2:
            try:
                from utils import load
                cfg = load("config.json").get(gid, {})
                api_key = cfg.get("gemini_api_key") or cfg.get("openai_api_key")
                if api_key:
                    from ai_guard import ask_ai
                    convo = [f"{m.get('author','User')}: {m.get('content','')}" for m in msgs[-20:] if m.get('content')]
                    if convo:
                        prompt = (
                            "Summarize this Discord ticket conversation in 1-2 brief sentences "
                            "(What was the issue/question and how was it handled):\n\n" + "\n".join(convo)
                        )
                        res = await ask_ai([prompt], system="You are an AI support ticket summarizer. Be extremely brief, clear and factual.",
                                           api_key=api_key, provider=cfg.get("ai_provider", "gemini"), model=cfg.get("ai_model"))
                        if res and not res.startswith("❌"):
                            ai_summary = res.strip()
                            ticket["ai_summary"] = ai_summary
            except Exception as _e:
                print(f"[Tickets] AI summary generation error: {_e}")

        save_guild_data(gid, g)
        _push_ticket_update(gid, 'ticket_closed', {
            'id': ticket["id"], 'channel_id': ticket["channel_id"],
            'closed_by': str(interaction.user),
            'ai_summary': ai_summary,
        })

        creator = guild.get_member(ticket["creator_id"])
        if two_step:
            closed_cat_id = panel.get("closed_category")
            closed_cat = guild.get_channel(int(closed_cat_id)) if str(closed_cat_id).isdigit() else None
            try:
                if creator:
                    await channel.set_permissions(creator, view_channel=False)
                if closed_cat:
                    await channel.edit(category=closed_cat, reason=f"Ticket closed by {interaction.user}")
            except discord.Forbidden:
                pass

            close_desc = f"🔒 Ticket closed by {interaction.user.mention}."
            if reason:
                close_desc += f"\n**Reason:** {reason}"
            if ai_summary:
                close_desc += f"\n\n🤖 **AI Summary:**\n*{ai_summary}*"

            await channel.send(
                embed=discord.Embed(
                    description=close_desc,
                    color=parse_color(g.get("embeds_color", "#f45142")),
                ),
                view=ClosedTicketControlView(self),
            )
            log_desc = f"{interaction.user.mention} closed {channel.mention}."
            if ai_summary:
                log_desc += f"\n**AI Summary:** {ai_summary}"
            await self._log_event(guild, g, "Ticket Closed", log_desc)
        else:
            await self._save_transcript_and_finish(guild, g, ticket, panel, channel, interaction.user, deleted=True)
            log_desc = f"{interaction.user.mention} closed & deleted {channel.name}."
            if ai_summary:
                log_desc += f"\n**AI Summary:** {ai_summary}"
            await self._log_event(guild, g, "Ticket Deleted", log_desc)
            try:
                await channel.delete(reason=f"Ticket closed by {interaction.user}")
            except discord.Forbidden:
                pass

    async def reopen_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        gid = str(guild.id)
        g = get_guild_data(gid)
        ticket = self._ticket_by_channel(g, interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("⚠️ This isn't a ticket channel.", ephemeral=True)
            return
        panel = g["panels"].get(ticket["panel_name"], {})
        if not (self.is_manager(interaction.user, g) or self.is_support(interaction.user, panel)):
            await interaction.response.send_message("🚫 You can't reopen this ticket.", ephemeral=True)
            return

        await interaction.response.defer()
        channel = interaction.channel
        ticket["status"] = "open"
        ticket["closed_at"] = None
        save_guild_data(gid, g)

        creator = guild.get_member(ticket["creator_id"])
        open_cat_id = panel.get("open_category")
        open_cat = guild.get_channel(int(open_cat_id)) if str(open_cat_id).isdigit() else None
        try:
            if creator:
                await channel.set_permissions(creator, view_channel=True, send_messages=True,
                                               read_message_history=True, attach_files=True)
            if open_cat:
                await channel.edit(category=open_cat, reason=f"Ticket reopened by {interaction.user}")
        except discord.Forbidden:
            pass

        await channel.send(
            embed=discord.Embed(description=f"🔓 Ticket reopened by {interaction.user.mention}.",
                                 color=parse_color(g.get("embeds_color", "#f45142"))),
            view=TicketControlView(self),
        )
        await self._log_event(guild, g, "Ticket Reopened",
                               f"{interaction.user.mention} reopened {channel.mention}.")

    async def delete_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        gid = str(guild.id)
        g = get_guild_data(gid)
        ticket = self._ticket_by_channel(g, interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("⚠️ This isn't a ticket channel.", ephemeral=True)
            return
        panel = g["panels"].get(ticket["panel_name"], {})
        if not (self.is_manager(interaction.user, g) or self.is_support(interaction.user, panel)):
            await interaction.response.send_message("🚫 You can't delete this ticket.", ephemeral=True)
            return

        await interaction.response.send_message("🗑️ Deleting ticket & saving transcript...", ephemeral=True)
        channel = interaction.channel
        await self._save_transcript_and_finish(guild, g, ticket, panel, channel, interaction.user, deleted=True)
        await self._log_event(guild, g, "Ticket Deleted",
                               f"{interaction.user.mention} deleted {channel.name}.")
        try:
            await channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except discord.Forbidden:
            pass

    async def _save_transcript_and_finish(self, guild, g, ticket, panel, channel, closer, deleted=False):
        setup = find_setup_for_panel(g.get("transcript_setups", []), ticket["panel_name"])
        if setup:
            ticket["transcript_saved"] = True
            ticket["transcript_theme"] = setup.get("theme", "Simple")
            ticket["transcript_permissions"] = setup.get("permissions", "Creator and Support Team")
        ticket["status"] = "deleted" if deleted else ticket["status"]
        save_guild_data(str(guild.id), g)

    # ── Logging channel helper ───────────────────────────────

    async def _log_event(self, guild: discord.Guild, g: dict, title: str, description: str):
        log_id = g.get("logging_channel")
        if not log_id or not str(log_id).isdigit():
            return
        log_channel = guild.get_channel(int(log_id))
        if not log_channel:
            return
        embed = discord.Embed(title=f"🎫 {title}", description=description,
                               color=parse_color(g.get("embeds_color", "#f45142")),
                               timestamp=datetime.now(timezone.utc))
        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            pass

    # ── Message logging (for transcripts) ───────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        gid = str(message.guild.id)
        g = get_guild_data(gid)
        ticket = self._ticket_by_channel(g, message.channel.id)
        if not ticket:
            return
        ticket.setdefault("messages", []).append({
            "author": str(message.author), "author_id": message.author.id,
            "content": message.content, "ts": int(message.created_at.timestamp()),
            "attachments": [a.url for a in message.attachments],
        })
        save_guild_data(gid, g)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        gid = str(member.guild.id)
        g = get_guild_data(gid)
        open_tickets = [t for t in g.get("tickets", {}).values()
                        if t["creator_id"] == member.id and t["status"] == "open"]
        for ticket in open_tickets:
            panel = g["panels"].get(ticket["panel_name"], {})
            setup = find_setup_for_panel(g.get("automation_setups", []), ticket["panel_name"])
            action = (setup or {}).get("action_on_leave", "None")
            channel = member.guild.get_channel(ticket["channel_id"])
            if not channel:
                continue
            if action == "Close":
                ticket["status"] = "closed"
                ticket["closed_at"] = int(time.time())
                try:
                    await channel.send(embed=discord.Embed(
                        description=f"🔒 Ticket auto-closed: {member} left the server.",
                        color=parse_color(g.get("embeds_color", "#f45142"))))
                except discord.Forbidden:
                    pass
            elif action == "Delete":
                try:
                    await self._save_transcript_and_finish(member.guild, g, ticket, panel, channel, member.guild.me, deleted=True)
                    await channel.delete(reason="Ticket creator left the server")
                except discord.Forbidden:
                    pass
        save_guild_data(gid, g)

    # ── Automation background loop (auto-close / auto-delete inactive) ──

    @tasks.loop(minutes=5)
    async def automation_loop(self):
        data = _load()
        now = time.time()
        for gid, g in list(data.items()):
            guild = self.bot.get_guild(int(gid))
            if not guild:
                continue
            changed = False
            for ticket in list(g.get("tickets", {}).values()):
                if ticket["status"] != "open":
                    continue
                setup = find_setup_for_panel(g.get("automation_setups", []), ticket["panel_name"])
                if not setup:
                    continue
                channel = guild.get_channel(ticket["channel_id"])
                if not channel:
                    continue
                last_msg_ts = ticket["messages"][-1]["ts"] if ticket.get("messages") else ticket["created_at"]
                inactive_for = now - last_msg_ts

                auto_close_after = setup.get("auto_close_minutes")
                auto_delete_after = setup.get("auto_delete_minutes")

                if auto_delete_after and inactive_for >= int(auto_delete_after) * 60:
                    panel = g["panels"].get(ticket["panel_name"], {})
                    try:
                        await self._save_transcript_and_finish(guild, g, ticket, panel, channel, guild.me, deleted=True)
                        await channel.delete(reason="Automation: inactive ticket auto-deleted")
                    except discord.Forbidden:
                        pass
                    changed = True
                elif auto_close_after and inactive_for >= int(auto_close_after) * 60:
                    ticket["status"] = "closed"
                    ticket["closed_at"] = int(now)
                    try:
                        await channel.send(embed=discord.Embed(
                            description="🔒 Ticket auto-closed due to inactivity.",
                            color=parse_color(g.get("embeds_color", "#f45142"))),
                            view=ClosedTicketControlView(self))
                    except discord.Forbidden:
                        pass
                    changed = True
            if changed:
                data[gid] = g
        _save(data)

    @automation_loop.before_loop
    async def _before_automation_loop(self):
        await self.bot.wait_until_ready()

    # ══════════════════════════════════════════════════════
    #  SLASH COMMANDS
    # ══════════════════════════════════════════════════════

    ticket = app_commands.Group(name="ticket", description="Ticket system commands")

    def _cmd_enabled(self, g: dict, name: str) -> bool:
        return g.get("commands", {}).get(name, True)

    @ticket.command(name="setup", description="Send a ticket panel to a channel")
    @app_commands.describe(panel="The panel name to send", channel="The channel to send it to")
    async def ticket_setup(self, interaction: discord.Interaction, panel: str,
                            channel: discord.TextChannel | None = None):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "setup"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        if not self.is_manager(interaction.user, g):
            await interaction.response.send_message("🚫 You don't have permission to do this.", ephemeral=True)
            return
        panel_cfg = g["panels"].get(panel)
        if not panel_cfg:
            await interaction.response.send_message(f"⚠️ No panel named **{panel}** exists.", ephemeral=True)
            return

        target = channel or interaction.channel
        raw_msg = panel_cfg.get("panel_message", ":envelope_with_arrow: Click the button below to contact the {server}'s support team.")
        msg_text = format_vars(raw_msg, server=interaction.guild)

        if panel_cfg.get("layout") == "select":
            connected = [panel] + (panel_cfg.get("connected_panels") or [])
            view = PanelSelectView(self, connected, g, panel)
        else:
            view = PanelButtonView(self, panel, panel_cfg)

        await target.send(content=msg_text, view=view)
        await interaction.response.send_message(f"✅ Panel **{panel}** sent to {target.mention}.", ephemeral=True)

    @ticket.command(name="create", description="Manually create a ticket")
    @app_commands.describe(panel="Which panel to use")
    async def ticket_create(self, interaction: discord.Interaction, panel: str):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "create"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        if not g.get("create_command_enabled"):
            await interaction.response.send_message("⚠️ `/ticket create` isn't enabled on this server.", ephemeral=True)
            return
        panel_cfg = g["panels"].get(panel)
        if not panel_cfg:
            await interaction.response.send_message(f"⚠️ No panel named **{panel}** exists.", ephemeral=True)
            return
        await self.handle_panel_trigger(interaction, panel)

    @ticket.command(name="close", description="Close the current ticket")
    @app_commands.describe(reason="Optional reason for closing")
    async def ticket_close(self, interaction: discord.Interaction, reason: str = ""):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "close"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        await self.close_ticket(interaction, reason=reason)

    @ticket.command(name="reopen", description="Reopen the current ticket")
    async def ticket_reopen(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "reopen"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        await self.reopen_ticket(interaction)

    @ticket.command(name="delete", description="Delete the current ticket")
    async def ticket_delete(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "delete"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        await self.delete_ticket(interaction)

    @ticket.command(name="add", description="Add a user to the current ticket")
    @app_commands.describe(user="The user to add")
    async def ticket_add(self, interaction: discord.Interaction, user: discord.Member):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "add"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        ticket = self._ticket_by_channel(g, interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("⚠️ This isn't a ticket channel.", ephemeral=True)
            return
        panel = g["panels"].get(ticket["panel_name"], {})
        if not (self.is_manager(interaction.user, g) or self.is_support(interaction.user, panel)
                or interaction.user.id == ticket["creator_id"]):
            await interaction.response.send_message("🚫 You can't manage this ticket.", ephemeral=True)
            return
        try:
            await interaction.channel.set_permissions(user, view_channel=True, send_messages=True,
                                                        read_message_history=True, attach_files=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to do that.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ {user.mention} was added to the ticket.")
        await self._log_event(interaction.guild, g, "User Added",
                               f"{interaction.user.mention} added {user.mention} to {interaction.channel.mention}.")

    @ticket.command(name="remove", description="Remove a user from the current ticket")
    @app_commands.describe(user="The user to remove")
    async def ticket_remove(self, interaction: discord.Interaction, user: discord.Member):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "remove"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        ticket = self._ticket_by_channel(g, interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("⚠️ This isn't a ticket channel.", ephemeral=True)
            return
        panel = g["panels"].get(ticket["panel_name"], {})
        if not (self.is_manager(interaction.user, g) or self.is_support(interaction.user, panel)):
            await interaction.response.send_message("🚫 You can't manage this ticket.", ephemeral=True)
            return
        try:
            await interaction.channel.set_permissions(user, overwrite=None)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to do that.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ {user.mention} was removed from the ticket.")
        await self._log_event(interaction.guild, g, "User Removed",
                               f"{interaction.user.mention} removed {user.mention} from {interaction.channel.mention}.")

    @ticket.command(name="automation", description="View automated actions configured for this ticket's panel")
    async def ticket_automation(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "automation"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        ticket = self._ticket_by_channel(g, interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("⚠️ This isn't a ticket channel.", ephemeral=True)
            return
        setup = find_setup_for_panel(g.get("automation_setups", []), ticket["panel_name"])
        if not setup:
            await interaction.response.send_message("ℹ️ No automations are configured for this panel.", ephemeral=True)
            return
        lines = [f"**Action on creator leaving:** {setup.get('action_on_leave', 'None')}"]
        if setup.get("auto_close_minutes"):
            lines.append(f"**Auto-close after:** {setup['auto_close_minutes']} minutes of inactivity")
        if setup.get("auto_delete_minutes"):
            lines.append(f"**Auto-delete after:** {setup['auto_delete_minutes']} minutes of inactivity")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @ticket.command(name="update-counts", description="Update the ticket counter for a panel")
    @app_commands.describe(panel="Panel name", count="New count value")
    async def ticket_update_counts(self, interaction: discord.Interaction, panel: str, count: int):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        if not self._cmd_enabled(g, "update_counts"):
            await interaction.response.send_message("⚠️ This command is disabled.", ephemeral=True)
            return
        if not self.is_manager(interaction.user, g):
            await interaction.response.send_message("🚫 You don't have permission to do this.", ephemeral=True)
            return
        if panel not in g["panels"]:
            await interaction.response.send_message(f"⚠️ No panel named **{panel}** exists.", ephemeral=True)
            return
        g["panel_counts"][panel] = max(0, count)
        save_guild_data(gid, g)
        await interaction.response.send_message(f"✅ Ticket count for **{panel}** set to **{count}**.", ephemeral=True)

    @ticket_setup.autocomplete("panel")
    @ticket_create.autocomplete("panel")
    @ticket_update_counts.autocomplete("panel")
    async def _panel_autocomplete(self, interaction: discord.Interaction, current: str):
        gid = str(interaction.guild_id)
        g = get_guild_data(gid)
        names = list(g.get("panels", {}).keys())
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
