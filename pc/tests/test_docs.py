"""Guards for the written documentation.

The guides are the first thing a person with a kit reads, so they are held to
a few mechanical rules: no em dashes anywhere, no personal or site words, every
relative link points at a file that exists (and at a heading that exists), and
the commands and screen labels the kit journey quotes still exist in the code.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

# Pages a work item has promised but not yet added to docs/. Links to them may
# dangle until they land; once a page exists its links are checked like any other.
# Empty: every guide the journey links to is in place.
PENDING_PAGES = set()

MARKDOWN = sorted(
    [REPO / "README.md", REPO / "embedded" / "README.md",
     REPO / "pc" / "src" / "openmuscle" / "web" / "README.md"]
    + list(DOCS.glob("*.md")))

# Written for people who have never opened a terminal.
KIT_PAGES = ["getting-started.md", "wifi-setup.md", "troubleshooting.md",
             "your-data.md", "band-fit.md"]

EM_DASH = chr(0x2014)


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _slug(heading):
    """GitHub-style anchor for a heading."""
    text = re.sub(r"`", "", heading.strip()).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _headings(path):
    out = set()
    in_code = False
    for line in _read(path).splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            m = re.match(r"^#{1,6}\s+(.*)$", line)
            if m:
                out.add(_slug(m.group(1)))
    return out


def _links(path):
    """(text, target) for every markdown link outside fenced code."""
    in_code = False
    for line in _read(path).splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in re.finditer(r"\[([^\]]*)\]\(([^)\s]+)\)", line):
            yield m.group(1), m.group(2)


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: str(p.relative_to(REPO)))
def test_no_em_dashes(path):
    assert EM_DASH not in _read(path)


def test_getting_started_is_plain():
    text = _read(DOCS / "getting-started.md").lower()
    for word in ("participant", "clark"):
        assert word not in text


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: str(p.relative_to(REPO)))
def test_relative_links_resolve(path):
    for text, target in _links(path):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue                       # http:, https:, mailto:
        base, _, anchor = target.partition("#")
        dest = path if not base else (path.parent / base).resolve()
        if base and dest.name in PENDING_PAGES and not dest.exists():
            continue
        assert dest.exists(), "%s links to missing %s" % (path.name, target)
        if anchor and dest.suffix == ".md":
            assert anchor in _headings(dest), \
                "%s links to %s but %s has no such heading" % (path.name, target, dest.name)


def test_kit_pages_exist_and_have_you_should_see():
    for name in KIT_PAGES:
        assert (DOCS / name).is_file(), name
    # The step-by-step journey ends every step with what to expect.
    text = _read(DOCS / "getting-started.md")
    steps = re.split(r"(?m)^## ", text)
    numbered = [s for s in steps if re.match(r"^[1-9]\. ", s)]
    assert len(numbered) >= 8
    for step in numbered:
        assert "You should see" in step or "you should see" in step, step.splitlines()[0]


def test_readme_front_door_links():
    top = "\n".join(_read(REPO / "README.md").splitlines()[:40])
    for needle in ("docs/getting-started.md", "docs/troubleshooting.md", "docs/wifi-setup.md"):
        assert needle in top
    assert "install" in top.lower()


def test_readme_has_no_stale_claims():
    text = _read(REPO / "README.md")
    assert "current FlexGrid revision, with the sensor-scan" not in text
    assert "write_flash" not in text            # no raw esptool recipe for a flexgrid_v1 flash
    assert "flexgrid_v1/*.py" not in text
    assert "FlexGridV4-Firmware" in text and "LASK5-Firmware" in text


def test_docs_do_not_claim_https_for_plain_web():
    pc_cli = _read(DOCS / "pc-cli.md")
    assert "Plain HTTP on `127.0.0.1:8000`" in pc_cli
    # the old broken replay example (a legacy .txt file) is gone
    assert "capture_45.txt" not in pc_cli
    assert "capture_45.txt" not in _read(REPO / "README.md")


def _cli_commands():
    from openmuscle.cli import main
    return set(main.commands)


def test_commands_named_in_the_guides_exist():
    """Every `openmuscle <command>` the kit pages tell a person to type is real."""
    known = _cli_commands()
    pat = re.compile(r"openmuscle(?:\.exe)?\"?\s+([a-z][a-z-]*)")
    for name in KIT_PAGES + ["pc-cli.md"]:
        for m in pat.finditer(_read(DOCS / name)):
            word = m.group(1)
            if word in ("is", "the", "to", "and", "in", "or", "from", "with", "so", "for",
                        "a", "folder", "program", "command", "keeps", "stays", "saves"):
                continue
            assert word in known, "%s mentions `openmuscle %s`, which does not exist" % (name, word)


def test_screen_labels_quoted_in_the_guides_exist():
    """Buttons and menu names the journey tells people to click are in the page."""
    static = REPO / "pc" / "src" / "openmuscle" / "web" / "static"
    pages = "".join(_read(p) for p in (static / "index.html", static / "app.js",
                                       static / "guide.js", static / "train-card.js", static / "rec-status.js",
                                       static / "setup" / "wizard.js"))
    for label in ("Set up a device", "Record", "Finger data from", "Export session",
                  "Export selected", "Open folder", "Copy path", "Scan subnet",
                  "Add a device by IP address", "Go to Train", "Predict", "Camera", "Include my notes",
                  "Download zip", "Recordings are saved in", "I am ready",
                  "Send to the device", "Set up the next device", "Run a check",
                  "Find my devices", "Show file", "Record next take", "too short",
                  "Include full raw streams", "Data &amp; Models"):
        assert label in pages, label


def test_kit_pages_offer_the_no_screen_path():
    """The kit bands ship without the small screen: the sticker holds the setup
    name and password, and the blue two-quick-blinks light is the normal setup
    pattern. Pages that talk about the screen must say so."""
    wifi = _read(DOCS / "wifi-setup.md")
    started = _read(DOCS / "getting-started.md")
    trouble = _read(DOCS / "troubleshooting.md")
    for name, text in (("wifi-setup.md", wifi), ("getting-started.md", started),
                       ("troubleshooting.md", trouble)):
        assert "sticker" in text, name
        assert re.search(r"two quick blinks", text), name
    assert "Setup password" in wifi and "Setup Wi-Fi" in wifi
    # No "read it off the screen" instruction left without the sticker path.
    assert "Type the password from the last line of the band's screen" not in wifi
    assert "shown on the band's screen" not in started
    # The glossary no longer says every band shows its password on a screen.
    gloss = _read(DOCS / "glossary.md")
    assert "sticker" in gloss
    # The troubleshooting table calls the blink pattern normal, not a fault.
    row = [l for l in trouble.splitlines()
           if l.startswith("| Blue, two quick blinks then a pause")]
    assert row and "normal" in row[0].lower() and "contact support" not in row[0]


def _html_text(path):
    """Visible words of an HTML file: tags dropped, entities left alone."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _read(path)))


def test_setup_is_described_with_usb_and_the_faint_glow():
    """The band keeps its status light faintly lit whenever it is powered, so
    dark means switched off, and its setup network does not start on battery
    alone: every place that describes setup says to plug in USB first."""
    wifi = _read(DOCS / "wifi-setup.md")
    started = _read(DOCS / "getting-started.md")
    trouble = _read(DOCS / "troubleshooting.md")
    for name, text in (("wifi-setup.md", wifi), ("getting-started.md", started),
                       ("troubleshooting.md", trouble)):
        assert "faint glow" in text, name
        assert "battery alone" in text, name
    # The light table has the glow row and a dark row that means "switched off".
    rows = [l for l in trouble.splitlines() if l.startswith("| ")]
    assert any(l.startswith("| Faint glow, always") and "powered on" in l for l in rows)
    assert any(l.startswith("| Dark") and "switched off" in l for l in rows)
    stuck = [l for l in rows if l.startswith("| Faint glow only")]
    assert stuck and "never blinks blue" in stuck[0] and "Plug the band into USB" in stuck[0]
    # The old wrong rows and instructions are gone.
    for text in (wifi, started, trouble):
        assert "battery empty with USB unplugged" not in text
        assert "plug in USB-C for a few minutes" not in text
        assert "A band switches on whenever USB power is connected" not in text
    # The step that starts setup says to plug in before anything else.
    assert "Plug the band into USB, turn it on" in wifi
    assert "faint glow" in _read(DOCS / "studio-tour.md")
    assert "faint glow" in _read(DOCS / "glossary.md")


def test_cheat_sheet_light_table_and_setup_steps_match_the_band():
    sheet = DOCS / "cheat-sheet" / "openmuscle-kit-cheat-sheet.html"
    text = _html_text(sheet)
    assert "Faint glow, always." in text
    assert "dark means switched off" in text
    assert "Dark. Switched off." in text
    assert "Light off" not in text
    assert "battery empty with USB unplugged" not in text
    # Page 1 switch-on step and board legend, page 3 Wi-Fi step 1.
    assert "Keep USB plugged in for Wi-Fi setup" in text
    assert "on battery alone the band cannot start its setup network" in text
    assert "Plug in for Wi-Fi setup" in text
    assert "USB cable plugged in, blue light blinking" in text
    assert EM_DASH not in _read(sheet)


def test_windows_install_prompts_and_the_private_network_check():
    """A cold run of the Windows install showed two dialogs a recipient must
    answer (the administrator question for the firewall rule, and the Windows
    Security Alert for python.exe during the installer's own check) and a trap:
    a network Windows calls Public is outside the firewall rule, so no band is
    ever found. Every install page says so, in the same words."""
    started = _read(DOCS / "getting-started.md")
    installers = _read(REPO / "installers" / "README.md")
    trouble = _read(DOCS / "troubleshooting.md")
    for name, text in (("getting-started.md", started), ("installers/README.md", installers)):
        assert "Windows Security Alert" in text, name
        assert "python.exe" in text, name
        assert "Allow access" in text, name
        assert "make changes" in text, name
        assert "Network profile type" in text, name
        assert "Private" in text and "Public" in text, name
    # The alert is described inside the install step, before Start the Studio.
    install = started.split("## 3. Start the Studio")[0]
    assert "Windows Security Alert" in install and "make changes" in install
    # The Private check is its own step straight after the install.
    assert re.search(r"(?m)^## 2b\. Windows: check your Wi-Fi is set to Private$", started)
    assert started.index("## 2b.") < started.index("## 3. Start the Studio")
    section = started.split("## 2b.")[1].split("\n## 3.")[0]
    assert "You should see" in section
    # The Wi-Fi step says the band gets its Wi-Fi over USB from the wizard.
    step4 = started.split("## 4. Put the bands on your Wi-Fi")[1].split("\n## 5.")[0]
    assert "USB cable" in step4 and "Set up a device" in step4 and "wifi-setup.md" in step4
    # The "No band found yet" row in troubleshooting leads with the Public profile.
    row = [l for l in trouble.splitlines() if l.startswith("| No band found yet.")]
    assert row and "Private" in row[0] and "Public" in row[0]
    assert row[0].index("Private") < row[0].index("A band does not show up")

