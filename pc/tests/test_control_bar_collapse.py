"""The sticky control bar must not hide the page after a take.

The wording and the action list are unit tested in js/rec-status.test.js; this
file checks the wiring in the shipped static files: the folded result card
keeps the verdict word and the three actions visible, and the bar is capped to
about a third of the window.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "openmuscle" / "web" / "static"


def _css():
    return (STATIC / "styles.css").read_text(encoding="utf-8")


def _rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def test_control_bar_is_capped_to_a_third_of_the_window():
    for sel, body in _rules(_css()):
        if sel.strip() == "#control-bar":
            assert re.search(r"max-height:\s*33vh", body)
            assert re.search(r"overflow-y:\s*auto", body)
            return
    raise AssertionError("#control-bar rule not found")


def test_collapsed_card_hides_detail_but_never_verdict_or_actions():
    hidden = []
    for sel, body in _rules(_css()):
        if "display: none" in body and ".rec-card.collapsed" in sel:
            hidden += [s.strip() for s in sel.split(",")]
    assert hidden, "folded card should hide its long parts"
    for s in hidden:
        assert "cap-verdict" not in s
        assert "rec-verdict-toggle" not in s
        assert "rec-next-actions" not in s
        assert not s.endswith(".rec-card.collapsed")
        assert "button" not in s
    # the long parts that must fold away
    joined = " ".join(hidden)
    for part in (".rec-sentence", ".rec-hint", ".rec-details"):
        assert part in joined


def test_app_renders_card_from_shared_action_list_and_arms_collapse():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    # the card is built from savedSummary (verdict word + the shared action list)
    assert "OMRecStatus.savedSummary(result)" in js and "sum.actions" in js
    assert 'id="rec-card"' in js
    assert 'id="rec-verdict-toggle"' in js
    assert "OMRecStatus.COLLAPSE_AFTER_MS" in js
    assert "addEventListener('scroll'" in js
    # the old hard-coded three buttons are gone: one list feeds both states
    assert '<button class="link" id="rec-next-take">' not in js


def test_index_loads_rec_status_before_app():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="/static/rec-status.js') < html.index('src="/static/app.js"')
