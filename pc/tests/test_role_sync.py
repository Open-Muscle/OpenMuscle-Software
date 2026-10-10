"""Roles travel both ways between the Studio and a band.

A band keeps its own role (set_role verb; reported in the get_info reply and in
the status frame's meta.role) and the Studio keeps a tag per device. These tests
run a fake band that speaks the firmware's set_role/get_info contract and check:
adoption on discovery and on a status frame, the Studio's change sending the
verb, failure keeping the local role and saying so, the hub-local devices (LASK5)
and the rehearsal simulator behaving like a real band.
"""

import json
import socket
import time

import pytest

from openmuscle.discovery.manager import DiscoveryManager, ROLE_HOLDOFF_S
from openmuscle.protocol.parser import parse_packet
from openmuscle.simulate.rehearsal import RehearsalConfig, RehearsalRig, RehearsalSimulator
from openmuscle.web.state import AppState

from tests.asgi_helper import call_json
from tests.test_discovery import FakeV4CmdServer, _announce


class FakeBand(FakeV4CmdServer):
    """FakeV4CmdServer plus the firmware's set_role verb and role in get_info."""

    def __init__(self, role="", supports_set_role=True, **kw):
        super().__init__(**kw)
        self.role = role
        self.supports_set_role = supports_set_role

    def _handle(self, line):
        pkt = json.loads(line.decode("utf-8"))
        data = pkt.get("data") or {}
        verb = data.get("verb")
        if verb == "set_role" and self.supports_set_role:
            self.received.append((verb, data))
            role = data.get("role", "")
            # DeviceState.set_role clamps anything unknown to "".
            self.role = role if role in ("left", "right", "labeler") else ""
            return self._ack(pkt.get("msg_id"), "ok", {"role": self.role})
        out = super()._handle(line)
        if verb == "get_info" and self.role:
            ack = json.loads(out.decode("utf-8"))
            ack["data"]["role"] = self.role
            return json.dumps(ack).encode("utf-8")
        return out


@pytest.fixture
def band():
    b = FakeBand(device_id="flexgrid-rs01")
    yield b
    b.stop()


@pytest.fixture
def mgr(tmp_path):
    m = DiscoveryManager(pc_host="127.0.0.1", udp_port=3141,
                         cache_path=str(tmp_path / "cache.json"),
                         auto_subscribe=False)
    yield m
    m.stop()


def _known(mgr, band, ip="127.0.0.1"):
    """Register the fake band in the manager the way a beacon would."""
    return mgr.on_announce(_announce(device_id=band.device_id, cmd=band.port), ip)


def _snap(mgr, did):
    return next(d for d in mgr.snapshot() if d["device_id"] == did)


def _status_frame(device_id, role, dev="flexgrid"):
    """A v1.0 status frame as the band sends it, with meta.role."""
    raw = json.dumps({"v": "1.0", "type": dev, "id": device_id, "ts": 1,
                      "data": {"matrix": [[0] * 4] * 15},
                      "meta": {"vbat": 4.0, "uptime_s": 10, "role": role}})
    return parse_packet(raw.encode("utf-8"))


# ---- band -> Studio -------------------------------------------------------------

class TestAdoption:
    def test_adopted_when_discovered_by_get_info(self, mgr):
        band = FakeBand(device_id="flexgrid-rs02", role="right")
        try:
            dev = mgr.probe("127.0.0.1", band.port)
            assert dev.role == "right"
            assert _snap(mgr, "flexgrid-rs02")["band_role"] == "right"
            # And it is persisted with the rest of the device cache.
            entry = next(e for e in mgr._load_cache()
                         if e["device_id"] == "flexgrid-rs02")
            assert entry["role"] == "right"
        finally:
            band.stop()

    def test_untagged_band_stays_untagged(self, mgr):
        band = FakeBand(device_id="flexgrid-rs03", role="")
        try:
            dev = mgr.probe("127.0.0.1", band.port)
            assert dev.role == ""
            assert _snap(mgr, "flexgrid-rs03")["band_role"] == ""
        finally:
            band.stop()

    def test_cached_studio_role_wins_over_first_report(self, mgr):
        band = FakeBand(device_id="flexgrid-rs04", role="right")
        try:
            dev = mgr.probe("127.0.0.1", band.port, role="left")
            assert dev.role == "left"
            assert _snap(mgr, "flexgrid-rs04")["band_role"] == "right"
        finally:
            band.stop()

    def test_adopted_from_a_status_frame(self, mgr, band):
        _known(mgr, band)
        assert _snap(mgr, band.device_id)["role"] == ""
        assert mgr.observe_role(band.device_id, "left") is True
        assert _snap(mgr, band.device_id)["role"] == "left"

    def test_band_retag_is_followed(self, mgr, band):
        _known(mgr, band)
        mgr.observe_role(band.device_id, "left")
        assert mgr.observe_role(band.device_id, "left") is False   # unchanged
        assert mgr.observe_role(band.device_id, "right") is True   # re-tagged
        assert _snap(mgr, band.device_id)["role"] == "right"

    def test_band_without_a_role_never_clears_the_tag(self, mgr, band):
        _known(mgr, band)
        mgr.set_role(band.device_id, "left")
        assert mgr.observe_role(band.device_id, "") is False
        assert _snap(mgr, band.device_id)["role"] == "left"

    def test_nonsense_report_is_ignored(self, mgr, band):
        _known(mgr, band)
        assert mgr.observe_role(band.device_id, "sideways") is False
        assert mgr.observe_role(band.device_id, None) is False
        assert mgr.observe_role(band.device_id, 3) is False
        assert _snap(mgr, band.device_id)["role"] == ""
        assert mgr.observe_role("not-a-device", "left") is False

    def test_local_choice_survives_the_bands_unchanged_report(self, mgr, band):
        """Studio set left; the band (push failed) keeps saying right. The
        repeat of what it said before must not undo the Studio's choice."""
        _known(mgr, band)
        mgr.observe_role(band.device_id, "right")        # adopted
        mgr.set_role(band.device_id, "left")             # Studio, hub-local
        assert mgr.observe_role(band.device_id, "right") is False
        assert _snap(mgr, band.device_id)["role"] == "left"

    def test_app_state_feeds_status_frame_roles_to_discovery(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openmuscle.discovery.manager.default_cache_path",
                            lambda: str(tmp_path / "devices.json"))
        state = AppState(udp_port=53961, captures_dir=str(tmp_path / "caps"),
                         discovery_announce_port=53962)
        try:
            state.discovery.auto_subscribe = False
            state.discovery.on_announce(_announce(device_id="flexgrid-rs05"),
                                        "10.0.0.9")
            state._handle_packet(_status_frame("flexgrid-rs05", ""))
            assert _snap(state.discovery, "flexgrid-rs05")["role"] == ""
            state._handle_packet(_status_frame("flexgrid-rs05", "right"))
            assert _snap(state.discovery, "flexgrid-rs05")["role"] == "right"
            # The next snapshot hands the role to the inference router too.
            state._snapshot()
            assert state._role_by_device["flexgrid-rs05"] == "right"
        finally:
            state.stop()


# ---- Studio -> band -------------------------------------------------------------

class TestStudioChange:
    def test_change_sends_the_verb(self, mgr, band):
        _known(mgr, band)
        res = mgr.assign_role(band.device_id, "Right")
        assert res == {"ok": True, "role": "right", "band": "updated", "message": ""}
        assert ("set_role", {"verb": "set_role", "role": "right"}) in band.received
        assert band.role == "right"
        snap = _snap(mgr, band.device_id)
        assert snap["role"] == "right" and snap["band_role"] == "right"
        assert snap["role_note"] == ""

    def test_clearing_sends_an_empty_role(self, mgr, band):
        _known(mgr, band)
        mgr.assign_role(band.device_id, "left")
        res = mgr.assign_role(band.device_id, "")
        assert res["band"] == "updated" and res["role"] == ""
        assert band.role == ""
        assert _snap(mgr, band.device_id)["role"] == ""

    def test_invalid_or_unknown_is_refused_without_a_send(self, mgr, band):
        _known(mgr, band)
        assert mgr.assign_role(band.device_id, "bogus")["ok"] is False
        assert mgr.assign_role("nope", "left")["ok"] is False
        assert not [r for r in band.received if r[0] == "set_role"]

    def test_stale_status_frame_right_after_a_push_is_ignored(self, mgr, band):
        _known(mgr, band)
        mgr.observe_role(band.device_id, "left")         # band said left
        mgr.assign_role(band.device_id, "right")
        # A frame sent before the band processed the verb still says left.
        assert mgr.observe_role(band.device_id, "left") is False
        assert _snap(mgr, band.device_id)["role"] == "right"

    def test_after_the_holdoff_a_real_retag_is_followed_again(self, mgr, band):
        _known(mgr, band)
        mgr.assign_role(band.device_id, "right")
        with mgr._lock:
            mgr._devices[band.device_id].role_pushed_at -= ROLE_HOLDOFF_S + 1
        assert mgr.observe_role(band.device_id, "left") is True
        assert _snap(mgr, band.device_id)["role"] == "left"

    def test_unreachable_band_keeps_the_local_role_and_says_so(self, mgr):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()
        mgr.on_announce(_announce(device_id="flexgrid-gone", cmd=dead_port),
                        "127.0.0.1")
        res = mgr.assign_role("flexgrid-gone", "left")
        assert res["ok"] is True and res["band"] == "failed"
        assert "band itself was not updated" in res["message"]
        snap = _snap(mgr, "flexgrid-gone")
        assert snap["role"] == "left"
        assert "not updated" in snap["role_note"]

    def test_band_that_errors_keeps_the_local_role_and_says_so(self, mgr):
        old = FakeBand(device_id="flexgrid-old", supports_set_role=False)
        try:
            _known(mgr, old)
            res = mgr.assign_role(old.device_id, "right")
            assert res["band"] == "failed"
            assert "unknown_verb" in res["message"]
            assert _snap(mgr, old.device_id)["role"] == "right"
        finally:
            old.stop()

    def test_failed_push_is_not_undone_by_the_bands_old_report(self, mgr):
        old = FakeBand(device_id="flexgrid-old2", supports_set_role=False,
                       role="left")
        try:
            mgr.probe("127.0.0.1", old.port)             # adopts left
            assert mgr.assign_role(old.device_id, "right")["band"] == "failed"
            assert mgr.observe_role(old.device_id, "left") is False
            assert _snap(mgr, old.device_id)["role"] == "right"
            # A later success clears the note.
            old.supports_set_role = True
            assert mgr.assign_role(old.device_id, "right")["band"] == "updated"
            assert _snap(mgr, old.device_id)["role_note"] == ""
        finally:
            old.stop()

    def test_lask5_keeps_its_role_on_the_hub_only(self, mgr):
        lask = FakeBand(device_id="lask5-rs", device_type="lask5")
        try:
            mgr.on_announce(dict(_announce(device_id="lask5-rs", dev="lask5",
                                           cmd=lask.port)), "127.0.0.1")
            res = mgr.assign_role("lask5-rs", "labeler")
            assert res["ok"] is True and res["band"] == "local"
            assert res["message"] == ""
            assert _snap(mgr, "lask5-rs")["role"] == "labeler"
            assert not [r for r in lask.received if r[0] == "set_role"]
        finally:
            lask.stop()

    def test_set_role_stays_hub_local(self, mgr, band):
        _known(mgr, band)
        assert mgr.set_role(band.device_id, "left") is True
        assert not [r for r in band.received if r[0] == "set_role"]


# ---- the REST route ---------------------------------------------------------------

class TestRoute:
    @pytest.fixture
    def app(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openmuscle.discovery.manager.default_cache_path",
                            lambda: str(tmp_path / "devices.json"))
        from openmuscle.web.app import create_app
        app = create_app(udp_port=53963, captures_dir=str(tmp_path / "caps"),
                         announce_port=53964, quiet=True)
        app.state.app_state.discovery.auto_subscribe = False
        yield app
        app.state.app_state.stop()

    def test_route_pushes_and_reports(self, app, band):
        disc = app.state.app_state.discovery
        _known(disc, band)
        status, out = call_json(app, "POST", "/api/discovery/role",
                                body={"device_id": band.device_id, "role": "left"})
        assert status == 200
        assert out == {"device_id": band.device_id, "role": "left",
                       "band": "updated", "warning": None}
        assert band.role == "left"

    def test_route_warns_when_the_band_was_not_updated(self, app):
        disc = app.state.app_state.discovery
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
        s.close()
        disc.on_announce(_announce(device_id="flexgrid-gone2", cmd=dead), "127.0.0.1")
        status, out = call_json(app, "POST", "/api/discovery/role",
                                body={"device_id": "flexgrid-gone2", "role": "right"})
        assert status == 200
        assert out["band"] == "failed" and "not updated" in out["warning"]
        assert _snap(disc, "flexgrid-gone2")["role"] == "right"

    def test_route_rejects_unknown_device(self, app):
        status, out = call_json(app, "POST", "/api/discovery/role",
                                body={"device_id": "ghost", "role": "left"})
        assert status == 400


# ---- the rehearsal simulator behaves like a real band -----------------------------

class TestRehearsalBand:
    def test_rig_status_frame_reports_the_bands_role(self):
        rig = RehearsalRig(RehearsalConfig())
        left = rig.bands[0].device_id

        def status_role():
            for k in range(60):
                for pkt in rig.tick(k / 25.0)["bands"]:
                    if pkt["id"] == left and "meta" in pkt:
                        return pkt["meta"]["role"]
            raise AssertionError("no status frame")

        assert status_role() == ""
        assert rig.set_device_role(left, "Left") == "left"
        assert status_role() == "left"
        assert rig.set_device_role(left, "sideways") == ""
        assert status_role() == ""

    def test_sim_honours_set_role_over_its_command_port(self, tmp_path):
        sim = RehearsalSimulator(
            RehearsalConfig(), udp_port=53965, announce_port=53966,
            tag_roles=False, log=lambda m: None)
        sim.start()
        try:
            bid = sim.rig.bands[0].device_id
            lid = "lask5-sim"
            mgr = DiscoveryManager(pc_host="127.0.0.1",
                                   cache_path=str(tmp_path / "c.json"),
                                   auto_subscribe=False)
            try:
                dev = mgr.probe("127.0.0.1", sim.devices[bid].cmd_port)
                assert dev.role == ""
                res = mgr.assign_role(bid, "left")
                assert res["band"] == "updated"
                assert sim.rig.device_roles[bid] == "left"
                # A new hub that probes it afterwards adopts that role.
                mgr2 = DiscoveryManager(pc_host="127.0.0.1",
                                        cache_path=str(tmp_path / "c2.json"),
                                        auto_subscribe=False)
                try:
                    assert mgr2.probe("127.0.0.1",
                                      sim.devices[bid].cmd_port).role == "left"
                finally:
                    mgr2.stop()
                # The sim LASK5 has no role verb: the hub keeps that tag locally.
                mgr.probe("127.0.0.1", sim.devices[lid].cmd_port)
                res = mgr.assign_role(lid, "labeler")
                assert res["band"] == "local"
                assert lid not in sim.rig.device_roles
            finally:
                mgr.stop()
        finally:
            sim.stop()
