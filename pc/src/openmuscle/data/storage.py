"""CSV capture writer for recording paired sensor + label data."""

import csv
import os
import time
from pathlib import Path


class CaptureWriter:
    """Writes matched FlexGrid + label data to CSV files.

    The CSV format uses columns:
        timestamp, R0C0, R0C1, ..., R3C15, label_0, label_1, label_2, label_3
    """

    def __init__(self, output_path: str = None, matrix_rows: int = 4,
                 matrix_cols: int = 16, label_count: int = 4):
        if output_path is None:
            os.makedirs("data/raw/merged", exist_ok=True)
            output_path = f"data/raw/merged/capture_{int(time.time())}.csv"

        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        sensor_cols = [f"R{r}C{c}" for r in range(matrix_rows) for c in range(matrix_cols)]
        label_cols = [f"label_{i}" for i in range(label_count)]

        self._file = open(self.path, "w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(["timestamp"] + sensor_cols + label_cols)
        self._count = 0

    def write_row(self, timestamp: float, sensor_values: list, label_values: list):
        self._writer.writerow([timestamp] + sensor_values + label_values)
        self._count += 1

    @property
    def row_count(self) -> int:
        return self._count

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
