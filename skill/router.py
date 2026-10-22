#!/usr/bin/env python3
"""The only process that talks to Telegram. One bot, one supergroup with Topics;
each background session is one forum topic, and the topic id is the routing key both ways.

Each tick: create topics for new sessions, drain their outboxes to Telegram, poll Telegram
once and deliver inbound messages into the owning session's inbox, refresh a status line.
Every Telegram call shells out to curl — local TLS fails under urllib here.
"""
import fcntl
import glob
import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import tg_fmt

MAILROOT = os.path.expanduser("~/.claude/mailboxes")
STATE_PATH = os.path.join(MAILROOT, "router.json")
ENV_PATH = os.path.join(MAILROOT, ".telegram.env")
LOCK_PATH = os.path.join(MAILROOT, "router.lock")
JOBS_GLOB = os.path.expanduser("~/.claude/jobs/*/state.json")
IMAGE_EXT = ("jpg", "jpeg", "png", "gif", "webp")
TICK_SECONDS = 3
LISTEN_THRESHOLD = 60   # seconds an inbound may sit unread before we warn the topic
ASKED_DEFAULT = 10      # /asked with no/invalid count
ASKED_MAX = 50          # cap one /asked reply so it stays within Telegram's message limit

TOKEN = CHAT_ID = None  # set by main() from .telegram.env; the network layer below reads them


# --- Telegram over curl ------------------------------------------------------

def _curl(*args):
    out = subprocess.run(["curl", "-s", *args], capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except ValueError:
        return {}

def _form(method, **params):
    args = ["https://api.telegram.org/bot%s/%s" % (TOKEN, method)]
    for k, v in params.items():
        args += ["--data-urlencode", "%s=%s" % (k, v)]
    return _curl(*args)

def send_message(thread, text):
    params = {"chat_id": CHAT_ID, "parse_mode": "HTML",
              "disable_web_page_preview": "true", "text": text}
    if thread and thread != 1:  # General is topic 1 and must omit the thread id
        params["message_thread_id"] = thread
    return _form("sendMessage", **params)

def send_photo(thread, path):
    args = ["https://api.telegram.org/bot%s/sendPhoto" % TOKEN,
            "-F", "photo=@%s" % path, "-F", "chat_id=%s" % CHAT_ID]
    if thread and thread != 1:
        args += ["-F", "message_thread_id=%s" % thread]
    return _curl(*args)

def create_topic(name):
    return _form("createForumTopic", chat_id=CHAT_ID, name=name)

def close_topic(thread):
    return _form("closeForumTopic", chat_id=CHAT_ID, message_thread_id=thread)

def edit_status(mid, text):
    return _form("editMessageText", chat_id=CHAT_ID, message_id=mid, text=text)

def tg_download(file_id, workdir, convert=True):
    """getFile + download into workdir. With convert a non-jpg/png image is turned into png; a
    document passes convert=False to keep its own bytes and extension. Returns the temp path
    (a dotfile, so a consumer's glob skips it until route_updates renames it into place)."""
    fp = (_form("getFile", file_id=file_id).get("result") or {}).get("file_path")
    if not fp:
        return None
    ext = fp.rsplit(".", 1)[-1].lower()
    tmp = os.path.join(workdir, ".dl.%d.%s" % (os.getpid(), ext))
    subprocess.run(["curl", "-s", "https://api.telegram.org/file/bot%s/%s" % (TOKEN, fp),
                    "-o", tmp], check=True)
    if convert and ext not in ("png", "jpg", "jpeg"):
        png = os.path.join(workdir, ".dl.%d.png" % os.getpid())
        subprocess.run(["sips", "-s", "format", "png", tmp, "--out", png],
                       check=True, capture_output=True)
        os.remove(tmp)
        return png
    return tmp


# --- pure routing (no network; download is injected so it is unit-testable) --

def _atomic_write(path, data):
    tmp = os.path.join(os.path.dirname(path), ".tmp." + os.path.basename(path))
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

def _parse_command(text):
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    if parts[0] == "/sessions":
        return ("sessions", None)
    if parts[0] == "/close":
        return ("close", parts[1].strip() if len(parts) > 1 else "")
    return None

def _history_arg(text):
    """The digit string for "/history N", "" for a bare /history, else None."""
    parts = text.strip().split()
    if not parts or parts[0] != "/history":
        return None
    if len(parts) == 1:
        return ""
    if len(parts) == 2 and parts[1].isdigit():
        return parts[1]
    return None

def _asked_arg(text):
    """The digit string for "/asked N", "" for a bare /asked, else None."""
    parts = text.strip().split()
    if not parts or parts[0] != "/asked":
        return None
    if len(parts) == 1:
        return ""
    if len(parts) == 2 and parts[1].isdigit():
        return parts[1]
    return None

def _append_asked(mailroot, sid, m):
    """Append one line to the session's durable asked.log for each delivered inbound message."""
    if m.get("photo"):
        caption = m.get("caption")
        summary = "[photo] %s" % caption if caption else "[photo]"
    elif m.get("document"):
        summary = "[file: %s]" % (m["document"].get("file_name") or "")
    else:
        summary = m["text"]
    sdir = os.path.join(mailroot, sid)
    os.makedirs(sdir, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(sdir, "asked.log"), "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (stamp, summary))

def _deliver(m, update_id, mailroot, sid, download):
    indir = os.path.join(mailroot, sid, "in")
    os.makedirs(indir, exist_ok=True)
    if m.get("photo"):
        tmp = download(m["photo"][-1]["file_id"], indir)
        if not tmp:
            return
        ext = tmp.rsplit(".", 1)[-1]
        final = os.path.join(indir, "%d.%s" % (update_id, ext))
        caption = m.get("caption")
        if caption:  # write the sidecar first; the image rename is the commit
            _atomic_write(final + ".caption", caption.encode("utf-8"))
        os.replace(tmp, final)
    elif m.get("document"):
        tmp = download(m["document"]["file_id"], indir, convert=False)
        if not tmp:
            return
        name = m["document"].get("file_name") or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        final = os.path.join(indir, "%d.%s" % (update_id, ext or "bin"))
        caption = m.get("caption")
        if caption:  # write the sidecar first; the file rename is the commit
            _atomic_write(final + ".caption", caption.encode("utf-8"))
        os.replace(tmp, final)
    else:
        _atomic_write(os.path.join(indir, "%d.txt" % update_id), m["text"].encode("utf-8"))

def route_updates(updates, topics, mailroot, download):
    """Deliver each message update into the owning session's inbox and collect General-topic
    commands for the caller to run. Returns (new_offset, commands). Skips bot and service
    messages. topics maps sessionId -> {topic_id, name, ...}."""
    by_thread = {t["topic_id"]: sid for sid, t in topics.items()}
    offset = 0
    commands = []
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        m = u.get("message")
        if not m or (m.get("from") or {}).get("is_bot"):
            continue
        if not m.get("text") and not m.get("photo") and not m.get("document"):
            continue  # service message (e.g. forum_topic_created)
        thread = m.get("message_thread_id")
        if thread is None or thread == 1:
            cmd = _parse_command(m.get("text", ""))
            if cmd:
                commands.append(cmd)
            continue
        sid = by_thread.get(thread)
        if sid:
            harg = _history_arg(m.get("text", ""))
            if harg is not None:  # /history [N] in a topic: ask its mirror to replay, don't deliver
                _atomic_write(os.path.join(mailroot, sid, ".history_request"), harg.encode("utf-8"))
                continue
            aarg = _asked_arg(m.get("text", ""))
            if aarg is not None:  # /asked [N]: replay the asked.log tail, don't deliver or log it
                _atomic_write(os.path.join(mailroot, sid, ".asked_request"), aarg.encode("utf-8"))
                continue
            _append_asked(mailroot, sid, m)
            _deliver(m, u["update_id"], mailroot, sid, download)
    return offset, commands


def stale_inbox(indir, threshold_s, now=None):
    """True if any plain inbound file in indir is older than threshold_s — the session's poll
    loop isn't draining it (asleep, closed, or not armed). Ignores seen/ and dotfiles."""
    if now is None:
        now = time.time()
    try:
        names = os.listdir(indir)
    except FileNotFoundError:
        return False
    for name in names:
        if name.startswith("."):
            continue
        path = os.path.join(indir, name)
        if os.path.isfile(path) and now - os.path.getmtime(path) > threshold_s:
            return True
    return False


# --- session + job state -----------------------------------------------------

def load_jobs():
    jobs = {}
    for state in glob.glob(JOBS_GLOB):
        try:
            d = json.load(open(state))
        except (OSError, ValueError):
            continue
        sid = d.get("sessionId")
        if sid:
            jobs[sid] = {"name": d.get("name") or sid[:8],
                         "state": d.get("state", "?"),
                         "updatedAt": d.get("updatedAt", "")}
    return jobs

def _session_dirs(mailroot):
    if not os.path.isdir(mailroot):
        return []
    return [d for d in os.listdir(mailroot) if os.path.isdir(os.path.join(mailroot, d))]

def _name_for(sid, mailroot, jobs):
    j = jobs.get(sid)
    if j and j.get("name"):
        return j["name"]
    meta = os.path.join(mailroot, sid, "meta.json")
    if os.path.exists(meta):
        try:
            n = json.load(open(meta)).get("name")
            if n:
                return n
        except (OSError, ValueError):
            pass
    return sid[:8]

def _ago(iso):
    if not iso:
        return "?"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    secs = int((datetime.now(timezone.utc) - t).total_seconds())
    if secs < 3600:
        return "%dm ago" % (secs // 60)
    if secs < 86400:
        return "%dh ago" % (secs // 3600)
    return "%dd ago" % (secs // 86400)

def board_text(jobs):
    lines = ["<b>sessions</b>"]
    for sid, j in sorted(jobs.items(), key=lambda kv: kv[1]["updatedAt"], reverse=True):
        state = "⏳ waiting on you" if j["state"] == "blocked" else j["state"]
        lines.append("%s — %s — %s" % (html.escape(j["name"]), state, _ago(j["updatedAt"])))
    return "\n".join(lines)


# --- persistence -------------------------------------------------------------

def load_router():
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH))
    return {"offset": 0, "status_mid": None, "topics": {}}

def save_router(router):
    tmp = STATE_PATH + ".tmp"
    json.dump(router, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE_PATH)


# --- the loop ----------------------------------------------------------------

def reconcile(router):
    jobs = load_jobs()
    # Dead sessions are never reaped — auto-reap of stale topics is a deliberate future add.
    for sid in _session_dirs(MAILROOT):
        if sid in router["topics"]:
            continue
        name = _name_for(sid, MAILROOT, jobs)
        tid = (create_topic(name).get("result") or {}).get("message_thread_id")
        if not tid:
            continue  # creation failed (transient) — retry next tick
        router["topics"][sid] = {"topic_id": tid, "name": name}
        save_router(router)
        send_message(tid, "🟢 %s attached" % html.escape(name))


def drain(sid, topic):
    outdir = os.path.join(MAILROOT, sid, "out")
    if not os.path.isdir(outdir):
        return
    for name in sorted(os.listdir(outdir)):
        if name.startswith("."):
            continue
        path = os.path.join(outdir, name)
        if name.rsplit(".", 1)[-1].lower() in IMAGE_EXT:
            send_photo(topic["topic_id"], path)
        else:
            send_message(topic["topic_id"], open(path, encoding="utf-8").read())
        os.remove(path)

def poll(router):
    updates = _form("getUpdates", offset=router["offset"], timeout=0).get("result", [])
    new_offset, commands = route_updates(updates, router["topics"], MAILROOT, tg_download)
    if updates:
        router["offset"] = new_offset
        save_router(router)
    for cmd, arg in commands:
        if cmd == "sessions":
            send_message(None, board_text(load_jobs()))
        elif cmd == "close":
            _handle_close(arg, router)

def _handle_close(name, router):
    for sid, t in router["topics"].items():
        if t["name"] == name:
            close_topic(t["topic_id"])
            t["closed"] = True
            save_router(router)
            send_message(None, "closed %s" % html.escape(name))
            return
    send_message(None, "no open topic named %s" % html.escape(name))

def update_status(router):
    text = "🟢 router alive · %d sessions · %s" % (len(router["topics"]),
                                                   time.strftime("%H:%M:%S"))
    mid = router.get("status_mid")
    if mid:
        edit_status(mid, text)
        return
    mid = (send_message(None, text).get("result") or {}).get("message_id")
    if mid:
        router["status_mid"] = mid
        save_router(router)

def check_listening(router, warned):
    """Warn in a topic when its inbound has sat unread past LISTEN_THRESHOLD (the session is
    asleep or closed), and say so once when it starts draining again. warned is in-memory."""
    for sid, topic in router["topics"].items():
        if stale_inbox(os.path.join(MAILROOT, sid, "in"), LISTEN_THRESHOLD):
            if not warned.get(sid):
                send_message(topic["topic_id"], "\u26a0\ufe0f this session isn't picking up right now"
                             " \u2014 your message is queued and I'll answer when it's back")
                warned[sid] = True
        elif warned.get(sid):
            send_message(topic["topic_id"], "\u2705 back online \u2014 answering your queued messages")
            warned[sid] = False

def tail_lines(path, n):
    """The last n non-empty lines of path (newline-stripped, in order); [] if it is missing."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    except FileNotFoundError:
        return []
    return lines[-n:]

def _asked_n(raw):
    try:
        n = int(raw.strip())
    except (ValueError, AttributeError):
        return ASKED_DEFAULT
    return min(n, ASKED_MAX) if n > 0 else ASKED_DEFAULT

def serve_asked_requests(router):
    """Answer a pending /asked by sending the tail of the session's asked.log into its topic."""
    for sid, topic in router["topics"].items():
        req = os.path.join(MAILROOT, sid, ".asked_request")
        try:
            raw = open(req).read()
        except FileNotFoundError:
            continue
        lines = tail_lines(os.path.join(MAILROOT, sid, "asked.log"), _asked_n(raw))
        if lines:
            body = tg_fmt.b("your recent questions:") + "\n" + tg_fmt.t("\n".join(lines))
        else:
            body = tg_fmt.t("nothing logged yet")
        send_message(topic["topic_id"], body)
        os.remove(req)

def tick(router, warned):
    reconcile(router)
    for sid, topic in list(router["topics"].items()):
        drain(sid, topic)
    poll(router)
    check_listening(router, warned)
    serve_asked_requests(router)
    update_status(router)

def main():
    global TOKEN, CHAT_ID
    env = {}
    for line in open(ENV_PATH):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    TOKEN, CHAT_ID = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not TOKEN or not CHAT_ID:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in %s" % ENV_PATH)

    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("router already running")
    lock.write(str(os.getpid()))
    lock.flush()

    router = load_router()
    warned = {}
    while True:
        tick(router, warned)
        time.sleep(TICK_SECONDS)

if __name__ == "__main__":
    main()
