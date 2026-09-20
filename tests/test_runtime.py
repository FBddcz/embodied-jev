import threading
import time

import numpy as np

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
