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

# States whose snapshot includes a live camera feed.
FEED_PUBLISH_STATES = {
    "VERIFY", "CAL_FLOOR", "CAL_AREA", "CAL_PLACE", "CAL_OBSTACLES",
    "CAL_CUP", "CAL_BALLS", "PLAY", "TURN_CHANGE", "HOLE_OUT", "OOB", "HOLE_START",
}


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


def make_app(force_sensor: str | None = None, allow_mock: bool = True) -> FastAPI:
    engine = GameEngine()
    broadcaster = Broadcaster()
    input_queue: queue.Queue = queue.Queue()
    stop = threading.Event()
    backend = None
    backend_kind = "none"

    def init_sensor() -> None:
        nonlocal backend, backend_kind
        backend, backend_kind = create_backend(force=force_sensor, allow_mock=allow_mock)
        if backend is None:
            return
        cam = getattr(backend, "cam", None) or default_camera(backend.description.depth_res)
        engine.attach_backend(backend, cam)

    def loop() -> None:
        init_sensor()
        if backend is None:
            engine.sensor_status = "none"
            broadcaster.publish(engine.snapshot(), None)
            while not stop.is_set():
                time.sleep(0.1)
            return
        interval = 1.0 / max(10.0, min(30.0, float(backend.description.fps)))
        while not stop.is_set():
            t0 = time.time()
            frame = backend.grab()
            engine.tick(frame)
            while True:  # drain user input (engine access stays single-threaded)
                try:
                    engine.handle_input(input_queue.get_nowait())
                except queue.Empty:
                    break
            if engine.state in FEED_PUBLISH_STATES:
                jpeg = engine.encode_frame()
            else:
                jpeg = None
            broadcaster.publish(engine.snapshot(), jpeg)
            dt = time.time() - t0
            if dt < interval:
                time.sleep(interval - dt)

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
        allow_mock: bool = True) -> None:
    import uvicorn
    uvicorn.run(make_app(force_sensor=force_sensor, allow_mock=allow_mock),
                host=host, port=port, log_level="info")
