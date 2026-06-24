"""CSV capture writer for recording paired sensor + label data."""

import csv
import os
import time
from pathlib import Path
from typing import Optional


class CaptureWriter:
    """Writes matched FlexGrid + label data to CSV files.

    The CSV format uses columns:
        timestamp, R0C0, R0C1, ..., R3C15, label_0, label_1, ..., label_N

    Header is written lazily on the first `write_row` call so the number
    of label columns can be inferred from that row's label_values length.
    Callers that know the count up front can pass it via `label_count`;
    callers that don't (e.g. Quest hand tracking, whose joint vector is
    not known until the first label packet arrives) pass `label_count=None`
    and the writer derives it from the first row.
    """

    def __init__(self, output_path: str = None, matrix_rows: int = 4,
                 matrix_cols: int = 16, label_count: Optional[int] = 4,
                 schema_version: str = "v1"):
        if output_path is None:
            os.makedirs("data/raw/merged", exist_ok=True)
            output_path = f"data/raw/merged/capture_{int(time.time())}.csv"

        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._matrix_rows = matrix_rows
        self._matrix_cols = matrix_cols
        self._label_count: Optional[int] = label_count   # None = infer on first row
        # "v1" = `timestamp, R{r}C{c}.., label_*` (single source).
        # "v2" = `ts_hub_ms, role, device_id, R{r}C{c}.., label_*` (multi-device,
        # one row per source frame). Byte-for-byte target is the schema-v2 golden
        # (OpenMuscle-Connect tools/make_golden_csv_v2.py, board #0073).
        self._schema_version = schema_version

        self._file = open(self.path, "w", newline="")
        self._writer = csv.writer(self._file)
        self._header_written = False
        self._count = 0

    def _write_header(self, label_count: int) -> None:
        sensor_cols = [f"R{r}C{c}" for r in range(self._matrix_rows)
                                     for c in range(self._matrix_cols)]
        label_cols = [f"label_{i}" for i in range(label_count)]
        if self._schema_version == "v2":
            lead = ["ts_hub_ms", "role", "device_id"]
        else:
            lead = ["timestamp"]
        self._writer.writerow(lead + sensor_cols + label_cols)
        self._label_count = label_count
        self._header_written = True

    def write_row(self, timestamp: float, sensor_values: list, label_values: list):
        if not self._header_written:
            count = (self._label_count if self._label_count is not None
                     else len(label_values))
            self._write_header(count)
        self._writer.writerow([timestamp] + sensor_values + label_values)
        self._count += 1

    def write_row_v2(self, ts_hub_ms: int, role: str, device_id: str,
                     sensor_values: list, label_values: list):
        """Write one schema-v2 row: ts_hub_ms, role, device_id, then row-major
        sensor features and labels. `sensor_values` MUST already be flattened
        row-major (R{r}C{c}, r outer) to match the header + the byte golden.
        The writer is role-agnostic: bilateral captures are just interleaved
        rows with different role/device_id, written by the caller in arrival
        order."""
        if self._schema_version != "v2":
            raise ValueError("write_row_v2 requires schema_version='v2'")
        if not self._header_written:
            count = (self._label_count if self._label_count is not None
                     else len(label_values))
            self._write_header(count)
        self._writer.writerow([ts_hub_ms, role, device_id]
                              + sensor_values + label_values)
        self._count += 1

    @property
    def row_count(self) -> int:
        return self._count

    @property
    def label_count(self) -> Optional[int]:
        """How many label columns the CSV has. None until the first row
        is written (or close() is called on an empty capture, which
        falls back to the constructor hint or 0)."""
        return self._label_count

    def close(self):
        if not self._header_written:
            # No row was ever paired -- still emit a header so consumers
            # don't trip on a zero-byte file. Use the constructor hint
            # if given, else 0 label columns (sensor-only capture).
            self._write_header(self._label_count if self._label_count is not None else 0)
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
