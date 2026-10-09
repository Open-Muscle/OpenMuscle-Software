"""Runs the Node unit tests for the dashboard's pure guidance module
(static/guide.js). Skipped when Node is not installed."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TEST = Path(__file__).parent / "js" / "guide.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_guide_js_unit_tests():
    r = subprocess.run(["node", "--test", str(JS_TEST)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
