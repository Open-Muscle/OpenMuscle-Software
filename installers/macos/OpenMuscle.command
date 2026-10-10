#!/bin/bash
# OpenMuscle launcher for macOS. The "OpenMuscle" file the installer puts on your
# Desktop runs this one. It is a plain script and nothing else.
#
# What it does:
#   1. Finds the OpenMuscle program the installer set up (no PATH, no activate).
#   2. If OpenMuscle is already running in another window, just opens the page in
#      your browser instead of starting a second copy.
#   3. Otherwise runs "openmuscle web" in this Terminal window. It opens your
#      browser by itself once the page is ready. The program also asks macOS to
#      keep the computer awake while it runs, so there is no need for caffeinate.
#
# Closing the Terminal window (or pressing Ctrl+C) stops OpenMuscle.
#
# Not yet run on a Mac by the author. Lines that depend on macOS-only behaviour
# are marked [unverified].

set -u

# A dry run (used by the CI smoke test) proves this file parses without
# installing or starting anything.
if [ -n "${OPENMUSCLE_DRY_RUN:-}" ]; then
    echo "OpenMuscle launcher dry run: nothing started."
    exit 0
fi

# Terminal tab title [unverified: cosmetic only].
printf '\033]0;OpenMuscle (leave open)\007'

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$SELF_DIR/bin/openmuscle" ]; then
    # The copy the installer keeps inside the app folder: the data folder is the
    # folder that holds the app folder.
    APP="$SELF_DIR"
    OPENMUSCLE_HOME="$(dirname "$APP")"
else
    # Run from the downloaded folder: look in the default place.
    OPENMUSCLE_HOME="${OPENMUSCLE_HOME:-$HOME/OpenMuscle}"
    APP="$OPENMUSCLE_HOME/app"
fi
export OPENMUSCLE_HOME
EXE="$APP/bin/openmuscle"

pause_if_interactive() {
    if [ -z "${OPENMUSCLE_NO_PAUSE:-}" ] && [ -t 0 ]; then
        printf '\nPress Return to close this window. '
        read -r _
    fi
}

if [ ! -x "$EXE" ]; then
    echo
    echo "OpenMuscle is not installed on this computer yet."
    echo "Open the OpenMuscle download folder, then installers, then macos,"
    echo "and double-click install.command. When it finishes, start OpenMuscle"
    echo "from the new OpenMuscle file on your Desktop."
    pause_if_interactive
    exit 1
fi

# Already running? Then just open the page (curl ships with macOS).
p=8000
while [ "$p" -le 8010 ]; do
    if curl -s --max-time 2 "http://127.0.0.1:$p/api/mode" 2>/dev/null | grep -q openmuscle; then
        echo "OpenMuscle is already running, so opening it in your browser."
        open "http://localhost:$p"
        sleep 2
        exit 0
    fi
    p=$((p + 1))
done

echo "Starting OpenMuscle. Leave this window open while you work."
echo "Close it, or press Ctrl+C, to stop."
echo
"$EXE" web "$@"
rc=$?
if [ "$rc" -ne 0 ] && [ "$rc" -ne 130 ]; then
    echo
    echo "OpenMuscle stopped with a problem. The message above says what went wrong."
    echo "Run install.command again to repair the install, or look at"
    echo "$OPENMUSCLE_HOME/logs/install.log if the install itself had trouble."
    pause_if_interactive
fi
exit "$rc"
