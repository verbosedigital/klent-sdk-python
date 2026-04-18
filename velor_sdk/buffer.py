"""Event buffer with size/interval-based flushing."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

FlushFn = Callable[[list[dict[str, Any]]], None]


class EventBuffer:
    """Buffers events in memory and flushes on size or timer.

    Thread-safe. Re-enqueues the batch on flush failure so events are not dropped.
    Callers should call ``flush()`` explicitly on shutdown (the client does this
    via an ``atexit`` hook).
    """

    def __init__(
        self,
        flush_fn: FlushFn,
        max_batch_size: int = 50,
        flush_interval_seconds: float = 2.0,
    ) -> None:
        self._flush_fn = flush_fn
        self._max_batch_size = max_batch_size
        self._flush_interval = flush_interval_seconds

        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def enqueue(self, event: dict[str, Any]) -> None:
        should_flush = False
        with self._lock:
            self._buffer.append(event)
            if len(self._buffer) >= self._max_batch_size:
                should_flush = True
            else:
                self._schedule_timer_locked()

        if should_flush:
            self.flush()

    def flush(self) -> None:
        # Serialize flushes to avoid interleaving HTTP calls and double-draining.
        with self._flush_lock:
            self._cancel_timer()
            with self._lock:
                if not self._buffer:
                    return
                batch = self._buffer
                self._buffer = []

            try:
                self._flush_fn(batch)
            except Exception:
                with self._lock:
                    self._buffer = batch + self._buffer
                raise

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    def _schedule_timer_locked(self) -> None:
        if self._timer is not None:
            return
        timer = threading.Timer(self._flush_interval, self._on_timer)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cancel_timer(self) -> None:
        with self._lock:
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()

    def _on_timer(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self.flush()
        except Exception:
            pass
