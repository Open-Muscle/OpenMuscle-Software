"""Export endpoints (zip download, size estimate, saved copy) driven over ASGI."""

import io
import time
import types
import zipfile
from pathlib import Path

import pytest

from openmuscle import paths
from openmuscle.web.app import create_app

from .asgi_helper import call, call_json
from .export_fixtures import band_packet, begin_take, labeler_packet, record_take


@pytest.fixture
def api(tmp_path):
    app = create_app(udp_port=53970, captures_dir=str(tmp_path / "captures"))
    state = app.state.app_state
    t0 = time.time()
    state._handle_packet(band_packet(t0, 0))
    state._handle_packet(labeler_packet(t0, 0))
    sess = state.start_session(name="baseline", wearer="P01", arm="left")
    record_take(state, "first.csv", notes="TAKE-NOTE-TEXT")
    record_take(state, "second.csv")
    state.end_session()
    record_take(state, "loose.csv")
    return types.SimpleNamespace(app=app, state=state, sid=sess["id"])


def _zip(body):
    zf = zipfile.ZipFile(io.BytesIO(body))
    assert zf.testzip() is None
    return zf


def test_session_export_downloads_a_valid_zip(api):
    status, headers, body = call(api.app, "GET", "/api/sessions/%s/export" % api.sid)
    assert status == 200
    assert headers["content-type"] == "application/zip"
    disp = headers["content-disposition"]
    assert "attachment" in disp and "openmuscle-session-%s.zip" % api.sid in disp
    assert "baseline" not in disp and "P01" not in disp          # no names in the file name
    zf = _zip(body)
    assert set(zf.namelist()) == {
        "README.txt", "session.json", "summary.csv",
        "takes/first.csv", "takes/first.meta.json",
        "takes/second.csv", "takes/second.meta.json"}
    assert b"TAKE-NOTE-TEXT" not in zf.read("takes/first.meta.json")     # notes off by default


def test_session_export_keeps_a_copy_in_the_exports_folder(api):
    call(api.app, "GET", "/api/sessions/%s/export" % api.sid)
    saved = list(paths.exports_dir().glob("openmuscle-session-*.zip"))
    assert len(saved) == 1


def test_raw_and_notes_flags(api):
    status, _h, body = call(api.app, "GET", "/api/sessions/%s/export" % api.sid,
                            query="raw=1&notes=1")
    assert status == 200
    zf = _zip(body)
    assert "takes/first.sensor.jsonl" in zf.namelist()
    assert b"TAKE-NOTE-TEXT" in zf.read("takes/first.meta.json")
    status, _h, body = call(api.app, "GET", "/api/sessions/%s/export" % api.sid,
                            query="raw=0&notes=0")
    assert not any(n.endswith(".jsonl") for n in _zip(body).namelist())


def test_unknown_session_is_404(api):
    for method, path in (("GET", "/api/sessions/nope/export"),
                         ("GET", "/api/sessions/nope/export/estimate"),
                         ("POST", "/api/sessions/nope/export")):
        status, body = call_json(api.app, method, path, body={} if method == "POST" else None)
        assert status == 404, (path, body)


def test_loose_takes_download(api):
    status, headers, body = call(api.app, "POST", "/api/export",
                                 body={"captures": ["loose.csv", "first.csv"]})
    assert status == 200
    assert headers["content-disposition"].startswith("attachment")
    zf = _zip(body)
    assert "takes/loose.csv" in zf.namelist() and "takes/first.csv" in zf.namelist()
    assert '"synthesized": true' in zf.read("session.json").decode()
    status, _h, body = call(api.app, "POST", "/api/export",
                            body={"captures": ["loose.csv"], "raw": True})
    assert "takes/loose.sensor.jsonl" in _zip(body).namelist()


def test_loose_export_errors(api):
    assert call_json(api.app, "POST", "/api/export",
                     body={"captures": ["missing.csv"]})[0] == 404
    assert call_json(api.app, "POST", "/api/export", body={"captures": []})[0] == 400
    assert call_json(api.app, "POST", "/api/export",
                     body={"captures": ["../../etc/passwd"]})[0] == 400
    assert call_json(api.app, "POST", "/api/export/estimate", body={"captures": []})[0] == 400


def test_save_session_export_describes_the_file(api):
    status, body = call_json(api.app, "POST", "/api/sessions/%s/export" % api.sid,
                             body={"raw": False, "notes": False})
    assert status == 200
    assert body["takes"] == ["first.csv", "second.csv"]
    assert body["zip_bytes"] > 0 and body["filename"].endswith(".zip")
    assert paths.exports_dir() in Path(body["out_file"]).parents
    assert zipfile.ZipFile(body["out_file"]).testzip() is None


def test_estimate_endpoints_match_the_real_zip(api):
    for raw in ("0", "1"):
        status, est = call_json(api.app, "GET",
                                "/api/sessions/%s/export/estimate" % api.sid,
                                query="raw=" + raw)
        assert status == 200 and est["takes"] == 2
        _s, _h, body = call(api.app, "GET", "/api/sessions/%s/export" % api.sid,
                            query="raw=" + raw)
        assert abs(est["estimated_zip_bytes"] - len(body)) <= 0.2 * len(body) + 600
    status, est = call_json(api.app, "POST", "/api/export/estimate",
                            body={"captures": ["loose.csv"]})
    assert status == 200 and est["takes"] == 1 and est["warn_large"] is False


def test_cannot_export_a_take_that_is_still_recording(api):
    state = api.state
    state.start_session(name="live", wearer="P02")
    begin_take(state, "live.csv", frames=20)
    live_sid = state.active_session["id"]
    status, body = call_json(api.app, "POST", "/api/export", body={"captures": ["live.csv"]})
    assert status == 409 and "Stop" in body["detail"]
    assert call_json(api.app, "GET", "/api/sessions/%s/export" % live_sid)[0] == 409
    state.stop_recording()
    assert call(api.app, "POST", "/api/export", body={"captures": ["live.csv"]})[0] == 200
