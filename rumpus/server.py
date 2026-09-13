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
import logging
import queue
import secrets
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("rumpus.server")

WS_MAX_TEXT = 4096
INPUT_QUEUE_SIZE = 256
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _json_default(o):
    """NumPy scalars look like bool/int/float but json.dumps rejects them."""
    if hasattr(o, "item"):
        try:
            return o.item()
        except Exception:
            pass
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)

from .engine import GameEngine
from .paths import WEB_DIR
from .sensor import create_backend
from .vision.geometry import default_camera

# The browser decides which screens show the picture. Always encode when we
# have a color frame so Settings / sensor-check can show a live preview.


def is_loopback_host(host: str) -> bool:
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def origin_matches_host(origin: str | None, host: str | None) -> bool:
    """True when Origin is missing (non-browser) or its host:port equals Host."""
    if not origin:
        return True
    netloc = (urlparse(origin).netloc or "").lower()
    return bool(netloc) and netloc == (host or "").lower()


def token_matches(got: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    got = got or ""
    if len(got) != len(expected):
        return False
    return secrets.compare_digest(got, expected)


def _advertise_url(bind: str, port: int) -> str:
    host = bind
    if bind in ("0.0.0.0", "::", ""):
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            host = s.getsockname()[0]
            s.close()
        except OSError:
            host = bind or "0.0.0.0"
    return f"http://{host}:{port}"


def _put_drop_oldest(q: queue.Queue, item) -> None:
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


class Broadcaster:
    """Thread-safe latest-value store for state + frame."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state_json = ""
        self.frame: bytes | None = None
        self.seq = -1

    def publish(self, state: dict, frame: bytes | None) -> None:
        with self._lock:
            self.state_json = json.dumps(state, default=_json_default)
            self.frame = frame
            self.seq += 1

    def latest(self) -> tuple[int, str, bytes | None]:
        with self._lock:
            return self.seq, self.state_json, self.frame


def make_app(force_sensor: str | None = None, allow_mock: bool = True,
             camera_index: int | None = None, camera_res: str | None = None,
             backend_mode: str | None = None,
             access_token: str | None = None) -> FastAPI:
    engine = GameEngine()
    broadcaster = Broadcaster()
    input_queue: queue.Queue = queue.Queue(maxsize=INPUT_QUEUE_SIZE)
    stop = threading.Event()

    def open_sensor(report: dict | None = None):
        """Open the camera off the game loop so the boot menu stays interactive."""
        cfg = engine.settings.get("camera", default={}) or {}
        idx = camera_index if camera_index is not None else int(cfg.get("device", 0))
        res = camera_res if camera_res is not None else str(cfg.get("resolution", "1280x720"))
        mode = backend_mode if backend_mode is not None else str(cfg.get("backend", "auto"))
        # An explicit webcam preference must not silently fall back to mock.
        use_mock = allow_mock and mode != "webcam" and force_sensor != "webcam"
        backend, _kind = create_backend(
            force=force_sensor, allow_mock=use_mock,
            camera_index=idx, camera_res=res, backend_mode=mode,
            settings=engine.settings, report=report,
        )
        if backend is None:
            return None, None
        # MSMF/DirectShow handles are thread-affine. Drop the worker's handle
        # so the game loop can reopen on the thread that actually grabs.
        detach = getattr(backend, "detach_handle", None)
        if callable(detach):
            detach()
        cam = getattr(backend, "cam", None) or default_camera(
            backend.description.depth_res if backend.description.depth_res != (0, 0)
            else backend.description.color_res
        )
        return backend, cam

    def loop() -> None:
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

        def publish(frame=None) -> None:
            jpeg = None
            if frame is not None:
                try:
                    jpeg = engine.encode_frame()
                except Exception:
                    jpeg = None
            try:
                state = engine.snapshot()
            except Exception:
                import traceback
                traceback.print_exc()
                # Publish something rather than nothing: skipping the publish
                # leaves every browser frozen on the last good state forever,
                # with no way for the player to tell that anything is wrong.
                state = {
                    "screen": "S01", "state": "BOOT", "sensor_status": "error",
                    "ui": {}, "game": {}, "setup": {}, "overlay": {"shapes": []},
                    "settings": {},
                    "feed": {"enabled": False, "w": 0, "h": 0, "stale": True,
                             "error": "The game hit an internal error. "
                                      "Restart Rumpus Golf."},
                }
            broadcaster.publish(state, jpeg)

        opened = {"done": False, "backend": None, "cam": None, "error": None}
        engine.sensor_status = "opening"

        def init_worker() -> None:
            try:
                backend, cam = open_sensor(report=opened)
                opened["backend"] = backend
                opened["cam"] = cam
            except Exception as exc:
                opened["error"] = exc
            opened["done"] = True

        # Webcam negotiation can take 30–60 s. Keep publishing + draining
        # input on this thread so New game / Settings / etc. work immediately.
        threading.Thread(target=init_worker, daemon=True, name="rumpus-sensor-init").start()
        try:
            publish()
        except Exception:
            import traceback
            traceback.print_exc()

        attached = False
        while not stop.is_set():
            if opened["done"] and not attached:
                attached = True
                if opened["error"] is not None:
                    import traceback
                    traceback.print_exception(opened["error"])
                    engine.sensor_status = "none"
                    engine._camera_error = (
                        "Could not open the camera. Close Zoom / Teams / Iriun "
                        "if it has the device, then press Retry."
                    )
                elif opened["backend"] is None:
                    engine.sensor_status = "none"
                    # A named cause ("your Kinect has no driver library") is
                    # worth far more than the generic busy-device guess.
                    engine._camera_error = opened.get("reason") or (
                        "Could not open the camera. Close Zoom / Teams / Iriun "
                        "if it has the device, then press Retry."
                    )
                else:
                    try:
                        backend = opened["backend"]
                        reopen = getattr(backend, "reopen_on_this_thread", None)
                        if callable(reopen):
                            if not reopen(full=False) and not reopen(full=True):
                                raise RuntimeError("webcam reopen failed")
                        engine.attach_backend(backend, opened["cam"])
                        # Attaching clears the error, so a "your Kinect is
                        # plugged in but unusable" note has to be re-applied —
                        # otherwise the fallback to the webcam looks like the
                        # Kinect was never seen at all.
                        if opened.get("reason"):
                            engine._camera_error = str(opened["reason"])
                    except Exception:
                        import traceback
                        traceback.print_exc()
                        engine.sensor_status = "none"
                        engine.backend = None
                        engine.sensor_desc = None
                        engine._camera_error = (
                            "Could not open the camera. Close Zoom / Teams / Iriun "
                            "if it has the device, then press Retry."
                        )
            frame = engine.grab_frame()
            interval = 0.1
            if engine.backend is not None:
                try:
                    interval = 1.0 / max(
                        10.0, min(30.0, float(engine.backend.description.fps))
                    )
                except Exception:
                    interval = 0.1
            try:
                drain_input()
                if frame is not None:
                    engine.tick(frame)
                drain_input()
                publish(frame)
            except Exception:
                import traceback
                traceback.print_exc()
                time.sleep(0.2)
                continue
            time.sleep(interval if frame is not None else 0.1)

    thread = threading.Thread(target=loop, daemon=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        thread.start()
        yield
        stop.set()
        engine.shutdown()

    app = FastAPI(title="Rumpus Golf", lifespan=lifespan, docs_url=None, redoc_url=None)

    async def _index(request: Request):
        if not token_matches(request.query_params.get("t"), access_token):
            return PlainTextResponse("Missing or invalid access token.", status_code=403)
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/")
    async def index(request: Request):
        return await _index(request)

    @app.get("/index.html")
    async def index_html(request: Request):
        return await _index(request)

    @app.get("/snapshot.jpg")
    async def snapshot_jpg(request: Request):
        if not token_matches(request.query_params.get("t"), access_token):
            return PlainTextResponse("Missing or invalid access token.", status_code=403)
        data = engine.finish_jpeg
        if not data:
            return Response(status_code=404)
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host")
        if not origin_matches_host(origin, host):
            log.warning("rejected websocket origin %s (host %s)", origin, host)
            print(f"[ws] rejected origin {origin!r} (host {host!r})", flush=True)
            await websocket.close(code=4403)
            return
        if not token_matches(websocket.query_params.get("t"), access_token):
            log.warning("rejected websocket: missing or invalid access token")
            print("[ws] rejected websocket: missing or invalid access token", flush=True)
            await websocket.close(code=4403)
            return
        await websocket.accept()
        last_seq = -1
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive(), timeout=0.01)
                if raw.get("type") == "websocket.disconnect":
                    break
                text = raw.get("text")
                if text is not None:
                    if len(text.encode("utf-8")) > WS_MAX_TEXT:
                        await websocket.close(code=1009)
                        break
                    try:
                        msg = json.loads(text)
                    except (TypeError, ValueError):
                        msg = None
                    if isinstance(msg, dict):
                        _put_drop_oldest(input_queue, msg)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break
            except Exception:
                import traceback
                traceback.print_exc()
                break
            seq, state_json, frame = broadcaster.latest()
            if seq != last_seq and seq >= 0 and state_json:
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
    token = None
    if not is_loopback_host(host):
        token = secrets.token_urlsafe(16)
        url = _advertise_url(host, port)
        print(
            f"[security] Server is reachable by the whole network. Open {url}/?t={token}",
            flush=True,
        )
    uvicorn.run(make_app(force_sensor=force_sensor, allow_mock=allow_mock,
                         camera_index=camera_index, camera_res=camera_res,
                         backend_mode=backend_mode, access_token=token),
                host=host, port=port, log_level="info")
