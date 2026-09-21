"""Transport accounting contracts; fixture responses are not benchmark data."""
import json
from types import SimpleNamespace

import httpx
import pytest

from embodied_jev import evaluation as e
from embodied_jev import evaluation_meter as meter
from embodied_jev.policies import DecisionPolicy


CONNECTION = {"key": "fixture-secret-do-not-export", "url": "https://private-fixture.invalid/api",
              "model": "configured-fixture-model", "json_mode": True}
PRIVATE_BODY = "private-response-body-do-not-export"
MISSING = object()


def response_body(provider="jev", usage=MISSING, **changes):
    choices = {name: "hold" for name in ("x", "y", "z", "gripper")}
    if provider == "chat":
        body = {"model": "returned-fixture-model", "choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps(choices)}}]}
    else:
        body = {"model": "returned-fixture-model", "answers": {name: {
            "choice": "hold", "probabilities": {option: .9 if option == "hold" else .05
                for option in spec["criteria"]}}
            for name, spec in e.motor_questions().items()}}
    if usage is not MISSING:
        body["usage"] = usage
    return {**body, **changes}


@pytest.fixture
def transport(monkeypatch):
    """Advance a deterministic clock so both failed and fast replies have known timing."""
    clock, sent, replies = [10.], [], []
    monkeypatch.setattr(meter.time, "perf_counter", lambda: clock[0])
    def post(self, url, **kwargs):
        sent.append({"url": url, **kwargs})
        clock[0] += .125
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(DecisionPolicy, "_post", post)
    def enqueue(body=MISSING, *, status=200, content=None, error=None):
        if error is not None:
            replies.append(error)
        else:
            request = httpx.Request("POST", CONNECTION["url"])
            replies.append(httpx.Response(status, request=request,
                **({"content": content} if content is not None else {"json": body})))
    return enqueue, sent


def choose(policy):
    return policy.choose_channels({"observation": {"tcp": [0, 0, 0]}}, e.motor_questions())


@pytest.mark.parametrize("provider, input_name, output_name", [
    ("jev", "input_tokens", "output_tokens"),
    ("chat", "prompt_tokens", "completion_tokens"),
])
@pytest.mark.parametrize("usage_kind", ["zero", "missing", "null", "null_fields", "input_only"])
def test_token_zero_is_measured_but_missing_or_null_is_unknown(
        transport, provider, input_name, output_name, usage_kind):
    enqueue, _ = transport
    usages = {"zero": {input_name: 0, output_name: 0}, "missing": MISSING, "null": None,
              "null_fields": {input_name: None, output_name: None}, "input_only": {input_name: 0}}
    enqueue(response_body(provider, usages[usage_kind]))
    policy = meter.make_policy(provider, CONNECTION)
    try:
        choose(policy)
        record = policy.api_calls[0]
        assert policy.calls == 1 and len(policy.api_calls) == 1
        assert record["input_tokens"] == (0 if usage_kind in {"zero", "input_only"} else None)
        assert record["output_tokens"] == (0 if usage_kind == "zero" else None)
        assert record["http_status"] == 200 and record["latency_ms"] == 125.
        summary = meter.token_summary(policy.api_calls, policy.calls)
        assert summary["complete"] is (usage_kind == "zero")
        assert summary["input_reported_calls"] == int(usage_kind in {"zero", "input_only"})
        assert summary["output_reported_calls"] == int(usage_kind == "zero")
    finally:
        policy.close()


@pytest.mark.parametrize("provider, input_name, output_name", [
    ("jev", "input_tokens", "output_tokens"),
    ("chat", "prompt_tokens", "completion_tokens"),
])
@pytest.mark.parametrize("invalid", [-1, True, False])
def test_malformed_token_usage_is_never_reported_as_measured(
        transport, provider, input_name, output_name, invalid):
    enqueue, _ = transport
    enqueue(response_body(provider, {input_name: invalid, output_name: 7}))
    policy = meter.make_policy(provider, CONNECTION)
    try:
        # Legacy policy parsing treats False as zero. The benchmark meter still
        # must not promote a malformed boolean to an observed token count.
        if invalid is False:
            choose(policy)
        else:
            with pytest.raises(ValueError, match="token counts"):
                choose(policy)
        assert policy.api_calls[0]["input_tokens"] is None
        assert policy.api_calls[0]["output_tokens"] == 7
        summary = meter.token_summary(policy.api_calls, policy.calls)
        assert not summary["input_complete"] and summary["output_complete"]
        assert summary["output_reported_tokens"] == 7
    finally:
        policy.close()


@pytest.mark.parametrize("failure", ["http_status", "decision", "json", "network"])
def test_failed_calls_keep_usage_and_timing_without_exporting_response_or_secrets(
        tmp_path, monkeypatch, transport, failure):
    enqueue, _ = transport
    if failure == "http_status":
        enqueue({"model": f"model-{CONNECTION['key']}", "usage": {"input_tokens": 11, "output_tokens": 7},
                 "error": PRIVATE_BODY}, status=429)
    elif failure == "decision":
        enqueue(response_body(usage={"input_tokens": 11, "output_tokens": 7},
                              answers={"private": PRIVATE_BODY}, error=CONNECTION["key"]))
    elif failure == "json":
        enqueue(content=(PRIVATE_BODY + CONNECTION["key"]).encode())
    else:
        enqueue(error=httpx.ConnectError(f"{CONNECTION['url']} {CONNECTION['key']} {PRIVATE_BODY}"))

    class Worker:
        def __init__(self, python, log):
            pass
        def request(self, packet, timeout=60):
            assert packet["command"] == "reset", "A failed model call must execute no action"
            return {"observation": {"task": "fixture", "tcp": [0, 0, 0]}, "metadata": {}}
        def close(self):
            pass
    monkeypatch.setattr(e, "Worker", Worker)
    args = SimpleNamespace(worker_python="fixture", max_steps=10, max_calls=2, timeout=30,
        observation_mode="privileged", action_repeat=5, policy="jev", threshold=0, action_scale=.5,
        _connection=dict(CONNECTION))
    row = e.external_episode({"backend": "metaworld"},
        {"id": "reach-0", "task": "reach-v3", "seed": 0}, args, tmp_path)
    assert row["status"] == "runtime_error" and row["success"] is False
    assert row["steps"] == 0 and row["model_calls"] == 1
    assert row["model_latency_ms"] == [125.]
    assert row["api_calls"][0]["latency_ms"] == 125.
    assert row["api_calls"][0]["http_status"] == (None if failure == "network" else 429 if failure == "http_status" else 200)
    reported = failure in {"http_status", "decision"}
    assert row["input_tokens"] == (11 if reported else None)
    assert row["output_tokens"] == (7 if reported else None)
    assert row["token_usage"]["complete"] is reported
    if failure == "http_status":
        assert row["http_status"] == 429
        assert row["api_calls"][0]["model"] == "model-[hidden]"
    if failure == "network":
        assert row["api_calls"][0]["error_type"] == "ConnectError"
    aggregate = e.aggregate([row], 1)
    assert aggregate["input_tokens"] == row["input_tokens"]
    assert aggregate["output_tokens"] == row["output_tokens"]
    exported = json.dumps({"episode": row, "aggregate": aggregate})
    exported += "".join(path.read_text() for path in tmp_path.iterdir() if path.is_file())
    assert all(private not in exported for private in (CONNECTION["key"], CONNECTION["url"], PRIVATE_BODY))


def test_partial_token_totals_are_separate_from_complete_episode_totals():
    records = [{"input_tokens": 0, "output_tokens": 3},
               {"input_tokens": 11, "output_tokens": None},
               {"input_tokens": None, "output_tokens": 7}]
    summary = meter.token_summary(records, attempts=4)
    assert summary == {"source": "provider_reported", "attempts": 4, "http_requests": 3,
        "input_reported_calls": 2, "input_reported_tokens": 11, "input_complete": False,
        "output_reported_calls": 2, "output_reported_tokens": 10, "output_complete": False,
        "complete": False}
    aggregate = e.aggregate([{"task": "fixture", "success": False, "status": "runtime_error",
        "model_calls": 4, "api_calls": records}], 1)
    assert aggregate["input_tokens"] is None and aggregate["output_tokens"] is None
    assert aggregate["token_usage"]["input_reported_tokens"] == 11
    assert aggregate["token_usage"]["output_reported_tokens"] == 10


def test_legacy_tokens_without_per_request_evidence_remain_unverified():
    legacy = {"task": "fixture", "success": True, "status": "success", "model_calls": 2,
              "input_tokens": 110, "output_tokens": 14}
    aggregate = e.aggregate([legacy], 1)
    assert aggregate["successes"] == 1
    assert aggregate["input_tokens"] is None and aggregate["output_tokens"] is None
    assert aggregate["token_usage"]["http_requests"] == 0
    assert not aggregate["token_usage"]["complete"]
    baseline = e.aggregate([{**legacy, "model_calls": 0, "input_tokens": 0, "output_tokens": 0}], 1)
    assert baseline["input_tokens"] == 0 and baseline["output_tokens"] == 0
    assert baseline["token_usage"]["complete"]
