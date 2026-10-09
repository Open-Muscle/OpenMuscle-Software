#!/bin/bash
# OpenMuscle installer for macOS. Double-click this file in Finder.
#
# First time from a downloaded ZIP: macOS may refuse to open it ("cannot be
# opened because it is from an unidentified developer"). Right-click the file,
# choose Open, then Open again; or run  xattr -dr com.apple.quarantine <folder>
# once on the extracted folder. installers/README.md explains both.
#
# What it does (no administrator password, never sudo):
#   1. Finds Python 3.10 or newer and says exactly how to get it if missing.
#   2. Makes a private Python environment in  ~/OpenMuscle/app  and installs
#      OpenMuscle into it (from this download, or from GitHub when run alone).
#   3. Creates the data folder ~/OpenMuscle (captures, sessions, models, ...).
#   4. Downloads the offline browser files when online (continues if offline).
#   5. Puts an "OpenMuscle" launcher on your Desktop.
#   6. Runs  openmuscle doctor  and says what to do about the first-run prompts.
#
# Safe to run again: a second run repairs or upgrades the same install.
# Everything is logged to  ~/OpenMuscle/logs/install.log .
# OPENMUSCLE_HOME moves the data folder. OPENMUSCLE_NO_PAUSE=1 skips the final
# "Press Return" (for scripted runs). OPENMUSCLE_DESKTOP_DIR overrides the Desktop.
#
# NOT YET RUN ON A MAC by the author (written and checked on Windows). Lines
# marked [unverified] depend on macOS-only behaviour. Written for the bash 3.2
# that ships with macOS (no associative arrays, no mapfile, no ${var,,}).

set -u

REPO_URL="https://github.com/Open-Muscle/OpenMuscle-Software"
GITHUB_ZIP="$REPO_URL/archive/refs/heads/main.zip#subdirectory=pc"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${OPENMUSCLE_HOME:-$HOME/OpenMuscle}"
APP="$ROOT/app"
LOGDIR="$ROOT/logs"
LOG="$LOGDIR/install.log"
DESKTOP="${OPENMUSCLE_DESKTOP_DIR:-$HOME/Desktop}"

mkdir -p "$LOGDIR" || {
    echo "OpenMuscle could not create its folder: $ROOT"
    echo "Choose a folder you can write to by setting OPENMUSCLE_HOME, then run this again."
    exit 1
}

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
say() { printf '%s\n' "$*"; log "$*"; }

pause_if_interactive() {
    if [ -z "${OPENMUSCLE_NO_PAUSE:-}" ] && [ -t 0 ]; then
        printf '\nPress Return to close this window. '
        read -r _
    fi
}

stop_install() {
    say ""
    say "Installation stopped: $1"
    shift
    for line in "$@"; do say "  $line"; done
    say ""
    say "A detailed log is saved here:  $LOG"
    pause_if_interactive
    exit 1
}

# Runs a command with a dot every few seconds, logging all of its output.
# Returns the command's exit status.
run_logged() {
    local what pid rc
    what="$1"
    shift
    log "RUN ($what): $*"
    "$@" >> "$LOG" 2>&1 &
    pid=$!
    printf '  '
    while kill -0 "$pid" 2>/dev/null; do
        sleep 2
        printf '.'
    done
    printf '\n'
    wait "$pid"
    rc=$?
    log "EXIT $rc ($what)"
    return "$rc"
}

log "---------------------------------------------------------------"
log "OpenMuscle install started on $(uname -sm), macOS $(sw_vers -productVersion 2>/dev/null)"
log "Data folder: $ROOT"

say ""
say "Installing OpenMuscle"
say "Your recordings and the program will live in:  $ROOT"
say ""

# ---------------------------------------------------------------- Python ----
py_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
    # The python.org installer, then Homebrew (Apple Silicon and Intel), then
    # whatever python3 is on PATH. /usr/bin/python3 is never used: on a Mac
    # without developer tools running it opens an install pop-up, and with them
    # it is older than 3.10.
    for v in 3.13 3.12 3.11 3.10 3.14; do
        for c in "/Library/Frameworks/Python.framework/Versions/$v/bin/python3" \
                 "/opt/homebrew/bin/python$v" "/usr/local/bin/python$v"; do
            if [ -x "$c" ] && py_ok "$c"; then
                echo "$c"
                return 0
            fi
        done
    done
    c="$(command -v python3 2>/dev/null || true)"
    if [ -n "$c" ] && [ "$c" != "/usr/bin/python3" ] && py_ok "$c"; then
        echo "$c"
        return 0
    fi
    return 1
}

say "1/5  Looking for Python 3.10 or newer..."
PY="$(find_python)" || PY=""
if [ -z "$PY" ]; then
    stop_install "Python 3.10 or newer is needed first." \
        "Either of these works (pick one):" \
        "  A. Download the macOS installer from  https://www.python.org/downloads/macos/" \
        "     and open it (click Continue until it finishes)." \
        "  B. If you use Homebrew, type in Terminal:  brew install python@3.12" \
        "Then double-click install.command again." \
        "(The Python that comes with the Mac, or is offered by the developer-tools" \
        "pop-up, is too old or incomplete for OpenMuscle.)"
fi
say "     Using $("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') at $PY"
log "Python: $PY"

# ------------------------------------------------------- virtual environment --
say "2/5  Setting up the private OpenMuscle environment..."
if [ -x "$APP/bin/python" ] && py_ok "$APP/bin/python"; then
    log "Reusing environment"
else
    if [ -f "$APP/pyvenv.cfg" ]; then
        say "     The old environment is broken; rebuilding it."
        rm -rf "${APP:?}/bin" "${APP:?}/lib" "${APP:?}/include" "${APP:?}/pyvenv.cfg"
    fi
    mkdir -p "$APP"
    if ! run_logged "create environment" "$PY" -m venv "$APP" || [ ! -x "$APP/bin/python" ]; then
        stop_install "could not create the Python environment." \
            "Install Python again from  https://www.python.org/downloads/macos/  and re-run."
    fi
fi

# --------------------------------------------------------------- install it --
say "3/5  Installing OpenMuscle and its libraries (a few minutes the first time)..."
export PIP_DISABLE_PIP_VERSION_CHECK=1
PC=""
if [ -f "$SCRIPT_DIR/../../pc/pyproject.toml" ]; then
    PC="$(cd "$SCRIPT_DIR/../../pc" && pwd)"
    log "Installing from: $PC"
else
    PC="$GITHUB_ZIP"
    log "Installing from GitHub: $PC"
fi

run_logged "upgrade pip" "$APP/bin/python" -m pip install --upgrade pip || true
# certifi gives the offline-files download a certificate list on python.org builds.
run_logged "install certifi" "$APP/bin/python" -m pip install --upgrade certifi || true
if run_logged "install openmuscle" "$APP/bin/python" -m pip install --upgrade "$PC"; then
    # Same version number on a re-run would keep the old files; force the refresh.
    run_logged "refresh openmuscle" "$APP/bin/python" -m pip install --no-deps --force-reinstall "$PC"
fi
if [ ! -x "$APP/bin/openmuscle" ]; then
    if grep -qiE 'Failed to establish|Max retries|nodename nor servname|Temporary failure|Network is unreachable|ProxyError|SSLError|No matching distribution|Could not find a version|timed out|ConnectionError' "$LOG"; then
        stop_install "the libraries could not be downloaded." \
            "This computer seems to be offline, or a firewall or proxy blocked the download." \
            "Connect to the internet (any Wi-Fi is fine), then double-click install.command again."
    fi
    stop_install "installing OpenMuscle failed." \
        "Run install.command again. If it fails again, send the log file named below" \
        "to the person who gave you OpenMuscle."
fi
export OPENMUSCLE_HOME="$ROOT"
"$APP/bin/python" -c 'from openmuscle import paths; paths.ensure_data_dirs()' >> "$LOG" 2>&1

# ------------------------------------------------------ launcher + assets ----
cp "$SCRIPT_DIR/OpenMuscle.command" "$APP/OpenMuscle.command" 2>/dev/null \
    || curl -fsSL "https://raw.githubusercontent.com/Open-Muscle/OpenMuscle-Software/main/installers/macos/OpenMuscle.command" -o "$APP/OpenMuscle.command" \
    || stop_install "the launcher file could not be copied." \
        "Extract the whole downloaded ZIP (not just one file) and run install.command from inside it."
chmod +x "$APP/OpenMuscle.command"
# Files made by this script are not quarantined; remove any flag a copy carried.
xattr -d com.apple.quarantine "$APP/OpenMuscle.command" 2>/dev/null || true  # [unverified]

say "4/5  Downloading the offline camera and 3D viewer files..."
CERT="$("$APP/bin/python" -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
if [ -n "$CERT" ]; then
    export SSL_CERT_FILE="$CERT"
fi
if ! run_logged "assets fetch" "$APP/bin/openmuscle" assets fetch; then
    say "     Could not download them (no internet?). OpenMuscle still works; the camera page"
    say "     will load them from the internet when you use it. Run this installer again to retry."
fi

# ------------------------------------------------------------ Desktop file ----
say "5/5  Creating the OpenMuscle launcher on your Desktop..."
STUB="$DESKTOP/OpenMuscle.command"
if mkdir -p "$DESKTOP" 2>/dev/null && {
    printf '#!/bin/bash\n'
    printf '# OpenMuscle launcher made by the installer. Double-click to start.\n'
    printf 'export OPENMUSCLE_HOME=%q\n' "$ROOT"
    # shellcheck disable=SC2016  # "$@" must reach the generated file unexpanded
    printf 'exec %q "$@"\n' "$APP/OpenMuscle.command"
} > "$STUB" 2>/dev/null; then
    chmod +x "$STUB"
    say "     Desktop launcher:  $STUB"
else
    say "     Could not write to the Desktop (macOS may have asked for permission and been"
    say "     declined). You can still start OpenMuscle by double-clicking:"
    say "     $APP/OpenMuscle.command"
fi

# ----------------------------------------------------------------- doctor ----
if [ -z "${OPENMUSCLE_SKIP_DOCTOR:-}" ]; then
    if "$APP/bin/openmuscle" doctor --no-discover >> "$LOG" 2>&1; then
        say "     Computer check: no problems found."
    else
        say "     The computer check found something to look at. It does not stop the install;"
        say "     the OpenMuscle window shows the same advice. Details are in the log."
    fi
fi

log "Install finished OK."
say ""
say "OpenMuscle is installed."
say ""
say "  To start it:  double-click OpenMuscle on your Desktop."
say "  A Terminal window opens and your web browser shows OpenMuscle."
say "  Leave the Terminal window open while you work. Closing it stops OpenMuscle."
say ""
say "  The first time, expect these macOS questions (answer as shown):"
say "    - \"Do you want the application Python to accept incoming network connections?\""
say "      Click Allow. The bands send their data to this computer over Wi-Fi."
say "    - \"Python would like to find and connect to devices on your local network\""
say "      Click Allow (System Settings > Privacy & Security > Local Network)."
say "    - The browser asking to use the camera (for the webcam labeler): click Allow."
say "      If it never asks: System Settings > Privacy & Security > Camera, and switch"
say "      your browser on."
say "    - If the Desktop launcher says it cannot be opened: right-click it, choose Open,"
say "      then Open again."
say ""
say "  Your recordings are saved in:  $ROOT/captures"
say ""
pause_if_interactive
exit 0
