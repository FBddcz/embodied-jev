"""Planning transport contracts, using local mocks with no paid provider calls."""
import base64
import hashlib
import json
import struct
import zlib

import httpx
import pytest

from embodied_jev.policies import DecisionPolicy


SECRET = "planning-test-secret-never-export"
OPTIONS = {"x_pos": "Move TCP 2 cm along +X", "hold": "Hold position"}
STATE = {"goal": "Put the red cube in the blue tray", "tcp": [.4, 0, .2],
         "observation_source": "RGB cameras and robot proprioception"}


def png(red):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, red, 0, 0]))) + chunk(b"IEND", b""))


FRAMES = [{"rgb": png(128), "capture_id": 41, "view": "external"},
          {"rgb": png(255), "capture_id": 42, "view": "wrist"}]


def policy(provider):
    return DecisionPolicy(provider, {"url": "https://planning.invalid/v1/decide", "key": SECRET,
                                     "model": "requested-model", "json_mode": True})


def body(provider, answer=None):
    answer = {"choice": "x_pos", "intent": "Move toward the cube", "visual_evidence": "The red cube is beside the fingers"} if answer is None else answer
    if provider == "chat":
        return {"model": "resolved-model", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(answer)}}], "usage": {"prompt_tokens": 53, "completion_tokens": 19}}
    if provider == "claude":
        return {"model": "resolved-model", "stop_reason": "tool_use", "content": [{
            "type": "tool_use", "name": "select_action", "input": answer}],
            "usage": {"input_tokens": 53, "output_tokens": 19}}
    return {"model": "resolved-model", "answers": {"action": {
        "choice": "x_pos", "probabilities": {"x_pos": 1}}}, "usage": {"input_tokens": 53, "output_tokens": 1}}


def transport(monkeypatch, handle):
    client_type = httpx.Client
    monkeypatch.setattr("embodied_jev.policies.httpx.Client", lambda: client_type(transport=httpx.MockTransport(handle)))


@pytest.mark.parametrize("provider", ["chat", "claude"])
@pytest.mark.parametrize("frame_count", [1, 2])
def test_native_images_are_labeled_ordered_and_exported_only_as_provenance(monkeypatch, provider, frame_count):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "requested-model"
        if provider == "chat":
            assert request.headers["authorization"] == f"Bearer {SECRET}"
            assert payload["response_format"] == {"type": "json_object"}
            system = payload["messages"][0]["content"]
        else:
            assert request.headers["x-api-key"] == SECRET
            schema = payload["tools"][0]["input_schema"]
            assert schema["properties"]["choice"]["enum"] == list(OPTIONS)
            assert schema["required"] == ["choice"] and schema["additionalProperties"] is False
            assert all(schema["properties"][key] == {"type": "string", "maxLength": 240}
                       for key in ("intent", "visual_evidence"))
            assert payload["tool_choice"]["disable_parallel_tool_use"]
            system = payload["system"]
        assert "Replan after every" in system and "no scripted stages" in system
        content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
        assert len(content) == 1 + 2 * frame_count
        assert json.loads(content[0]["text"]) == chosen.last_input
        for index, frame in enumerate(FRAMES[:frame_count]):
            assert content[1 + index * 2] == {"type": "text", "text": f"Camera view: {frame['view']}"}
            block = content[2 + index * 2]
            if provider == "chat":
                assert block["type"] == "image_url"
                assert block["image_url"]["url"].startswith("data:image/png;base64,")
                raw = base64.b64decode(block["image_url"]["url"].split(",", 1)[1], validate=True)
            else:
                assert block["type"] == "image"
                assert block["source"]["type"] == "base64" and block["source"]["media_type"] == "image/png"
                raw = base64.b64decode(block["source"]["data"], validate=True)
            assert raw == frame["rgb"]
        return httpx.Response(200, json=body(provider))

    transport(monkeypatch, handle)
    chosen = policy(provider)
    supplied = {**FRAMES[0], "metadata": {"ignored_metadata": SECRET, "rgb": FRAMES[0]["rgb"]}} if frame_count == 1 else FRAMES
    answer = chosen.choose_plan(STATE, "Choose the next step", OPTIONS, image=supplied)
    assert len(requests) == chosen.calls == len(chosen.latencies) == 1
    assert chosen.tokens == 53 and chosen.output_tokens == 19
    assert answer["model_call"] and answer["choice"] == "x_pos" and answer["probabilities"] == {}
    assert answer["selected_probability"] is None and answer["provider_confidence"] is None
    assert answer["intent"] == "Move toward the cube"
    assert answer["image_count"] == frame_count and answer["image_views"] == [f["view"] for f in FRAMES[:frame_count]]
    assert answer["image_sha256"] == hashlib.sha256(FRAMES[0]["rgb"]).hexdigest()
    assert chosen.last_input == {"state": STATE,
        "decision": {"type": "choice", "instructions": "Choose the next step", "criteria": OPTIONS},
        "images": [{"sha256": hashlib.sha256(f["rgb"]).hexdigest(), "byte_length": len(f["rgb"]),
                    "capture_id": f["capture_id"], "view": f["view"]} for f in FRAMES[:frame_count]]}
    exported = json.dumps({"input": chosen.last_input, "decision": answer})
    assert SECRET not in exported and "data:image" not in exported and "ignored_metadata" not in exported
    assert all(base64.b64encode(f["rgb"]).decode() not in exported for f in FRAMES)
    assert answer["latency_ms"] == chosen.latencies[0]
    chosen.close()


@pytest.mark.parametrize("provider", ["chat", "claude", "jev", "local", "minicpm"])
def test_state_only_singleton_always_calls_model_and_never_uses_default(monkeypatch, provider):
    chosen = policy(provider)
    seen = []
    singleton = {"x_pos": OPTIONS["x_pos"]}

    def handle(request):
        payload = json.loads(request.content)
        seen.append(payload)
        if provider in ("jev", "local"):
            assert payload["state"] == STATE
            assert payload["questions"]["action"]["criteria"] == singleton
        else:
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            assert isinstance(content, str)
            assert json.loads(content) == chosen.last_input
        return httpx.Response(200, json=body(provider))

    def infer(state, spec):
        seen.append(spec)
        assert state == STATE and spec["criteria"] == singleton
        return {"choice": "x_pos", "probabilities": {"x_pos": 1}}

    transport(monkeypatch, handle)
    monkeypatch.setattr(chosen, "_local_inference", infer)
    # An invalid baseline hint must not override, bypass, or prevent a model call.
    answer = chosen.choose_plan(STATE, "Choose next step", singleton, baseline_choice="hold")
    assert len(seen) == chosen.calls == len(chosen.latencies) == 1
    assert answer["model_call"] and answer["choice"] == "x_pos"
    assert answer["image_count"] == 0 and answer["image_views"] == [] and answer["image_sha256"] is None
    assert "images" not in chosen.last_input
    chosen.close()


def test_only_explicit_baseline_can_avoid_a_model_call(monkeypatch):
    chosen = policy("baseline")
    monkeypatch.setattr(chosen, "_post", lambda *a, **kw: pytest.fail("Baseline cannot request a model"))
    for default in (None, "unknown", []):
        with pytest.raises(ValueError, match="explicitly supplied"):
            chosen.choose_plan(STATE, "Choose", OPTIONS, baseline_choice=default)
    answer = chosen.choose_plan(STATE, "Choose", OPTIONS, baseline_choice="hold")
    assert answer["choice"] == "hold" and answer["reason"] == "baseline" and not answer["model_call"]
    assert chosen.calls == 0 and chosen.latencies == []


@pytest.mark.parametrize("provider", ["baseline", "jev", "local", "minicpm"])
def test_unsupported_provider_rejects_pixels_before_request_or_fallback(monkeypatch, provider):
    chosen = policy(provider)
    monkeypatch.setattr(chosen, "_post", lambda *a, **kw: pytest.fail("Image input cannot be silently dropped"))
    monkeypatch.setattr(chosen, "_local_inference", lambda *a: pytest.fail("Unsupported model cannot be loaded"))
    with pytest.raises(ValueError, match="Native image planning"):
        chosen.choose_plan(STATE, "Choose", OPTIONS, baseline_choice="hold", image=FRAMES)
    assert chosen.calls == 0 and chosen.latencies == [] and chosen.last_input is None


@pytest.mark.parametrize("provider", ["chat", "claude"])
@pytest.mark.parametrize("answer", [
    [], {}, {"choice": []}, {"choice": "unknown"}, {"choice": "x_pos", "intent": None},
    {"choice": "x_pos", "visual_evidence": ["cube"]}, {"choice": "x_pos", "intent": "a" * 241},
    {"choice": "x_pos", "reasoning": "private reasoning must not be retained"},
    {"choice": "x_pos", "visual_evidence": "data:image/png;base64,private-pixels"},
])
def test_malformed_model_answers_are_rejected_without_rule_fallback(monkeypatch, provider, answer):
    transport(monkeypatch, lambda request: httpx.Response(200, json=body(provider, answer)))
    chosen = policy(provider)
    with pytest.raises(ValueError):
        chosen.choose_plan(STATE, "Choose", OPTIONS, baseline_choice="hold", image=FRAMES)
    assert chosen.calls == len(chosen.latencies) == 1 and chosen.latencies[0] >= 0
    assert chosen.tokens == 53 and chosen.output_tokens == 19
    assert chosen.last_input["state"] == STATE
    chosen.close()


@pytest.mark.parametrize("provider", ["chat", "claude", "jev", "local", "minicpm"])
def test_failed_planning_attempts_record_latency_without_baseline_fallback(monkeypatch, provider):
    chosen = policy(provider)
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("simulated failure")

    monkeypatch.setattr(chosen, "_post", fail)
    monkeypatch.setattr(chosen, "_local_inference", fail)
    with pytest.raises(httpx.ReadTimeout):
        chosen.choose_plan(STATE, "Choose", OPTIONS, baseline_choice="hold")
    assert chosen.calls == len(calls) == len(chosen.latencies) == 1
    assert chosen.tokens == 0 and chosen.output_tokens == 0


@pytest.mark.parametrize("provider", ["chat", "claude"])
def test_public_summaries_and_text_inputs_cannot_echo_key_and_input_is_snapshot(monkeypatch, provider):
    chosen = policy(provider)
    state = {"goal": f"Place cube {SECRET}", "tcp": [.4, 0, .2]}
    options = dict(OPTIONS)

    def handle(request):
        payload = json.loads(request.content)
        assert SECRET not in json.dumps(payload)
        state["tcp"][0] = 99
        options["x_pos"] = "Changed after request"
        response = body(provider, {"choice": "x_pos", "intent": f"Move {SECRET}",
                                  "visual_evidence": f"  Cube\nnear   fingers {SECRET}  "})
        response["model"] = f"model-{SECRET}"
        return httpx.Response(200, json=response)

    transport(monkeypatch, handle)
    answer = chosen.choose_plan(state, f"Choose {SECRET}", options,
                               image={**FRAMES[0], "capture_id": f"frame-{SECRET}"})
    assert chosen.last_input["state"]["tcp"][0] == .4
    assert chosen.last_input["decision"]["criteria"]["x_pos"] == OPTIONS["x_pos"]
    assert answer["intent"] == "Move [已隐藏]"
    assert answer["visual_evidence"] == "Cube near fingers [已隐藏]"
    assert SECRET not in json.dumps({"answer": answer, "input": chosen.last_input, "model": chosen.model})
    chosen.close()


@pytest.mark.parametrize("bad_image", [
    [], FRAMES + [FRAMES[0]], [FRAMES[0], FRAMES[0]], {},
    {**FRAMES[0], "rgb": "data:image/png;base64,AA=="}, {**FRAMES[0], "rgb": b"not-a-png"},
    {**FRAMES[0], "capture_id": True}, {**FRAMES[0], "capture_id": ""}, {**FRAMES[0], "view": "unknown"},
])
def test_invalid_images_fail_before_counting_a_model_attempt(monkeypatch, bad_image):
    chosen = policy("chat")
    monkeypatch.setattr(chosen, "_post", lambda *a, **kw: pytest.fail("No request for invalid image"))
    with pytest.raises(ValueError):
        chosen.choose_plan(STATE, "Choose", OPTIONS, image=bad_image)
    assert chosen.calls == 0 and chosen.latencies == [] and chosen.last_input is None


@pytest.mark.parametrize("state", [
    {"secret": "anything"}, {"input": {"api_key": SECRET}}, {"tcp": [float("nan")]},
    {"pixels": FRAMES[0]["rgb"]}, {"text": "data:image/png;base64,AA=="},
])
def test_invalid_or_sensitive_text_is_never_recorded_or_sent(monkeypatch, state):
    chosen = policy("chat")
    monkeypatch.setattr(chosen, "_post", lambda *a, **kw: pytest.fail("No request for invalid input"))
    with pytest.raises(ValueError, match="finite JSON"):
        chosen.choose_plan(state, "Choose", OPTIONS)
    assert chosen.calls == 0 and chosen.last_input is None


@pytest.mark.parametrize("provider", ["chat", "claude"])
@pytest.mark.parametrize("failure", ["empty", "truncated", "duplicate", "invalid_json"])
def test_incomplete_native_responses_fail_with_one_latency(monkeypatch, provider, failure):
    response = body(provider)
    if failure == "empty":
        response = {}
    elif failure == "truncated":
        if provider == "chat":
            response["choices"][0]["finish_reason"] = "length"
        else:
            response["stop_reason"] = "max_tokens"
    elif failure == "duplicate":
        response["choices" if provider == "chat" else "content"] *= 2
    if failure == "invalid_json":
        transport(monkeypatch, lambda request: httpx.Response(200, text="not JSON"))
    else:
        transport(monkeypatch, lambda request: httpx.Response(200, json=response))
    chosen = policy(provider)
    with pytest.raises(ValueError):
        chosen.choose_plan(STATE, "Choose", OPTIONS, baseline_choice="hold")
    assert chosen.calls == len(chosen.latencies) == 1
    chosen.close()
