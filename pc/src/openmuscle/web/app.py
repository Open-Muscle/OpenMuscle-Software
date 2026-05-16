"""FastAPI app for the OpenMuscle web UI.

Launch via the CLI: `openmuscle web --port 8000`
"""

# NOTE: deliberately NOT using `from __future__ import annotations` here.
# That makes all annotations into strings, which breaks FastAPI's body-vs-query
# inference for Pydantic model parameters (it reads "StartRecordingBody" as a
# string and falls back to treating the param as a query string field).

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openmuscle.web.state import AppState


STATIC_DIR = Path(__file__).parent / "static"


def create_app(udp_port: int = 3141, captures_dir: Optional[str] = None,
               model_path: Optional[str] = None,
               hand_target: Optional[tuple] = None) -> FastAPI:
    state = AppState(
        udp_port=udp_port,
        captures_dir=captures_dir,
        model_path=model_path,
        hand_target=hand_target,
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
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

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

    # ----- REST: devices -----

    @app.get("/api/devices")
    async def list_devices():
        return JSONResponse(state._snapshot()["devices"])

    # ----- REST: recording -----

    class StartRecordingBody(BaseModel):
        # All optional -- recorder auto-picks first flexgrid + first lask5 if
        # the operator doesn't specify. Pass label_device_id="" (empty string)
        # to deliberately record sensor-only without pairing.
        sensor_device_id: Optional[str] = None
        label_device_id: Optional[str] = None
        filename: Optional[str] = None
        window_ms: int = 100

    @app.post("/api/recording")
    async def start_recording(body: StartRecordingBody):
        try:
            rec = state.start_recording(
                sensor_device_id=body.sensor_device_id,
                label_device_id=body.label_device_id,
                filename=body.filename,
                window_ms=body.window_ms,
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

    return app


def serve(host: str = "0.0.0.0", port: int = 8000, udp_port: int = 3141,
          captures_dir: Optional[str] = None,
          model_path: Optional[str] = None,
          hand_target: Optional[tuple] = None):
    """Run the web UI server (blocks)."""
    import uvicorn
    app = create_app(
        udp_port=udp_port,
        captures_dir=captures_dir,
        model_path=model_path,
        hand_target=hand_target,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
