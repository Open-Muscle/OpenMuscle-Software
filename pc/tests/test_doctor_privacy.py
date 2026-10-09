"""The support zip and report must not carry wearer names, take names or the OS
user name, using the exact log formats the app writes."""

import os
import time
import zipfile

import pytest

from openmuscle import doctor, paths


def _entry(source, message, level="INFO"):
    return {"id": 1, "t": time.time(), "level": level, "source": source,
            "message": message}


@pytest.fixture
def calm_result(monkeypatch):
    # run_doctor against the real machine is slow; a canned result is enough here.
    return {"ok": True, "checks": [
        {"id": "x", "title": "T", "status": "pass",
         "detail": "d", "fix": ""}],
        "udp_port": 3141, "bound_host": "0.0.0.0", "devices_seen": [],
        "last_announce_s": None, "firewall": {},
        "likely_cause": "none",
        "advice": {"sentence": "All good.", "action": ""}}


def test_scrub_masks_wearer_and_adjusted_names():
    out = doctor.scrub("started: 20261009-sess (wearer=Jane Doe, arm=left, "
                       "labeler=lask5, 2 device(s))")
    assert "Jane" not in out and "Doe" not in out
    assert "arm=left" in out
    out = doctor.scrub("capture name adjusted: 'my take' -> 'my take_2'")
    assert "my take" not in out


def test_scrub_masks_file_names_with_spaces():
    out = doctor.scrub("Saved C:/Users/Someone/OpenMuscle/captures/Jane take 2.csv ok")
    assert "Jane" not in out and "take 2" not in out and "Someone" not in out
    out = doctor.scrub("wrote 'Jane take 3.csv' (240 rows)")
    assert "Jane" not in out and "<file>" in out


def test_scrub_home_folder_any_case_and_slash(monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: "C:\\Users\\Zed")
    for raw in ("C:/Users/Zed/foo", "c:\\users\\zed\\x", "C:\\Users\\Zed\\x"):
        out = doctor.scrub("at " + raw)
        assert "zed" not in out.lower(), (raw, out)
    # a profile folder that is not the current user is masked as well
    assert "Other" not in doctor.scrub("at D:/Users/Other/stuff")
    assert "Other" not in doctor.scrub("at /home/Other/stuff")


def test_scrub_data_root_either_slash(tmp_path, monkeypatch):
    root = tmp_path / "Mixed Case Root"
    monkeypatch.setattr(paths, "data_root", lambda: root)
    for raw in (str(root), str(root).replace("\\", "/"), str(root).upper()):
        assert "mixed" not in doctor.scrub("in " + raw + "/captures").lower()


def test_support_zip_and_report_carry_no_names(calm_result, tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser", lambda p: "C:\\Users\\Zed")
    logs = [
        _entry("session", "started: 20261009-sess (wearer=Jane Doe, arm=left, "
                          "labeler=lask5, 2 device(s))"),
        _entry("recording", "capture name adjusted: 'my take' -> 'my take_2'"),
        _entry("recording", "started: Jane take 2.csv"),
        _entry("openmuscle.web.state", "session started (wearer=Jane Doe, arm=right)"),
        _entry("devices", "Saved C:/Users/Zed/OpenMuscle/captures/Jane take 2.csv"),
        _entry("server", "opening c:\\users\\zed\\x and C:/Users/Zed/foo"),
        _entry("uvicorn.access", '127.0.0.1:5 - "GET /api/captures/Jane%20take HTTP/1.1" 200'),
        _entry("devices", "Found FlexGrid band at 10.0.0.23"),
    ]
    path = doctor.build_support_zip(calm_result, logs, ssid="HomeNet", out_dir=tmp_path)
    with zipfile.ZipFile(path) as z:
        blobs = {n: z.read(n).decode("utf-8") for n in z.namelist()}
    for name, blob in blobs.items():
        low = blob.lower()
        for secret in ("jane", "doe", "my take", "zed", "homenet"):
            assert secret not in low, (name, secret)
    assert "Found FlexGrid band at 10.0.0.23" in blobs["recent-logs.txt"]
    report = doctor.report_text(calm_result, ssid="HomeNet")
    assert "jane" not in report.lower() and "zed" not in report.lower()
