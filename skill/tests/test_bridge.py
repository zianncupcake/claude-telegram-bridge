"""Local tests — no live Telegram. Run: python3 -m unittest discover -s skill/tests"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL)
import router
import tg_fmt
import bot_mirror


class RouteUpdates(unittest.TestCase):
    def test_text_message_written_by_update_id_atomically(self):
        mailroot = tempfile.mkdtemp()
        sid = "demo-session"
        topics = {sid: {"topic_id": 7, "name": "demo"}}
        updates = [{"update_id": 42, "message": {
            "from": {"id": 1, "is_bot": False},
            "chat": {"id": -100, "type": "supergroup"},
            "message_thread_id": 7, "text": "hello there"}}]

        def no_download(file_id, workdir):
            raise AssertionError("text delivery must not download")

        offset, commands = router.route_updates(updates, topics, mailroot, no_download)

        self.assertEqual(offset, 43)
        self.assertEqual(commands, [])
        indir = os.path.join(mailroot, sid, "in")
        self.assertEqual(os.listdir(indir), ["42.txt"])  # no leftover temp == atomic
        with open(os.path.join(indir, "42.txt")) as f:
            self.assertEqual(f.read(), "hello there")


class BotPoll(unittest.TestCase):
    @staticmethod
    def _write(path, text):
        with open(path, "w") as f:
            f.write(text)

    def test_emits_in_update_id_order_and_moves_to_seen(self):
        home = tempfile.mkdtemp()
        sid = "demo-session"
        indir = os.path.join(home, ".claude", "mailboxes", sid, "in")
        os.makedirs(indir)
        self._write(os.path.join(indir, "10.png"), "img")   # higher id, created first
        self._write(os.path.join(indir, "9.txt"), "msg")    # lower id -> must sort first
        env = {**os.environ, "HOME": home, "CLAUDE_CODE_SESSION_ID": sid}
        script = os.path.join(SKILL, "bot_poll.sh")
        seen = os.path.join(indir, "seen")

        out = subprocess.run(["bash", script], env=env, capture_output=True, text=True).stdout
        self.assertEqual(out.splitlines(),
                         ["TEXT %s/9.txt" % seen, "PHOTO %s/10.png" % seen])
        self.assertTrue(os.path.exists(os.path.join(seen, "9.txt")))
        self.assertTrue(os.path.exists(os.path.join(seen, "10.png")))

        out2 = subprocess.run(["bash", script], env=env, capture_output=True, text=True).stdout
        self.assertEqual(out2.strip(), "NONE")


class BotSend(unittest.TestCase):
    @staticmethod
    def _write(path, text):
        with open(path, "w") as f:
            f.write(text)

    def test_lands_in_outbox_with_increasing_sequence(self):
        home = tempfile.mkdtemp()
        sid = "demo-session"
        src = os.path.join(home, "reply.html")
        self._write(src, "<pre>hi</pre>")
        env = {**os.environ, "HOME": home, "CLAUDE_CODE_SESSION_ID": sid}
        script = os.path.join(SKILL, "bot_send.sh")

        p1 = subprocess.run(["bash", script, src], env=env, capture_output=True, text=True).stdout.strip()
        p2 = subprocess.run(["bash", script, src], env=env, capture_output=True, text=True).stdout.strip()

        self.assertTrue(p1.endswith("000001.html"))
        self.assertTrue(p2.endswith("000002.html"))
        outdir = os.path.join(home, ".claude", "mailboxes", sid, "out")
        self.assertEqual(sorted(os.listdir(outdir)), ["000001.html", "000002.html"])


class Formatter(unittest.TestCase):
    def test_code_escapes_and_nbsp_indents(self):
        out = tg_fmt.code("    if a < b & c:")
        self.assertIn("&lt;", out)
        self.assertIn("&amp;", out)
        self.assertTrue(out.startswith("<pre>") and out.endswith("</pre>"))
        self.assertIn(tg_fmt.NBSP * 4, out)            # indented with nbsp
        self.assertNotIn("<pre>    ", out)             # not with plain spaces


class DocumentDelivery(unittest.TestCase):
    def test_document_saved_unconverted_with_caption(self):
        mailroot = tempfile.mkdtemp()
        sid = "demo-session"
        topics = {sid: {"topic_id": 7, "name": "demo"}}
        payload = b"%PDF-1.4 raw bytes \x00\xff not an image"
        updates = [{"update_id": 55, "message": {
            "from": {"id": 1, "is_bot": False},
            "message_thread_id": 7,
            "document": {"file_id": "FID", "file_name": "statement.pdf"},
            "caption": "march"}}]
        seen = {}

        def fake_download(file_id, workdir, convert=True):
            seen["convert"] = convert
            tmp = os.path.join(workdir, ".dl.pdf")
            with open(tmp, "wb") as f:
                f.write(payload)
            return tmp

        router.route_updates(updates, topics, mailroot, fake_download)

        self.assertFalse(seen["convert"])  # a document is never run through sips
        indir = os.path.join(mailroot, sid, "in")
        self.assertEqual(sorted(os.listdir(indir)), ["55.pdf", "55.pdf.caption"])
        with open(os.path.join(indir, "55.pdf"), "rb") as f:
            self.assertEqual(f.read(), payload)  # bytes untouched
        with open(os.path.join(indir, "55.pdf.caption")) as f:
            self.assertEqual(f.read(), "march")


class BotPollClassification(unittest.TestCase):
    def test_text_photo_file_in_update_id_order(self):
        home = tempfile.mkdtemp()
        sid = "demo-session"
        indir = os.path.join(home, ".claude", "mailboxes", sid, "in")
        os.makedirs(indir)
        for name in ("1.txt", "2.png", "3.pdf"):
            with open(os.path.join(indir, name), "w") as f:
                f.write("x")
        env = {**os.environ, "HOME": home, "CLAUDE_CODE_SESSION_ID": sid}
        seen = os.path.join(indir, "seen")
        out = subprocess.run(["bash", os.path.join(SKILL, "bot_poll.sh")],
                             env=env, capture_output=True, text=True).stdout
        self.assertEqual(out.splitlines(),
                         ["TEXT %s/1.txt" % seen, "PHOTO %s/2.png" % seen, "FILE %s/3.pdf" % seen])
        self.assertEqual(sorted(os.listdir(seen)), ["1.txt", "2.png", "3.pdf"])


class MirrorExtraction(unittest.TestCase):
    def test_assistant_text_concatenates_and_skips_tool_only(self):
        with_text = {"message": {"role": "assistant", "content": [
            {"type": "text", "text": "Hello "},
            {"type": "tool_use", "name": "Bash", "input": {}},
            {"type": "text", "text": "world"}]}}
        tool_only = {"message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {}}]}}
        a_user = {"message": {"role": "user", "content": "hi"}}
        self.assertEqual(bot_mirror.assistant_text(with_text), "Hello world")
        self.assertIsNone(bot_mirror.assistant_text(tool_only))
        self.assertIsNone(bot_mirror.assistant_text(a_user))

    def test_chunk_under_limit_at_line_boundaries_and_rejoins(self):
        text = ("x" * 100 + "\n") * 60
        pieces = bot_mirror.chunk(text)
        self.assertGreater(len(pieces), 1)
        for p in pieces:
            self.assertLessEqual(len(p), bot_mirror.CHUNK_LIMIT)
            self.assertTrue(p.endswith("\n"))  # split on a line boundary
        self.assertEqual("".join(pieces), text)

    def test_escaped_special_chars_chunk_within_telegram_limit(self):
        # the bug this guards: html-escaping expands < > & ' so text must be escaped BEFORE chunking
        raw = ("x < y && z = 'q'\n") * 400
        pieces = bot_mirror.chunk(tg_fmt.t(raw))
        self.assertGreater(len(pieces), 1)
        for p in pieces:
            self.assertLessEqual(len(p), bot_mirror.CHUNK_LIMIT)
            self.assertNotRegex(p, r"&[a-zA-Z#0-9]*$")    # never ends mid-entity
        self.assertEqual("".join(pieces), tg_fmt.t(raw))


class MirrorHistory(unittest.TestCase):
    def test_history_request_replays_capped_then_deletes(self):
        mbox = tempfile.mkdtemp()
        transcript = os.path.join(mbox, "t.jsonl")
        rows = []
        for i in range(9):
            rows.append(json.dumps({"message": {"role": "user", "content": "q%d" % i}}))
            rows.append(json.dumps({"message": {"role": "assistant",
                                                "content": [{"type": "text", "text": "a%d" % i}]}}))
        with open(transcript, "w") as f:
            f.write("\n".join(rows) + "\n")
        request_path = os.path.join(mbox, ".history_request")
        with open(request_path, "w") as f:
            f.write("7")
        sent = []

        bot_mirror.serve_history_request(transcript, request_path, sent.append)

        self.assertEqual(len(sent), 7)           # last 7 of 9 turns
        self.assertFalse(os.path.exists(request_path))
        self.assertIn("q8", sent[-1])            # newest turn last, with its user line
        self.assertIn("a8", sent[-1])


class StaleInbox(unittest.TestCase):
    def test_old_file_stale_fresh_not_seen_and_dotfiles_ignored(self):
        indir = tempfile.mkdtemp()
        self.assertFalse(router.stale_inbox(indir, 60))          # empty dir

        fresh = os.path.join(indir, "1.txt")
        open(fresh, "w").close()
        self.assertFalse(router.stale_inbox(indir, 60))          # just arrived
        old = time.time() - 120
        os.utime(fresh, (old, old))
        self.assertTrue(router.stale_inbox(indir, 60))           # 120s > 60s threshold
        os.remove(fresh)

        seen = os.path.join(indir, "seen")                       # aged file under seen/ ignored
        os.makedirs(seen)
        seenf = os.path.join(seen, "1.txt")
        open(seenf, "w").close()
        os.utime(seenf, (old, old))
        self.assertFalse(router.stale_inbox(indir, 60))

        dot = os.path.join(indir, ".dl.tmp")                     # aged dotfile ignored
        open(dot, "w").close()
        os.utime(dot, (old, old))
        self.assertFalse(router.stale_inbox(indir, 60))


class AskedLog(unittest.TestCase):
    def test_text_and_document_each_append_a_line(self):
        mailroot = tempfile.mkdtemp()
        sid = "demo-session"
        topics = {sid: {"topic_id": 7, "name": "demo"}}

        def no_download(file_id, workdir, convert=True):
            raise AssertionError("text delivery must not download")

        router.route_updates([{"update_id": 1, "message": {
            "from": {"id": 1, "is_bot": False},
            "message_thread_id": 7, "text": "ping the server"}}], topics, mailroot, no_download)

        def fake_download(file_id, workdir, convert=True):
            tmp = os.path.join(workdir, ".dl.pdf")
            with open(tmp, "wb") as f:
                f.write(b"bytes")
            return tmp

        router.route_updates([{"update_id": 2, "message": {
            "from": {"id": 1, "is_bot": False}, "message_thread_id": 7,
            "document": {"file_id": "FID", "file_name": "statement.pdf"}}}],
            topics, mailroot, fake_download)

        with open(os.path.join(mailroot, sid, "asked.log")) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("ping the server"))
        self.assertIn("[file: statement.pdf]", lines[1])


class AskedCommand(unittest.TestCase):
    def test_asked_in_topic_writes_request_and_does_not_deliver_or_log(self):
        mailroot = tempfile.mkdtemp()
        sid = "demo-session"
        topics = {sid: {"topic_id": 7, "name": "demo"}}
        os.makedirs(os.path.join(mailroot, sid))

        def no_download(file_id, workdir, convert=True):
            raise AssertionError("a command must not download")

        router.route_updates([{"update_id": 3, "message": {
            "from": {"id": 1, "is_bot": False},
            "message_thread_id": 7, "text": "/asked 5"}}], topics, mailroot, no_download)

        with open(os.path.join(mailroot, sid, ".asked_request")) as f:
            self.assertEqual(f.read(), "5")
        indir = os.path.join(mailroot, sid, "in")
        self.assertFalse(os.path.isdir(indir) and os.listdir(indir))     # nothing delivered
        self.assertFalse(os.path.exists(os.path.join(mailroot, sid, "asked.log")))  # not logged

    def test_asked_arg_parsing(self):
        self.assertEqual(router._asked_arg("/asked"), "")
        self.assertIsNone(router._asked_arg("/asked x"))


if __name__ == "__main__":
    unittest.main()
