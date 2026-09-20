import copy
import threading
import time

import httpx
import numpy as np
import pytest

from embodied_jev.runtime import Session, run_headless


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


def test_pause_step_and_stop_own_the_physics_state():
    session = Session(preview=False, speed=4)
    try:
        session.start(single_step=True)
        wait_until(lambda: session.status == "paused")
        assert session.cycles == 1
        qpos = session.world.data.qpos.copy()
        time.sleep(.12)
        np.testing.assert_array_equal(qpos, session.world.data.qpos)
        session.start()
        wait_until(lambda: session.cycles >= 2)
        session.stop()
        qpos = session.world.data.qpos.copy()
        session.worker.join(5)
        assert not session.worker.is_alive()
        np.testing.assert_array_equal(qpos, session.world.data.qpos)
        assert session.status == "stopped"
    finally:
        session.stop()


def test_stop_ignores_delayed_model_answer():
    session = Session(preview=False, speed=0)
    entered, release = threading.Event(), threading.Event()
    original = session.policy.choose
    def delayed(*args):
        entered.set()
        release.wait(5)
        return original(*args)
    session.policy.choose = delayed
    qpos = session.world.data.qpos.copy()
    session.start()
    assert entered.wait(5)
    session.stop()
    release.set()
    session.worker.join(5)
    assert session.status == "stopped"
    assert session.cycles == 0
    np.testing.assert_array_equal(qpos, session.world.data.qpos)


def test_uncertain_gate_and_threshold_resume():
    session = Session(preview=False, speed=0, max_cycles=1)
    original = session.policy.choose
    def uncertain(*args):
        answer = original(*args)
        answer["selected_probability"] = .4
        return answer
    session.policy.choose = uncertain
    try:
        session.start()
        wait_until(lambda: session.status == "uncertain")
        assert session.cycles == 0
        session.start(threshold=.3)
        session.worker.join(10)
        assert session.status == "exhausted"
        assert session.cycles == 1
    finally:
        session.stop()


def test_headless_records_uncertainty_without_waiting_for_timeout(monkeypatch):
    from embodied_jev.policies import DecisionPolicy
    def uncertain(self, *args):
        return {"choice": "approach", "selected_probability": .4, "model_call": True}
    monkeypatch.setattr(DecisionPolicy, "choose", uncertain)
    session = run_headless(preview=False, threshold=.55, timeout=10)
    assert session.status == "uncertain"
    assert session.cycles == 0
    assert not session.worker.is_alive()
    assert session.export()["last_decision"]["selected_probability"] == .4


def test_headless_timeout_does_not_execute_a_delayed_answer(monkeypatch):
    from embodied_jev.policies import DecisionPolicy
    original = DecisionPolicy.choose
    def delayed(self, *args):
        time.sleep(.2)
        return original(self, *args)
    monkeypatch.setattr(DecisionPolicy, "choose", delayed)
    session = run_headless(preview=False, timeout=.05)
    assert session.status == "timeout"
    assert session.cycles == 0
    assert not session.worker.is_alive()


def test_duplicate_start_does_not_replace_single_step_mode():
    from concurrent.futures import ThreadPoolExecutor
    session = Session(preview=False, speed=0)
    entered, release = threading.Event(), threading.Event()
    original = session.policy.choose
    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    session.policy.choose = delayed
    try:
        session.start(single_step=True)
        assert entered.wait(5)
        worker = session.worker
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: session.start(), range(8)))
        assert session.worker is worker and session.single_step
        release.set()
        wait_until(lambda: session.status == "paused")
        assert session.cycles == 1
        assert sum(e["event"] == "step" for e in session.events) == 1
    finally:
        release.set()
        session.stop()
        session.worker.join(5)


def test_repeated_stationary_choices_stop_without_a_rule_fallback():
    session = Session(preview=False, speed=0, max_cycles=20)
    choices = []
    def hold(observation, question, options, baseline_choice, history):
        choice = "hold" if "hold" in options else baseline_choice
        choices.append(choice)
        return {"choice": choice, "selected_probability": None, "probabilities": {}, "model_call": False}
    session.policy.choose = hold
    session.start()
    session.worker.join(10)
    assert session.status == "stalled"
    assert session.cycles == 3
    assert all(h["action"]["id"] == "hold" for h in session.history)
    assert session.events[-1]["event"] == "stalled"
    assert not session.worker.is_alive()


def test_events_and_exports_do_not_expose_provider_exception_text(caplog):
    session = Session(preview=False, speed=0)
    def fail(*args):
        raise ValueError("secret-from-provider-response")
    session.policy.choose = fail
    session.start()
    session.worker.join(5)
    assert session.status == "error"
    assert "secret-from-provider-response" not in str(session.export())
    assert "secret-from-provider-response" not in caplog.text
    assert session.events[-1]["level"] == "error"
    for _ in range(205):
        session._event("test", "test")
    assert len(session.snapshot()["events"]) == 200


def test_history_keeps_full_candidates_and_actual_decision_inputs():
    session = Session(preview=False, speed=0)
    try:
        session.start(single_step=True)
        wait_until(lambda: session.status == "paused")
        entry = session.export()["history"][0]
        assert {c["id"] for c in entry["candidates"]} == {"direct", "gentle", "hold"}
        assert entry["decision_inputs"] == {"phase": None, "action": None}
    finally:
        session.stop()
        session.worker.join(5)


@pytest.mark.parametrize("control", ["pause", "stop"])
def test_control_during_preview_does_not_start_another_decision(monkeypatch, control):
    import embodied_jev.runtime as runtime
    session = Session(preview=False, speed=0)
    preview_ready, release_preview, action_gate = (threading.Event() for _ in range(3))
    original_candidates = runtime.candidates
    original_choose = session.policy.choose
    original_gate = session._choose
    calls = []

    def delayed_preview(*args, **kwargs):
        options = original_candidates(*args, **kwargs)
        preview_ready.set()
        assert release_preview.wait(5)
        return options

    def tracked_choice(*args):
        calls.append(list(args[2]))
        return original_choose(*args)

    def tracked_gate(stage, *args):
        if stage == "action":
            action_gate.set()
        return original_gate(stage, *args)

    monkeypatch.setattr(runtime, "candidates", delayed_preview)
    session.policy.choose = tracked_choice
    session._choose = tracked_gate
    try:
        session.start(single_step=True)
        assert preview_ready.wait(5)
        getattr(session, control)()
        qpos = session.world.data.qpos.copy()
        release_preview.set()
        if control == "stop":
            session.worker.join(5)
            assert not session.worker.is_alive()
            assert session.status == "stopped"
        else:
            assert action_gate.wait(5)
            time.sleep(.05)
            assert session.status == "paused"
        assert len(calls) == 1 and session.cycles == 0
        np.testing.assert_array_equal(qpos, session.world.data.qpos)
        if control == "pause":
            session.start(single_step=True)
            wait_until(lambda: session.status == "paused" and session.cycles == 1)
            assert len(calls) == 2
    finally:
        release_preview.set()
        session.stop()
        session.worker.join(5)


def test_stop_is_not_overwritten_by_a_pending_stall_verdict():
    session = Session(preview=False, speed=0)
    verdict_ready, release_verdict = threading.Event(), threading.Event()
    original = session._stalled

    def hold(observation, question, options, baseline_choice, history):
        return {"choice": "hold" if "hold" in options else baseline_choice,
                "selected_probability": None, "probabilities": {}, "model_call": False}

    def delayed_verdict():
        result = original()
        if result:
            verdict_ready.set()
            assert release_verdict.wait(5)
        return result

    session.policy.choose = hold
    session._stalled = delayed_verdict
    try:
        session.start()
        assert verdict_ready.wait(5)
        assert session.cycles == 3
        session.stop()
        release_verdict.set()
        session.worker.join(5)
        assert session.status == "stopped" and not session.worker.is_alive()
        assert session.events[-1]["event"] == "stopped"
        assert not any(e["event"] == "stalled" for e in session.events)
    finally:
        release_verdict.set()
        session.stop()
        session.worker.join(5)


def test_stop_is_not_overwritten_when_the_action_budget_is_reached():
    session = Session(preview=False, speed=0, max_cycles=1)
    budget_ready, release_budget = threading.Event(), threading.Event()
    original = session._wait

    def delayed_budget_check():
        ready = original()
        if ready and session.cycles == 1 and session.stage == "observing":
            budget_ready.set()
            assert release_budget.wait(5)
        return ready

    session._wait = delayed_budget_check
    try:
        session.start()
        assert budget_ready.wait(5)
        session.stop()
        release_budget.set()
        session.worker.join(5)
        assert session.status == "stopped" and not session.worker.is_alive()
        assert not any(e["event"] == "budget_exhausted" for e in session.events)
    finally:
        release_budget.set()
        session.stop()
        session.worker.join(5)


@pytest.mark.parametrize("moving_body", ["tcp", "object"])
def test_small_steps_that_accumulate_progress_are_not_stalled(moving_body):
    session = Session(preview=False, speed=0)
    initial = session.world.observe()
    try:
        for index in range(3):
            before, after = copy.deepcopy(initial), copy.deepcopy(initial)
            before[moving_body][0] += index * .0015
            after[moving_body][0] += (index + 1) * .0015
            session.history.append({"phase": "descend", "before": before, "after": after})
        assert not session._stalled()
    finally:
        session.stop()


@pytest.mark.parametrize("outcome", ["uncertain", "error", "stopped"])
def test_unexecuted_decisions_keep_actual_inputs_without_credentials(outcome):
    session = Session(preview=False, speed=0, provider="local", connection={
        "url": "http://127.0.0.1:9123/decide", "model": "recording-test", "key": "input-log-secret"})
    entered, release = threading.Event(), threading.Event()
    requests = []

    def response(url, **kwargs):
        payload = copy.deepcopy(kwargs["json"])
        requests.append(payload)
        entered.set()
        if outcome == "stopped":
            assert release.wait(5)
        if outcome == "error":
            raise ValueError("input-log-secret private-provider-message")
        options = list(payload["questions"]["action"]["criteria"])
        probabilities = {key: .4 if i == 0 else .3 for i, key in enumerate(options)}
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "recording-test", "answers": {"action": {
                "choice": options[0], "probabilities": probabilities}}})

    session.policy._post = response
    try:
        session.start()
        assert entered.wait(5)
        if outcome == "stopped":
            session.stop()
            release.set()
            session.worker.join(5)
        else:
            wait_until(lambda: session.status == outcome)
        exported = session.export()
        assert session.status == outcome and session.cycles == 0
        assert exported["history"] == []
        expected = {"state": requests[0]["state"], "decision": requests[0]["questions"]["action"]}
        assert exported["last_decision_inputs"] == {"phase": None, "action": expected}
        assert session.snapshot()["last_decision_inputs"] == exported["last_decision_inputs"]
        assert "input-log-secret" not in str(exported)
        assert "private-provider-message" not in str(exported)
        if outcome == "uncertain":
            # A new attempt must clear both old slots before waiting for another answer.
            next_entered, next_release = threading.Event(), threading.Event()
            original = session.policy.choose

            def next_choice(*args):
                next_entered.set()
                assert next_release.wait(5)
                return original(*args)

            session.policy.choose = next_choice
            try:
                session.start()
                assert next_entered.wait(5)
                assert session.snapshot()["last_decision_inputs"] == {"phase": None, "action": None}
                # Starting another attempt does not mutate the old exported input.
                assert exported["last_decision_inputs"]["action"] == expected
            finally:
                session.stop()
                next_release.set()
    finally:
        release.set()
        session.stop()
        session.worker.join(5)
