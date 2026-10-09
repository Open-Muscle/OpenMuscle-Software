"""The dashboard side of training: what the page ships, the real payloads run
through the card module under Node, and the model-folder reveal."""

import asyncio
import csv
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from openmuscle.web.app import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "openmuscle" / "web" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CARD_JS = STATIC / "train-card.js"

NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


# ------------------------------------------------------------- static page

def test_page_loads_the_card_module_before_app_js():
    assert HTML.index("/static/train-card.js") < HTML.index("/static/app.js")
    assert 'id="train-result"' in HTML and 'id="model-banner"' in HTML


def test_models_table_is_plain_with_details_behind_a_toggle():
    head = HTML[HTML.index('<table id="models-table">'):HTML.index('<tbody id="models-body">')]
    visible = re.sub(r'<th class="detail-col">.*?</th>', "", head)
    for word in ("Model", "When", "Participant", "Bands", "Score"):
        assert f"<th>{word}</th>" in visible
    assert "R²" not in visible and "MSE" not in visible and "Features" not in visible
    assert 'id="models-details-toggle"' in HTML
    assert head.count('class="detail-col"') == 2


def test_one_participant_code_field_replaces_wearer_and_subject():
    assert 'id="sess-subject"' not in HTML
    assert 'for="sess-wearer">Participant code<' in HTML
    assert "never a name" in HTML
    assert ">Wearer<" not in HTML and ">Subject<" not in HTML


def test_no_raw_score_readout_or_double_prefix_left_in_app_js():
    assert "R²=" not in APP_JS and "MSE=" not in APP_JS
    assert "Train failed:" not in APP_JS
    app_py = (STATIC.parent / "app.py").read_text(encoding="utf-8")
    assert "Training failed:" not in app_py


def test_resume_wording_is_gone():
    assert "Resume" not in APP_JS.replace("renderResume", "")


def test_participant_compare_in_js_is_case_insensitive():
    # the chip warning goes through OMTrain.sameParticipant, not !==
    assert "wearer !== sessionWearer" not in APP_JS
    assert "OMTrain.sameParticipant" in APP_JS


# ----------------------------------------------- real payloads through Node

HEADER = (["ts_hub_ms", "role", "device_id"] + [f"R0C{c}" for c in range(8)]
          + ["label_0", "label_1", "label_2", "label_3"])


def _write(path, n, learnable, seed):
    rng = random.Random(seed)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        phase = [rng.random() * 6 for _ in range(4)]
        for i in range(n):
            drive = [0.5 + 0.5 * __import__("math").sin(i / (9 + 3 * k) + phase[k]) for k in range(4)]
            feats = [200 + 600 * drive[c % 4] + rng.gauss(0, 8) for c in range(8)]
            labels = drive if learnable else [rng.random() for _ in range(4)]
            if not learnable:
                feats = [200 + 600 * rng.random() for _ in range(8)]
            w.writerow([1_000_000 + i * 40, "left", "fg-1"] + feats + labels)


def _train_payload(tmp_path, monkeypatch, learnable):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    caps = tmp_path / "caps"
    caps.mkdir()
    app = create_app(udp_port=53870 + int(learnable), captures_dir=str(caps))
    for k in range(3):
        _write(caps / f"take{k}.csv", 500, learnable, seed=k)
    train = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/train")
    body = train.__annotations__["body"](captures=["take0.csv", "take1.csv", "take2.csv"],
                                         role="left", n_estimators=20)
    return asyncio.run(train(body))


def _cards(payload):
    js = ("const T=require(%s);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
          "const p=JSON.parse(s);const c=T.cardsFor(p);"
          "console.log(JSON.stringify({cards:c,all:T.allAutoStart(c)}));});"
          % json.dumps(str(CARD_JS)))
    r = subprocess.run(["node", "-e", js], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@NEEDS_NODE
def test_noise_data_gives_a_red_card_that_does_not_auto_start(tmp_path, monkeypatch):
    payload = _train_payload(tmp_path, monkeypatch, learnable=False)
    out = _cards(payload)
    card = out["cards"][0]
    assert payload["verdict"]["grade"] == "not_working"
    assert card["tone"] == "bad"
    assert card["autoStart"] is False and out["all"] is False
    assert card["canStartAnyway"] is True
    assert "Not working yet" in card["headline"]
    assert card["status"] == "Model saved but not started."
    visible = " ".join([card["headline"], card["detail"], card["nextStep"], card["status"]])
    assert "R²" not in visible and "R2" not in visible
    # the raw numbers exist, but only in the details list
    assert any("R²" in k for k, _v in card["technical"])
    assert "n_samples" not in json.dumps(payload)


@NEEDS_NODE
def test_learnable_data_gives_a_green_or_amber_card_with_finger_bars(tmp_path, monkeypatch):
    payload = _train_payload(tmp_path, monkeypatch, learnable=True)
    out = _cards(payload)
    card = out["cards"][0]
    assert card["tone"] in ("good", "fair"), payload["verdict"]
    assert out["all"] is True and card["autoStart"] is True
    assert len(card["bars"]) == 4
    assert all(b["pct"] is not None and b["pct"] > 0 for b in card["bars"])
    assert payload["model_dir"] and Path(payload["model_dir"]).is_dir()


# ------------------------------------------------------------ open folder

def test_reveal_opens_a_model_folder_only_inside_the_models_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMUSCLE_HOME", str(tmp_path))
    from openmuscle.web import app as app_module
    opened = []
    monkeypatch.setattr(app_module, "_reveal_path_in_file_manager",
                        lambda p, select_file: opened.append((Path(p), select_file)))
    app = create_app(udp_port=53872, captures_dir=str(tmp_path / "caps"))
    reveal = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/reveal"
                  and "POST" in r.methods)
    body_cls = reveal.__annotations__["body"]

    model_dir = tmp_path / "models" / "random_forest_x"
    model_dir.mkdir(parents=True)
    (model_dir / "model.pkl").write_bytes(b"x")
    out = asyncio.run(reveal(body_cls(model=str(model_dir))))
    assert Path(out["opened"]) == model_dir.resolve() and opened[-1][1] is False
    # a path to the pickle opens its folder
    asyncio.run(reveal(body_cls(model=str(model_dir / "model.pkl"))))
    assert opened[-1][0] == model_dir.resolve()

    from fastapi import HTTPException
    for bad in (str(tmp_path), str(tmp_path / "models" / ".." / "captures"), "C:/Windows"):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(reveal(body_cls(model=bad)))
        assert ei.value.status_code == 404
