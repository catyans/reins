"""Authenticated loopback control API; never a public agent execution endpoint."""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from reins.control.ledger import Ledger


class ControlServer(ThreadingHTTPServer):
    def __init__(self, ledger: Ledger, token: str, port=8795):
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("Use a credential of at least 32 characters")
        self.ledger, self.token = ledger, token
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def respond(self, code, payload, content_type="application/json"):
        content = (
            json.dumps(payload, allow_nan=False).encode()
            if content_type == "application/json"
            else payload
        )
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def trusted_origin(self):
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in allowed and (origin is None or origin == "http://" + host)

    def do_GET(self):
        if not self.trusted_origin():
            return self.respond(403, {"error": "Local origin required"})
        names = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/control.js": ("control.js", "application/javascript"),
            "/control.css": ("control.css", "text/css"),
        }
        if self.path not in names:
            return self.respond(404, {"error": "Not found"})
        name, mime = names[self.path]
        self.respond(200, (Path(__file__).parent / "static" / name).read_bytes(), mime)

    def do_POST(self):
        if not self.trusted_origin() or not hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + self.server.token
        ):
            return self.respond(403, {"error": "Local credential required"})
        actions = {
            "/v1/workflows": self.server.ledger.workflow,
            "/v1/tasks": self.server.ledger.branch,
            "/v1/reserve": self.server.ledger.reserve,
            "/v1/settle": self.server.ledger.settle,
            "/v1/uncertain": self.server.ledger.uncertain,
            "/v1/transition": self.server.ledger.transition,
            "/v1/progress": self.server.ledger.progress,
            "/v1/reconcile": self.server.ledger.reconcile,
            "/v1/context": lambda b: self.server.ledger.context(b["task_id"]),
            "/v1/status": lambda b: self.server.ledger.status(b.get("workflow_id")),
        }
        if self.path not in actions:
            return self.respond(404, {"error": "Not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= 1048576:
                raise ValueError("Body must be 1 byte to 1 MiB")
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("Expected object")
            result = actions[self.path](body)
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            return self.respond(400, {"error": str(exc)})
        except Exception:
            return self.respond(500, {"error": "Control operation failed; do not repeat paid work"})
        self.respond(200, result)
