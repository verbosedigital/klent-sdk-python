"""Tests for run_tool. Uses a fake KlentClient that records interactions."""

from __future__ import annotations

from typing import Any

from klent_sdk.run_tool import run_tool


def _decision(**overrides: Any) -> dict[str, Any]:
    """Build an EvaluateActionResponse stub with sane defaults for new fields."""
    base: dict[str, Any] = {
        "decision": "allow",
        "matched_policy_id": None,
        "modifications": None,
        "redirect_to": None,
        "pending_action_id": None,
        "reason": None,
    }
    base.update(overrides)
    return base


class FakeVelor:
    """Stands in for KlentClient. Records all calls, returns a scripted decision."""

    def __init__(
        self,
        decision: dict[str, Any],
        pending_responses: list[dict[str, Any]] | None = None,
    ):
        self.decision = decision
        self.events: list[dict[str, Any]] = []
        self.evaluations: list[dict[str, Any]] = []
        self.polls: list[dict[str, Any]] = []
        self._pending_responses = pending_responses or []
        self._poll_idx = 0

    def log_event(self, body: dict[str, Any]) -> None:
        self.events.append(body)

    def evaluate_action(self, body: dict[str, Any]) -> dict[str, Any]:
        self.evaluations.append(body)
        return self.decision

    def get_pending_action(self, pending_action_id: str, *, wait_ms: int = 0) -> dict[str, Any]:
        self.polls.append({"id": pending_action_id, "wait_ms": wait_ms})
        if self._poll_idx >= len(self._pending_responses):
            return self._pending_responses[-1]
        row = self._pending_responses[self._poll_idx]
        self._poll_idx += 1
        return row


def test_allow_executes_and_logs_action_executed():
    klent = FakeVelor(_decision())
    called_with = {}

    def execute(inp):
        called_with.update(inp)
        return "ok"

    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="send_email",
        input={"to": "a@b.com"},
        execute=execute,
    )

    assert result["status"] == "allowed"
    assert result["output"] == "ok"
    assert called_with == {"to": "a@b.com"}

    types = [e["type"] for e in klent.events]
    assert types == ["action_requested", "action_executed"]


def test_deny_short_circuits_and_does_not_execute():
    klent = FakeVelor(
        _decision(
            decision="deny",
            matched_policy_id="pol_1",
            reason="Matched high-risk policy",
        )
    )
    called = False

    def execute(_inp):
        nonlocal called
        called = True
        return "should not run"

    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="transfer_funds",
        input={"amount": 50000},
        execute=execute,
    )

    assert result["status"] == "denied"
    assert result["matched_policy_id"] == "pol_1"
    assert result["reason"] == "Matched high-risk policy"
    assert called is False

    types = [e["type"] for e in klent.events]
    assert types == ["action_requested"]


def test_modify_applies_modifications_before_execute():
    klent = FakeVelor(
        _decision(
            decision="modify",
            matched_policy_id="pol_m",
            modifications=[
                {"field": "cc", "value": "audit@example.com"},
                {"field": "headers.X-Audit", "value": "on"},
            ],
            reason="audit",
        )
    )

    received: dict[str, Any] = {}

    def execute(inp):
        received.update(inp)
        return "sent"

    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="send_email",
        input={"to": "a@b.com", "headers": {}},
        execute=execute,
    )

    assert result["status"] == "allowed"
    assert received["to"] == "a@b.com"
    assert received["cc"] == "audit@example.com"
    assert received["headers"] == {"X-Audit": "on"}


def test_modify_does_not_mutate_caller_input():
    klent = FakeVelor(
        _decision(
            decision="modify",
            matched_policy_id="pol_m",
            modifications=[{"field": "cc", "value": "audit@example.com"}],
        )
    )
    caller_input = {"to": "a@b.com"}

    run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="send_email",
        input=caller_input,
        execute=lambda _inp: "ok",
    )

    assert caller_input == {"to": "a@b.com"}


def test_error_is_captured_and_logged():
    klent = FakeVelor(_decision())

    def execute(_inp):
        raise RuntimeError("boom")

    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="flaky_tool",
        input={},
        execute=execute,
    )

    assert result["status"] == "error"
    assert isinstance(result["error"], RuntimeError)
    assert str(result["error"]) == "boom"

    types = [e["type"] for e in klent.events]
    assert types == ["action_requested", "error"]


def test_metadata_is_forwarded_to_every_event():
    klent = FakeVelor(_decision())

    run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="x",
        input={},
        execute=lambda _i: None,
        metadata={"tenant": "acme"},
    )

    for evt in klent.events:
        assert evt["metadata"] == {"tenant": "acme"}
    for evaluation in klent.evaluations:
        assert evaluation["metadata"] == {"tenant": "acme"}


# ───────────────────────── steer ─────────────────────────


def test_steer_runs_executeSteered_with_redirect_target():
    klent = FakeVelor(
        _decision(
            decision="steer",
            matched_policy_id="pol_steer",
            redirect_to={"tool": "send_via_audit", "input": {"to": "a@b.com", "audit": True}},
            reason="redirected",
        )
    )

    received: dict[str, Any] = {}

    def execute(_inp):
        return "should not run"

    def execute_steered(tool, inp):
        received["tool"] = tool
        received["input"] = inp
        return "sent_via_audit"

    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="send_email",
        input={"to": "a@b.com"},
        execute=execute,
        execute_steered=execute_steered,
    )

    assert result["status"] == "allowed"
    assert result["output"] == "sent_via_audit"
    assert received == {
        "tool": "send_via_audit",
        "input": {"to": "a@b.com", "audit": True},
    }


def test_steer_falls_back_to_execute_with_steered_input():
    klent = FakeVelor(
        _decision(
            decision="steer",
            matched_policy_id="pol_steer",
            redirect_to={"tool": "doesnt_matter", "input": {"x": 42}},
        )
    )

    received: dict[str, Any] = {}

    def execute(inp):
        received.update(inp)
        return "ok"

    run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="orig",
        input={"x": 1},
        execute=execute,
    )

    assert received == {"x": 42}


def test_steer_without_redirect_is_an_error():
    klent = FakeVelor(_decision(decision="steer", redirect_to=None))
    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="x",
        input={},
        execute=lambda _i: "ok",
    )
    assert result["status"] == "error"


# ───────────────────────── approve ─────────────────────────


def _pending_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "pact_1",
        "project_id": "proj_x",
        "execution_id": "exec_1",
        "event_id": None,
        "tool": "transfer_funds",
        "input": {"amount": 9000},
        "metadata": {},
        "status": "pending",
        "matched_policy_id": "pol_hitl",
        "reason": None,
        "modifications": None,
        "requested_at": "2026-01-01T00:00:00Z",
        "expires_at": None,
        "resolved_at": None,
        "resolved_by_user_id": None,
        "resolution_note": None,
    }
    base.update(overrides)
    return base


def test_approve_returns_pending_when_no_wait_is_provided():
    klent = FakeVelor(
        _decision(
            decision="approve",
            matched_policy_id="pol_hitl",
            pending_action_id="pact_1",
            reason="needs review",
        )
    )

    called = False

    def execute(_inp):
        nonlocal called
        called = True
        return "should not run"

    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="transfer_funds",
        input={"amount": 9000},
        execute=execute,
    )

    assert result["status"] == "pending"
    assert result["pending_action_id"] == "pact_1"
    assert result["reason"] == "needs review"
    assert called is False


def test_approve_with_wait_runs_tool_when_approved():
    klent = FakeVelor(
        _decision(
            decision="approve",
            matched_policy_id="pol_hitl",
            pending_action_id="pact_1",
        ),
        pending_responses=[_pending_row(status="approved")],
    )

    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="transfer_funds",
        input={"amount": 9000},
        execute=lambda _inp: "transferred",
        approval_wait={"timeout_seconds": 5.0, "use_long_poll": True},
    )

    assert result["status"] == "allowed"
    assert result["output"] == "transferred"


def test_approve_with_wait_applies_resolver_modifications():
    klent = FakeVelor(
        _decision(
            decision="approve",
            matched_policy_id="pol_hitl",
            pending_action_id="pact_1",
        ),
        pending_responses=[
            _pending_row(
                status="approved",
                modifications=[{"field": "amount", "value": 5000}],
                resolution_note="capped",
            )
        ],
    )
    received: dict[str, Any] = {}

    def execute(inp):
        received.update(inp)
        return "ok"

    run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="transfer_funds",
        input={"amount": 9000, "to": "acct_a"},
        execute=execute,
        approval_wait={"timeout_seconds": 5.0},
    )
    assert received == {"amount": 5000, "to": "acct_a"}


def test_approve_with_wait_returns_denied_when_rejected():
    klent = FakeVelor(
        _decision(
            decision="approve",
            matched_policy_id="pol_hitl",
            pending_action_id="pact_1",
        ),
        pending_responses=[_pending_row(status="rejected", resolution_note="too risky")],
    )

    result = run_tool(
        klent,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="transfer_funds",
        input={},
        execute=lambda _inp: "never runs",
        approval_wait={"timeout_seconds": 5.0},
    )
    assert result["status"] == "denied"
    assert result["reason"] == "too risky"
