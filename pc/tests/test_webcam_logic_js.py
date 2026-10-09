"""Runs the Node unit tests for the webcam page's pure logic
(static/webcam/track-logic.js: hand selection, failure policy, plain error
wording). Skipped when Node is not installed."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TEST = Path(__file__).parent / "js" / "webcam-logic.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_webcam_logic_js_unit_tests():
    r = subprocess.run(["node", "--test", str(JS_TEST)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
