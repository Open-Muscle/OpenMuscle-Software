"""Tests for the rehearsal kit simulator (simulate/rehearsal.py).

The rig has to be three things at once: shaped like a V4 kit (15x4 bands that
announce on the discovery port and answer the command channel), correlated
(a LASK5 and a hand derived from the same curls as the band pressure), and
tunable (the noise knob makes a weak dataset on demand). The first group pins
the pure generator; the live group runs it against a real AppState over UDP and
checks the whole record -> train -> held-out score path, including a wss://
label stream against an HTTPS server.
"""

import json
import queue
import shutil
import socket
import subprocess
import threading
import time

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from openmuscle.cli import main
from openmuscle.simulate import net
from openmuscle.simulate.quest_hand import JOINT_NAMES, finger_curls, hand_frame
from openmuscle.simulate.rehearsal import (
    BAND_CELLS, BAND_COLS, BAND_ROWS, IMU_SCALE, NOISE_PRESETS, PERIOD_S,
    RehearsalConfig, RehearsalRig, RehearsalSimulator, activity, latent_curls,
    parse_noise,
)
from openmuscle.web.state import AppState

pytestmark = pytest.mark.usefixtures("tiny_training_ok")


# ---- pure generator ----------------------------------------------------------

def _run(rig, seconds=40.0, hz=25):
    """Tick the rig and collect (t, [band matrices flattened], pistons)."""
    ts, bands, pistons = [], [[] for _ in rig.bands], []
    for k in range(int(seconds * hz)):
        t = k / hz
        frame = rig.tick(t)
        ts.append(t)
        for i, pkt in enumerate(frame["bands"]):
            m = pkt["data"]["matrix"]
            bands[i].append([m[c][r] for r in range(BAND_ROWS)
                             for c in range(BAND_COLS)])
        if frame["lask5"] is not None:
            pistons.append(frame["lask5"]["data"]["values"])
    return np.array(ts), [np.array(b, float) for b in bands], np.array(pistons)


def _lstsq_r2(x_train, y_train, x_test, y_test):
    """Held-out R^2 of a ridge-ish linear fit (no sklearn needed)."""
    xa = np.hstack([x_train, np.ones((len(x_train), 1))])
    xb = np.hstack([x_test, np.ones((len(x_test), 1))])
    reg = 1e-3 * np.eye(xa.shape[1])
    w = np.linalg.solve(xa.T @ xa + reg, xa.T @ y_train)
    pred = xb @ w
    ss_res = ((y_test - pred) ** 2).sum()
    ss_tot = ((y_test - y_test.mean(axis=0)) ** 2).sum()
    return 1 - ss_res / ss_tot


class TestConfig:
    def test_defaults_and_sides(self):
        cfg = RehearsalConfig()
        assert (cfg.bands, cfg.labeler, cfg.noise) == (2, "lask5", 1.0)
        assert cfg.sides == ("left", "right")
        assert RehearsalConfig(bands=1).sides == ("left",)

    @pytest.mark.parametrize("kwargs", [
        {"bands": 0}, {"bands": 3}, {"labeler": "gamepad"},
        {"noise": -1}, {"noise": "loud"}, {"rate_hz": 0},
    ])
    def test_rejects_bad_settings(self, kwargs):
        with pytest.raises(ValueError):
            RehearsalConfig(**kwargs)

    def test_noise_parsing(self):
        assert parse_noise("default") == NOISE_PRESETS["default"] == 1.0
        assert parse_noise("LOW") < 1.0 < parse_noise("high")
        assert parse_noise("2.5") == 2.5
        assert parse_noise(0) == 0.0
        with pytest.raises(ValueError):
            parse_noise("nan")


class TestBandFrames:
    def test_v4_packet_shape(self):
        rig = RehearsalRig()
        frame = rig.tick(1.0)
        assert len(frame["bands"]) == 2
        for pkt in frame["bands"]:
            assert pkt["v"] == "1.0" and pkt["type"] == "flexgrid"
            assert isinstance(pkt["ts"], int)
            data = pkt["data"]
            assert (data["rows"], data["cols"]) == (BAND_ROWS, BAND_COLS) == (4, 15)
            matrix = data["matrix"]
            assert len(matrix) == 15 and all(len(col) == 4 for col in matrix)
            assert BAND_CELLS == 60
            assert all(isinstance(v, int) and 0 <= v <= 4095
                       for col in matrix for v in col)
            assert len(data["imu"]["gyro"]) == 3 and len(data["imu"]["accel"]) == 3
            # The first frame carries the ~1 Hz status block.
            assert {"vbat", "pct", "uptime_s", "rssi"} <= set(pkt["meta"])
        assert {p["id"] for p in frame["bands"]} == {
            "flexgrid-sim-left", "flexgrid-sim-right"}
        json.dumps(frame["bands"])        # wire-serializable

    def test_status_meta_is_about_once_a_second(self):
        rig = RehearsalRig()
        with_meta = 0
        for k in range(100):
            if "meta" in rig.tick(k / 25)["bands"][0]:
                with_meta += 1
        assert with_meta == 4             # 100 frames at 25 Hz = 4 s

    def test_uptime_never_goes_backwards(self):
        # DeviceInfo flags a falling uptime_s as a reboot.
        rig = RehearsalRig()
        ups = [p["meta"]["uptime_s"] for k in range(200)
               for p in [rig.tick(k / 25)["bands"][0]] if "meta" in p]
        assert ups == sorted(ups)

    def test_one_band(self):
        rig = RehearsalRig(RehearsalConfig(bands=1))
        assert [p["id"] for p in rig.tick(0)["bands"]] == ["flexgrid-sim-left"]
        assert rig.roles == {"flexgrid-sim-left": "left", "lask5-sim": "labeler"}

    def test_left_and_right_differ(self):
        rig = RehearsalRig(RehearsalConfig(noise=0))
        frame = rig.tick(0.0)             # at rest: pure per-cell offsets
        left = np.array(frame["bands"][0]["data"]["matrix"], float)
        right = np.array(frame["bands"][1]["data"]["matrix"], float)
        assert np.abs(left - right).mean() > 50

    def test_rest_is_below_active(self):
        assert activity(0.0) == 0.0 and activity(1.0) == 0.0
        assert activity(5.0) == 1.0
        assert 0.0 < activity(2.75) < 1.0
        rig = RehearsalRig(RehearsalConfig(noise=0))
        rest = np.array(rig.tick(1.0)["bands"][0]["data"]["matrix"], float)
        moving = np.array(rig.tick(5.0)["bands"][0]["data"]["matrix"], float)
        assert moving.mean() > rest.mean() + 100

    def test_seed_reproducible(self):
        a = RehearsalRig(RehearsalConfig(seed=5))
        b = RehearsalRig(RehearsalConfig(seed=5))
        c = RehearsalRig(RehearsalConfig(seed=6))
        fa, fb, fc = a.tick(3.0), b.tick(3.0), c.tick(3.0)
        assert fa == fb
        assert fa["bands"][0]["data"]["matrix"] != fc["bands"][0]["data"]["matrix"]

    def test_noise_knob_scales_scatter(self):
        def scatter(noise):
            clean = RehearsalRig(RehearsalConfig(noise=0))
            noisy = RehearsalRig(RehearsalConfig(noise=noise))
            diffs = []
            for k in range(100, 160):
                t = k / 25
                a = np.array(clean.tick(t)["bands"][0]["data"]["matrix"], float)
                b = np.array(noisy.tick(t)["bands"][0]["data"]["matrix"], float)
                diffs.append((a - b).std())
            return float(np.mean(diffs))
        low, default, high = (scatter(parse_noise(n))
                              for n in ("low", "default", "high"))
        assert low < default < high
        assert high > 5 * default


class TestLabelerFrames:
    def test_lask5_packet(self):
        rig = RehearsalRig()
        for k in range(100):
            pkt = rig.tick(k / 25)["lask5"]
            assert pkt["type"] == "lask5" and pkt["id"] == "lask5-sim"
            vals = pkt["data"]["values"]
            assert len(vals) == 4 and all(0.0 <= v <= 1.0 for v in vals)
            joy = pkt["data"]["joystick"]
            assert 0 <= joy["x"] <= 4095 and 0 <= joy["y"] <= 4095
            assert isinstance(joy["x"], int)

    def test_pistons_follow_the_shared_curls(self):
        rig = RehearsalRig()
        _, _, pistons = _run(rig)
        curls = np.array([latent_curls(k / 25)[1:] for k in range(len(pistons))])
        for i in range(4):
            r = np.corrcoef(pistons[:, i], curls[:, i])[0, 1]
            assert r > 0.98
        # Each finger moves on its own phase, so the pistons are not copies.
        assert np.corrcoef(pistons[:, 0], pistons[:, 3])[0, 1] < 0.9

    def test_bands_are_learnable_from_pistons_held_out(self):
        rig = RehearsalRig()
        _, bands, pistons = _run(rig)
        cut = int(len(pistons) * 0.7)
        for band in bands:
            r2 = _lstsq_r2(band[:cut - 25], pistons[:cut - 25],
                           band[cut:], pistons[cut:])
            assert r2 > 0.5      # even a linear read-out recovers the curls

    def test_high_noise_destroys_the_signal(self):
        rig = RehearsalRig(RehearsalConfig(noise=parse_noise("high")))
        _, bands, pistons = _run(rig)
        cut = int(len(pistons) * 0.7)
        r2 = _lstsq_r2(bands[0][:cut - 25], pistons[:cut - 25],
                       bands[0][cut:], pistons[cut:])
        assert r2 < 0.2

    def test_webcam_and_quest_hands_share_the_curls(self):
        t = 5.0
        quest = RehearsalRig(RehearsalConfig(labeler="quest")).tick(t)
        webcam = RehearsalRig(RehearsalConfig(labeler="webcam")).tick(t)
        assert quest["lask5"] is None and webcam["lask5"] is None
        qpath, qframe = quest["hand"]
        wpath, wframe = webcam["hand"]
        assert (qpath, wpath) == ("/ws/quest", "/ws/webcam")
        assert [j["name"] for j in qframe["joints"]] == list(JOINT_NAMES)
        assert qframe == hand_frame(t, curls=latent_curls(t))
        # webcam: hand-local frame, wrist at the origin, identity rotations
        assert wframe["device_id"] == "webcam-right"
        assert wframe["joints"][0]["pos"] == [0.0, 0.0, 0.0]
        assert all(j["rot"] == [0.0, 0.0, 0.0, 1.0] for j in wframe["joints"])
        # at rest the hand is the zero-curl hand, not the ungated finger_curls one
        rest = RehearsalRig(RehearsalConfig(labeler="quest")).tick(1.0)["hand"][1]
        assert rest == hand_frame(1.0, curls=(0.0,) * 5)
        assert rest != hand_frame(1.0, curls=finger_curls(1.0))

    def test_announce_payloads(self):
        rig = RehearsalRig()
        band = rig.announce("flexgrid-sim-left", 9001, 3141)
        assert band["type"] == "announce" and band["dev"] == "flexgrid"
        assert band["matrix"] == [15, 4]
        assert band["services"] == {"sensor": 3141, "cmd": 9001}
        assert band["imu"] == IMU_SCALE
        assert "ip" not in band           # the hub reads it from the datagram
        lask = rig.announce("lask5-sim", 9002, 3141)
        assert lask["dev"] == "lask5" and lask["pistons"] == 4
        assert lask["services"]["cmd"] == 9002


# ---- live: real sockets against a real AppState --------------------------------

def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _free_tcp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(cond, timeout=15.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


class Hub:
    """An AppState wired like the web server, minus HTTP: a real UDP listener,
    real discovery (own beacon port), and a thread standing in for the
    broadcaster that drains the packet queue."""

    def __init__(self, captures_dir):
        self.udp_port = _free_udp_port()
        self.announce_port = _free_udp_port()
        self.state = AppState(udp_port=self.udp_port, captures_dir=captures_dir,
                              discovery_announce_port=self.announce_port)
        self._stop = threading.Event()
        # In the server, packet handling and the REST handlers share one event
        # loop; here the drain thread and the test thread need a lock to match.
        self.lock = threading.Lock()
        self._drain_thread = threading.Thread(target=self._drain, daemon=True)

    def _drain(self):
        while not self._stop.is_set():
            try:
                pkt = self.state.listener.packet_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            with self.lock:
                self.state._handle_packet(pkt)

    def start(self):
        self.state.start()
        self._drain_thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._drain_thread.join(timeout=2)
        self.state.stop()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Keep every side effect (discovery cache, model registry, captures) in tmp."""
    monkeypatch.setattr("openmuscle.discovery.manager.default_cache_path",
                        lambda: str(tmp_path / "discovered_devices.json"))
    monkeypatch.chdir(tmp_path)
    captures = tmp_path / "captures"
    return captures


@pytest.fixture
def hub(sandbox):
    h = Hub(sandbox).start()
    yield h
    h.stop()


def _start_sim(hub, **kw):
    cfg = RehearsalConfig(**kw.pop("cfg", {}))
    sim = RehearsalSimulator(
        cfg, udp_port=hub.udp_port, announce_port=hub.announce_port,
        role_setter=hub.state.discovery.set_role, log=lambda m: None, **kw)
    sim.start()
    return sim


def _wait_for_kit(hub, sim):
    ids = set(sim.rig.device_ids)
    assert _wait(lambda: ids <= {d["device_id"] for d in
                                 hub.state.discovery.snapshot()}), "not discovered"
    assert sim.tagged.wait(15), "roles never tagged"
    assert _wait(lambda: all(
        d in hub.state.devices and (hub.state.devices[d].rows or
                                    hub.state.devices[d].device_type != "flexgrid")
        for d in ids)), "no frames received"


class TestDiscovery:
    def test_kit_appears_in_discovery_subscribed_and_tagged(self, hub):
        sim = _start_sim(hub, speed=1.0)
        try:
            _wait_for_kit(hub, sim)
            snap = {d["device_id"]: d for d in hub.state.discovery.snapshot()}
            assert set(snap) == {"flexgrid-sim-left", "flexgrid-sim-right",
                                 "lask5-sim"}
            for did in ("flexgrid-sim-left", "flexgrid-sim-right"):
                assert snap[did]["device_type"] == "flexgrid"
                assert snap[did]["matrix"] == [15, 4]
                assert snap[did]["imu"] == IMU_SCALE
            assert snap["flexgrid-sim-left"]["role"] == "left"
            assert snap["flexgrid-sim-right"]["role"] == "right"
            assert snap["lask5-sim"]["role"] == "labeler"
            assert snap["lask5-sim"]["device_type"] == "lask5"
            # The hub's keeper really subscribed over the TCP command channel.
            assert _wait(lambda: all(d["subscribed"] for d in
                                     hub.state.discovery.snapshot()))
            assert all(d["sub_error"] == "" for d in
                       hub.state.discovery.snapshot())
            # Frames arrive as 15x4 bands in the live device table.
            for did in ("flexgrid-sim-left", "flexgrid-sim-right"):
                dev = hub.state.devices[did]
                assert (dev.rows, dev.cols) == (4, 15)
                assert dev.last_imu and dev.status.get("vbat")
        finally:
            sim.stop()

    def test_beacons_stop_once_subscribed(self, hub):
        sim = _start_sim(hub)
        try:
            _wait_for_kit(hub, sim)
            assert _wait(lambda: all(d["subscribed"] for d in
                                     hub.state.discovery.snapshot()))
            sent = sim.beacons_sent
            time.sleep(2.6)
            # A held device stays quiet (firmware behavior); allow one in-flight.
            assert sim.beacons_sent <= sent + 1
        finally:
            sim.stop()

    def test_get_info_over_the_command_channel(self, hub):
        sim = _start_sim(hub, cfg={"bands": 1})
        try:
            _wait_for_kit(hub, sim)
            dev = sim.devices["flexgrid-sim-left"]
            with socket.create_connection(("127.0.0.1", dev.cmd_port), 3) as s:
                s.sendall(json.dumps({"v": "1.0", "type": "cmd", "msg_id": 1,
                                      "data": {"verb": "get_info"}}).encode()
                          + b"\n")
                ack = json.loads(s.makefile().readline())
                s.sendall(json.dumps({"v": "1.0", "type": "cmd", "msg_id": 2,
                                      "data": {"verb": "bogus"}}).encode() + b"\n")
                bad = json.loads(s.makefile().readline())
            assert ack["status"] == "ok" and ack["msg_id"] == 1
            assert ack["data"]["id"] == "flexgrid-sim-left"
            assert ack["data"]["matrix"] == [15, 4]
            assert bad["status"] == "error"
        finally:
            sim.stop()


def _record_and_split(hub, sim, name, seconds=40.0, train_s=27.0):
    """Record `seconds` of sim time through the real recorder, then split the
    capture in time: everything before `train_s` (minus a 1 s guard) is the
    training capture, the rest is held out. Returns (train_name, held_df)."""
    state = hub.state
    # Start where a guided take starts: just before a rest, so the take (and the
    # held-out tail) covers the same stretch of the schedule on every run.
    assert _wait(lambda: (sim.sim_time() % PERIOD_S) > PERIOD_S - 0.6, timeout=20,
                 step=0.01)
    state.start_multiband_recording(filename=name)
    start = sim.sim_time()
    assert _wait(lambda: sim.sim_time() - start >= seconds, timeout=seconds + 30,
                 step=0.05)
    with hub.lock:
        result = state.stop_recording()
    assert result["rows"] > seconds * 25 * 0.9 * len(sim.rig.bands) * 0.8, result
    df = pd.read_csv(state.captures_dir / result["filename"])
    t0 = df["ts_hub_ms"].min()
    train = df[df["ts_hub_ms"] < t0 + (train_s - 1.0) * 1000 / sim.speed]
    held = df[df["ts_hub_ms"] >= t0 + train_s * 1000 / sim.speed]
    train_name = name.replace(".csv", "_train.csv")
    train.to_csv(state.captures_dir / train_name, index=False)
    return train_name, held, df


def _curl_r2(held, model_path, role):
    """Held-out R^2 of a trained model against the labeler's curls (the four
    LASK5 piston columns)."""
    import pickle
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    rows = held[held["role"] == role]
    assert len(rows) > 100
    feats = list(model.feature_names_in_)
    labels = [c for c in rows.columns if c.startswith("label_")]
    assert len(labels) == 4
    pred = model.predict(rows[feats])
    from sklearn.metrics import r2_score
    return float(r2_score(rows[labels], pred))


SPEED = 8.0


class TestRecordTrainPredict:
    def test_default_noise_trains_a_model_that_holds_out(self, hub):
        """40 s of the two-band kit, per-hand models, scored on a held-out tail."""
        sim = _start_sim(hub, speed=SPEED)
        try:
            _wait_for_kit(hub, sim)
            train_name, held, full = _record_and_split(hub, sim, "rehearsal.csv")
        finally:
            sim.stop()

        assert set(full["role"]) == {"left", "right"}
        assert {c for c in full.columns if c.startswith("R")} >= {"R0C0", "R3C14"}
        assert len([c for c in full.columns if c.startswith("R")
                    and c[1].isdigit()]) == 60
        # Matching by the LASK5 labeler: the pistons land in label_0..3 as 0..1.
        labels = full[[f"label_{i}" for i in range(4)]]
        assert labels.min().min() >= 0.0 and labels.max().max() <= 1.0
        assert labels.max().max() > 0.8          # the fingers really curl

        r2 = {}
        for i, role in enumerate(("left", "right")):
            if i:
                time.sleep(1.1)       # model dirs are named to the second
            out = hub.state.train_from_captures([train_name], role=role)
            assert out["active"] and out["activate_error"] is None
            r2[role] = _curl_r2(held, out["model_path"], role)
        assert r2["left"] > 0.6 and r2["right"] > 0.6, r2

        # ...and the live engines predict from the sim's own frames.
        hub.state.set_inference_enabled(True)
        hub.state._snapshot()                    # refreshes the band -> role map
        sim2 = _start_sim(hub, speed=SPEED)
        try:
            assert _wait(lambda: {"flexgrid-sim-left", "flexgrid-sim-right"} <=
                         set(hub.state._inference_by_device), 10)
            by_dev = hub.state._inference_by_device
            assert by_dev["flexgrid-sim-left"]["role"] == "left"
            assert by_dev["flexgrid-sim-right"]["role"] == "right"
            assert len(by_dev["flexgrid-sim-left"]["values"]) == 4
        finally:
            sim2.stop()

    def test_high_noise_trains_a_weak_model(self, hub):
        sim = _start_sim(hub, speed=SPEED, cfg={"bands": 1, "noise": "high"})
        try:
            _wait_for_kit(hub, sim)
            train_name, held, _ = _record_and_split(hub, sim, "noisy.csv")
        finally:
            sim.stop()
        out = hub.state.train_from_captures([train_name], role="left")
        weak = _curl_r2(held, out["model_path"], "left")
        assert weak < 0.2, weak


# ---- TLS: wss:// and https:// against a real uvicorn server ----------------------

def _make_cert(tmp_path):
    exe = shutil.which("openssl")
    if not exe:
        pytest.skip("openssl not available to mint a test certificate")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    r = subprocess.run(
        [exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost"],
        capture_output=True, timeout=60)
    if r.returncode != 0 or not cert.exists():
        pytest.skip("openssl could not create a certificate: %r" % r.stderr[-200:])
    return str(cert), str(key)


class Server:
    """create_app() served by uvicorn on a thread, optionally with TLS."""

    def __init__(self, tmp_path, tls):
        import uvicorn
        from openmuscle.web.app import create_app
        self.http_port = _free_tcp_port()
        self.udp_port = _free_udp_port()
        self.announce_port = _free_udp_port()
        self.app = create_app(udp_port=self.udp_port,
                              captures_dir=str(tmp_path / "captures"),
                              announce_port=self.announce_port)
        self.state = self.app.state.app_state
        kw = {}
        if tls:
            kw["ssl_certfile"], kw["ssl_keyfile"] = _make_cert(tmp_path)
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=self.http_port,
            log_level="warning", **kw))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        assert _wait(lambda: self.server.started, 20), "server did not start"
        return self

    def __exit__(self, *exc):
        # The broadcaster parks an executor thread in packet_queue.get(); wake
        # it so the event loop can finish shutting down instead of waiting it out.
        self.state.listener.packet_queue.put(None)
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.mark.parametrize("tls, labeler, device_id, device_type", [
    (True, "quest", "quest-sim", "quest_hand"),
    (True, "webcam", "webcam-right", "webcam_hand"),
    (False, "quest", "quest-sim", "quest_hand"),
])
def test_hand_label_stream_follows_the_server_scheme(
        sandbox, tmp_path, tls, labeler, device_id, device_type):
    """wss:// when the server is HTTPS (the auto-loaded mkcert case), ws:// when
    it is plain, and the roles get tagged through the matching https/http REST."""
    with Server(tmp_path, tls) as srv:
        scheme = "https" if tls else "http"
        assert net.detect_web_scheme("127.0.0.1", srv.http_port) == scheme
        logs = []
        sim = RehearsalSimulator(
            RehearsalConfig(bands=1, labeler=labeler), udp_port=srv.udp_port,
            announce_port=srv.announce_port, web_port=srv.http_port,
            web_scheme="auto", speed=2.0, log=logs.append)
        sim.start()
        try:
            assert _wait(lambda: device_id in srv.state.devices, 20), logs
            assert srv.state.devices[device_id].device_type == device_type
            assert len(srv.state.devices[device_id].last_values) == 25 * 7
            assert _wait(lambda: "flexgrid-sim-left" in srv.state.devices, 10)
            # roles tagged over REST (the bands are discovered via beacon)
            assert sim.tagged.wait(20), logs
            disc = net.http_json(scheme, "127.0.0.1", srv.http_port,
                                 "/api/discovery")
            assert {d["device_id"]: d["role"] for d in disc} == {
                "flexgrid-sim-left": "left"}
            assert disc[0]["matrix"] == [15, 4]
            assert any("label stream connected: %s://" % ("wss" if tls else "ws")
                       in m for m in logs), logs
        finally:
            sim.stop()


def test_legacy_quest_helper_speaks_wss(sandbox, tmp_path):
    """The shared ws_connect helper (used by `--device-type quest_hand/combo`
    and replay) reaches an HTTPS server's /ws/quest."""
    with Server(tmp_path, tls=True) as srv:
        scheme = net.resolve_scheme("auto", "127.0.0.1", srv.http_port)
        assert scheme == "https"
        with net.ws_connect(scheme, "127.0.0.1", srv.http_port, "/ws/quest") as ws:
            ws.send(json.dumps(hand_frame(0.5)))
            assert _wait(lambda: "quest-sim" in srv.state.devices, 10)
        # a plain ws:// client is refused by the TLS port, with a clear error
        with pytest.raises(Exception):
            net.ws_connect("http", "127.0.0.1", srv.http_port, "/ws/quest").close()


@pytest.mark.parametrize("tls", [False, True])
@pytest.mark.parametrize("device_type", ["quest_hand", "combo"])
def test_legacy_quest_modes_stream_end_to_end(sandbox, tmp_path, tls, device_type):
    """`--device-type quest_hand` / `combo` actually deliver frames: the hand
    shows up over /ws/quest (wss:// for an HTTPS server) and, for combo, the
    correlated flexgrid UDP device too."""
    from openmuscle.simulate.transmitter import _generate_quest
    with Server(tmp_path, tls) as srv:
        stop = threading.Event()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        th = threading.Thread(
            target=_generate_quest,
            args=(sock, device_type, "127.0.0.1", srv.udp_port, srv.http_port,
                  "auto"),
            kwargs={"stop_event": stop}, daemon=True)
        th.start()
        try:
            assert _wait(lambda: "quest-sim" in srv.state.devices, 20)
            if device_type == "combo":
                assert _wait(lambda: "flexgrid-sim" in srv.state.devices, 20)
            else:
                assert "flexgrid-sim" not in srv.state.devices
        finally:
            stop.set()
            th.join(timeout=10)
            sock.close()
        assert not th.is_alive()


def test_simulate_rejects_non_positive_speed():
    for bad in ("0", "-1"):
        res = CliRunner().invoke(main, ["simulate", "--device-type",
                                        "rehearsal", "--speed", bad])
        assert res.exit_code == 2, res.output
        assert "--speed" in res.output


def test_label_stream_failure_is_printed_not_silent(sandbox):
    logs = []
    sim = RehearsalSimulator(
        RehearsalConfig(bands=1, labeler="quest"), udp_port=_free_udp_port(),
        announce_port=_free_udp_port(), web_port=_free_tcp_port(),
        web_scheme="http", speed=2.0, tag_roles=False, log=logs.append)
    sim.start()
    try:
        assert _wait(lambda: any("label stream" in m and "failed" in m
                                 for m in logs), 10), logs
    finally:
        sim.stop()
    assert any("Is `openmuscle web` running" in m for m in logs)


# ---- CLI -----------------------------------------------------------------------

class TestCli:
    def test_help_lists_the_rehearsal_options(self):
        out = CliRunner().invoke(main, ["simulate", "--help"]).output
        for flag in ("rehearsal", "--bands", "--labeler", "--noise", "--seed",
                     "--web-scheme", "--announce-port", "--duration"):
            assert flag in out

    def test_bad_noise_is_a_usage_error(self):
        r = CliRunner().invoke(main, ["simulate", "--device-type", "rehearsal",
                                      "--noise", "loud"])
        assert r.exit_code == 2 and "--noise" in r.output

    def test_bad_bands_is_a_usage_error(self):
        r = CliRunner().invoke(main, ["simulate", "--device-type", "rehearsal",
                                      "--bands", "3"])
        assert r.exit_code == 2

    def test_runs_and_stops_by_itself(self):
        r = CliRunner().invoke(main, [
            "simulate", "--device-type", "rehearsal", "--duration", "2",
            "--speed", "10", "--no-tag-roles", "--bands", "1", "--noise", "low",
            "--port", str(_free_udp_port()),
            "--announce-port", str(_free_udp_port())])
        assert r.exit_code == 0, r.output
        assert "Rehearsal kit: 1 band(s) + lask5 labeler, noise x0.4" in r.output
        assert "Stopped after" in r.output
