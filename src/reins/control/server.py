"""Authenticated loopback control API; never a public agent execution endpoint."""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from reins.control.ledger import Ledger


class ControlServer(ThreadingHTTPServer):
    def __init__(self, ledger: Ledger, token: str, port=8795, *, worker_token=None):
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("Use a credential of at least 32 characters")
        if worker_token is not None and (len(worker_token) < 32 or worker_token == token):
            raise ValueError("Worker credential must be distinct and at least 32 characters")
        self.worker_token = worker_token
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
        auth = self.headers.get("Authorization", "")
        operator = hmac.compare_digest(auth, "Bearer " + self.server.token)
        worker = bool(self.server.worker_token) and hmac.compare_digest(
            auth, "Bearer " + (self.server.worker_token or "")
        )
        if not self.trusted_origin() or not (operator or worker):
            return self.respond(403, {"error": "Local credential required"})
        privileged = {"/v1/pools", "/v1/tools", "/v1/approvals", "/v1/reconcile", "/v1/economics"}
        if not operator and self.path in privileged:
            return self.respond(403, {"error": "Operator credential required"})
        actions = {
            "/v1/regression/case": self.server.ledger.regression_case,
            "/v1/pools": self.server.ledger.pool,
            "/v1/tools": self.server.ledger.register_tool,
            "/v1/approvals": self.server.ledger.approval,
            "/v1/state/write": self.server.ledger.state_write,
            "/v1/state/read": self.server.ledger.state_read,
            "/v1/handoff": self.server.ledger.handoff,
            "/v1/economics": self.server.ledger.economic_entry,
            "/v1/context/sample": self.server.ledger.context_sample,
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
            if not operator:
                if self.path == "/v1/workflows":
                    # Operators provision immutable workflow policy before dispatching workers.
                    wid = body.get("workflow_id")
                    with self.server.ledger.lock:
                        exists = self.server.ledger.db.execute(
                            "SELECT 1 FROM workflows WHERE id=?", (wid,)
                        ).fetchone()
                    if not exists:
                        return self.respond(403, {"error": "Operator must provision workflow"})
                if self.path == "/v1/transition" and body.get("action") not in ("finish", "wrapup"):
                    return self.respond(403, {"error": "Operator required to pause or resume"})
            result = actions[self.path](body)
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            return self.respond(400, {"error": str(exc)})
        except Exception:
            return self.respond(500, {"error": "Control operation failed; do not repeat paid work"})
        self.respond(200, result)
