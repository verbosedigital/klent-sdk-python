"""Anthropic tool-use orchestrator with Argus in the path.

Install with:  pip install "argus-sdk[anthropic]"
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from argus_sdk.client import ArgusClient
from argus_sdk.run_tool import run_tool

if TYPE_CHECKING:
    from anthropic import Anthropic


@dataclass
class ArgusTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


@dataclass
class RunAnthropicAgentResult:
    execution_id: str
    messages: list[dict[str, Any]]
    stop_reason: str | None
    final_text: str
    turns: int


def run_anthropic_agent(
    *,
    client: "Anthropic",
    argus: ArgusClient,
    agent_id: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[ArgusTool],
    max_turns: int = 8,
    max_tokens: int = 1024,
    system: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RunAnthropicAgentResult:
    """Run a full Anthropic tool-use loop with Argus evaluating every call.

    Starts an execution, ciclea ``messages.create`` and the tool-use round-trip
    until the model stops asking for tools (or ``max_turns`` is hit), and
    records every decision and outcome on the execution timeline.
    """
    execution = argus.start_execution(
        {
            "agent_id": agent_id,
            "metadata": {**(metadata or {}), "model": model, "tool_count": len(tools)},
        }
    )

    tool_defs = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    tool_index = {t.name: t for t in tools}
    working_messages = list(messages)

    stop_reason: str | None = None
    turns = 0
    final_text = ""

    for turn in range(1, max_turns + 1):
        turns = turn

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "tools": tool_defs,
            "messages": working_messages,
        }
        if system is not None:
            kwargs["system"] = system

        llm_started = time.monotonic()
        response = client.messages.create(**kwargs)
        llm_duration_ms = int((time.monotonic() - llm_started) * 1000)

        stop_reason = response.stop_reason
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )

        argus.log_event(
            {
                "execution_id": execution["id"],
                "type": "decision",
                "payload": {
                    "turn": turn,
                    "stop_reason": stop_reason,
                    "text": text or None,
                },
                "duration_ms": llm_duration_ms,
                "metadata": metadata or {},
            }
        )

        if response.stop_reason != "tool_use":
            final_text = text
            break

        working_messages.append({"role": "assistant", "content": response.content})

        tool_results: list[dict[str, Any]] = []

        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue

            tool = tool_index.get(block.name)
            if tool is None:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": True,
                        "content": f'Unknown tool "{block.name}"',
                    }
                )
                continue

            result = run_tool(
                argus,
                execution_id=execution["id"],
                tool=block.name,
                input=dict(block.input or {}),
                execute=tool.handler,
                metadata={**(metadata or {}), "tool_use_id": block.id},
            )

            if result["status"] == "denied":
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": True,
                        "content": f"Blocked by Argus policy: {result['reason']}",
                    }
                )
            elif result["status"] == "pending":
                # Surface pending state to the model so the agent can decide
                # what to do (retry later, narrate to the user, fall back).
                # Callers wanting synchronous waiting should call run_tool
                # directly with approval_wait set.
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": True,
                        "content": (
                            f"Awaiting human approval "
                            f"(pending_action_id={result['pending_action_id']}). "
                            f"{result['reason']}"
                        ),
                    }
                )
            elif result["status"] == "error":
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": True,
                        "content": str(result["error"]),
                    }
                )
            else:
                output = result["output"]
                content = output if isinstance(output, str) else json.dumps(output)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                    }
                )

        working_messages.append({"role": "user", "content": tool_results})

    argus.flush()

    return RunAnthropicAgentResult(
        execution_id=execution["id"],
        messages=working_messages,
        stop_reason=stop_reason,
        final_text=final_text,
        turns=turns,
    )
