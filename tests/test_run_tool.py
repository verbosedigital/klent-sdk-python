"""Tests for run_tool. Uses a fake VelorClient that records interactions."""

from __future__ import annotations

from typing import Any

from velor_sdk.run_tool import run_tool


class FakeVelor:
    """Stands in for VelorClient. Records all calls, returns a scripted decision."""

    def __init__(self, decision: dict[str, Any]):
        self.decision = decision
        self.events: list[dict[str, Any]] = []
        self.evaluations: list[dict[str, Any]] = []

    def log_event(self, body: dict[str, Any]) -> None:
        self.events.append(body)

    def evaluate_action(self, body: dict[str, Any]) -> dict[str, Any]:
        self.evaluations.append(body)
        return self.decision


def test_allow_executes_and_logs_action_executed():
    velor = FakeVelor(
        {
            "decision": "allow",
            "matched_policy_id": None,
            "modifications": None,
            "reason": None,
        }
    )
    called_with = {}

    def execute(inp):
        called_with.update(inp)
        return "ok"

    result = run_tool(
        velor,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="send_email",
        input={"to": "a@b.com"},
        execute=execute,
    )

    assert result["status"] == "allowed"
    assert result["output"] == "ok"
    assert called_with == {"to": "a@b.com"}

    types = [e["type"] for e in velor.events]
    assert types == ["action_requested", "action_executed"]


def test_deny_short_circuits_and_does_not_execute():
    velor = FakeVelor(
        {
            "decision": "deny",
            "matched_policy_id": "pol_1",
            "modifications": None,
            "reason": "Matched high-risk policy",
        }
    )
    called = False

    def execute(_inp):
        nonlocal called
        called = True
        return "should not run"

    result = run_tool(
        velor,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="transfer_funds",
        input={"amount": 50000},
        execute=execute,
    )

    assert result["status"] == "denied"
    assert result["matched_policy_id"] == "pol_1"
    assert result["reason"] == "Matched high-risk policy"
    assert called is False

    # Only action_requested was logged by run_tool.
    # The action_blocked event is written server-side by evaluate_action.
    types = [e["type"] for e in velor.events]
    assert types == ["action_requested"]


def test_modify_applies_modifications_before_execute():
    velor = FakeVelor(
        {
            "decision": "modify",
            "matched_policy_id": "pol_m",
            "modifications": [
                {"field": "cc", "value": "audit@example.com"},
                {"field": "headers.X-Audit", "value": "on"},
            ],
            "reason": "audit",
        }
    )

    received: dict[str, Any] = {}

    def execute(inp):
        received.update(inp)
        return "sent"

    result = run_tool(
        velor,  # type: ignore[arg-type]
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
    velor = FakeVelor(
        {
            "decision": "modify",
            "matched_policy_id": "pol_m",
            "modifications": [{"field": "cc", "value": "audit@example.com"}],
            "reason": None,
        }
    )
    caller_input = {"to": "a@b.com"}

    run_tool(
        velor,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="send_email",
        input=caller_input,
        execute=lambda _inp: "ok",
    )

    # run_tool must not mutate the dict passed by the caller.
    assert caller_input == {"to": "a@b.com"}


def test_error_is_captured_and_logged():
    velor = FakeVelor(
        {"decision": "allow", "matched_policy_id": None, "modifications": None, "reason": None}
    )

    def execute(_inp):
        raise RuntimeError("boom")

    result = run_tool(
        velor,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="flaky_tool",
        input={},
        execute=execute,
    )

    assert result["status"] == "error"
    assert isinstance(result["error"], RuntimeError)
    assert str(result["error"]) == "boom"

    types = [e["type"] for e in velor.events]
    assert types == ["action_requested", "error"]


def test_metadata_is_forwarded_to_every_event():
    velor = FakeVelor(
        {"decision": "allow", "matched_policy_id": None, "modifications": None, "reason": None}
    )

    run_tool(
        velor,  # type: ignore[arg-type]
        execution_id="exec_1",
        tool="x",
        input={},
        execute=lambda _i: None,
        metadata={"tenant": "acme"},
    )

    for evt in velor.events:
        assert evt["metadata"] == {"tenant": "acme"}
    for evaluation in velor.evaluations:
        assert evaluation["metadata"] == {"tenant": "acme"}
