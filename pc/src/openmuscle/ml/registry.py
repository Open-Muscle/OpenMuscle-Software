"""Model save/load/version management with metadata tracking."""

import json
import os
import pickle
from datetime import datetime
from pathlib import Path


class ModelRegistry:
    """Save, load, and list trained models with metadata.

    Each model is stored as a directory containing:
        - model.pkl: the trained sklearn model
        - metadata.json: training metrics, columns, date
    """

    def __init__(self, base_dir: str = "data/models"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, model, name: str, metrics: dict = None) -> Path:
        """Save a model with metadata. Returns the model.pkl path."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_dir = self.base_dir / f"{name}_{ts}"
        model_dir.mkdir(parents=True, exist_ok=True)

        model_path = model_dir / "model.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        meta = {
            "name": name,
            "created": ts,
            "model_type": type(model).__name__,
            "metrics": metrics or {},
        }
        with open(model_dir / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2)

        return model_path

    def load(self, path: str):
        """Load a model from a .pkl file."""
        with open(path, "rb") as f:
            return pickle.load(f)

    def list_models(self) -> list[dict]:
        """List all models in the registry with their metadata."""
        models = []
        for d in sorted(self.base_dir.iterdir()):
            if not d.is_dir():
                continue
            meta_path = d / "metadata.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)
                meta["path"] = str(d / "model.pkl")
                models.append(meta)
        return models
