import httpx
import pytest
import respx

from argus_sdk import ArgusClient


BASE_URL = "http://api.test.local/v1"


def make_client(**kwargs):
    return ArgusClient(
        api_key="ak_test_abc",
        base_url=BASE_URL,
        max_retries=0,
        flush_interval_seconds=0.05,
        **kwargs,
    )


@respx.mock
def test_start_execution_sends_bearer_and_parses_response():
    route = respx.post(f"{BASE_URL}/executions").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "exec_1",
                "project_id": "proj_1",
                "agent_id": "a",
                "status": "running",
                "started_at": "2025-01-01T00:00:00Z",
                "ended_at": None,
                "metadata": {},
            },
        ),
    )

    with make_client() as client:
        execution = client.start_execution({"agent_id": "a"})

    assert execution["id"] == "exec_1"
    assert route.call_count == 1
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer ak_test_abc"


@respx.mock
def test_evaluate_action_returns_decision():
    respx.post(f"{BASE_URL}/actions/evaluate").mock(
        return_value=httpx.Response(
            200,
            json={
                "decision": "deny",
                "matched_policy_id": "pol_1",
                "modifications": None,
                "reason": "matched",
            },
        ),
    )

    with make_client() as client:
        res = client.evaluate_action(
            {"execution_id": "exec_1", "tool": "drop_db", "input": {}}
        )

    assert res["decision"] == "deny"
    assert res["matched_policy_id"] == "pol_1"


@respx.mock
def test_log_event_flushes_by_size():
    route = respx.post(f"{BASE_URL}/events").mock(
        return_value=httpx.Response(202),
    )

    with make_client(max_batch_size=2) as client:
        client.log_event({"execution_id": "exec_1", "type": "decision", "payload": {}})
        assert route.call_count == 0
        client.log_event({"execution_id": "exec_1", "type": "decision", "payload": {}})
        # Second enqueue should trip max_batch_size and flush both.

    assert route.call_count == 2


@respx.mock
def test_raises_on_4xx():
    respx.post(f"{BASE_URL}/executions").mock(
        return_value=httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}}),
    )

    with make_client() as client:
        with pytest.raises(RuntimeError) as exc_info:
            client.start_execution({"agent_id": "a"})
    assert "401" in str(exc_info.value)


@respx.mock
def test_retries_on_5xx():
    call_counter = {"n": 0}

    def handler(request):
        call_counter["n"] += 1
        if call_counter["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(
            201,
            json={
                "id": "exec_ok",
                "project_id": "p",
                "agent_id": "a",
                "status": "running",
                "started_at": "2025-01-01T00:00:00Z",
                "ended_at": None,
                "metadata": {},
            },
        )

    respx.post(f"{BASE_URL}/executions").mock(side_effect=handler)

    client = ArgusClient(
        api_key="k",
        base_url=BASE_URL,
        max_retries=3,
        flush_interval_seconds=10,
    )
    execution = client.start_execution({"agent_id": "a"})
    assert execution["id"] == "exec_ok"
    assert call_counter["n"] == 3


def test_requires_api_key():
    with pytest.raises(ValueError):
        ArgusClient(api_key="")
