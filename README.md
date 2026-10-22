# claude-telegram-bridge

Drive many background [Claude Code](https://claude.com/claude-code) sessions from Telegram — one
chat, one topic per session, messages flowing both ways.

## Demo

<!-- demo.gif -->

## The problem

A Telegram bot has a single `getUpdates` stream and a single offset, so only one process can poll
it. But you may have several background sessions running at once, each wanting to reach you and hear
back. They can't all hold the bot.

## The idea: forum topics as the routing key

The chat is a private **supergroup with Topics** (forum mode). Each session gets its own **forum
topic**, and the topic id is the routing key in both directions:

- outbound — a session's message is sent into its topic (`message_thread_id`);
- inbound — your reply inside a topic carries that same id, which names the session it belongs to.

Replying inside a topic is the whole interface: no commands to learn, no addressing.

## Architecture

One router owns Telegram; sessions only ever read and write local files.

```
   Telegram supergroup (Topics)
   ┌───────────────────────────────┐
   │ General   demo-session   job-B │   <- one topic per session
   └───────────────────────────────┘
                  │  getUpdates / sendMessage        (curl only)
          ┌───────┴────────┐
          │   router.py    │  single poller · persisted offset · lockfile
          └───────┬────────┘
                  │  files only (no network in a session)
   ~/.claude/mailboxes/<sessionId>/
          in/  <- router drops your messages      ->  bot_poll.sh
          out/ <- session drops replies            ->  router sends them
```

- The router is the only process that touches Telegram, and every API call shells out to `curl`.
- A session opts in by creating its mailbox (`bot_attach.sh`); the router creates its topic on the
  next tick and names it from the job's state.
- Routing state (offset, topic map) lives in `~/.claude/mailboxes/router.json`; the token and chat
  id live only in `.telegram.env`.

## Setup

1. Make a bot with @BotFather; put the token in `~/.claude/mailboxes/.telegram.env`
   (`TELEGRAM_BOT_TOKEN=...`, `chmod 600`). See `.telegram.env.example`.
2. `skill/setup.sh` — creates/points at a private supergroup with Topics and writes `TELEGRAM_CHAT_ID`.

## Install

    ./install.sh            # symlink ~/.claude/skills/bot -> skill/
    ./install.sh --copy     # copy instead (use this if a symlinked skill isn't picked up)

Then run the router: `python3 skill/router.py`, or load `skill/claude-bot.plist` as a LaunchAgent.
`skill/SKILL.md` is the operational guide for a session.

## Tests

    python3 -m unittest discover -s skill/tests
