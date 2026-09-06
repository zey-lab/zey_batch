#!/usr/bin/env python3
"""Run the private Vagaro webhook receiver for Cloudflare Tunnel."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sms_campaign.webhook import WebhookError, WebhookProcessor

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("ZEY_DATABASE_PATH", str(ROOT / "data" / "customer_master.sqlite3")))
HOST = os.getenv("VAGARO_WEBHOOK_HOST", "127.0.0.1")
PORT = int(os.getenv("VAGARO_WEBHOOK_PORT", "8787"))
WEBHOOK_PATH = os.getenv("VAGARO_WEBHOOK_PATH", "/vagaro/webhook")
MAX_BODY_BYTES = 1_048_576


class Handler(BaseHTTPRequestHandler):
    processor = WebhookProcessor(DB_PATH, os.getenv("VAGARO_WEBHOOK_TOKEN"))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != WEBHOOK_PATH:
            self._send(404, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            self._send(413, {"error": "request body too large or missing length"})
            return

        try:
            result = self.processor.process(self.headers, self.rfile.read(content_length))
        except WebhookError as exc:
            self._send(401 if "token" in str(exc).lower() else 400, {"error": str(exc)})
        except Exception:
            self._send(500, {"error": "event processing failed"})
        else:
            self._send(200, result)

    def log_message(self, format: str, *args: object) -> None:
        # Do not log request bodies or authorization headers.
        super().log_message(format, *args)

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    if not os.getenv("VAGARO_WEBHOOK_TOKEN"):
        raise SystemExit("Set VAGARO_WEBHOOK_TOKEN before starting the receiver")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Vagaro webhook listening on http://{HOST}:{PORT}{WEBHOOK_PATH}")
    server.serve_forever()
