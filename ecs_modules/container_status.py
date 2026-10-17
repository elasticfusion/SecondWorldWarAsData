"""In-container HTTP status endpoint — the authoritative 'is this container running an
ongoing task?' API that the nat_teardown Lambda polls before tearing down NAT.

Design (owner directive 2026-10-15): a REAL HTTP endpoint on each pipeline container.
`GET /status` returns progress-based busy — a container is BUSY when it made forward
progress (an S3 output upload / an explicit progress ping) within ``IDLE_SECONDS``;
otherwise IDLE. This distinguishes a genuinely-working task from one that is merely alive
(the alive-but-wedged hole that pure liveness/lease heartbeats cannot see).

Stdlib-only (http.server) so it adds no dependency and starts instantly. Served on the
task's private IP:port; the SG allows the status port only from the teardown Lambda's SG.

Response body (JSON):
    {"busy": bool, "phase": str, "task_id": str,
     "last_progress_ts": float, "idle_seconds": float, "idle_threshold": int}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

STATUS_PORT = int(os.environ.get("STATUS_PORT", "8077"))
# A container is BUSY if it progressed within this window. Must comfortably exceed the
# 120s BackgroundSync interval so a mid-cycle poll doesn't read a working task as idle.
IDLE_SECONDS = int(os.environ.get("STATUS_IDLE_SECONDS", "300"))


class ProgressState:
    """Thread-safe forward-progress clock shared with BackgroundSync. ``mark()`` is called
    on real progress (an output upload); ``busy`` is True within IDLE_SECONDS of the last
    mark. Starts BUSY (a just-started task is working until proven idle)."""

    def __init__(self, idle_seconds: int = IDLE_SECONDS):
        self._idle = idle_seconds
        self._lock = threading.Lock()
        self._last = time.time()
        self._phase = os.environ.get("PIPELINE_PHASE", "")

    def mark(self, phase: str | None = None) -> None:
        with self._lock:
            self._last = time.time()
            if phase:
                self._phase = phase

    def snapshot(self) -> dict:
        with self._lock:
            idle_for = time.time() - self._last
            return {
                "busy": idle_for < self._idle,
                "phase": self._phase,
                "task_id": os.environ.get("ECS_TASK_ID", ""),
                "last_progress_ts": self._last,
                "idle_seconds": round(idle_for, 1),
                "idle_threshold": self._idle,
            }


def _make_handler(state: ProgressState):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            if self.path.rstrip("/") not in ("/status", ""):
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(state.snapshot()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # silence per-request stderr spam
            return

    return _Handler


def _bind_ip() -> str:
    """Resolve this task's PRIVATE VPC IP to bind to — never 0.0.0.0 (don't listen on all
    interfaces). Prefers the ECS task-metadata private IP; falls back to the primary
    outbound-route source IP (the private-subnet ENI); last resort loopback (status API
    simply won't be reachable, which is fail-safe — the Lambda treats unreachable as busy).
    """
    import json as _json
    import socket
    import urllib.request

    meta = os.environ.get("ECS_CONTAINER_METADATA_URI_V4", "")
    if meta:
        try:
            with urllib.request.urlopen(meta + "/task", timeout=2) as r:  # nosec B310
                doc = _json.loads(r.read().decode("utf-8"))
            for c in doc.get("Containers", []):
                for net in c.get("Networks", []):
                    for ip in net.get("IPv4Addresses", []):
                        if ip:
                            return ip
        except Exception as e:  # noqa: BLE001
            logger.warning("status bind: ECS metadata IP lookup failed (%s)", e)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet sent; reveals the private ENI IP
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def start_status_server(state: ProgressState, port: int = STATUS_PORT):
    """Start the status HTTP server on a daemon thread, bound to the task's PRIVATE VPC IP
    (not all interfaces). Returns the server (or None on failure — the status API is
    best-effort and must never block the pipeline)."""
    bind_ip = _bind_ip()
    try:
        server = ThreadingHTTPServer((bind_ip, port), _make_handler(state))
    except Exception as e:  # noqa: BLE001 - never let the status server break the task
        logger.warning(
            "Status server failed to bind %s:%d (%s) — continuing", bind_ip, port, e
        )
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Container status API listening on %s:%d/status", bind_ip, port)
    return server
