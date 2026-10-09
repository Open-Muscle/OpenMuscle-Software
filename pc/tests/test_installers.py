"""The installer scripts: right line endings, executable bits, plain text, and
that each one at least parses. The scripts themselves are exercised by hand (and
by test_install_windows_smoke.py); these checks catch the quiet breakages: a bare
LF in a .bat, a CRLF in a shell script, a lost executable bit, a stray non-ASCII
character that Windows PowerShell 5.1 would misread.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLERS = REPO / "installers"

WINDOWS_SCRIPTS = ("*.bat", "*.cmd", "*.ps1")
UNIX_SCRIPTS = ("*.sh", "*.command")

# The names every guide and the other agents rely on.
CONTRACT = [
    "installers/README.md",
    "installers/windows/install.ps1",
    "installers/windows/Install-OpenMuscle.bat",
    "installers/windows/OpenMuscle.bat",
    "installers/windows/openmuscle.ico",
    "installers/macos/install.command",
    "installers/macos/OpenMuscle.command",
    "installers/linux/install.sh",
    "installers/linux/openmuscle.desktop",
]


def _git(*args):
    try:
        out = subprocess.run(["git"] + list(args), cwd=REPO, capture_output=True,
                             check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.decode("utf-8", "replace")


def _scripts(patterns):
    """Tracked files matching the patterns, else whatever is on disk."""
    listed = _git("ls-files", "-z")
    if listed is not None:
        names = [n for n in listed.split("\0") if n]
        found = [REPO / n for n in names if any(Path(n).match(p) for p in patterns)]
    else:
        found = [p for pat in patterns for p in REPO.rglob(pat)]
    skip = ("node_modules", ".venv", "venv")
    return sorted(p for p in found if p.is_file() and not any(s in p.parts for s in skip))


def _rel(p):
    return p.relative_to(REPO).as_posix()


def test_contract_files_exist():
    missing = [c for c in CONTRACT if not (REPO / c).is_file()]
    assert not missing, "missing installer files: %s" % missing


def test_gitattributes_pins_the_line_endings():
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    rules = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pattern, _, attrs = line.partition(" ")
            rules[pattern] = attrs.split()
    # Windows scripts are stored as-is (CRLF in the repository, never converted).
    for pat in WINDOWS_SCRIPTS:
        assert "-text" in rules.get(pat, []), "%s must be -text (stored with CRLF)" % pat
    # Unix scripts are LF in the repository and on checkout.
    for pat in UNIX_SCRIPTS + ("*.desktop",):
        assert "eol=lf" in rules.get(pat, []), "%s must say eol=lf" % pat


def test_windows_scripts_use_crlf_only():
    scripts = _scripts(WINDOWS_SCRIPTS)
    assert scripts, "no .bat/.ps1 files found"
    bad = []
    for p in scripts:
        data = p.read_bytes()
        bare_lf = len(re.findall(rb"(?<!\r)\n", data))
        if bare_lf:
            bad.append("%s has %d line(s) ending in a bare LF" % (_rel(p), bare_lf))
    assert not bad, "\n".join(bad)


def test_unix_scripts_use_lf_only_and_a_shebang():
    scripts = _scripts(UNIX_SCRIPTS)
    assert scripts, "no .sh/.command files found"
    bad = []
    for p in scripts:
        data = p.read_bytes()
        if b"\r" in data:
            bad.append("%s contains a carriage return" % _rel(p))
        if not data.startswith(b"#!"):
            bad.append("%s does not start with a #! line" % _rel(p))
    assert not bad, "\n".join(bad)


def test_desktop_entry_is_lf_and_complete():
    text = (INSTALLERS / "linux" / "openmuscle.desktop").read_bytes()
    assert b"\r" not in text
    body = text.decode("utf-8")
    for key in ("[Desktop Entry]", "Type=Application", "Name=OpenMuscle", "Terminal=true"):
        assert key in body
    assert "@EXEC@" in body and "@ICON@" in body


def test_unix_scripts_are_executable_in_git():
    out = _git("ls-files", "-s", "-z")
    if out is None:
        pytest.skip("not a git checkout")
    modes = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, name = entry.partition("\t")
        modes[name] = meta.split()[0]
    scripts = [n for n in modes if n.startswith("installers/")
               and n.endswith((".sh", ".command"))]
    assert scripts, "no tracked installer scripts (not added to git yet?)"
    bad = [n for n in scripts if modes[n] != "100755"]
    assert not bad, "not mode 100755 in git (git update-index --chmod=+x): %s" % bad


def test_installer_files_are_plain_ascii_without_em_dashes():
    """Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI, so any non-ASCII
    character can turn into mojibake or a parse error. Keep every script, and the
    guide next to them, to plain ASCII."""
    bad = []
    for p in INSTALLERS.rglob("*"):
        if not p.is_file() or p.suffix in (".ico", ".png"):
            continue
        data = p.read_bytes()
        offenders = sorted({c for c in data if c > 126 or (c < 32 and c not in (9, 10, 13))})
        if offenders:
            bad.append("%s has non-ASCII or control bytes %s" % (_rel(p), offenders[:5]))
    assert not bad, "\n".join(bad)


def test_installers_do_not_ask_for_admin_rights():
    for p in _scripts(WINDOWS_SCRIPTS + UNIX_SCRIPTS):
        if "installers" not in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"RunAs|#requires -RunAsAdministrator", text, re.I), _rel(p)
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            assert not re.match(r"\s*sudo\s", code), \
                "%s runs sudo: %s" % (_rel(p), line.strip())


def test_windows_installer_follows_the_python_rules():
    text = (INSTALLERS / "windows" / "install.ps1").read_text(encoding="ascii")
    # Never trust the bare "python" name (Microsoft Store shortcut on a fresh PC).
    assert "py.exe" in text
    assert not re.search(r"&\s*python(\.exe)?\s", text)
    assert "Python.Python.3.12" in text and "--scope" in text
    assert "Add python.exe to PATH" in text
    assert "install.log" in text
    # Every path the script builds is quoted when passed to another program.
    assert "ConvertTo-ArgText" in text


def test_launcher_runs_the_web_command_and_checks_for_a_running_copy():
    bat = (INSTALLERS / "windows" / "OpenMuscle.bat").read_text(encoding="ascii")
    assert "web %*" in bat
    assert "OpenMuscle (leave open)" in bat
    assert "/api/mode" in bat and "pause" in bat
    for rel in ("macos/OpenMuscle.command",):
        sh = (INSTALLERS / rel).read_text(encoding="ascii")
        assert '"$EXE" web "$@"' in sh and "/api/mode" in sh
    linux = (INSTALLERS / "linux" / "install.sh").read_text(encoding="ascii")
    assert '"\\$EXE" web "\\$@"' in linux and "/api/mode" in linux


def test_icon_is_a_valid_multi_size_ico():
    data = (INSTALLERS / "windows" / "openmuscle.ico").read_bytes()
    reserved, kind, count = int.from_bytes(data[0:2], "little"), \
        int.from_bytes(data[2:4], "little"), int.from_bytes(data[4:6], "little")
    assert (reserved, kind) == (0, 1)
    assert count >= 3
    for i in range(count):
        size = int.from_bytes(data[6 + 16 * i + 8:6 + 16 * i + 12], "little")
        offset = int.from_bytes(data[6 + 16 * i + 12:6 + 16 * i + 16], "little")
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + size <= len(data)


def test_powershell_script_parses():
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe or not sys.platform.startswith("win"):
        pytest.skip("PowerShell parser only checked on Windows")
    target = str(INSTALLERS / "windows" / "install.ps1")
    cmd = ("$e=$null;$t=$null;"
           "[void][System.Management.Automation.Language.Parser]::ParseFile("
           "'%s',[ref]$t,[ref]$e);"
           "if($e.Count){$e|%%{$_.Message+' line '+$_.Extent.StartLineNumber};exit 1}" % target)
    r = subprocess.run([exe, "-NoProfile", "-Command", cmd], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("rel", ["linux/install.sh", "macos/install.command",
                                 "macos/OpenMuscle.command"])
def test_shell_script_syntax(rel):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    r = subprocess.run([bash, "-n", str(INSTALLERS / rel)], capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("rel", ["linux/install.sh", "macos/install.command",
                                 "macos/OpenMuscle.command"])
def test_shell_script_shellcheck_clean(rel):
    sc = shutil.which("shellcheck")
    if not sc:
        pytest.skip("shellcheck not installed")
    r = subprocess.run([sc, "-s", "bash", str(INSTALLERS / rel)], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="cmd.exe only")
def test_windows_launcher_explains_a_missing_install(tmp_path):
    """Run from a download folder with nothing installed: a plain message, exit 1."""
    env = dict(os.environ, OPENMUSCLE_HOME=str(tmp_path / "nothing here"))
    r = subprocess.run(["cmd.exe", "/c", str(INSTALLERS / "windows" / "OpenMuscle.bat")],
                       capture_output=True, text=True, env=env, timeout=60,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 1
    assert "not installed" in r.stdout
    assert "Install-OpenMuscle.bat" in r.stdout
