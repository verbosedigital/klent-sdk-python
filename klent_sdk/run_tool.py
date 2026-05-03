"""Generic helper that wraps one tool invocation with the full Klent decision loop."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypedDict

from klent_sdk.client import KlentClient


class AllowedResult(TypedDict):
    status: Any  # "allowed"
    output: Any
    matched_policy_id: str | None


class DeniedResult(TypedDict):
    status: Any  # "denied"
    reason: str
    matched_policy_id: str


class PendingResult(TypedDict):
    status: Any  # "pending"
    pending_action_id: str
    reason: str
    matched_policy_id: str | None


class ErrorResult(TypedDict):
    status: Any  # "error"
    error: BaseException


class ApprovalWait(TypedDict, total=False):
    """How long, and how, to block on a human approval."""

    timeout_seconds: float
    poll_seconds: float
    use_long_poll: bool


def run_tool(
    client: KlentClient,
    *,
    execution_id: str,
    tool: str,
    input: dict[str, Any],
    execute: Callable[[dict[str, Any]], Any],
    execute_steered: Callable[[str, dict[str, Any]], Any] | None = None,
    metadata: dict[str, Any] | None = None,
    approval_wait: ApprovalWait | None = None,
) -> dict[str, Any]:
    """Run one tool call through Klent.

    Performs, in order::

      action_requested → evaluate → branch on decision:
        allow / modify  → execute → action_executed | error
        steer           → run redirect_to.tool → action_executed | error
        approve         → return pending, or wait + run if approved
        deny            → action_blocked (server-side); SDK returns status=denied

    Returns one of::

      {"status": "allowed", "output": ..., "matched_policy_id": str | None}
      {"status": "denied",  "reason": str, "matched_policy_id": str}
      {"status": "pending", "pending_action_id": str, "reason": str, "matched_policy_id": str | None}
      {"status": "error",   "error": BaseException}

    Pass ``approval_wait`` to block on ``approve`` decisions until a human
    resolves them via the dashboard. Otherwise an ``approve`` returns
    immediately with ``status="pending"`` so the caller can poll/UI as it sees fit.

    Pass ``execute_steered`` if your ``execute`` is bound to a single tool —
    that callback receives ``(tool_name, input)`` for the substituted call. If
    omitted, ``execute`` is reused with the steered input.
    """
    metadata = metadata or {}
    client.log_event(
        {
            "execution_id": execution_id,
            "type": "action_requested",
            "payload": {"tool": tool, "input": input},
            "metadata": metadata,
        }
    )

    decision = client.evaluate_action(
        {
            "execution_id": execution_id,
            "tool": tool,
            "input": input,
            "metadata": metadata,
        }
    )

    if decision["decision"] == "deny":
        return {
            "status": "denied",
            "reason": decision.get("reason") or "Denied by policy",
            "matched_policy_id": decision.get("matched_policy_id") or "unknown",
        }

    if decision["decision"] == "steer":
        redirect = decision.get("redirect_to")
        if not redirect:
            return {
                "status": "error",
                "error": RuntimeError("Klent returned steer decision without redirect_to"),
            }
        invoke: Callable[[], Any] = (
            (lambda: execute_steered(redirect["tool"], redirect["input"]))  # type: ignore[misc]
            if execute_steered is not None
            else (lambda: execute(redirect["input"]))
        )
        return _run_execution(
            client,
            execution_id=execution_id,
            tool=redirect["tool"],
            invoke=invoke,
            metadata=metadata,
            matched_policy_id=decision.get("matched_policy_id"),
        )

    if decision["decision"] == "approve":
        pending_id = decision.get("pending_action_id")
        if not pending_id:
            return {
                "status": "error",
                "error": RuntimeError("Klent returned approve decision without pending_action_id"),
            }
        if approval_wait is None:
            return {
                "status": "pending",
                "pending_action_id": pending_id,
                "reason": decision.get("reason") or "Awaiting human approval",
                "matched_policy_id": decision.get("matched_policy_id"),
            }

        outcome = _wait_for_approval(client, pending_id, approval_wait)
        if outcome["status"] == "pending":
            return {
                "status": "pending",
                "pending_action_id": pending_id,
                "reason": "Approval wait timed out",
                "matched_policy_id": decision.get("matched_policy_id"),
            }
        if outcome["status"] != "approved":
            return {
                "status": "denied",
                "reason": outcome.get("note") or f"Approval {outcome['status']}",
                "matched_policy_id": decision.get("matched_policy_id") or "unknown",
            }
        staged = outcome.get("modifications") or []
        final_input = _apply_modifications(input, staged) if staged else input
        return _run_execution(
            client,
            execution_id=execution_id,
            tool=tool,
            invoke=lambda: execute(final_input),
            metadata=metadata,
            matched_policy_id=decision.get("matched_policy_id"),
        )

    # allow / modify
    effective_input = input
    if decision["decision"] == "modify" and decision.get("modifications"):
        effective_input = _apply_modifications(input, decision["modifications"])

    return _run_execution(
        client,
        execution_id=execution_id,
        tool=tool,
        invoke=lambda: execute(effective_input),
        metadata=metadata,
        matched_policy_id=decision.get("matched_policy_id"),
    )


def _run_execution(
    client: KlentClient,
    *,
    execution_id: str,
    tool: str,
    invoke: Callable[[], Any],
    metadata: dict[str, Any],
    matched_policy_id: str | None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        output = invoke()
    except BaseException as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        client.log_event(
            {
                "execution_id": execution_id,
                "type": "error",
                "payload": {"tool": tool, "message": str(exc)},
                "duration_ms": duration_ms,
                "metadata": metadata,
            }
        )
        return {"status": "error", "error": exc}

    duration_ms = int((time.monotonic() - started) * 1000)
    client.log_event(
        {
            "execution_id": execution_id,
            "type": "action_executed",
            "payload": {"tool": tool, "output": output},
            "duration_ms": duration_ms,
            "metadata": metadata,
        }
    )
    return {"status": "allowed", "output": output, "matched_policy_id": matched_policy_id}


def _wait_for_approval(
    client: KlentClient,
    pending_id: str,
    cfg: ApprovalWait,
) -> dict[str, Any]:
    """Block until the pending action leaves 'pending' or the budget elapses.

    Returns one of:
      {"status": "approved", "modifications": [...] | None}
      {"status": "rejected" | "expired", "note": str | None}
      {"status": "pending"}  # timed out
    """
    timeout = cfg.get("timeout_seconds", 60.0)
    poll_seconds = cfg.get("poll_seconds", 1.0)
    use_long_poll = cfg.get("use_long_poll", True)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining_seconds = deadline - time.monotonic()
        # Server caps wait_ms at 30s — split a longer budget into chunks.
        wait_ms = int(min(remaining_seconds, 30.0) * 1000) if use_long_poll else 0
        row = client.get_pending_action(pending_id, wait_ms=wait_ms)

        status = row["status"]
        if status == "approved":
            return {"status": "approved", "modifications": row.get("modifications")}
        if status in ("rejected", "expired"):
            return {"status": status, "note": row.get("resolution_note")}
        if not use_long_poll:
            remaining_after = deadline - time.monotonic()
            if remaining_after <= 0:
                break
            time.sleep(min(poll_seconds, remaining_after))
        # use_long_poll=True: server already waited; loop right back if budget left.
    return {"status": "pending"}


def _apply_modifications(
    base: dict[str, Any],
    modifications: list[dict[str, Any]],
) -> dict[str, Any]:
    import copy

    result = copy.deepcopy(base)
    for mod in modifications:
        _set_by_path(result, mod["field"], mod["value"])
    return result


def _set_by_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for key in parts[:-1]:
        existing = cursor.get(key)
        if not isinstance(existing, dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[parts[-1]] = value
