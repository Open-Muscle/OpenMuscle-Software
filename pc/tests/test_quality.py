"""Flat-cell dud detection (board #0311.4 / #0314.2): the exact dud capture
capture_1779731309 (band not engaged; 50/60 cells with range < 5 yet a
normal-looking file) MUST be flagged, and an engaged take must not be."""

import csv
import random
from pathlib import Path

from openmuscle.quality import CellActivityTracker

FIXTURE = Path(__file__).parent / "fixtures" / "capture_1779731309_dud.csv"


def feed_csv_sensor_rows(tracker, path):
    with open(path) as f:
        reader = csv.reader(f)
        header = next(reader)
        idx = [i for i, c in enumerate(header)
               if c.startswith("R") and "C" in c[1:]]
        for row in reader:
            tracker.update([float(row[i]) for i in idx])
    return tracker


def test_dud_capture_1779731309_is_flagged_flat():
    # The regression fixture is the REAL dud file, byte-copied from the capture.
    t = feed_csv_sensor_rows(CellActivityTracker(), FIXTURE)
    assert t.n_cells == 60
    assert t.frames >= 20                        # enough frames for a verdict
    assert t.flat_cells() >= 50, t.flat_cells()  # the audit's 50/60 finding
    assert t.is_flat_take() is True              # -> verdict BAD in the UI


def test_engaged_take_not_flagged():
    rng = random.Random(42)
    t = CellActivityTracker()
    for _ in range(40):
        # A worn band: cells wander well beyond the noise floor.
        t.update([200 + rng.uniform(-40, 40) for _ in range(60)])
    assert t.is_flat_take() is False
    assert t.flat_cells() < 10


def test_no_verdict_before_min_frames():
    t = CellActivityTracker()
    for _ in range(5):                            # brand-new recording
        t.update([100.0] * 60)                    # everything flat so far
    assert t.is_flat_take() is False              # too early to call


def test_tracker_tolerates_empty_and_ragged_rows():
    t = CellActivityTracker()
    t.update([])
    t.update([1.0, 2.0, 3.0])
    t.update([1.0, 2.0])                          # shorter row: extra ignored
    assert t.n_cells == 3
    assert t.frames == 2                          # empty row not counted
