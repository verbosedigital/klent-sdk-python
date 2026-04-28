"""TypedDict definitions matching packages/schema/src."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

EventType = Literal[
    "decision",
    "action_requested",
    "action_executed",
    "action_blocked",
    "error",
]

PolicyEffect = Literal["allow", "deny", "modify"]


class CreateExecutionRequest(TypedDict, total=False):
    agent_id: str
    metadata: dict[str, Any]


class Execution(TypedDict):
    id: str
    project_id: str
    agent_id: str
    status: str
    started_at: str
    ended_at: str | None
    metadata: dict[str, Any]


class LogEventRequest(TypedDict, total=False):
    execution_id: str
    type: EventType
    payload: dict[str, Any]
    metadata: NotRequired[dict[str, Any]]
    occurred_at: NotRequired[str]


class Event(TypedDict):
    id: str
    project_id: str
    execution_id: str
    type: EventType
    payload: dict[str, Any]
    metadata: dict[str, Any]
    occurred_at: str
    received_at: str


class EvaluateActionRequest(TypedDict, total=False):
    execution_id: str
    tool: str
    input: dict[str, Any]
    metadata: NotRequired[dict[str, Any]]


class PolicyModification(TypedDict):
    field: str
    value: Any


class EvaluateActionResponse(TypedDict):
    decision: PolicyEffect
    matched_policy_id: str | None
    modifications: list[PolicyModification] | None
    reason: str | None
