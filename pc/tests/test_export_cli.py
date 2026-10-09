"""`openmuscle bundle`: the command-line way to make the export zip."""

import time
import zipfile

from click.testing import CliRunner

from openmuscle import paths
from openmuscle.cli import main

from .export_fixtures import band_packet, labeler_packet, make_state, record_take


def _recorded(tmp_path):
    state = make_state(tmp_path / "captures")
    t0 = time.time()
    state._handle_packet(band_packet(t0, 0))
    state._handle_packet(labeler_packet(t0, 0))
    sess = state.start_session(name="morning baseline", wearer="P01")
    record_take(state, "one.csv")
    record_take(state, "two.csv")
    state.end_session()
    record_take(state, "loose.csv")
    return state, sess["id"]


def test_help_documents_the_command():
    r = CliRunner().invoke(main, ["bundle", "--help"])
    assert r.exit_code == 0
    for word in ("--raw", "--notes", "--take", "--output", "README.txt", "summary.csv"):
        assert word in r.output
    top = CliRunner().invoke(main, ["--help"])
    assert "bundle" in top.output and "export" in top.output     # model export untouched


def test_bundle_by_session_id_writes_into_exports(tmp_path):
    _state, sid = _recorded(tmp_path)
    r = CliRunner().invoke(main, ["bundle", sid, "--captures-dir", str(tmp_path / "captures")])
    assert r.exit_code == 0, r.output
    out = paths.exports_dir() / ("openmuscle-session-%s.zip" % sid)
    assert out.is_file() and str(out) in r.output
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
        assert "takes/one.csv" in zf.namelist() and "takes/loose.csv" not in zf.namelist()
    assert "Notes were left out" in r.output
    # A second run never overwrites the first.
    r = CliRunner().invoke(main, ["bundle", sid, "--captures-dir", str(tmp_path / "captures")])
    assert r.exit_code == 0 and len(list(paths.exports_dir().glob("*.zip"))) == 2


def test_bundle_by_name_with_output_and_flags(tmp_path):
    _recorded(tmp_path)
    out = tmp_path / "mine.zip"
    r = CliRunner().invoke(main, ["bundle", "Morning Baseline", "-o", str(out), "--raw",
                                  "--captures-dir", str(tmp_path / "captures")])
    assert r.exit_code == 0, r.output
    with zipfile.ZipFile(out) as zf:
        assert "takes/one.sensor.jsonl" in zf.namelist()


def test_bundle_loose_takes(tmp_path):
    _recorded(tmp_path)
    out = tmp_path / "loose.zip"
    r = CliRunner().invoke(main, ["bundle", "--take", "loose.csv", "-o", str(out),
                                  "--captures-dir", str(tmp_path / "captures")])
    assert r.exit_code == 0, r.output
    with zipfile.ZipFile(out) as zf:
        assert "takes/loose.csv" in zf.namelist()


def test_bundle_errors_are_plain(tmp_path):
    _recorded(tmp_path)
    cap = ["--captures-dir", str(tmp_path / "captures")]
    r = CliRunner().invoke(main, ["bundle", "no-such-session"] + cap)
    assert r.exit_code != 0 and "No session" in r.output and "Traceback" not in r.output
    r = CliRunner().invoke(main, ["bundle", "--take", "missing.csv"] + cap)
    assert r.exit_code != 0 and "missing.csv" in r.output
    r = CliRunner().invoke(main, ["bundle"] + cap)
    assert r.exit_code != 0
    r = CliRunner().invoke(main, ["bundle", "x", "--take", "loose.csv"] + cap)
    assert r.exit_code != 0
    r = CliRunner().invoke(main, ["bundle", "--take", "../x.csv"] + cap)
    assert r.exit_code != 0 and "valid capture name" in r.output
