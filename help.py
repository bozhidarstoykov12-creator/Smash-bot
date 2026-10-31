import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from utils import safe_interaction_send

# ── Category data ─────────────────────────────────────────────────────────────

CATEGORIES = [
    {
        "label": "🏠 Home",
        "emoji": "🏠",
        "title": "SMASH BOT — Help Menu",
        "color": discord.Color.blurple(),
        "description": (
            "Welcome to **SMASH BOT** — the all-in-one bot for your Smash Karts community!\n\n"
            "**🛡️ Moderation** — Ban, mute, warn, anti-scam protection & more\n"
            "**🎫 Tickets** — Full ticket system with panels, transcripts & automations\n"
            "**🎵 Music** — Stream music from YouTube & Spotify with queue and DJ controls\n"
            "**🎮 Games** — Fun commands and mini-games\n"
            "**📊 Leveling** — XP, ranks, and leaderboards\n"
            "**🤖 Smash Bot AI** — Powered by Gemini or ChatGPT (your choice)\n"
            "**⏰ Reminders** — Personal reminders via DM\n"
            "**🎉 Giveaways** — Host and manage giveaways\n"
            "**🏎️ Tournament** — Smash Karts tournaments with bracket & round robin\n"
            "**📖 Story** — One Word Story game\n"
            "**🔢 Counting** — Counting game with leaderboard\n"
            "**📺 YouTube** — YouTube upload alerts\n"
            "**📅 Daily** — Fact, Quote, Riddle, Song of the Day\n"
            "**📈 Stats** — Server statistics\n"
            "**💬 Dead Chat** — Pings inactive members to revive quiet servers\n"
            "**📨 Invites** — Tracks who invited who, with a leaderboard\n"
            "**🧩 Custom Commands** — Admin-built `!commands` with a text reply, no code needed\n"
            "**🕹️ Arcade** — Reflex mini-games (reaction, simon, whack, 2048, snake, bullseye)\n"
            "**🤖 AI Games** — Word & trivia games powered by AI\n"
            "**🎉 Party Games** — Mafia, Spyfall, Battleship & more for groups\n"
            "**🧭 RPG & Collections** — Fishing, pets, gardening, quests & badges\n"
            "**🎰 Casino** — 18 betting games with configurable limits\n"
            "**🎟️ Lottery & Duels** — Weekly lottery draw and 1v1 currency duels\n"
            "**🪙 Economy** — Server currency, shop, activities & items\n"
            "**🪙 Economy 2.0** — /work, /rob, /crime, /beg, bank & interest\n"
            "**🌐 Language** — Built-in bot replies can be set to English or Bulgarian per server\n\n"
            "**Tip:** Commands with `[...]` have optional options.\n"
            "**⚠️ Two command types:** Most commands below use **slash (`/`)**. "
            "**Moderation, Games, and Tournament** commands still use the classic **`!` prefix** — "
            "see the ⚠️ note in those categories before using them.\n"
            "**🎛️ Everything is configurable** from the Web Dashboard — including per-game reward multipliers."
        ),
        "fields": []
    },
    {
        "label": "🛡️ Moderation",
        "emoji": "🛡️",
        "title": "🛡️ Moderation",
        "color": discord.Color.red(),
        "description": (
            "Commands for managing members and the server. Most require **Manage Guild** or **Moderator** permissions.\n\n"
            "**⚠️ These are `!` prefix commands, not slash commands.** They only work by typing the prefix "
            "(default `!`, changeable per-server on the Web Dashboard) directly in a channel — they will **not** "
            "show up when you type `/`. Because they're normal messages, everyone in the channel can see the exact "
            "command (and any reason/argument you typed) — double-check the channel and wording before sending, "
            "especially for bans/kicks/warns."
        ),
        "fields": [
            ("Punishments", (
                "`!ban @member [reason] [days]` — Permanently ban\n"
                "`!unban <user_id>` — Unban by ID\n"
                "`!softban @member [reason]` — Ban + unban (clears messages)\n"
                "`!tempban @member <hours> [reason]` — Temporary ban\n"
                "`!kick @member [reason]` — Kick from server\n"
                "`!mute @member <minutes> [reason]` — Timeout member\n"
                "`!unmute @member` — Remove timeout"
            )),
            ("Warnings", (
                "`!warn @member <reason>` — Warn a member\n"
                "`!warnings @member` — View warnings\n"
                "`!clearwarns @member` — Clear all warnings\n"
                "`!note @member <note>` — Add private mod note\n"
                "`!notes @member` — View mod notes"
            )),
            ("Channel Management", (
                "`!clear <amount>` — Delete messages (1–100)\n"
                "`!slowmode <seconds>` — Set slowmode (0 to disable)\n"
                "`!lock [channel]` — Lock a channel\n"
                "`!unlock [channel]` — Unlock a channel\n"
                "`!hide [channel]` — Hide from members\n"
                "`!unhide [channel]` — Unhide channel"
            )),
            ("Server Tools", (
                "`!nick @member [nickname]` — Change/reset nickname\n"
                "`!role @member @role` — Add or remove a role\n"
                "`!massrole add/remove @role` — Apply role to all\n"
                "`!announce <title> <message> [channel]` — Send embed announcement\n"
                "`!setbotavatar [url|attachment]` — Change bot's profile picture\n"
                "`!setbotname <new_name>` — Change bot's global username\n"
                "`!setlogchannel #channel` — Set mod log channel\n"
                "`!setwelcome #channel` — Set welcome channel\n"
                "`!setautorole @role` — Set auto-role for new members\n"
                "`!serverinfo` — Show server info\n"
                "`!userinfo [@member]` — Detailed member info"
            )),
        ]
    },
    {
        "label": "🎮 Games",
        "emoji": "🎮",
        "title": "🎮 Games & Fun",
        "color": discord.Color.purple(),
        "description": (
            "Fun commands and mini-games anyone can use.\n\n"
            "**⚠️ These are `!` prefix commands, not slash commands.** Type them directly in chat with the "
            "server's prefix (default `!`) — they won't appear in the `/` command list."
        ),
        "fields": [
            ("Mini-Games", (
                "`!rps <choice>` — Rock Paper Scissors vs the bot\n"
                "`!tictactoe @member` — Play Tic Tac Toe with someone\n"
                "`!connect4 @member` — Play Connect Four with someone\n"
                "`!wordle` — Play Wordle\n"
                "`!hangman` — Play Hangman (guess the word!)\n"
                "`!trivia` — Answer a random trivia question\n"
                "`!guess` — Guess a number between 1–100\n"
                "`!scramble` — Unscramble a word\n"
                "`!minesweeper` — Play an interactive Minesweeper game\n"
                "`!memory` — Simon-style sequence memory\n"
                "`!hilo` — Higher/Lower card guessing game\n"
                "`!blackjack` — Play blackjack against the dealer\n"
                "`!slots` — Spin the slot machine"
            )),
            ("Chat Games", (
                "`!quickmath` — First correct math answer wins\n"
                "`!emojiquiz` — Guess the answer from emoji clues\n"
                "`!typerace` — First to retype a phrase wins"
            )),
            ("Random & Fun", (
                "`!8ball <question>` — Ask the magic 8-ball\n"
                "`!coinflip` — Flip a coin\n"
                "`!roll [sides]` — Roll a dice (default: 6)\n"
                "`!joke` — Get a random joke\n"
                "`!avatar [@member]` — Show full-size avatar"
            )),
            ("Poll", (
                "`!poll <question> [options]` — Create a poll (up to 10 options)\n"
                "`!wouldyourather` — Start a Would You Rather vote\n"
                "• Separate options with `|` e.g. `Yes|No|Maybe`\n"
                "• Leave options empty for 👍👎 poll"
            )),
        ]
    },
    {
        "label": "📊 Leveling",
        "emoji": "📊",
        "title": "📊 Leveling & XP",
        "color": discord.Color.gold(),
        "description": "Earn XP by chatting in the server and climb the leaderboard!",
        "fields": [
            ("Commands", (
                "`/rank [@member]` — Check XP, level, and progress bar\n"
                "`/leaderboard [page]` — Server XP leaderboard (10 per page)\n"
                "`!userinfo [@member]` — Also shows level, XP, and badges *(prefix command)*\n"
                "`/addxp @member <amount>` — Add or remove XP *(Manage Guild)*"
            )),
            ("How XP Works", (
                "• Send messages to earn XP\n"
                "• Cooldown between XP gains (no spam farming)\n"
                "• Level formula: each level requires more XP than the last\n"
                "• Badges are awarded at milestone levels"
            )),
        ]
    },
    {
        "label": "🤖 AI",
        "emoji": "🤖",
        "title": "🤖 Smash Bot AI",
        "color": discord.Color.teal(),
        "description": "Powered by **Google Gemini** or **ChatGPT (OpenAI)** — pick the provider and model in the dashboard. Can chat, generate images, and more.",
        "fields": [
            ("How to Use", (
                "**Mention the bot** in any message to chat with the AI\n"
                "Example: `@SMASH BOT what is the capital of France?`\n\n"
                "**Generate an image:** Include `generate image` or `create image` in your message\n"
                "Example: `@SMASH BOT generate image of a racing kart`"
            )),
            ("Commands", (
                "`/ai_status` — Check AI load, daily usage stats\n"
                "`/ai_emoji <description>` — Render a custom emoji into chat via AI"
            )),
            ("Notes", (
                "• The AI remembers context within the same conversation thread\n"
                "• Image generation may be slower than text responses\n"
                "• Responses are filtered to keep the chat safe"
            )),
        ]
    },
    {
        "label": "⏰ Reminders",
        "emoji": "⏰",
        "title": "⏰ Personal Reminders",
        "color": discord.Color.green(),
        "description": "Set reminders and the bot will DM you when they fire. Survives bot restarts!",
        "fields": [
            ("Commands", (
                "`/remind <duration> <message>` — Set a reminder\n"
                "`/reminders` — List your active reminders\n"
                "`/remindcancel <id>` — Cancel a reminder by ID"
            )),
            ("Duration Formats", (
                "`30s` — 30 seconds\n"
                "`15m` — 15 minutes\n"
                "`2h` — 2 hours\n"
                "`1d` — 1 day\n"
                "`1h30m` — 1 hour 30 minutes\n"
                "Maximum: **30 days** | Up to **10 active** reminders per user"
            )),
        ]
    },
    {
        "label": "🎉 Giveaways",
        "emoji": "🎉",
        "title": "🎉 Giveaway System",
        "color": discord.Color.gold(),
        "description": "Host giveaways with automatic winner selection. Requires **Manage Guild**.",
        "fields": [
            ("Commands", (
                "`/giveaway <duration> <prize> [winners] [channel]` — Start a giveaway\n"
                "`/gend <message_id>` — End a giveaway early\n"
                "`/greroll <message_id>` — Re-roll new winners"
            )),
            ("How it Works", (
                "1. Admin runs `/giveaway 1h Nitro Classic winners:2`\n"
                "2. Bot posts an embed — members react with 🎉 to enter\n"
                "3. When time's up, bot picks random winners and announces them\n"
                "4. Use `/greroll` if a winner doesn't claim their prize\n\n"
                "• Duration formats: `10m`, `2h`, `1d` (max 30 days)\n"
                "• Up to **20 winners** per giveaway\n"
                "• Giveaways survive bot restarts"
            )),
        ]
    },
    {
        "label": "🏎️ Tournament",
        "emoji": "🏎️",
        "title": "🏎️ Smash Karts Tournament",
        "color": discord.Color.orange(),
        "description": (
            "Run single-elimination Smash Karts tournaments with bracket generation, interactive button signups, "
            "substitutes, score tracking, match forfeits, and fan predictions — via `!` commands below or from the Web Dashboard.\n\n"
            "**⚠️ Prefix Commands (`!`):** Type these directly in chat using your server prefix."
        ),
        "fields": [
            ("Setup & Management (Admin / Host)", (
                "`!tcreate <name> [max_teams] [1v1|2v2|3v3] [max_subs] ...` — Create tournament\n"
                "`!tpanel` — Post interactive registration panel with buttons (Join, Sub, Leave, Roster)\n"
                "`!tadd @member [team_name] [display_name]` — Force-add a member to a team\n"
                "`!tremove @member` — Remove a member from their team\n"
                "`!tswap @member @sub_member` — Swap active team member with a substitute\n"
                "`!tstart` — Generate bracket tree and start tournament\n"
                "`!tend` — Cancel/end active tournament\n"
                "`!tmaps [ffa|ctf]` — List available Smash Karts arena maps"
            )),
            ("Registration & Players", (
                "`!tjoin [team_name] [display_name]` — Join tournament / team\n"
                "`!tsub <team_name> [display_name]` — Join a team as a substitute\n"
                "`!tleave` — Leave your current team (registration phase)\n"
                "`!tplayers` — View all registered teams and substitutes\n"
                "`!tbracket` — View live bracket tree and current match statuses"
            )),
            ("Match Results & Forfeits", (
                "`!tresult <round> <match> @winner [score]` — Record match winner & score (e.g. `2-1`)\n"
                "`!tforfeit <round> <match> <winner_team> [score] [reason]` — Forfeit match with reason\n"
                "• Admins **and** members with the **Referee Role** can report results\n"
                "• Match scores and forfeit reasons are posted automatically to the Results channel\n"
                "• Winner auto-advances in the bracket tree"
            )),
            ("Fan Predictions & Web Extras", (
                "`!tpredict` — Get direct link to the Web Fan Predictions page (`/predict/<gid>`)\n"
                "`!tpredictionlead` — View fan prediction leaderboard (+3 pts winner, +2 pts exact score)\n"
                "• **Bracket Channel** — Self-updating live bracket embed\n"
                "• **Match Threads** — Automatic Discord threads per match with player pings\n"
                "• **Recurring Cycles** — Automatically opens a new tournament cycle every N days"
            )),
        ]
    },
    {
        "label": "🎫 Tickets",
        "emoji": "🎫",
        "title": "🎫 Ticket System",
        "color": discord.Color.red(),
        "description": "Full-featured ticket system — panels, transcripts, limits, automations and more.",
        "fields": [
            ("Admin — Panel Setup", (
                "`/ticket setup <panel> [#channel]` — Send a panel to a channel\n"
                "`/ticket update-counts <panel> <count>` — Override ticket counter"
            )),
            ("Staff — In Ticket", (
                "`/ticket close [reason]` — Close the current ticket\n"
                "`/ticket reopen` — Reopen a closed ticket\n"
                "`/ticket delete` — Delete ticket & save transcript\n"
                "`/ticket add @user` — Add a user to the ticket\n"
                "`/ticket remove @user` — Remove a user from the ticket\n"
                "`/ticket automation` — View automation config for this ticket"
            )),
            ("Members", (
                "`/ticket create <panel>` — Manually create a ticket (if enabled)\n"
                "• Or click the panel button / select menu in the ticket channel"
            )),
        ]
    },
    {
        "label": "📖 Story",
        "emoji": "📖",
        "title": "📖 One Word Story",
        "color": discord.Color.purple(),
        "description": "Build a story together — one word at a time! Each message must be a single word.",
        "fields": [
            ("Setup (Admin)", (
                "`/story setchannel #channel` — Set the story channel\n"
                "`/story enable` — Enable the story game\n"
                "`/story disable` — Disable the story game\n"
                "`/story reset` — Reset the story"
            )),
            ("View", (
                "`/story show` — Show the current story\n"
                "`/story info` — Channel and settings info\n"
                "`/story stats [@member]` — View a user's story contributions\n"
                "`/story lb` — Story contribution leaderboard"
            )),
            ("Rules", (
                "• One word per message in the story channel\n"
                "• You can't send two words in a row — wait for someone else!\n"
                "• The story grows until reset"
            )),
        ]
    },
    {
        "label": "🔢 Counting",
        "emoji": "🔢",
        "title": "🔢 Counting Game",
        "color": discord.Color.blue(),
        "description": "Count together as a server! One number at a time, and don't ruin the count!",
        "fields": [
            ("Setup (Admin)", (
                "`/counting setchannel #channel` — Set the counting channel\n"
                "`/counting enable` — Enable counting\n"
                "`/counting disable` — Disable counting\n"
                "`/counting goal <number>` — Set a counting goal\n"
                "`/counting reset` — Reset count to 0\n"
                "`/counting shamerole @role` — Role given when count is ruined\n"
                "`/counting allowsameuser` — Toggle same-user counting"
            )),
            ("View", (
                "`/counting info` — Current count and settings\n"
                "`/counting stats [@member]` — A user's counting stats\n"
                "`/counting lb` — Counting leaderboard\n"
                "`/counting fails` — Hall of shame (who ruined it most)"
            )),
        ]
    },
    {
        "label": "📺 YouTube",
        "emoji": "📺",
        "title": "📺 YouTube Alerts",
        "color": discord.Color.red(),
        "description": "Get notified when a YouTube channel uploads a new video. Requires **Manage Guild**.",
        "fields": [
            ("Commands", (
                "`/youtube add <url> #channel` — Track a YouTube channel\n"
                "`/youtube remove <channel_id>` — Remove a tracker\n"
                "`/youtube list` — List all tracked channels\n"
                "`/youtube toggle <channel_id>` — Enable/disable an alert\n"
                "`/youtube setmessage <channel_id> <message>` — Custom alert message\n"
                "`/youtube latest <channel_id>` — Show the latest video\n"
                "`/youtube test <channel_id>` — Send a test alert"
            )),
            ("Custom Message Variables", (
                "`{title}` — Video title\n"
                "`{url}` — Video URL\n"
                "`{channel}` — Channel name\n"
                "`{date}` — Upload date"
            )),
        ]
    },
    {
        "label": "📅 Daily",
        "emoji": "📅",
        "title": "📅 Daily Modules",
        "color": discord.Color.green(),
        "description": "Automated daily posts — Fact, Quote, Riddle, and Song of the Day. Requires **Manage Guild** to configure.",
        "fields": [
            ("Commands", (
                "`/fotd_submit <channel>` — Set Fact of the Day channel\n"
                "`/qotd_submit <channel>` — Set Quote of the Day channel\n"
                "`/rotd_submit <riddle> <answer>` — Submit a Riddle of the Day\n"
                "`/sotd_submit <channel>` — Set Song of the Day channel\n\n"
                "`/fotd_queue` — View the fact queue\n"
                "`/qotd_queue` — View the quote queue\n"
                "`/rotd_queue` — View the riddle queue\n"
                "`/sotd_queue` — View the song queue"
            )),
            ("Manual Trigger (Admin)", (
                "`/trigger_fotd` — Send Fact of the Day now\n"
                "`/trigger_qotd` — Send Quote of the Day now\n"
                "`/trigger_rotd` — Send Riddle of the Day now\n"
                "`/trigger_sotd` — Send Song of the Day now"
            )),
        ]
    },
    {
        "label": "📈 Stats",
        "emoji": "📈",
        "title": "📈 Server Stats",
        "color": discord.Color.blurple(),
        "description": "Server statistics and bot information.",
        "fields": [
            ("Commands", (
                "`/stats` — Full server stats: member counts, channels, boosts, uptime, top XP earners\n"
                "`!serverinfo` — Server info (also in Moderation) *(prefix command)*\n"
                "`!userinfo [@member]` — Member info with XP, level, warnings, badges *(prefix command)*"
            )),
        ]
    },
    {
        "label": "📊 Server Stats",
        "emoji": "📊",
        "title": "📊 Server Stats Counter Channels",
        "color": discord.Color.blurple(),
        "description": "Live voice channels that display member/bot/boost counts and update automatically. Moderators only (Manage Server). Configure everything from the Web Dashboard → Server Stats.",
        "fields": [
            ("Commands", (
                "`/serverstats setup` — Create the live stat counter channels\n"
                "`/serverstats refresh` — Update the stat channels right now\n"
                "`/serverstats disable` — Pause auto-updates (keeps the channels)\n"
                "`/serverstats remove` — Delete the stat channels and category"
            )),
        ]
    },
    {
        "label": "🛡️ AutoMod",
        "emoji": "🛡️",
        "title": "🛡️ AutoMod & Anti-Scam",
        "color": discord.Color.red(),
        "description": "Automatic moderation to keep your server safe. **Configured exclusively via the Web Dashboard.**",
        "fields": [
            ("Features", (
                "• **Anti-Spam** — Deletes/timeouts users flooding messages\n"
                "• **Anti-Caps** — Deletes all-caps messages\n"
                "• **Mass-Mention** — Blocks messages pinging too many users\n"
                "• **Emoji Spam** — Blocks excessive emojis\n"
                "• **Repeated Message** — Deletes copy-paste spam\n"
                "• **Link Filter** — Blocks URLs (with optional whitelist)\n"
                "• **Anti-Raid** — Auto-locks server during bot attacks\n"
                "• **Anti-Scam** — Blocks phishing links and scam images via OCR"
            )),
            ("Configuration", (
                "AutoMod does not use Discord commands.\n"
                "To configure it, please log in to the **Web Dashboard**, navigate to your server settings, and open the AutoMod tab."
            )),
        ]
    },
    {
        "label": "💬 Dead Chat",
        "emoji": "💬",
        "title": "💬 Dead Chat Reviver",
        "color": discord.Color.dark_teal(),
        "description": (
            "Automatically pings members who've gone quiet, to bring the server back to life. "
            "**Enable/configure via the Web Dashboard** (inactivity window, cooldown, ping channel/role, message)."
        ),
        "fields": [
            ("Commands", (
                "`/deadchat check` — Run an inactivity check now and ping inactive members *(Manage Guild)*\n"
                "`/deadchat list` — List inactive members without pinging them *(Manage Guild)*"
            )),
            ("How it Works", (
                "• Tracks the last time each member sent a message\n"
                "• Every 6 hours, checks for members inactive past the configured threshold\n"
                "• Pings them in the configured channel with a customizable message\n"
                "• A cooldown prevents re-pinging the same member too often\n"
                "• Roles can be included/excluded from checks via the dashboard"
            )),
        ]
    },
    {
        "label": "📨 Invites",
        "emoji": "📨",
        "title": "📨 Invite Tracker",
        "color": discord.Color.blurple(),
        "description": (
            "Tracks who invited who, with fake/alt detection. "
            "**Enable/configure via the Web Dashboard** (log channel, log message, fake-account threshold)."
        ),
        "fields": [
            ("Commands", (
                "`/invites stats [@member]` — Check how many people someone has invited\n"
                "`/invites leaderboard` — Show the top inviters in this server\n"
                "`/invites add @member <amount>` — Add bonus invites to a member *(Manage Guild)*\n"
                "`/invites remove @member <amount>` — Remove bonus invites from a member *(Manage Guild)*"
            )),
            ("How it Works", (
                "• Total = Regular + Bonus − Left\n"
                "• **Regular** — invites from members who joined and stayed\n"
                "• **Left** — invited members who later left the server\n"
                "• **Fake/flagged** — accounts younger than the configured threshold when they joined "
                "(excluded from totals unless the dashboard is set to count them)\n"
                "• **Bonus** — manually added/removed by staff with `/invites add` and `/invites remove`"
            )),
        ]
    },
    {
        "label": "🧩 Custom Commands",
        "emoji": "🧩",
        "title": "🧩 Custom Commands",
        "color": discord.Color.blurple(),
        "description": (
            "Admins build simple `!command → text reply` pairs straight from the Web Dashboard — "
            "no coding required. **Configured exclusively via the Web Dashboard's Custom Commands page.**"
        ),
        "fields": [
            ("How it Works", (
                "1. On the dashboard, add a **trigger word** and a **response**\n"
                "2. Any member types the server's prefix + trigger (e.g. `!rules`) anywhere in the server\n"
                "3. The bot replies with your configured text — that's it\n\n"
                "• A short cooldown per user/trigger stops spam\n"
                "• Trigger words that match a real bot command (like `ban` or `poll`) are blocked automatically, "
                "so custom commands can never override built-in ones"
            )),
            ("Response Variables", (
                "`{user.mention}` — Pings whoever ran the command\n"
                "`{user.name}` — Their username\n"
                "`{server.name}` — This server's name"
            )),
        ]
    },
    {
        "label": "🪙 Economy",
        "emoji": "🪙",
        "title": "🪙 Economy",
        "color": discord.Color.gold(),
        "description": "Earn and spend the server's currency. **Configure the shop, currency name/emoji, earn rates, activity payouts and per-game rewards via the Web Dashboard.**",
        "fields": [
            ("Commands", (
                "`/economy balance [@member]` — Check a currency balance\n"
                "`/economy pay @member <amount>` — Send currency to another member\n"
                "`/economy shop` — View items available to buy\n"
                "`/economy buy <item>` — Purchase an item from the shop\n"
                "`/economy inventory [@member]` — View purchased items\n"
                "`/economy leaderboard` — Top currency holders\n"
                "`/economy add @member <amount>` — Add or remove currency *(Manage Guild)*"
            )),
            ("Activities (Economy 2.0)", (
                "`/work` — Do a shift for a random payout (cooldown applies)\n"
                "`/rob @member` — Attempt to steal from another member's wallet\n"
                "`/crime` — Commit a crime for a big payout — or a fine\n"
                "`/beg` — Beg for a small amount of spare change\n"
                "`/bank` — View your wallet, bank balance and interest info\n"
                "`/deposit <amount>` — Move currency from your wallet into the bank\n"
                "`/withdraw <amount>` — Move currency from the bank back to your wallet"
            )),
            ("How it Works", (
                "• Earn currency from chatting, counting, leveling up, activities and games (configurable)\n"
                "• Shop items can be roles, XP boosts, counting-mistake saves, or custom rewards\n"
                "• `amount` can be negative in `/economy add` to remove currency\n"
                "• Every activity payout, cooldown and success rate is tunable from **Dashboard ➜ Games & Fun**"
            )),
        ]
    },
    {
        "label": "🎰 Casino",
        "emoji": "🎰",
        "title": "🎰 Casino",
        "color": discord.Color.from_rgb(255, 196, 0),
        "description": "Bet your currency on 19 casino games. **Configure min/max bets, daily loss limits, cooldown and a casino-only channel via the Web Dashboard.**",
        "fields": [
            ("Slots & Reels", (
                "`/casino slots` — Spin the slot machine\n"
                "`/casino wheel` — Spin the prize wheel\n"
                "`/casino scratch` — Buy a scratch card\n"
                "`/casino plinko` — Drop a ball down the plinko board"
            )),
            ("Cards & Dice", (
                "`/casino blackjack` — Play blackjack against the dealer\n"
                "`/casino hilo` — Guess if the next card is higher or lower\n"
                "`/casino dice` — Roll two dice and bet on the total\n"
                "`/casino coinflip` — Bet on a coin flip (2x)\n"
                "`/casino streak` — Guess coin flips in a row to grow your multiplier"
            )),
            ("Numbers & Luck", (
                "`/casino roulette` — Bet on a roulette spin\n"
                "`/casino keno` — Pick numbers and hope they are drawn\n"
                "`/casino limbo` — Pick a target multiplier — win if the roll beats it\n"
                "`/casino crash` — Cash out before the multiplier crashes\n"
                "`/casino mines` — Reveal safe tiles and cash out before hitting a mine\n"
                "`/casino tower` — Climb the tower and cash out before you fall\n"
                "`/casino predict` — Predict a number between 1 and 10\n"
                "`/casino race` — Bet on a racer\n"
                "`/casino bossraid` — Join a raid on the boss for a chance at loot"
            )),
            ("How it Works", (
                "• All bets are validated against your wallet and the server's min/max bet limits\n"
                "• A daily loss limit can stop you from losing more than a set amount per day\n"
                "• Admins can restrict casino games to a single channel from the dashboard"
            )),
        ]
    },
    {
        "label": "🎟️ Lottery & Duels",
        "emoji": "🎟️",
        "title": "🎟️ Lottery & Duels",
        "color": discord.Color.teal(),
        "description": "Buy lottery tickets for the weekly draw, or challenge another member to a currency duel. **Configure ticket price, payout % and duel payout via the Web Dashboard.**",
        "fields": [
            ("Lottery", (
                "`/lottery buy [tickets]` — Buy one or more tickets for the next draw\n"
                "`/lottery info` — See the current pot, ticket price and draw time\n"
                "• A winner is drawn automatically every Friday — the pot is paid out to one lucky ticket holder"
            )),
            ("Duels", (
                "`/duel @member <bet>` — Challenge someone to a coin-flip duel for their bet\n"
                "• Both players stake the same amount; the winner takes the pot (minus the house cut)\n"
                "• Bets are refunded automatically if a duel is cancelled or times out"
            )),
        ]
    },
    {
        "label": "🕹️ Arcade",
        "emoji": "🕹️",
        "title": "🕹️ Arcade Games",
        "color": discord.Color.blue(),
        "description": "Fast, reflex-based mini-games that pay out currency. **Enable/disable each game and scale its reward from the Web Dashboard.**",
        "fields": [
            ("Commands", (
                "`/reaction` — Tap the button the instant it turns green\n"
                "`/simon` — Repeat the growing colour sequence\n"
                "`/whack` — Whack the mole before it hides\n"
                "`/2048` — Slide tiles to reach 2048\n"
                "`/snake` — Eat apples and avoid your own tail\n"
                "`/bullseye` — Stop the moving marker on the target"
            )),
            ("How it Works", (
                "• Each game rewards currency based on your score or level\n"
                "• Rewards respect the server's daily earn cap\n"
                "• Admins can set a per-game reward multiplier (0 = no payout) from the dashboard"
            )),
        ]
    },
    {
        "label": "🤖 AI Games",
        "emoji": "🤖",
        "title": "🤖 AI-Powered Games",
        "color": discord.Color.from_rgb(88, 101, 242),
        "description": "Word and trivia games powered by AI. **Enable/disable each game and scale its reward from the Web Dashboard.**",
        "fields": [
            ("Commands", (
                "`/wordchain` — Each word must start with the last letter of the previous one\n"
                "`/connections` — Group words into four hidden categories\n"
                "`/taboo` — Describe the word without saying the banned ones\n"
                "`/riddle` — Solve an AI-generated riddle\n"
                "`/twentyq` — Guess the secret in twenty yes/no questions\n"
                "`/debate` — Argue a random side of a topic against the AI"
            )),
            ("How it Works", (
                "• These games use the server's configured AI provider (Gemini/OpenAI)\n"
                "• Rewards respect the server's daily earn cap and per-game multiplier"
            )),
        ]
    },
    {
        "label": "🎉 Party Games",
        "emoji": "🎉",
        "title": "🎉 Party Games",
        "color": discord.Color.orange(),
        "description": "Social games for groups. **Enable/disable each game and scale its reward from the Web Dashboard.**",
        "fields": [
            ("Commands", (
                "`/mafia` — Find the hidden mafia among the players\n"
                "`/spyfall` — Spot the spy who doesn't know the location\n"
                "`/twotruths` — Two truths and a lie — guess the lie\n"
                "`/mostlikely` — Vote for who best fits the prompt\n"
                "`/neverhave` — Never have I ever — confess or pass\n"
                "`/battleship` — Sink the enemy fleet on a 5×5 grid"
            )),
            ("How it Works", (
                "• Great for voice or text channels with several players\n"
                "• Winning players earn currency (respects the daily cap and multiplier)"
            )),
        ]
    },
    {
        "label": "🧭 RPG & Collections",
        "emoji": "🧭",
        "title": "🧭 RPG & Collections",
        "color": discord.Color.green(),
        "description": "Progression, pets, farming and collectibles. **Enable/disable each module and scale its reward from the Web Dashboard.**",
        "fields": [
            ("Commands", (
                "`/fish` — Cast a line and reel in the catch\n"
                "`/pet` — Adopt and raise a companion\n"
                "`/garden` — Plant, water and harvest crops\n"
                "`/adventure` — Go on a random text adventure\n"
                "`/quest` — View and claim daily quests\n"
                "`/badges` — View your achievement badges\n"
                "`/pixel` — Draw and share tiny pixel art\n"
                "`/caption` — Write the funniest caption for an image"
            )),
            ("How it Works", (
                "• Fishing, pets, gardening and adventures pay out currency\n"
                "• Quests reward currency for playing games, earning money and chatting\n"
                "• Badges, pixel art and captions are collectible/creative — no currency payout"
            )),
        ]
    },
    {
        "label": "💡 Suggestions",
        "emoji": "💡",
        "title": "💡 Suggestions",
        "color": discord.Color.blurple(),
        "description": "Propose ideas for the server — the community votes with 👍👎, staff reviews on the Web Dashboard. **Enable/configure the channel via the Web Dashboard.**",
        "fields": [
            ("Commands", (
                "`/suggest <idea>` — Submit a suggestion (posted for everyone to vote on)"
            )),
            ("How it Works", (
                "• Your suggestion is posted as an embed with 👍/👎 reactions for voting\n"
                "• Staff approve or deny it from the Web Dashboard — the embed updates live\n"
                "• Denied/approved suggestions stay visible with their final status"
            )),
        ]
    },
    {
        "label": "🎂 Birthdays",
        "emoji": "🎂",
        "title": "🎂 Birthday Tracker",
        "color": discord.Color.magenta(),
        "description": "Get an automatic shoutout on your birthday. Only the day & month are stored — no year, no age. **Enable/configure the announcement channel via the Web Dashboard.**",
        "fields": [
            ("Commands", (
                "`/birthday set <month> <day>` — Save your birthday\n"
                "`/birthday remove` — Delete your saved birthday\n"
                "`/birthday view [@member]` — Check a birthday (day/month only)"
            )),
        ]
    },
    {
        "label": "🔥 Daily Streaks",
        "emoji": "🔥",
        "title": "🔥 Daily Streaks",
        "color": discord.Color.orange(),
        "description": "Check in once a day for XP & currency — the longer your streak, the bigger the bonus. **Reward amounts configurable via the Web Dashboard.**",
        "fields": [
            ("Commands", (
                "`/daily` — Claim today's reward and extend your streak\n"
                "`/streak [@member]` — Check a current/best streak"
            )),
            ("How it Works", (
                "• One claim roughly every 20+ hours — no same-day double-dipping\n"
                "• Miss the configured grace window and your streak resets to 1 on the next claim\n"
                "• Longer streaks earn a bigger XP/currency bonus, up to a configurable cap"
            )),
        ]
    },
    {
        "label": "🎵 Music",
        "emoji": "🎵",
        "title": "🎵 Music & Voice",
        "color": discord.Color.from_rgb(29, 185, 84),
        "description": (
            "Stream high-quality audio directly in voice channels from **YouTube** and **Spotify**!\n"
            "Full playback controls with queues, loops, shuffling, volume adjustment, and auto-disconnect.\n\n"
            "**⚙️ Dashboard Settings:** Admins can enable/disable the module, configure a DJ Role, set default volume, max queue size, max track duration, and idle timeout on the Web Dashboard."
        ),
        "fields": [
            ("Playback Commands", (
                "`/play <query>` — Search YouTube or stream directly from a YouTube or Spotify URL\n"
                "`/pause` — Pause or resume playback\n"
                "`/skip` — Skip to the next track in queue\n"
                "`/stop` — Stop playback, clear queue, and reset loop settings\n"
                "`/disconnect` — Disconnect the bot from the voice channel"
            )),
            ("Queue & Controls", (
                "`/nowplaying` — Show current song with animated progress bar\n"
                "`/queue [page]` — View up to 10 songs per page with total queue time\n"
                "`/volume <0-100>` — Adjust playback volume\n"
                "`/loop` — Cycle loop modes: `Off` ➔ `🔂 Track Loop` ➔ `🔁 Queue Loop`\n"
                "`/shuffle` — Randomly shuffle all upcoming songs in queue\n"
                "`/remove <position>` — Remove a specific song from queue by number\n"
                "`/clear` — Clear all upcoming songs in queue"
            )),
            ("Prefix Aliases (!)", (
                "You can also use text commands:\n"
                "`!play` / `!p <query>`, `!skip` / `!s`, `!stop`, `!pause` / `!resume`, "
                "`!np` / `!nowplaying`, `!queue` / `!q`, `!vol` / `!volume <level>`, `!dc` / `!leave`"
            )),
            ("Features & Rules", (
                "• **Playlists & Albums:** Supports full YouTube playlists & Spotify albums/playlists\n"
                "• **Auto-Disconnect:** Leaves voice channel automatically after inactivity or if alone\n"
                "• **Voice Channel Check:** You must be in the same voice channel as the bot to control playback\n"
                "• **DJ Role:** Server admins can restrict control to a specific DJ Role via Web Dashboard"
            )),
        ]
    },
    {
        "label": "🌐 Translator",
        "emoji": "🌐",
        "title": "🌐 Multi-language & Translator",
        "color": discord.Color.from_rgb(66, 133, 244),
        "description": (
            "Translate messages across 12+ languages seamlessly!\n\n"
            "**Supported Languages:** English 🇬🇧, Bulgarian 🇧🇬, German 🇩🇪, Spanish 🇪🇸, French 🇫🇷, Russian 🇷🇺, Italian 🇮🇹, Turkish 🇹🇷, Polish 🇵🇱, Ukrainian 🇺🇦, Greek 🇬🇷, Romanian 🇷🇴."
        ),
        "fields": [
            ("Commands", (
                "`/translate <text> [language]` — Translate any text into your chosen language\n"
                "**Apps Context Menu** — Right-click any message ➔ **Apps** ➔ **Translate Message**"
            )),
            ("Flag Reactions", (
                "• Simply react to **any message** with a country flag emoji (🇬🇧, 🇧🇬, 🇩🇪, 🇪🇸, 🇫🇷, 🇷🇺, etc.)!\n"
                "• The bot will instantly reply with the translated text."
            )),
            ("Server Language", (
                "• Server admins can switch the bot's default UI language (EN, BG, DE, ES, FR, RU) via **Dashboard ➔ Bot Identity**."
            )),
        ]
    },
]


# ── View ──────────────────────────────────────────────────────────────────────


# Mapping of dashboard route to category label(s) in CATEGORIES.
# A value may be a single label or a list of labels (e.g. the Games & Fun
# dashboard page controls every game-related help category at once).
ROUTE_TO_CATEGORY = {
    'moderation': '🛡️ Moderation',
    'games': ['🎮 Games', '🕹️ Arcade', '🤖 AI Games', '🎉 Party Games', '🧭 RPG & Collections', '🎰 Casino', '🎟️ Lottery & Duels'],
    'smash-karts': '🎮 Games',
    'levels': '📊 Leveling',
    'rankcard-settings': '📊 Leveling',
    'ai-settings': '🤖 AI',
    'reminders': '⏰ Reminders',
    'giveaways': '🎉 Giveaways',
    'tournaments': '🏎️ Tournament',
    'tickets': '🎫 Tickets',
    'story': '📖 Story',
    'counting': '🔢 Counting',
    'youtube': '📺 YouTube',
    'daily-modules': '📅 Daily',
    'stats': '📈 Stats',
    'server-stats': '📊 Server Stats',
    'automod': '🛡️ AutoMod',
    'deadchat': '💬 Dead Chat',
    'invites': '📨 Invites',
    'custom-commands': '🧩 Custom Commands',
    'channels': '📡 Channels',
    'roles': '🛡️ Moderation',
    'reaction-roles': '🛡️ Moderation',
    'forms': '🎫 Tickets',
    'embed-studio': '📢 Announcements',
    'members': '🛡️ Moderation',
    'economy': '🪙 Economy',
    'suggestions': '💡 Suggestions',
    'birthdays': '🎂 Birthdays',
    'daily-streaks': '🔥 Daily Streaks',
    'music': '🎵 Music',
    'translator': '🌐 Translator',
}

def get_filtered_categories(gid: str | None = None) -> list[dict]:
    """Return categories filtered by what is enabled for the guild."""
    if not gid:
        return CATEGORIES
    try:
        from utils import load
        gates = load('module_gates.json').get(str(gid), {})
        cmd_gates = gates.get('commands_enabled', {})
        # Find which category labels are disabled
        disabled_labels = set()
        for route, enabled in cmd_gates.items():
            if not enabled:
                cat_label = ROUTE_TO_CATEGORY.get(route)
                if isinstance(cat_label, (list, tuple)):
                    disabled_labels.update(cat_label)
                elif cat_label:
                    disabled_labels.add(cat_label)
        
        filtered = [cat for cat in CATEGORIES if cat["label"] not in disabled_labels]
        return filtered if filtered else CATEGORIES
    except Exception:
        return CATEGORIES

class HelpView(discord.ui.View):
    def __init__(self, author_id: int, guild_id: str | None = None):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.guild_id = str(guild_id) if guild_id else None
        self.categories = get_filtered_categories(self.guild_id)
        self.page = 0
        self.interaction: discord.Interaction | None = None
        self._add_buttons()

    def _add_buttons(self):
        self.clear_items()
        # Home button
        home_btn = discord.ui.Button(
            label="🏠", style=discord.ButtonStyle.secondary,
            custom_id="home", disabled=(self.page == 0), row=0
        )
        home_btn.callback = self._home
        self.add_item(home_btn)

        # Prev
        prev_btn = discord.ui.Button(
            label="◀", style=discord.ButtonStyle.secondary,
            custom_id="prev", disabled=(self.page <= 0), row=0
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)

        # Page indicator (disabled button)
        page_btn = discord.ui.Button(
            label=f"{self.page + 1}/{len(self.categories)}",
            style=discord.ButtonStyle.secondary,
            disabled=True, row=0
        )
        self.add_item(page_btn)

        # Next
        next_btn = discord.ui.Button(
            label="▶", style=discord.ButtonStyle.secondary,
            custom_id="next", disabled=(self.page >= len(self.categories) - 1), row=0
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        # Category select menu (Discord allows at most 25 options)
        options = [
            discord.SelectOption(
                label=cat["label"],
                value=str(i),
                default=(i == self.page)
            )
            for i, cat in enumerate(self.categories[:25])
        ]
        select = discord.ui.Select(
            placeholder="Jump to a category…",
            options=options,
            row=1
        )
        select.callback = self._select
        self.add_item(select)

    def build_embed(self) -> discord.Embed:
        cat = self.categories[self.page] if self.page < len(self.categories) else self.categories[0]
        em = discord.Embed(
            title=cat["title"],
            description=cat["description"],
            color=cat["color"],
            timestamp=datetime.now(timezone.utc)
        )
        for name, value in cat.get("fields", []):
            em.add_field(name=name, value=value, inline=False)
        em.set_footer(text=f"SMASH BOT Help · Page {self.page + 1}/{len(self.categories)}")
        return em

    async def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await safe_interaction_send(
                interaction,
                "This menu belongs to someone else! Run `/help` yourself.",
                ephemeral=True,
                label="Help menu",
            )
            return False
        return True

    async def _home(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        self.page = 0
        self._add_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _prev(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        self.page = max(0, self.page - 1)
        self._add_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        self.page = min(len(self.categories) - 1, self.page + 1)
        self._add_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _select(self, interaction: discord.Interaction):
        if not await self._check(interaction): return
        self.page = int(interaction.data["values"][0])
        self._add_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(view=self)
            except Exception:
                pass


# ── Cog ───────────────────────────────────────────────────────────────────────

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show the full SMASH BOT help menu")
    @app_commands.describe(category="Jump straight to a category")
    @app_commands.choices(category=[
        app_commands.Choice(name="🛡️ Moderation", value="1"),
        app_commands.Choice(name="🎮 Games", value="2"),
        app_commands.Choice(name="📊 Leveling", value="3"),
        app_commands.Choice(name="🤖 Smash Bot AI", value="4"),
        app_commands.Choice(name="⏰ Reminders", value="5"),
        app_commands.Choice(name="🎉 Giveaways", value="6"),
        app_commands.Choice(name="🏎️ Tournament", value="7"),
        app_commands.Choice(name="🎫 Tickets", value="8"),
        app_commands.Choice(name="📖 Story", value="9"),
        app_commands.Choice(name="🔢 Counting", value="10"),
        app_commands.Choice(name="📺 YouTube", value="11"),
        app_commands.Choice(name="📅 Daily", value="12"),
        app_commands.Choice(name="📈 Stats", value="13"),
        app_commands.Choice(name="🛡️ AutoMod", value="14"),
        app_commands.Choice(name="💬 Dead Chat", value="15"),
        app_commands.Choice(name="📨 Invites", value="16"),
        app_commands.Choice(name="🧩 Custom Commands", value="17"),
        app_commands.Choice(name="🪙 Economy", value="18"),
        app_commands.Choice(name="🎰 Casino", value="19"),
        app_commands.Choice(name="🎟️ Lottery & Duels", value="20"),
        app_commands.Choice(name="🕹️ Arcade", value="21"),
        app_commands.Choice(name="🤖 AI Games", value="22"),
        app_commands.Choice(name="🎉 Party Games", value="23"),
        app_commands.Choice(name="🧭 RPG & Collections", value="24"),
        app_commands.Choice(name="💡 Suggestions", value="25"),
    ])
    async def help(self, interaction: discord.Interaction, category: str = "0"):
        gid = str(interaction.guild.id) if interaction.guild else None
        view = HelpView(author_id=interaction.user.id, guild_id=gid)
        req_page = int(category)
        view.page = req_page if req_page < len(view.categories) else 0
        view._add_buttons()
        view.interaction = interaction
        await safe_interaction_send(
            interaction,
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            label="/help",
        )


async def setup(bot):
    await bot.add_cog(Help(bot))
