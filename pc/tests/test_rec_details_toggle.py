"""The saved-take card must remember when Details is open.

The card is rendered straight into recordStatus.innerHTML, bypassing
setRecStatusHtml, so the toggle wiring lives in a shared helper both paths
call. A fake-DOM run in Node proves the helper keeps _recDetailsOpen in step
with the element; a static check proves both render paths call it.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = (Path(__file__).resolve().parents[1] / "src" / "openmuscle" / "web"
          / "static" / "app.js")


def _js():
    return APP_JS.read_text(encoding="utf-8")


def _function_body(js, name):
    m = re.search(r"function %s\([^)]*\) \{" % name, js)
    assert m, name + " not found"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(js[i], 0)
        i += 1
    return js[m.start():i]


def test_both_render_paths_wire_the_details_toggle():
    js = _js()
    assert "toggle" in _function_body(js, "wireRecDetailsToggle")
    assert "wireRecDetailsToggle()" in _function_body(js, "setRecStatusHtml")
    assert "wireRecDetailsToggle()" in _function_body(js, "showPostStopCard")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_details_toggle_updates_open_state_in_a_fake_dom(tmp_path):
    helper = _function_body(_js(), "wireRecDetailsToggle")
    script = """
let _recDetailsOpen = false;
const listeners = {};
const d = { open: false, addEventListener: (t, f) => { listeners[t] = f; } };
const recordStatus = { querySelector: (q) => q === 'details.rec-details' ? d : null };
%s
wireRecDetailsToggle();
if (typeof listeners.toggle !== 'function') throw new Error('no toggle listener');
d.open = true; listeners.toggle();
if (_recDetailsOpen !== true) throw new Error('open not tracked');
d.open = false; listeners.toggle();
if (_recDetailsOpen !== false) throw new Error('close not tracked');
""" % helper
    f = tmp_path / "details_toggle.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
