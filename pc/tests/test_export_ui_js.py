"""Runs the Node smoke test for the export dialog and the storage panel in
static/app.js (the real script, in a vm sandbox with a stand-in DOM). Skipped
when Node is not installed."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TEST = Path(__file__).parent / "js" / "export_ui.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_export_ui_js_smoke():
    r = subprocess.run(["node", "--test", str(JS_TEST)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
