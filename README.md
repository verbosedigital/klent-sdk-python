# klent-sdk

The Python SDK for [Klent](https://klent.dev) — the control + observability layer for AI agents in production. Wraps every tool call your agent makes through Klent's policy engine: `allow`, `deny`, `modify`, `steer` to a different tool, or pause for `approve` (human-in-the-loop, synchronous wait via dashboard or email).

[![PyPI](https://img.shields.io/pypi/v/klent-sdk)](https://pypi.org/project/klent-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/klent-sdk)](https://pypi.org/project/klent-sdk/)
[![License](https://img.shields.io/pypi/l/klent-sdk)](./LICENSE)

## Install

```bash
pip install klent-sdk
```

Or with Anthropic agent helpers:

```bash
pip install 'klent-sdk[anthropic]'
```

## Quick start

```python
import os
from klent_sdk import KlentClient, run_tool

klent = KlentClient(api_key=os.environ["KLENT_API_KEY"])
execution = klent.start_execution(agent_id="my-agent")

result = run_tool(
    klent,
    execution_id=execution["id"],
    tool="transfer_funds",
    input={"amount": 50_000, "currency": "USD"},
    execute=lambda inp: transfer_funds(**inp),
    approval={"wait": {"timeout_ms": 30 * 60_000}},
)

if result["status"] == "allowed":
    print("output:", result["output"])
elif result["status"] == "denied":
    print("blocked:", result["reason"])
```

The `approval.wait` kwarg makes `run_tool` block synchronously until a human resolves the action on the dashboard (or via the email Resend sends with Approve / Reject buttons), then runs the tool and returns its output.

Full docs at [klent.dev/docs](https://klent.dev/docs).

## Anthropic agent helper

A higher-level orchestrator runs an Anthropic agent loop with every tool call gated through Klent:

```python
from anthropic import Anthropic
from klent_sdk import KlentClient
from klent_sdk.anthropic import run_anthropic_agent

result = run_anthropic_agent(
    anthropic=Anthropic(),
    klent=KlentClient(api_key=...),
    agent_id="research-agent",
    system="You are a helpful research assistant...",
    tools=[...],
    user_message="Find the top 3 things...",
)
```

See `klent_sdk/anthropic.py` for the full surface.

## Develop

```bash
git clone https://github.com/verbosedigital/klent-sdk-python.git
cd klent-sdk-python
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Publishing

Tag `sdk-py-vX.Y.Z` → `.github/workflows/publish-sdk-python.yml` builds wheel + sdist and publishes to PyPI via OIDC trusted publisher. No tokens stored — auth is the GitHub Actions OIDC id-token verified against PyPI's trusted-publisher config for this repo.

## Project

- Product, docs, dashboard: [klent.dev](https://klent.dev)
- Issues for this SDK: this repo
- Issues for the API, dashboard, or product: <hello@klent.dev>
- TypeScript SDK, MCP server, starter policies, examples: [verbosedigital/klent-sdk-ts](https://github.com/verbosedigital/klent-sdk-ts)

## License

Apache-2.0. See [`LICENSE`](./LICENSE).
