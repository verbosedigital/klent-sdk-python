"""Tests for the Anthropic orchestrator. Mocks both KlentClient and the
Anthropic client so we can drive the loop deterministically and verify the
events that get logged — in particular the per-turn `decision` event.

The Anthropic SDK isn't even imported here; we just feed in objects that
duck-type the response shape (`stop_reason`, `content`, optional `usage`).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from klent_sdk.anthropic import KlentTool, run_anthropic_agent


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


def _response(text: str, *, usage: FakeUsage | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[FakeTextBlock(text=text)],
        usage=usage,
    )


class FakeKlent:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.flushed = False

    def start_execution(self, _body: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": "exec_test",
            "project_id": "proj_test",
            "agent_id": "test",
            "status": "running",
            "started_at": "2026-05-04T00:00:00Z",
            "ended_at": None,
            "metadata": {},
        }

    def log_event(self, body: dict[str, Any]) -> None:
        self.events.append(body)

    def flush(self) -> None:
        self.flushed = True


class FakeAnthropic:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = responses
        self._idx = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **_kwargs: Any) -> SimpleNamespace:
        if self._idx >= len(self._responses):
            raise AssertionError("ran out of scripted responses")
        r = self._responses[self._idx]
        self._idx += 1
        return r


def test_records_token_usage_and_model_on_decision_event():
    klent = FakeKlent()
    anthropic = FakeAnthropic(
        [_response("ok", usage=FakeUsage(input_tokens=123, output_tokens=45))]
    )

    run_anthropic_agent(
        client=anthropic,  # type: ignore[arg-type]
        klent=klent,  # type: ignore[arg-type]
        agent_id="test",
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )

    decisions = [e for e in klent.events if e["type"] == "decision"]
    assert len(decisions) == 1
    assert decisions[0]["model"] == "claude-sonnet-4-6"
    assert decisions[0]["input_tokens"] == 123
    assert decisions[0]["output_tokens"] == 45


def test_omits_token_fields_when_response_has_no_usage():
    klent = FakeKlent()
    anthropic = FakeAnthropic([_response("ok", usage=None)])

    run_anthropic_agent(
        client=anthropic,  # type: ignore[arg-type]
        klent=klent,  # type: ignore[arg-type]
        agent_id="test",
        model="claude-test",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )

    decisions = [e for e in klent.events if e["type"] == "decision"]
    assert len(decisions) == 1
    assert decisions[0]["model"] == "claude-test"
    assert "input_tokens" not in decisions[0]
    assert "output_tokens" not in decisions[0]


def test_handles_partial_usage_fields():
    klent = FakeKlent()
    # Some providers return usage but with one of the counts missing.
    partial_usage = SimpleNamespace(input_tokens=100, output_tokens=None)
    anthropic = FakeAnthropic([_response("ok", usage=partial_usage)])  # type: ignore[arg-type]

    run_anthropic_agent(
        client=anthropic,  # type: ignore[arg-type]
        klent=klent,  # type: ignore[arg-type]
        agent_id="test",
        model="claude-test",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )

    decision = [e for e in klent.events if e["type"] == "decision"][0]
    assert decision["input_tokens"] == 100
    assert "output_tokens" not in decision


def test_does_not_break_when_no_tools_configured():
    """Smoke test: orchestrator must complete cleanly with empty tools and a
    one-shot end_turn response. Nothing about cost tracking, just regression."""
    klent = FakeKlent()
    anthropic = FakeAnthropic([_response("hello", usage=FakeUsage(10, 5))])

    result = run_anthropic_agent(
        client=anthropic,  # type: ignore[arg-type]
        klent=klent,  # type: ignore[arg-type]
        agent_id="test",
        model="claude-test",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )

    assert result.final_text == "hello"
    assert result.turns == 1
    assert klent.flushed is True


# Type-only reference so the import isn't unused-flagged in lint configurations.
_ = KlentTool
