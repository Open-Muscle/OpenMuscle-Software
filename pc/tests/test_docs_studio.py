"""The Studio guides must describe the Studio that is actually in the repo.

Checks on docs/studio-tour.md, first-model.md, movement-sheet.md, glossary.md and
webcam-labeling.md:

- every label written in bold exists, word for word, in the page source or in the
  module that produces it (so a renamed button breaks the build, not the reader);
- the quality words and the cut-offs in first-model.md come from ml/verdict.py,
  take_limits.py and quality.py, not from memory;
- no em dashes, and none of the words a public guide must not contain;
- relative links and heading anchors resolve (pages owned by the other kit guides
  are allowed to be missing until they are merged);
- the pictures exist and are a sensible size;
- every term the guides rely on is in the glossary.
"""

import codecs
import html
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
STATIC = REPO / "pc" / "src" / "openmuscle" / "web" / "static"
SRC = REPO / "pc" / "src" / "openmuscle"

GUIDES = ["studio-tour.md", "first-model.md", "movement-sheet.md", "glossary.md",
          "webcam-labeling.md", "studio-guides.md"]
LABEL_CHECKED = ["studio-tour.md", "first-model.md", "movement-sheet.md"]

# Pages written by the other kit guides; links to them are fine before they merge.
SIBLING_PAGES = {"getting-started.md", "wifi-setup.md", "troubleshooting.md",
                 "your-data.md", "band-fit.md", "device-reference.md", "install.md"}

# Source the on-screen words come from.
LABEL_SOURCES = [
    STATIC / "index.html", STATIC / "app.js", STATIC / "guide.js",
    STATIC / "rec-status.js", STATIC / "train-card.js", STATIC / "participant.js",
    STATIC / "camera-panel.js", STATIC / "webcam" / "index.html",
    STATIC / "webcam" / "webcam.js", STATIC / "webcam" / "track-logic.js",
    SRC / "quality.py", SRC / "ml" / "verdict.py",
]

# Bold text that is not a label on the Studio page: the browser's own prompt,
# the words of the support table, and the names of parts of the tour.
NOT_A_LABEL = {
    "allow",                                    # the browser's camera prompt
    "built for it; partly checked.", "expected to work.", "untested.",
    "not available.",                           # support matrix legend
    "chrome", "edge", "firefox", "safari",      # browser names
    "windows", "macos", "linux",
    "expected to work", "untested", "not available",
    "built for it; partly checked",
    "bad", "good", "check",                     # quality words, checked separately
    "fair", "weak",                             # grades, checked against verdict.py
    "left hand", "right hand",                  # result card titles (cap + " hand")
    "stack", "side by side",                    # checked below with the symbol
    "load",
    "no camera",
}

# Words a public guide must not contain (a site, a study, a person, an internal
# folder). Stored rot13-encoded so this file does not itself spell them out.
BANNED = [codecs.decode(w, "rot13") for w in (
    "Bertba", "Pynex", "gurfvf", "nzchgrr", "havirefvgl", "BarQevir", "NV_Grnz")]

# Terms the guides lean on that are not everyday English.
GLOSSARY_TERMS = [
    "2.4 GHz", "Arm", "Assets", "Band", "Camera panel", "Capture", "Cell", "Check",
    "Checksum", "Client isolation", "CPU and GPU", "CSV", "dBm", "Debug mode",
    "Feature", "Finger curl", "Firewall", "Firmware", "FlexGrid", "Ground truth", "Guest network",
    "Heatmap", "Held-out score", "Hot-swap", "Hz", "IMU", "IP address", "Label", "Labeler", "LASK5",
    "Localhost", "MediaPipe", "Mirrored training", "Model", "OLED",
    "Paired frame / unpaired frame", "Participant code", "Piston", "Port", "Prediction",
    "PSK", "Pressure myography", "Quest", "R2 score", "Role", "Router",
    "Sensor-only recording", "Session", "Setup mode", "Sidecar files", "SSID", "Subnet",
    "Subscribe", "Take", "Terminal", "Time window", "Training", "UDP", "Verdict",
    "Virtual environment", "Wearer", "Wi-Fi password", "Window ms", "Zip file",
]


# Icons and symbols the Studio puts in front of button text.
FULLWIDTH_PLUS = chr(0xFF0B)
ICON_CHARS = [chr(c) for c in (0x25CF, 0x25A0, 0x25B8, 0x25BE, 0x25A4, 0x25A5, 0x23F8,
                               0x25B6, 0x2699, 0x25F1, 0x1F4C2)]
EM_DASH = chr(0x2014)


def _read(name):
    return (DOCS / name).read_text(encoding="utf-8")


def _norm(text):
    """Lower-case, entity-decoded, symbols removed, whitespace collapsed."""
    t = html.unescape(text)
    t = t.replace(FULLWIDTH_PLUS, "+")
    for ch in ICON_CHARS:
        t = t.replace(ch, " ")
    for ch in (0x201C, 0x201D):
        t = t.replace(chr(ch), '"')
    for ch in (0x2018, 0x2019):
        t = t.replace(chr(ch), "'")
    return re.sub(r"\s+", " ", t).strip().lower()


def _corpus():
    raw = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in LABEL_SOURCES)
    # Markup inside a label (<span>vs</span>) is not part of what the reader sees.
    return _norm(raw + "\n" + re.sub(r"<[^>]+>", " ", raw))


def _bold(text):
    # Drop code spans first so a `**` inside code is not read as bold.
    text = re.sub(r"`[^`]*`", "", text)
    return re.findall(r"\*\*([^*\n]+?)\*\*", text)


# --------------------------------------------------------------- exist

@pytest.mark.parametrize("name", GUIDES)
def test_guide_exists_and_has_no_em_dash(name):
    text = _read(name)
    assert text.strip()
    assert EM_DASH not in text, name + " contains an em dash"


@pytest.mark.parametrize("name", GUIDES)
def test_no_banned_words(name):
    text = _read(name)
    for word in BANNED:
        assert not re.search(r"\b" + re.escape(word) + r"\b", text, re.I), (
            "{} contains '{}'".format(name, word))


# --------------------------------------------------------------- labels

@pytest.mark.parametrize("name", LABEL_CHECKED)
def test_bold_labels_exist_in_the_ui(name):
    corpus = _corpus()
    missing = []
    for label in _bold(_read(name)):
        n = _norm(label).rstrip(":")
        if not n or _norm(label) in NOT_A_LABEL or n in NOT_A_LABEL:
            continue
        if n not in corpus:
            missing.append(label)
    assert not missing, "{}: bold text not found in the UI source: {}".format(name, missing)


def test_quality_words_match_the_ui():
    from openmuscle.quality import FLAG_WORDS
    tour = _norm(_read("studio-tour.md"))
    for word in FLAG_WORDS.values():
        assert word.lower() in tour, "quality tag '{}' missing from studio-tour.md".format(word)
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    m = re.search(r"QUALITY_WORD\s*=\s*\{([^}]*)\}", app)
    words = re.findall(r"'([A-Za-z]+)'", m.group(1))
    assert set(words) == {"Good", "Check", "Bad"}
    for w in words:
        assert "**{}**".format(w) in _read("studio-tour.md")


# ------------------------------------------------- result card vs verdict

def test_grade_words_and_cutoffs_come_from_verdict_py():
    from openmuscle.ml import verdict
    text = _read("first-model.md")
    for grade, headline in (("good", "Good"), ("fair", "Fair"), ("weak", "Weak"),
                            ("not_working", "Not working yet")):
        assert verdict._HEADLINES[grade] == headline
        assert "**{}**".format(headline) in text
    assert "**Not tested yet**" in text
    for grade in ("good", "fair", "weak"):
        pct = "{}%".format(int(round(verdict.THRESHOLDS[grade] * 100)))
        row = [ln for ln in text.splitlines()
               if ln.startswith("| **{}**".format(grade.capitalize()))]
        assert row and pct in row[0], "{} row should start at {}".format(grade, pct)
    assert "below {}%".format(int(round(verdict.THRESHOLDS["weak"] * 100))) in text
    # per-finger wording
    fth = verdict.FINGER_THRESHOLDS
    assert "{}% or more".format(int(round(fth["follows"] * 100))) in text
    assert "below {}%".format(int(round(fth["not_learned"] * 100))) in text
    assert "{}% to {}%".format(int(round(fth["not_learned"] * 100)),
                               int(round(fth["follows"] * 100)) - 1) in text


def test_next_steps_are_quoted_exactly():
    from openmuscle.ml.verdict import describe_result
    text = _read("first-model.md")
    base = {"eval_method": "held_out_capture", "eval_captures": ["x"],
            "score_space": "curl", "per_finger": []}
    for score in (0.9, 0.6, 0.3, 0.05):
        step = describe_result(dict(base, curl_r2=score), n_captures=8)["next_step"]
        assert step in text, "next step not quoted in first-model.md: " + step
    assert "Held at Fair" in text
    held = describe_result(dict(base, eval_method="held_out_tail", curl_r2=0.9),
                           n_captures=1)
    assert "Held at Fair" in held["detail"]


# ------------------------------------------------------------ take length

def test_take_lengths_match_take_limits():
    from openmuscle.take_limits import MIN_TAKE_SECONDS, RECOMMENDED_TAKE_SECONDS
    sheet = _read("movement-sheet.md")
    first = _read("first-model.md")
    tour = _read("studio-tour.md")
    assert "{} seconds".format(int(MIN_TAKE_SECONDS)) in first
    assert "{} seconds".format(int(RECOMMENDED_TAKE_SECONDS)) in first
    assert "0:{:02d}".format(int(MIN_TAKE_SECONDS)) in sheet
    assert "0:{:02d}".format(int(RECOMMENDED_TAKE_SECONDS)) in sheet
    assert "{} seconds".format(int(MIN_TAKE_SECONDS)) in tour


def test_protocol_has_eight_takes_of_thirty_seconds():
    first = _read("first-model.md")
    sheet = _read("movement-sheet.md")
    assert "eight takes" in first.lower()
    for n in range(1, 9):
        assert "`0{}-".format(n) in sheet or "0{}".format(n) in first
    rows = [ln for ln in sheet.splitlines() if re.match(r"\| \[ \] \| \d \|", ln)]
    assert len(rows) == 8
    # the held-out take is the last one, and the sheet says so
    assert "kept aside" in rows[-1]


def test_the_last_takes_are_the_held_out_ones():
    """training.py holds out the last recorded takes, so the script must end on the
    free mix, and 8 takes hold out 1 or 2 of them."""
    from openmuscle.ml import evaluate as ev
    caps = []
    for i in range(1, 9):
        caps += ["take{}".format(i)] * 300
    plan = ev.plan_holdout(len(caps), captures=caps, times_ms=list(range(len(caps))))
    assert plan["method"] == "held_out_capture"
    assert plan["eval_captures"][-1] == "take8"
    assert len(plan["eval_captures"]) in (1, 2)
    six = []
    for i in range(1, 7):
        six += ["take{}".format(i)] * 300
    plan6 = ev.plan_holdout(len(six), captures=six, times_ms=list(range(len(six))))
    assert plan6["eval_captures"] == ["take6"]


# ------------------------------------------------------------ links

def _slug(heading):
    h = heading.strip().lower()
    h = re.sub(r"[`*_]", "", h)
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def _anchors(md):
    return {_slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", md, re.M)}


@pytest.mark.parametrize("name", GUIDES)
def test_relative_links_resolve(name):
    text = _read(name)
    bad = []
    for m in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)", text):
        target = m.group(1)
        if re.match(r"[a-z]+://", target) or target.startswith("mailto:"):
            continue
        path, _, anchor = target.partition("#")
        if not path:
            if anchor and anchor not in _anchors(text):
                bad.append(target)
            continue
        dest = (DOCS / path).resolve()
        if not dest.exists():
            if Path(path).name in SIBLING_PAGES:
                continue
            bad.append(target)
            continue
        if anchor and dest.suffix == ".md" and anchor not in _anchors(
                dest.read_text(encoding="utf-8")):
            bad.append(target)
    assert not bad, "{}: links that do not resolve: {}".format(name, bad)


@pytest.mark.parametrize("name", ["studio-tour.md", "first-model.md", "webcam-labeling.md"])
def test_guides_link_to_the_glossary_or_are_linked_from_it(name):
    text = _read(name)
    assert "glossary.md" in text or "glossary.md" in _read("studio-guides.md")


def test_images_exist_and_are_small():
    seen = set()
    for name in GUIDES:
        for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)\)", _read(name)):
            dest = DOCS / m.group(1)
            assert dest.exists(), "{} references missing image {}".format(name, m.group(1))
            assert dest.stat().st_size < 600 * 1024, m.group(1) + " is too large"
            assert dest.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
            seen.add(m.group(1))
    assert len(seen) >= 3


# ------------------------------------------------------------ glossary

def _entries(text):
    """Glossary terms: a bold word that starts a paragraph."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return [m.group(1) for p in paragraphs
            for m in [re.match(r"\*\*(.+?)\*\*", p.strip())] if m]


def test_glossary_is_alphabetical_by_section_and_has_every_term():
    text = _read("glossary.md")
    heads = re.findall(r"^## (.+)$", text, re.M)
    letters = [h for h in heads if len(h) == 1]
    assert letters == sorted(letters)
    terms = [t.strip() for t in _entries(text)]
    low = [t.lower() for t in terms]
    for want in GLOSSARY_TERMS:
        assert want.lower() in low, "glossary is missing: " + want
    # each lettered section lists its terms in order
    for sec in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = sec.partition("\n")
        if len(title.strip()) != 1:
            continue
        names = [t.lower() for t in _entries(body)]
        assert names == sorted(names), "section {} is not alphabetical: {}".format(title, names)
        for n in names:
            assert n[0].upper() == title.strip().upper() or n[0].isdigit(), (
                "{} filed under {}".format(n, title))


def test_glossary_defines_the_quality_and_result_words():
    g = _read("glossary.md")
    for w in ("**Check**", "**Verdict**", "**Held-out score**", "**R2 score**"):
        assert w in g


# ------------------------------------------------------------ matrix

def test_support_matrix_is_filled():
    text = _read("webcam-labeling.md")
    rows = {}
    for ln in text.splitlines():
        m = re.match(r"\| \*\*(Chrome|Edge|Firefox|Safari)\*\* \|(.*)\|\s*$", ln)
        if m:
            rows[m.group(1)] = [c.strip() for c in m.group(2).split("|")]
    assert set(rows) == {"Chrome", "Edge", "Firefox", "Safari"}
    for browser, cells in rows.items():
        assert len(cells) == 3 and all(cells), browser
    assert rows["Safari"][0] == "Not available" and rows["Safari"][2] == "Not available"
    assert "http://localhost:8000/webcam" in text
    assert "https://localhost" not in text
    assert "17.6 MB" in text
    assert "8 MB" not in text.replace("17.6 MB", "")
    assert "Unsupported" in text or "What does not work, and why" in text
