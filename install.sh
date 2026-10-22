#!/bin/bash
# Installs the skill as ~/.claude/skills/bot. Symlinks by default; --copy copies instead
# (use --copy if Claude does not pick up the symlinked skill).
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
SRC="$REPO/skill"
DST="$HOME/.claude/skills/bot"
mkdir -p "$HOME/.claude/skills"
rm -rf "$DST"
if [ "${1:-}" = "--copy" ]; then
  cp -R "$SRC" "$DST"
  echo "copied $SRC -> $DST"
else
  ln -s "$SRC" "$DST"
  echo "symlinked $DST -> $SRC"
fi
