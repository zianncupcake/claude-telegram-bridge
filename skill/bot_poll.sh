#!/bin/bash
# Prints one line per new inbound file, in update_id order, and moves it to in/seen/:
#   TEXT <path> | PHOTO <path> | FILE <path>   (NONE if the inbox is empty)
# A .caption sidecar travels with its file but is never a line of its own.
set -euo pipefail
SID="${1:-${CLAUDE_CODE_SESSION_ID:?pass a session id or set CLAUDE_CODE_SESSION_ID}}"
IN="$HOME/.claude/mailboxes/$SID/in"
SEEN="$IN/seen"
mkdir -p "$SEEN"
emitted=0
while IFS= read -r base; do
  [ -n "$base" ] || continue
  src="$IN/$base"; dest="$SEEN/$base"
  mv "$src" "$dest"
  [ -e "$src.caption" ] && mv "$src.caption" "$dest.caption"
  case "$base" in
    *.txt)                           echo "TEXT $dest" ;;
    *.png|*.jpg|*.jpeg|*.gif|*.webp) echo "PHOTO $dest" ;;
    *)                               echo "FILE $dest" ;;
  esac
  emitted=1
done < <(ls -1 "$IN" 2>/dev/null | grep -Ev '^seen$|\.caption$' | sort -t. -k1,1n)
[ "$emitted" = 0 ] && echo "NONE"
exit 0
