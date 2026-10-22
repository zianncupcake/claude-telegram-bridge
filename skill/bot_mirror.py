#!/usr/bin/env python3
"""Mirror a session's transcript replies into its Telegram topic.

For a generic session that does not format and push its own replies: launched once at attach, it
streams the session's assistant replies into the session outbox (via bot_send.sh) so the router
ships them, and replays a little history. It only writes local files — it never talks to Telegram.
"""
import collections
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg_fmt

MAILROOT = os.path.expanduser("~/.claude/mailboxes")
PROJECTS = os.path.expanduser("~/.claude/projects")
BOT_SEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_send.sh")

CHUNK_LIMIT = 3800      # live replies go full; Telegram's hard cap is 4096, so leave headroom
COMPACT_BODY = 600      # assistant text kept in a compact history turn
COMPACT_HEAD = 120      # the preceding user line kept in a compact history turn
HISTORY_TURNS = 5       # compact turns replayed on first attach
REQUEST_DEFAULT = 20    # /history with no/invalid count
REQUEST_MAX = 50
TICK = 2


def resolve_transcript(sid):
    """The session's transcript, newest-mtime when several project dirs hold <sid>.jsonl."""
    matches = glob.glob(os.path.join(PROJECTS, "*", "%s.jsonl" % sid))
    return max(matches, key=os.path.getmtime) if matches else None


def read_new_lines(path, offset):
    """Complete lines from offset, and the byte offset past them; a trailing partial line is left."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n")
    if end == -1:
        return [], offset
    complete = data[:end + 1]
    lines = [ln for ln in complete.decode("utf-8", "replace").split("\n") if ln]
    return lines, offset + len(complete)


def parse(line):
    try:
        return json.loads(line)
    except ValueError:
        return None


def assistant_text(entry):
    """Concatenated text blocks of an assistant entry, or None when it is tool-only."""
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return None
    content = msg.get("content")
    if not isinstance(content, list):
        return None
    text = "".join(b.get("text", "") for b in content
                   if isinstance(b, dict) and b.get("type") == "text")
    return text or None


def user_text(entry):
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _safe_cut(line, limit):
    """A cut point <= limit that does not fall inside an HTML entity (&...;)."""
    amp = line.rfind("&", 0, limit)
    if amp != -1 and ";" not in line[amp:limit]:
        return amp or limit     # a lone unterminated '&' at 0 cannot be avoided; cut at limit
    return limit


def chunk(text):
    """Split ALREADY-ESCAPED text into pieces each <= CHUNK_LIMIT at line boundaries (an entity holds
    no newline, so a boundary never splits one); an overlong single line is cut without severing an
    entity. The pieces rejoin to the input."""
    if len(text) <= CHUNK_LIMIT:
        return [text]
    pieces, buf = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > CHUNK_LIMIT:          # a lone line too long to keep whole
            if buf:
                pieces.append(buf)
                buf = ""
            cut = _safe_cut(line, CHUNK_LIMIT)
            pieces.append(line[:cut])
            line = line[cut:]
        if len(buf) + len(line) > CHUNK_LIMIT:
            pieces.append(buf)
            buf = line
        else:
            buf += line
    if buf:
        pieces.append(buf)
    return pieces


def _clip(s, n):
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


def compact_turns(lines, n):
    """Up to the last n (preceding-user, assistant) pairs, oldest first."""
    last_user = ""
    turns = collections.deque(maxlen=n)
    for line in lines:
        entry = parse(line)
        if entry is None:
            continue
        text = assistant_text(entry)
        if text is not None:
            turns.append((last_user, text))
            continue
        u = user_text(entry)
        if u:
            last_user = u
    return list(turns)


def render_compact(user, asst):
    return tg_fmt.t("▸ you: %s\n\n%s" % (_clip(user, COMPACT_HEAD), _clip(asst, COMPACT_BODY)))


def request_n(raw):
    try:
        n = int(raw.strip())
    except (ValueError, AttributeError):
        return REQUEST_DEFAULT
    return min(n, REQUEST_MAX) if n > 0 else REQUEST_DEFAULT


def send(sid, html):
    fd, tmp = tempfile.mkstemp(suffix=".html")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(html)
        subprocess.run([BOT_SEND, sid, tmp])
    finally:
        os.remove(tmp)


def emit_compact(lines, n, emit):
    for user, asst in compact_turns(lines, n):
        emit(render_compact(user, asst))


def serve_history_request(transcript_path, request_path, emit):
    """Replay the compact turns a pending /history asked for, then clear the request."""
    try:
        with open(request_path) as f:
            raw = f.read()
    except FileNotFoundError:
        return
    lines, _ = read_new_lines(transcript_path, 0)
    emit_compact(lines, request_n(raw), emit)
    os.remove(request_path)


def read_cursor(path):
    try:
        return int(open(path).read().strip())
    except (OSError, ValueError):
        return 0


def write_cursor(path, offset):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(offset))
    os.replace(tmp, path)


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        sys.exit("pass a session id or set CLAUDE_CODE_SESSION_ID")
    mailbox = os.path.join(MAILROOT, sid)
    os.makedirs(mailbox, exist_ok=True)
    cursor_path = os.path.join(mailbox, ".mirror_cursor")
    request_path = os.path.join(mailbox, ".history_request")

    transcript = resolve_transcript(sid)
    while transcript is None:
        time.sleep(TICK)
        transcript = resolve_transcript(sid)

    def emit(html):
        send(sid, html)

    if not os.path.exists(cursor_path):  # first attach: a little history, then start at EOF
        lines, offset = read_new_lines(transcript, 0)
        emit_compact(lines, HISTORY_TURNS, emit)
        write_cursor(cursor_path, offset)

    while True:
        serve_history_request(transcript, request_path, emit)
        lines, offset = read_new_lines(transcript, read_cursor(cursor_path))
        for line in lines:
            entry = parse(line)
            text = assistant_text(entry) if entry else None
            if text:                                 # escape BEFORE chunk: entities expand length
                for piece in chunk(tg_fmt.t(text)):
                    emit(piece)
        write_cursor(cursor_path, offset)
        time.sleep(TICK)


if __name__ == "__main__":
    main()
