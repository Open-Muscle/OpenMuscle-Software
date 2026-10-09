"""Offline browser assets: the pinned manifest, the verified fetch, and serving
them under /static/vendor/ (no network is used; downloads come from a fake)."""

import asyncio
import hashlib
import io
from pathlib import Path

import pytest
from click.testing import CliRunner

from openmuscle import assets, paths
from openmuscle.assets import Asset, AssetError


def _fake_manifest(files):
    """files: {rel: bytes} -> a manifest pinned to those bytes (sha256 for most,
    md5 only for the model file, like the real manifest)."""
    out = []
    for rel, data in files.items():
        group = "three" if rel.startswith("three/") else "tracker"
        if rel.endswith(".task"):
            out.append(Asset(group, rel, "http://fake/" + rel, len(data), None,
                             hashlib.md5(data).hexdigest()))
        else:
            out.append(Asset(group, rel, "http://fake/" + rel, len(data),
                             hashlib.sha256(data).hexdigest(),
                             optional="nosimd" in rel))
    return tuple(out)


FILES = {
    "mediapipe/vision_bundle.mjs": b"export const x = 1;\n" * 10,
    "mediapipe/wasm/vision_wasm_internal.js": b"// loader\n" * 7,
    "mediapipe/wasm/vision_wasm_internal.wasm": b"\x00asm\x01\x00\x00\x00" + bytes(range(256)) * 4,
    "mediapipe/hand_landmarker.task": b"MODEL" * 100,
    "mediapipe/wasm/vision_wasm_nosimd_internal.js": b"// nosimd\n" * 3,
    "three/three.module.js": b"export const THREE = {};\n" * 20,
}
NOSIMD = "mediapipe/wasm/vision_wasm_nosimd_internal.js"


def _opener(served, calls=None):
    def open_url(url):
        if calls is not None:
            calls.append(url)
        rel = url.split("http://fake/", 1)[1]
        return io.BytesIO(served[rel])
    return open_url


# ---------- the real manifest ----------

def test_real_manifest_is_pinned_and_well_formed():
    rels = set()
    for a in assets.MANIFEST:
        assert a.url.startswith("https://"), a.rel
        assert a.size > 0
        assert a.sha256 or a.md5, f"{a.rel} has no checksum"
        if a.sha256:
            assert len(a.sha256) == 64 and int(a.sha256, 16) >= 0
        if a.md5:
            assert len(a.md5) == 32 and int(a.md5, 16) >= 0
        assert a.group in assets.GROUP_LABELS
        assert not a.rel.startswith("/") and ".." not in a.rel
        rels.add(a.rel)
    assert {"mediapipe/vision_bundle.mjs", "mediapipe/wasm/vision_wasm_internal.js",
            "mediapipe/wasm/vision_wasm_internal.wasm", "mediapipe/hand_landmarker.task",
            "three/three.module.js"} <= rels


def test_only_the_model_lacks_a_sha256_pin():
    # The model is pinned by size and Google's MD5 until a maintainer pins its
    # sha256 (see the comment above MANIFEST); everything from npm has a sha256.
    unpinned = [a.rel for a in assets.MANIFEST if not a.sha256]
    assert unpinned == ["mediapipe/hand_landmarker.task"]


def test_real_manifest_versions_match_what_the_pages_load():
    static = Path(__file__).resolve().parents[1] / "src" / "openmuscle" / "web" / "static"
    assert f"TRACKER_VERSION = '{assets.TRACKER_VERSION}'" in (
        static / "webcam" / "webcam.js").read_text("utf-8")
    assert f"three@{assets.THREE_VERSION}" in (static / "index.html").read_text("utf-8")
    for a in assets.MANIFEST:
        assert (assets.TRACKER_VERSION in a.url) or (assets.THREE_VERSION in a.url) \
            or a.rel.endswith(".task"), a.rel


def test_real_download_sizes():
    need = sum(a.size for a in assets.MANIFEST if a.group == "tracker" and not a.optional)
    assert 17.5e6 < need < 17.8e6          # about 17.6 MB
    full = sum(a.size for a in assets.MANIFEST if a.group == "tracker")
    assert 27.0e6 < full < 27.3e6          # about 27.1 MB with the no-SIMD files


def test_binaries_are_not_committed():
    static = Path(__file__).resolve().parents[1] / "src" / "openmuscle" / "web" / "static"
    assert not (static / "vendor").exists()
    for ext in ("*.wasm", "*.task"):
        assert not list(static.rglob(ext))


# ---------- fetch ----------

def test_fetch_downloads_verifies_and_installs(tmp_path):
    manifest = _fake_manifest(FILES)
    got = assets.fetch(tmp_path, manifest, opener=_opener(FILES))
    # the optional no-SIMD file is not fetched by default
    assert {p.relative_to(tmp_path).as_posix() for p in got} == set(FILES) - {NOSIMD}
    for p in got:
        assert p.read_bytes() == FILES[p.relative_to(tmp_path).as_posix()]
    assert (tmp_path / "NOTICE.txt").is_file()
    assert not list(tmp_path.rglob("*.part"))
    assert assets.group_ready("tracker", tmp_path, manifest)
    assert assets.group_ready("three", tmp_path, manifest)


def test_fetch_skips_what_is_already_correct(tmp_path):
    manifest = _fake_manifest(FILES)
    assets.fetch(tmp_path, manifest, opener=_opener(FILES))
    calls = []
    assert assets.fetch(tmp_path, manifest, opener=_opener(FILES, calls)) == []
    assert calls == []
    assets.fetch(tmp_path, manifest, force=True, opener=_opener(FILES, calls))
    assert len(calls) == 5


def test_fetch_include_optional(tmp_path):
    manifest = _fake_manifest(FILES)
    assets.fetch(tmp_path, manifest, include_optional=True, opener=_opener(FILES))
    assert (tmp_path / NOSIMD).is_file()


def test_checksum_mismatch_aborts_and_installs_nothing_for_that_file(tmp_path):
    manifest = _fake_manifest(FILES)
    tampered = dict(FILES)
    tampered["mediapipe/wasm/vision_wasm_internal.js"] = b"// evil!!\n" * 7   # same size
    with pytest.raises(AssetError, match="checksum does not match"):
        assets.fetch(tmp_path, manifest, opener=_opener(tampered))
    assert not (tmp_path / "mediapipe/wasm/vision_wasm_internal.js").exists()
    assert not list(tmp_path.rglob("*.part"))
    # it stopped at the bad file: later files were not fetched
    assert not (tmp_path / "mediapipe/hand_landmarker.task").exists()


def test_md5_pinned_file_mismatch_is_caught(tmp_path):
    manifest = _fake_manifest(FILES)
    tampered = dict(FILES)
    tampered["mediapipe/hand_landmarker.task"] = b"EVILX" * 100
    with pytest.raises(AssetError, match="checksum does not match"):
        assets.fetch(tmp_path, manifest, opener=_opener(tampered))
    assert not (tmp_path / "mediapipe/hand_landmarker.task").exists()


def test_wrong_size_is_rejected(tmp_path):
    manifest = _fake_manifest(FILES)
    short = dict(FILES)
    short["mediapipe/vision_bundle.mjs"] = FILES["mediapipe/vision_bundle.mjs"][:-5]
    with pytest.raises(AssetError, match="wrong size"):
        assets.fetch(tmp_path, manifest, opener=_opener(short))
    longer = dict(FILES)
    longer["mediapipe/vision_bundle.mjs"] = FILES["mediapipe/vision_bundle.mjs"] + b"x" * 50
    with pytest.raises(AssetError, match="wrong size"):
        assets.fetch(tmp_path, manifest, opener=_opener(longer))
    assert not (tmp_path / "mediapipe/vision_bundle.mjs").exists()


def test_offline_failure_is_a_plain_message(tmp_path):
    manifest = _fake_manifest(FILES)

    def offline(url):
        raise OSError("getaddrinfo failed")

    with pytest.raises(AssetError, match="Could not download vision_bundle.mjs.*online"):
        assets.fetch(tmp_path, manifest, opener=offline)
    assert not list(tmp_path.rglob("*.part"))


def test_a_corrupted_installed_file_is_not_ready_and_is_replaced(tmp_path):
    manifest = _fake_manifest(FILES)
    assets.fetch(tmp_path, manifest, opener=_opener(FILES))
    victim = tmp_path / "mediapipe/hand_landmarker.task"
    victim.write_bytes(b"BROKE" * 100)               # same size, wrong content
    assert not assets.group_ready("tracker", tmp_path, manifest)
    info = assets.web_status(tmp_path, manifest)
    assert info["groups"]["tracker"]["ready"] is False
    assert info["groups"]["tracker"]["problems"][0]["file"] == "mediapipe/hand_landmarker.task"
    assert info["groups"]["three"]["ready"] is True
    got = assets.fetch(tmp_path, manifest, opener=_opener(FILES))
    assert got == [victim]
    assert assets.group_ready("tracker", tmp_path, manifest)


def test_missing_model_means_tracker_not_ready(tmp_path):
    manifest = _fake_manifest(FILES)
    assets.fetch(tmp_path, manifest, opener=_opener(FILES))
    (tmp_path / "mediapipe/hand_landmarker.task").unlink()
    assert not assets.group_ready("tracker", tmp_path, manifest)
    assert assets.group_ready("three", tmp_path, manifest)


def test_empty_folder_is_not_ready(tmp_path):
    assert not assets.group_ready("tracker", tmp_path / "nope")
    assert not assets.group_ready("three", tmp_path / "nope")


# ---------- CLI ----------

def test_cli_fetch_reports_offline_without_a_traceback(tmp_path, monkeypatch):
    from openmuscle.cli import main

    def offline(url):
        raise OSError("no route to host")

    monkeypatch.setattr(assets, "_open_url", offline)
    r = CliRunner().invoke(main, ["assets", "fetch", "--data-dir", str(tmp_path)])
    assert r.exit_code != 0
    assert "Could not download" in r.output
    assert "Traceback" not in r.output


def test_cli_fetch_and_status_with_fake_download(tmp_path, monkeypatch):
    from openmuscle.cli import main
    monkeypatch.setattr(assets, "MANIFEST", _fake_manifest(FILES))
    monkeypatch.setattr(assets, "_open_url", _opener(FILES))
    runner = CliRunner()
    r = runner.invoke(main, ["assets", "fetch", "--data-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "downloaded and checked" in r.output
    r2 = runner.invoke(main, ["assets", "status", "--data-dir", str(tmp_path), "--hashes"])
    assert r2.exit_code == 0, r2.output
    assert "hand tracker for the webcam page: ready" in r2.output
    assert "sha256=" in r2.output
    assert str(tmp_path / "assets") in r2.output


def test_cli_fetch_again_says_everything_is_in_place(tmp_path, monkeypatch):
    from openmuscle.cli import main
    monkeypatch.setattr(assets, "MANIFEST", _fake_manifest(FILES))
    monkeypatch.setattr(assets, "_open_url", _opener(FILES))
    runner = CliRunner()
    runner.invoke(main, ["assets", "fetch", "--data-dir", str(tmp_path)])
    r = runner.invoke(main, ["assets", "fetch", "--data-dir", str(tmp_path)])
    assert r.exit_code == 0
    assert "already in place" in r.output


# ---------- serving ----------

def _http_get(app, path):
    async def run():
        sent = []
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "root_path": "", "headers": [(b"host", b"localhost")],
                 "client": ("127.0.0.1", 5000), "server": ("127.0.0.1", 8000)}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        await app(scope, receive, send)
        return sent

    sent = asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], headers, body


def _app_with_assets(tmp_path):
    paths.set_data_root(tmp_path / "root")
    assets.fetch(paths.assets_dir(), _fake_manifest(FILES), include_optional=True,
                 opener=_opener(FILES))
    from openmuscle.web.app import create_app
    return create_app(udp_port=53976)


JS_TYPES = ("text/javascript", "application/javascript")


def test_vendor_files_are_served_with_module_and_wasm_types(tmp_path):
    app = _app_with_assets(tmp_path)
    status, headers, body = _http_get(app, "/static/vendor/mediapipe/wasm/vision_wasm_internal.wasm")
    assert status == 200
    assert headers["content-type"].split(";")[0] == "application/wasm"
    assert body == FILES["mediapipe/wasm/vision_wasm_internal.wasm"]
    status, headers, _ = _http_get(app, "/static/vendor/mediapipe/vision_bundle.mjs")
    assert status == 200 and headers["content-type"].split(";")[0] in JS_TYPES
    status, headers, _ = _http_get(app, "/static/vendor/three/three.module.js")
    assert status == 200 and headers["content-type"].split(";")[0] in JS_TYPES
    status, _, body = _http_get(app, "/static/vendor/mediapipe/hand_landmarker.task")
    assert status == 200 and body == FILES["mediapipe/hand_landmarker.task"]


def test_files_fetched_after_the_server_started_are_served(tmp_path):
    paths.set_data_root(tmp_path / "root")
    from openmuscle.web.app import create_app
    app = create_app(udp_port=53972)
    assert _http_get(app, "/static/vendor/three/three.module.js")[0] == 404
    assets.fetch(paths.assets_dir(), _fake_manifest(FILES), opener=_opener(FILES))
    assert _http_get(app, "/static/vendor/three/three.module.js")[0] == 200


def test_vendor_404_when_nothing_is_installed(tmp_path):
    paths.set_data_root(tmp_path / "empty-root")
    from openmuscle.web.app import create_app
    app = create_app(udp_port=53975)
    assert _http_get(app, "/static/vendor/mediapipe/vision_bundle.mjs")[0] == 404


def test_vendor_cannot_read_outside_the_assets_folder(tmp_path):
    app = _app_with_assets(tmp_path)
    (paths.data_root() / "secret.txt").write_text("nope")
    for url in ("/static/vendor/../secret.txt", "/static/vendor/%2e%2e/secret.txt",
                "/static/vendor/..%2fsecret.txt"):
        status, _, body = _http_get(app, url)
        assert status in (307, 400, 404) and b"nope" not in body, url


def test_package_static_still_served(tmp_path):
    paths.set_data_root(tmp_path / "root")
    from openmuscle.web.app import create_app
    app = create_app(udp_port=53974)
    assert _http_get(app, "/static/webcam/webcam.js")[0] == 200
    assert _http_get(app, "/static/webcam/track-logic.js")[0] == 200
    assert _http_get(app, "/static/guide.js")[0] == 200


def test_api_assets_reports_what_is_usable(tmp_path):
    app = _app_with_assets(tmp_path)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/assets")
    info = asyncio.run(route.endpoint())
    assert info["dir"] == str(paths.assets_dir())
    assert set(info["groups"]) == {"tracker", "three"}
    # The app judges files against the real manifest, which the fake files do
    # not satisfy: the page must not be told a wrong copy is usable.
    assert info["groups"]["tracker"]["ready"] is False
    assert info["groups"]["tracker"]["problems"]


def test_dashboard_uses_local_three_only_when_it_is_verified(tmp_path, monkeypatch):
    paths.set_data_root(tmp_path / "root")
    from openmuscle.web import app as webapp
    app = webapp.create_app(udp_port=53973)
    cdn = webapp._THREE_CDN_URL
    status, _, body = _http_get(app, "/")          # nothing installed: the CDN
    assert status == 200 and cdn.encode() in body
    monkeypatch.setattr(webapp.om_assets, "group_ready", lambda g, *a, **k: g == "three")
    status, _, body = _http_get(app, "/")          # installed and verified: local
    assert status == 200
    assert cdn.encode() not in body
    assert b"/static/vendor/three/three.module.js" in body


def test_wasm_type_is_registered():
    import mimetypes
    import openmuscle.web.app  # noqa: F401  (registers the types on import)
    assert mimetypes.guess_type("x.wasm")[0] == "application/wasm"
    assert mimetypes.guess_type("x.mjs")[0] in JS_TYPES
    assert mimetypes.guess_type("x.js")[0] in JS_TYPES
