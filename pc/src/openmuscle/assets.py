"""Offline copies of the browser assets the webcam labeler and 3D viewer use.

The webcam page tracks a hand with Google's MediaPipe Hand Landmarker, and the
dashboard draws the headset hand with three.js. By default both are loaded from
a public CDN, which fails on a computer with no internet. `openmuscle assets
fetch` downloads the exact pinned versions once into <data root>/assets, checks
every file against the size and checksum recorded below, and the web server
then serves them under /static/vendor/ so the pages work with the network off.

Nothing here is committed to the repository as a binary: this file records
where each file comes from and what it must hash to.

Layout under <data root>/assets (served at /static/vendor/):
    mediapipe/vision_bundle.mjs
    mediapipe/wasm/vision_wasm_internal.js
    mediapipe/wasm/vision_wasm_internal.wasm
    mediapipe/hand_landmarker.task
    three/three.module.js
    NOTICE.txt
"""

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from openmuscle import paths

TRACKER_VERSION = "0.10.14"
THREE_VERSION = "0.160.0"

_NPM_TRACKER = f"https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@{TRACKER_VERSION}"
_NPM_THREE = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}"
_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
              "hand_landmarker/float16/1/hand_landmarker.task")

GROUP_LABELS = {
    "tracker": "hand tracker for the webcam page",
    "three": "3D hand viewer for the dashboard",
}


class AssetError(Exception):
    """A download or verification problem, worded for the person running it."""


@dataclass(frozen=True)
class Asset:
    group: str                    # "tracker" or "three"
    rel: str                      # path below the assets folder, forward slashes
    url: str
    size: int                     # bytes
    sha256: Optional[str] = None  # hex
    md5: Optional[str] = None     # hex; checked only when sha256 is not pinned
    optional: bool = False        # not needed for the group to count as ready

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1]


# sha256 values: the npm registry hashes published by jsDelivr for
# @mediapipe/tasks-vision 0.10.14 and three 0.160.0 (sizes match the packages).
# The model file is hosted by Google as an immutable object; its sha256 has not
# been pinned yet (see `openmuscle assets status`, which prints the sha256 of
# whatever is installed so a maintainer can pin it), so it is pinned by size and
# by the MD5 Google publishes for that object in the bucket metadata.
MANIFEST = (
    Asset("tracker", "mediapipe/vision_bundle.mjs", f"{_NPM_TRACKER}/vision_bundle.mjs",
          136870, "e77f281f9619150d937023c355bae170e9120e3b9e43f1e23a2a7bee07197669"),
    Asset("tracker", "mediapipe/wasm/vision_wasm_internal.js",
          f"{_NPM_TRACKER}/wasm/vision_wasm_internal.js",
          209826, "9440cf0cc0cea21800e31581ec32aeedcc5fbf9df4509796bbc7d3f99e52ab9c"),
    Asset("tracker", "mediapipe/wasm/vision_wasm_internal.wasm",
          f"{_NPM_TRACKER}/wasm/vision_wasm_internal.wasm",
          9423986, "f82a8e6c05e08a44cc9f9e7ec5f845935bcbb1b1500ebe8c2f4812fb4e2917dc"),
    Asset("tracker", "mediapipe/hand_landmarker.task", _MODEL_URL,
          7819105, None, "15318430ea3851670fe9914116a9cfad"),
    # Only for computers whose processor lacks SIMD (the tracker then asks for
    # these instead). Fetched with `--with-nosimd`.
    Asset("tracker", "mediapipe/wasm/vision_wasm_nosimd_internal.js",
          f"{_NPM_TRACKER}/wasm/vision_wasm_nosimd_internal.js",
          209735, "abe9b6fbeaf86fcb53a5edce3926c82ccb0619e18fed4d9d9ce561ee7f55e054", None, True),
    Asset("tracker", "mediapipe/wasm/vision_wasm_nosimd_internal.wasm",
          f"{_NPM_TRACKER}/wasm/vision_wasm_nosimd_internal.wasm",
          9294247, "38b61feab2fd7934e05cbe9f68baa308978a5e3b7f85c1913bb8ae89b8ef8b97", None, True),
    Asset("three", "three/three.module.js", f"{_NPM_THREE}/build/three.module.js",
          1272972, "76dea8151bc9352aef3528b4262e249b2604f62543828328db978d060d61a495"),
)

NOTICE = f"""\
Third-party files downloaded by `openmuscle assets fetch`

These files are not part of OpenMuscle. They are fetched unchanged from their
publishers and checked against the sizes and checksums pinned in OpenMuscle.

MediaPipe Tasks Vision {TRACKER_VERSION} (vision_bundle.mjs, wasm/)
    Copyright Google LLC. The npm package declares the Apache-2.0 licence:
    https://www.apache.org/licenses/LICENSE-2.0
    Source: {_NPM_TRACKER}/

MediaPipe Hand Landmarker model (hand_landmarker.task, float16, version 1)
    Published by Google (MediaPipe). The terms on its model card apply.
    Source: {_MODEL_URL}

three.js {THREE_VERSION} (three.module.js)
    Copyright 2010-2023 three.js authors. MIT licence:
    https://github.com/mrdoob/three.js/blob/r160/LICENSE
    Source: {_NPM_THREE}/build/three.module.js
"""


# ---------- verification ----------

def _hash_file(path: Path):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def _check(asset: Asset, size: int, sha_hex: str, md5_hex: str) -> Optional[str]:
    """None when (size, hashes) match the pin, else a short reason."""
    if size != asset.size:
        return f"wrong size ({size} bytes, expected {asset.size})"
    if asset.sha256:
        if sha_hex != asset.sha256:
            return "checksum does not match"
    elif asset.md5 and md5_hex != asset.md5:
        return "checksum does not match"
    return None


_verified: Dict[tuple, Optional[str]] = {}
_verified_lock = threading.Lock()


def verify_file(path: Path, asset: Asset) -> Optional[str]:
    """None if the file at `path` is the pinned file, else why not.

    The full hash is computed once per (path, size, mtime) and remembered, so
    the server can ask on every page load without re-reading 17 MB each time.
    """
    try:
        st = path.stat()
    except OSError:
        return "missing"
    if not path.is_file():
        return "missing"
    if st.st_size != asset.size:
        return f"wrong size ({st.st_size} bytes, expected {asset.size})"
    key = (str(path), st.st_size, st.st_mtime_ns, asset.sha256, asset.md5)
    with _verified_lock:
        if key in _verified:
            return _verified[key]
    try:
        sha_hex, md5_hex = _hash_file(path)
    except OSError as e:
        return f"unreadable ({e})"
    result = _check(asset, st.st_size, sha_hex, md5_hex)
    with _verified_lock:
        _verified[key] = result
    return result


def status(root: Optional[Path] = None, manifest=None) -> List[dict]:
    """One row per manifest file: {asset, path, problem} (problem None = good)."""
    base = Path(root) if root else paths.assets_dir()
    return [{"asset": a, "path": base / a.rel, "problem": verify_file(base / a.rel, a)}
            for a in (MANIFEST if manifest is None else manifest)]


def group_ready(group: str, root: Optional[Path] = None, manifest=None) -> bool:
    """True when every non-optional file of the group is present and verified."""
    rows = [r for r in status(root, manifest)
            if r["asset"].group == group and not r["asset"].optional]
    return bool(rows) and all(r["problem"] is None for r in rows)


def web_status(root: Optional[Path] = None, manifest=None) -> dict:
    """What GET /api/assets returns: per group, is the local copy usable."""
    base = Path(root) if root else paths.assets_dir()
    rows = status(base, manifest)
    groups = {}
    for g, label in GROUP_LABELS.items():
        mine = [r for r in rows if r["asset"].group == g and not r["asset"].optional]
        bad = [{"file": r["asset"].rel, "problem": r["problem"]}
               for r in mine if r["problem"] is not None]
        groups[g] = {"label": label, "ready": bool(mine) and not bad, "problems": bad}
    return {"dir": str(base), "tracker_version": TRACKER_VERSION,
            "three_version": THREE_VERSION, "groups": groups}


# ---------- download ----------

def _open_url(url: str):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "openmuscle-assets"})
    return urllib.request.urlopen(req, timeout=60)


def fetch(root: Optional[Path] = None, manifest=None, groups=None,
          include_optional: bool = False, force: bool = False,
          opener: Optional[Callable] = None,
          say: Callable[[str], None] = lambda _m: None) -> List[Path]:
    """Download whatever is missing or wrong, verifying each file.

    Returns the paths downloaded now. Raises AssetError at the first problem
    (no internet, a failed download, or a size/checksum mismatch); the bad file
    is deleted, never left in place. Files already present and correct are
    skipped unless force is set. `opener(url)` returns a binary file-like object
    and exists so tests need no network.
    """
    base = Path(root) if root else paths.assets_dir()
    opener = opener or _open_url
    done: List[Path] = []
    for a in (MANIFEST if manifest is None else manifest):
        if groups and a.group not in groups:
            continue
        if a.optional and not include_optional:
            continue
        dest = base / a.rel
        if not force and verify_file(dest, a) is None:
            say(f"  already have {a.rel}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        say(f"  downloading {a.rel} ({a.size / 1e6:.1f} MB)")
        sha, md5, got = hashlib.sha256(), hashlib.md5(), 0
        try:
            with opener(a.url) as r, open(part, "wb") as out:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > a.size:
                        break          # longer than pinned: stop, reported below
                    sha.update(chunk)
                    md5.update(chunk)
                    out.write(chunk)
        except Exception as e:
            _discard(part)
            raise AssetError(
                f"Could not download {a.name}: {e}. Check that this computer is "
                f"online and run the command again.") from e
        problem = _check(a, got, sha.hexdigest(), md5.hexdigest())
        if problem:
            _discard(part)
            raise AssetError(
                f"{a.name} was downloaded but {problem}, so it was discarded and "
                f"nothing was installed for it. Try again; if it keeps happening "
                f"do not use this file.")
        os.replace(part, dest)
        done.append(dest)
    _write_notice(base, say)
    return done


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _write_notice(base: Path, say) -> None:
    try:
        base.mkdir(parents=True, exist_ok=True)
        (base / "NOTICE.txt").write_text(NOTICE, encoding="utf-8")
    except OSError as e:
        say(f"  (could not write NOTICE.txt: {e})")


def actual_hashes(root: Optional[Path] = None, manifest=None) -> List[dict]:
    """sha256 and md5 of each installed file, for a maintainer pinning values."""
    out = []
    for row in status(root, manifest):
        try:
            sha, md5 = _hash_file(row["path"])
        except OSError:
            continue
        out.append({"rel": row["asset"].rel, "sha256": sha, "md5": md5})
    return out
