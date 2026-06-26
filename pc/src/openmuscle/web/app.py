"""FastAPI app for the OpenMuscle web UI.

Launch via the CLI: `openmuscle web --port 8000`
"""

# NOTE: deliberately NOT using `from __future__ import annotations` here.
# That makes all annotations into strings, which breaks FastAPI's body-vs-query
# inference for Pydantic model parameters (it reads "StartRecordingBody" as a
# string and falls back to treating the param as a query string field).

import asyncio
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openmuscle.web.state import AppState


STATIC_DIR = Path(__file__).parent / "static"


def _reveal_path_in_file_manager(path: Path, select_file: bool) -> None:
    """Open `path` in the OS file manager. If `select_file=True` and the
    platform supports it, highlight the file inside its parent folder
    rather than just opening the folder. Raises RuntimeError on failure.

    Whitelist-guarded by the caller: this function does NOT verify that
    `path` is inside captures_dir. That check happens in the route.
    """
    if not path.exists():
        raise RuntimeError(f"Path does not exist: {path}")

    # Explorer / Finder / xdg-open all need *absolute* paths -- they don't
    # inherit our CWD predictably and a relative path like
    # "data/raw/merged/foo.csv" silently fails with "location not found".
    path = path.resolve()

    try:
        if sys.platform.startswith("win"):
            if select_file and path.is_file():
                # explorer /select,"C:\full\path\file.csv" -- highlights file
                subprocess.Popen(["explorer", f"/select,{path}"])
            else:
                # Open the folder itself
                folder = path if path.is_dir() else path.parent
                subprocess.Popen(["explorer", str(folder)])
        elif sys.platform == "darwin":
            if select_file and path.is_file():
                subprocess.Popen(["open", "-R", str(path)])
            else:
                folder = path if path.is_dir() else path.parent
                subprocess.Popen(["open", str(folder)])
        else:
            # Linux / other -- xdg-open only opens directories cleanly
            opener = shutil.which("xdg-open") or shutil.which("gio")
            if opener is None:
                raise RuntimeError("No file-manager opener found (xdg-open / gio)")
            folder = path if path.is_dir() else path.parent
            subprocess.Popen([opener, str(folder)])
    except Exception as e:
        raise RuntimeError(f"Failed to open file manager: {e}")


def create_app(udp_port: int = 3141, captures_dir: Optional[str] = None,
               model_path: Optional[str] = None,
               hand_target: Optional[tuple] = None,
               announce_port: int = 3140) -> FastAPI:
    state = AppState(
        udp_port=udp_port,
        captures_dir=captures_dir,
        model_path=model_path,
        hand_target=hand_target,
        discovery_announce_port=announce_port,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.start()
        task = asyncio.create_task(state.run_broadcaster())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            state.stop()

    app = FastAPI(title="OpenMuscle Web UI", lifespan=lifespan)
    app.state.app_state = state

    # ----- static frontend -----
    # Disable browser caching so live edits to app.js / styles.css are picked up
    # the moment you refresh. This is a developer/local app, not internet-facing.

    @app.middleware("http")
    async def no_cache_static(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        # Cache-bust the HTML entry points + every static asset. Without
        # /vr in this list, Quest Browser cached the old VR HTML (which
        # pointed at app.js without the version querystring), so refreshes
        # kept loading stale JS even after the file changed on disk.
        if path == "/" or path == "/vr" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    # WebXR companion page served at /vr. Quest Browser loads this URL,
    # negotiates 'hand-tracking', opens /ws/quest, and streams XRHand frames.
    # WebXR requires a secure context -- HTTPS over LAN (mkcert) or
    # http://localhost via `adb reverse tcp:8000 tcp:8000` over USB.
    @app.get("/vr")
    async def vr_page():
        return FileResponse(STATIC_DIR / "vr" / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ----- live data over WebSocket -----

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket):
        await websocket.accept()
        state.ws_clients.add(websocket)
        try:
            # Initial snapshot so the client doesn't have to wait
            await websocket.send_json(state._snapshot())
            while True:
                # Keep the connection open; the broadcaster pushes from the
                # server side. We just need to handle disconnects.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.ws_clients.discard(websocket)

    # Inbound WS from the Quest headset. Browsers can't speak UDP so the
    # WebXR client opens this socket and pushes XRHand joint frames as
    # JSON. We feed each frame through ingest_quest_packet, which
    # synthesizes a device_type="quest_hand" OpenMusclePacket and routes
    # it through the same _handle_packet path as UDP devices. Net effect:
    # the Quest looks like any other label-producing device to the
    # recorder, matcher, snapshot, and meta-sidecar code.
    @app.websocket("/ws/quest")
    async def ws_quest(websocket: WebSocket):
        await websocket.accept()
        client = (f"{websocket.client.host}:{websocket.client.port}"
                  if websocket.client else "unknown")
        state.log_buffer.info("quest", f"connected: {client}")
        frame_count = 0
        try:
            while True:
                payload = await websocket.receive_json()
                try:
                    state.ingest_quest_packet(payload)
                    frame_count += 1
                except Exception as e:
                    # Per-frame errors shouldn't kill the socket -- a single
                    # malformed frame happens; the next one is usually fine.
                    state.log_buffer.warn(
                        "quest", f"ingest failed at frame {frame_count}: "
                                 f"{type(e).__name__}: {e}")
        except WebSocketDisconnect:
            state.log_buffer.info(
                "quest", f"disconnected: {client} after {frame_count} frames")
        except Exception as e:
            state.log_buffer.error(
                "quest", f"socket error from {client}: "
                         f"{type(e).__name__}: {e}")

    # ----- REST: devices -----

    @app.get("/api/devices")
    async def list_devices():
        return JSONResponse(state._snapshot()["devices"])

    # ----- REST: native V4 discovery -----

    @app.get("/api/discovery")
    async def list_discovery():
        """Known V4 sources + subscription state (auto-discover replaces the
        manual bridge_subscriber.py)."""
        return JSONResponse(state.discovery.snapshot() if state.discovery else [])

    class ProbeBody(BaseModel):
        ip: str
        cmd_port: Optional[int] = None      # try 8001 then 8002 when omitted

    @app.post("/api/discovery/probe")
    async def discovery_probe(body: ProbeBody):
        """Manually probe an address (lab setup / device not beaconing)."""
        if not state.discovery:
            raise HTTPException(status_code=409, detail="discovery disabled")
        dev = await asyncio.to_thread(state.discovery.probe, body.ip, body.cmd_port)
        if dev is None:
            raise HTTPException(
                status_code=404,
                detail=f"no V4 command server responded at {body.ip}")
        return dev.to_snapshot()

    class ScanBody(BaseModel):
        start: int = 1
        end: int = 254
        timeout: float = 0.5

    @app.post("/api/discovery/scan")
    async def discovery_scan(body: ScanBody):
        """Probe the local /24 for V4 devices that are not beaconing (held by
        another hub), with no manual IP. Cold-cache discovery, the P3
        late-joiner path. Runs to completion (a few seconds) and returns the
        device_ids found plus the refreshed device list."""
        if not state.discovery:
            raise HTTPException(status_code=409, detail="discovery disabled")
        found = await asyncio.to_thread(
            state.discovery.scan_subnet, None, body.start, body.end,
            body.timeout, 32, False)      # background=False -> returns id list
        return {"found": found or [], "devices": state.discovery.snapshot()}

    class DeviceIdBody(BaseModel):
        device_id: str

    @app.post("/api/discovery/subscribe")
    async def discovery_subscribe(body: DeviceIdBody):
        if not state.discovery:
            raise HTTPException(status_code=409, detail="discovery disabled")
        ok = await asyncio.to_thread(state.discovery.subscribe, body.device_id)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=f"could not subscribe to {body.device_id} "
                       f"(unknown, unreachable, or list full)")
        return {"device_id": body.device_id, "subscribed": True}

    @app.post("/api/discovery/unsubscribe")
    async def discovery_unsubscribe(body: DeviceIdBody):
        if not state.discovery:
            raise HTTPException(status_code=409, detail="discovery disabled")
        removed = state.discovery.unsubscribe(body.device_id)
        return {"device_id": body.device_id, "unsubscribed": bool(removed)}

    class RoleBody(BaseModel):
        device_id: str
        role: str = ""      # left / right / labeler, or "" to clear

    @app.post("/api/discovery/role")
    async def discovery_set_role(body: RoleBody):
        """Tag a discovered device with a capture role (hub-local, per device_id).
        Drives multi-band capture: tag two bands left/right + the labeler."""
        if not state.discovery:
            raise HTTPException(status_code=409, detail="discovery disabled")
        ok = state.discovery.set_role(body.device_id, body.role)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=f"could not set role for {body.device_id} "
                       f"(unknown device, or invalid role)")
        return {"device_id": body.device_id, "role": (body.role or "").strip().lower()}

    # ----- REST: recording -----

    class StartRecordingBody(BaseModel):
        # All optional -- recorder auto-picks first flexgrid + first lask5 if
        # the operator doesn't specify. Pass label_device_id="" (empty string)
        # to deliberately record sensor-only without pairing.
        sensor_device_id: Optional[str] = None
        label_device_id: Optional[str] = None
        filename: Optional[str] = None
        # If None, AppState picks per-device-type (lask5=100, quest_hand=175).
        window_ms: Optional[int] = None
        # schema-v2 role tag for the sensor band: left / right / labeler
        # (lowercase wire tokens). Defaults to left for a single-source capture.
        role: Optional[str] = "left"
        # Additional bands for a multi-band capture: [{"device_id":..,"role":..}].
        # Each must be a seen flexgrid of the same matrix dims as the primary.
        extra_sensors: Optional[list] = None

    @app.post("/api/recording")
    async def start_recording(body: StartRecordingBody):
        try:
            rec = state.start_recording(
                sensor_device_id=body.sensor_device_id,
                label_device_id=body.label_device_id,
                filename=body.filename,
                window_ms=body.window_ms,
                role=body.role or "left",
                extra_sensors=body.extra_sensors,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500,
                                detail=f"Failed to create capture file: {e}")
        return {
            "filename": rec.path.name,
            "sensor_device_id": rec.sensor_device_id,
            "label_device_id": rec.label_device_id,
            "sensors": dict(rec.sensors),
            "window_ms": int(rec.window_s * 1000),
            "shape": [rec.rows, rec.cols],
        }

    class MultibandBody(BaseModel):
        filename: Optional[str] = None
        window_ms: Optional[int] = None

    @app.post("/api/recording/multiband")
    async def start_multiband(body: MultibandBody):
        """Start a multi-band capture from the Sources-panel role tags: every
        flexgrid tagged left/right is a band, the labeler-tagged device is the
        label source (else auto-picked)."""
        try:
            rec = state.start_multiband_recording(
                filename=body.filename, window_ms=body.window_ms)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500,
                                detail=f"Failed to create capture file: {e}")
        return {
            "filename": rec.path.name,
            "sensors": dict(rec.sensors),
            "label_device_id": rec.label_device_id,
            "window_ms": int(rec.window_s * 1000),
            "shape": [rec.rows, rec.cols],
        }

    @app.post("/api/recording/bilateral")
    async def start_bilateral(body: MultibandBody):
        """Start a two-hand capture: the left band is matched to the left-hand
        Quest stream and the right band to the right-hand stream. Bands come from
        the Sources-panel left/right role tags; labelers are quest-left /
        quest-right (the VR client opened with ?arm=both)."""
        try:
            rec = state.start_bilateral_recording(
                filename=body.filename, window_ms=body.window_ms)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500,
                                detail=f"Failed to create capture file: {e}")
        return {
            "filename": rec.path.name,
            "sensors": dict(rec.sensors),
            "side_labelers": dict(rec.label_id_side),
            "window_ms": int(rec.window_s * 1000),
            "shape": [rec.rows, rec.cols],
        }

    @app.delete("/api/recording")
    async def stop_recording():
        result = state.stop_recording()
        if result is None:
            raise HTTPException(status_code=400, detail="Not currently recording")
        return result

    @app.get("/api/recording")
    async def recording_status():
        if state.recording is None:
            return {"recording": False}
        r = state.recording
        return {
            "recording": True,
            "filename": r.path.name,
            "sensor_device_id": r.sensor_device_id,
            "sensors": dict(r.sensors),
            "label_device_id": r.label_device_id,
            "window_ms": int(r.window_s * 1000),
            "rows": r.row_count,
            "duration_s": round(r.duration_s, 1),
            "matched": r.matched_count,
            "unpaired_sensor": r.unpaired_sensor_count,
            "sensor_frames_seen": r.sensor_frames_seen,
            "label_packets_seen": r.label_packets_seen,
            "match_rate": round(r.match_rate, 3),
        }

    # ----- REST: captures -----

    @app.get("/api/captures")
    async def list_captures():
        return state.list_captures()

    @app.get("/api/captures/{name}/download")
    async def download_capture(name: str):
        p = state.capture_path(name)
        if p is None:
            raise HTTPException(status_code=404, detail="Capture not found")
        return FileResponse(p, filename=p.name, media_type="text/csv")

    class RevealBody(BaseModel):
        # If empty/None -> just open captures_dir. Otherwise must be a
        # capture name whitelisted by state.capture_path().
        name: Optional[str] = None

    @app.post("/api/reveal")
    async def reveal_in_folder(body: RevealBody):
        """Open the captures folder (and optionally highlight a specific
        capture) in the OS file manager. Local-only convenience; the server
        is intended for localhost use."""
        if body.name:
            p = state.capture_path(body.name)
            if p is None:
                raise HTTPException(status_code=404, detail="Capture not found")
            target = p
            select = True
        else:
            target = state.captures_dir
            select = False
        try:
            _reveal_path_in_file_manager(target, select_file=select)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"opened": str(target), "selected": select}

    @app.delete("/api/captures/{name}")
    async def delete_capture(name: str):
        ok = state.delete_capture(name)
        if not ok:
            raise HTTPException(status_code=404, detail="Capture not found")
        return {"deleted": name}

    # ----- REST: capture metadata sidecars -----

    @app.get("/api/captures/{name}/meta")
    async def get_capture_meta(name: str):
        if state.capture_path(name) is None:
            raise HTTPException(status_code=404, detail="Capture not found")
        return state.read_capture_meta(name)

    @app.put("/api/captures/{name}/meta")
    async def put_capture_meta(name: str, body: dict):
        """Merge-update a capture's metadata sidecar.

        Body is a plain dict; recognized user fields (arm, subject, gesture,
        tags, notes) land at top level. Auto fields (sensor_device_id etc.)
        land under `.auto`. Anything else goes under `.extras` so the schema
        stays self-describing.
        """
        if state.capture_path(name) is None:
            raise HTTPException(status_code=404, detail="Capture not found")
        try:
            merged = state.write_capture_meta(name, body or {})
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Could not write meta: {e}")
        return merged

    # ----- REST: training + model registry -----

    class TrainBody(BaseModel):
        captures: list[str]                # filenames inside captures_dir
        model_type: str = "random_forest"
        n_estimators: int = 100
        test_split: float = 0.2
        activate: bool = True              # hot-swap the trained model into engine

    @app.post("/api/train")
    async def train_endpoint(body: TrainBody):
        if not body.captures:
            raise HTTPException(status_code=400, detail="No captures specified")
        try:
            # Training is CPU-bound (RandomForest fit). Run it in a worker
            # thread so the asyncio event loop / WS broadcast keeps flowing
            # to other clients while training runs.
            result = await asyncio.to_thread(
                state.train_from_captures,
                body.captures,
                body.model_type,
                body.n_estimators,
                body.test_split,
                body.activate,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Training failed: {e}")
        return result

    @app.get("/api/models")
    async def list_models():
        return state.list_models()

    class SetModelBody(BaseModel):
        path: str

    @app.post("/api/inference/model")
    async def set_model(body: SetModelBody):
        try:
            state.set_model(body.path)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Model file not found")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not load model: {e}")
        return {
            "active": state.engine is not None,
            "enabled": state.inference_enabled,
            "model": state.engine.name if state.engine else None,
        }

    class SetEnabledBody(BaseModel):
        enabled: bool

    @app.post("/api/inference/enabled")
    async def set_inference_enabled(body: SetEnabledBody):
        state.set_inference_enabled(body.enabled)
        return {
            "enabled": state.inference_enabled,
            "model": state.engine.name if state.engine else None,
        }

    class SetHandBody(BaseModel):
        # host=null or empty -> disable forwarding. Otherwise port defaults
        # to 3145 (the robot hand's UDP listen port).
        host: Optional[str] = None
        port: int = 3145

    @app.post("/api/inference/hand")
    async def set_hand(body: SetHandBody):
        try:
            state.set_hand_target(body.host, body.port)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "hand_target": (
                f"{state.hand_target[0]}:{state.hand_target[1]}"
                if state.hand_target else None
            ),
        }

    # ----- REST: logs -----

    @app.get("/api/logs")
    async def list_logs(since: int = 0, limit: int = 200):
        """Return recent log entries. Frontend polls with ?since=<last_id>
        so each call only ships new entries. `latest_id` lets the client
        bootstrap to the tail of the buffer on first load."""
        entries = state.log_buffer.entries(since_id=since, limit=limit)
        return {
            "latest_id": state.log_buffer.latest_id(),
            "entries": entries,
        }

    # ----- REST: sessions -----

    class StartSessionBody(BaseModel):
        name: Optional[str] = ""
        subject: Optional[str] = ""
        arm: Optional[str] = None       # "left" | "right" | None
        gestures: Optional[list] = None  # planned gesture set, free-form strings
        notes: Optional[str] = ""
        tags: Optional[list] = None

    @app.get("/api/sessions")
    async def list_sessions_endpoint():
        return state.list_sessions()

    @app.get("/api/sessions/active")
    async def get_active_session():
        return state.active_session  # may be null

    @app.post("/api/sessions")
    async def start_session_endpoint(body: StartSessionBody):
        try:
            s = state.start_session(
                name=body.name or "",
                subject=body.subject or "",
                arm=body.arm,
                gestures=body.gestures,
                notes=body.notes or "",
                tags=body.tags,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return s

    @app.post("/api/sessions/end")
    async def end_session_endpoint():
        s = state.end_session()
        if s is None:
            raise HTTPException(status_code=404, detail="No active session")
        return s

    @app.get("/api/sessions/{session_id}")
    async def get_session_endpoint(session_id: str):
        s = state.get_session(session_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return s

    @app.put("/api/sessions/{session_id}")
    async def update_session_endpoint(session_id: str, body: dict):
        s = state.update_session(session_id, body or {})
        if s is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return s

    @app.delete("/api/sessions/{session_id}")
    async def delete_session_endpoint(session_id: str, unlink_captures: bool = True):
        ok = state.delete_session(session_id, also_unlink_captures=unlink_captures)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"deleted": session_id}

    # ----- REST: retroactive session<->capture linking -----
    #
    # A capture made *outside* an active session can be added to one
    # afterwards, and vice versa removed. This is the "I forgot to start a
    # session before recording" recovery path. We update both:
    #   1. the session JSON's `captures` list (authoritative)
    #   2. the capture's meta sidecar (tag `session:<id>` + auto.session_id)
    # so the captures-panel filter, the past-sessions expansion, and any
    # future export all agree on which session a capture belongs to.

    class LinkCapturesBody(BaseModel):
        capture_names: list[str]   # bulk add

    @app.post("/api/sessions/{session_id}/captures")
    async def add_captures_to_session(session_id: str, body: LinkCapturesBody):
        s = state.get_session(session_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Session not found")

        tag = "session:" + session_id
        added, skipped = [], []
        for name in body.capture_names:
            if state.capture_path(name) is None:
                skipped.append({"name": name, "reason": "capture not found"})
                continue
            if name in s.get("captures", []):
                skipped.append({"name": name, "reason": "already in session"})
                continue
            try:
                state.link_capture_to_session(session_id, name)
                # Update the capture's meta so the tag-based filter + any
                # future export sees this capture as part of the session.
                meta = state.read_capture_meta(name) or {}
                tags = list(meta.get("tags") or [])
                if tag not in tags:
                    tags.append(tag)
                state.write_capture_meta(name, {
                    "tags": tags,
                    "auto": {"session_id": session_id},
                })
                added.append(name)
            except Exception as e:
                skipped.append({"name": name, "reason": str(e)})

        return {
            "added": added,
            "skipped": skipped,
            "session": state.get_session(session_id),
        }

    @app.delete("/api/sessions/{session_id}/captures/{capture_name}")
    async def remove_capture_from_session(session_id: str, capture_name: str):
        s = state.get_session(session_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if capture_name not in s.get("captures", []):
            raise HTTPException(status_code=404, detail="Capture not in session")
        try:
            state.unlink_capture_from_session(session_id, capture_name)
            # Strip the session tag + clear auto.session_id, but ONLY for this
            # session (leave any other 'session:xxx' tags alone -- though by
            # the data model a capture should only ever belong to one session).
            tag = "session:" + session_id
            meta = state.read_capture_meta(capture_name) or {}
            new_tags = [t for t in (meta.get("tags") or []) if t != tag]
            state.write_capture_meta(capture_name, {
                "tags": new_tags,
                "auto": {"session_id": None},
            })
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {
            "removed": capture_name,
            "session": state.get_session(session_id),
        }

    return app


def serve(host: str = "0.0.0.0", port: int = 8000, udp_port: int = 3141,
          captures_dir: Optional[str] = None,
          model_path: Optional[str] = None,
          hand_target: Optional[tuple] = None,
          ssl_certfile: Optional[str] = None,
          ssl_keyfile: Optional[str] = None,
          announce_port: int = 3140):
    """Run the web UI server (blocks).

    Pass ssl_certfile + ssl_keyfile to serve HTTPS -- required for the
    WebXR /vr page since Quest Browser refuses hand-tracking on plain
    HTTP. Generate certs locally with mkcert and install the root CA
    on the headset (see README).
    """
    import uvicorn
    app = create_app(
        udp_port=udp_port,
        captures_dir=captures_dir,
        model_path=model_path,
        hand_target=hand_target,
        announce_port=announce_port,
    )
    uvicorn.run(app, host=host, port=port, log_level="info",
                ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile)
