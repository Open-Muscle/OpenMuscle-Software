"""Export bundle builder: a zip of a session's takes plus a plain README."""

import csv
import io
import json
import time
import types
import zipfile

import pytest

from openmuscle.data import export_bundle as eb
from openmuscle.data.storage import CaptureWriter

from .export_fixtures import band_packet, labeler_packet, make_state, record_take


@pytest.fixture
def env(tmp_path):
    """A session with two takes (one with a note), plus one loose take."""
    state = make_state(tmp_path / "captures")
    t0 = time.time()
    state._handle_packet(band_packet(t0, 0))
    state._handle_packet(labeler_packet(t0, 0))
    sess = state.start_session(name="baseline", wearer="P01", arm="left",
                               notes="SESSION-NOTE-TEXT")
    record_take(state, "first.csv", notes="TAKE-NOTE-TEXT")
    record_take(state, "second.csv")
    state.end_session()
    record_take(state, "loose.csv")
    return types.SimpleNamespace(
        state=state, captures=state.captures_dir, sessions=state.sessions_dir,
        sid=sess["id"], out=tmp_path / "out" / "bundle.zip", tmp=tmp_path)


def _names(path):
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def test_compact_session_bundle_inventory(env):
    report = eb.build_session_bundle(env.captures, env.sessions, env.out,
                                     session_id=env.sid)
    names = _names(env.out)
    assert names == {
        "README.txt", "session.json", "summary.csv",
        "takes/first.csv", "takes/first.meta.json",
        "takes/second.csv", "takes/second.meta.json",
    }
    assert set(report.files) == names
    assert report.takes == ["first.csv", "second.csv"]
    assert not any(n.endswith(".jsonl") for n in names)       # compact: no raw
    assert not any("model" in n for n in names)               # never models
    assert report.zip_bytes == env.out.stat().st_size
    assert report.uncompressed_bytes > 0
    with zipfile.ZipFile(env.out) as zf:
        assert zf.testzip() is None


def test_readme_mentions_every_included_file(env):
    eb.build_session_bundle(env.captures, env.sessions, env.out, session_id=env.sid,
                            include_raw=True)
    with zipfile.ZipFile(env.out) as zf:
        readme = zf.read("README.txt").decode("utf-8")
        for n in zf.namelist():
            assert n in readme, n + " is not described in README.txt"
    assert "P01" in readme
    assert "good" in readme and "check" in readme and "bad" in readme


def test_summary_has_one_row_per_take(env):
    eb.build_session_bundle(env.captures, env.sessions, env.out, session_id=env.sid)
    with zipfile.ZipFile(env.out) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read("summary.csv").decode("utf-8"))))
    assert [r["file"] for r in rows] == ["takes/first.csv", "takes/second.csv"]
    r = rows[0]
    assert r["participant_code"] == "P01"
    assert r["quality"] in ("good", "check", "bad")
    assert r["labeler"] == "LASK5 finger labeler"
    assert r["bands"] == "left"
    assert int(r["rows"]) > 0 and float(r["seconds"]) > 0
    assert "notes" not in r                                  # notes off by default


def test_raw_streams_only_when_asked(env):
    eb.build_session_bundle(env.captures, env.sessions, env.out, session_id=env.sid,
                            include_raw=True)
    names = _names(env.out)
    assert "takes/first.sensor.jsonl" in names
    assert "takes/first.label.jsonl" in names


def test_notes_are_left_out_unless_asked(env):
    eb.build_session_bundle(env.captures, env.sessions, env.out, session_id=env.sid)
    with zipfile.ZipFile(env.out) as zf:
        blob = b"".join(zf.read(n) for n in zf.namelist())
    assert b"TAKE-NOTE-TEXT" not in blob
    assert b"SESSION-NOTE-TEXT" not in blob

    eb.build_session_bundle(env.captures, env.sessions, env.out, session_id=env.sid,
                            include_notes=True)
    with zipfile.ZipFile(env.out) as zf:
        assert b"TAKE-NOTE-TEXT" in zf.read("takes/first.meta.json")
        assert b"SESSION-NOTE-TEXT" in zf.read("session.json")
        assert "TAKE-NOTE-TEXT" in zf.read("summary.csv").decode("utf-8")
        assert "notes" in zf.read("summary.csv").decode("utf-8").splitlines()[0]
    # The real files on disk are untouched either way.
    assert "TAKE-NOTE-TEXT" in (env.captures / "first.meta.json").read_text()


def test_loose_takes_get_a_synthesized_session(env):
    report = eb.build_session_bundle(env.captures, env.sessions, env.out,
                                     capture_names=["loose.csv", "first"])
    assert report.session_id is None
    with zipfile.ZipFile(env.out) as zf:
        sess = json.loads(zf.read("session.json"))
        assert sess["synthesized"] is True
        assert sorted(sess["captures"]) == ["first.csv", "loose.csv"]
        assert "takes/loose.csv" in zf.namelist()
        readme = zf.read("README.txt").decode()
    assert "not part of a session" in " ".join(readme.split())


def test_session_json_matches_the_session(env):
    eb.build_session_bundle(env.captures, env.sessions, env.out, session_id=env.sid)
    with zipfile.ZipFile(env.out) as zf:
        sess = json.loads(zf.read("session.json"))
    assert sess["id"] == env.sid and sess["wearer"] == "P01"
    assert sess["captures"] == ["first.csv", "second.csv"]
    assert sess["synthesized"] is False
    assert "context" in sess


@pytest.mark.parametrize("bad", ["../secret.csv", "sub/take.csv", "sub\\take.csv",
                                 "C:evil.csv", "..", "a..b.csv", ""])
def test_path_traversal_names_are_rejected(env, bad):
    with pytest.raises(eb.BundleError) as e:
        eb.build_session_bundle(env.captures, env.sessions, env.out, capture_names=[bad])
    assert not isinstance(e.value, eb.BundleNotFound)
    assert not env.out.exists()


def test_traversal_session_id_is_rejected(env):
    for bad in ("../x", "a/b", "..\\x", ".hidden"):
        with pytest.raises(eb.BundleError):
            eb.build_session_bundle(env.captures, env.sessions, env.out, session_id=bad)


def test_missing_capture_and_session_are_clear_errors(env):
    with pytest.raises(eb.BundleNotFound, match="nothere.csv"):
        eb.build_session_bundle(env.captures, env.sessions, env.out,
                                capture_names=["first.csv", "nothere.csv"])
    with pytest.raises(eb.BundleNotFound):
        eb.build_session_bundle(env.captures, env.sessions, env.out, session_id="nope")
    assert not env.out.exists()


def test_must_choose_session_or_takes(env):
    with pytest.raises(eb.BundleError):
        eb.build_session_bundle(env.captures, env.sessions, env.out)
    with pytest.raises(eb.BundleError):
        eb.build_session_bundle(env.captures, env.sessions, env.out,
                                session_id=env.sid, capture_names=["first.csv"])


def test_take_deleted_from_disk_is_skipped_with_a_warning(env):
    (env.captures / "second.csv").unlink()
    report = eb.build_session_bundle(env.captures, env.sessions, env.out,
                                     session_id=env.sid)
    assert report.takes == ["first.csv"]
    assert any("second.csv" in w for w in report.warnings)
    with zipfile.ZipFile(env.out) as zf:
        assert "second.csv" in zf.read("README.txt").decode()


def _write_plain_take(captures, name, n_rows, labels=4):
    p = captures / (name + ".csv")
    with CaptureWriter(str(p), matrix_rows=4, matrix_cols=15, label_count=labels,
                       schema_version="v2") as w:
        for i in range(n_rows):
            w.write_row_v2(1000 + i * 100, "left", "fg-1",
                           [100 + (i % 9) * 40 + c for c in range(60)],
                           [i] * labels)
    return p


def test_older_take_without_stored_quality_is_scanned(env):
    # A take from before quality was stored: CSV + a meta with no final block.
    _write_plain_take(env.captures, "legacy", 120)
    (env.captures / "legacy.meta.json").write_text(json.dumps(
        {"notes": "", "auto": {}, "roles": {"fg-1": "left"}, "label_source": "lask5"}))
    eb.build_session_bundle(env.captures, env.sessions, env.out, capture_names=["legacy"])
    with zipfile.ZipFile(env.out) as zf:
        row = next(csv.DictReader(io.StringIO(zf.read("summary.csv").decode())))
    assert row["rows"] == "120"
    assert row["quality"] in ("good", "check", "bad")


def test_sensor_only_take_is_labelled_as_such(env):
    _write_plain_take(env.captures, "nolabel", 60, labels=0)
    (env.captures / "nolabel.meta.json").write_text(json.dumps(
        {"auto": {"final": {"rows": 60, "duration_s": 6.0, "has_labeler": False,
                            "verdict": "check", "reasons": ["No labeler was used."],
                            "flags": []}}}))
    eb.build_session_bundle(env.captures, env.sessions, env.out, capture_names=["nolabel"])
    with zipfile.ZipFile(env.out) as zf:
        row = next(csv.DictReader(io.StringIO(zf.read("summary.csv").decode())))
    assert row["labeler"] == "none (sensor only)"
    assert row["quality"] == "check"
    assert "No labeler" in row["quality_reasons"]


def test_filename_never_contains_a_session_name(env):
    name = eb.bundle_filename("2026-05-16T08-42-13")
    assert name == "openmuscle-session-2026-05-16T08-42-13.zip"
    assert eb.bundle_filename(None, now=0).startswith("openmuscle-takes-")
    first = eb.unique_path(env.tmp / "exp", name)
    first.write_bytes(b"x")
    assert eb.unique_path(env.tmp / "exp", name).name.endswith("-2.zip")


def test_find_session_by_id_or_name(env):
    assert eb.find_session(env.sessions, env.sid)["id"] == env.sid
    assert eb.find_session(env.sessions, "BASELINE")["id"] == env.sid
    with pytest.raises(eb.BundleNotFound):
        eb.find_session(env.sessions, "unknown")


# ----- size -----

def _write_ten_minute_take(captures, name="long", hz=100, minutes=10, seed=7):
    """Two bands at `hz` for `minutes`: the size of a realistic long recording."""
    import numpy as np
    rng = np.random.default_rng(seed)
    n = int(hz * 60 * minutes)
    path = captures / (name + ".csv")
    base = rng.integers(1500, 2500, size=60)
    with CaptureWriter(str(path), matrix_rows=4, matrix_cols=15, label_count=5,
                       schema_version="v2") as w:
        for i in range(n):
            wave = int(300 * np.sin(i / 40.0))
            noise = rng.integers(-12, 13, size=60)
            row = (base + wave + noise).tolist()
            w.write_row_v2(i * (1000 // hz), "left" if i % 2 else "right", "fg-%d" % (i % 2),
                           row, [wave, wave // 2, 0, 1, i % 100])
    (captures / (name + ".meta.json")).write_text(json.dumps({
        "created_ms": 1_700_000_000_000, "label_source": "lask5",
        "roles": {"fg-0": "right", "fg-1": "left"},
        "auto": {"final": {"rows": n, "duration_s": minutes * 60.0, "verdict": "good",
                           "reasons": [], "flags": []}}}))
    return path


def test_ten_minute_compact_bundle_is_small_and_estimate_is_close(tmp_path):
    cap = tmp_path / "captures"
    cap.mkdir()
    sess = tmp_path / "sessions"
    sess.mkdir()
    csv_path = _write_ten_minute_take(cap)
    out = tmp_path / "big.zip"
    est = eb.estimate_bundle(cap, sess, capture_names=["long"])
    report = eb.build_session_bundle(cap, sess, out, capture_names=["long"])
    assert report.zip_bytes < 25 * 1024 * 1024
    assert report.zip_bytes < csv_path.stat().st_size            # actually compressed
    assert abs(est["estimated_zip_bytes"] - report.zip_bytes) <= 0.2 * report.zip_bytes, (
        est["estimated_zip_bytes"], report.zip_bytes)
    assert est["uncompressed_bytes"] >= csv_path.stat().st_size
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None


# ----- estimate -----

def test_estimate_within_twenty_percent_for_a_session(env):
    for raw in (False, True):
        est = eb.estimate_bundle(env.captures, env.sessions, session_id=env.sid,
                                 include_raw=raw)
        report = eb.build_session_bundle(env.captures, env.sessions, env.out,
                                         session_id=env.sid, include_raw=raw)
        assert est["takes"] == 2
        assert abs(est["estimated_zip_bytes"] - report.zip_bytes) <= 0.2 * report.zip_bytes + 600
    assert est["with_raw_zip_bytes"] >= est["compact_zip_bytes"]
    assert est["warn_large"] is False and est["warn_over_bytes"] == 20 * 1024 * 1024


def test_estimate_flags_large_bundles(env, monkeypatch):
    monkeypatch.setattr(eb, "WARN_BYTES", 10)
    est = eb.estimate_bundle(env.captures, env.sessions, session_id=env.sid)
    assert est["warn_large"] is True


def test_free_text_fields_are_left_out_without_notes(tmp_path):
    state = make_state(tmp_path / "captures")
    t0 = time.time()
    state._handle_packet(band_packet(t0, 0))
    state._handle_packet(labeler_packet(t0, 0))
    sess = state.start_session(name="baseline", wearer="P01",
                               video_ref="D:/videos/SECRETNAME/v.mp4",
                               tags=["SECRETTAG"])
    record_take(state, "first.csv")
    state.write_capture_meta("first.csv", {"tags": ["SECRETTAG", "OTHERSECRET"]})
    state.end_session()
    out = tmp_path / "out" / "b.zip"
    eb.build_session_bundle(state.captures_dir, state.sessions_dir, out,
                            session_id=sess["id"])
    with zipfile.ZipFile(out) as zf:
        for n in zf.namelist():
            blob = zf.read(n)
            assert b"SECRETNAME" not in blob, n
            assert b"SECRETTAG" not in blob, n
            assert b"OTHERSECRET" not in blob, n
        sj = json.loads(zf.read("session.json"))
        meta = json.loads(zf.read("takes/first.meta.json"))
    assert sj["video_ref"] == ""
    assert sj["tags"] == []
    assert all(t.startswith("session:") for t in meta["tags"])

    eb.build_session_bundle(state.captures_dir, state.sessions_dir, out,
                            session_id=sess["id"], include_notes=True)
    with zipfile.ZipFile(out) as zf:
        assert json.loads(zf.read("session.json"))["video_ref"] == "D:/videos/SECRETNAME/v.mp4"
        assert "SECRETTAG" in json.loads(zf.read("session.json"))["tags"]


def test_concurrent_exports_get_distinct_valid_zips(env):
    import threading

    folder = env.tmp / "exports"
    name = eb.bundle_filename(env.sid)
    results, errors = [], []
    barrier = threading.Barrier(6)

    def one():
        try:
            barrier.wait()
            out = eb.unique_path(folder, name)
            eb.build_session_bundle(env.captures, env.sessions, out, session_id=env.sid)
            results.append(out)
        except BaseException as e:      # collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=one) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len({str(p) for p in results}) == 6
    for p in results:
        with zipfile.ZipFile(p) as zf:
            assert zf.testzip() is None
            assert "summary.csv" in zf.namelist()
    assert not list(folder.glob("*.part"))
    assert len(list(folder.glob("*.zip"))) == 6
