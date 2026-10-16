"""Runs the Node tests for the USB cable branch of the device setup wizard
(tests/js/wizard-usb.test.js): the step machine in static/setup/wizard.js and the
page in static/setup/setup.js against a fake DOM. Skipped when Node is not
installed."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TEST = Path(__file__).parent / "js" / "wizard-usb.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_wizard_usb_js_unit_tests():
    r = subprocess.run(["node", "--test", str(JS_TEST)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
