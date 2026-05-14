# om_settings.py - Unified settings manager for all OpenMuscle devices
#
# Consolidates the best of both patterns:
# - FlexGrid V1: simple static load/save
# - LASK5/SensorBand: instance-based with dict-like access and history tracking
#
# Also performs a one-shot rename of legacy keys (camelCase + UPPERCASE PIN
# suffixes from the monolithic firmware era) -> snake_case on load. This means
# a settings.json pulled off an older device just works after the first boot
# of modular firmware.

import json
import uos

# Legacy -> modern key renames. Generic across devices; harmless if a key
# isn't present on a given device. Kept here (not per-device) so the same
# settings.json from any pre-modular firmware migrates cleanly.
LEGACY_KEY_MAP = {
    "SSID":            "wifi_ssid",
    "Pass":            "wifi_password",
    "PCIP":            "udp_target_ip",
    "device_name":     "device_id",
    "sclPIN":          "scl_pin",
    "sdaPIN":          "sda_pin",
    "oledWIDTH":       "oled_width",
    "oledHEIGHT":      "oled_height",
    "ledPIN":          "led_pin",
    "startPIN":        "start_pin",
    "selectPIN":       "select_pin",
    "upPIN":           "up_pin",
    "downPIN":         "down_pin",
    "joystick_xPIN":   "joystick_x_pin",
    "joystick_yPIN":   "joystick_y_pin",
    "Joystick_SW":     "joystick_sw_pin",
}


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

    def _migrate_legacy_keys(self):
        """Rename any legacy keys in self._data to their modern names.

        Returns True if anything was renamed (caller should persist).
        Modern key wins if both happen to be present.
        """
        changed = False
        for old, new in LEGACY_KEY_MAP.items():
            if old in self._data:
                if new not in self._data:
                    self._data[new] = self._data[old]
                del self._data[old]
                changed = True
        return changed

    def _backfill_defaults(self):
        """Add any default keys missing from loaded data without overwriting
        the user's existing values. Returns True if anything was added."""
        changed = False
        for k, v in self.defaults.items():
            if k not in self._data:
                self._data[k] = v
                changed = True
        return changed

    def _load_or_init(self):
        try:
            with open(self.path, "r") as f:
                self._data = json.load(f)
        except Exception:
            self.is_first_run = True
            self._data = dict(self.defaults)
            self.save()
            return

        # Existing settings.json: migrate legacy keys + backfill any new defaults.
        # Persist if either changed so subsequent boots have a clean schema.
        dirty = False
        if self._migrate_legacy_keys():
            dirty = True
        if self._backfill_defaults():
            dirty = True
        if dirty:
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
