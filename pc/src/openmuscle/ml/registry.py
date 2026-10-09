"""Model save/load/version management with metadata tracking."""

import json
import os
import pickle
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
        same second) can never write into the same directory. Names still sort
        in the order they were created, which the "newest model" lookups rely on.
        `extra` is merged into metadata.json at the top level (provenance such as
        participant and band ids).
        """
        now = datetime.now()
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
        """List all models in the registry with their metadata, oldest first."""
        models = []
        for d in sorted(self.base_dir.iterdir()):
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
                models.append(meta)
        return models
