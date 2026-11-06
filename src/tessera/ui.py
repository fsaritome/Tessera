"""`tessera map`: local web page with the zoomable map.

Binds to 127.0.0.1 only and serves your own data to your own browser; nothing is sent
anywhere.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import sys
import time
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import Config
from .graph import build_graph, share_view

try:  # optional local extensions, not part of the published package
    from . import local_extensions as ext
except ImportError:
    ext = None  # type: ignore[assignment]

PAGE = Path(__file__).parent / "web" / "index.html"


class MapServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second server bind a port that is already taken,
    # silently. Without it, a second map fails clearly instead.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def make_handler(
    cfg: Config, client: Any = None, share: bool = False, token: str = ""
) -> type[BaseHTTPRequestHandler]:
    # Read once at start: a plugin update may delete this folder while the map is open.
    page = PAGE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                body = (None if share or not ext else ext.page(cfg)) or page
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
            elif path == "/api/graph":
                try:
                    g = build_graph(cfg)
                    if share:
                        g = share_view(g)
                    elif ext:
                        g.update(ext.graph_extras(cfg))
                    self._json(HTTPStatus.OK, g)
                except (RuntimeError, OSError) as e:
                    self._json(HTTPStatus.CONFLICT, {"error": str(e)})
            elif path == "/api/ping":  # lets `tessera stop` confirm it stops the right process
                self._json(HTTPStatus.OK, {"app": "tessera", "token": token, "share": share})
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self) -> None:
            if share or not (ext and ext.post(self, cfg, client)):
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    return Handler


def _pidfile(cfg: Config) -> Path:
    return cfg.state_dir / "map.json"


def serve(cfg: Config, port: int = 18765, open_browser: bool = True, share: bool = False) -> None:
    url = f"http://127.0.0.1:{port}/"
    token = secrets.token_hex(8)
    try:
        server = MapServer(("127.0.0.1", port), make_handler(cfg, share=share, token=token))
    except OSError:
        print(f"Port {port} is already in use: the map is probably already running at {url} "
              "(open or reload it there). `tessera stop` stops it.", file=sys.stderr)  # fmt: skip
        raise SystemExit(1) from None
    pidfile = _pidfile(cfg)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token}))
    mode = "  share mode: no work projects, only shareable activities" if share else ""
    print(f"Tessera map on {url}{mode}  (stop: tessera stop, or Ctrl+C)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    finally:
        server.server_close()
        _forget(pidfile, token)


def _forget(pidfile: Path, token: str) -> None:
    try:
        if json.loads(pidfile.read_text()).get("token") == token:
            pidfile.unlink()
    except (OSError, ValueError):
        pass


def stop(cfg: Config) -> str:
    """Stop the map this home started. Returns a one-line message for the user."""
    pidfile = _pidfile(cfg)
    try:
        info = json.loads(pidfile.read_text())
    except (OSError, ValueError):
        return "No Tessera map is running."
    url = f"http://127.0.0.1:{info['port']}/api/ping"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            alive = json.load(r).get("token") == info.get("token")
    except (OSError, ValueError):
        alive = False
    if not alive:  # stale file: the map already ended without cleaning up
        pidfile.unlink(missing_ok=True)
        return "No Tessera map is running."
    os.kill(int(info["pid"]), signal.SIGTERM)  # on Windows this ends the process
    for _ in range(30):
        time.sleep(0.1)
        try:
            urllib.request.urlopen(url, timeout=0.5)
        except OSError:
            break
    pidfile.unlink(missing_ok=True)
    return f"Stopped the Tessera map on port {info['port']}."
