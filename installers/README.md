# Installers

One double-click (Windows, macOS) or one command (Linux) turns a downloaded copy of
this repository into an OpenMuscle you can start from an icon. None of them needs
administrator rights, and none of them changes your system Python.

What every installer does, in the same order:

1. Looks for Python 3.10 or newer, and tells you exactly how to get it if it is missing.
2. Makes a private Python environment in `~/OpenMuscle/app` and installs OpenMuscle into
   it, from the folder you downloaded (or from GitHub if you run the script on its own).
3. Creates the data folder `~/OpenMuscle` with `captures`, `sessions`, `models`,
   `exports` and `logs` inside. This is where your recordings go.
4. Downloads the offline camera and 3D viewer files when you are online. If you are
   offline it carries on; those pages then load the files from the internet instead.
5. Creates the launcher: a Desktop and Start-menu shortcut on Windows, a Desktop
   launcher on macOS, an application-menu entry and an `openmuscle-start` command on Linux.
6. Runs `openmuscle doctor` and says whether it found anything to fix.

Running the installer again is safe. It repairs a broken install or upgrades an old one
and never touches your recordings.

Everything the installer does is written to `~/OpenMuscle/logs/install.log`. If an
install stops, that file says why; send it to whoever gave you OpenMuscle.

The launcher opens your browser at OpenMuscle and keeps a window open while it runs.
Leave that window open; closing it stops OpenMuscle. Starting the launcher a second
time while OpenMuscle is already running just opens the page again.

Where `~` is your home folder (for example `C:\Users\you` on Windows). Set the
environment variable `OPENMUSCLE_HOME` before installing to put the data folder somewhere
else; the launcher remembers it.

## Windows 10 and 11

1. Download the repository (green Code button, Download ZIP) and extract the whole ZIP.
   Right-click the ZIP first, choose Properties, tick Unblock if you see it, then extract:
   that avoids most Windows warnings.
2. Open the extracted folder, then `installers`, then `windows`.
3. Double-click `Install-OpenMuscle.bat`.
   - If Windows says "Windows protected your PC", click More info, then Run anyway.
   - If Python is missing it installs Python 3.12 for you with winget when it can. If it
     cannot, it opens the python.org download page. On the first screen of the Python
     installer, tick **Add python.exe to PATH**, finish, then double-click
     `Install-OpenMuscle.bat` again.
   - It asks once whether to add the Windows firewall rule the bands need (UDP ports
     3140 and 3141, home and work networks only). Answer Y, then Yes to the Windows
     administrator question. Nothing else asks for administrator rights.
4. When it says "OpenMuscle is installed", double-click the **OpenMuscle** icon on your
   Desktop (or find it in the Start menu).

Files in `installers\windows`:

| File | What it is |
| --- | --- |
| `Install-OpenMuscle.bat` | The double-click entry point. Runs `install.ps1` with the PowerShell policy bypassed for that one script only. |
| `install.ps1` | The installer. Options: `-SkipAssets`, `-SkipDoctor`, `-NoPythonInstall`, `-Firewall yes\|no\|ask`. |
| `OpenMuscle.bat` | The launcher the shortcut points at. The installer keeps a copy in `~\OpenMuscle\app` so the shortcut survives deleting the download. |
| `openmuscle.ico` | The shortcut icon. |

Paths with spaces (including a user name with a space) are supported everywhere.

## macOS 12 or newer, Intel or Apple Silicon

1. Download the repository (Code, Download ZIP) and double-click the ZIP to extract it,
   or `git clone` it. A `git clone` avoids the Gatekeeper warning below completely.
2. Open the extracted folder, then `installers`, then `macos`.
3. Double-click `install.command`.
   - First time from a ZIP, macOS may say it "cannot be opened because it is from an
     unidentified developer". Right-click `install.command`, choose **Open**, then
     **Open** again. Or, once, in Terminal: `xattr -dr com.apple.quarantine <the extracted folder>`
     (type the command, then drag the folder into the Terminal window to fill in its path).
   - If you see "Python 3.10 or newer is needed": install it from
     <https://www.python.org/downloads/macos/> (the universal installer works on Intel and
     Apple Silicon) or with `brew install python@3.12`, then run `install.command` again.
     The Python that ships with the Mac is too old, and the developer-tools pop-up does
     not fix that.
4. When it says "OpenMuscle is installed", double-click **OpenMuscle** on your Desktop.
   The first time, macOS asks three things; allow all of them:
   - "Do you want the application Python to accept incoming network connections?" Allow.
   - "Python would like to find and connect to devices on your local network." Allow
     (System Settings, Privacy and Security, Local Network).
   - Your browser asking to use the camera. If it never asks: System Settings, Privacy and
     Security, Camera, and switch your browser on.

   If macOS refuses the Desktop launcher, right-click it, choose Open, then Open again.

Files in `installers/macos`: `install.command` (the installer) and `OpenMuscle.command`
(the launcher; a copy is kept in `~/OpenMuscle/app`). Both must keep their executable
bit; git stores them as mode 100755 and the ZIP from GitHub preserves it. macOS keeps the
computer awake while OpenMuscle runs, so you do not need to do anything for long recordings
(closing the lid still sleeps it).

## Linux

```
bash installers/linux/install.sh
```

Then open **OpenMuscle** from your application menu, or type `openmuscle-start`.

- Python 3.10 or newer is needed. If `python3 -m venv` does not work, the installer prints
  the one package to install for your distribution (Debian and Ubuntu:
  `sudo apt install python3-venv python3-pip`; Fedora: `sudo dnf install python3`) and stops.
- It never runs `sudo`. If a firewall is on (ufw or firewalld) it prints the exact lines
  to copy: `sudo ufw allow 3140:3141/udp`, or the two `firewall-cmd` lines.
- The launcher goes to `~/.local/bin/openmuscle-start` and the menu entry to
  `~/.local/share/applications/openmuscle.desktop`. If `~/.local/bin` is not on your PATH
  the installer says so; the menu entry works either way.
- The Python environment lives under `~/OpenMuscle/app`, which also satisfies
  "externally managed environment" (PEP 668) systems, since nothing is installed system-wide.
- Another way, if you prefer `pipx`: `pipx install ./pc` from the repository folder, then
  `openmuscle web`. You then have no menu entry or offline files; run `openmuscle assets fetch`.

Files in `installers/linux`: `install.sh`, `openmuscle.desktop` (the template the installer
fills in) and `openmuscle.png` (the icon).

## Uninstalling

Your recordings are in `~/OpenMuscle/captures` and are never removed by these steps.

- Windows: delete the OpenMuscle shortcut from the Desktop and the Start menu, then delete
  the folder `%USERPROFILE%\OpenMuscle\app`. Optional: remove the firewall rule with
  `openmuscle firewall --remove` before you delete the app folder.
- macOS: delete `OpenMuscle` from the Desktop and the folder `~/OpenMuscle/app`.
- Linux: delete `~/.local/bin/openmuscle-start`, `~/.local/share/applications/openmuscle.desktop`
  and the folder `~/OpenMuscle/app`.

## What has been tested

| Part | Tested how |
| --- | --- |
| Windows installer, launcher, shortcut | Run end to end on Windows 11 into a throwaway data folder with spaces in the path; run twice; offline failure message and log; second launch opens the page instead of a second server; closing the window frees the ports. |
| Linux installer and launcher | Run end to end on Ubuntu 24.04 (WSL) into a throwaway folder, twice; the launcher served the VR page. `shellcheck` is clean. Not yet run on Fedora, or with a firewall switched on. |
| macOS installer and launcher | The shell logic was run on Linux with a stand-in `python3`; `shellcheck` is clean. **Not run on a Mac.** Lines depending on macOS (the quarantine flag, the Terminal title, the Desktop permission) are marked `[unverified]` in the scripts. |

A test in `pc/tests/test_installers.py` keeps the line endings right (CRLF in `.bat` and
`.ps1`, LF in `.sh`, `.command` and `.desktop`; `.gitattributes` enforces it) and checks
the scripts parse. Setting `OPENMUSCLE_INSTALL_SMOKE=1` also makes
`pc/tests/test_install_windows_smoke.py` run the real Windows installer into a temporary
folder (a few minutes, needs the internet).
