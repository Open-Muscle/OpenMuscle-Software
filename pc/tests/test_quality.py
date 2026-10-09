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


# ---------------------------------------------------------------------------
# Take verdict: one test per reason (evaluate_take)
# ---------------------------------------------------------------------------

from openmuscle.quality import (  # noqa: E402
    LabelActivityTracker, evaluate_take, quality_of, scan_capture_csv,
    summarize_take, worst_band,
)

GOOD = dict(rows=600, duration_s=60.0, has_labeler=True, match_rate=0.98,
            matched=590, sensor_frames_seen=600, flat_cells=2, cells_total=60,
            band_flat=False, label_cols=5, label_cols_moved=5,
            label_width_mismatch=0, hand_lost_rows=0)


def _codes(q):
    return [f["code"] for f in q["flags"]]


def test_clean_take_is_good():
    q = evaluate_take(GOOD)
    assert q["verdict"] == "good" and q["reasons"] == [] and q["flags"] == []


def test_band_flat_is_bad_with_plain_reason():
    q = evaluate_take(dict(GOOD, band_flat=True, flat_cells=52))
    assert q["verdict"] == "bad"
    assert q["reasons"] == ["Band did not move: 52 of 60 cells flat. Was it on the arm?"]
    assert _codes(q) == ["weak_signal"]


def test_low_match_rate_thresholds():
    assert evaluate_take(dict(GOOD, match_rate=0.41))["verdict"] == "bad"
    mid = evaluate_take(dict(GOOD, match_rate=0.75))
    assert mid["verdict"] == "check"
    assert mid["reasons"] == ["Labels matched only 75% of frames."]
    assert evaluate_take(dict(GOOD, match_rate=0.9))["verdict"] == "good"


def test_labels_never_arrived_is_bad():
    q = evaluate_take(dict(GOOD, matched=0, match_rate=0.0, sensor_frames_seen=100))
    assert q["verdict"] == "bad" and _codes(q) == ["no_labels"]


def test_sensor_only_take_is_check_not_bad():
    q = evaluate_take(dict(GOOD, has_labeler=False, match_rate=0.0, matched=0,
                           label_cols=0, label_cols_moved=0))
    assert q["verdict"] == "check" and _codes(q) == ["no_labels"]


def test_short_take_flag():
    q = evaluate_take(dict(GOOD, duration_s=6.2, rows=120))
    assert q["verdict"] == "check" and _codes(q) == ["short"]
    assert q["reasons"][0].startswith("Only 6 seconds recorded")
    few = evaluate_take(dict(GOOD, duration_s=30.0, rows=12))
    assert _codes(few) == ["short"] and "12 rows" in few["reasons"][0]


def test_empty_take_is_bad():
    q = evaluate_take(dict(GOOD, rows=0, duration_s=0.0))
    assert q["verdict"] == "bad" and _codes(q) == ["empty"]


def test_idle_labeler_flag():
    q = evaluate_take(dict(GOOD, label_cols_moved=0))
    assert q["verdict"] == "check" and _codes(q) == ["idle_labeler"]
    # One of five columns moving is a (poor) labeler, not an idle one.
    assert evaluate_take(dict(GOOD, label_cols_moved=1))["verdict"] == "good"


def test_hand_lost_flag_levels():
    mism = evaluate_take(dict(GOOD, label_width_mismatch=3, hand_lost_rows=3))
    assert mism["verdict"] == "check" and _codes(mism) == ["hand_lost"]
    assert "3 of 600 frames" in mism["reasons"][0]
    most = evaluate_take(dict(GOOD, hand_lost_rows=400))
    assert most["verdict"] == "bad" and _codes(most) == ["hand_lost"]
    # Tiny fraction with no padded rows stays quiet.
    assert evaluate_take(dict(GOOD, hand_lost_rows=5))["verdict"] == "good"


def test_worst_reason_listed_first():
    q = evaluate_take(dict(GOOD, duration_s=4.0, band_flat=True, flat_cells=60))
    assert _codes(q) == ["weak_signal", "short"]


def test_live_take_is_not_nagged_early():
    q = evaluate_take(dict(GOOD, rows=0, duration_s=1.0, label_cols_moved=0, live=True))
    assert q["verdict"] == "good"
    # ...but a flat band is flagged mid-recording.
    assert evaluate_take(dict(GOOD, band_flat=True, live=True))["verdict"] == "bad"


def test_label_tracker_ignores_jitter_on_large_values():
    t = LabelActivityTracker()
    for v in (2000, 2003, 2001, 2005):          # ADC jitter under 1% of magnitude
        t.update([v, 0.5])
    t.update([2000, 0.9])                        # second column really moves
    assert t.n_cols == 2 and t.moved_cols() == 1


def test_worst_band_picks_least_engaged_band():
    flat, live = CellActivityTracker(), CellActivityTracker()
    for i in range(30):
        flat.update([100.0] * 60)
        live.update([100.0 + 40 * (i % 5)] * 60)
    w = worst_band({"left": live, "right": flat})
    assert w["band_flat"] is True and w["flat_cells"] == 60


def test_summarize_and_quality_of_round_trip():
    summ = summarize_take(dict(GOOD, band_flat=True, flat_cells=55))
    q = quality_of(summ)
    assert q["verdict"] == "bad" and q["reasons"] and q["flags"][0]["code"] == "weak_signal"
    assert summ["flat_cells"] == 55
    assert quality_of({"rows": 10}) is None      # pre-quality meta


def test_scan_of_dud_fixture_is_bad():
    stats = scan_capture_csv(FIXTURE)
    assert stats["rows"] >= 20 and stats["flat_cells"] >= 50
    assert stats["band_flat"] is True
    q = evaluate_take(stats)
    assert q["verdict"] == "bad"
    assert q["reasons"][0].startswith("Band did not move: ")


def test_scan_v2_csv_judges_worst_band(tmp_path):
    import csv as _csv
    p = tmp_path / "bi.csv"
    with open(p, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["ts_hub_ms", "role", "device_id", "R0C0", "R0C1", "label_0"])
        for i in range(40):
            w.writerow([1000 + 50 * i, "left", "L", 10 + 20 * (i % 4), 5 + 30 * (i % 3), i * 0.1])
            w.writerow([1000 + 50 * i, "right", "R", 7, 7, i * 0.1])    # flat band
    stats = scan_capture_csv(p)
    assert stats["band_flat"] is True and stats["rows"] == 80
    assert stats["duration_s"] == 2.0
