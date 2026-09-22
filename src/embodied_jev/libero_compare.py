"""Run paired, camera-grounded GPT-only and GPT+Jev LIBERO episodes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time

from .evaluation import Worker, load_manifest, saved_connection
from .libero_policy import (PROTOCOL, ModelClient, RequestBudget, decode_depth, decode_image,
                            local_state, motor_action, reached, usage_summary)

SOURCES = ("libero_compare.py", "libero_policy.py", "libero_worker.py", "benchmark_worker.py",
           "evaluation.py", "evaluation_meter.py", "policies.py", "evidence.py", "connection_store.py")


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def source_hashes():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in SOURCES}


def save_frame(directory, packet, elapsed, *, depth=False):
    step = packet["observation"]["step"]
    paths = {}
    for view, image in packet.get("images", {}).items():
        prefix = f"frames/{step:05d}-{view}"
        rgb = decode_image(image)
        (directory / (prefix + ".png")).write_bytes(rgb)
        paths[view] = {"rgb": prefix + ".png", "sha256": image["sha256"]}
        if depth:
            depth_map = decode_depth(image)
            (directory / (prefix + ".depth.f32")).write_bytes(depth_map.tobytes())
            paths[view].update(depth=prefix + ".depth.f32", depth_sha256=image["depth_sha256"],
                width=image["width"], height=image["height"], intrinsics=image["intrinsics"], camera_to_world=image["camera_to_world"])
    return {"step": step, "wall_seconds": elapsed, "simulation_seconds": step / 20,
            "observation": packet["observation"], "images": paths}


def verify_pair(reference, current):
    for key in ("initial_state_sha256", "settled_state_sha256", "versions", "libero_revision", "case",
                "camera_size", "camera_transform", "controller", "action_output_min", "action_output_max"):
        if reference[key] != current[key]:
            raise ValueError(f"Paired LIBERO setup mismatch: {key}")


def run_episode(args, case, mode, directory, connections, reference=None):
    if getattr(args, "architecture", "waypoint-v1") == "supervisor-v2" and mode != "noop":
        from .libero_supervisor import run_episode as supervised_episode
        return supervised_episode(args, case, mode, directory, connections, reference)
    wall_time = getattr(args, "budget_mode", "bounded") == "wall-time"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "frames").mkdir()
    (directory / "inputs").mkdir()
    started = time.monotonic()
    worker = Worker(args.worker_python, directory / "worker.log", script=Path(__file__).with_name("libero_worker.py"))
    row = {"id": case["id"], "case": case, "mode": mode, "protocol": PROTOCOL,
           "success": None, "status": "setup_error", "steps": 0, "decisions": [], "frames": [], "api_calls": []}
    budget, clients = None, []
    stream = (directory / "events.jsonl").open("w")
    def event(kind, **values):
        stream.write(json.dumps({"kind": kind, **values}, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
    try:
        packet = worker.request({"command": "reset", "case": case, "libero_root": args.libero_root,
            "config_dir": str(directory / "libero-config"), "horizon": 1_000_000_000 if wall_time else args.max_steps,
            "camera_size": args.camera_size, "settle_steps": 10}, timeout=240)
        row["metadata"] = packet["metadata"]
        if reference:
            verify_pair(reference, packet["metadata"])
        if packet["metadata"]["initial_success"]:
            raise ValueError("Task is already successful after reset; choose another initial state")
        row["setup_seconds"] = time.monotonic() - started
        rollout_start = time.monotonic()
        budget = RequestBudget(None if wall_time else args.max_calls, args.timeout, args.max_usd)
        if mode != "noop":
            planner = ModelClient("chat", connections["chat"], budget)
            motor_provider = "chat" if mode == "gpt6" else "jev"
            motor = ModelClient(motor_provider, connections[motor_provider], budget)
            clients = [planner, motor]
        row["success"], row["status"] = False, "step_budget"
        row["frames"].append(save_frame(directory, packet, 0, depth=True))
        event("reset", metadata=row["metadata"], frame=row["frames"][-1])
        plan, plan_age, finger, recent, stalled = None, 0, -1., [], 0
        while wall_time or row["steps"] < args.max_steps:
            budget.check()
            observation = packet["observation"]
            decision = {"index": len(row["decisions"]), "step": row["steps"],
                        "start_seconds": time.monotonic() - rollout_start}
            if mode == "noop":
                action = [0., 0., 0., 0., 0., 0., -1.]
                decision.update(stage="installation check", choices={}, probabilities={}, action=action)
            else:
                state = local_state(observation, plan, finger, recent) if plan else None
                refresh = plan is None or plan_age >= plan["max_motor_steps"] or reached(state) or stalled >= 3
                if refresh:
                    event("request_start", stage="vision_plan", step=row["steps"], wall_seconds=time.monotonic()-rollout_start)
                    plan, annotated = planner.plan(observation, packet["images"], plan, recent)
                    for view, image in annotated.items():
                        (directory / f"inputs/{row['steps']:05d}-{view}.png").write_bytes(image)
                    # Save the exact depth/calibration from the observation used to ground this target.
                    row["frames"][-1] = save_frame(directory, packet, row["frames"][-1]["wall_seconds"], depth=True)
                    plan_age, stalled = 0, 0
                    decision["new_plan"] = plan
                    event("plan", step=row["steps"], wall_seconds=time.monotonic()-rollout_start, plan=plan,
                          input_images={view: {"path": f"inputs/{row['steps']:05d}-{view}.png", "sha256": hashlib.sha256(img).hexdigest()}
                                        for view, img in annotated.items()})
                state = local_state(observation, plan, finger, recent)
                event("request_start", stage="local_control", step=row["steps"], wall_seconds=time.monotonic()-rollout_start)
                choices, probabilities = motor.motor(state)
                # Budget/clock are checked after inference, before any new physical action.
                if time.monotonic() >= budget.deadline:
                    raise RuntimeError("time_budget")
                action = motor_action(choices, state, repeat=args.action_repeat, scale=args.action_scale)
                decision.update(stage=plan["stage"], plan=plan, state=state, choices=choices,
                                probabilities=probabilities, action=action)
            decision["inference_end_seconds"] = time.monotonic() - rollout_start
            row["decisions"].append(decision)
            event("decision", **decision)
            before = observation
            for _ in range(args.action_repeat if wall_time else min(args.action_repeat, args.max_steps-row["steps"])):
                if time.monotonic() >= budget.deadline:
                    raise RuntimeError("time_budget")
                packet = worker.request({"command": "step", "action": action, "capture": True})
                row["steps"] = packet["observation"]["step"]
                frame = save_frame(directory, packet, time.monotonic()-rollout_start)
                row["frames"].append(frame)
                event("frame", **frame)
                if packet["success"] or packet["truncated"]:
                    row["success"] = packet["success"]
                    row["status"] = "success" if packet["success"] else "step_budget"
                    break
            decision["end_seconds"] = time.monotonic() - rollout_start
            decision["end_step"] = row["steps"]
            finger = action[-1]
            movement = math.dist(before["tcp"], packet["observation"]["tcp"])
            recent.append({"before_tcp": before["tcp"], "after_tcp": packet["observation"]["tcp"],
                           "delta_m": [b-a for a, b in zip(before["tcp"], packet["observation"]["tcp"])],
                           "gripper_qpos": packet["observation"]["gripper_qpos"], "action": action})
            stalled = stalled + 1 if movement < .001 else 0
            plan_age += 1
            row["api_calls"] = budget.calls
            write_json(directory / "episode.json", row)
            print(json.dumps({"case": case["id"], "mode": mode, "step": row["steps"],
                              "stage": decision["stage"], "requests": len(budget.calls),
                              "success": row["success"]}, ensure_ascii=False), flush=True)
            if row["success"] or packet["truncated"]:
                break
        row["rollout_seconds"] = time.monotonic()-rollout_start
    except KeyboardInterrupt:
        row["status"] = "interrupted"
    except Exception as exc:
        row["status"] = str(exc) if str(exc) in {"time_budget", "request_budget", "cost_budget"} else ("setup_error" if row["success"] is None else "runtime_error")
        # Never copy exception URLs/headers/body to public reports.
        row["error_type"] = type(exc).__name__
        if isinstance(exc, ValueError):
            row["validation_error"] = str(exc)[:300]
            row["last_answers"] = [client.last_answer for client in clients]
        print(json.dumps({"case": case["id"], "mode": mode, "status": row["status"], "error_type": row["error_type"]}), flush=True)
        if row["status"] == "setup_error":
            (directory / "setup-error.txt").write_text(str(exc)[:2000])
    finally:
        if budget:
            row.setdefault("rollout_seconds", time.monotonic()-budget.started)
            if row["decisions"] and "end_step" not in row["decisions"][-1]:
                row["decisions"][-1].update(end_step=row["steps"], end_seconds=row["rollout_seconds"], interrupted_action=True)
        for client in clients:
            client.close()
        worker.close()
        stream.close()
        row["wall_seconds"] = time.monotonic()-started
        if budget:
            row["api_calls"] = budget.calls
        row["metrics"] = usage_summary(row["api_calls"])
        write_json(directory / "episode.json", row)
    return row


def run(args):
    manifest = load_manifest(args.manifest)
    if manifest["backend"] != "libero":
        raise ValueError("This experiment requires a LIBERO manifest")
    if len(set(args.modes)) != len(args.modes) or "noop" in args.modes and len(args.modes) != 1:
        raise ValueError("Modes must be unique; noop installation checks run separately")
    if not (0 < args.max_steps <= 1000 and 0 < args.max_calls <= 500 and 0 < args.timeout <= 7200
            and 1 <= args.action_repeat <= 10 and 0 < args.action_scale <= .5 and 0 < args.max_usd <= 50):
        raise ValueError("Invalid experiment budget or action bounds")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    frozen = source_hashes()
    architecture = getattr(args, "architecture", "waypoint-v1")
    version = PROTOCOL
    if architecture == "supervisor-v2":
        from .libero_supervisor import PROTOCOL as version
        frozen["libero_supervisor.py"] = hashlib.sha256(Path(__file__).with_name("libero_supervisor.py").read_bytes()).hexdigest()
    (output / "reproduction").mkdir()
    for name in frozen:
        shutil.copy2(Path(__file__).with_name(name), output / "reproduction" / name)
    protocol = {"version": version, "architecture": architecture, "created_at": datetime.now(timezone.utc).isoformat(),
                "manifest": manifest, "modes": args.modes, "source_sha256": frozen,
                "budget": {name: getattr(args, name) for name in ("max_steps", "max_calls", "timeout", "max_usd", "action_repeat", "action_scale", "camera_size")},
                "pricing": {"jev_input_per_million": .042, "jev_output_per_million": 0,
                    "gpt6_astra_input_per_million": 10, "gpt6_astra_output_per_million": 50,
                    "sources": ["https://docs.typesafe.ai/models", "https://developers.openai.com/api/docs/models/gpt-6-astra"],
                    "note": "Uncached public-rate estimate; proxy invoices and cache discounts may differ. Unknown usage is not zero."},
                "design": "Both modes use the same GPT visual planner, RGB-D grounding and checkpoint rules. GPT-only uses GPT local control; hybrid uses Jev local control. Custom development subset, not a full LIBERO score."}
    protocol["budget"]["mode"] = getattr(args, "budget_mode", "bounded")
    if architecture == "supervisor-v2":
        protocol["design"] = ("Both modes use temporal BEFORE/NOW dual-camera GPT candidate generation (2-3 candidates), "
            "then GPT or Jev selects one candidate with normal/cautious speed or reobserve. "
            "Both execute identical deterministic numeric servo for at most four blocks per checkpoint. "
            "No object ground truth, force/contact truth or future simulation results enter models. "
            "New architecture: not a single-factor comparison with waypoint-v1.")
        protocol["candidate_control"] = {"max_blocks": 4, "cautious_magnitude_multiplier": .4,
            "stall_blocks": 2, "stall_movement_m": .001, "observation": "previous and current external/wrist RGB + calibrated depth grounding"}
    if protocol["budget"]["mode"] == "wall-time":
        protocol["budget"].update(max_steps=None, max_calls=None,
            stopping_rule="official success or rollout wall deadline; cost admission guard remains active")
    write_json(output / "protocol.json", protocol)
    connections = {}
    if any(mode != "noop" for mode in args.modes):
        connections["chat"] = saved_connection("chat")
        if not connections["chat"]["model"].startswith("gpt-6-astra"):
            raise ValueError("Saved chat connection must select GPT-6 Astra for this comparison")
    if "gpt6-jev" in args.modes:
        connections["jev"] = saved_connection("jev")
    rows = []
    report = {"protocol": protocol, "episodes": rows, "complete": False}
    for case in manifest["cases"]:
        reference = None
        for mode in args.modes:
            if any(hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != digest for name, digest in frozen.items()):
                raise ValueError("Source changed after protocol freeze; start a fresh experiment")
            row = run_episode(args, case, mode, output / mode / case["id"], connections, reference)
            if reference is None and "metadata" in row:
                reference = row["metadata"]
            rows.append({**{key: value for key, value in row.items() if key not in {"frames", "decisions", "api_calls"}},
                         "episode_path": f"{mode}/{case['id']}/episode.json"})
            write_json(output / "summary.json", report)
            if row["status"] in {"setup_error", "runtime_error", "interrupted"}:
                return report
    report["complete"] = True
    report["sources_unchanged"] = all(hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() == digest for name, digest in frozen.items())
    write_json(output / "summary.json", report)
    return report


def add_arguments(parser):
    parser.add_argument("--architecture", choices=["waypoint-v1", "supervisor-v2"], default="waypoint-v1",
                        help="supervisor-v2 uses temporal visual candidates, one semantic selection and code-owned servo")
    parser.add_argument("--budget-mode", choices=["bounded", "wall-time"], default="bounded",
                        help="wall-time stops at success/deadline without step or request caps; keeps the cost guard")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--worker-python", required=True)
    parser.add_argument("--libero-root", required=True)
    parser.add_argument("--output", required=True, help="New directory; existing results are never mixed")
    parser.add_argument("--modes", nargs="+", choices=["gpt6", "gpt6-jev", "noop"], default=["gpt6", "gpt6-jev"])
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-calls", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--max-usd", type=float, default=5)
    parser.add_argument("--action-repeat", type=int, default=5)
    parser.add_argument("--action-scale", type=float, default=.5)
    parser.add_argument("--camera-size", type=int, default=384)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args()
    try:
        result = run(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
