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


def _wizard_text():
    """The wizard's wording as the page shows it (the source escapes quotes)."""
    static = REPO / "pc" / "src" / "openmuscle" / "web" / "static"
    return _read(static / "setup" / "wizard.js").replace("\\'", "'")


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
    assert EM_DASH not in _read(sheet)


def test_cheat_sheet_wifi_step_is_the_usb_road():
    """Page 3 step 5 gives the Wi-Fi over the USB cable, with the band's own Wi-Fi
    only as the fallback, and quotes the wizard's labels exactly."""
    text = _html_text(DOCS / "cheat-sheet" / "openmuscle-kit-cheat-sheet.html")
    for needle in ("once per band; the Studio sends it over the USB cable",
                   "Plug the band into this computer",
                   "Found Band",
                   "Send to the device",
                   "Send the same Wi-Fi details",
                   "Use the band's own Wi-Fi instead",
                   "Start over with this device",
                   "Unplug the cable and plug it back in (or press BOOT), then try again",
                   "some cables only charge"):
        assert needle in text, needle
    # The old setup-network walk-through is gone from step 5.
    assert "Join the Wi-Fi starting" not in text
    assert "After about 20 s rejoin your usual Wi-Fi" not in text
    wizard = _wizard_text()
    for label in ("Plug the band into this computer", "Send to the device",
                  "Send the same Wi-Fi details", "Use the band's own Wi-Fi instead",
                  "Start over with this device", "I am ready", "Set up a device"):
        assert label in wizard, label


def test_wifi_setup_has_the_usb_cable_as_method_1_and_the_setup_network_as_method_2():
    wifi = _read(DOCS / "wifi-setup.md")
    m1 = "## Method 1: the USB cable (recommended)"
    m2 = "## Method 2: the band's own Wi-Fi"
    assert m1 in wifi and m2 in wifi
    assert wifi.index(m1) < wifi.index(m2)
    one, two = wifi[wifi.index(m1):wifi.index(m2)], wifi[wifi.index(m2):]
    # Method 1 is the cable: no OM- network to join, and it says so.
    assert "Plug the band into this computer" in one
    assert "Your computer does not leave its Wi-Fi" in one
    assert "setup-usb" in one
    # Method 2 keeps the setup-network walk-through and how to reach it.
    assert "Use the band's own Wi-Fi instead" in two
    assert "Join the device's own Wi-Fi" not in one
    assert "Yes, that is the one" in two and "Setup password" in two


def test_usb_labels_in_the_guides_match_the_wizard_and_the_host_code():
    """Every sentence the guides quote from the USB road is the live text: the
    wizard's own wording, or the sentence usb.py hands the page."""
    wizard = _wizard_text()
    usb_py = _read(REPO / "pc" / "src" / "openmuscle" / "provision" / "usb.py")
    wifi = _read(DOCS / "wifi-setup.md")
    trouble = _read(DOCS / "troubleshooting.md")
    from_wizard = [
        "Plug the band into this computer",
        "Looking for a band on the USB cable...",
        "More than one band is plugged in. Choose the one to set up now, or unplug the others.",
        "Does the Setup Wi-Fi line on its sticker end with",
        "If not, this is a different band.",
        "Sending new details replaces them.",
        "Saved on the band. It is restarting (about 20 seconds) and will join",
        "Keep it plugged in until it is online.",
        "Use the band's own Wi-Fi instead",
        "Send the same Wi-Fi details",
        "Start over with this device",
        "Remember for the next device (this browser tab only)",
        "A band is plugged in but is not answering",
        "No band found yet",
    ]
    for label in from_wizard:
        assert label in wizard, label
        assert label in wifi or label in trouble, label
    from_usb = [
        "Could not open the USB port",
        "The band could not save the Wi-Fi details",
        "This band's software does not know how to be set up over USB",
        "Unplug the cable and plug it back in (or press the BOOT button), then try again.",
    ]
    for label in from_usb:
        assert label in usb_py, label
        assert label in wifi or label in trouble, label


def test_troubleshooting_has_the_usb_rows():
    trouble = _read(DOCS / "troubleshooting.md")
    assert "### Setting up over the USB cable" in trouble
    section = trouble[trouble.index("### Setting up over the USB cable"):
                      trouble.index("### The band keeps going back to setup mode")]
    rows = [l for l in section.splitlines() if l.startswith("| **") or l.startswith("| It said")]
    assert len(rows) >= 6
    by_start = {l.split("|")[1].strip(): l for l in rows}
    nofind = next(l for k, l in by_start.items() if k.startswith("**Looking for a band"))
    assert "charge" in nofind and "data" in nofind
    silent = next(l for k, l in by_start.items() if "not answering" in k)
    assert "plug it back in (or press the BOOT button)" in silent
    several = next(l for k, l in by_start.items() if "More than one band" in k)
    assert "Unplug all but the one" in several


def test_setup_usb_is_a_real_command_and_the_guides_name_it():
    assert "setup-usb" in _cli_commands()
    for name in ("wifi-setup.md", "pc-cli.md"):
        assert "openmuscle setup-usb" in _read(DOCS / name) or "setup-usb" in _read(DOCS / name), name
    assert "setup-usb" in _read(REPO / "README.md")
