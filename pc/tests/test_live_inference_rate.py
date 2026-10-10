"""Live prediction must not fall behind the bands.

A real band sends about 60 frames a second and a random-forest predict costs
several milliseconds. Predicting every frame on the server's event loop let the
packet queue grow without bound and froze the whole page after training, so
live inference is rate-limited per band, and the loaded model runs on one
thread (a thread pool per single-row predict is slower than a plain call).
"""

import pickle
import time

from sklearn.ensemble import RandomForestRegressor

from openmuscle.protocol import OpenMusclePacket
from openmuscle.web.inference import InferenceEngine
from openmuscle.web.state import AppState


class _CountingEngine:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def predict(self, matrix):
        self.calls += 1
        return [0.5, 0.5, 0.5, 0.5, 0.5]


def _packet(device_id="fg-1"):
    return OpenMusclePacket(
        version="1.0", device_type="flexgrid", device_id=device_id,
        timestamp_ms=1, data={"matrix": [[0] * 4 for _ in range(15)]},
        receive_time=time.time())


def _state(tmp_path, port):
    s = AppState(udp_port=port, captures_dir=str(tmp_path), enable_discovery=False)
    s.engine = _CountingEngine()
    s.inference_enabled = True
    return s


def test_a_burst_of_frames_is_predicted_at_the_limited_rate(tmp_path):
    s = _state(tmp_path, 53981)
    for _ in range(200):                 # 200 frames back to back
        s._handle_packet(_packet())
    assert s.engine.calls == 1           # the rest fall inside the first window
    assert s._last_inference_values is not None


def test_each_band_has_its_own_window(tmp_path):
    s = _state(tmp_path, 53982)
    for _ in range(50):
        s._handle_packet(_packet("fg-left"))
        s._handle_packet(_packet("fg-right"))
    assert s.engine.calls == 2           # one prediction per band
    assert set(s._inference_by_device) == {"fg-left", "fg-right"}


def test_prediction_resumes_after_the_window(tmp_path, monkeypatch):
    s = _state(tmp_path, 53983)
    clock = [1000.0]
    monkeypatch.setattr("openmuscle.web.state.time.monotonic", lambda: clock[0])
    s._handle_packet(_packet())
    clock[0] += 0.5 / AppState.INFERENCE_MAX_HZ
    s._handle_packet(_packet())          # too soon
    clock[0] += 1.0 / AppState.INFERENCE_MAX_HZ
    s._handle_packet(_packet())          # due again
    assert s.engine.calls == 2


def test_paused_prediction_does_not_use_the_window(tmp_path):
    s = _state(tmp_path, 53984)
    s.inference_enabled = False
    s._handle_packet(_packet())
    assert s.engine.calls == 0
    s.inference_enabled = True
    s._handle_packet(_packet())
    assert s.engine.calls == 1


def test_loaded_forest_runs_on_one_thread(tmp_path):
    model = RandomForestRegressor(n_estimators=3, n_jobs=-1, random_state=0)
    model.fit([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]], [[0.1, 0.2], [0.9, 0.8], [0.5, 0.5]])
    path = tmp_path / "model.pkl"
    with open(path, "wb") as f:
        pickle.dump(model, f)
    eng = InferenceEngine(str(path))
    assert eng.model.n_jobs == 1
    assert eng.predict([[0.2], [0.8]]) is not None
