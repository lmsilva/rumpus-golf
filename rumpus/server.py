"""Local web server + websocket.

FastAPI serves the ``web/`` front-end and a single ``/ws`` endpoint. A game-loop
thread runs the engine (sensor grab -> vision -> rules) and publishes a JSON
state snapshot + JPEG feed frames. The browser is a dumb display: it receives
state, renders, and sends user-intent messages back over the websocket.

This is the hard seam from the requirements: the UI is replaceable and the
Python core stays portable — a tablet edition serves the same page to a TV.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .engine import GameEngine
from .paths import WEB_DIR
from .sensor import create_backend
from .vision.geometry import default_camera

# The browser decides which screens show the picture. Always encode when we
# have a color frame so Settings / sensor-check can show a live preview.


class Broadcaster:
    """Thread-safe latest-value store for state + frame."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state_json = "{}"
        self.frame: bytes | None = None
        self.seq = 0

    def publish(self, state: dict, frame: bytes | None) -> None:
        with self._lock:
            self.state_json = json.dumps(state)
            self.frame = frame
            self.seq += 1

    def latest(self) -> tuple[int, str, bytes | None]:
        with self._lock:
            return self.seq, self.state_json, self.frame


def make_app(force_sensor: str | None = None, allow_mock: bool = True,
             camera_index: int | None = None, camera_res: str | None = None,
             backend_mode: str | None = None) -> FastAPI:
    engine = GameEngine()
    broadcaster = Broadcaster()
    input_queue: queue.Queue = queue.Queue()
    stop = threading.Event()

    def init_sensor() -> None:
        cfg = engine.settings.get("camera", default={}) or {}
        idx = camera_index if camera_index is not None else int(cfg.get("device", 0))
        res = camera_res if camera_res is not None else str(cfg.get("resolution", "1280x720"))
        mode = backend_mode if backend_mode is not None else str(cfg.get("backend", "auto"))
        # An explicit webcam preference must not silently fall back to mock.
        use_mock = allow_mock and mode != "webcam" and force_sensor != "webcam"
        backend, _kind = create_backend(
            force=force_sensor, allow_mock=use_mock,
            camera_index=idx, camera_res=res, backend_mode=mode,
        )
        if backend is None:
            engine.sensor_status = "none"
            return
        cam = getattr(backend, "cam", None) or default_camera(
            backend.description.depth_res if backend.description.depth_res != (0, 0)
            else backend.description.color_res
        )
        engine.attach_backend(backend, cam)

    def loop() -> None:
        init_sensor()
        while not stop.is_set():
            frame = engine.grab_frame()
            if frame is None:
                time.sleep(0.1)
                continue
            interval = 1.0 / max(10.0, min(30.0, float(engine.backend.description.fps))) if engine.backend else 0.1

            def drain_input() -> None:
                last_move = None
                while True:
                    try:
                        msg = input_queue.get_nowait()
                    except queue.Empty:
                        break
                    if msg.get("t") == "pointer" and msg.get("type") == "move":
                        last_move = msg
                    else:
                        engine.handle_input(msg)
                if last_move is not None:
                    engine.handle_input(last_move)

            drain_input()
            engine.tick(frame)
            drain_input()
            jpeg = engine.encode_frame()
            broadcaster.publish(engine.snapshot(), jpeg)
            time.sleep(interval)

    thread = threading.Thread(target=loop, daemon=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        thread.start()
        yield
        stop.set()
        engine.shutdown()

    app = FastAPI(title="Rumpus Golf", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        last_seq = -1
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=0.01)
                input_queue.put(msg)
            except asyncio.TimeoutError:
                pass
            except (WebSocketDisconnect, Exception):
                break
            seq, state_json, frame = broadcaster.latest()
            if seq != last_seq:
                await websocket.send_text(state_json)
                if frame is not None:
                    await websocket.send_bytes(frame)
                last_seq = seq
            await asyncio.sleep(0.03)

    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


def run(host: str = "127.0.0.1", port: int = 8000, force_sensor: str | None = None,
        allow_mock: bool = True, camera_index: int | None = None,
        camera_res: str | None = None, backend_mode: str | None = None) -> None:
    import uvicorn
    uvicorn.run(make_app(force_sensor=force_sensor, allow_mock=allow_mock,
                         camera_index=camera_index, camera_res=camera_res,
                         backend_mode=backend_mode),
                host=host, port=port, log_level="info")
