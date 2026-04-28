"""Generic helper that wraps one tool invocation with the full Argus decision loop."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypedDict

from argus_sdk.client import ArgusClient


class AllowedResult(TypedDict):
    status: Any  # "allowed"
    output: Any
    matched_policy_id: str | None


class DeniedResult(TypedDict):
    status: Any  # "denied"
    reason: str
    matched_policy_id: str


class ErrorResult(TypedDict):
    status: Any  # "error"
    error: BaseException


def run_tool(
    client: ArgusClient,
    *,
    execution_id: str,
    tool: str,
    input: dict[str, Any],
    execute: Callable[[dict[str, Any]], Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one tool call through Argus.

    Performs, in order:
      action_requested → evaluate → (modify | allow | deny) → action_executed | action_blocked | error

    Returns one of:
      {"status": "allowed", "output": <value>, "matched_policy_id": str | None}
      {"status": "denied", "reason": str, "matched_policy_id": str}
      {"status": "error", "error": BaseException}

    Framework-agnostic — pass whatever tool function you already have as ``execute``.
    """
    client.log_event(
        {
            "execution_id": execution_id,
            "type": "action_requested",
            "payload": {"tool": tool, "input": input},
            "metadata": metadata or {},
        }
    )

    decision = client.evaluate_action(
        {
            "execution_id": execution_id,
            "tool": tool,
            "input": input,
            "metadata": metadata or {},
        }
    )

    if decision["decision"] == "deny":
        return {
            "status": "denied",
            "reason": decision.get("reason") or "Denied by policy",
            "matched_policy_id": decision.get("matched_policy_id") or "unknown",
        }

    effective_input = input
    if decision["decision"] == "modify" and decision.get("modifications"):
        effective_input = _apply_modifications(input, decision["modifications"])

    started = time.monotonic()
    try:
        output = execute(effective_input)
    except BaseException as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        client.log_event(
            {
                "execution_id": execution_id,
                "type": "error",
                "payload": {"tool": tool, "message": str(exc)},
                "duration_ms": duration_ms,
                "metadata": metadata or {},
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
            "metadata": metadata or {},
        }
    )
    return {
        "status": "allowed",
        "output": output,
        "matched_policy_id": decision.get("matched_policy_id"),
    }


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
