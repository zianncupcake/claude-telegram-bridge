#!/bin/bash
# One-time: discover the supergroup chat id and write it into .telegram.env.
# Expects TELEGRAM_BOT_TOKEN already present there. Idempotent (re-run to re-detect).
set -euo pipefail
ENV="$HOME/.claude/mailboxes/.telegram.env"
[ -f "$ENV" ] || { echo "create $ENV with TELEGRAM_BOT_TOKEN=... first (chmod 600)"; exit 1; }
source "$ENV"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN missing in $ENV}"

cat <<'STEPS'
Before continuing, in Telegram:
  1. Create a PRIVATE supergroup.
  2. Add your bot to it and promote it to admin with "Manage topics".
  3. Turn on Topics (group settings -> Topics).
  4. Send any message in the group's General topic.
Then press Enter.
STEPS
read -r _

CHAT_ID="$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" \
  | python3 -c '
import sys, json
for u in reversed(json.load(sys.stdin).get("result", [])):
    chat = (u.get("message") or {}).get("chat") or {}
    if chat.get("type") in ("supergroup", "group"):
        print(chat["id"]); break
')"
[ -n "$CHAT_ID" ] || { echo "no group message in getUpdates — send one in the group and re-run"; exit 1; }

tmp="$(mktemp)"
grep -v '^TELEGRAM_CHAT_ID=' "$ENV" > "$tmp" || true
echo "TELEGRAM_CHAT_ID=${CHAT_ID}" >> "$tmp"
cat "$tmp" > "$ENV"
rm -f "$tmp"
chmod 600 "$ENV"
echo "wrote TELEGRAM_CHAT_ID=${CHAT_ID} to $ENV"
