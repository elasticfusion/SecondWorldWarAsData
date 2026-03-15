"""Heartbeat monitor for long-running pipeline operations."""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class Heartbeat:
    """Logs a warning if no progress is reported within a timeout period.

    Usage:
        hb = Heartbeat(timeout=300)
        hb.start()
        for item in items:
            process(item)
            hb.ping(f"Processed {item}")
        hb.stop()
    """

    def __init__(self, timeout: int = 300, label: str = "Pipeline"):
        self._timeout = timeout
        self._label = label
        self._last_ping = time.monotonic()
        self._last_msg = ""
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        """Start the heartbeat monitor thread."""
        self._last_ping = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the heartbeat monitor thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def ping(self, message: str = ""):
        """Report progress."""
        self._last_ping = time.monotonic()
        self._last_msg = message

    def _monitor(self):
        """Background thread that checks for stalls."""
        while not self._stop.wait(timeout=30):
            elapsed = time.monotonic() - self._last_ping
            if elapsed >= self._timeout:
                logger.warning(
                    "%s: no progress for %d minutes. Last: %s",
                    self._label,
                    int(elapsed / 60),
                    self._last_msg or "(none)",
                )
