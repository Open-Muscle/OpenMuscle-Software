import os
import json
from collections import deque

class SettingsManager:
    def __init__(self, filename='settings.json', defaults=None):
        """
        Initializes the SettingsManager.

        :param filename: Name of the file to store settings.
        :param defaults: A dictionary of default settings.
        """
        self.filename = filename
        self.defaults = defaults if defaults else {}
        self.settings = {}
        self.history_keys = {}  # Stores keys and their history size
        self.is_first_run = not self._file_exists()

        if self.is_first_run:
            # First run: use defaults and save them
            self.settings = self.defaults.copy()
            self.save()
        else:
            # Not first run: load existing settings
            self.load()

    def _file_exists(self):
        """Checks if the settings file exists."""
        return self.filename in os.listdir()

    def save(self):
        """Saves the current settings to the file in JSON format."""
        try:
            # Convert deque objects to lists before saving
            serializable_settings = self._prepare_for_serialization(self.settings)
            with open(self.filename, 'w') as f:
                json.dump(serializable_settings, f)
        except Exception as e:
            print("Error saving settings:", e)

    def load(self):
        """Loads the settings from the file."""
        try:
            with open(self.filename, 'r') as f:
                loaded_settings = json.load(f)
                self.settings = self._restore_from_serialization(loaded_settings)
        except Exception as e:
            print("Error loading settings:", e)
            # Optionally, you can reinitialize settings or handle the error as needed

    def _prepare_for_serialization(self, obj):
        """Converts non-serializable objects (like deque) to serializable ones."""
        if isinstance(obj, deque):
            return list(obj)
        elif isinstance(obj, dict):
            return {k: self._prepare_for_serialization(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._prepare_for_serialization(i) for i in obj]
        else:
            return obj

    def _restore_from_serialization(self, obj):
        """Restores serialized objects to their original types."""
        if isinstance(obj, dict):
            restored = {}
            for k, v in obj.items():
                if k in self.history_keys:
                    # Restore to deque with maxlen
                    restored[k] = deque(v, maxlen=self.history_keys[k])
                else:
                    restored[k] = self._restore_from_serialization(v)
            return restored
        elif isinstance(obj, list):
            return [self._restore_from_serialization(i) for i in obj]
        else:
            return obj

    def set(self, key, value):
        """
        Sets a setting value. If the key is set to keep history, it appends to the history.

        :param key: The setting's key.
        :param value: The setting's value.
        """
        if key in self.history_keys:
            if key not in self.settings or not isinstance(self.settings[key], deque):
                self.settings[key] = deque(maxlen=self.history_keys[key])
            self.settings[key].append(value)
        else:
            self.settings[key] = value

    def get(self, key, default=None):
        """
        Gets a setting value.

        :param key: The setting's key.
        :param default: Default value if the key is not found.
        :return: The setting's value.
        """
        return self.settings.get(key, default)

    def get_existing_or_false(self, key):
        """
        Returns the stored object if it exists, or False otherwise.

        :param key: The setting's key.
        :return: The setting's value or False.
        """
        return self.settings.get(key, False)

    def key_exists(self, key):
        """
        Checks if a key exists in the settings.

        :param key: The setting's key.
        :return: True if key exists, False otherwise.
        """
        return key in self.settings

    def enable_history(self, key, n):
        """
        Enables history tracking for a specific key.

        :param key: The setting's key.
        :param n: Number of past values to keep.
        """
        self.history_keys[key] = n
        if key not in self.settings or not isinstance(self.settings[key], deque):
            self.settings[key] = deque(maxlen=n)

    def get_history(self, key):
        """
        Retrieves the history of a specific key.

        :param key: The setting's key.
        :return: A list of past values, or None if history is not enabled for the key.
        """
        if key in self.settings and isinstance(self.settings[key], deque):
            return list(self.settings[key])
        return None

    def __getitem__(self, key):
        """Allows dictionary-like access (settings['key'])."""
        return self.settings.get(key)

    def __setitem__(self, key, value):
        """Allows dictionary-like setting (settings['key'] = value)."""
        self.set(key, value)

    def __contains__(self, key):
        """Allows usage of 'in' keyword (key in settings)."""
        return key in self.settings
