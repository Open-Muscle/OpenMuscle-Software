#!/usr/bin/env bash
# OpenMuscle installer for Linux. Run it from a terminal:  bash install.sh
# (or double-click it in a file manager that offers "Run in terminal").
#
# What it does. It never uses sudo and never changes system packages:
#   1. Finds Python 3.10 or newer, and checks that "python3 -m venv" works. If it
#      does not, it prints the package to install for your distribution.
#   2. Makes a private Python environment in  ~/OpenMuscle/app  and installs
#      OpenMuscle into it (this satisfies PEP 668 "externally managed" Pythons;
#      nothing is installed system-wide). From this download, or from GitHub when
#      the script is run alone.
#   3. Creates the data folder ~/OpenMuscle (captures, sessions, models, ...).
#   4. Downloads the offline browser files when online (continues if offline).
#   5. Writes the launcher  ~/.local/bin/openmuscle-start  and the app-menu entry
#      ~/.local/share/applications/openmuscle.desktop .
#   6. Runs  openmuscle doctor , and prints the firewall commands to run yourself
#      only when a firewall (ufw or firewalld) is switched on.
#
# Safe to run again: a second run repairs or upgrades the same install.
# Everything is logged to  ~/OpenMuscle/logs/install.log .
# Environment: OPENMUSCLE_HOME moves the data folder, OPENMUSCLE_SKIP_DOCTOR=1
# skips the final check, OPENMUSCLE_SKIP_ASSETS=1 skips the offline-files
# download, OPENMUSCLE_BIN_DIR / OPENMUSCLE_APPS_DIR override where the launcher
# and the menu entry go (for tests).
#
# NOT YET RUN ON LINUX by the author (written and syntax-checked on Windows).
# Lines marked [unverified] depend on desktop behaviour that could not be tried.

set -u

REPO_URL="https://github.com/Open-Muscle/OpenMuscle-Software"
GITHUB_ZIP="$REPO_URL/archive/refs/heads/main.zip#subdirectory=pc"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${OPENMUSCLE_HOME:-$HOME/OpenMuscle}"
APP="$ROOT/app"
LOGDIR="$ROOT/logs"
LOG="$LOGDIR/install.log"
BIN_DIR="${OPENMUSCLE_BIN_DIR:-$HOME/.local/bin}"
APPS_DIR="${OPENMUSCLE_APPS_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/applications}"
WRAPPER="$BIN_DIR/openmuscle-start"

mkdir -p "$LOGDIR" || {
    echo "OpenMuscle could not create its folder: $ROOT"
    echo "Choose a folder you can write to by setting OPENMUSCLE_HOME, then run this again."
    exit 1
}

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
say() { printf '%s\n' "$*"; log "$*"; }

stop_install() {
    say ""
    say "Installation stopped: $1"
    shift
    local line
    for line in "$@"; do say "  $line"; done
    say ""
    say "A detailed log is saved here:  $LOG"
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

# The package to install (as a command to copy) when "python3 -m venv" fails.
venv_hint() {
    local id="" like="" line
    if [ -r /etc/os-release ]; then
        while IFS= read -r line; do
            case "$line" in
                ID=*) id="${line#ID=}" ;;
                ID_LIKE=*) like="${line#ID_LIKE=}" ;;
            esac
        done < /etc/os-release
    fi
    id="${id//\"/}"
    like="${like//\"/}"
    case " $id $like " in
        *" debian "*|*" ubuntu "*)
            echo "sudo apt install python3-venv python3-pip" ;;
        *" fedora "*|*" rhel "*|*" centos "*)
            echo "sudo dnf install python3 python3-pip   (Fedora's python3 normally includes venv)" ;;
        *" arch "*)
            echo "sudo pacman -S python python-pip" ;;
        *" suse "*|*" opensuse "*)
            echo "sudo zypper install python3 python3-pip" ;;
        *)
            echo "install your distribution's Python 3 venv and pip packages" ;;
    esac
}

log "---------------------------------------------------------------"
log "OpenMuscle install started on $(uname -sm)"
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
    local c
    for c in python3.12 python3.13 python3.11 python3.10 python3.14 python3; do
        if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then
            command -v "$c"
            return 0
        fi
    done
    return 1
}

say "1/5  Looking for Python 3.10 or newer..."
PY="$(find_python)" || PY=""
if [ -z "$PY" ]; then
    FOUND="$(command -v python3 2>/dev/null || true)"
    if [ -n "$FOUND" ]; then
        OLD="$("$FOUND" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo unknown)"
        stop_install "Python 3.10 or newer is needed, and this computer has $OLD." \
            "Install a newer Python with your package manager, for example:" \
            "  Ubuntu or Debian:  sudo apt install python3.12 python3.12-venv" \
            "  Fedora:            sudo dnf install python3" \
            "Then run this installer again."
    fi
    stop_install "Python 3 is not installed." \
        "Install it with your package manager, for example:" \
        "  Ubuntu or Debian:  sudo apt install python3 python3-venv python3-pip" \
        "  Fedora:            sudo dnf install python3" \
        "Then run this installer again."
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
        rm -rf "${APP:?}/bin" "${APP:?}/lib" "${APP:?}/lib64" "${APP:?}/include" "${APP:?}/pyvenv.cfg"
    fi
    mkdir -p "$APP"
    if ! run_logged "create environment" "$PY" -m venv "$APP" || [ ! -x "$APP/bin/python" ]; then
        rm -rf "${APP:?}/bin" "${APP:?}/lib" "${APP:?}/lib64" "${APP:?}/include" "${APP:?}/pyvenv.cfg"
        stop_install "Python could not make its private environment." \
            "On many Linux systems that needs one extra package. Copy this line into the" \
            "terminal, enter your password when asked, then run this installer again:" \
            "  $(venv_hint)"
    fi
fi

# --------------------------------------------------------------- install it --
say "3/5  Installing OpenMuscle and its libraries (a few minutes the first time)..."
export PIP_DISABLE_PIP_VERSION_CHECK=1
if [ -f "$SCRIPT_DIR/../../pc/pyproject.toml" ]; then
    PC="$(cd "$SCRIPT_DIR/../../pc" && pwd)"
    log "Installing from: $PC"
else
    PC="$GITHUB_ZIP"
    log "Installing from GitHub: $PC"
fi

run_logged "upgrade pip" "$APP/bin/python" -m pip install --upgrade pip || true
if run_logged "install openmuscle" "$APP/bin/python" -m pip install --upgrade "$PC"; then
    # Same version number on a re-run would keep the old files; force the refresh.
    run_logged "refresh openmuscle" "$APP/bin/python" -m pip install --no-deps --force-reinstall "$PC"
fi
if [ ! -x "$APP/bin/openmuscle" ]; then
    if grep -qiE 'Failed to establish|Max retries|Temporary failure|Network is unreachable|ProxyError|SSLError|No matching distribution|Could not find a version|timed out|ConnectionError' "$LOG"; then
        stop_install "the libraries could not be downloaded." \
            "This computer seems to be offline, or a firewall or proxy blocked the download." \
            "Connect to the internet (any Wi-Fi is fine), then run this installer again."
    fi
    stop_install "installing OpenMuscle failed." \
        "Run this installer again. If it fails again, send the log file named below" \
        "to the person who gave you OpenMuscle."
fi
export OPENMUSCLE_HOME="$ROOT"
"$APP/bin/python" -c 'from openmuscle import paths; paths.ensure_data_dirs()' >> "$LOG" 2>&1

# ---------------------------------------------------------------- assets ----
if [ -n "${OPENMUSCLE_SKIP_ASSETS:-}" ]; then
    say "4/5  Skipping the offline browser files (as requested)."
else
    say "4/5  Downloading the offline camera and 3D viewer files..."
    if ! run_logged "assets fetch" "$APP/bin/openmuscle" assets fetch; then
        say "     Could not download them (no internet?). OpenMuscle still works; the camera page"
        say "     will load them from the internet when you use it. Run this installer again to retry."
    fi
fi

# --------------------------------------------- launcher, icon and menu entry ----
say "5/5  Creating the launcher and the app-menu entry..."
ICON_SRC="$SCRIPT_DIR/openmuscle.png"
ICON="$APP/openmuscle.png"
[ -f "$ICON_SRC" ] && cp "$ICON_SRC" "$ICON"

mkdir -p "$BIN_DIR"
# The launcher: opens the page if OpenMuscle already runs, else starts it here.
# Unquoted heredoc on purpose: $ROOT and $APP are filled in now, \$ ones at run time.
cat > "$WRAPPER" <<EOF
#!/bin/sh
# OpenMuscle launcher made by the installer. Run it, or use the OpenMuscle entry
# in your application menu. Closing this terminal (or Ctrl+C) stops OpenMuscle.
OPENMUSCLE_HOME='$ROOT'
export OPENMUSCLE_HOME
EXE='$APP/bin/openmuscle'
if [ ! -x "\$EXE" ]; then
    echo "OpenMuscle is not installed here. Run installers/linux/install.sh again."
    exit 1
fi
# Already running? Then just open the page. The port the last run used is tried
# first (saved in logs/last-port.txt in the data folder), then 8000 to 8010. Only
# a server that answers as OpenMuscle counts, and the address opened is printed.
ports=""
last=""
if [ -r "\$OPENMUSCLE_HOME/logs/last-port.txt" ]; then
    read -r last < "\$OPENMUSCLE_HOME/logs/last-port.txt" || true
    last="\$(printf '%s' "\$last" | tr -d '\r')"
fi
case "\$last" in
    ''|*[!0-9]*) ;;
    *) ports="\$last" ;;
esac
p=8000
while [ "\$p" -le 8010 ]; do
    ports="\$ports \$p"
    p=\$((p + 1))
done
for p in \$ports; do
    if curl -s --max-time 2 "http://127.0.0.1:\$p/api/mode" 2>/dev/null | grep -q openmuscle; then
        echo "OpenMuscle is already running at http://localhost:\$p"
        echo "Opening that address in your browser."
        xdg-open "http://localhost:\$p" >/dev/null 2>&1 || echo "Open http://localhost:\$p in your browser."
        exit 0
    fi
done
echo "Starting OpenMuscle. Leave this window open while you work."
echo "Close it, or press Ctrl+C, to stop."
echo
"\$EXE" web "\$@"
rc=\$?
if [ "\$rc" -ne 0 ] && [ "\$rc" -ne 130 ] && [ -t 0 ]; then
    echo
    echo "OpenMuscle stopped with a problem. The message above says what went wrong."
    echo "Run installers/linux/install.sh again to repair the install."
    printf 'Press Return to close this window. '
    read -r _
fi
exit "\$rc"
EOF
chmod +x "$WRAPPER"
say "     Launcher:  $WRAPPER"

mkdir -p "$APPS_DIR"
DESKTOP_FILE="$APPS_DIR/openmuscle.desktop"
if [ -f "$SCRIPT_DIR/openmuscle.desktop" ]; then
    sed -e "s|@EXEC@|$WRAPPER|" -e "s|@ICON@|$ICON|" "$SCRIPT_DIR/openmuscle.desktop" > "$DESKTOP_FILE"
else
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=OpenMuscle
Comment=Record and view forearm pressure-sensor data
Exec=$WRAPPER
Icon=$ICON
Terminal=true
Categories=Science;Education;
EOF
fi
chmod 644 "$DESKTOP_FILE"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true   # [unverified]
fi
say "     Menu entry:  $DESKTOP_FILE"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) say "     Note: $BIN_DIR is not on your PATH, so type the full path above, or use the menu entry." ;;
esac

# ----------------------------------------------------------------- doctor ----
if [ -z "${OPENMUSCLE_SKIP_DOCTOR:-}" ]; then
    if "$APP/bin/openmuscle" doctor --no-discover >> "$LOG" 2>&1; then
        say "     Computer check: no problems found."
    else
        say "     The computer check found something to look at. It does not stop the install;"
        say "     the OpenMuscle window shows the same advice. Details are in the log."
    fi
fi

# --------------------------------------------------------------- firewall ----
# Printed only when a firewall is switched on. This script never runs them: they
# need your password and change your computer's security settings.
fw_active() {
    command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$1" 2>/dev/null
}
if fw_active ufw || { [ -r /etc/ufw/ufw.conf ] && grep -qi '^ENABLED=yes' /etc/ufw/ufw.conf; }; then
    say ""
    say "  Your firewall (ufw) is on. The bands send data to this computer over Wi-Fi, so"
    say "  allow it once by copying this line into a terminal:"
    say "    sudo ufw allow 3140:3141/udp"
fi
if fw_active firewalld; then
    say ""
    say "  Your firewall (firewalld) is on. The bands send data to this computer over Wi-Fi,"
    say "  so allow it once by copying these two lines into a terminal:"
    say "    sudo firewall-cmd --permanent --add-port=3140-3141/udp"
    say "    sudo firewall-cmd --reload"
fi

log "Install finished OK."
say ""
say "OpenMuscle is installed."
say ""
say "  To start it:  open OpenMuscle from your application menu,"
say "                or type  openmuscle-start  in a terminal."
say "  A terminal window opens and your web browser shows OpenMuscle."
say "  Leave the terminal window open while you work. Closing it stops OpenMuscle."
say ""
say "  Your recordings are saved in:  $ROOT/captures"
say ""
exit 0
