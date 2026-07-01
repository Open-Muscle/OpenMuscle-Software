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

    # IMU columns (board: LASK5 gyro + flexgrid band orientation into the
    # training set). Two groups per row: the SENSOR band that produced the row,
    # and the matched LABELER (e.g. a LASK5 whose gyro is the supination
    # ground-truth). Appended AFTER the labels so name-addressable readers stay
    # backward compatible and the label-count inference is unaffected.
    _IMU_SENSOR_COLS = ["imu_gx", "imu_gy", "imu_gz", "imu_ax", "imu_ay", "imu_az"]
    _IMU_LABEL_COLS = ["lbl_imu_gx", "lbl_imu_gy", "lbl_imu_gz",
                       "lbl_imu_ax", "lbl_imu_ay", "lbl_imu_az"]

    # Forearm-orientation ground-truth derived from the matched Quest hand
    # (forearm.py): a gravity-relative roll angle + a palm-up flag (board
    # #0207/#0228, ratified single-column). Appended AFTER the labels + IMU so
    # name-addressable readers stay backward compatible and label-count
    # inference is unaffected. Omitted when the capture has no Quest labeler.
    _FOREARM_COLS = ["forearm_roll_deg", "palm_up"]

    # Ratified canonical LABEL columns (DATA_SCHEMA.md #0302): the schema-aware
    # training target every source maps onto. Tier-1 shared core = per-finger
    # flexion NORMALIZED [0,1]; Tier-2 Quest-only = per-joint flexion in degrees
    # (empty/NaN-masked when a source cannot fill them). Opt-in + appended AFTER
    # the raw label_* / imu / forearm columns, so the schema-v2 byte golden and
    # name-addressable readers stay backward compatible; the raw label_* stay in
    # the CSV + .jsonl for re-derivation.
    _CANONICAL_LABEL_COLS = [
        "lbl_flex_thumb", "lbl_flex_index", "lbl_flex_middle", "lbl_flex_ring", "lbl_flex_pinky",
        "lbl_ang_thumb_mcp", "lbl_ang_thumb_ip",
        "lbl_ang_index_mcp", "lbl_ang_index_pip",
        "lbl_ang_middle_mcp", "lbl_ang_middle_pip",
        "lbl_ang_ring_mcp", "lbl_ang_ring_pip",
        "lbl_ang_pinky_mcp", "lbl_ang_pinky_pip",
    ]

    def __init__(self, output_path: str = None, matrix_rows: int = 4,
                 matrix_cols: int = 16, label_count: Optional[int] = 4,
                 schema_version: str = "v1", with_imu: bool = False,
                 with_forearm: bool = False, with_canonical: bool = False):
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
        # Append raw-IMU columns (sensor band + matched labeler). v2 only; opt-in
        # so the schema-v2 byte golden (no IMU) stays the default.
        self._with_imu = bool(with_imu) and schema_version == "v2"
        # Append forearm-orientation columns (from the matched Quest hand). v2
        # only; on when the capture has a Quest labeler (omit-when-no-quest).
        self._with_forearm = bool(with_forearm) and schema_version == "v2"
        # Append the ratified canonical lbl_* label columns (derived at capture
        # time). v2 only; opt-in so the byte golden (minimal v2) stays the default.
        self._with_canonical = bool(with_canonical) and schema_version == "v2"

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
        imu_cols = (self._IMU_SENSOR_COLS + self._IMU_LABEL_COLS
                    if self._with_imu else [])
        forearm_cols = self._FOREARM_COLS if self._with_forearm else []
        canonical_cols = self._CANONICAL_LABEL_COLS if self._with_canonical else []
        self._writer.writerow(
            lead + sensor_cols + label_cols + imu_cols + forearm_cols + canonical_cols)
        self._label_count = label_count
        self._header_written = True

    @staticmethod
    def _imu_cells(imu) -> list:
        """Flatten {gyro:[gx,gy,gz], accel:[ax,ay,az]} to 6 cells. Absent or
        short -> empty cells (distinguishable from a real 0 reading)."""
        if not isinstance(imu, dict):
            return [""] * 6

        def three(key):
            v = list(imu.get(key) or [])[:3]
            return v + [""] * (3 - len(v))

        return three("gyro") + three("accel")

    @staticmethod
    def _forearm_cells(forearm) -> list:
        """Flatten a (forearm_roll_deg, palm_up) tuple to 2 cells. None (no Quest
        match / too few joints) -> empty cells, distinguishable from a real 0."""
        if not forearm:
            return ["", ""]
        roll, palm_up = forearm[0], forearm[1]
        return [roll, int(bool(palm_up))]

    def _canonical_cells(self, canonical) -> list:
        """Flatten a canonical-labels dict to the fixed _CANONICAL_LABEL_COLS
        order. A missing / None DOF -> empty cell (pandas reads it as NaN = the
        masked target), distinguishable from a real 0.0 (a fully-extended finger).
        Present values (incl. 0.0) are written as-is."""
        canonical = canonical or {}
        return ["" if canonical.get(c) is None else canonical.get(c)
                for c in self._CANONICAL_LABEL_COLS]

    def write_row(self, timestamp: float, sensor_values: list, label_values: list):
        if not self._header_written:
            count = (self._label_count if self._label_count is not None
                     else len(label_values))
            self._write_header(count)
        self._writer.writerow([timestamp] + sensor_values + label_values)
        self._count += 1

    def write_row_v2(self, ts_hub_ms: int, role: str, device_id: str,
                     sensor_values: list, label_values: list,
                     sensor_imu=None, label_imu=None, forearm=None, canonical=None):
        """Write one schema-v2 row: ts_hub_ms, role, device_id, then row-major
        sensor features and labels. `sensor_values` MUST already be flattened
        row-major (R{r}C{c}, r outer) to match the header + the byte golden.
        The writer is role-agnostic: bilateral captures are just interleaved
        rows with different role/device_id, written by the caller in arrival
        order.

        sensor_imu / label_imu are {gyro,accel} dicts (or None) for the band
        that produced the row and the matched labeler; only written when the
        writer was built with_imu=True (else ignored).

        forearm is a (forearm_roll_deg, palm_up) tuple (or None) derived from the
        matched Quest hand; only written when built with_forearm=True."""
        if self._schema_version != "v2":
            raise ValueError("write_row_v2 requires schema_version='v2'")
        if not self._header_written:
            count = (self._label_count if self._label_count is not None
                     else len(label_values))
            self._write_header(count)
        row = [ts_hub_ms, role, device_id] + sensor_values + label_values
        if self._with_imu:
            row += self._imu_cells(sensor_imu) + self._imu_cells(label_imu)
        if self._with_forearm:
            row += self._forearm_cells(forearm)
        if self._with_canonical:
            row += self._canonical_cells(canonical)
        self._writer.writerow(row)
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
