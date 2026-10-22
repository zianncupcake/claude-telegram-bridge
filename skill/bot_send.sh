#!/bin/bash
# Usage: bot_send.sh [<sid>] <file>
# Copies the file into the session outbox under the next zero-padded sequence name.
# The router sends outbox files in that order (HTML as a message, images as a photo).
set -euo pipefail
if [ "$#" -ge 2 ]; then SID="$1"; SRC="$2"; else SID="${CLAUDE_CODE_SESSION_ID:?pass a session id or set CLAUDE_CODE_SESSION_ID}"; SRC="$1"; fi
OUT="$HOME/.claude/mailboxes/$SID/out"
mkdir -p "$OUT"
last="$(ls -1 "$OUT" 2>/dev/null | grep -E '^[0-9]+\.' | sort -n | tail -1 | cut -d. -f1 || true)"
seq="$(printf '%06d' "$(( 10#${last:-0} + 1 ))")"
ext="${SRC##*.}"
cp "$SRC" "$OUT/.tmp.$seq.$ext"
mv "$OUT/.tmp.$seq.$ext" "$OUT/$seq.$ext"
echo "$OUT/$seq.$ext"
