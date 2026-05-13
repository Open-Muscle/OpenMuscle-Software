"""FastAPI app for the OpenMuscle web UI.

Launch via the CLI: `openmuscle web --port 8000`
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openmuscle.web.state import AppState


STATIC_DIR = Path(__file__).parent / "static"


def create_app(udp_port: int = 3141, captures_dir: str | None = None) -> FastAPI:
    state = AppState(udp_port=udp_port, captures_dir=captures_dir)

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
        device_id: str
        filename: str | None = None

    @app.post("/api/recording")
    async def start_recording(body: StartRecordingBody):
        try:
            rec = state.start_recording(body.device_id, body.filename)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "filename": rec.path.name,
            "device_id": rec.device_id,
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
        return {
            "recording": True,
            "filename": state.recording.path.name,
            "device_id": state.recording.device_id,
            "rows": state.recording.row_count,
            "duration_s": round(state.recording.duration_s, 1),
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

    return app


def serve(host: str = "0.0.0.0", port: int = 8000, udp_port: int = 3141,
          captures_dir: str | None = None):
    """Run the web UI server (blocks)."""
    import uvicorn
    app = create_app(udp_port=udp_port, captures_dir=captures_dir)
    uvicorn.run(app, host=host, port=port, log_level="info")
