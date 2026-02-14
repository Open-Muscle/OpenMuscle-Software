# om_sensor.py - Abstract sensor interface for OpenMuscle devices
#
# All device-specific sensor implementations should inherit from this
# and implement read() at minimum.

class SensorInterface:
    """Base class for all sensor types."""

    def read(self):
        """
        Read current sensor data.

        Returns:
            dict matching the 'data' field of the standard packet schema.
            Example for flexgrid: {"matrix": [[...], ...], "rows": 4, "cols": 16}
            Example for lask5: {"values": [v0, v1, v2, v3]}
        """
        raise NotImplementedError

    def calibrate(self, settings):
        """
        Run calibration routine and store results in settings.

        Args:
            settings: Settings instance to persist calibration data
        """
        raise NotImplementedError
