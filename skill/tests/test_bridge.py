"""Local tests — no live Telegram. Run: python3 -m unittest discover -s skill/tests"""
import os
import subprocess
import sys
import tempfile
import unittest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL)
import router
import tg_fmt


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


if __name__ == "__main__":
    unittest.main()
