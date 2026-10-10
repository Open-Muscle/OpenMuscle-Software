"""Public-repo hygiene: no internal coordination references or machine paths.

The source ships to people who only have the public repos, so comments, docs
and tests must not carry internal message-board numbers, team-state folder
names, cloud-sync paths or a hard-coded default Wi-Fi password. This is a
cheap tripwire, not a substitute for review.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# pc/data holds untracked user captures and models, so only its README is scanned.
SCAN_DIRS = ["pc/src", "pc/tests", "docs", "embedded", "analysis", "installers"]
SCAN_FILES = ["pc/data/README.md"]
SCAN_EXT = {".py", ".js", ".html", ".css", ".md", ".json", ".bat", ".ps1", ".sh",
            ".command", ".desktop"}
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


def _tracked():
    """Paths tracked by git under the scanned dirs, or None outside a checkout."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--"] + SCAN_DIRS + SCAN_FILES,
                             cwd=REPO, capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = [n for n in out.decode("utf-8", "ignore").split("\0") if n]
    return [REPO / n for n in names] or None


def _candidates():
    tracked = _tracked()
    if tracked is not None:
        return tracked
    # No git (e.g. an exported source tree): walk the dirs, skipping untracked
    # user data under pc/data by never listing it.
    found = [REPO / f for f in SCAN_FILES]
    for d in SCAN_DIRS:
        found.extend((REPO / d).rglob("*"))
    return found


def _files():
    for p in _candidates():
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


def test_no_em_dashes_in_shipped_text():
    """Guides, installers, the README and the web page strings use plain
    punctuation: an em dash is the one character this project does not ship."""
    import subprocess
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True,
                         check=True).stdout.decode("utf-8")
    checked = []
    for rel in out.split("\0"):
        if not rel:
            continue
        shipped = (rel == "README.md" or rel.startswith(("docs/", "installers/"))
                   or (rel.startswith("pc/src/openmuscle/web/static/")
                       and "/vendor/" not in rel))
        if not shipped or not rel.endswith((".md", ".html", ".js", ".css", ".ps1",
                                            ".bat", ".sh", ".command", ".desktop")):
            continue
        checked.append(rel)
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        assert chr(0x2014) not in text, "%s contains an em dash" % rel
    assert checked, "nothing scanned; is this a git checkout?"
