"""Telegram-safe HTML formatting — nbsp-indent so <pre> code blocks keep their indentation.
Telegram STRIPS normal leading spaces inside <pre> (plain or highlighted, form-encoded or JSON).
Non-breaking spaces (U+00A0) survive, so indent with those.
Tradeoff: tap-to-copy then carries nbsp, which Python rejects as indentation."""
import html
NBSP = " "

def _indent(text):
    rows = []
    for line in text.rstrip("\n").split("\n"):
        n = len(line) - len(line.lstrip(" "))
        rows.append(NBSP * n + line[n:])
    return "\n".join(rows)

def t(s):       return html.escape(s)   # escape literal prose (< > &) — tags are added via b()/i()
def code(py):   return "<pre>" + html.escape(_indent(py)) + "</pre>"
def block(t):   return "<pre>" + html.escape(_indent(t)) + "</pre>"
def b(s):       return "<b>" + s + "</b>"
def i(s):       return "<i>" + s + "</i>"
