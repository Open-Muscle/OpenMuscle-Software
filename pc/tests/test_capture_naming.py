"""A take is never overwritten, and typed capture names are made file-safe.

Reproduction this pins: record a 30-row take as "foo", record again as "foo";
the second used to open foo.csv with 'w' and destroy the first. Now the second
lands in foo-2.csv and the first is intact.
"""

import asyncio
import time

import pytest

from openmuscle.protocol.schema import OpenMusclePacket, CURRENT_VERSION
from openmuscle.web.app import create_app
from openmuscle.web.state import (
    AppState, sanitize_capture_stem, unique_capture_stem, capture_stem_taken)


def _fg(did, val):
    return OpenMusclePacket(version=CURRENT_VERSION, device_type="flexgrid",
                            device_id=did, timestamp_ms=0,
                            data={"matrix": [[val] * 4 for _ in range(15)]},
                            receive_time=time.time())


def _state(tmp_path, port=53996):
    s = AppState(udp_port=port, captures_dir=str(tmp_path), enable_discovery=False)
    s._handle_packet(_fg("fg-L", 100))
    return s


def _record(s, name, frames=3):
    rec = s.start_recording(sensor_device_id="fg-L", role="left",
                            label_device_id="", filename=name)
    for _ in range(frames):
        s._handle_packet(_fg("fg-L", 100))
    s.stop_recording()
    return rec


# ----- sanitizing -----

@pytest.mark.parametrize("raw,expected", [
    ("take1", "take1"),
    ("take1.csv", "take1"),
    ("TAKE1.CSV", "TAKE1"),
    ("my take 1", "my-take-1"),
    (r"a/b\c:d?e*f", "c-d-e-f"),           # path parts are dropped
    ("what: now? ok*", "what-now-ok"),
    ('a<b>c|d"e', "a-b-c-d-e"),
    ("../../etc/passwd", "passwd"),
    ("take 1.5", "take-1-5"),
    ("  --spaced--  ", "spaced"),
    ("naïve-café", "naïve-café"),
    ("CON", "take-CON"),
    ("nul.csv", "take-nul"),
    ("", ""),
    (None, ""),
    ("???", ""),
    ("dir/", "dir"),
])
def test_sanitize_capture_stem(raw, expected):
    assert sanitize_capture_stem(raw) == expected


def test_sanitize_caps_length():
    out = sanitize_capture_stem("x" * 500)
    assert 0 < len(out) <= 80


def test_unique_stem_skips_each_sidecar(tmp_path):
    for suffix in (".csv", ".sensor.jsonl", ".label.jsonl",
                   ".meta.json", ".labels.schema.json"):
        d = tmp_path / suffix.strip(".").replace(".", "_")
        d.mkdir()
        assert unique_capture_stem(d, "foo") == "foo"
        (d / ("foo" + suffix)).write_text("x")
        assert capture_stem_taken(d, "foo")
        assert unique_capture_stem(d, "foo") == "foo-2"


def test_unique_stem_counts_up(tmp_path):
    (tmp_path / "foo.csv").write_text("x")
    (tmp_path / "foo-2.csv").write_text("x")
    assert unique_capture_stem(tmp_path, "foo") == "foo-3"


# ----- recording -----

def test_same_name_twice_keeps_both(tmp_path):
    s = _state(tmp_path)
    r1 = _record(s, "foo", frames=5)
    first = (tmp_path / "foo.csv").read_text()
    r2 = _record(s, "foo", frames=2)
    assert r1.path.name == "foo.csv"
    assert r2.path.name == "foo-2.csv"
    assert r2.name_adjusted is True
    assert (tmp_path / "foo.csv").read_text() == first      # untouched
    assert (tmp_path / "foo-2.csv").exists()
    r3 = _record(s, "foo.csv", frames=1)
    assert r3.path.name == "foo-3.csv"


def test_sidecar_collision_forces_new_name(tmp_path):
    # An orphaned sidecar (CSV deleted by hand) must not be reopened with 'w'.
    (tmp_path / "foo.sensor.jsonl").write_text("precious\n")
    s = _state(tmp_path)
    rec = _record(s, "foo")
    assert rec.path.name == "foo-2.csv"
    assert (tmp_path / "foo.sensor.jsonl").read_text() == "precious\n"


def test_unsafe_names_make_valid_files(tmp_path):
    s = _state(tmp_path)
    rec = _record(s, r"arm 1: left? *fast*/sub\take")
    assert rec.name_adjusted is True
    assert rec.path.parent == tmp_path
    assert rec.path.name == "take.csv"
    assert rec.path.exists()
    rec2 = _record(s, "what: now? ok *")
    assert rec2.path.name == "what-now-ok.csv"


def test_blank_or_junk_name_gets_generated_name(tmp_path):
    s = _state(tmp_path)
    for raw in (None, "", "???"):
        rec = s.start_recording(sensor_device_id="fg-L", label_device_id="",
                                filename=raw)
        s.stop_recording()
        assert rec.path.name.startswith("capture_") and rec.path.suffix == ".csv"
    assert len(list(tmp_path.glob("capture_*.csv"))) == 3     # none overwritten


def test_unchanged_name_is_not_flagged(tmp_path):
    s = _state(tmp_path)
    assert _record(s, "clean-name").name_adjusted is False
    assert _record(s, "clean-name.csv", frames=1).name_adjusted is True  # collided


def test_delete_then_next_name_never_reuses(tmp_path):
    s = _state(tmp_path)
    for n in (1, 2, 3):
        _record(s, f"sess-take{n}")
    assert s.delete_capture("sess-take2.csv")
    # capture_count-style suggestion would say take3 (2 left + 1): it exists.
    assert s.next_capture_name("sess", start=3) == "sess-take4.csv"
    # The freed slot is fine to suggest again.
    assert s.next_capture_name("sess", start=2) == "sess-take2.csv"
    # Even if a stale suggestion is submitted, Record cannot overwrite.
    rec = _record(s, "sess-take3")
    assert rec.path.name == "sess-take3-2.csv"


def test_next_name_sanitizes_base(tmp_path):
    s = _state(tmp_path)
    assert s.next_capture_name("My Session: 1") == "My-Session-1-take1.csv"
    assert s.next_capture_name("???") == "take-take1.csv"


# ----- HTTP -----
#
# Route handlers are called directly (the optional TestClient dependency is
# not needed to prove the request/response shape).

def _route(app, path, method):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return r.endpoint
    raise AssertionError(f"no route {method} {path}")


def test_http_start_returns_final_name_and_next_name(tmp_path):
    app = create_app(udp_port=53997, captures_dir=str(tmp_path))
    app.state.app_state._handle_packet(_fg("fg-L", 100))
    start = _route(app, "/api/recording", "POST")
    stop = _route(app, "/api/recording", "DELETE")
    next_name = _route(app, "/api/captures/next-name", "GET")
    body = start.__annotations__["body"](
        sensor_device_id="fg-L", label_device_id="", filename="my take")

    r = asyncio.run(start(body))
    assert r["filename"] == "my-take.csv"
    assert r["name_adjusted"] is True
    asyncio.run(stop())
    r = asyncio.run(start(body))
    assert r["filename"] == "my-take-2.csv"
    asyncio.run(stop())
    assert asyncio.run(next_name(base="my take", start=1)) == {
        "filename": "my-take-take1.csv"}
