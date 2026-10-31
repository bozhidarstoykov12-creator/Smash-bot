"""Arcade — 6 hazard-free skill games (no betting, small rewards).

Games: /reaction /simon /whack /2048 /snake /bullseye

Rewards go through game_reward() (honours the guild daily cap). All state
mutations happen under doc_lock('reaction_scores.json') where relevant and
never await inside a lock (two-phase pattern).
"""
import asyncio
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils import load, save, ok, err, info, doc_lock, get_guild_cfg, game_reward, quest_progress
from i18n import t


def _currency(gid):
    cfg = load('economy.json').get(str(gid), {})
    return cfg.get('currency_name', 'SK'), cfg.get('currency_emoji', '🪙')


def _enabled(gid, key):
    return bool(get_guild_cfg(gid, "games").get(key, True))


async def _reward(interaction, gid, uid, amount, reason):
    granted = game_reward(gid, uid, amount, reason=reason)
    quest_progress(gid, uid, "game_played", 1)
    if granted > 0:
        currency, emoji = _currency(gid)
        try:
            await interaction.followup.send(embed=info(t(gid, 'game.reward', amount=granted, currency=currency, emoji=emoji)))
        except discord.HTTPException:
            pass


# ══════════════════════════════════════════════════════════════════════
#  REACTION
# ══════════════════════════════════════════════════════════════════════

class ReactionView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=30)
        self.gid, self.uid = gid, uid
        self.ready_at = None
        self.early = False
        self.done = False

    @discord.ui.button(label="CLICK!", style=discord.ButtonStyle.green)
    async def click(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.done:
            return
        if self.ready_at is None:
            self.early = True
            self.done = True
            button.disabled = True
            return await interaction.response.edit_message(
                embed=err(t(self.gid, 'arcade.reaction_early')), view=self)
        ms = int((time.time() - self.ready_at) * 1000)
        self.done = True
        button.disabled = True
        await interaction.response.edit_message(
            embed=ok(t(self.gid, 'arcade.reaction_result', ms=ms)), view=self)
        self.stop()
        # Reward scales with speed.
        reward = max(5, min(100, 120 - ms // 10))
        await _reward(interaction, self.gid, self.uid, reward, "reaction")


# ══════════════════════════════════════════════════════════════════════
#  SIMON SAYS
# ══════════════════════════════════════════════════════════════════════

SIMON_EMOJI = ["🔴", "🟢", "🔵", "🟡"]


class SimonButton(discord.ui.Button):
    def __init__(self, idx, view):
        super().__init__(label=SIMON_EMOJI[idx], style=discord.ButtonStyle.secondary)
        self.idx = idx
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        await self.view_ref.press(interaction, self.idx)


class SimonView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=120)
        self.gid, self.uid = gid, uid
        self.sequence = []
        self.input = []
        self.level = 0
        self.finished = False
        for i in range(4):
            self.add_item(SimonButton(i, self))

    async def start(self, interaction):
        await interaction.response.send_message(embed=info(t(self.gid, 'arcade.simon_title'),
                                                          t(self.gid, 'arcade.simon_watch')), view=self)
        self.message = await interaction.original_response()
        await self.next_round()

    async def next_round(self):
        self.level += 1
        self.sequence.append(random.randint(0, 3))
        self.input = []
        for c in self.children:
            c.disabled = True
        try:
            await self.message.edit(embed=info(t(self.gid, 'arcade.simon_title'), t(self.gid, 'arcade.simon_watch')), view=self)
        except discord.HTTPException:
            pass
        await asyncio.sleep(1.0)
        for idx in self.sequence:
            try:
                await self.message.edit(content=f"**{SIMON_EMOJI[idx]}**")
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.6)
            try:
                await self.message.edit(content="\u200b")
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.2)
        for c in self.children:
            c.disabled = False
        try:
            await self.message.edit(content="\u200b",
                                    embed=info(t(self.gid, 'arcade.simon_title'), t(self.gid, 'arcade.simon_your_turn')),
                                    view=self)
        except discord.HTTPException:
            pass

    async def press(self, interaction, idx):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        await interaction.response.defer()
        self.input.append(idx)
        pos = len(self.input) - 1
        if self.sequence[pos] != idx:
            self.finished = True
            for c in self.children:
                c.disabled = True
            await self.message.edit(
                embed=err(t(self.gid, 'arcade.simon_wrong', level=self.level - 1)), view=self)
            self.stop()
            await _reward(interaction, self.gid, self.uid, (self.level - 1) * 15, "simon")
            return
        if len(self.input) == len(self.sequence):
            await self.next_round()

    async def on_timeout(self):
        self.finished = True


# ══════════════════════════════════════════════════════════════════════
#  WHACK-A-MOLE
# ══════════════════════════════════════════════════════════════════════

class WhackButton(discord.ui.Button):
    def __init__(self, idx, view):
        super().__init__(label="⬜", style=discord.ButtonStyle.secondary, row=idx // 3)
        self.idx = idx
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        await self.view_ref.whack(interaction, self.idx)


class WhackView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=45)
        self.gid, self.uid = gid, uid
        self.score = 0
        self.mole = None
        self.finished = False
        self.task = None
        for i in range(9):
            self.add_item(WhackButton(i, self))

    async def start(self, interaction):
        await interaction.response.send_message(embed=info(t(self.gid, 'arcade.whack_title'), "Hit the mole!"), view=self)
        self.message = await interaction.original_response()
        self.task = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            for _ in range(15):
                if self.finished:
                    return
                self.mole = random.randint(0, 8)
                for c in self.children:
                    c.label = "🐹" if getattr(c, 'idx', -1) == self.mole else "⬜"
                try:
                    await self.message.edit(embed=info(t(self.gid, 'arcade.whack_title'), f"Score: **{self.score}**"), view=self)
                except discord.HTTPException:
                    pass
                await asyncio.sleep(1.2)
            self.finished = True
            for c in self.children:
                c.disabled = True
            try:
                await self.message.edit(embed=ok(t(self.gid, 'arcade.whack_end', score=self.score)), view=self)
            except discord.HTTPException:
                pass
            await _reward(None, self.gid, self.uid, self.score * 5, "whack")
        except asyncio.CancelledError:
            pass

    async def whack(self, interaction, idx):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        if idx == self.mole:
            self.score += 1
            self.mole = None
            await interaction.response.edit_message(
                embed=info(t(self.gid, 'arcade.whack_title'), t(self.gid, 'arcade.whack_hit', score=self.score)), view=self)
        else:
            await interaction.response.defer()

    async def on_timeout(self):
        self.finished = True
        if self.task:
            self.task.cancel()


# ══════════════════════════════════════════════════════════════════════
#  2048
# ══════════════════════════════════════════════════════════════════════

class G2048View(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=180)
        self.gid, self.uid = gid, uid
        self.grid = [[0] * 4 for _ in range(4)]
        self.finished = False
        self._spawn()
        self._spawn()

    def _spawn(self):
        empties = [(r, c) for r in range(4) for c in range(4) if self.grid[r][c] == 0]
        if empties:
            r, c = random.choice(empties)
            self.grid[r][c] = 4 if random.random() < 0.1 else 2

    def _render(self):
        lines = []
        for row in self.grid:
            lines.append(" | ".join(f"{v:>4}" if v else "   ·" for v in row))
        return "```\n" + "\n".join(lines) + "\n```"

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'arcade.g2048_title'), description=self._render(),
                           color=discord.Color.blue())
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    def _move(self, direction):
        def compress(row):
            row = [v for v in row if v]
            out = []
            i = 0
            while i < len(row):
                if i + 1 < len(row) and row[i] == row[i + 1]:
                    out.append(row[i] * 2)
                    i += 2
                else:
                    out.append(row[i])
                    i += 1
            return out + [0] * (4 - len(out))

        before = [r[:] for r in self.grid]
        if direction in ("left", "right"):
            for r in range(4):
                row = self.grid[r]
                if direction == "right":
                    row = row[::-1]
                row = compress(row)
                if direction == "right":
                    row = row[::-1]
                self.grid[r] = row
        else:
            for c in range(4):
                col = [self.grid[r][c] for r in range(4)]
                if direction == "down":
                    col = col[::-1]
                col = compress(col)
                if direction == "down":
                    col = col[::-1]
                for r in range(4):
                    self.grid[r][c] = col[r]
        return before != self.grid

    def _best(self):
        return max(max(r) for r in self.grid)

    def _can_move(self):
        for r in range(4):
            for c in range(4):
                if self.grid[r][c] == 0:
                    return True
                if c + 1 < 4 and self.grid[r][c] == self.grid[r][c + 1]:
                    return True
                if r + 1 < 4 and self.grid[r][c] == self.grid[r + 1][c]:
                    return True
        return False

    async def _do(self, interaction, direction):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        moved = self._move(direction)
        if moved:
            self._spawn()
        if not self._can_move():
            self.finished = True
            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(
                embed=self._embed(t(self.gid, 'arcade.g2048_end', tile=self._best())), view=self)
            self.stop()
            await _reward(interaction, self.gid, self.uid, min(200, self._best() // 4), "g2048")
            return
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="⬆️", style=discord.ButtonStyle.blurple, row=0)
    async def up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "up")

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.blurple, row=1)
    async def left(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "left")

    @discord.ui.button(label="⬇️", style=discord.ButtonStyle.blurple, row=1)
    async def down(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "down")

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.blurple, row=1)
    async def right(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "right")


# ══════════════════════════════════════════════════════════════════════
#  SNAKE
# ══════════════════════════════════════════════════════════════════════

class SnakeView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=120)
        self.gid, self.uid = gid, uid
        self.size = 8
        self.snake = [(4, 4), (4, 3), (4, 2)]
        self.direction = (0, 1)
        self.food = self._place_food()
        self.score = 0
        self.finished = False

    def _place_food(self):
        empties = [(r, c) for r in range(self.size) for c in range(self.size) if (r, c) not in self.snake]
        return random.choice(empties) if empties else None

    def _render(self):
        rows = []
        for r in range(self.size):
            line = ""
            for c in range(self.size):
                if (r, c) == self.snake[0]:
                    line += "🟢"
                elif (r, c) in self.snake:
                    line += "🟩"
                elif (r, c) == self.food:
                    line += "🍎"
                else:
                    line += "⬛"
            rows.append(line)
        return "\n".join(rows)

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'arcade.snake_title'),
                           description=f"{self._render()}\nScore: **{self.score}**", color=discord.Color.green())
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    async def _step(self, interaction, direction):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        dr, dc = direction
        if (dr, dc) == (-self.direction[0], -self.direction[1]):
            direction = self.direction
            dr, dc = direction
        self.direction = (dr, dc)
        head = (self.snake[0][0] + dr, self.snake[0][1] + dc)
        if (head[0] < 0 or head[0] >= self.size or head[1] < 0 or head[1] >= self.size
                or head in self.snake[:-1]):
            self.finished = True
            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(
                embed=self._embed(t(self.gid, 'arcade.snake_end', score=self.score)), view=self)
            self.stop()
            await _reward(interaction, self.gid, self.uid, self.score * 10, "snake")
            return
        self.snake.insert(0, head)
        if head == self.food:
            self.score += 1
            self.food = self._place_food()
        else:
            self.snake.pop()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="⬆️", style=discord.ButtonStyle.green, row=0)
    async def up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._step(interaction, (-1, 0))

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.green, row=1)
    async def left(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._step(interaction, (0, -1))

    @discord.ui.button(label="⬇️", style=discord.ButtonStyle.green, row=1)
    async def down(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._step(interaction, (1, 0))

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.green, row=1)
    async def right(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._step(interaction, (0, 1))


# ══════════════════════════════════════════════════════════════════════
#  BULLSEYE
# ══════════════════════════════════════════════════════════════════════

class BullseyeView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=90)
        self.gid, self.uid = gid, uid
        self.level = 1
        self.finished = False

    def _embed(self, note=None):
        em = discord.Embed(title=t(self.gid, 'arcade.bullseye_title'),
                           description=f"Level **{self.level}** — hit the target!", color=discord.Color.red())
        if note:
            em.add_field(name="\u200b", value=note, inline=False)
        return em

    @discord.ui.button(label="🎯 Aim", style=discord.ButtonStyle.red)
    async def aim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        # Higher levels are harder.
        chance = max(0.15, 0.9 - (self.level - 1) * 0.08)
        if random.random() < chance:
            self.level += 1
            await interaction.response.edit_message(
                embed=self._embed(t(self.gid, 'arcade.bullseye_hit', level=self.level - 1)), view=self)
        else:
            self.finished = True
            for c in self.children:
                c.disabled = True
            await interaction.response.edit_message(
                embed=self._embed(t(self.gid, 'arcade.bullseye_end', level=self.level - 1)), view=self)
            self.stop()
            await _reward(interaction, self.gid, self.uid, (self.level - 1) * 20, "bullseye")

    @discord.ui.button(label="🏳️ Give up", style=discord.ButtonStyle.gray)
    async def giveup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != int(self.uid):
            return await interaction.response.send_message("This is not your game.", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            embed=self._embed(t(self.gid, 'arcade.bullseye_end', level=self.level - 1)), view=self)
        self.stop()
        await _reward(interaction, self.gid, self.uid, (self.level - 1) * 20, "bullseye")


# ══════════════════════════════════════════════════════════════════════
#  COG
# ══════════════════════════════════════════════════════════════════════

class Arcade(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="reaction", description="Test your reaction time")
    async def reaction(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "reaction_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = ReactionView(gid, str(interaction.user.id))
        await interaction.response.send_message(embed=info(t(gid, 'arcade.reaction_ready'), "Wait for it..."), view=view)
        await asyncio.sleep(random.uniform(2.0, 5.0))
        if view.done:
            return
        view.ready_at = time.time()
        try:
            await interaction.edit_original_response(
                embed=info(t(gid, 'arcade.reaction_click'), "Click the button now!"), view=view)
        except discord.HTTPException:
            pass

    @app_commands.command(name="simon", description="Repeat the growing color sequence")
    async def simon(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "simon_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = SimonView(gid, str(interaction.user.id))
        await view.start(interaction)

    @app_commands.command(name="whack", description="Whack the moles as fast as you can")
    async def whack(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "whack_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = WhackView(gid, str(interaction.user.id))
        await view.start(interaction)

    @app_commands.command(name="2048", description="Play 2048 — merge tiles to reach the highest number")
    async def g2048(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "g2048_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = G2048View(gid, str(interaction.user.id))
        await interaction.response.send_message(embed=view._embed(), view=view)

    @app_commands.command(name="snake", description="Play Snake — eat apples and avoid the walls")
    async def snake(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "snake_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = SnakeView(gid, str(interaction.user.id))
        await interaction.response.send_message(embed=view._embed(), view=view)

    @app_commands.command(name="bullseye", description="Hit the bullseye — each level gets harder")
    async def bullseye(self, interaction: discord.Interaction):
        gid = str(interaction.guild.id)
        if not _enabled(gid, "bullseye_enabled"):
            return await interaction.response.send_message(embed=err(t(gid, 'game.disabled')), ephemeral=True)
        view = BullseyeView(gid, str(interaction.user.id))
        await interaction.response.send_message(embed=view._embed(), view=view)


async def setup(bot):
    await bot.add_cog(Arcade(bot))
