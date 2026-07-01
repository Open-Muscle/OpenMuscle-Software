"""Tests for openmuscle.data.converter.combine_csvs, focused on the
anti-corruption guard: same-schema captures concat, mismatched schemas are
REFUSED (loud ValueError, no partial output) instead of silently corrupting."""

import csv

import pytest

from openmuscle.data.converter import combine_csvs


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


H = ["ts_hub_ms", "role", "device_id", "R0C0", "R0C1", "label_0", "label_1"]


def test_same_schema_concats(tmp_path):
    a, b, out = tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "out.csv"
    # left + right differ only by row VALUES (role/device_id), same header -> the
    # separable-per-hand concat must work.
    write_csv(a, H, [[1, "left", "d1", 10, 20, 0.1, 0.2],
                     [2, "left", "d1", 11, 21, 0.3, 0.4]])
    write_csv(b, H, [[3, "right", "d2", 12, 22, 0.5, 0.6]])
    n = combine_csvs([str(a), str(b)], str(out))
    assert n == 3
    lines = out.read_text().strip().splitlines()
    assert lines[0].split(",") == H          # header written exactly once
    assert len(lines) == 4                    # header + 3 data rows


def test_single_file_passthrough(tmp_path):
    a, out = tmp_path / "a.csv", tmp_path / "out.csv"
    write_csv(a, H, [[1, "left", "d1", 5, 6, 0.1, 0.2]])
    assert combine_csvs([str(a)], str(out)) == 1


def test_mismatched_label_count_refused(tmp_path):
    # The exact corruption case: a 1-label capture vs a 2-label capture.
    h1 = ["ts_hub_ms", "role", "device_id", "R0C0", "label_0"]
    a, b, out = tmp_path / "lask.csv", tmp_path / "quest.csv", tmp_path / "out.csv"
    write_csv(a, h1, [[1, "labeler", "l1", 5, 0.1]])
    write_csv(b, H, [[2, "left", "d1", 6, 7, 0.2, 0.3]])
    with pytest.raises(ValueError) as ei:
        combine_csvs([str(a), str(b)], str(out))
    assert "different schemas" in str(ei.value)
    assert not out.exists()                   # no partial/corrupt output left behind


def test_mismatched_sensor_count_refused(tmp_path):
    h2 = ["ts_hub_ms", "role", "device_id", "R0C0", "label_0", "label_1"]  # 1 sensor col
    a, b, out = tmp_path / "v1.csv", tmp_path / "v4.csv", tmp_path / "out.csv"
    write_csv(a, h2, [[1, "left", "d1", 5, 0.1, 0.2]])
    write_csv(b, H, [[2, "left", "d1", 6, 7, 0.2, 0.3]])   # 2 sensor cols
    with pytest.raises(ValueError):
        combine_csvs([str(a), str(b)], str(out))
    assert not out.exists()


def test_empty_input_list_refused(tmp_path):
    with pytest.raises(ValueError):
        combine_csvs([], str(tmp_path / "out.csv"))


def test_headerless_file_refused(tmp_path):
    a = tmp_path / "empty.csv"
    a.write_text("")
    with pytest.raises(ValueError):
        combine_csvs([str(a)], str(tmp_path / "out.csv"))
