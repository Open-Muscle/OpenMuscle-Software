"""The CI smoke script (scripts/ci_smoke.py) and the things it guards.

The script is what .github/workflows/ci.yml runs on Windows, macOS and Linux, so
its decisions are tested here: what counts as a bad launcher, a short wheel or an
unreadable capture, and that a whole run against the source tree passes and a
broken run is reported red. The last group starts real servers on throwaway ports.
"""

import glob
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PC = Path(__file__).resolve().parents[1]
REPO = PC.parent
SCRIPT = REPO / "scripts" / "ci_smoke.py"
STATIC = PC / "src" / "openmuscle" / "web" / "static"

pytestmark = pytest.mark.skipif(not SCRIPT.is_file(),
                                reason="scripts/ci_smoke.py is not in this checkout")


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("ci_smoke", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ci_smoke"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_script(*args, timeout=240, env_extra=None):
    env = dict(os.environ, PYTHONPATH=str(PC / "src"), PYTHONIOENCODING="utf-8")
    env.pop("OPENMUSCLE_DRY_RUN", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, timeout=timeout,
                          capture_output=True, text=True, encoding="utf-8")


# ---- launcher line endings -----------------------------------------------------

def test_batch_files_need_crlf(smoke):
    assert smoke.check_line_endings("a/run.bat", b"@echo off\r\necho hi\r\n") == []
    bad = smoke.check_line_endings("a/run.bat", b"@echo off\necho hi\n")
    assert bad and "CRLF" in bad[0] and ".gitattributes" in bad[0]
    assert smoke.check_line_endings("a/run.cmd", b"@echo off\r\necho hi\n")   # mixed


def test_shell_files_need_lf_and_a_shebang(smoke):
    assert smoke.check_line_endings("a/run.sh", b"#!/bin/bash\necho hi\n") == []
    assert smoke.check_line_endings("a/x.command", b"#!/bin/bash\necho hi\n") == []
    crlf = smoke.check_line_endings("a/run.sh", b"#!/bin/bash\r\necho hi\r\n")
    assert crlf and "LF only" in crlf[0]
    noshebang = smoke.check_line_endings("a/run.command", b"echo hi\n")
    assert noshebang and "#!" in noshebang[0]
    # A .desktop entry is not a script: LF, but no shebang.
    assert smoke.check_line_endings("a/om.desktop", b"[Desktop Entry]\nName=x\n") == []
    assert smoke.check_line_endings("a/om.desktop", b"[Desktop Entry]\r\nName=x\r\n")


def test_byte_order_mark_is_flagged(smoke):
    assert smoke.check_line_endings("a/run.sh", b"\xef\xbb\xbf#!/bin/bash\n")
    assert smoke.check_line_endings("a/run.bat", b"\xef\xbb\xbf@echo off\r\n")


def test_gitattributes_pins_launcher_line_endings():
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    rules = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.lstrip().startswith("#"):
            rules[parts[0]] = parts[1:]
    for pat in ("*.bat", "*.cmd"):
        # Either convert on checkout (eol=crlf) or store the CRLF bytes as they
        # are (-text); the files step checks the bytes themselves either way.
        assert "eol=crlf" in rules[pat] or "-text" in rules[pat], pat
    for pat in ("*.sh", "*.command", "*.desktop"):
        assert "eol=lf" in rules[pat], pat


# ---- package data ----------------------------------------------------------------

def _package_data_patterns():
    text = (PC / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'"openmuscle\.web"\s*=\s*\[([^\]]*)\]', text)
    assert m, "no package-data entry for openmuscle.web in pc/pyproject.toml"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_package_data_globs_cover_every_browser_file(smoke):
    web = PC / "src" / "openmuscle" / "web"
    covered = set()
    for pat in _package_data_patterns():
        for hit in glob.glob(pat, root_dir=str(web), recursive=True):
            # The patterns also ship help_docs/ (built by setup.py, absent in a
            # fresh clone); only the static tree is compared here.
            if (web / hit).is_file() and Path(hit).parts[0] == "static":
                covered.add(Path(hit).relative_to("static").as_posix())
    missing = [f for f in smoke.source_static_files() if f not in covered]
    assert not missing, "pyproject package-data would leave out: %s" % missing[:5]
    # The folders the kit relies on really exist in the source tree.
    subs = {f.split("/")[0] for f in smoke.source_static_files() if "/" in f}
    assert {"vr", "webcam", "setup"} <= subs


def test_a_top_level_only_glob_would_be_caught(smoke):
    """The old pattern ("static/*") ships index.html but not vr/, webcam/, setup/."""
    web = PC / "src" / "openmuscle" / "web"
    shipped = {Path(h).relative_to("static").as_posix()
               for h in glob.glob("static/*", root_dir=str(web), recursive=True)
               if (web / h).is_file()}
    names = ["openmuscle/web/static/" + f for f in shipped]
    missing = smoke.wheel_missing_static(names, smoke.source_static_files())
    assert any(m.startswith("vr/") for m in missing)
    assert any(m.startswith("webcam/") for m in missing)
    assert any(m.startswith("setup/") for m in missing)


def test_wheel_and_installed_comparison(smoke, tmp_path):
    files = smoke.source_static_files()
    assert "index.html" in files
    names = ["openmuscle/web/static/" + f for f in files]
    assert smoke.wheel_missing_static(names, files) == []
    assert smoke.wheel_missing_static(names[:-1], files) == [files[-1]]
    for rel in files:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("x", encoding="utf-8")
    assert smoke.installed_missing_static(tmp_path, files) == []
    (tmp_path / "vr" / "app.js").unlink()
    assert smoke.installed_missing_static(tmp_path, files) == ["vr/app.js"]


# ---- the recorded CSV --------------------------------------------------------------

def _csv(tmp_path, header, rows):
    p = tmp_path / "t.csv"
    p.write_text("\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows])
                 + "\n", encoding="utf-8")
    return p


def test_capture_csv_validation(smoke, tmp_path):
    cells = ["R%dC%d" % (r, c) for r in range(2) for c in range(3)]
    header = ["timestamp"] + cells + ["label_0", "label_1"]
    row = [1.5] + [10] * 6 + [0.1, 0.2]
    assert smoke.validate_capture_csv(_csv(tmp_path, header, [row] * 6), 2, 3) == []
    # Too few rows.
    assert smoke.validate_capture_csv(_csv(tmp_path, header, [row] * 2), 2, 3)
    # A sensor column missing.
    short = [h for h in header if h != "R1C2"]
    out = smoke.validate_capture_csv(_csv(tmp_path, short, [row[:-3] + row[-2:]] * 6), 2, 3)
    assert any("R1C2" in m for m in out)
    # No labels, no time column.
    nolabel = smoke.validate_capture_csv(_csv(tmp_path, ["timestamp"] + cells,
                                              [[1] + [2] * 6] * 6), 2, 3)
    assert any("label_" in m for m in nolabel)
    notime = smoke.validate_capture_csv(_csv(tmp_path, cells + ["label_0"],
                                             [[2] * 6 + [1]] * 6), 2, 3)
    assert any("time column" in m for m in notime)
    # A non-numeric sensor cell.
    bad = [1.5] + ["x"] * 6 + [0.1, 0.2]
    assert any("non-numeric" in m for m in
               smoke.validate_capture_csv(_csv(tmp_path, header, [bad] * 6), 2, 3))
    # Empty file.
    empty = tmp_path / "e.csv"
    empty.write_text("", encoding="utf-8")
    assert smoke.validate_capture_csv(empty, 2, 3) == ["the CSV is empty"]


def test_capture_csv_with_a_non_ascii_header_reads_back(smoke, tmp_path):
    """Captures are read as UTF-8 everywhere, whatever the system code page is."""
    header = ["timestamp", "R0C0", "label_0", "note_é"]
    p = tmp_path / "u.csv"
    p.write_text(",".join(header) + "\n" + "\n".join("1.0,2,0.5,ü" for _ in range(6))
                 + "\n", encoding="utf-8")
    assert smoke.validate_capture_csv(p, 1, 1) == []


# ---- the steps, pointed at a made-up checkout ----------------------------------------

def _fake_repo(tmp_path, files):
    for rel, data in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return tmp_path


def _steps(smoke, repo, only, *extra):
    args = smoke.parse_args(["--repo", str(repo), "--only", only, *extra])
    s = smoke.Smoke(args)
    code = s.run()
    return code, {name: (status, detail) for status, name, detail in s.results}


def test_files_step_goes_red_on_a_bad_launcher(smoke, tmp_path):
    repo = _fake_repo(tmp_path, {
        "installers/windows/OpenMuscle.bat": b"@echo off\necho hi\n",       # LF: wrong
        "installers/linux/run.sh": b"#!/bin/bash\r\necho hi\r\n",           # CRLF: wrong
    })
    code, res = _steps(smoke, repo, "files")
    assert code == 1
    status, detail = res["files"]
    assert status == "FAIL"
    assert "OpenMuscle.bat" in detail and "run.sh" in detail


def test_files_step_passes_good_launchers_and_skips_when_none(smoke, tmp_path):
    good = _fake_repo(tmp_path / "good", {
        "installers/windows/OpenMuscle.bat": b"@echo off\r\necho hi\r\n",
        "installers/macos/OpenMuscle.command": b"#!/bin/bash\necho hi\n",
        "installers/linux/openmuscle.desktop": b"[Desktop Entry]\nName=x\n",
    })
    code, res = _steps(smoke, good, "files")
    assert code == 0 and res["files"][0] == "PASS"
    empty = tmp_path / "empty"
    empty.mkdir()
    code, res = _steps(smoke, empty, "files")
    assert code == 0 and res["files"][0] == "SKIP"


def _dry_run_launcher(ok):
    """A launcher for this OS that succeeds only when OPENMUSCLE_DRY_RUN=1."""
    if sys.platform.startswith("win"):
        body = ('@echo off\r\nif "%OPENMUSCLE_DRY_RUN%"=="1" (exit /b {rc})\r\n'
                'exit /b 9\r\n').format(rc=0 if ok else 3)
        return "run-openmuscle.bat", body.encode("ascii")
    body = ('#!/bin/bash\nif [ "$OPENMUSCLE_DRY_RUN" = "1" ]; then exit {rc}; fi\n'
            'exit 9\n').format(rc=0 if ok else 3)
    return "run-openmuscle.sh", body.encode("ascii")


@pytest.mark.skipif(not sys.platform.startswith("win") and not __import__("shutil").which("bash"),
                    reason="bash is needed to run a shell launcher")
def test_launchers_step_runs_each_launcher_in_dry_run(smoke, tmp_path):
    name, body = _dry_run_launcher(ok=True)
    repo = _fake_repo(tmp_path / "ok", {name: body})
    code, res = _steps(smoke, repo, "launchers")
    assert code == 0 and res["launchers"][0] == "PASS", res
    name, body = _dry_run_launcher(ok=False)
    repo = _fake_repo(tmp_path / "bad", {name: body})
    code, res = _steps(smoke, repo, "launchers")
    assert code == 1 and res["launchers"][0] == "FAIL"
    assert "exit code 3" in res["launchers"][1]


# ---- whole runs (real servers on throwaway ports) ------------------------------------

def test_full_smoke_against_the_source_tree_is_green(tmp_path):
    work = tmp_path / "work"
    res = run_script("--work-dir", str(work), "--sim-seconds", "20")
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    for step in ("package", "web", "simulate", "recording", "second", "stop", "doctor",
                 "assets"):
        assert re.search(r"^PASS\s+%s\s" % step, out, re.M), "%s did not pass:\n%s" % (step, out)
    assert "0 failed" in out
    # The recording really is on disk, in the throwaway data folder.
    assert list((work / "home" / "captures").glob("ci-smoke*.csv"))


def test_installed_flag_refuses_the_source_tree():
    res = run_script("--installed", "--only", "package")
    out = res.stdout + res.stderr
    assert res.returncode == 1, out
    assert "FAIL" in out and "source tree" in out


def test_smoke_goes_red_when_the_second_window_check_cannot_be_met(tmp_path):
    """An impossible time limit proves the second-instance step really asserts."""
    res = run_script("--work-dir", str(tmp_path / "w"), "--sim-seconds", "20",
                     "--second-limit", "0.001")
    out = res.stdout + res.stderr
    assert res.returncode == 1, out
    assert re.search(r"^FAIL\s+second\s", out, re.M), out
    assert re.search(r"^PASS\s+recording\s", out, re.M), out
