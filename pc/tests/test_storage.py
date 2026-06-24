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
