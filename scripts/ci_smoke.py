#!/usr/bin/env python3
"""Install and data-flow smoke test for OpenMuscle (used by .github/workflows/ci.yml).

Runs the same steps on Windows, macOS and Linux and prints one PASS / FAIL / SKIP
line per step. Exit code 0 when nothing failed, 1 otherwise. Standard library
only, so it also works before anything else is installed.

What it proves, in order:

  files      launcher scripts have the right line endings, a shebang and (in git)
             the executable bit; a .bat saved with LF line endings breaks on
             Windows and a .sh saved with CRLF breaks on macOS and Linux.
  launchers  every launcher found for this OS runs to the end with
             OPENMUSCLE_DRY_RUN=1 (no install, no server).
  package    the installed copy of OpenMuscle contains every browser file the
             source tree has, including the vr/, webcam/ and setup/ subfolders
             (a package-data glob that only matches top-level files ships a web
             page with broken sub-pages). With --wheel the wheel is checked too.
  web        `openmuscle web --no-browser` starts, answers /api/mode and serves
             the pages and sub-folder files from the installed package.
  simulate   `openmuscle simulate --device-type rehearsal` joins it; /api/devices
             lists both bands and the labeler with frames flowing.
  recording  a short recording through the API leaves a readable CSV in the
             data folder with the expected columns and rows.
  second     a second `openmuscle web` on the same ports exits within 3 seconds
             with the one-sentence "already running" message.
  stop       the server stops and releases its ports.
  doctor     `openmuscle doctor` and `openmuscle assets status` run.

Typical use from a clone, after `pip install ./pc`:

    python scripts/ci_smoke.py --installed

Everything is written under a throwaway data folder (OPENMUSCLE_HOME); your own
~/OpenMuscle is never touched.
"""

import argparse
import csv
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_PKG = REPO / "pc" / "src" / "openmuscle"
SRC_STATIC = SRC_PKG / "web" / "static"

# Seconds a second `openmuscle web` may take to refuse politely.
SECOND_INSTANCE_LIMIT_S = 3.0
IS_WINDOWS = sys.platform.startswith("win")

# Doctor results that mean this computer or install is broken. Everything else
# (firewall, Wi-Fi, internet, bands) depends on the machine the job runs on.
DOCTOR_REQUIRED = {"python", "packages", "static_files", "data_folder"}

CRLF_SUFFIXES = {".bat", ".cmd"}
LF_SUFFIXES = {".sh", ".command", ".desktop"}
SHEBANG_SUFFIXES = {".sh", ".command"}
EXEC_SUFFIXES = {".sh", ".command"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "build", "dist", "__pycache__",
             "wheelhouse", "OpenMuscle"}


class SmokeFailure(AssertionError):
    """A step's assertion failed; the message is the plain reason."""


class Skip(Exception):
    """A step that does not apply here (reason in the message)."""


# ---------------------------------------------------------------- small helpers

def need(cond, message):
    if not cond:
        raise SmokeFailure(message)


def free_tcp_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_get(port, path, timeout=5.0):
    """(status, body bytes) for GET http://127.0.0.1:port/path; HTTP errors return
    their status, connection errors raise."""
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_json(port, path, body=None, method=None, timeout=10.0):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raise SmokeFailure("%s %s answered HTTP %d: %s" % (
            req.get_method(), path, e.code, e.read()[:300].decode("utf-8", "replace")))


def wait_until(fn, timeout, interval=0.3):
    """Poll fn() until it returns a truthy value; return it, or None on timeout."""
    deadline = time.time() + timeout
    while True:
        try:
            value = fn()
        except (OSError, urllib.error.URLError, ValueError):
            value = None
        if value:
            return value
        if time.time() >= deadline:
            return None
        time.sleep(interval)


def kill_tree(proc):
    """Stop a child and anything it started; never raises."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


def spawn(args, env, log_path):
    """Start a long-running child with its output going to a file (a full pipe
    would stall it). On POSIX it gets its own process group so kill_tree works."""
    log = open(log_path, "wb")
    kwargs = {}
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **kwargs)
    proc._smoke_log = log          # closed by tail()/the caller's cleanup
    proc._smoke_log_path = log_path
    return proc


def tail(proc, lines=25):
    path = getattr(proc, "_smoke_log_path", None)
    if not path or not Path(path).exists():
        return ""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


# ---------------------------------------------------------------- pure checks
# These take plain data so tests can feed them fabricated input.

def check_line_endings(name, data):
    """Return a list of problems for one launcher file's bytes (empty = fine).

    .bat / .cmd must use CRLF on every line; .sh / .command / .desktop must have
    no CR at all and the first two must start with a #! line.
    """
    suffix = Path(name).suffix.lower()
    problems = []
    if suffix in CRLF_SUFFIXES:
        lone_lf = data.count(b"\n") - data.count(b"\r\n")
        lone_cr = data.count(b"\r") - data.count(b"\r\n")
        if lone_lf:
            problems.append("%s: %d line(s) end in LF only; a Windows batch file needs "
                            "CRLF (check .gitattributes has 'eol=crlf' for it)"
                            % (name, lone_lf))
        if lone_cr:
            problems.append("%s: %d stray carriage return(s)" % (name, lone_cr))
    elif suffix in LF_SUFFIXES:
        if b"\r" in data:
            problems.append("%s: has CRLF line endings; macOS and Linux need LF only "
                            "(check .gitattributes has 'eol=lf' for it)" % name)
        if suffix in SHEBANG_SUFFIXES and not data.startswith(b"#!"):
            problems.append("%s: first line is not a #! interpreter line" % name)
    if data.startswith(b"\xef\xbb\xbf") and suffix in LF_SUFFIXES | CRLF_SUFFIXES:
        problems.append("%s: starts with a byte-order mark, which breaks the first line"
                        % name)
    return problems


def wheel_missing_static(wheel_names, source_files):
    """Source browser files (relative to openmuscle/web/static, '/' separators)
    that the wheel's member list does not contain."""
    have = set(wheel_names)
    return sorted(rel for rel in source_files
                  if "openmuscle/web/static/" + rel not in have)


def installed_missing_static(installed_dir, source_files):
    root = Path(installed_dir)
    return sorted(rel for rel in source_files if not (root / rel).is_file())


def source_static_files(static_dir=SRC_STATIC):
    """Every file under the source static folder, relative, '/' separators."""
    out = []
    for p in sorted(Path(static_dir).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out.append(p.relative_to(static_dir).as_posix())
    return out


def validate_capture_csv(path, rows, cols, min_rows=5):
    """Plain-language problems with a recorded CSV (empty list = fine).

    The header must carry a time column, every R{r}C{c} sensor cell and at least
    one label column; there must be at least min_rows data rows and every sensor
    cell of the first and last row must be a number.
    """
    problems = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return ["the CSV is empty"]
        body = [r for r in reader if r]
    cells = ["R%dC%d" % (r, c) for r in range(rows) for c in range(cols)]
    missing = [c for c in cells if c not in header]
    if missing:
        problems.append("header lacks sensor columns such as %s" % ", ".join(missing[:3]))
    if not any(h in header for h in ("timestamp", "ts_hub_ms")):
        problems.append("header has no time column (timestamp or ts_hub_ms)")
    if not any(h.startswith("label_") for h in header):
        problems.append("header has no label_* columns")
    if len(body) < min_rows:
        problems.append("only %d data row(s); expected at least %d" % (len(body), min_rows))
    if not missing and body:
        idx = [header.index(c) for c in cells]
        for which, row in (("first", body[0]), ("last", body[-1])):
            try:
                [float(row[i]) for i in idx]
            except (ValueError, IndexError):
                problems.append("the %s data row has non-numeric or missing sensor cells"
                                % which)
    return problems


# ---------------------------------------------------------------- steps

class Smoke:
    def __init__(self, args):
        self.args = args
        self.results = []                 # (status, name, detail)
        self.work = Path(args.work_dir) if args.work_dir else Path(
            tempfile.mkdtemp(prefix="om-smoke-"))
        self.work.mkdir(parents=True, exist_ok=True)
        self.home = self.work / "home"
        self.http = args.port or free_tcp_port()
        self.udp = args.udp_port or free_udp_port()
        self.announce = args.announce_port or free_udp_port()
        self.env = dict(os.environ, OPENMUSCLE_HOME=str(self.home),
                        PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self.env.pop("OPENMUSCLE_DRY_RUN", None)
        self.web = None
        self.sim = None
        self.pkg_static = None
        self.repo = Path(args.repo).resolve()

    # --- plumbing

    def step(self, name, fn):
        t0 = time.time()
        try:
            detail = fn() or ""
            status = "PASS"
        except Skip as e:
            status, detail = "SKIP", str(e)
        except SmokeFailure as e:
            status, detail = "FAIL", str(e)
        except Exception as e:                       # a crash is a failure, with its type
            status, detail = "FAIL", "%s: %s" % (type(e).__name__, e)
        self.results.append((status, name, detail))
        print("%-5s %-10s %s  (%.1fs)" % (status, name, detail.splitlines()[0] if detail else "",
                                          time.time() - t0), flush=True)
        for extra in detail.splitlines()[1:]:
            print("                  " + extra, flush=True)
        return status

    def cli(self, *argv):
        return [sys.executable, "-m", "openmuscle", *argv]

    def web_args(self, port=None):
        return self.cli("web", "--host", "127.0.0.1", "--no-browser",
                        "--port", str(port or self.http),
                        "--udp-port", str(self.udp),
                        "--announce-port", str(self.announce))

    # --- files and launchers

    def launcher_files(self):
        """Tracked (or, outside git, found) launcher-type files, as repo-relative
        posix paths."""
        suffixes = tuple(CRLF_SUFFIXES | LF_SUFFIXES)
        try:
            out = subprocess.run(["git", "ls-files", "-z"], cwd=self.repo, capture_output=True,
                                 check=True, timeout=30).stdout
            names = [n for n in out.decode("utf-8", "replace").split("\0") if n]
            found = [n for n in names if n.lower().endswith(suffixes)
                     and (self.repo / n).is_file()]
            if found or names:
                return sorted(found)
        except (OSError, subprocess.SubprocessError):
            pass
        found = []
        for dirpath, dirnames, filenames in os.walk(self.repo):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.lower().endswith(suffixes):
                    found.append(Path(dirpath, fn).relative_to(self.repo).as_posix())
        return sorted(found)

    def git_modes(self, files):
        """{path: mode string} from the git index, or {} when git cannot say."""
        if not files:
            return {}
        try:
            out = subprocess.run(["git", "ls-files", "-s", "-z", "--", *files], cwd=self.repo,
                                 capture_output=True, check=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        modes = {}
        for rec in out.decode("utf-8", "replace").split("\0"):
            if rec.strip():
                meta, _, path = rec.partition("\t")
                modes[path] = meta.split()[0]
        return modes

    def step_files(self):
        files = self.launcher_files()
        if not files:
            raise Skip("no launcher scripts in this checkout yet")
        problems = []
        for rel in files:
            problems += check_line_endings(rel, (self.repo / rel).read_bytes())
        modes = self.git_modes([f for f in files if Path(f).suffix.lower() in EXEC_SUFFIXES])
        for rel, mode in sorted(modes.items()):
            if mode != "100755":
                problems.append("%s: not executable in git (mode %s); run "
                                "'git update-index --chmod=+x %s'" % (rel, mode, rel))
        need(not problems, "\n".join(problems))
        return "%d launcher file(s) have correct line endings" % len(files)

    def dry_run_candidates(self):
        """Launchers that make sense to run on this OS."""
        files = self.launcher_files()
        out = []
        for rel in files:
            low = rel.lower()
            base = Path(low).name
            is_launcher = (base.startswith("run-openmuscle")
                           or (low.startswith("installers/") and base.startswith("openmuscle.")
                               and not base.endswith(".desktop")))
            if not is_launcher:
                continue
            suffix = Path(low).suffix
            if suffix in CRLF_SUFFIXES and IS_WINDOWS:
                out.append(rel)
            elif suffix in SHEBANG_SUFFIXES and not IS_WINDOWS:
                out.append(rel)
        return out

    def step_launchers(self):
        found = self.dry_run_candidates()
        if not found:
            raise Skip("no launcher for this OS in the checkout yet")
        env = dict(self.env, OPENMUSCLE_DRY_RUN="1")
        bad = []
        for rel in found:
            path = self.repo / rel
            if path.suffix.lower() in CRLF_SUFFIXES:
                argv = ["cmd", "/c", str(path)]
            else:
                argv = ["bash", str(path)]
            try:
                res = subprocess.run(argv, env=env, cwd=path.parent, timeout=90,
                                     stdin=subprocess.DEVNULL, capture_output=True)
            except subprocess.TimeoutExpired:
                bad.append("%s: still running after 90 s with OPENMUSCLE_DRY_RUN=1" % rel)
                continue
            if res.returncode != 0:
                out = (res.stdout + res.stderr).decode("utf-8", "replace").strip()
                bad.append("%s: exit code %d\n%s" % (rel, res.returncode, out[-400:]))
        need(not bad, "\n".join(bad))
        return "%d launcher(s) dry-run clean: %s" % (len(found), ", ".join(found))

    # --- package contents

    def step_package(self):
        import importlib.util
        spec = importlib.util.find_spec("openmuscle")
        need(spec and spec.origin, "openmuscle is not installed in this Python")
        pkg_dir = Path(spec.origin).resolve().parent
        self.pkg_static = pkg_dir / "web" / "static"
        if self.args.installed:
            try:
                pkg_dir.relative_to(SRC_PKG.parent.resolve())
                inside = True
            except ValueError:
                inside = False
            need(not inside, "openmuscle is being imported from the source tree (%s), "
                 "not from an installed copy; install it with 'pip install ./pc' "
                 "(not -e) so the packaged files are what gets tested" % pkg_dir)
        source = source_static_files()
        need(source, "found no browser files in the source tree (%s)" % SRC_STATIC)
        missing = installed_missing_static(self.pkg_static, source)
        need(not missing, "the installed package lacks %d browser file(s), e.g. %s "
             "(package-data in pc/pyproject.toml must include sub-folders)"
             % (len(missing), ", ".join(missing[:4])))
        subs = sorted({r.split("/")[0] for r in source if "/" in r})
        return "%d browser files incl. folders %s are installed" % (len(source), ", ".join(subs))

    def resolve_wheel(self):
        target = self.args.wheel
        if self.args.build_wheel:
            out = self.work / "wheel"
            out.mkdir(exist_ok=True)
            res = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                                  "--no-input", "-w", str(out), str(REPO / "pc")],
                                 capture_output=True, text=True, timeout=600)
            need(res.returncode == 0, "building the wheel failed:\n" + res.stderr[-600:])
            target = str(out)
        need(target, "no wheel given")
        p = Path(target)
        if p.is_dir():
            wheels = sorted(p.glob("openmuscle-*.whl"))
            need(wheels, "no openmuscle-*.whl in %s" % p)
            return wheels[-1]
        need(p.is_file(), "wheel not found: %s" % p)
        return p

    def step_wheel(self):
        if not (self.args.wheel or self.args.build_wheel):
            raise Skip("no --wheel given")
        wheel = self.resolve_wheel()
        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
        source = source_static_files()
        missing = wheel_missing_static(names, source)
        need(not missing, "%s lacks %d browser file(s), e.g. %s (package-data in "
             "pc/pyproject.toml must include sub-folders)"
             % (wheel.name, len(missing), ", ".join(missing[:4])))
        return "%s contains all %d browser files" % (wheel.name, len(source))

    # --- running server

    def step_version(self):
        res = subprocess.run(self.cli("--version"), env=self.env, capture_output=True,
                             text=True, timeout=60)
        need(res.returncode == 0, "'openmuscle --version' failed: " + res.stderr[-300:])
        exe = shutil.which("openmuscle")
        if self.args.installed:
            need(exe, "the 'openmuscle' command is not on PATH after install")
            res2 = subprocess.run([exe, "--version"], env=self.env, capture_output=True,
                                  text=True, timeout=60)
            need(res2.returncode == 0, "the 'openmuscle' command failed: " + res2.stderr[-300:])
        return res.stdout.strip()

    def step_web(self):
        self.web = spawn(self.web_args(), self.env, self.work / "web.log")
        mode = wait_until(lambda: self._mode(), self.args.start_timeout)
        need(mode, "the server did not answer /api/mode within %ds\n%s"
             % (self.args.start_timeout, tail(self.web)))
        need(mode.get("app") == "openmuscle", "/api/mode did not say app=openmuscle: %r" % mode)
        # Pages and sub-folder files come from the installed package.
        pages = ["/", "/webcam", "/setup", "/vr"]
        for page in pages:
            status, body = http_get(self.http, page)
            need(status == 200 and body, "GET %s answered HTTP %d" % (page, status))
        shown = []
        for sub in sorted({r.split("/")[0] for r in source_static_files() if "/" in r}):
            rel = next((r for r in source_static_files() if r.startswith(sub + "/")), None)
            if not rel:
                continue
            status, body = http_get(self.http, "/static/" + rel)
            need(status == 200 and body,
                 "GET /static/%s answered HTTP %d (sub-folder files are not served)"
                 % (rel, status))
            shown.append(rel)
        return "up on port %d (version %s); served %s" % (
            self.http, mode.get("version"), ", ".join(shown))

    def _mode(self):
        if self.web.poll() is not None:
            raise SmokeFailure("the server exited early (code %s)\n%s"
                               % (self.web.returncode, tail(self.web)))
        status, body = http_get(self.http, "/api/mode", timeout=2.0)
        return json.loads(body) if status == 200 else None

    def step_simulate(self):
        need(self.web is not None and self.web.poll() is None, "the server is not running")
        self.sim = spawn(
            self.cli("simulate", "--device-type", "rehearsal", "--bands", "2",
                     "--port", str(self.udp), "--announce-port", str(self.announce),
                     "--web-port", str(self.http), "--web-scheme", "http",
                     "--duration", str(self.args.sim_seconds)),
            self.env, self.work / "simulate.log")

        def ready():
            if self.sim.poll() is not None:
                raise SmokeFailure("the simulator exited early (code %s)\n%s"
                                   % (self.sim.returncode, tail(self.sim)))
            devs = http_json(self.http, "/api/devices")
            flex = [d for d in devs if d.get("device_type") == "flexgrid"
                    and d.get("packets", 0) > 0]
            lask = [d for d in devs if d.get("device_type") == "lask5"
                    and d.get("packets", 0) > 0]
            tagged = {d.get("role") for d in flex} >= {"left", "right"}
            return (devs, flex, lask) if len(flex) >= 2 and lask and tagged else None

        got = wait_until(ready, self.args.device_timeout, interval=0.5)
        if not got:
            devs = http_json(self.http, "/api/devices")
            summary = [(d.get("device_id"), d.get("device_type"), d.get("packets"),
                        d.get("role")) for d in devs]
            raise SmokeFailure(
                "expected 2 flexgrid bands (tagged left and right) and a lask5 labeler "
                "with frames within %ds; /api/devices has %r\n--- web ---\n%s\n--- sim ---\n%s"
                % (self.args.device_timeout, summary, tail(self.web, 10), tail(self.sim, 10)))
        devs, flex, lask = got
        return "bands %s and labeler %s are streaming" % (
            ", ".join(sorted(d["device_id"] for d in flex)), lask[0]["device_id"])

    def step_recording(self):
        start = http_json(self.http, "/api/recording",
                          {"filename": "ci-smoke.csv", "window_ms": 100})
        name = start["filename"]
        rows, cols = start["shape"]

        def has_rows():
            st = http_json(self.http, "/api/recording")
            return st.get("recording") and st.get("rows", 0) >= 10

        grew = wait_until(has_rows, 30, interval=0.5)
        stop = http_json(self.http, "/api/recording", method="DELETE")
        need(grew, "the recording gathered no rows in 30 s (frames not matching labels?)\n"
             + tail(self.web, 10))
        names = [c["name"] for c in http_json(self.http, "/api/captures")]
        need(name in names, "/api/captures does not list %s: %r" % (name, names))
        csv_path = self.home / "captures" / name
        need(csv_path.is_file(), "the data folder has no %s (looked in %s)"
             % (name, csv_path.parent))
        problems = validate_capture_csv(csv_path, rows, cols)
        need(not problems, "%s is not a usable capture: %s" % (name, "; ".join(problems)))
        status, body = http_get(self.http, "/api/captures/%s/download" % name)
        need(status == 200 and body == csv_path.read_bytes(),
             "downloading %s through the API did not return the file on disk" % name)
        return "%s: %d sensor cells, %d rows saved (%s)" % (
            name, rows * cols, stop.get("rows", 0),
            (stop.get("quality") or {}).get("verdict", "no verdict"))

    def step_second(self):
        need(self.web is not None and self.web.poll() is None, "the server is not running")
        limit = self.args.second_limit
        took = res = None
        for attempt in (1, 2):               # one retry: a cold machine can be slow once
            t0 = time.time()
            res = subprocess.run(self.web_args(), env=self.env, timeout=60,
                                 stdin=subprocess.DEVNULL, capture_output=True)
            took = time.time() - t0
            if took <= limit:
                break
        out = (res.stdout + res.stderr).decode("utf-8", "replace").strip()
        need(res.returncode != 0, "a second instance on the same ports exited 0 "
             "instead of refusing\n" + out[-300:])
        need(took <= limit, "the second instance took %.1fs to exit (limit %.1fs)"
             % (took, limit))
        want = "OpenMuscle is already running at http://localhost:%d" % self.http
        need(want in out, "the second instance did not say %r; it said:\n%s" % (want, out[-300:]))
        need("Traceback" not in out, "the second instance printed a traceback:\n" + out[-400:])
        need(self.web.poll() is None, "the first server died when the second one started")
        return "refused in %.1fs: %s" % (took, out.splitlines()[-1] if out else "")

    def step_stop(self):
        # The simulator is only needed while the server runs; stop both, then
        # prove the ports come back (no orphan holding them).
        kill_tree(self.sim)
        kill_tree(self.web)
        net = self._net()
        released = wait_until(lambda: net.tcp_port_free("127.0.0.1", self.http)
                              and net.udp_bind_check(self.udp)[0], 15, interval=0.3)
        need(released, "the server's ports were still held 15 s after it was stopped")
        return "ports %d and %d released" % (self.http, self.udp)

    def _net(self):
        from openmuscle import netutil
        return netutil

    def step_doctor(self):
        res = subprocess.run(self.cli("doctor", "--no-discover", "--port", str(self.http),
                                      "--udp-port", str(self.udp),
                                      "--announce-port", str(self.announce)),
                             env=self.env, capture_output=True, timeout=120)
        out = res.stdout.decode("utf-8", "replace")
        need(res.returncode in (0, 1) and "OpenMuscle doctor" in out and "Traceback" not in out,
             "'openmuscle doctor' crashed (exit %d):\n%s%s"
             % (res.returncode, out[-300:], res.stderr.decode("utf-8", "replace")[-300:]))
        from openmuscle import doctor as dr
        result = dr.run_doctor(http_port=self.http, udp_port=self.udp,
                               announce_port=self.announce, discover=False, redact=True)
        broken = [c for c in result["checks"]
                  if c["id"] in DOCTOR_REQUIRED and c["status"] == dr.FAIL]
        need(not broken, "doctor reports a broken install: " + "; ".join(
            "%s: %s" % (c["title"], c["detail"]) for c in broken))
        marks = ", ".join("%s=%s" % (c["id"], c["status"]) for c in result["checks"]
                          if c["status"] != dr.PASS)
        fails = [l.strip() for l in out.splitlines() if l.strip().startswith("FAIL")]
        extra = "\nthe command printed: " + "; ".join(fails) if fails else ""
        return "ran (exit %d); not PASS: %s%s" % (res.returncode, marks or "none", extra)

    def step_assets(self):
        res = subprocess.run(self.cli("assets", "status"), env=self.env, capture_output=True,
                             text=True, timeout=60)
        need(res.returncode == 0 and "Offline browser files in" in res.stdout,
             "'openmuscle assets status' failed (exit %d): %s"
             % (res.returncode, (res.stdout + res.stderr)[-300:]))
        ready = [l.strip() for l in res.stdout.splitlines() if l.strip().endswith("ready")]
        return "ran; %s" % ("; ".join(ready) if ready else "nothing fetched (offline mode)")

    # --- run it all

    def selected(self, name):
        return not self.args.only or name in self.args.only.split(",")

    def run(self):
        print("OpenMuscle smoke on %s, Python %s, work folder %s"
              % (sys.platform, sys.version.split()[0], self.work), flush=True)
        print("ports: http %d, udp %d, announce %d" % (self.http, self.udp, self.announce),
              flush=True)
        try:
            for name, fn in (("files", self.step_files),
                             ("launchers", self.step_launchers),
                             ("package", self.step_package),
                             ("wheel", self.step_wheel),
                             ("version", self.step_version)):
                if self.selected(name):
                    self.step(name, fn)
            if self.selected("web") and self.step("web", self.step_web) == "PASS":
                self.step("simulate", self.step_simulate)
                if self.results[-1][0] == "PASS":
                    self.step("recording", self.step_recording)
                self.step("second", self.step_second)
            elif self.selected("web"):
                for name in ("simulate", "recording", "second"):
                    self.results.append(("SKIP", name, "the server did not start"))
            for name, fn in (("stop", self.step_stop),
                             ("doctor", self.step_doctor),
                             ("assets", self.step_assets)):
                if self.selected(name):
                    self.step(name, fn)
        finally:
            kill_tree(self.sim)
            kill_tree(self.web)
            for proc in (self.sim, self.web):
                log = getattr(proc, "_smoke_log", None)
                if log:
                    log.close()
            if not self.args.keep and not self.args.work_dir:
                shutil.rmtree(self.work, ignore_errors=True)
        failed = [r for r in self.results if r[0] == "FAIL"]
        print("", flush=True)
        print("%d passed, %d failed, %d skipped" % (
            sum(r[0] == "PASS" for r in self.results), len(failed),
            sum(r[0] == "SKIP" for r in self.results)), flush=True)
        return 1 if failed else 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--installed", action="store_true",
                   help="require that openmuscle is imported from an installed copy "
                        "(not the source tree) and that the 'openmuscle' command exists")
    p.add_argument("--wheel", default=None,
                   help="a built openmuscle .whl (or a folder holding it) to inspect")
    p.add_argument("--build-wheel", action="store_true",
                   help="build a wheel from pc/ and inspect it (needs pip and a network)")
    p.add_argument("--only", default=None,
                   help="comma-separated step names to run (default: all). Steps: files, "
                        "launchers, package, wheel, version, web (with simulate, recording "
                        "and second), stop, doctor, assets")
    p.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    p.add_argument("--port", type=int, default=0, help="HTTP port (default: a free one)")
    p.add_argument("--udp-port", type=int, default=0, help="UDP data port (default: a free one)")
    p.add_argument("--announce-port", type=int, default=0,
                   help="UDP discovery port (default: a free one)")
    p.add_argument("--work-dir", default=None,
                   help="keep everything in this folder (default: a temporary one)")
    p.add_argument("--keep", action="store_true", help="do not delete the temporary folder")
    p.add_argument("--start-timeout", type=int, default=90,
                   help="seconds to wait for the server to start")
    p.add_argument("--device-timeout", type=int, default=90,
                   help="seconds to wait for the simulated kit to appear")
    p.add_argument("--sim-seconds", type=float, default=45.0,
                   help="how long the simulator runs (simulated seconds)")
    p.add_argument("--second-limit", type=float,
                   default=float(os.environ.get("OPENMUSCLE_SMOKE_SECOND_LIMIT",
                                                SECOND_INSTANCE_LIMIT_S)),
                   help="seconds a second instance may take to refuse (default 3)")
    return p.parse_args(argv)


def main(argv=None):
    return Smoke(parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
