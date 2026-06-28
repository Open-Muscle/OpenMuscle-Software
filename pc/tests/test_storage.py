"""Tests for the CSV capture writer's lazy header behavior.

The writer used to emit its header in __init__ from a constructor-provided
label_count. The Quest hand-tracking ingest path doesn't know the label
width until the first packet arrives (Quest 3 = 25 joints * 7 floats = 175,
but the constant may shift), so label_count became Optional[int] and the
header is deferred until the first write_row. These tests pin that contract.
"""

import tempfile
from pathlib import Path

from openmuscle.data.storage import CaptureWriter


def _read_csv_lines(path):
    with open(path) as f:
        return [line.rstrip("\n").split(",") for line in f if line.strip()]


class TestCaptureWriterLazyHeader:
    def test_label_count_inferred_from_first_row(self):
        """label_count=None lets the writer derive width from the first row.

        This is the Quest path: we don't know the joint count at start time.
        """
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = Path(tf.name)
        w = CaptureWriter(output_path=str(path), matrix_rows=4,
                          matrix_cols=15, label_count=None)
        w.write_row(1.0, [0] * 60, list(range(175)))
        w.close()

        rows = _read_csv_lines(path)
        header = rows[0]
        assert len(header) == 1 + 60 + 175
        assert header[0] == "timestamp"
        assert header[1 + 60 + 174] == "label_174"
        assert w.label_count == 175
        path.unlink()

    def test_explicit_label_count_still_works(self):
        """LASK5 callers pass label_count=4 and that's what we honor."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = Path(tf.name)
        w = CaptureWriter(output_path=str(path), matrix_rows=4,
                          matrix_cols=16, label_count=4)
        w.write_row(1.0, [0] * 64, [10, 20, 30, 40])
        w.close()

        header = _read_csv_lines(path)[0]
        assert len(header) == 1 + 64 + 4
        assert header[-1] == "label_3"
        path.unlink()

    def test_close_on_empty_capture_writes_header(self):
        """Empty captures (no row ever paired) still produce a non-empty file.

        Without this, downstream pandas readers trip on a zero-byte CSV. The
        writer falls back to the constructor hint (default 4) or 0 if None
        was passed.
        """
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = Path(tf.name)
        w = CaptureWriter(output_path=str(path), matrix_rows=4,
                          matrix_cols=15, label_count=4)
        w.close()   # no write_row call

        rows = _read_csv_lines(path)
        assert len(rows) == 1, "should have header only, no data rows"
        header = rows[0]
        assert len(header) == 1 + 60 + 4
        assert header[-1] == "label_3"
        path.unlink()

    def test_close_on_empty_capture_with_none_count(self):
        """If label_count was None and no row was written, falls back to 0."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = Path(tf.name)
        w = CaptureWriter(output_path=str(path), matrix_rows=4,
                          matrix_cols=15, label_count=None)
        w.close()

        rows = _read_csv_lines(path)
        assert len(rows) == 1
        header = rows[0]
        # 1 timestamp + 60 sensor + 0 label columns
        assert len(header) == 1 + 60
        path.unlink()


class TestCaptureWriterV2Golden:
    """The PC CaptureWriter v2 output must match the schema-v2 byte golden
    (OpenMuscle-Connect tools/make_golden_csv_v2.py, board #0073) byte-for-byte,
    so the phone and PC writers stay interchangeable. Features are flattened with
    the canonical row-major helper (flat_sensor_values, fixed in 0bbd6e4)."""

    # Pinned canonical bytes copied from the golden generator's EXPECTED_*.
    EXPECTED_SINGLE = (
        b"ts_hub_ms,role,device_id,R0C0,R0C1,R1C0,R1C1,label_0,label_1\r\n"
        b"1718000000000,left,fg-left,12,18,20,25,1.0,0.5\r\n"
        b"1718000000033,left,fg-left,13,19,21,24,0.8,0.5\r\n"
    )
    EXPECTED_BILATERAL = (
        b"ts_hub_ms,role,device_id,R0C0,R0C1,R1C0,R1C1,label_0,label_1\r\n"
        b"1718000000000,left,fg-left,12,18,20,25,1.0,0.5\r\n"
        b"1718000000007,right,fg-right,30,28,22,19,1.0,0.5\r\n"
        b"1718000000033,left,fg-left,13,19,21,24,0.8,0.5\r\n"
        b"1718000000040,right,fg-right,31,27,23,18,0.8,0.5\r\n"
    )

    @staticmethod
    def _rowmajor(matrix):
        # Flatten via the production canonical helper (row-major), not a local copy.
        from openmuscle.protocol.schema import OpenMusclePacket
        return OpenMusclePacket(
            version="1.0", device_type="flexgrid", device_id="x",
            timestamp_ms=0, data={"matrix": matrix},
        ).flat_sensor_values()

    def _write(self, path, rows_data):
        rm = self._rowmajor
        w = CaptureWriter(output_path=str(path), matrix_rows=2, matrix_cols=2,
                          label_count=2, schema_version="v2")
        for ts, role, dev, matrix, labels in rows_data:
            w.write_row_v2(ts, role, dev, rm(matrix), labels)
        w.close()
        with open(path, "rb") as f:
            return f.read()

    def test_single_source_matches_golden(self):
        with tempfile.TemporaryDirectory() as d:
            got = self._write(Path(d) / "v2single.csv", [
                (1718000000000, "left", "fg-left", [[12, 20], [18, 25]], [1.0, 0.5]),
                (1718000000033, "left", "fg-left", [[13, 21], [19, 24]], [0.8, 0.5]),
            ])
        assert got == self.EXPECTED_SINGLE

    def test_bilateral_matches_golden(self):
        with tempfile.TemporaryDirectory() as d:
            got = self._write(Path(d) / "v2bi.csv", [
                (1718000000000, "left",  "fg-left",  [[12, 20], [18, 25]], [1.0, 0.5]),
                (1718000000007, "right", "fg-right", [[30, 22], [28, 19]], [1.0, 0.5]),
                (1718000000033, "left",  "fg-left",  [[13, 21], [19, 24]], [0.8, 0.5]),
                (1718000000040, "right", "fg-right", [[31, 23], [27, 18]], [0.8, 0.5]),
            ])
        assert got == self.EXPECTED_BILATERAL

    def test_write_row_v2_requires_v2_schema(self):
        import pytest
        with tempfile.TemporaryDirectory() as d:
            w = CaptureWriter(output_path=str(Path(d) / "v1.csv"),
                              matrix_rows=2, matrix_cols=2, label_count=2)
            with pytest.raises(ValueError):
                w.write_row_v2(1, "left", "fg", [1, 2, 3, 4], [0.0, 0.0])
            w.close()


class TestImuColumns:
    """with_imu appends raw-IMU columns after the labels: imu_* for the sensor
    band, lbl_imu_* for the matched labeler (e.g. a LASK5 gyro for supination)."""

    IMU_HEADER = ["imu_gx", "imu_gy", "imu_gz", "imu_ax", "imu_ay", "imu_az",
                  "lbl_imu_gx", "lbl_imu_gy", "lbl_imu_gz",
                  "lbl_imu_ax", "lbl_imu_ay", "lbl_imu_az"]

    def test_header_and_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "imu.csv"
            w = CaptureWriter(output_path=str(path), matrix_rows=1, matrix_cols=2,
                              label_count=2, schema_version="v2", with_imu=True)
            w.write_row_v2(1000, "left", "fg-1", [10, 20], [0.0, 1.0],
                           sensor_imu={"gyro": [1, 2, 3], "accel": [4, 5, 6]},
                           label_imu={"gyro": [7, 8, 9], "accel": [10, 11, 12]})
            w.write_row_v2(1001, "left", "fg-1", [11, 21], [0.0, 1.0])  # no imu
            w.close()
            lines = _read_csv_lines(path)
        assert lines[0][-12:] == self.IMU_HEADER
        assert lines[1][-12:] == ["1", "2", "3", "4", "5", "6",
                                  "7", "8", "9", "10", "11", "12"]
        assert lines[2][-12:] == [""] * 12          # absent IMU -> empty cells

    def test_short_imu_padded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "short.csv"
            w = CaptureWriter(output_path=str(path), matrix_rows=1, matrix_cols=1,
                              label_count=1, schema_version="v2", with_imu=True)
            # accel missing, gyro short -> padded to 6 cells, label imu absent.
            w.write_row_v2(1, "left", "fg", [5], [0.0], sensor_imu={"gyro": [9]})
            w.close()
            lines = _read_csv_lines(path)
        assert lines[1][-12:] == ["9", "", "", "", "", "", "", "", "", "", "", ""]

    def test_default_off_no_imu_columns(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "off.csv"
            w = CaptureWriter(output_path=str(path), matrix_rows=1, matrix_cols=2,
                              label_count=2, schema_version="v2")  # default off
            w.write_row_v2(1, "left", "fg", [1, 2], [0.0, 0.0],
                           sensor_imu={"gyro": [1, 2, 3]})          # ignored
            w.close()
            header = _read_csv_lines(path)[0]
        assert "imu_gx" not in header
        assert header[-1] == "label_1"

    def test_v1_ignores_with_imu(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "v1imu.csv"
            w = CaptureWriter(output_path=str(path), matrix_rows=1, matrix_cols=2,
                              label_count=1, schema_version="v1", with_imu=True)
            w.write_row(1.0, [1, 2], [0.0])
            w.close()
            header = _read_csv_lines(path)[0]
        assert "imu_gx" not in header     # IMU columns are v2-only


class TestForearmColumns:
    """with_forearm appends forearm_roll_deg + palm_up (gravity-relative
    orientation from the matched Quest hand) AFTER the labels + any IMU cols."""

    def test_header_and_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fa.csv"
            w = CaptureWriter(output_path=str(path), matrix_rows=1, matrix_cols=2,
                              label_count=2, schema_version="v2", with_forearm=True)
            w.write_row_v2(1000, "left", "fg-1", [10, 20], [0.0, 1.0],
                           forearm=(42.5, True))
            w.write_row_v2(1001, "left", "fg-1", [11, 21], [0.0, 1.0])  # no match
            w.close()
            lines = _read_csv_lines(path)
        assert lines[0][-2:] == ["forearm_roll_deg", "palm_up"]
        assert lines[1][-2:] == ["42.5", "1"]            # palm_up bool -> 1
        assert lines[2][-2:] == ["", ""]                 # no forearm -> empty cells

    def test_palm_down_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fa0.csv"
            w = CaptureWriter(output_path=str(path), matrix_rows=1, matrix_cols=1,
                              label_count=1, schema_version="v2", with_forearm=True)
            w.write_row_v2(1, "right", "fg", [5], [0.0], forearm=(-170.0, False))
            w.close()
            lines = _read_csv_lines(path)
        assert lines[1][-2:] == ["-170.0", "0"]

    def test_forearm_after_imu_when_both(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "both.csv"
            w = CaptureWriter(output_path=str(path), matrix_rows=1, matrix_cols=1,
                              label_count=1, schema_version="v2",
                              with_imu=True, with_forearm=True)
            w.write_row_v2(1, "left", "fg", [5], [0.0],
                           sensor_imu={"gyro": [1, 2, 3], "accel": [4, 5, 6]},
                           forearm=(10.0, True))
            w.close()
            header = _read_csv_lines(path)[0]
        # forearm columns are the final two, after all imu columns.
        assert header[-2:] == ["forearm_roll_deg", "palm_up"]
        assert header.index("imu_gx") < header.index("forearm_roll_deg")

    def test_default_off_no_forearm_columns(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "off.csv"
            w = CaptureWriter(output_path=str(path), matrix_rows=1, matrix_cols=2,
                              label_count=2, schema_version="v2")  # default off
            w.write_row_v2(1, "left", "fg", [1, 2], [0.0, 0.0], forearm=(1.0, True))
            w.close()
            header = _read_csv_lines(path)[0]
        assert "forearm_roll_deg" not in header
        assert header[-1] == "label_1"
