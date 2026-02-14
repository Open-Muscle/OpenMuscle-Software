# om_settings.py - Unified settings manager for all OpenMuscle devices
#
# Consolidates the best of both patterns:
# - FlexGrid V1: simple static load/save
# - LASK5/SensorBand: instance-based with dict-like access and history tracking

import json
import uos

class Settings:
    def __init__(self, defaults, path="config/settings.json"):
        """
        Args:
            defaults: dict of default settings values
            path: filesystem path for persistent JSON storage
        """
        self.path = path
        self.defaults = defaults
        self._data = {}
        self.is_first_run = False
        self._ensure_dir()
        self._load_or_init()

    def _ensure_dir(self):
        parts = self.path.rsplit("/", 1)
        if len(parts) == 2:
            try:
                uos.stat(parts[0])
            except OSError:
                uos.mkdir(parts[0])

    def _load_or_init(self):
        try:
            with open(self.path, "r") as f:
                self._data = json.load(f)
        except Exception:
            self.is_first_run = True
            self._data = dict(self.defaults)
            self.save()

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def to_dict(self):
        return dict(self._data)
