"""End-to-end check of the Windows installer on a real Windows machine.

Slow (a few minutes) and needs the internet, so it only runs when asked:

    set OPENMUSCLE_INSTALL_SMOKE=1
    py -m pytest tests/test_install_windows_smoke.py -q

It double-click-installs through Install-OpenMuscle.bat into a throwaway data
folder whose path contains spaces (OPENMUSCLE_HOME), with the two shortcuts sent
to throwaway folders too, then checks the environment, the command, the shortcuts,
the data folders and the log, and runs the installer a second time.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BAT = REPO / "installers" / "windows" / "Install-OpenMuscle.bat"

pytestmark = pytest.mark.skipif(
    not (sys.platform.startswith("win") and os.environ.get("OPENMUSCLE_INSTALL_SMOKE") == "1"),
    reason="set OPENMUSCLE_INSTALL_SMOKE=1 on Windows to run the installer for real")


def _run_installer(home, desktop, menu):
    env = dict(os.environ, OPENMUSCLE_HOME=str(home))
    # One command-line string, as a double-click would produce, so a path with
    # spaces is quoted exactly once (/s strips the outer pair of quotes).
    line = ('cmd.exe /d /s /c ""%s" -SkipAssets -SkipDoctor -Firewall no '
            '-DesktopDir "%s" -StartMenuDir "%s""' % (BAT, desktop, menu))
    return subprocess.run(line, capture_output=True, text=True, env=env, timeout=1500,
                          stdin=subprocess.DEVNULL)


def _shortcut_target(lnk):
    cmd = ("(New-Object -ComObject WScript.Shell).CreateShortcut('%s').TargetPath" % lnk)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                       text=True, timeout=60)
    return r.stdout.strip()


def test_installer_makes_a_working_install_and_is_repeatable(tmp_path):
    home = tmp_path / "open muscle home"
    desktop = tmp_path / "desk top"
    menu = tmp_path / "start menu"

    for attempt in (1, 2):
        r = _run_installer(home, desktop, menu)
        log = home / "logs" / "install.log"
        assert r.returncode == 0, "run %d failed:\n%s\n%s" % (
            attempt, r.stdout[-2000:], log.read_text(encoding="utf-8", errors="replace")[-2000:]
            if log.exists() else "")
        assert "OpenMuscle is installed" in r.stdout

    exe = home / "app" / "Scripts" / "openmuscle.exe"
    assert exe.is_file()
    version = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=120)
    assert version.returncode == 0 and "openmuscle" in version.stdout.lower()

    # A normal (non-editable) install must carry the VR page.
    probe = subprocess.run(
        [str(home / "app" / "Scripts" / "python.exe"), "-c",
         "import os, openmuscle; "
         "print(os.path.isfile(os.path.join(os.path.dirname(openmuscle.__file__), "
         "'web', 'static', 'vr', 'index.html')))"],
        capture_output=True, text=True, timeout=120)
    assert probe.stdout.strip() == "True"

    for sub in ("captures", "sessions", "models", "exports", "logs"):
        assert (home / sub).is_dir(), sub
    launcher = home / "app" / "OpenMuscle.bat"
    assert launcher.is_file() and (home / "app" / "openmuscle.ico").is_file()

    for folder in (desktop, menu):
        lnk = folder / "OpenMuscle.lnk"
        assert lnk.is_file(), lnk
        assert _shortcut_target(lnk).lower() == str(launcher).lower()
    assert (home / "logs" / "install.log").is_file()
