"""TypedDict definitions matching packages/schema/src."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

EventType = Literal[
    "decision",
    "action_requested",
    "action_executed",
    "action_blocked",
    "action_steered",
    "pending_approval",
    "approval_vote",
    "approval_resolved",
    "error",
]

PolicyEffect = Literal["allow", "deny", "modify", "approve", "steer"]

PendingActionStatus = Literal["pending", "approved", "rejected", "expired"]


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
    duration_ms: NotRequired[int]
    model: NotRequired[str]
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    occurred_at: NotRequired[str]


class Event(TypedDict):
    id: str
    project_id: str
    execution_id: str
    type: EventType
    payload: dict[str, Any]
    metadata: dict[str, Any]
    duration_ms: int | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
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


class PolicyRedirect(TypedDict):
    """For `effect: 'steer'` policies — the substitute tool to run instead."""

    tool: str
    input: dict[str, Any]


class EvaluateActionResponse(TypedDict):
    decision: PolicyEffect
    matched_policy_id: str | None
    modifications: list[PolicyModification] | None
    redirect_to: PolicyRedirect | None
    pending_action_id: str | None
    reason: str | None


class PendingActionApproval(TypedDict):
    """One human's vote on a pending action.

    Multi-step approval policies (`required_approvals > 1`) accumulate these
    until quorum or a single rejection.
    """

    user_id: str
    decision: Literal["approve", "reject"]
    note: str | None
    created_at: str


class PendingAction(TypedDict):
    """An action parked waiting for human approval."""

    id: str
    project_id: str
    execution_id: str
    event_id: str | None
    tool: str
    input: dict[str, Any]
    metadata: dict[str, Any]
    status: PendingActionStatus
    matched_policy_id: str | None
    reason: str | None
    modifications: list[PolicyModification] | None
    required_approvals: int
    approvals: list[PendingActionApproval]
    requested_at: str
    expires_at: str | None
    resolved_at: str | None
    resolved_by_user_id: str | None
    resolution_note: str | None
