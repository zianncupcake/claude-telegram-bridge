#!/bin/bash
# Creates this session's mailbox. Its existence is the opt-in signal the router watches for.
set -euo pipefail
: "${CLAUDE_CODE_SESSION_ID:?set CLAUDE_CODE_SESSION_ID}"
mkdir -p "$HOME/.claude/mailboxes/$CLAUDE_CODE_SESSION_ID"/{in,in/seen,out}
echo "attached $CLAUDE_CODE_SESSION_ID"
