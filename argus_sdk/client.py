"""Argus Python SDK client."""

from __future__ import annotations

import atexit
import random
import time
from typing import Any, cast

import httpx

from argus_sdk.buffer import EventBuffer
from argus_sdk.types import (
    CreateExecutionRequest,
    EvaluateActionRequest,
    EvaluateActionResponse,
    Execution,
    LogEventRequest,
    PendingAction,
)

DEFAULT_BASE_URL = "https://api.argus.dev/v1"


class ArgusClient:
    """Synchronous Argus client.

    The ``evaluate_action`` and ``start_execution`` methods are blocking.
    ``log_event`` is non-blocking — events are buffered and flushed in the
    background by size or interval, and flushed once more on process exit.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        max_batch_size: int = 50,
        flush_interval_seconds: float = 2.0,
        max_retries: int = 3,
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("ArgusClient: api_key is required")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._owned_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout_seconds)

        self._buffer = EventBuffer(
            flush_fn=self._send_event_batch,
            max_batch_size=max_batch_size,
            flush_interval_seconds=flush_interval_seconds,
        )
        atexit.register(self._on_exit)

    def start_execution(self, body: CreateExecutionRequest) -> Execution:
        return cast(Execution, self._request("POST", "/executions", body))

    def log_event(self, body: LogEventRequest) -> None:
        self._buffer.enqueue(dict(body))

    def evaluate_action(self, body: EvaluateActionRequest) -> EvaluateActionResponse:
        return cast(EvaluateActionResponse, self._request("POST", "/actions/evaluate", body))

    def get_pending_action(self, pending_action_id: str, *, wait_ms: int = 0) -> PendingAction:
        """Read a pending action.

        ``wait_ms`` enables server-side long-polling: the call holds the HTTP
        connection until the row is resolved or the budget elapses (max 30s
        per call). Pass 0 (default) for a single-shot read; the retry/backoff
        loop is bypassed when ``wait_ms > 0`` so the wait is the budget.
        """
        if wait_ms < 0:
            raise ValueError("wait_ms must be >= 0")
        if wait_ms == 0:
            return cast(PendingAction, self._request("GET", f"/pending_actions/{pending_action_id}"))
        return cast(
            PendingAction,
            self._request_no_retry("GET", f"/pending_actions/{pending_action_id}?wait_ms={wait_ms}"),
        )

    def _request_no_retry(self, method: str, path: str, body: object | None = None) -> Any:
        """Single-attempt request, no retry/backoff. Used for long-poll reads."""
        url = f"{self._base_url}{path}"
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        response = self._http.request(method, url, json=body, headers=headers)
        if 200 <= response.status_code < 300:
            if response.status_code in (202, 204) or not response.content:
                return None
            return response.json()
        raise _HttpError(method, path, response.status_code, response.text)

    def flush(self) -> None:
        self._buffer.flush()

    def close(self) -> None:
        try:
            self.flush()
        finally:
            if self._owned_client:
                self._http.close()

    def __enter__(self) -> ArgusClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _send_event_batch(self, batch: list[dict[str, Any]]) -> None:
        for event in batch:
            self._request("POST", "/events", event)

    def _request(
        self,
        method: str,
        path: str,
        body: object | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

        attempt = 0
        last_exc: Exception | None = None

        while attempt <= self._max_retries:
            try:
                response = self._http.request(method, url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                attempt += 1
                if attempt > self._max_retries:
                    break
                time.sleep(_backoff_seconds(attempt))
                continue

            if 200 <= response.status_code < 300:
                if response.status_code in (202, 204) or not response.content:
                    return None
                return response.json()

            if response.status_code >= 500 or response.status_code == 429:
                last_exc = _HttpError(method, path, response.status_code, response.text)
                attempt += 1
                if attempt > self._max_retries:
                    break
                time.sleep(_backoff_seconds(attempt))
                continue

            raise _HttpError(method, path, response.status_code, response.text)

        assert last_exc is not None
        raise last_exc

    def _on_exit(self) -> None:
        try:
            self.flush()
        except Exception:
            pass


class _HttpError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"Argus {method} {path} failed: {status} {body}")
        self.status = status


def _backoff_seconds(attempt: int) -> float:
    base = min(1.0 * (2 ** (attempt - 1)), 8.0)
    return base + random.uniform(0, 0.25)
