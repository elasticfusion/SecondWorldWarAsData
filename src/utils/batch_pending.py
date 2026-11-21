"""Collect "queued for the batch" across the items of a loop (Phase 3 batch rounds).

Under ``--batch`` a Grok call that misses the cache is QUEUED and raises ``BatchModeCollecting``:
not an answer, not a failure. A loop over many items must not stop at the first one (that queues one
item per round) and must not turn it into a verdict (a stamp, a cached negative, a warning). It
visits every item, remembers that something was queued, and raises ONCE after the loop, so the step is
recorded ``pending`` (``phase3_enrich_data.run_enrichment_step``) and the next round has every answer::

    pending = Pending()
    for item in items:
        with pending.item():
            process(item)          # may raise BatchModeCollecting
    pending.raise_if_queued()      # after the loop's own summary/bookkeeping
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from src.grok_client import BatchModeCollecting


class Pending:
    def __init__(self) -> None:
        self.queued = 0
        self._first: Optional[BatchModeCollecting] = None
        self._lock = threading.Lock()  # a loop may run its items on worker threads

    @contextmanager
    def item(self) -> Iterator[None]:
        """Run one item: a queued request is remembered, anything else propagates unchanged."""
        try:
            yield
        except BatchModeCollecting as e:
            self.note(e)

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """``fn(*args)``, or None when it queued a request (remembered, raised later)."""
        try:
            return fn(*args, **kwargs)
        except BatchModeCollecting as e:
            self.note(e)
            return None

    def note(self, e: BatchModeCollecting) -> None:
        with self._lock:
            self.queued += 1
            if self._first is None:
                self._first = e

    def __bool__(self) -> bool:
        return self.queued > 0

    def raise_if_queued(self) -> None:
        if self._first is not None:
            raise self._first
