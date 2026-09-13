"""Origin check, LAN token, and websocket size cap (stdlib + uvicorn)."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import uvicorn

from rumpus.engine import GameEngine, S
from rumpus.models import Player
from rumpus.server import make_app, origin_matches_host


def test_origin_host_and_port_must_match():
    assert origin_matches_host(None, "127.0.0.1:8000")
    assert origin_matches_host("http://127.0.0.1:8000", "127.0.0.1:8000")
    assert not origin_matches_host("http://127.0.0.1:9999", "127.0.0.1:8000")
    assert not origin_matches_host("http://evil.example", "127.0.0.1:8000")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    def __init__(self, access_token=None):
        self.port = _free_port()
        app = make_app(force_sensor="mock", access_token=access_token)
        self._uv = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning",
        ))
        self._thread = threading.Thread(target=self._uv.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            if self._uv.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def __exit__(self, *exc):
        self._uv.should_exit = True
        self._thread.join(timeout=4)


def _http_get(port: int, path: str) -> tuple[int, bytes]:
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
            return r.status, r.read()
    except HTTPError as e:
        return e.code, e.read()


def _ws_handshake(port: int, path: str = "/ws", origin: str | None = None,
                  host: str | None = None) -> tuple[int, socket.socket, bytes]:
    """Return (http_status, sock, rest). sock is None if the handshake failed."""
    host = host or f"127.0.0.1:{port}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if origin is not None:
        lines.append(f"Origin: {origin}")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.sendall(raw)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
    try:
        status = int(status_line.split(" ")[1])
    except (IndexError, ValueError):
        sock.close()
        return 0, None, b""
    if status != 101:
        sock.close()
        return status, None, rest
    expect = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    )
    if expect not in head:
        sock.close()
        return status, None, rest
    return status, sock, rest


def _ws_send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    mask = os.urandom(4)
    header.extend(mask)
    sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _ws_read_frames(sock: socket.socket, leftover: bytes = b"", limit: int = 8):
    buf = leftover
    frames = []
    sock.settimeout(2)
    for _ in range(limit):
        while len(buf) < 2:
            chunk = sock.recv(4096)
            if not chunk:
                return frames
            buf += chunk
        b0, b1 = buf[0], buf[1]
        opcode = b0 & 0x0F
        ln = b1 & 0x7F
        idx = 2
        if ln == 126:
            while len(buf) < idx + 2:
                buf += sock.recv(4096)
            ln = struct.unpack("!H", buf[idx:idx + 2])[0]
            idx += 2
        elif ln == 127:
            while len(buf) < idx + 8:
                buf += sock.recv(4096)
            ln = struct.unpack("!Q", buf[idx:idx + 8])[0]
            idx += 8
        while len(buf) < idx + ln:
            chunk = sock.recv(4096)
            if not chunk:
                return frames
            buf += chunk
        payload = buf[idx:idx + ln]
        buf = buf[idx + ln:]
        frames.append((opcode, payload))
        if opcode == 0x8:
            break
    return frames


def test_foreign_origin_cannot_open_ws():
    with _Server() as srv:
        status, sock, _ = _ws_handshake(
            srv.port, origin="http://127.0.0.1:9999", host=f"127.0.0.1:{srv.port}",
        )
        assert sock is None
        assert status in (403, 4403, 0) or status != 101


def test_same_origin_ws_connects():
    with _Server() as srv:
        status, sock, rest = _ws_handshake(
            srv.port, origin=f"http://127.0.0.1:{srv.port}",
            host=f"127.0.0.1:{srv.port}",
        )
        assert status == 101 and sock is not None
        frames = _ws_read_frames(sock, rest, limit=4)
        sock.close()
        texts = [p.decode("utf-8", "replace") for op, p in frames if op == 0x1]
        assert any(t.startswith("{") for t in texts)


def test_lan_token_required_on_index_and_ws():
    token = "FAKESECRET_c3d4e5f6g7h8i9j0k1l2"
    with _Server(access_token=token) as srv:
        assert _http_get(srv.port, "/")[0] == 403
        assert _http_get(srv.port, "/?t=wrong-token-xxxx")[0] == 403
        assert _http_get(srv.port, f"/?t={token}")[0] == 200
        status, sock, _ = _ws_handshake(srv.port, "/ws")
        assert sock is None
        status, sock, rest = _ws_handshake(srv.port, f"/ws?t={token}")
        assert status == 101 and sock is not None
        frames = _ws_read_frames(sock, rest, limit=4)
        sock.close()
        texts = [p.decode("utf-8", "replace") for op, p in frames if op == 0x1]
        assert any(t.startswith("{") for t in texts)


def test_huge_ws_message_closes_without_killing_loop():
    with _Server() as srv:
        status, sock, rest = _ws_handshake(srv.port)
        assert status == 101 and sock is not None
        _ws_read_frames(sock, rest, limit=2)
        _ws_send_text(sock, "x" * (1024 * 1024))
        frames = _ws_read_frames(sock, b"", limit=8)
        sock.close()
        closes = [p for op, p in frames if op == 0x8]
        assert closes, "server should close after a 1 MB text frame"
        code = struct.unpack("!H", closes[0][:2])[0] if len(closes[0]) >= 2 else 0
        assert code in (1009, 1000, 1001)
        status2, sock2, rest2 = _ws_handshake(srv.port)
        assert status2 == 101 and sock2 is not None
        frames2 = _ws_read_frames(sock2, rest2, limit=4)
        sock2.close()
        texts = [p.decode("utf-8", "replace") for op, p in frames2 if op == 0x1]
        assert any(t.startswith("{") for t in texts)


def test_text_inputs_trimmed_and_capped():
    e = GameEngine()
    e.state = S.CAL_BALLS
    e.players = [Player(id="p0", name="Old", color="#ff8a3d")]
    e.handle_input({
        "t": "text", "key": "player_name", "index": 0,
        "value": "  " + ("N" * 80) + "  ",
    })
    assert e.players[0].name == "N" * 40
    e.handle_input({"t": "text", "key": "setup_name", "value": "x" * 80})
    assert e.setup.name == "x" * 40


def test_malformed_set_does_not_raise():
    e = GameEngine()
    e.handle_input({"t": "set", "key": "holes", "value": "nope"})
    e.handle_input({"t": "set", "key": "settings.camera.exposure", "value": "zz"})
    e.handle_input({"t": "set", "key": "stepper", "delta": "x"})


if __name__ == "__main__":
    test_origin_host_and_port_must_match()
    test_foreign_origin_cannot_open_ws()
    test_same_origin_ws_connects()
    test_lan_token_required_on_index_and_ws()
    test_huge_ws_message_closes_without_killing_loop()
    test_text_inputs_trimmed_and_capped()
    test_malformed_set_does_not_raise()
    print("ok")
