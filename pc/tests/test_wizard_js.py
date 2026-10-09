"""Runs the Node unit tests for the device setup wizard's pure module
(static/setup/wizard.js). Skipped when Node is not installed."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TEST = Path(__file__).parent / "js" / "wizard.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_wizard_js_unit_tests():
    r = subprocess.run(["node", "--test", str(JS_TEST)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
