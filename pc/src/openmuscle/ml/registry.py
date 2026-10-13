"""Model save/load/version management with metadata tracking."""

import json
import time
import os
import pickle
import shutil
from datetime import datetime
from pathlib import Path

from openmuscle import paths


class ModelRegistry:
    """Save, load, and list trained models with metadata.

    Each model is stored as a directory containing:
        - model.pkl: the trained sklearn model
        - metadata.json: training metrics, columns, date
    """

    def __init__(self, base_dir: str = None):
        # Default: <data root>/models (see openmuscle.paths), never cwd-relative.
        self.base_dir = Path(base_dir) if base_dir else paths.models_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, model, name: str, metrics: dict = None, role: str = None,
             extra: dict = None) -> Path:
        """Save a model with metadata. Returns the model.pkl path.

        The directory name is <name>_<YYYYmmdd_HHMMSS>_<mmm>[_<role>]: millisecond
        precision plus the hand, and a numeric suffix if the name is still taken,
        so a left and a right model trained back to back (or two runs in the
        same second) can never write into the same directory. Creation order is
        kept in metadata.json as created_ns, and list_models() orders by it, so
        the "newest model" lookups do not depend on how the names sort (two
        saves in the same millisecond would otherwise sort by hand, not by age).
        `extra` is merged into metadata.json at the top level (provenance such as
        participant and band ids).
        """
        now = datetime.now()
        created_ns = time.time_ns()
        ts = now.strftime("%Y%m%d_%H%M%S")
        stem = f"{name}_{ts}_{now.microsecond // 1000:03d}"
        if role in ("left", "right"):
            stem += f"_{role}"
        model_dir = None
        for n in range(1, 1000):
            candidate = self.base_dir / (stem if n == 1 else f"{stem}-{n}")
            try:
                candidate.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            model_dir = candidate
            break
        if model_dir is None:
            raise OSError(f"could not create a unique model folder under {self.base_dir}")

        model_path = model_dir / "model.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        meta = {
            "name": name,
            "created": ts,
            "created_ns": created_ns,
            "dir": model_dir.name,
            "model_type": type(model).__name__,
            "metrics": metrics or {},
        }
        if extra:
            meta.update(extra)
        with open(model_dir / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2)

        return model_path

    def load(self, path: str):
        """Load a model from a .pkl file."""
        with open(path, "rb") as f:
            return pickle.load(f)

    def list_models(self) -> list[dict]:
        """List all models in the registry with their metadata, oldest first.

        Order is by creation time (created_ns in metadata.json; a folder saved
        before that field existed uses its own mtime), then by name.
        """
        found = []
        for d in self.base_dir.iterdir():
            if not d.is_dir():
                continue
            meta_path = d / "metadata.json"
            if meta_path.exists():
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    continue          # a half-written or damaged folder is skipped
                meta["path"] = str(d / "model.pkl")
                meta["dir"] = d.name
                meta["size_bytes"] = self.folder_size(d)
                created = meta.get("created_ns")
                if not isinstance(created, int):
                    try:
                        created = d.stat().st_mtime_ns
                    except OSError:
                        created = 0
                found.append((created, d.name, meta))
        found.sort(key=lambda item: (item[0], item[1]))
        return [meta for _, _, meta in found]

    @staticmethod
    def folder_size(folder: Path) -> int:
        """Bytes a model folder takes on disk (the model plus its metadata)."""
        total = 0
        try:
            for f in Path(folder).iterdir():
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    pass
        except OSError:
            pass
        return total

    def model_dir(self, name: str):
        """The folder of the registry model called `name`, or None. Only a plain
        folder name directly under the models folder that holds a model counts, so
        a request can never reach outside it ("..", separators, a drive)."""
        name = str(name or "")
        if (not name or name in (".", "..") or name != Path(name).name
                or "/" in name or "\\" in name or ":" in name):
            return None
        d = self.base_dir / name
        if d.is_symlink() or not d.is_dir():
            return None
        if not ((d / "model.pkl").exists() or (d / "metadata.json").exists()):
            return None
        return d

    def delete(self, name: str) -> int:
        """Delete one model folder. Returns the bytes freed; FileNotFoundError if
        there is no such model. Never touches a recording."""
        d = self.model_dir(name)
        if d is None:
            raise FileNotFoundError(name)
        freed = self.folder_size(d)
        shutil.rmtree(d)
        return freed
