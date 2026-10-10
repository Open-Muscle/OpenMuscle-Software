"""Where OpenMuscle keeps your files: one data root for everything.

Captures, sessions, trained models, exports and logs all live under a single
folder so the answer to "where did my recording go?" never depends on which
directory the program was started from.

Resolution order for the root (first match wins):
  1. set_data_root(...), which the CLI calls for --data-dir
  2. the OPENMUSCLE_HOME environment variable
  3. ~/OpenMuscle   (the home folder itself, not Documents, which is often
     synced to a cloud drive and does not suit large recordings)

Layout:
    <root>/captures   recorded CSVs plus their .meta.json / .labels sidecars
    <root>/sessions   one JSON file per recording session
    <root>/models     trained model folders
    <root>/exports    prediction exports, shareable archives
    <root>/logs       server logs
    <root>/devices.json  the bands this computer has seen before (a small
                      file, not a folder; copied once from the older
                      ~/.openmuscle/discovered_devices.json location)
    <root>/assets     optional offline copies of the hand tracker and 3D viewer
                      (created by `openmuscle assets fetch`)
"""

import os
import shutil
from pathlib import Path
from typing import List, Optional

ENV_HOME = "OPENMUSCLE_HOME"
SUBDIRS = ("captures", "sessions", "models", "exports", "logs")

_override: Optional[Path] = None


def set_data_root(path) -> None:
    """Pin the data root for this process (used for --data-dir). None clears."""
    global _override
    _override = None if not path else _expand(path)


def _expand(path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def data_root() -> Path:
    """The absolute data root. Does not create anything."""
    if _override is not None:
        return _override
    env = os.environ.get(ENV_HOME, "").strip()
    if env:
        return _expand(env)
    return Path(os.path.abspath(os.path.join(os.path.expanduser("~"), "OpenMuscle")))


def captures_dir() -> Path:
    return data_root() / "captures"


def sessions_dir() -> Path:
    return data_root() / "sessions"


def models_dir() -> Path:
    return data_root() / "models"


def exports_dir() -> Path:
    return data_root() / "exports"


def logs_dir() -> Path:
    return data_root() / "logs"


def last_port_path() -> Path:
    """Where `openmuscle web` records the port it is serving on, so the launchers
    can reopen the same address next time instead of guessing."""
    return logs_dir() / "last-port.txt"


def write_last_port(port) -> None:
    """Record the web port for the launchers. Never raises: a read-only data
    folder only costs the launchers their hint."""
    try:
        path = last_port_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("%d\n" % int(port), encoding="ascii")
    except (OSError, ValueError):
        pass


def read_last_port():
    """The port recorded by the last `openmuscle web` run, or None."""
    try:
        text = last_port_path().read_text(encoding="ascii", errors="replace").strip()
        port = int(text)
    except (OSError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def assets_dir() -> Path:
    """Offline browser assets. Not in SUBDIRS: only `assets fetch` creates it."""
    return data_root() / "assets"


def devices_cache_path() -> Path:
    """The remembered-bands file. It follows the data root, so a different
    --data-dir or OPENMUSCLE_HOME never shows bands seen under another root."""
    return data_root() / "devices.json"


def legacy_devices_cache_path() -> Path:
    """Where earlier versions kept the remembered bands: in the user profile,
    whatever the data root was."""
    return Path(os.path.join(os.path.expanduser("~"), ".openmuscle",
                             "discovered_devices.json"))


def migrate_devices_cache() -> Path:
    """Return the remembered-bands path, first copying the older profile file
    there once. Only when the new file does not exist and the old one does; the
    old file is left in place and never touched again. Never raises."""
    new = devices_cache_path()
    try:
        old = legacy_devices_cache_path()
        if not new.exists() and old.is_file():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(old), str(new))
    except OSError:
        pass
    return new


def ensure_data_dirs() -> Path:
    """Create the root and every standard subfolder. Returns the root."""
    root = data_root()
    for name in SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def absolute(path) -> Path:
    """Absolute form of a user-supplied path (~ expanded, relative to cwd)."""
    return _expand(path)


def _has_files(folder: Path, pattern: str) -> bool:
    try:
        return folder.is_dir() and any(folder.glob(pattern))
    except OSError:
        return False


def legacy_cwd_data(cwd: Optional[Path] = None) -> List[Path]:
    """Older-style data folders found under the current directory.

    Before the single data root, captures went to ./data/raw/merged, sessions to
    ./data/raw/sessions and models to ./data/models relative to wherever the
    program was started. Returns the ones that hold files and are not already
    inside the data root, so the CLI can hint that they are no longer used.
    """
    cwd = Path(cwd) if cwd else Path.cwd()
    root = data_root()
    found = []
    for rel, pattern in ((("data", "raw", "merged"), "*.csv"),
                         (("data", "raw", "sessions"), "*.json"),
                         (("data", "models"), "*/model.pkl")):
        folder = cwd.joinpath(*rel)
        if not _has_files(folder, pattern):
            continue
        try:
            folder.resolve().relative_to(root.resolve())
            continue                      # already inside the data root
        except ValueError:
            pass
        found.append(folder)
    return found


def legacy_hint(cwd: Optional[Path] = None) -> Optional[str]:
    """One-line hint when cwd holds older data, else None."""
    found = legacy_cwd_data(cwd)
    if not found:
        return None
    where = ", ".join(str(p) for p in found)
    return ("Note: found older data in {} . OpenMuscle now saves to {} ; copy those "
            "files into the matching subfolder there to see them.".format(where, data_root()))
