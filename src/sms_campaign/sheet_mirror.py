"""Coalesce webhook-triggered Google Sheet mirrors."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class CoalescingMirror:
    """Run one mirror after a quiet period, merging bursts of webhook events."""

    def __init__(self, mirror: Callable[[], None], delay_seconds: float = 30.0) -> None:
        self.mirror = mirror
        self.delay_seconds = max(0.0, delay_seconds)
        self._lock = threading.Lock()
        self._pending = False
        self._worker: threading.Thread | None = None

    def request(self) -> None:
        """Schedule a mirror without blocking the webhook response."""
        with self._lock:
            self._pending = True
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run,
                name="google-sheet-mirror",
                daemon=True,
            )
            self._worker.start()

    def _run(self) -> None:
        time.sleep(self.delay_seconds)
        with self._lock:
            if not self._pending:
                self._worker = None
                return
            self._pending = False

        try:
            self.mirror()
        except Exception:
            # The database remains the source of truth and the next scheduled
            # mirror will retry. Do not turn a successful webhook into a 500.
            logger.exception("webhook-triggered Google Sheets mirror failed")

        with self._lock:
            if self._pending:
                # Events received while mirroring are handled by a fresh quiet
                # period, preventing a race from losing the newest state.
                self._worker = threading.Thread(
                    target=self._run,
                    name="google-sheet-mirror",
                    daemon=True,
                )
                self._worker.start()
            else:
                self._worker = None
