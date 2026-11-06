"""`tessera map`: local web page with the zoomable map.

Binds to 127.0.0.1 only and serves your own data to your own browser; nothing is sent
anywhere.
"""

from __future__ import annotations

import json
import sys
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


def make_handler(
    cfg: Config, client: Any = None, share: bool = False
) -> type[BaseHTTPRequestHandler]:
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
                body = (None if share or not ext else ext.page(cfg)) or PAGE.read_bytes()
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
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self) -> None:
            if share or not (ext and ext.post(self, cfg, client)):
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    return Handler


def serve(cfg: Config, port: int = 18765, open_browser: bool = True, share: bool = False) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, share=share))
    url = f"http://127.0.0.1:{port}/"
    mode = "  share mode: no work projects, only shareable activities" if share else ""
    print(f"Tessera map on {url}{mode}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    finally:
        server.server_close()
