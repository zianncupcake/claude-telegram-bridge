---
name: bot
description: Bridge Telegram to background Claude Code sessions. One bot in one private supergroup with Topics; each session is a forum topic. A session opts in with bot_attach.sh, reads inbound messages with bot_poll.sh, and replies with bot_send.sh — it only ever touches local files. One router daemon (router.py) is the only thing that talks to Telegram.
---

# bot — Telegram bridge for background sessions

A session never calls Telegram. It writes and reads files under its own mailbox
(`~/.claude/mailboxes/$CLAUDE_CODE_SESSION_ID/`); the router moves them to and from Telegram.
The session id is the routing key, and a session's mailbox existing is how it opts in.

## One-time Telegram setup

1. Make a bot with @BotFather and put its token in `~/.claude/mailboxes/.telegram.env`
   as `TELEGRAM_BOT_TOKEN=...` (`chmod 600`).
2. Run `setup.sh`. It walks you through creating a private supergroup, adding the bot as an
   admin with **Manage topics**, turning on **Topics**, and sending one message in the group;
   then it writes `TELEGRAM_CHAT_ID` into `.telegram.env`.

## How a session uses it

On start, opt in:

    "$HOME/.claude/skills/bot/bot_attach.sh"

Optionally mirror this session's replies into its topic — for a *generic* session that does not
format and push its own replies. Launch it once, right after attaching (it reads
`$CLAUDE_CODE_SESSION_ID` and on first run replays the last 5 turns):

    nohup python3 "$HOME/.claude/skills/bot/bot_mirror.py" >/dev/null 2>&1 &

A session that already sends its own formatted replies must NOT also run the mirror — it would
double every message. Inside the session's topic, type `/history [N]` to replay the last N turns
(N defaults to 20).

If a message you send sits unread for a minute — laptop asleep, session closed, poll loop not
armed — the router posts a ⚠️ in that topic to say it is queued, and a ✅ when the session starts
answering again. Every inbound message is also recorded, one line per message, in a durable
`asked.log` under that session's mailbox (`~/.claude/mailboxes/<sid>/asked.log`); type `/asked [N]`
in the topic to see the last N (default 10).

Listen with a Monitor whose command loops `bot_poll.sh` and prints only on a real message:

    while true; do out=$("$HOME/.claude/skills/bot/bot_poll.sh" 2>/dev/null || true); case "$out" in TEXT*|PHOTO*|FILE*) echo "$out";; esac; sleep 5; done

Each line is `TEXT <path>`, `PHOTO <path>`, or `FILE <path>` (a forwarded document — Read it, Claude
reads PDFs). Read the file (for a photo or file, also read `<path>.caption` if present), do the
work, then reply:

    python3 -c 'import sys; sys.path.insert(0,"'"$HOME"'/.claude/skills/bot"); import tg_fmt
    open("/tmp/reply.html","w").write(tg_fmt.b("done")+"\n"+tg_fmt.code("print(1)"))'
    "$HOME/.claude/skills/bot/bot_send.sh" /tmp/reply.html

Use `tg_fmt.py` for the HTML: `code()`/`block()` indent with non-breaking spaces so `<pre>`
blocks keep their indentation, and `t()` escapes literal prose so a raw `<` or `&` can't
break the send. Never Telegram Markdown.

## Running the router

    python3 "$HOME/.claude/skills/bot/router.py"

Or install the LaunchAgent: copy `claude-bot.plist` to `~/Library/LaunchAgents/`, replace the
`__REPO__`/`__PYTHON__` placeholders, and `launchctl load` it (KeepAlive restarts it).

Only one router may run — it holds a lockfile. In the group's **General** topic two commands work:
`/sessions` prints a board of jobs, and `/close <name>` closes a session's topic.
