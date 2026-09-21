"""Evaluation contracts, not measurements of model performance."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from embodied_jev import evaluation as e
from embodied_jev.benchmark_worker import MetaWorld, validate_action


def manifest(tmp_path, **changes):
    value = {"format": "embodied-jev-suite-v1", "name": "fixture", "backend": "metaworld", "split": "smoke",
             "cases": [{"id": "reach-0", "task": "reach-v3", "seed": 0}], **changes}
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(value))
    return path


def options(tmp_path, **changes):
    defaults = dict(manifest=manifest(tmp_path), output=str(tmp_path / "run"),
        worker_python="fixture-python", policy="noop", max_steps=3, max_calls=2,
        timeout=30., threshold=0., action_scale=.5, control_mode="skills", observation_mode="privileged")
    return SimpleNamespace(**{**defaults, **changes})


@pytest.mark.parametrize("cases", [[], [{"id": "../escape", "task": "reach-v3", "seed": 0}],
    [{"id": "a", "task": "reach-v3", "seed": 0}, {"id": "b", "task": "reach-v3", "seed": 0}],
    [{"id": "a", "task": "reach-v3", "seed": True}]])
def test_manifest_rejects_unsafe_or_duplicate_trials(tmp_path, cases):
    with pytest.raises(ValueError):
        e.load_manifest(manifest(tmp_path, cases=cases))


def test_summary_keeps_missing_and_unscored_trials_visible():
    rows = [{"task": "a", "success": True, "status": "success"},
            {"task": "a", "success": False, "status": "step_limit"},
            {"task": "b", "success": None, "status": "setup_error"}]
    result = e.aggregate(rows, 4)
    assert result["success_rate"] == .5
    assert result["scored"] == 2 and result["unscored"] == 1 and result["missing"] == 1
    assert not result["complete"] and result["per_task"]["a"]["n"] == 2
    assert e.wilson(10, 10)[0] == pytest.approx(.7224672)
    assert e.wilson(0, 0) is None


def test_state_projection_never_reads_success_or_private_simulator():
    backend = object.__new__(MetaWorld)
    backend.case, backend.raw = {"task": "push-v3"}, np.arange(39.)
    state = backend.observation()
    assert state["tcp"] == [0, 1, 2] and state["goal"] == [36, 37, 38]
    assert "success" not in state and "reward" not in state
    assert state["object_slots"][0]["position"] == [4, 5, 6]
    backend.raw = np.zeros(38)
    with pytest.raises(ValueError, match="39D"):
        backend.observation()


@pytest.mark.parametrize("action", [[0, 0, 0], [float("nan"), 0, 0, 1], [1.01, 0, 0, 1]])
def test_model_action_is_rejected_not_silently_clipped(action):
    with pytest.raises(ValueError):
        validate_action(action, 4)


class FakeWorker:
    packets = []
    closed = False
    def __init__(self, python, log):
        self.steps = 0
        type(self).packets = []
    def request(self, packet, timeout=60):
        type(self).packets.append(packet)
        if packet["command"] == "reset":
            return {"observation": {"task": "fixture"}, "metadata": {"success_source": "fixture"}}
        self.steps += 1
        return {"observation": {"task": "fixture"}, "success": self.steps == 2,
                "terminated": False, "truncated": False, "action": packet["action"]}
    def close(self):
        type(self).closed = True


def test_external_loop_uses_official_success_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "Worker", FakeWorker)
    args = options(tmp_path)
    report = e.run(args)
    assert report["aggregate"]["successes"] == 1
    assert report["episodes"][0]["steps"] == 2
    assert len(FakeWorker.packets) == 3 and FakeWorker.closed
    trace = (tmp_path / "run/reach-0-trace.jsonl").read_text()
    assert len(trace.splitlines()) == 2
    with pytest.raises(FileExistsError):
        e.run(args)


def test_setup_failure_keeps_coverage_incomplete(tmp_path, monkeypatch):
    class Broken(FakeWorker):
        def request(self, *args, **kwargs):
            raise RuntimeError("installation error")
    monkeypatch.setattr(e, "Worker", Broken)
    report = e.run(options(tmp_path))
    assert report["episodes"][0]["success"] is None
    assert report["aggregate"]["success_rate"] is None
    assert not report["aggregate"]["complete"]


def test_model_choices_are_executed_without_goal_correction_and_budget_is_hard(tmp_path, monkeypatch):
    from embodied_jev import policies
    states = []
    class FakePolicy:
        def __init__(self, provider):
            self.calls, self.tokens, self.output_tokens, self.latencies, self.model = 0, 0, 0, [], "fixture"
        def choose_channels(self, state, questions):
            states.append(state)
            self.calls += 1
            return {a: {"choice": c, "selected_probability": .9}
                    for a, c in dict(x="negative", y="positive", z="hold", gripper="close").items()}
        def close(self):
            pass
    class NeverSuccess(FakeWorker):
        def request(self, packet, timeout=60):
            result = super().request(packet, timeout)
            if packet["command"] == "step":
                result["success"] = False
            return result
    monkeypatch.setattr(policies, "DecisionPolicy", FakePolicy)
    monkeypatch.setattr(e, "Worker", NeverSuccess)
    args = options(tmp_path)
    args.policy = "jev"
    report = e.run(args)
    assert report["episodes"][0]["status"] == "call_budget"
    assert report["episodes"][0]["model_calls"] == 2
    assert NeverSuccess.packets[-1]["action"] == [-.5, .5, 0., 1.]
    assert "evaluation" not in json.dumps(states) and "success" not in json.dumps(states)


def test_jev_provider_round_trip_to_libero_normalized_action(tmp_path, monkeypatch):
    import httpx
    from embodied_jev.policies import DecisionPolicy
    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture-not-a-real-key")
    sent = []
    choices = dict(x="negative", y="hold", z="positive", gripper="close")
    def post(self, url, **kwargs):
        body = kwargs["json"]
        sent.append(body)
        answers = {name: {"choice": choices[name], "probabilities": {
            option: .9 if option == choices[name] else .05 for option in spec["criteria"]}}
            for name, spec in body["questions"].items()}
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"model": "fixture-jev", "answers": answers})
    monkeypatch.setattr(DecisionPolicy, "_post", post)
    monkeypatch.setattr(e, "Worker", FakeWorker)
    args = options(tmp_path)
    args.policy = "jev"
    args.manifest = manifest(tmp_path, backend="libero", cases=[{
        "id": "spatial-0", "suite": "libero_spatial", "task_id": 0, "init_index": 4, "seed": 7}])
    report = e.run(args)
    assert FakeWorker.packets[0]["case"]["init_index"] == 4
    assert FakeWorker.packets[-1]["action"] == [-.5, 0., .5, 0., 0., 0., 1.]
    assert report["episodes"][0]["model_calls"] == 2
    assert all(set(body["questions"]) == {"x", "y", "z", "gripper"} for body in sent)
    assert "fixture-not-a-real-key" not in json.dumps(report)
    assert "evaluation" not in json.dumps(sent) and "success" not in json.dumps(sent)


def fake_jev_responses(monkeypatch):
    """Exercise real response parsing, with no network or performance measurement."""
    import httpx
    from embodied_jev.policies import DecisionPolicy
    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture-environment-key")
    sent = []
    def post(self, url, **kwargs):
        sent.append({"url": url, **kwargs})
        answers = {name: {"choice": "hold", "probabilities": {
            option: .9 if option == "hold" else .05 for option in spec["criteria"]}}
            for name, spec in kwargs["json"]["questions"].items()}
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "fixture-resolved-model", "answers": answers,
            "usage": {"input_tokens": 11, "output_tokens": 7}})
    monkeypatch.setattr(DecisionPolicy, "_post", post)
    return sent


@pytest.mark.parametrize("terminal, expected_status, expected_success", [
    ("success", "success", True),
    ("terminated", "terminated", False),
    ("truncated", "step_limit", False),
])
def test_action_repeat_discards_remaining_actions_at_episode_end(
        tmp_path, monkeypatch, terminal, expected_status, expected_success):
    sent = fake_jev_responses(monkeypatch)
    class EndingWorker(FakeWorker):
        def request(self, packet, timeout=60):
            result = super().request(packet, timeout)
            if packet["command"] == "step":
                result.update(success=False, terminated=False, truncated=False)
                result[terminal] = self.steps == 2
            return result
    monkeypatch.setattr(e, "Worker", EndingWorker)
    report = e.run(options(tmp_path, policy="jev", action_repeat=5, max_steps=10))
    row = report["episodes"][0]
    assert (row["status"], row["success"], row["steps"], row["model_calls"]) == (
        expected_status, expected_success, 2, 1)
    assert len(sent) == 1
    assert sum(packet["command"] == "step" for packet in EndingWorker.packets) == 2
    records = [json.loads(line) for line in (tmp_path / "run/reach-0-trace.jsonl").read_text().splitlines()]
    assert [record["chunk_offset"] for record in records] == [0, 1]
    assert records[0]["decisions"] is not None and records[1]["decisions"] is None
    assert all(record["decision_index"] == 0 for record in records)


@pytest.mark.parametrize("max_steps, max_calls, expected_steps, expected_calls, expected_status", [
    (20, 2, 10, 2, "call_budget"),
    (7, 10, 7, 2, "step_limit"),
])
def test_action_repeat_obeys_call_and_environment_step_budgets(
        tmp_path, monkeypatch, max_steps, max_calls, expected_steps, expected_calls, expected_status):
    sent = fake_jev_responses(monkeypatch)
    class ContinuingWorker(FakeWorker):
        def request(self, packet, timeout=60):
            result = super().request(packet, timeout)
            if packet["command"] == "step":
                result["success"] = False
            return result
    monkeypatch.setattr(e, "Worker", ContinuingWorker)
    report = e.run(options(tmp_path, policy="jev", action_repeat=5,
                           max_steps=max_steps, max_calls=max_calls))
    row = report["episodes"][0]
    assert (row["steps"], row["model_calls"], row["status"]) == (
        expected_steps, expected_calls, expected_status)
    assert len(sent) == expected_calls
    assert sum(packet["command"] == "step" for packet in ContinuingWorker.packets) == expected_steps
    records = [json.loads(line) for line in (tmp_path / "run/reach-0-trace.jsonl").read_text().splitlines()]
    assert len(records) == expected_steps
    assert [record["decision_index"] for record in records] == [step // 5 for step in range(expected_steps)]
    assert [record["chunk_offset"] for record in records] == [step % 5 for step in range(expected_steps)]
    assert all((record["decisions"] is not None) == (record["chunk_offset"] == 0) for record in records)
    assert all(request["json"]["state"]["action_contract"]["repeat_env_steps"] == 5 for request in sent)


def test_saved_connection_is_loaded_once_reused_and_kept_out_of_artifacts(tmp_path, monkeypatch, capsys):
    sent = fake_jev_responses(monkeypatch)
    connection = {"key": "fixture-saved-private-key", "url": "https://private-fixture.invalid/policy",
                  "model": "fixture-configured-model", "json_mode": True}
    loads = []
    def load(provider):
        loads.append(provider)
        return dict(connection)
    monkeypatch.setattr(e, "saved_connection", load)
    monkeypatch.setattr(e, "Worker", FakeWorker)
    args = options(tmp_path, policy="jev", connection_source="saved", action_repeat=5)
    args.manifest = manifest(tmp_path, cases=[
        {"id": "reach-0", "task": "reach-v3", "seed": 0},
        {"id": "reach-1", "task": "reach-v3", "seed": 1},
    ])
    report = e.run(args)
    assert loads == ["jev"]
    assert report["aggregate"]["successes"] == 2
    assert report["configuration"]["configured_model"] == connection["model"]
    assert report["configuration"]["connection_source"] == "saved"
    assert all(row["model"] == "fixture-resolved-model" for row in report["episodes"])
    assert len(sent) == 2
    assert all(request["url"] == connection["url"] for request in sent)
    assert all(request["headers"]["Authorization"] == f"Bearer {connection['key']}" for request in sent)
    artifact_text = json.dumps(report) + capsys.readouterr().out
    artifact_text += "".join(path.read_text() for path in (tmp_path / "run").iterdir() if path.is_file())
    assert connection["key"] not in artifact_text and connection["url"] not in artifact_text
    assert "fixture-environment-key" not in artifact_text and "_connection" not in artifact_text


def test_preloaded_saved_connection_is_reused_without_store_access_or_secret_export(
        tmp_path, monkeypatch, capsys):
    sent = fake_jev_responses(monkeypatch)
    connection = {"key": "fixture-preflight-private-key", "url": "https://preflight-fixture.invalid/policy",
                  "model": "fixture-preflight-model", "json_mode": True}
    original = dict(connection)
    def unexpected_load(provider):
        pytest.fail("Preflight already loaded the connection; evaluation must not load it again")
    monkeypatch.setattr(e, "saved_connection", unexpected_load)
    monkeypatch.setattr(e, "Worker", FakeWorker)
    args = options(tmp_path, policy="jev", connection_source="saved", action_repeat=5)
    args.manifest = manifest(tmp_path, cases=[
        {"id": "reach-0", "task": "reach-v3", "seed": 0},
        {"id": "reach-1", "task": "reach-v3", "seed": 1},
    ])
    report = e.run(args, connection=connection)
    assert connection == original
    assert report["aggregate"]["successes"] == 2 and len(sent) == 2
    assert report["configuration"]["configured_model"] == connection["model"]
    assert report["configuration"]["connection_source"] == "saved"
    assert all(request["json"]["model"] == connection["model"] for request in sent)
    assert all(request["url"] == connection["url"] for request in sent)
    assert all(request["headers"]["Authorization"] == f"Bearer {connection['key']}" for request in sent)
    assert all(row["model"] == "fixture-resolved-model" for row in report["episodes"])
    exported = json.dumps(report) + capsys.readouterr().out
    exported += "".join(path.read_text() for path in (tmp_path / "run").iterdir() if path.is_file())
    assert connection["key"] not in exported and connection["url"] not in exported
    assert "fixture-environment-key" not in exported and "_connection" not in exported
