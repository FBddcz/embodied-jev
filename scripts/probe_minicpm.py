"""Bounded real-weight diagnostics: fixed-state order probes plus closed-loop trials.

Run after installing .[minicpm] and enabling EMBODIED_MINICPM=1. This performs
real inference and can download weights; it is deliberately outside pytest.
"""
import argparse
import json
import platform
import time
from pathlib import Path

from embodied_jev.physics import RobotWorld
from embodied_jev.planning import baseline_phase, candidates, eligible_phases, phase_options
from embodied_jev.policies import DecisionPolicy, minicpm_status
from embodied_jev.runtime import POLICY_VERSION, run_headless


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/minicpm-probes.json"))
    parser.add_argument("--max-cycles", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not 1 <= args.max_cycles <= 100 or args.timeout <= 0:
        parser.error("Use 1–100 cycles and a positive timeout")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    policy = DecisionPolicy("minicpm")
    started = time.monotonic()
    report = {"policy_version": POLICY_VERSION, "platform": platform.platform(),
              "note": "Probe states come from the rule baseline; fixed-state choices are not model-controlled task successes.",
              "probes": [], "episodes": []}

    def save():
        report["elapsed_seconds"] = time.monotonic() - started
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    try:
        report["warmup"] = policy.choose({"purpose": "Ready check"}, "Choose ready.",
                                          {"ready": "Ready", "hold": "Hold"}, "ready", [])
        report["model_runtime"] = minicpm_status()
        world, history = RobotWorld(), []
        for step in range(8):
            observation, phases = world.observe(), eligible_phases(world)
            reference = baseline_phase(world)
            if len(phases) > 1:
                for order in (phases, list(reversed(phases))):
                    tokens = policy.tokens
                    answer = policy.choose(observation,
                        "Choose the next phase that makes progress toward the goal, given the measured geometry and contacts. "
                        "Avoid repeating a motion that has already reached its target.",
                        phase_options(world, order), reference, history)
                    row = {"step": step, "reference_phase": reference, "order": order,
                           "decision": answer, "input_tokens": policy.tokens - tokens, "input": policy.last_input}
                    report["probes"].append(row)
                    print(json.dumps({key: value for key, value in row.items() if key != "input"}), flush=True)
            action = candidates(world, reference)[0]
            for _ in world.motion(action.target, action.gripper, action.seconds, emit=False):
                pass
            history.append({"phase": reference, "action": action.serialise(),
                            "before": observation, "after": world.observe()})
            save()
    finally:
        policy.close()

    for task in ("transfer", "stack", "barrier"):
        session = run_headless(task, 0, provider="minicpm", threshold=0,
                               max_cycles=args.max_cycles, timeout=args.timeout)
        episode = session.export()
        path = args.output.with_name(f"{args.output.stem}-{task}-0.json")
        path.write_text(json.dumps(episode, ensure_ascii=False, indent=2) + "\n")
        row = {key: episode[key] for key in ("task", "seed", "status", "success", "model_calls",
                "input_tokens", "model_latency_ms", "wall_seconds", "policy_version")}
        row.update(cycles=session.cycles, max_lift_m=session.world.max_lift,
                   forbidden_contact_steps=session.world.unsafe_contacts, episode_file=path.name)
        report["episodes"].append(row)
        save()
        print(json.dumps(row), flush=True)
        if session.worker.is_alive():
            raise RuntimeError("Inference still stopping; remaining trials cancelled")


if __name__ == "__main__":
    main()
