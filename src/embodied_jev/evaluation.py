"""Reproducible evaluation with explicit task lists and isolated simulators."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
import math
import os
import platform
from pathlib import Path
import queue
import re
import statistics
import subprocess
import threading
import time


def load_manifest(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or value.get("format") != "embodied-jev-suite-v1":
        raise ValueError("Expected embodied-jev-suite-v1 manifest")
    if value.get("backend") not in {"builtin", "metaworld", "libero"}:
        raise ValueError("Unknown benchmark backend")
    if value.get("split") not in {"smoke", "development", "test"}:
        raise ValueError("Declare split: smoke, development or test")
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifest needs a nonempty cases list")
    seen, trials = set(), set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", case["id"])):
            raise ValueError("Each case needs a safe unique id")
        fields = {"id", "suite", "task_id", "init_index", "seed"} if value["backend"] == "libero" else {"id", "task", "seed"}
        if set(case) != fields:
            raise ValueError("Case fields must match the backend schema; unsupported settings are not ignored")
        if case["id"] in seen or type(case.get("seed")) is not int or not 0 <= case["seed"] <= 99999:
            raise ValueError("Duplicate case id or invalid seed")
        if value["backend"] == "libero":
            if (case.get("suite") not in {"libero_spatial", "libero_object", "libero_goal", "libero_10", "libero_90"}
                    or any(type(case.get(k)) is not int or case[k] < 0 for k in ("task_id", "init_index"))):
                raise ValueError("LIBERO requires a suite, task_id and init_index")
            trial = (case["suite"], case["task_id"], case["init_index"], case["seed"])
        else:
            task = case.get("task", "")
            if (not isinstance(task, str)
                    or value["backend"] == "builtin" and task not in {"transfer", "stack", "barrier"}
                    or value["backend"] == "metaworld" and not re.fullmatch(r"[a-z-]+-v3", task)):
                raise ValueError("Invalid task for this backend")
            trial = (task, case["seed"])
        if trial in trials:
            raise ValueError("Duplicate trial would inflate the sample size")
        trials.add(trial)
        seen.add(case["id"])
    return value


def wilson(successes, count):
    if not count:
        return None
    z, p = 1.959963984540054, successes / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return [max(0, center - radius), min(1, center + radius)]


def aggregate(rows, planned):
    scored = [r for r in rows if r.get("success") is not None]
    wins = sum(r["success"] for r in scored)
    groups = {}
    for row in scored:
        groups.setdefault(row["task"], []).append(row)
    latencies = [x for r in rows for x in r.get("model_latency_ms", [])]
    requests = [call for r in rows for call in r.get("api_calls", [])]
    from .evaluation_meter import token_summary
    usage = token_summary(requests, sum(r.get("model_calls", 0) for r in rows))
    failures = {}
    for row in rows:
        if not row.get("success"):
            failures[row["status"]] = failures.get(row["status"], 0) + 1
    return {"planned": planned, "recorded": len(rows), "scored": len(scored),
            "unscored": len(rows) - len(scored), "missing": planned - len(rows),
            "complete": len(rows) == planned and len(scored) == planned
                        and not any(r["status"] in {"setup_error", "runtime_error"} for r in rows),
            "successes": wins, "success_rate": wins / len(scored) if scored else None,
            "wilson_95": wilson(wins, len(scored)),
            "task_macro_success_rate": statistics.mean(sum(r["success"] for r in rs) / len(rs)
                                                        for rs in groups.values()) if groups else None,
            "per_task": {name: {"n": len(rs), "successes": sum(r["success"] for r in rs),
                                  "wilson_95": wilson(sum(r["success"] for r in rs), len(rs))}
                         for name, rs in groups.items()},
            "model_calls": sum(r.get("model_calls", 0) for r in rows),
            "http_requests": len(requests), "token_usage": usage,
            "input_tokens": usage["input_reported_tokens"] if usage["input_complete"] else None,
            "output_tokens": usage["output_reported_tokens"] if usage["output_complete"] else None,
            "wall_seconds": sum(r.get("wall_seconds", 0) for r in rows),
            "environment_steps": sum(r.get("steps", 0) for r in rows),
            "latency_median_ms": statistics.median(latencies) if latencies else None,
            "failures": failures,
            "interval_note": "Binomial Wilson interval over scored episodes; not evidence of unseen-task generalization."}


class Worker:
    def __init__(self, python, log, *, script=None):
        self.log = Path(log).open("w")
        env = {k: v for k, v in os.environ.items()
               if not any(word in k.upper() for word in ("KEY", "TOKEN", "PASSWORD", "SECRET"))}
        try:
            self.process = subprocess.Popen([python, str(script or Path(__file__).with_name("benchmark_worker.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, env=env)
        except Exception:
            self.log.close()
            raise
        self.messages = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.messages.put(line)
            self.messages.put(None)
        threading.Thread(target=read, daemon=True).start()

    def request(self, message, timeout=60):
        self.process.stdin.write(json.dumps(message, allow_nan=False) + "\n")
        self.process.stdin.flush()
        try:
            line = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("Benchmark worker timed out; see worker log") from None
        if line is None:
            raise RuntimeError("Benchmark worker exited; see worker log")
        result = json.loads(line)
        if not result.get("ok"):
            raise RuntimeError(f"Benchmark worker {result.get('error')}: {result.get('message')}")
        return result

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"command":"close"}\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()
        self.log.close()


def motor_questions():
    questions = {}
    for axis in "xyz":
        questions[axis] = {"type": "choice", "instructions":
            f"Choose world {axis.upper()} motion for the task from the current observation and recent outcomes. "
            "Replan each step. No fixed manipulation sequence is assumed. Other channels are chosen concurrently.",
            "criteria": {"negative": f"Move toward -{axis}.", "hold": f"Hold {axis}.", "positive": f"Move toward +{axis}."}}
    questions["gripper"] = {"type": "choice", "instructions": "Choose the finger command for the current task and observation.",
                             "criteria": {"open": "Open fingers.", "hold": "Retain previous finger command.", "close": "Close fingers."}}
    return questions


def saved_connection(provider):
    from .connection_store import SystemConnectionStore
    connection = SystemConnectionStore().load()["connections"].get(provider)
    if not connection or not connection.get("key"):
        raise ValueError(f"Saved {provider} connection is unavailable; open the app connection settings")
    return connection


def external_episode(manifest, case, args, output):
    from .evaluation_meter import make_policy, token_summary
    worker, policy = None, None
    row = {"id": case["id"], "task": case.get("task", f"{case.get('suite')}/{case.get('task_id')}"),
           "seed": case["seed"], "success": None, "status": "setup_error", "steps": 0,
           "model_calls": 0, "model_latency_ms": [], "worker_log": f"{case['id']}-worker.log"}
    start, finger, history = time.monotonic(), -1., []
    rollout_start = None
    repeat = getattr(args, "action_repeat", 1)
    control_mode = getattr(args, "control_mode", "skills")
    hierarchical = control_mode == "hierarchical"
    try:
        worker = Worker(args.worker_python, output / f"{case['id']}-worker.log")
        reset = worker.request({"command": "reset", "backend": manifest["backend"], "case": case,
                                "horizon": args.max_steps, "observation_mode": args.observation_mode,
                                "control_mode": control_mode}, timeout=args.timeout)
        observation = reset["observation"]
        initial_observation = observation
        row["setup_seconds"] = round(time.monotonic() - start, 3)
        rollout_start = time.monotonic()
        row.update(metadata=reset["metadata"], success=False, status="step_limit",
                   initial_observation_sha256=hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest())
        if args.policy not in {"noop", "scripted"}:
            connection = getattr(args, "_connection", None)
            policy = make_policy(args.policy, connection=connection)
        with (output / f"{case['id']}-trace.jsonl").open("x") as trace:
            while row["steps"] < args.max_steps:
                if time.monotonic() - start >= args.timeout:
                    row["status"] = "timeout"
                    break
                decisions = None
                subgoal = None
                hierarchy_trace = {}
                action = [0., 0., 0., finger]
                if policy:
                    if policy.calls + (2 if hierarchical else 1) > args.max_calls:
                        row["status"] = "call_budget"
                        break
                    scales = [args.action_scale] * 3
                    if hierarchical:
                        from . import benchmark_hierarchy as hierarchy
                        state, options = hierarchy.prepare(observation, initial_observation, history)
                        subgoal = policy.choose_plan(state, hierarchy.QUESTION,
                                                     {name: spec["description"] for name, spec in options.items()})
                        hierarchy_trace = {"subgoal": subgoal, "subgoal_input": policy.last_input}
                        if subgoal["selected_probability"] is not None and subgoal["selected_probability"] < args.threshold:
                            row["status"] = "uncertain"
                            trace.write(json.dumps({"step": row["steps"], "observation": observation,
                                                    **hierarchy_trace, "executed": False}) + "\n")
                            break
                        # The second request must not start after a late subgoal reply.
                        if time.monotonic() - start >= args.timeout:
                            row["status"] = "timeout"
                            trace.write(json.dumps({"step": row["steps"], "observation": observation,
                                                    **hierarchy_trace, "executed": False}) + "\n")
                            break
                        motor, questions, scales = hierarchy.motor_input(state, options, subgoal["choice"],
                                                                        observation, args.action_scale, repeat)
                        decisions = policy.choose_channels(motor, questions)
                        hierarchy_trace["motor_input"] = policy.last_input
                    else:
                        decisions = policy.choose_channels({"observation": observation, "recent_actions": history[-4:],
                            "action_contract": {"translation": "normalized Cartesian controller input, not absolute XYZ or metres",
                                                "amplitude": args.action_scale, "repeat_env_steps": repeat,
                                                "rotation": "fixed; not model-controlled"}}, motor_questions())
                    if any(d["selected_probability"] is not None and d["selected_probability"] < args.threshold for d in decisions.values()):
                        row["status"] = "uncertain"
                        trace.write(json.dumps({"step": row["steps"], "observation": observation, "decisions": decisions,
                                                **hierarchy_trace, "executed": False}) + "\n")
                        break
                    finger = {"open": -1., "close": 1., "hold": finger}[decisions["gripper"]["choice"]]
                    action = [{"negative": -scale, "hold": 0., "positive": scale}
                              [decisions[a]["choice"]] for a, scale in zip("xyz", scales)] + [finger]
                if manifest["backend"] == "libero":
                    action = action[:3] + [0., 0., 0.] + action[3:]
                # Never execute a late model answer after the wall-time budget.
                remaining = args.timeout - (time.monotonic() - start)
                if remaining <= 0:
                    row["status"] = "timeout"
                    break
                finished = False
                for offset in range(min(repeat, args.max_steps - row["steps"])):
                    remaining = args.timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        row["status"], finished = "timeout", True
                        break
                    result = worker.request({"command": "step", "action": action, "scripted": args.policy == "scripted"},
                                            timeout=min(remaining, 60))
                    trace.write(json.dumps({"step": row["steps"], "observation": observation,
                                            "decisions": decisions if offset == 0 else None,
                                            **(hierarchy_trace if offset == 0 else {}),
                                            "decision_index": len(history), "chunk_offset": offset,
                                            "executed": True, "action": result["action"], "after": result["observation"],
                                            "evaluation": {k: result[k] for k in ("success", "terminated", "truncated")}}) + "\n")
                    trace.flush()
                    observation = result["observation"]
                    row["steps"] += 1
                    if result["success"]:
                        row.update(success=True, status="success")
                        finished = True
                        break
                    if result["terminated"] or result["truncated"]:
                        row["status"] = "terminated" if result["terminated"] else "step_limit"
                        finished = True
                        break
                history.append({"action": action, **({"subgoal": subgoal["choice"]} if subgoal else {})})
                if finished:
                    break
    except Exception as exc:
        row.update(status="setup_error" if row["success"] is None else "runtime_error",
                   error_type=type(exc).__name__)
        response = getattr(exc, "response", None)
        if response is not None and isinstance(getattr(response, "status_code", None), int):
            row["http_status"] = response.status_code
        # Provider response bodies and credentials are never put in reports.
    finally:
        if rollout_start is not None:
            row["rollout_seconds"] = round(time.monotonic() - rollout_start, 3)
        if policy:
            usage = token_summary(policy.api_calls, policy.calls)
            row.update(model=policy.model, model_calls=policy.calls, model_latency_ms=policy.latencies,
                       api_calls=policy.api_calls, token_usage=usage,
                       input_tokens=usage["input_reported_tokens"] if usage["input_complete"] else None,
                       output_tokens=usage["output_reported_tokens"] if usage["output_complete"] else None)
            policy.close()
        if worker:
            worker.close()
        row["wall_seconds"] = round(time.monotonic() - start, 3)
        hz = row.get("metadata", {}).get("action_spec", {}).get("control_hz")
        if isinstance(hz, (int, float)) and hz > 0:
            row["simulated_seconds"] = row["steps"] / hz
    return row


def builtin_episode(case, args, output):
    from .runtime import run_headless
    session = run_headless(case["task"], case["seed"], provider=args.policy,
                           max_cycles=min(args.max_steps, args.max_calls // 2) if args.policy != "baseline" else args.max_steps,
                           threshold=args.threshold, timeout=args.timeout, control_mode=args.control_mode,
                           observation_mode=args.observation_mode, connection=getattr(args, "_connection", None))
    exported = session.export()
    (output / f"{case['id']}-episode.json").write_text(json.dumps(exported, ensure_ascii=False))
    if session.worker.is_alive():
        raise TimeoutError("An admitted model call is still stopping; no further episode is started")
    return {"id": case["id"], "task": case["task"], "seed": case["seed"],
            "success": exported["success"], "status": "success" if exported["success"] else exported["status"],
            "steps": session.cycles,
            "cycles": session.cycles, "scene_hash": exported["scene_hash"],
            "forbidden_contact_steps": session.world.unsafe_contacts, "max_lift_m": session.world.max_lift,
            **{k: exported[k] for k in ("model", "model_calls", "input_tokens", "output_tokens", "model_latency_ms", "wall_seconds")}}


def run(args, *, connection=None):
    manifest = load_manifest(args.manifest)
    if args.max_steps < 1 or args.max_calls < 2 or not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("Positive step/time budgets and at least two API calls are required")
    if not 0 < args.action_scale <= 1 or not 0 <= args.threshold <= 1:
        raise ValueError("Invalid action scale or probability threshold")
    if type(getattr(args, "action_repeat", 1)) is not int or not 1 <= getattr(args, "action_repeat", 1) <= 20:
        raise ValueError("action-repeat must be between 1 and 20")
    backend = manifest["backend"]
    if backend != "builtin" and (not args.worker_python or args.policy == "baseline"):
        raise ValueError("External benchmarks need --worker-python and --policy noop, scripted or a model")
    if backend == "builtin" and args.policy in {"scripted", "noop"} or backend == "libero" and args.policy == "scripted":
        raise ValueError("This comparator is not available for this backend")
    if backend != "builtin" and args.observation_mode != "privileged":
        raise ValueError("External adapter currently supports privileged state only; images are not inferred")
    if backend != "builtin" and args.control_mode != "skills":
        if backend != "metaworld" or args.control_mode != "hierarchical" or args.policy not in {"jev", "chat", "claude"}:
            raise ValueError("External hierarchy supports Meta-World model policies only")
        from .benchmark_hierarchy import TASKS
        if any(case["task"] not in TASKS for case in manifest["cases"]):
            raise ValueError("No validated hierarchy for a requested Meta-World task")
    if backend == "builtin" and getattr(args, "action_repeat", 1) != 1:
        raise ValueError("action-repeat applies only to external benchmarks")
    if getattr(args, "connection_source", "environment") == "saved" and args.policy not in {"baseline", "noop", "scripted"}:
        args._connection = dict(connection) if connection is not None else saved_connection(args.policy)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    frozen = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    source = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
              for name in ("evaluation.py", "evaluation_meter.py", "benchmark_worker.py", "benchmark_hierarchy.py", "policies.py", "hierarchical.py", "physics.py")}
    report = {"format": "embodied-jev-evaluation-v1", "manifest": manifest,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "host_runtime": {"python": platform.python_version(), "os": platform.system(), "architecture": platform.machine()},
              "manifest_sha256": hashlib.sha256(frozen.encode()).hexdigest(), "source_sha256": source,
              "configuration": {k: getattr(args, k) for k in ("policy", "max_steps", "max_calls", "timeout",
                  "threshold", "action_scale", "control_mode", "observation_mode")},
              "scope": "Custom frozen task list, not a complete official benchmark score", "episodes": []}
    if backend != "builtin":
        report["configuration"]["control_mode"] = ("official_scripted" if args.policy == "scripted" else
            "hierarchical_xyz_gripper" if args.control_mode == "hierarchical" else "normalized_xyz_gripper")
    report["configuration"]["action_repeat"] = getattr(args, "action_repeat", 1)
    report["configuration"]["connection_source"] = getattr(args, "connection_source", "environment")
    if getattr(args, "_connection", None):
        report["configuration"]["configured_model"] = args._connection["model"]
    def save():
        report["aggregate"] = aggregate(report["episodes"], len(manifest["cases"]))
        temp = output / "summary.tmp"
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        temp.replace(output / "summary.json")
    save()
    for case in manifest["cases"]:
        try:
            row = builtin_episode(case, args, output) if backend == "builtin" else external_episode(manifest, case, args, output)
        except Exception as exc:
            row = {"id": case["id"], "task": case.get("task"), "success": None,
                   "status": "setup_error", "error_type": type(exc).__name__}
        report["episodes"].append(row)
        save()
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if row["status"] in {"setup_error", "runtime_error"}:
            break  # Preserve incomplete coverage; do not repeat or silently skip errors.
    return report
