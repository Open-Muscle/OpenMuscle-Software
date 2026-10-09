"""Public-repo hygiene: no internal coordination references or machine paths.

The source ships to people who only have the public repos, so comments, docs
and tests must not carry internal message-board numbers, team-state folder
names, cloud-sync paths or a hard-coded default Wi-Fi password. This is a
cheap tripwire, not a substitute for review.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCAN_DIRS = ["pc/src", "pc/tests", "pc/data", "docs", "embedded", "analysis"]
SCAN_EXT = {".py", ".js", ".html", ".css", ".md", ".json", ".bat"}
SELF = Path(__file__).resolve()

# Patterns are assembled from pieces so this file does not match itself.
PATTERNS = {
    "board number reference": re.compile(r"\bboard ?" + "#" + r"\d"),
    "bare 4-digit ticket reference": re.compile(r"(?<![\w&#])" + "#" + r"0[1-3]\d\d\b(?![0-9a-fA-F])"),
    "internal team-state folder": re.compile("AI_Team" + "_State"),
    "cloud-sync path note": re.compile("One" + "Drive", re.I),
    "overseer message reference": re.compile(r"overseer ?" + "#" + r"\d"),
    "hard-coded default wifi password": re.compile("31415" + "92653"),
    "absolute user profile path": re.compile(r"[A-Za-z]:[\/]+Users[\/]+(?!<)\w+"),
}


def _files():
    for d in SCAN_DIRS:
        for p in (REPO / d).rglob("*"):
            if (p.is_file() and p.suffix in SCAN_EXT and p.resolve() != SELF
                    and "node_modules" not in p.parts):
                yield p


def test_no_internal_references_in_public_files():
    hits = []
    for p in _files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pat in PATTERNS.items():
            m = pat.search(text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                hits.append("{}:{} {}".format(p.relative_to(REPO), line, label))
    assert not hits, "internal references found:\n" + "\n".join(hits)
