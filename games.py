import aiohttp
import asyncio
import discord
import html
import re
import random
import time
from discord import app_commands
from discord.ext import commands
from utils import err, info, ok

TRIVIA = [
    {"q": "Capital of France?", "a": "paris"},
    {"q": "Sides on a hexagon?", "a": "6"},
    {"q": "15 x 15?", "a": "225"},
    {"q": "Fastest land animal?", "a": "cheetah"},
    {"q": "Largest planet?", "a": "jupiter"},
    {"q": "7 x 8?", "a": "56"},
    {"q": "Colors in a rainbow?", "a": "7"},
    {"q": "Capital of Japan?", "a": "tokyo"},
    {"q": "Spider legs?", "a": "8"},
    {"q": "Square root of 144?", "a": "12"},
    {"q": "Largest ocean?", "a": "pacific"},
    {"q": "Players on a football team?", "a": "11"},
    {"q": "Chemical symbol for water?", "a": "h2o"},
    {"q": "Continents on Earth?", "a": "7"},
    {"q": "Longest river?", "a": "nile"},
    {"q": "Smallest planet?", "a": "mercury"},
    {"q": "Bones in the human body?", "a": "206"},
    {"q": "This bot is written in?", "a": "python"},
]

HANGMAN_WORDS = [
    'computer', 'keyboard', 'monitor', 'internet', 'discord', 'gaming',
    'headset', 'controller', 'microphone', 'javascript', 'python', 'server'
]
HANGMAN_ART = [
    "```\n  +---+\n  |   |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n  |   |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|   |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n /    |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n / \\  |\n      |\n=========```",
]

RPS_EMOJIS = {
    'rock': '🪨',
    'paper': '📄',
    'scissors': '✂️',
}
RPS_WINS = {
    ('rock', 'scissors'),
    ('paper', 'rock'),
    ('scissors', 'paper'),
}

TRIVIA_DIFFICULTY_CHOICES = [
    app_commands.Choice(name='Easy', value='easy'),
    app_commands.Choice(name='Medium', value='medium'),
    app_commands.Choice(name='Hard', value='hard'),
]

COINFLIP_CALL_CHOICES = [
    app_commands.Choice(name='Heads', value='heads'),
    app_commands.Choice(name='Tails', value='tails'),
]

MINESWEEPER_DIFFICULTY_CHOICES = [
    app_commands.Choice(name='Easy', value='easy'),
    app_commands.Choice(name='Medium', value='medium'),
    app_commands.Choice(name='Hard', value='hard'),
]

BLACKJACK_SUITS = ['♠', '♥', '♦', '♣']
BLACKJACK_RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
BLACKJACK_VALUES = {'A': 11, 'J': 10, 'Q': 10, 'K': 10}

BLACKJACK_MESSAGE = "🂡 Blackjack"

MEMORY_COLORS = [
    ('red', '🔴', discord.ButtonStyle.danger),
    ('green', '🟢', discord.ButtonStyle.success),
    ('blue', '🔵', discord.ButtonStyle.primary),
    ('yellow', '🟡', discord.ButtonStyle.secondary),
]

HILO_RANKS = {11: 'J', 12: 'Q', 13: 'K', 14: 'A'}

MINESWEEPER_COUNTS = ['0️⃣', '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣']

EMOJI_QUIZ = [
    ('🕷️🕸️🧑', 'spiderman'),
    ('👑🦁', 'lion king'),
    ('🚗💨', 'fast and furious'),
    ('🧊🧙', 'frozen'),
    ('🏴‍☠️⚓', 'pirates of the caribbean'),
    ('🐼🥋', 'kung fu panda'),
    ('🦇🦸', 'batman'),
    ('🕵️‍♂️🔎', 'detective'),
    ('🌊🐟', 'finding nemo'),
    ('🧙‍♂️⚡', 'harry potter'),
    ('🚀🌌', 'star wars'),
    ('🤠🐴', 'toy story'),
    ('🧟‍♂️🏠', 'zombie house'),
    ('🎩🐇', 'magic trick'),
    ('🧜‍♀️🌊', 'little mermaid'),
]

TYPERACE_PHRASES = [
    'the quick brown fox jumps over the lazy dog',
    'discord minigames are surprisingly fun',
    'practice makes perfect',
    'keep calm and code on',
    'never trust a smiling cat',
    'the sky is not the limit',
    'small steps lead to big wins',
    'focus on the next right thing',
    'coffee fuels the morning',
    'debugging is just detective work',
    'write once test twice',
    'music makes the moment better',
    'kindness always matters',
    'a smooth sea never made a skilled sailor',
    'speed matters but accuracy wins',
]

MINESWEEPER_NUMBER_EMOJIS = {i: MINESWEEPER_COUNTS[i] for i in range(9)}

WORDLE_WORDS = [
    'apple', 'crane', 'house', 'table', 'sound', 'plant', 'grape', 'stone',
    'chair', 'flame', 'crown', 'light', 'brick', 'smile', 'bread', 'green',
    'water', 'world', 'brain', 'party', 'cloud', 'plane', 'train', 'sugar',
    'storm', 'baker', 'dance', 'eagle', 'fruit', 'glass', 'heart', 'jelly',
    'knife', 'lemon', 'magic', 'night', 'ocean', 'piano', 'queen', 'river',
    'scale', 'teeth', 'uncle', 'vivid', 'wheat', 'youth', 'zebra', 'blush',
    'chess', 'drift', 'ember', 'frost', 'giant', 'honey', 'ivory', 'jolly',
    'koala', 'lunar', 'mango', 'noble', 'olive', 'proud', 'quiet', 'river',
    'spice', 'thyme', 'ultra', 'vapor', 'whale', 'xenon', 'young', 'zesty',
]
SCRAMBLE_WORDS = WORDLE_WORDS + [
    'discord', 'python', 'server', 'minigame', 'galaxy', 'rocket', 'penguin',
    'wizard', 'window', 'banana', 'camera', 'festival', 'journey', 'marble',
]
WYR_PAIRS = [
    ('Have a rewind button in your life', 'Have a pause button in your life'),
    ('Be able to fly', 'Be able to breathe underwater'),
    ('Have unlimited money', 'Have unlimited free time'),
    ('Live in space', 'Live under the sea'),
    ('Be invisible', 'Read minds'),
    ('Always know the truth', 'Always know when someone lies'),
    ('Never need sleep', 'Never need food'),
    ('Explore the past', 'Explore the future'),
    ('Have a personal robot', 'Have a personal chef'),
    ('Be the funniest person in the room', 'Be the smartest person in the room'),
    ('Win every argument', 'Win every game'),
    ('Teleport anywhere', 'Time travel anywhere'),
    ('Speak every language', 'Play every instrument'),
    ('Live a long calm life', 'Live a short adventurous life'),
    ('Be famous', 'Be wealthy and unknown'),
]


def _normalize_text(s):
    return re.sub(r'[^a-z0-9]+', '', str(s).replace('\u200b', '').casefold())


def _normalize_phrase(s):
    return re.sub(r'\s+', ' ', str(s).replace('\u200b', '').strip().casefold())


def _make_blackjack_deck():
    deck = [(rank, suit) for suit in BLACKJACK_SUITS for rank in BLACKJACK_RANKS]
    random.shuffle(deck)
    return deck


def _draw_blackjack_card(deck):
    return deck.pop()


def _card_label(card):
    rank, suit = card
    return f'{rank}{suit}'


def _blackjack_total(hand):
    total = 0
    aces = 0
    for rank, _ in hand:
        if rank == 'A':
            total += 11
            aces += 1
        elif rank in BLACKJACK_VALUES:
            total += BLACKJACK_VALUES[rank]
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _blackjack_hand_text(hand, hide_second=False):
    if hide_second and len(hand) > 1:
        return ' '.join([_card_label(hand[0]), '??'])
    return ' '.join(_card_label(card) for card in hand)


def _format_hilo_rank(value):
    return HILO_RANKS.get(value, str(value))


def _make_minesweeper_board(rows, cols, mines):
    board = [[0 for _ in range(cols)] for _ in range(rows)]
    all_cells = [(r, c) for r in range(rows) for c in range(cols)]
    mine_cells = set(random.sample(all_cells, mines))
    for r, c in mine_cells:
        board[r][c] = -1
    for r in range(rows):
        for c in range(cols):
            if board[r][c] == -1:
                continue
            count = 0
            for rr in range(max(0, r - 1), min(rows, r + 2)):
                for cc in range(max(0, c - 1), min(cols, c + 2)):
                    if (rr, cc) != (r, c) and board[rr][cc] == -1:
                        count += 1
            board[r][c] = count
    return board


def _render_minesweeper(board, hint=None):
    rows = []
    for r, row in enumerate(board):
        rendered = []
        for c, value in enumerate(row):
            emoji = '💥' if value == -1 else MINESWEEPER_NUMBER_EMOJIS[value]
            if hint == (r, c):
                rendered.append(emoji)
            else:
                rendered.append(f'||{emoji}||')
        rows.append(' '.join(rendered))
    return '\n'.join(rows)


# ── Interactive Minesweeper ────────────────────────────────────────────────────

class MinesweeperButton(discord.ui.Button):
    def __init__(self, r: int, c: int, value: int):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label='\u200b',
            row=r,
        )
        self.r = r
        self.c = c
        self.value = value  # -1 = mine, 0-8 = neighbour count

    async def callback(self, interaction: discord.Interaction):
        v = self.view
        if interaction.user != v.player:
            return await interaction.response.send_message(
                embed=err('Only the person who started this game can play!'), ephemeral=True
            )
        if v.finished:
            return await interaction.response.send_message(
                embed=err('This game is already over!'), ephemeral=True
            )
        if self.disabled:
            return await interaction.response.defer()
        if self.value == -1:
            await v.game_over(interaction, self)
        else:
            await v.reveal_safe(interaction, self)


class MinesweeperView(discord.ui.View):
    def __init__(self, player, board: list, difficulty: str):
        super().__init__(timeout=300)
        self.player     = player
        self.board      = board
        self.difficulty = difficulty
        self.finished   = False
        self.message    = None
        self.rows       = len(board)
        self.cols       = len(board[0])
        self.mine_count = sum(
            1 for r in range(self.rows) for c in range(self.cols) if board[r][c] == -1
        )
        self.total_safe = self.rows * self.cols - self.mine_count
        self.revealed   = 0
        for r in range(self.rows):
            for c in range(self.cols):
                self.add_item(MinesweeperButton(r, c, board[r][c]))

    def _get_btn(self, r: int, c: int):
        for child in self.children:
            if isinstance(child, MinesweeperButton) and child.r == r and child.c == c:
                return child
        return None

    def _do_reveal(self, btn):
        """Visually reveal one safe cell and increment the counter."""
        if btn.disabled or btn.value == -1:
            return
        btn.disabled = True
        btn.label    = None
        if btn.value == 0:
            btn.emoji = '⬜'
            btn.style = discord.ButtonStyle.success
        else:
            btn.emoji = MINESWEEPER_COUNTS[btn.value]
            btn.style = discord.ButtonStyle.primary
        self.revealed += 1

    def _flood_reveal(self, start_r: int, start_c: int):
        """BFS flood-fill: auto-reveal connected zero cells and their numbered border."""
        queue   = [(start_r, start_c)]
        visited = {(start_r, start_c)}
        while queue:
            cr, cc = queue.pop(0)
            for nr in range(max(0, cr - 1), min(self.rows, cr + 2)):
                for nc in range(max(0, cc - 1), min(self.cols, cc + 2)):
                    if (nr, nc) not in visited:
                        visited.add((nr, nc))
                        nb = self._get_btn(nr, nc)
                        if nb and not nb.disabled and nb.value != -1:
                            self._do_reveal(nb)
                            if nb.value == 0:
                                queue.append((nr, nc))

    def build_embed(self, title='💣 Minesweeper',
                    description=None, color=None):
        color = color or discord.Color.blurple()
        desc  = description or (
            f'**{self.difficulty.capitalize()}** — {self.rows}×{self.cols} grid '
            f'with **{self.mine_count}** mines.\n'
            f'Safe cells revealed: **{self.revealed}/{self.total_safe}**'
        )
        return discord.Embed(title=title, description=desc, color=color)

    async def reveal_safe(self, interaction: discord.Interaction, btn):
        self._do_reveal(btn)
        if btn.value == 0:
            self._flood_reveal(btn.r, btn.c)

        if self.revealed >= self.total_safe:
            self.finished = True
            for child in self.children:
                child.disabled = True
            self.stop()
            em = discord.Embed(
                title='🎉 You Win!',
                description=(
                    f'You cleared all **{self.total_safe}** safe cells without hitting a mine!\n'
                    f'**{self.difficulty.capitalize()}** — {self.mine_count} mines avoided. 🏆'
                ),
                color=discord.Color.green(),
            )
            return await interaction.response.edit_message(embed=em, view=self)

        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def game_over(self, interaction: discord.Interaction, hit_btn):
        self.finished = True
        for child in self.children:
            child.disabled = True
            if isinstance(child, MinesweeperButton) and child.value == -1:
                child.label = None
                child.emoji = '💥'
                child.style = discord.ButtonStyle.danger
        self.stop()
        em = discord.Embed(
            title='💥 BOOM! You hit a mine!',
            description=(
                f'**Game over!** You revealed **{self.revealed}/{self.total_safe}** safe cells '
                f'before hitting a mine.\nAll mines have been uncovered.'
            ),
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=em, view=self)

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(
                embed=self.build_embed(
                    description=(
                        f'⏰ **Timed out!** You revealed **{self.revealed}/{self.total_safe}** '
                        f'safe cells.\n**{self.difficulty.capitalize()}** — {self.mine_count} mines.'
                    )
                ),
                view=self,
            )



def _norm(s):
    return ' '.join(str(s).split()).strip().lower()


def _make_trivia_options(correct, wrongs):
    options = list(dict.fromkeys(wrongs + [correct]))
    while len(options) < 4:
        options.append(random.choice([item['a'] for item in TRIVIA if _norm(item['a']) != _norm(correct)]))
    random.shuffle(options)
    return options[:4]


async def _fetch_opentdb_question(difficulty=None):
    url = 'https://opentdb.com/api.php?amount=1&type=multiple'
    if difficulty:
        url += f'&difficulty={difficulty}'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception:
        return None

    results = data.get('results', [])
    if not results:
        return None

    q = results[0]
    question = html.unescape(q.get('question', ''))
    category = html.unescape(q.get('category', ''))
    correct = html.unescape(q.get('correct_answer', ''))
    wrongs = [html.unescape(a) for a in q.get('incorrect_answers', []) if a]
    options = wrongs + [correct]
    random.shuffle(options)
    return {
        'question': question,
        'category': category,
        'correct': correct,
        'options': options,
    }


def _static_trivia_question():
    q = random.choice(TRIVIA)
    correct = html.unescape(str(q['a']))
    wrong_pool = list(dict.fromkeys(html.unescape(str(item['a'])) for item in TRIVIA if _norm(item['a']) != _norm(correct)))
    wrongs = random.sample(wrong_pool, k=min(3, len(wrong_pool)))
    options = _make_trivia_options(correct, wrongs)
    return {
        'question': html.unescape(str(q['q'])),
        'category': None,
        'correct': correct,
        'options': options,
    }


def _score_wordle_guess(guess, secret):
    guess = guess.upper()
    secret = secret.upper()
    score = ['⬛'] * 5
    remaining = {}
    for i, (g, s) in enumerate(zip(guess, secret)):
        if g == s:
            score[i] = '🟩'
        else:
            remaining[s] = remaining.get(s, 0) + 1
    for i, g in enumerate(guess):
        if score[i] == '🟩':
            continue
        if remaining.get(g, 0) > 0:
            score[i] = '🟨'
            remaining[g] -= 1
    return ''.join(score)


def _render_connect4_board(board):
    symbols = {0: '⬛', 1: '🔴', 2: '🟡'}
    header = '1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣ 6️⃣ 7️⃣'
    rows = [' '.join(symbols[cell] for cell in row) for row in board]
    return header + '\n' + '\n'.join(rows)


def _connect4_drop(board, column, token):
    for row in range(5, -1, -1):
        if board[row][column] == 0:
            board[row][column] = token
            return row
    return None


def _connect4_winner(board):
    rows = len(board)
    cols = len(board[0])
    for r in range(rows):
        for c in range(cols):
            token = board[r][c]
            if not token:
                continue
            if c <= cols - 4 and all(board[r][c + i] == token for i in range(4)):
                return token
            if r <= rows - 4 and all(board[r + i][c] == token for i in range(4)):
                return token
            if r <= rows - 4 and c <= cols - 4 and all(board[r + i][c + i] == token for i in range(4)):
                return token
            if r <= rows - 4 and c >= 3 and all(board[r + i][c - i] == token for i in range(4)):
                return token
    return None


def _connect4_draw(board):
    return all(cell != 0 for row in board for cell in row)


def _vote_bar(pct):
    filled = round(pct / 10)
    return '█' * filled + '░' * (10 - filled)


class TTTButton(discord.ui.Button):
    def __init__(self, x, y):
        super().__init__(style=discord.ButtonStyle.secondary, label='\u200b', row=y)
        self.x, self.y = x, y

    async def callback(self, interaction: discord.Interaction):
        v: TTTView = self.view
        if interaction.user not in (v.p1, v.p2):
            return await interaction.response.send_message("You're not in this game!", ephemeral=True)
        if interaction.user != v.current:
            return await interaction.response.send_message('Not your turn!', ephemeral=True)
        if v.board[self.y][self.x] != 0:
            return await interaction.response.send_message('Cell taken!', ephemeral=True)
        sym = 1 if v.current == v.p1 else 2
        v.board[self.y][self.x] = sym
        self.label = '❌' if sym == 1 else '⭕'
        self.style = discord.ButtonStyle.danger if sym == 1 else discord.ButtonStyle.primary
        self.disabled = True
        winner = v.check_winner()
        if winner:
            for c in v.children:
                c.disabled = True
            name = v.p1.display_name if winner == 1 else v.p2.display_name
            v.stop()
            return await interaction.response.edit_message(content=f'🎉 **{name}** wins!', view=v)
        if all(v.board[r][c] != 0 for r in range(3) for c in range(3)):
            for c in v.children:
                c.disabled = True
            v.stop()
            return await interaction.response.edit_message(content='**Draw!** 🤝', view=v)
        v.current = v.p2 if v.current == v.p1 else v.p1
        await interaction.response.edit_message(content=f"🎮 **{v.current.display_name}'s** turn!", view=v)


class TTTView(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=120)
        self.p1, self.p2, self.current = p1, p2, p1
        self.board = [[0] * 3 for _ in range(3)]
        for y in range(3):
            for x in range(3):
                self.add_item(TTTButton(x, y))

    def check_winner(self):
        b = self.board
        for line in [
            [b[0][0], b[0][1], b[0][2]], [b[1][0], b[1][1], b[1][2]], [b[2][0], b[2][1], b[2][2]],
            [b[0][0], b[1][0], b[2][0]], [b[0][1], b[1][1], b[2][1]], [b[0][2], b[1][2], b[2][2]],
            [b[0][0], b[1][1], b[2][2]], [b[0][2], b[1][1], b[2][0]],
        ]:
            if line == [1, 1, 1]:
                return 1
            if line == [2, 2, 2]:
                return 2
        return None


class RPSButton(discord.ui.Button):
    def __init__(self, choice, emoji, row):
        super().__init__(style=discord.ButtonStyle.secondary, label=choice.capitalize(), emoji=emoji, row=row)
        self.choice = choice

    async def callback(self, interaction: discord.Interaction):
        v: RPSView = self.view
        if v.finished:
            return await interaction.response.send_message(embed=err('This game is already over!'), ephemeral=True)
        if v.bot_mode:
            if interaction.user != v.player:
                return await interaction.response.send_message(embed=err('Only the command user can play!'), ephemeral=True)
            user_choice = self.choice
            bot_choice = random.choice(list(RPS_EMOJIS))
            v.finished = True
            for child in v.children:
                child.disabled = True
            embed = v.build_result_embed(user_choice, bot_choice)
            v.stop()
            return await interaction.response.edit_message(embed=embed, view=v)

        if interaction.user.id not in (v.p1.id, v.p2.id):
            return await interaction.response.send_message(embed=err('Only the two players can click these buttons!'), ephemeral=True)
        if interaction.user.id in v.choices:
            return await interaction.response.send_message(embed=err('You already picked!'), ephemeral=True)
        v.choices[interaction.user.id] = self.choice
        await interaction.response.send_message(embed=info('Rock Paper Scissors', f"You picked {RPS_EMOJIS[self.choice]} **{self.choice.capitalize()}**!"), ephemeral=True)
        if len(v.choices) < 2 or v.finished:
            return
        v.finished = True
        for child in v.children:
            child.disabled = True
        embed = v.build_result_embed(v.choices[v.p1.id], v.choices[v.p2.id])
        v.stop()
        try:
            await interaction.message.edit(embed=embed, view=v)
        except Exception:
            pass


class RPSView(discord.ui.View):
    def __init__(self, player, opponent=None):
        super().__init__(timeout=60)
        self.player = player
        self.p1 = player
        self.p2 = opponent
        self.bot_mode = opponent is None or opponent.bot or opponent == player
        self.choices = {}
        self.finished = False
        self.message = None
        for idx, choice in enumerate(('rock', 'paper', 'scissors')):
            self.add_item(RPSButton(choice, RPS_EMOJIS[choice], idx))

    def build_result_embed(self, user_choice, other_choice):
        if self.bot_mode:
            other_name = 'Bot'
            user_name = self.player.display_name
            winner = None
            if user_choice == other_choice:
                title = '🤝 Tie!'
                color = discord.Color.yellow()
                result = 'It is a tie!'
            elif (user_choice, other_choice) in RPS_WINS:
                title = '🎉 You win!'
                color = discord.Color.green()
                result = f'**{user_name}** wins!'
            else:
                title = '😈 I win!'
                color = discord.Color.red()
                result = 'The bot wins!'
            em = discord.Embed(title='✂️ Rock Paper Scissors', color=color)
            em.add_field(name='You', value=f"{RPS_EMOJIS[user_choice]} **{user_choice.capitalize()}**", inline=True)
            em.add_field(name='Bot', value=f"{RPS_EMOJIS[other_choice]} **{other_choice.capitalize()}**", inline=True)
            em.add_field(name='Result', value=result, inline=False)
            em.set_footer(text=title)
            return em

        p1_choice = user_choice
        p2_choice = other_choice
        if p1_choice == p2_choice:
            color = discord.Color.yellow()
            result = 'It is a tie! 🤝'
        elif (p1_choice, p2_choice) in RPS_WINS:
            color = discord.Color.green()
            result = f"**{self.p1.display_name}** wins! 🎉"
        else:
            color = discord.Color.red()
            result = f"**{self.p2.display_name}** wins! 🎉"
        em = discord.Embed(title='✂️ Rock Paper Scissors', color=color)
        em.add_field(name=self.p1.display_name, value=f"{RPS_EMOJIS[p1_choice]} **{p1_choice.capitalize()}**", inline=True)
        em.add_field(name=self.p2.display_name, value=f"{RPS_EMOJIS[p2_choice]} **{p2_choice.capitalize()}**", inline=True)
        em.add_field(name='Result', value=result, inline=False)
        em.set_footer(text='RPS duel complete')
        return em

    async def on_timeout(self):
        if self.finished:
            return
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class TriviaChoiceButton(discord.ui.Button):
    def __init__(self, label, index):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, row=0)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        v: TriviaView = self.view
        if v.answered:
            return await interaction.response.send_message(embed=err('This question is already over!'), ephemeral=True)
        if interaction.user != v.author:
            return await interaction.response.send_message(embed=err('Only the command user can answer!'), ephemeral=True)
        v.answered = True
        v.selected = self.index
        correct_index = v.correct_index
        for i, child in enumerate(v.children):
            child.disabled = True
            if i == correct_index:
                child.style = discord.ButtonStyle.success
            elif i == self.index and self.index != correct_index:
                child.style = discord.ButtonStyle.danger
            else:
                child.style = discord.ButtonStyle.secondary
        v.embed = v.build_embed(True, False)
        v.stop()
        await interaction.response.edit_message(embed=v.embed, view=v)


class TriviaView(discord.ui.View):
    def __init__(self, author, question, category, options, correct_answer):
        super().__init__(timeout=20)
        self.author = author
        self.question = question
        self.category = category
        self.options = options
        self.correct_answer = correct_answer
        self.correct_index = options.index(correct_answer)
        self.answered = False
        self.selected = None
        self.message = None
        self.embed = self.build_embed()
        for idx, label in enumerate(('A', 'B', 'C', 'D')):
            self.add_item(TriviaChoiceButton(label, idx))

    def build_embed(self, answered=False, timed_out=False):
        if not answered and not timed_out:
            em = discord.Embed(title='🧠 Trivia', description=f'**{self.question}**', color=discord.Color.blue())
            if self.category:
                em.add_field(name='Category', value=self.category, inline=False)
            em.set_footer(text='Choose A, B, C, or D · 20 seconds')
            return em
        if timed_out:
            title = '⏰ Time\'s up!'
            color = discord.Color.red()
            desc = f'The correct answer was **{self.correct_answer}**.'
        elif self.selected == self.correct_index:
            title = '✅ Correct!'
            color = discord.Color.green()
            desc = f'**{self.author.display_name}** got it right!'
        else:
            title = '❌ Wrong!'
            color = discord.Color.red()
            chosen = self.options[self.selected] if self.selected is not None else 'No answer'
            desc = f'You picked **{chosen}**. The correct answer was **{self.correct_answer}**.'
        em = discord.Embed(title=title, description=f'**{self.question}**\n\n{desc}', color=color)
        if self.category:
            em.add_field(name='Category', value=self.category, inline=False)
        em.add_field(name='Answer', value=self.correct_answer, inline=False)
        em.set_footer(text='Trivia complete')
        return em

    async def on_timeout(self):
        if self.answered:
            return
        for child in self.children:
            child.disabled = True
            if child.index == self.correct_index:
                child.style = discord.ButtonStyle.success
        self.embed = self.build_embed(False, True)
        self.stop()
        if self.message:
            try:
                await self.message.edit(embed=self.embed, view=self)
            except Exception:
                pass


class Connect4Button(discord.ui.Button):
    def __init__(self, column):
        row = 0 if column < 5 else 1
        super().__init__(style=discord.ButtonStyle.secondary, label=str(column + 1), row=row)
        self.column = column

    async def callback(self, interaction: discord.Interaction):
        v: Connect4View = self.view
        if interaction.user not in (v.p1, v.p2):
            return await interaction.response.send_message(embed=err('Only the two players can play!'), ephemeral=True)
        if interaction.user != v.current:
            return await interaction.response.send_message(embed=err('Not your turn!'), ephemeral=True)
        if v.children[self.column].disabled:
            return await interaction.response.send_message(embed=err('That column is full!'), ephemeral=True)
        token = 1 if v.current == v.p1 else 2
        if _connect4_drop(v.board, self.column, token) is None:
            self.disabled = True
            await interaction.response.edit_message(content=v.render_content(), view=v)
            return
        winner = _connect4_winner(v.board)
        if winner:
            for child in v.children:
                child.disabled = True
            v.stop()
            name = v.p1.display_name if winner == 1 else v.p2.display_name
            await interaction.response.edit_message(content=v.render_content(f'🏆 **{name}** wins!'), view=v)
            return
        if _connect4_draw(v.board):
            for child in v.children:
                child.disabled = True
            v.stop()
            await interaction.response.edit_message(content=v.render_content('🤝 **Draw!** The board is full.'), view=v)
            return
        v.current = v.p2 if v.current == v.p1 else v.p1
        for idx, child in enumerate(v.children):
            child.disabled = not any(cell == 0 for cell in (row[idx] for row in v.board))
        await interaction.response.edit_message(content=v.render_content(), view=v)


class Connect4View(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=180)
        self.p1 = p1
        self.p2 = p2
        self.current = p1
        self.board = [[0] * 7 for _ in range(6)]
        self.message = None
        for col in range(7):
            self.add_item(Connect4Button(col))

    def render_content(self, suffix=''):
        base = f"🎮 **Connect Four**\n{_render_connect4_board(self.board)}"
        if suffix:
            return base + f"\n\n{suffix}"
        turn = f"🔴 **{self.current.display_name}**'s turn" if self.current == self.p1 else f"🟡 **{self.current.display_name}**'s turn"
        return base + f"\n\n{turn}"

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message:
            try:
                await self.message.edit(content=self.render_content('⏰ Game over.'), view=self)
            except Exception:
                pass


class WYRButton(discord.ui.Button):
    def __init__(self, label, option):
        super().__init__(style=discord.ButtonStyle.primary, label=label, row=0)
        self.option = option

    async def callback(self, interaction: discord.Interaction):
        v: WouldYouRatherView = self.view
        v.votes[interaction.user.id] = self.option
        await interaction.response.edit_message(embed=v.build_embed(), view=v)


class WouldYouRatherView(discord.ui.View):
    def __init__(self, question_a, question_b):
        super().__init__(timeout=120)
        self.question_a = question_a
        self.question_b = question_b
        self.votes = {}
        self.message = None
        self.add_item(WYRButton('🅰', 'a'))
        self.add_item(WYRButton('🅱', 'b'))

    def build_embed(self, final=False):
        count_a = sum(1 for vote in self.votes.values() if vote == 'a')
        count_b = sum(1 for vote in self.votes.values() if vote == 'b')
        total = count_a + count_b
        pct_a = round((count_a / total) * 100) if total else 0
        pct_b = round((count_b / total) * 100) if total else 0
        em = discord.Embed(title='🤔 Would You Rather', color=discord.Color.blurple() if not final else discord.Color.green())
        em.add_field(name='🅰 Option A', value=f'**{self.question_a}**\n`[{_vote_bar(pct_a)}]` {count_a} vote(s) ({pct_a}%)', inline=False)
        em.add_field(name='🅱 Option B', value=f'**{self.question_b}**\n`[{_vote_bar(pct_b)}]` {count_b} vote(s) ({pct_b}%)', inline=False)
        em.set_footer(text=f'Total votes: {total}' + (' · Poll ended' if final else ''))
        return em

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(final=True), view=self)
            except Exception:
                pass


class BlackjackButton(discord.ui.Button):
    def __init__(self, label, action, style):
        super().__init__(label=label, style=style)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        v: BlackjackView = self.view
        if interaction.user != v.player:
            return await interaction.response.send_message(embed=err('Only the command user can play!'), ephemeral=True)
        if v.finished:
            return await interaction.response.send_message(embed=err('This game is already over!'), ephemeral=True)

        if self.action == 'hit':
            v.player_hand.append(_draw_blackjack_card(v.deck))
            player_total = _blackjack_total(v.player_hand)
            if player_total > 21:
                await interaction.response.defer()
                await v.finish(interaction, f'💥 You busted with **{player_total}**. Dealer wins!')
                return
            if player_total == 21:
                await interaction.response.defer()
                await v.stand_and_resolve(interaction, auto=True)
                return
            await interaction.response.defer()
            await v.refresh()
            return

        await interaction.response.defer()
        await v.stand_and_resolve(interaction)


class BlackjackView(discord.ui.View):
    def __init__(self, player, player_hand, dealer_hand, deck):
        super().__init__(timeout=120)
        self.player = player
        self.player_hand = player_hand
        self.dealer_hand = dealer_hand
        self.deck = deck
        self.finished = False
        self.message = None
        self.add_item(BlackjackButton('Hit', 'hit', discord.ButtonStyle.primary))
        self.add_item(BlackjackButton('Stand', 'stand', discord.ButtonStyle.secondary))

    def build_embed(self, reveal=False, result=None):
        player_total = _blackjack_total(self.player_hand)
        dealer_total = _blackjack_total(self.dealer_hand)
        color = discord.Color.gold() if result is None else (discord.Color.green() if 'win' in result.lower() or 'push' in result.lower() else discord.Color.red())
        em = discord.Embed(title='🂡 Blackjack', color=color)
        em.add_field(name=f'{self.player.display_name}', value=f'`{_blackjack_hand_text(self.player_hand)}`\nTotal: **{player_total}**', inline=False)
        dealer_cards = _blackjack_hand_text(self.dealer_hand, hide_second=not reveal)
        dealer_value = f'`{dealer_cards}`\nTotal: **{dealer_total if reveal else "??"}**'
        em.add_field(name='Dealer', value=dealer_value, inline=False)
        if result:
            em.add_field(name='Result', value=result, inline=False)
        elif not reveal:
            em.set_footer(text='Hit or stand to play your hand.')
        return em

    async def refresh(self):
        if self.message:
            await self.message.edit(embed=self.build_embed(), view=self)

    async def stand_and_resolve(self, interaction: discord.Interaction, auto=False):
        while _blackjack_total(self.dealer_hand) < 17:
            self.dealer_hand.append(_draw_blackjack_card(self.deck))
        player_total = _blackjack_total(self.player_hand)
        dealer_total = _blackjack_total(self.dealer_hand)
        if player_total > 21:
            result = f'💥 You busted with **{player_total}**. Dealer wins!'
        elif dealer_total > 21:
            result = f'🎉 Dealer busted with **{dealer_total}**. You win!'
        elif player_total > dealer_total:
            result = f'🎉 You win with **{player_total}** against **{dealer_total}**!'
        elif player_total < dealer_total:
            result = f'😈 Dealer wins with **{dealer_total}** against **{player_total}**.'
        else:
            result = f'🤝 Push! Both ended on **{player_total}**.'
        await self.finish(interaction, result)

    async def finish(self, interaction: discord.Interaction, result):
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True
        self.stop()
        target = self.message or interaction.message
        if target:
            await target.edit(embed=self.build_embed(reveal=True, result=result), view=self)

    async def on_timeout(self):
        if self.finished:
            return
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(reveal=True, result='⏰ Time is up!'), view=self)
            except Exception:
                pass


class MemoryButton(discord.ui.Button):
    def __init__(self, key, emoji, style):
        super().__init__(style=style, emoji=emoji, label='\u200b')
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        v: MemoryView = self.view
        if interaction.user != v.player:
            return await interaction.response.send_message(embed=err('Only the command user can play!'), ephemeral=True)
        if v.finished:
            return await interaction.response.send_message(embed=err('This game is already over!'), ephemeral=True)
        if not v.accepting:
            return await interaction.response.send_message(embed=info('Memory', 'Watch the sequence first!'), ephemeral=True)

        expected = v.sequence[v.position]
        if self.key != expected:
            await interaction.response.send_message(embed=err(f'Wrong button! The streak was **{v.score}**.'), ephemeral=True)
            await v.finish(f'Game over — you reached **{v.score}** round(s).')
            return

        v.position += 1
        if v.position >= len(v.sequence):
            v.score += 1
            await interaction.response.defer()
            await v.advance_round()
            return

        await interaction.response.defer()


class MemoryView(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=300)
        self.player = player
        self.sequence = []
        self.position = 0
        self.score = 0
        self.accepting = False
        self.finished = False
        self.message = None
        self._timeout_handle = None
        for key, emoji, style in MEMORY_COLORS:
            self.add_item(MemoryButton(key, emoji, style))

    def _cancel_timer(self):
        if self._timeout_handle:
            self._timeout_handle.cancel()
            self._timeout_handle = None

    def _arm_timer(self):
        self._cancel_timer()
        loop = asyncio.get_running_loop()
        self._timeout_handle = loop.call_later(30, lambda: asyncio.create_task(self._timeout_expired()))

    def build_embed(self, reveal=False, note=None):
        em = discord.Embed(title='🎵 Memory', color=discord.Color.blurple())
        em.add_field(name='Score', value=f'**{self.score}**', inline=True)
        em.add_field(name='Round', value=f'**{len(self.sequence)}**', inline=True)
        if reveal:
            seq = ' '.join(MEMORY_COLORS[[item[0] for item in MEMORY_COLORS].index(color)][1] for color in self.sequence)
            em.description = f'Memorize this sequence:\n\n{seq}'
        else:
            em.description = 'Repeat the sequence using the color buttons below.'
        if note:
            em.add_field(name='Status', value=note, inline=False)
        return em

    async def _show_sequence(self, note=None):
        self.accepting = False
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(embed=self.build_embed(reveal=True, note=note), view=self)
        await asyncio.sleep(2)
        if self.finished:
            return
        for child in self.children:
            child.disabled = False
        self.accepting = True
        if self.message:
            await self.message.edit(embed=self.build_embed(note='Repeat the sequence!'), view=self)
        self._arm_timer()

    async def start(self):
        self.sequence = [random.choice([key for key, _, _ in MEMORY_COLORS])]
        self.position = 0
        await self._show_sequence('Round 1')

    async def advance_round(self):
        self._cancel_timer()
        self.position = 0
        self.sequence.append(random.choice([key for key, _, _ in MEMORY_COLORS]))
        await self._show_sequence(f'Great job! Score: **{self.score}**')

    async def finish(self, note):
        if self.finished:
            return
        self.finished = True
        self.accepting = False
        self._cancel_timer()
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message:
            await self.message.edit(embed=self.build_embed(note=note), view=self)

    async def _timeout_expired(self):
        if self.finished:
            return
        await self.finish(f'⏰ Time is up! Final score: **{self.score}**')

    async def on_timeout(self):
        await self.finish(f'⏰ Time is up! Final score: **{self.score}**')


class HiloButton(discord.ui.Button):
    def __init__(self, label, action, style):
        super().__init__(label=label, style=style)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        v: HiloView = self.view
        if interaction.user != v.player:
            return await interaction.response.send_message(embed=err('Only the command user can play!'), ephemeral=True)
        if v.finished:
            return await interaction.response.send_message(embed=err('This game is already over!'), ephemeral=True)

        if self.action == 'cashout':
            await interaction.response.defer()
            await v.finish(f'💸 Cash out! You ended with a streak of **{v.streak}**.')
            return

        next_card = random.randint(2, 14)
        current = v.current_value
        correct = next_card > current if self.action == 'higher' else next_card < current
        await interaction.response.defer()
        if next_card == current or not correct:
            await v.finish(f'❌ Wrong! The next card was **{_format_hilo_rank(next_card)}**. Final streak: **{v.streak}**.')
            return

        v.streak += 1
        v.current_value = next_card
        await v.refresh(f'✅ Correct! The card was **{_format_hilo_rank(next_card)}**.')


class HiloView(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=300)
        self.player = player
        self.current_value = random.randint(2, 14)
        self.streak = 0
        self.finished = False
        self.message = None
        self._timeout_handle = None
        self.add_item(HiloButton('⬆ Higher', 'higher', discord.ButtonStyle.success))
        self.add_item(HiloButton('⬇ Lower', 'lower', discord.ButtonStyle.danger))
        self.add_item(HiloButton('Cash out', 'cashout', discord.ButtonStyle.secondary))

    def _cancel_timer(self):
        if self._timeout_handle:
            self._timeout_handle.cancel()
            self._timeout_handle = None

    def _arm_timer(self):
        self._cancel_timer()
        loop = asyncio.get_running_loop()
        self._timeout_handle = loop.call_later(30, lambda: asyncio.create_task(self._timeout_expired()))

    def build_embed(self, note=None):
        em = discord.Embed(title='🔼 Higher or Lower', color=discord.Color.blurple())
        em.add_field(name='Current card', value=f'**{_format_hilo_rank(self.current_value)}**', inline=True)
        em.add_field(name='Streak', value=f'**{self.streak}**', inline=True)
        em.description = 'Predict whether the next card will be higher or lower.'
        if note:
            em.add_field(name='Status', value=note, inline=False)
        return em

    async def refresh(self, note=None):
        if self.message:
            await self.message.edit(embed=self.build_embed(note), view=self)
        self._arm_timer()

    async def finish(self, note):
        if self.finished:
            return
        self.finished = True
        self._cancel_timer()
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message:
            await self.message.edit(embed=self.build_embed(note), view=self)

    async def _timeout_expired(self):
        if self.finished:
            return
        await self.finish(f'⏰ Time is up! Final streak: **{self.streak}**.')

    async def on_timeout(self):
        await self.finish(f'⏰ Time is up! Final streak: **{self.streak}**.')




class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='rps', help='Rock Paper Scissors vs the bot or a member')
    async def rps(self, ctx: commands.Context, opponent: discord.Member = None):
        bot_mode = opponent is None or opponent.bot or opponent == ctx.author
        view = RPSView(ctx.author, opponent if not bot_mode else None)
        em = info('✂️ Rock Paper Scissors', 'Pick Rock, Paper, or Scissors below.')
        if bot_mode:
            em.set_footer(text='Play against the bot')
        else:
            em.set_footer(text=f"{ctx.author.display_name} vs {opponent.display_name}")
        view.message = await ctx.send(embed=em, view=view)

    @commands.command(name='8ball', help='Ask the magic 8ball')
    async def eightball(self, ctx: commands.Context, question: str):
        answers = [
            ('Yes, definitely! ✅', 'positive'),
            ('Without a doubt! ✅', 'positive'),
            ('Most likely! ✅', 'positive'),
            ('Absolutely! ✅', 'positive'),
            ('It is certain! ✅', 'positive'),
            ('The odds are in your favor. ✅', 'positive'),
            ('No doubt about it. ✅', 'positive'),
            ("Don't count on it ❌", 'negative'),
            ('No! ❌', 'negative'),
            ('Very doubtful ❌', 'negative'),
            ('Definitely not ❌', 'negative'),
            ('I would not bet on it. ❌', 'negative'),
            ('The outlook is grim. ❌', 'negative'),
            ('Probably not. ❌', 'negative'),
            ('Ask again later 🤔', 'neutral'),
            ('Not sure 🤔', 'neutral'),
            ('Maybe 🤔', 'neutral'),
            ('Signs point to yes 🤔', 'neutral'),
            ('Reply hazy, try again 🤔', 'neutral'),
            ('The answer is unclear. 🤔', 'neutral'),
        ]
        answer, sentiment = random.choice(answers)
        em = discord.Embed(title='🎱 Magic 8Ball', color={'positive': discord.Color.green(), 'negative': discord.Color.red(), 'neutral': discord.Color.gold()}[sentiment])
        em.add_field(name='❓ Question', value=question, inline=False)
        em.add_field(name='💬 Answer', value=answer, inline=False)
        await ctx.send(embed=em)

    @commands.command(name='coinflip', help='Flip a coin')
    async def coinflip(self, ctx: commands.Context, call: str = None, count: int = 1):
        count = max(1, min(10, count))
        flips = [random.choice(['Heads 🌕', 'Tails 🌑']) for _ in range(count)]
        tally_heads = sum(1 for flip in flips if flip.startswith('Heads'))
        tally_tails = count - tally_heads
        em = discord.Embed(title='🪙 Coin Flip', color=discord.Color.gold())
        em.add_field(name='Results', value='\n'.join(f'{i+1}. **{flip}**' for i, flip in enumerate(flips)), inline=False)
        em.add_field(name='Tally', value=f'Heads: **{tally_heads}**\nTails: **{tally_tails}**', inline=False)
        if call:
            called = 'Heads 🌕' if call == 'heads' else 'Tails 🌑'
            matched = sum(1 for flip in flips if flip.startswith('Heads') == (call == 'heads'))
            if count == 1:
                outcome = 'You win! 🎉' if flips[0].startswith('Heads') == (call == 'heads') else 'You lose! 💀'
                em.add_field(name='Call', value=f'You called **{called}** → {outcome}', inline=False)
                em.color = discord.Color.green() if flips[0].startswith('Heads') == (call == 'heads') else discord.Color.red()
            else:
                em.add_field(name='Call', value=f'You called **{called}** → **{matched}/{count}** matched.', inline=False)
                em.color = discord.Color.green() if matched >= (count / 2) else discord.Color.blurple()
        await ctx.send(embed=em)

    @commands.command(name='roll', help='Roll a dice')
    async def roll(self, ctx: commands.Context, dice: str = '1d6'):
        dice = dice.strip().lower()
        match = re.fullmatch(r'(?:(\d*)d(\d+))(?:([+-]\d+))?', dice)
        if not match:
            return await ctx.send(embed=err('Use dice notation like `1d6`, `2d6`, or `3d8+2`.'))
        count = int(match.group(1) or '1')
        sides = int(match.group(2))
        modifier = int(match.group(3) or '0')
        if count < 1 or count > 100 or sides < 2 or sides > 1000:
            return await ctx.send(embed=err('Dice must be within `1-100` rolls and `2-1000` sides.'))
        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls) + modifier
        modifier_text = f'{modifier:+d}' if modifier else 'none'
        em = discord.Embed(title='🎲 Dice Roll', color=discord.Color.blue())
        em.add_field(name='Notation', value=f'`{count}d{sides}`' + (f'`{modifier:+d}`' if modifier else ''), inline=False)
        em.add_field(name='Rolls', value=' + '.join(str(n) for n in rolls), inline=False)
        em.add_field(name='Modifier', value=modifier_text, inline=True)
        em.add_field(name='Total', value=str(total), inline=True)
        await ctx.send(embed=em)

    @commands.command(name='trivia', help='Answer a trivia question')
    async def trivia(self, ctx: commands.Context, difficulty: str = None):
        payload = await _fetch_opentdb_question(difficulty)
        if not payload:
            payload = _static_trivia_question()
        view = TriviaView(ctx.author, payload['question'], payload['category'], payload['options'], payload['correct'])
        em = view.embed
        msg = await ctx.send(embed=em, view=view)
        view.message = msg

    @commands.command(name='hangman', help='Play hangman')
    async def hangman(self, ctx: commands.Context):
        word = random.choice(HANGMAN_WORDS)
        guessed = set()
        lives = 6

        def display():
            return ' '.join(c if c in guessed else '_' for c in word)

        async def update(gm):
            col = discord.Color.blue() if lives > 2 else discord.Color.red()
            em = discord.Embed(title='🪓 Hangman', color=col)
            em.description = HANGMAN_ART[6 - lives]
            em.add_field(name='Word', value=f'`{display()}`', inline=False)
            em.add_field(name=f'Lives {"❤️" * lives}{"🖤" * (6 - lives)}', value=f"Guessed: `{''.join(sorted(guessed)) or '-'}`", inline=False)
            em.set_footer(text='Type one letter in chat!')
            await gm.edit(embed=em)

        em = discord.Embed(title='🪓 Hangman', description=HANGMAN_ART[0], color=discord.Color.blue())
        em.add_field(name='Word', value=f'`{display()}`')
        em.set_footer(text='Type one letter in chat!')
        gm = await ctx.send(embed=em)

        def check(m):
            return m.channel == ctx.channel and m.author == ctx.author and len(m.content) == 1 and m.content.isalpha()

        while lives > 0 and not all(c in guessed for c in word):
            try:
                g = await self.bot.wait_for('message', check=check, timeout=30.0)
                letter = g.content.lower()
                if letter in guessed:
                    await ctx.channel.send('Already guessed!', delete_after=3)
                    continue
                guessed.add(letter)
                if letter not in word:
                    lives -= 1
                await update(gm)
            except asyncio.TimeoutError:
                return await ctx.send(embed=err(f"⏰ Time's up! Word was **{word}**!"))

        if lives == 0:
            await ctx.send(embed=err(f"💀 You lost! Word was **{word}**!"))
        else:
            await ctx.send(embed=ok(f'You got it: **{word}**! 🎉'))

    @commands.command(name='guess', help='Guess a number between 1 and 100')
    async def guess(self, ctx: commands.Context):
        number = random.randint(1, 100)
        await ctx.send(embed=info('🔢 Guess the Number', 'Number from **1 to 100** — 7 attempts! Type in chat.'))
        def check(m):
            return m.channel == ctx.channel and m.author == ctx.author and m.content.isdigit()
        for attempt in range(1, 8):
            try:
                msg = await self.bot.wait_for('message', check=check, timeout=30.0)
                n = int(msg.content)
                if n == number:
                    return await ctx.send(embed=ok('Correct! 🎉', f'Number was **{number}**! Got it in **{attempt}** attempt(s)!'))
                await ctx.send(embed=info('📈 Higher!' if n < number else '📉 Lower!', f'Attempt {attempt}/7'))
            except asyncio.TimeoutError:
                return await ctx.send(embed=err(f"⏰ Time's up! Number was **{number}**!"))
        await ctx.send(embed=err(f'💀 Out of attempts! Number was **{number}**!'))

    @commands.command(name='tictactoe', help='Play Tic Tac Toe with someone')
    async def tictactoe(self, ctx: commands.Context, opponent: discord.Member):
        if opponent.bot:
            return await ctx.send(embed=err("Can't play against a bot!"))
        if opponent == ctx.author:
            return await ctx.send(embed=err("Can't play against yourself!"))
        view = TTTView(ctx.author, opponent)
        await ctx.send(
            content=f"❌ **{ctx.author.display_name}** vs ⭕ **{opponent.display_name}**\n🎮 **{ctx.author.display_name}'s** turn!",
            view=view,
        )

    @commands.command(name='connect4', help='Play Connect Four with someone')
    async def connect4(self, ctx: commands.Context, opponent: discord.Member):
        if opponent.bot:
            return await ctx.send(embed=err("Can't play against a bot!"))
        if opponent == ctx.author:
            return await ctx.send(embed=err("Can't play against yourself!"))
        view = Connect4View(ctx.author, opponent)
        view.message = await ctx.send(content=view.render_content(), view=view)

    @commands.command(name='wordle', help='Play Wordle')
    async def wordle(self, ctx: commands.Context):
        secret = random.choice(WORDLE_WORDS).upper()
        guesses = []
        attempts = 0
        intro = info('🟩 Wordle', 'Guess the 5-letter word. You have 6 tries. Type your guesses in chat.')
        intro.set_footer(text='120 seconds per guess')
        msg = await ctx.send(embed=intro)

        def check(m):
            return m.channel == ctx.channel and m.author == ctx.author and not m.author.bot

        while attempts < 6:
            try:
                guess_msg = await self.bot.wait_for('message', check=check, timeout=120.0)
            except asyncio.TimeoutError:
                em = discord.Embed(title='🟩 Wordle', description='⏰ Time\'s up! The word was **' + secret + '**.', color=discord.Color.red())
                em.set_footer(text=f'Guesses made: {attempts}/6')
                await msg.edit(embed=em)
                return

            guess = guess_msg.content.strip().upper()
            if len(guess) != 5 or not guess.isalpha():
                await ctx.send(embed=err('Please guess exactly 5 letters.'))
                continue

            attempts += 1
            feedback = _score_wordle_guess(guess, secret)
            guesses.append((guess, feedback))
            em = discord.Embed(title='🟩 Wordle', color=discord.Color.green() if guess == secret else discord.Color.blue())
            em.description = '\n'.join(f'`{g}` {fb}' for g, fb in guesses)
            em.set_footer(text=f'Guess {attempts}/6 · 120 seconds per guess')
            await msg.edit(embed=em)

            if guess == secret:
                em.color = discord.Color.green()
                em.set_footer(text=f'Solved in {attempts}/6 guesses')
                await msg.edit(embed=em)
                return

        em = discord.Embed(title='🟩 Wordle', description='💀 Out of guesses! The word was **' + secret + '**.', color=discord.Color.red())
        em.description = '\n'.join(f'`{g}` {fb}' for g, fb in guesses) + f"\n\n💀 The word was **{secret}**."
        em.set_footer(text='Wordle complete')
        await msg.edit(embed=em)

    @commands.command(name='scramble', help='Unscramble a word')
    async def scramble(self, ctx: commands.Context):
        secret = random.choice(SCRAMBLE_WORDS).upper()
        letters = list(secret)
        shuffled = letters[:]
        while True:
            random.shuffle(shuffled)
            if ''.join(shuffled) != secret:
                break
        em = info('🔀 Scramble', f"Unscramble this word: **{' '.join(shuffled)}**\nYou have 30 seconds!")
        msg = await ctx.send(embed=em)

        def check(m):
            return m.channel == ctx.channel and m.author == ctx.author and not m.author.bot

        try:
            guess_msg = await self.bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await msg.edit(embed=err(f"⏰ Time's up! The word was **{secret.lower()}**!"))
            return

        guess = guess_msg.content.strip().lower()
        if guess == secret.lower():
            await msg.edit(embed=ok('Correct! 🎉', f'The word was **{secret.lower()}**!'))
        else:
            await msg.edit(embed=err(f"Nope! The word was **{secret.lower()}**."))

    @commands.command(name='slots', help='Spin the slot machine')
    async def slots(self, ctx: commands.Context):
        symbols = ['🍒', '🍋', '🍇', '🔔', '⭐', '💎']
        reels = [random.choice(symbols) for _ in range(3)]
        result = f"[ {' | '.join(reels)} ]"
        if len(set(reels)) == 1:
            title = '🎰 Jackpot!'
            desc = f'{result}\n\n🎉 Triple match! Big win!'
            color = discord.Color.gold()
        elif len(set(reels)) == 2:
            title = '🎰 Small win!'
            desc = f'{result}\n\n✨ Two matched — nice spin!'
            color = discord.Color.green()
        else:
            title = '🎰 Slots'
            desc = f'{result}\n\nNo match — try again!'
            color = discord.Color.red()
        await ctx.send(embed=discord.Embed(title=title, description=desc, color=color))

    @commands.command(name='wouldyourather', help='Start a Would You Rather vote')
    async def wouldyourather(self, ctx: commands.Context):
        question_a, question_b = random.choice(WYR_PAIRS)
        view = WouldYouRatherView(question_a, question_b)
        em = view.build_embed()
        view.message = await ctx.send(embed=em, view=view)

    @commands.command(name='poll', help='Create a poll with up to 10 options')
    async def poll(self, ctx: commands.Context, question: str, options: str = None, duration: int = None):
        POLL_EMOJIS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']

        if options:
            choices = [o.strip() for o in options.split('|') if o.strip()]
            if len(choices) < 2:
                return await ctx.send(embed=discord.Embed(description='❌ Provide at least 2 options separated by `|`', color=discord.Color.red()))
            if len(choices) > 10:
                return await ctx.send(embed=discord.Embed(description='❌ Maximum 10 options.', color=discord.Color.red()))
        else:
            choices = None

        em = discord.Embed(title=f'📊 {question}', color=discord.Color.blurple())
        em.set_footer(text=f"Poll by {ctx.author.display_name}" + (f' · Closes in {duration} min' if duration else ''), icon_url=ctx.author.display_avatar.url)
        if choices:
            em.description = '\n'.join(f'{POLL_EMOJIS[i]}  {opt}' for i, opt in enumerate(choices))
        else:
            em.description = 'React with ✅ or ❌ to vote!'

        msg = await ctx.send(embed=em)

        if choices:
            for i in range(len(choices)):
                await msg.add_reaction(POLL_EMOJIS[i])
        else:
            await msg.add_reaction('✅')
            await msg.add_reaction('❌')

        if duration and 1 <= duration <= 1440:
            await asyncio.sleep(duration * 60)
            try:
                msg = await ctx.channel.fetch_message(msg.id)
                active_emojis = POLL_EMOJIS[:len(choices)] if choices else ['✅', '❌']
                results = {str(r.emoji): r.count - 1 for r in msg.reactions if str(r.emoji) in active_emojis}
                total = sum(results.values())
                winner = max(results, key=results.get) if total else None
                lines = []
                for emoji, count in results.items():
                    pct = round(count / total * 100) if total else 0
                    b = '█' * round(pct / 10) + '░' * (10 - round(pct / 10))
                    label = choices[POLL_EMOJIS.index(emoji)] if choices and emoji in POLL_EMOJIS else ('Yes' if emoji == '✅' else 'No')
                    lines.append(f"{emoji} **{label}**\n`[{b}]` {count} vote(s) ({pct}%)" + (' 👑' if emoji == winner and total > 0 else ''))
                close_em = discord.Embed(title=f'📊 Poll Closed — {question}', description='\n\n'.join(lines), color=discord.Color.green())
                close_em.set_footer(text=f'Total votes: {total}')
                await ctx.channel.send(embed=close_em, reference=msg)
            except Exception as e:
                print(f'[Poll close error]: {e}')

    @commands.command(name='joke', help='Tell a joke')
    async def joke(self, ctx: commands.Context):
        jokes = [
            'Why do programmers prefer dark mode? Light attracts bugs! 🐛',
            'How many programmers to change a lightbulb? None — hardware problem! 💡',
            'Why did the scarecrow win an award? Outstanding in his field! 🌾',
            "Why don't scientists trust atoms? They make up everything! ⚛",
            'Why did the bicycle fall over? It was two-tired! 🚲',
            'What do you call fake spaghetti? An impasta! 🍝',
            'Why did the math book look sad? It had too many problems. 📘',
            'What do you call cheese that is not yours? Nacho cheese! 🧀',
            'Why did the golfer bring two pairs of pants? In case he got a hole in one! ⛳',
            'What did one wall say to the other wall? I will meet you at the corner! 🧱',
            'Why can’t your nose be 12 inches long? Because then it would be a foot. 👃',
            'Why did the tomato blush? It saw the salad dressing! 🍅',
            'What do you call a bear with no teeth? A gummy bear! 🐻',
            'Why did the cookie go to the doctor? It felt crummy. 🍪',
            'What did the janitor say when he jumped out of the closet? Supplies! 🧹',
            'Why was the computer cold? It left its Windows open. 🪟',
            'What do you call a sleeping bull? A bulldozer. 🐂',
            'Why did the student eat his homework? The teacher said it was a piece of cake. 🎂',
            'Why did the orange stop? It ran out of juice. 🍊',
            'What do you call a fish wearing a bowtie? Sofishticated. 🐟',
        ]
        await ctx.send(embed=discord.Embed(
            title='😂 Joke', description=random.choice(jokes), color=discord.Color.yellow(),
        ))

    @commands.command(name='avatar', help="Show a member's full-size avatar")
    async def avatar(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        color = member.color if member.color.value else discord.Color.blurple()
        em = discord.Embed(title=f"🖼️ {member.display_name}'s Avatar", color=color)
        em.set_image(url=f"{member.display_avatar.url}?size=1024")
        avatar_hash = member.display_avatar.key
        if avatar_hash:
            cdn = f"https://cdn.discordapp.com/avatars/{member.id}/{avatar_hash}"
            ext = 'gif' if avatar_hash.startswith('a_') else 'png'
            links = [f"[PNG]({cdn}.png?size=1024)", f"[JPG]({cdn}.jpg?size=1024)", f"[WebP]({cdn}.webp?size=1024)"]
            if ext == 'gif':
                links.append(f"[GIF]({cdn}.gif?size=1024)")
            em.description = '**Download:** ' + ' · '.join(links)
        if member.guild_avatar:
            em.set_footer(text='Showing server avatar · member also has a global avatar')
        await ctx.send(embed=em)


    @commands.command(name='blackjack', help='Play blackjack against the dealer')
    async def blackjack(self, ctx: commands.Context):
        deck = _make_blackjack_deck()
        player_hand = [_draw_blackjack_card(deck), _draw_blackjack_card(deck)]
        dealer_hand = [_draw_blackjack_card(deck), _draw_blackjack_card(deck)]
        player_total = _blackjack_total(player_hand)
        dealer_total = _blackjack_total(dealer_hand)
        view = BlackjackView(ctx.author, player_hand, dealer_hand, deck)

        if player_total == 21 or dealer_total == 21:
            if player_total == 21 and dealer_total == 21:
                result = '🤝 Both you and the dealer have blackjack. Push!'
            elif player_total == 21:
                result = '🎉 Natural blackjack! You win!'
            else:
                result = '😈 Dealer has blackjack. You lose!'
            await ctx.send(embed=view.build_embed(reveal=True, result=result))
            return

        view.message = await ctx.send(embed=view.build_embed(), view=view)

    @commands.command(name='minesweeper', help='Play an interactive Minesweeper game')
    async def minesweeper(self, ctx: commands.Context, difficulty: str = 'easy'):
        # All modes use a 5x5 grid — Discord's max 25 buttons (5 rows × 5 cols).
        sizes = {
            'easy':   (5, 5, 5),
            'medium': (5, 5, 8),
            'hard':   (5, 5, 12),
        }
        rows, cols, mines = sizes.get(difficulty, sizes['easy'])
        board = _make_minesweeper_board(rows, cols, mines)
        view  = MinesweeperView(ctx.author, board, difficulty)
        em    = view.build_embed()
        em.set_footer(text='Click any cell to reveal it. Good luck!')
        view.message = await ctx.send(embed=em, view=view)

    @commands.command(name='memory', help='Simon-style sequence memory')
    async def memory(self, ctx: commands.Context):
        view = MemoryView(ctx.author)
        view.message = await ctx.send(embed=info('🎵 Memory', 'Watch the sequence, then repeat it with the buttons below.'), view=view)
        await view.start()

    @commands.command(name='hilo', help='Guess whether the next card will be higher or lower')
    async def hilo(self, ctx: commands.Context):
        view = HiloView(ctx.author)
        view.message = await ctx.send(embed=view.build_embed(), view=view)
        view._arm_timer()

    @commands.command(name='quickmath', help='First correct answer in the channel wins')
    async def quickmath(self, ctx: commands.Context):
        op = random.choice(['+', '-', '×'])
        if op == '+':
            a, b = random.randint(1, 30), random.randint(1, 30)
            answer = a + b
        elif op == '-':
            a = random.randint(10, 50)
            b = random.randint(1, a)
            answer = a - b
        else:
            a, b = random.randint(2, 12), random.randint(2, 12)
            answer = a * b
        question = f'**{a} {op} {b} = ?**'
        em = info('⚡ Quick Math', f'First correct answer in the channel wins!\n\n{question}')
        em.set_footer(text='20 seconds')
        msg = await ctx.send(embed=em)
        start = time.monotonic()

        def check(m):
            return m.channel == ctx.channel and not m.author.bot

        while True:
            try:
                m = await self.bot.wait_for('message', check=check, timeout=20.0)
            except asyncio.TimeoutError:
                await msg.edit(embed=err(f'⏰ Time is up! The answer was **{answer}**.'))
                return
            try:
                guess = int(m.content.strip())
            except ValueError:
                continue
            if guess == answer:
                elapsed = time.monotonic() - start
                await msg.edit(embed=ok('Correct! 🎉', f'**{m.author.display_name}** answered first in **{elapsed:.2f}s**.\nAnswer: **{answer}**'))
                return

    @commands.command(name='emojiquiz', help='Guess the answer from emoji clues')
    async def emojiquiz(self, ctx: commands.Context):
        clue, answer = random.choice(EMOJI_QUIZ)
        em = info('🧩 Emoji Quiz', f'What does this mean?\n\n**{clue}**')
        em.set_footer(text='30 seconds · invoker only')
        msg = await ctx.send(embed=em)

        def check(m):
            return m.channel == ctx.channel and m.author == ctx.author and not m.author.bot

        try:
            guess_msg = await self.bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await msg.edit(embed=err(f'⏰ Time is up! The answer was **{answer}**.'))
            return

        if _normalize_text(guess_msg.content) == _normalize_text(answer):
            await msg.edit(embed=ok('Correct! 🎉', f'Answer: **{answer}**'))
        else:
            await msg.edit(embed=err(f'Nope! The answer was **{answer}**.'))

    @commands.command(name='typerace', help='First to retype a phrase wins')
    async def typerace(self, ctx: commands.Context):
        phrase = random.choice(TYPERACE_PHRASES)
        hidden = '\u200b'.join(phrase)
        em = info('⌨️ Typerace', f'Retype this phrase:\n\n**{hidden}**')
        em.set_footer(text='30 seconds · first correct message wins')
        msg = await ctx.send(embed=em)
        start = time.monotonic()
        target = _normalize_phrase(phrase)

        def check(m):
            return m.channel == ctx.channel and not m.author.bot

        while True:
            try:
                typed = await self.bot.wait_for('message', check=check, timeout=30.0)
            except asyncio.TimeoutError:
                await msg.edit(embed=err(f'⏰ Time is up! The phrase was **{phrase}**.'))
                return
            if _normalize_phrase(typed.content) == target:
                elapsed = time.monotonic() - start
                await msg.edit(embed=ok('Fastest typer! 🎉', f'**{typed.author.display_name}** won in **{elapsed:.2f}s**.\nPhrase: **{phrase}**'))
                return

async def setup(bot):
    await bot.add_cog(Games(bot))
