"""ml/verdict.py: one place that turns trainer metrics into plain words.

Table-driven so each grade boundary, the tail-hold-out cap and the wording rules
are pinned. Also checks the verdict reaches the train result, the model list and
the CLI.
"""

import csv
import math
import random

import pytest
from click.testing import CliRunner

from openmuscle.ml import verdict as vd
from openmuscle.ml.verdict import describe_result

pytestmark = pytest.mark.usefixtures("tiny_training_ok")


def _metrics(score, method="held_out_capture", per_finger=None, **extra):
    m = {"curl_r2": score, "eval_method": method, "score_space": "curl",
         "eval_captures": ["take2.csv"], "per_finger": per_finger or []}
    m.update(extra)
    return m


def _fingers(**scores):
    return [{"finger": k, "r2": v, "mae": 0.1, "constant": v is None}
            for k, v in scores.items()]


# ------------------------------------------------------------ grade boundaries

@pytest.mark.parametrize("score,grade", [
    (1.0, "good"), (0.80, "good"), (0.75, "good"),
    (0.7499, "fair"), (0.60, "fair"), (0.50, "fair"),
    (0.4999, "weak"), (0.30, "weak"), (0.25, "weak"),
    (0.2499, "not_working"), (0.0, "not_working"), (-0.4, "not_working"),
    (-25.0, "not_working"),
])
def test_grade_boundaries_on_held_out_captures(score, grade):
    v = describe_result(_metrics(score))
    assert v["grade"] == grade
    assert v["show_live_ok"] == (grade in ("fair", "good"))
    assert v["headline"] and v["detail"] and v["next_step"]


@pytest.mark.parametrize("score,grade,capped", [
    (0.95, "fair", True), (0.75, "fair", True),       # capped from good
    (0.60, "fair", False),                            # already fair
    (0.30, "weak", False), (0.10, "not_working", False),
])
def test_tail_hold_out_is_capped_at_fair(score, grade, capped):
    v = describe_result(_metrics(score, method="held_out_tail"))
    assert v["grade"] == grade
    assert v["capped"] is capped
    assert "same take" in v["detail"]
    if capped:
        assert "Held at Fair" in v["detail"]


def test_thresholds_are_one_tunable_dict():
    assert vd.THRESHOLDS == {"good": 0.75, "fair": 0.50, "weak": 0.25}
    custom = {"good": 0.9, "fair": 0.6, "weak": 0.3}
    assert describe_result(_metrics(0.8), thresholds=custom)["grade"] == "fair"
    assert describe_result(_metrics(0.8))["grade"] == "good"


# ------------------------------------------------------ no honest number cases

@pytest.mark.parametrize("method,expect,forbid", [
    ("held_out_capture", "had no finger movement", "before held-out testing existed"),
    ("held_out_tail", "had no finger movement", "before held-out testing existed"),
    ("none", "too few rows", "before held-out testing existed"),
    ("random_split", "before held-out testing existed", "had no finger movement"),
    (None, "before held-out testing existed", "had no finger movement"),
])
def test_no_honest_number_message_depends_on_why(method, expect, forbid):
    v = describe_result({"eval_method": method, "curl_r2": None})
    assert v["grade"] == "untested"
    assert v["show_live_ok"] is False
    assert v["score_pct"] is None
    assert expect in v["detail"]
    assert forbid not in v["detail"]
    assert v["next_step"]


def test_held_out_without_movement_asks_for_a_take_with_movement():
    v = describe_result(_metrics(None))
    assert v["grade"] == "untested"
    assert "could not be scored" in v["detail"]
    assert "includes finger movement" in v["next_step"]


# ---------------------------------------------------------------- the wording

def test_percentage_is_plain_and_never_negative():
    v = describe_result(_metrics(0.824))
    assert "82%" in v["headline"]
    assert v["score_pct"] == 82
    bad = describe_result(_metrics(-0.35))
    assert bad["headline"] == "Not working yet"
    assert bad["score_pct"] == 0
    assert "-" not in bad["headline"] and "-35" not in bad["detail"]
    assert "%" not in bad["headline"]
    assert "R2" not in bad["headline"] + bad["detail"] + bad["next_step"]


def test_per_finger_sentence():
    v = describe_result(_metrics(
        0.6, per_finger=_fingers(thumb=0.9, index=0.8, middle=0.7, ring=0.1, pinky=0.35)))
    assert "Thumb, index and middle follow well" in v["detail"]
    assert "Ring is not learned yet" in v["detail"]
    assert "Pinky is partly tracked" in v["detail"]


def test_per_finger_all_good_and_constant_finger_note():
    v = describe_result(_metrics(0.9, per_finger=_fingers(index=0.9, middle=0.8)))
    assert "Every finger follows well" in v["detail"]
    v = describe_result(_metrics(0.7, per_finger=_fingers(index=0.9, ring=None)))
    assert "Index follows well" in v["detail"]
    assert "Ring did not move in the test recording" in v["detail"]


def test_hand_side_and_recording_count_in_detail():
    v = describe_result(_metrics(0.8), hand_side="left")
    assert "left-hand model" in v["detail"]
    two = describe_result(_metrics(0.8, eval_captures=["a", "b"]))
    assert "2 recordings it never saw" in two["detail"]


def test_single_capture_next_step_suggests_a_second_recording():
    one = describe_result(_metrics(0.6), n_captures=1)
    many = describe_result(_metrics(0.6), n_captures=3)
    assert "separate recordings" in one["next_step"]
    assert "separate recordings" not in many["next_step"]


def test_warn_checks_are_folded_into_detail():
    checks = [{"level": "warn", "message": "Only one capture"},
              {"level": "block", "message": "ignored here"}]
    v = describe_result(_metrics(0.8), checks=checks)
    assert "Only one capture." in v["detail"]
    assert "ignored here" not in v["detail"]


# ------------------------------------------------------- nothing honest to grade

def test_legacy_model_without_held_out_score_is_untested():
    v = describe_result({"r2": 0.93, "mse": 0.01, "n_train": 80, "n_test": 20})
    assert v["grade"] == "untested"
    assert v["show_live_ok"] is False
    assert "0.93" not in v["detail"]


def test_too_small_recording_is_untested():
    v = describe_result({"eval_method": "none", "curl_r2": None, "r2": None})
    assert v["grade"] == "untested"
    assert "too few rows" in v["detail"]


def test_empty_metrics_do_not_crash():
    assert describe_result(None)["grade"] == "untested"
    assert describe_result({})["grade"] == "untested"


# -------------------------------------------------- wired into state and the CLI

def _write(path, n=200, seed=0):
    rng = random.Random(seed)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_hub_ms", "role", "device_id", "R0C0", "R0C1", "R0C2",
                    "label_0", "label_1", "label_2", "label_3"])
        for i in range(n):
            c = [0.5 + 0.5 * math.sin(i / (6.0 + 2 * k) + k) for k in range(4)]
            w.writerow([1_000_000 + i * 40, "left", "fg-left"]
                       + [c[0] * 50 + rng.gauss(0, 1), c[1] * 50 + rng.gauss(0, 1),
                          c[2] * 50 + c[3] * 20 + rng.gauss(0, 1)] + c)


def test_train_result_and_model_list_carry_a_verdict(tmp_path, monkeypatch):
    from openmuscle.web.state import AppState
    monkeypatch.chdir(tmp_path)
    caps = tmp_path / "captures"
    caps.mkdir()
    s = AppState(udp_port=53892, captures_dir=str(caps), enable_discovery=False)
    for k in range(3):
        _write(caps / f"take{k}.csv", seed=k)
        s.write_capture_meta(f"take{k}.csv", {"created_ms": 1000 * (k + 1)})
    res = s.train_from_captures(["take0.csv", "take1.csv", "take2.csv"],
                                n_estimators=8, activate=False)
    for key in ("grade", "headline", "detail", "next_step"):
        assert res["verdict"][key]
    assert res["metrics"]["eval_captures"] == ["take2.csv"]

    listed = s.list_models()
    assert len(listed) == 1
    assert listed[0]["verdict"]["grade"] == res["verdict"]["grade"]
    assert listed[0]["verdict"]["headline"] == res["verdict"]["headline"]


def test_old_registry_entry_gets_an_untested_verdict(tmp_path, monkeypatch):
    from openmuscle.ml.registry import ModelRegistry
    from openmuscle.web.state import AppState
    monkeypatch.chdir(tmp_path)
    ModelRegistry().save(object(), "random_forest", {"r2": 0.9, "mse": 0.1})
    s = AppState(udp_port=53893, captures_dir=str(tmp_path / "c"),
                 enable_discovery=False)
    (entry,) = s.list_models()
    assert entry["verdict"]["grade"] == "untested"


def test_cli_train_prints_the_verdict(tmp_path, monkeypatch):
    from openmuscle.cli import main
    monkeypatch.chdir(tmp_path)
    paths = []
    for k in range(2):
        p = tmp_path / f"c{k}.csv"
        _write(p, seed=k)
        paths.append(str(p))
    res = CliRunner().invoke(main, ["train", *paths, "--trees", "8",
                                    "-o", str(tmp_path / "m.pkl")])
    assert res.exit_code == 0, res.output
    assert "Next:" in res.output
    assert "Tested" in res.output

    res = CliRunner().invoke(main, ["train", paths[0], "--trees", "8",
                                    "-o", str(tmp_path / "m2.pkl")])
    assert res.exit_code == 0, res.output
    assert "Next:" in res.output
