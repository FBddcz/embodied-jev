"""Temporal visual candidates, semantic selection, and a code-owned short servo.

No simulator object state or task predicates enter either model. This is a new
system protocol, deliberately separate from the published waypoint-v1 runs.
"""
from __future__ import annotations

import hashlib
import copy
import json
import math
from pathlib import Path
import time

from .evaluation import Worker
from .libero_policy import (AXES, ModelClient, PLANNER_SYSTEM, RequestBudget,
    annotated_image, local_state, motor_action, reached, resolve_plan, usage_summary)
from .policies import validate_answer

PROTOCOL = "libero-rgbd-candidate-supervisor-v2"
PROGRESS = {"initial", "advancing", "unchanged", "regressing", "unknown"}
CONTACT = {"visible_touch", "possible_touch", "no_visible_touch", "unknown"}
PLAN_FIELDS = {"stage", "intent", "visual_evidence", "target", "rotation_delta", "gripper", "max_motor_steps"}
SYSTEM = PLANNER_SYSTEM.split("Return only JSON")[0] + """
Your role is to propose 2 or 3 DISTINCT short executable alternatives. A separate
selector chooses one; code follows it with numeric servo. Each candidate lasts
at most 4 blocks of 5 environment steps. Do not assume motion of the TCP means
motion of the task object. Compare BEFORE and NOW cameras at every checkpoint.
Report progress of the OBJECT toward the language task, even when the arm moved.
During approach/reposition, object stillness is expected; check whether the hand
reached the intended contact pose. Judge manipulation by object progress.
The previous plan is intention, not observation; never copy its contact claim.
Compare the SAME camera before and now. Wrist-camera motion alone is not object
motion, because that camera moves with the robot. Compare against static scenery.
Unknown visibility/comparison remains unknown, not unchanged or successful.
Apparent image overlap is not proof of contact. No force/contact sensor is supplied.
When repeated pushing has no visible object effect, offer a different contact
location or approach, not another waypoint beyond the same ineffective contact.
Reason from the visible moving surface and joint motion: a hinged door traces an
arc; one long straight push may lose contact. Check both cameras for occlusion.
Do not reuse imagined contact from the previous plan. Unknown remains unknown.
Include an alternative for reacquiring contact, changing orientation or observing
from a better position when appropriate. Candidates must differ physically,
not just in wording. Do not use task names to invent coordinates or hidden state.
Use measured pixel depth for initial contact locations; offsets are world metres.
Only choose pixels from NOW images; BEFORE pixels are comparison evidence only.
Prefer small contact moves and visible geometry. All target schemas from the
waypoint interface are allowed: pixel with camera/pixel/offset_m; relative with
delta_m; world with xyz_m. Rotation is world axis-angle relative to current wrist.
Return JSON with exactly these fields:
{"assessment":{"progress":"initial|advancing|unchanged|regressing|unknown",
 "target_visibility":"clear|partial|occluded", "contact":"visible_touch|possible_touch|no_visible_touch|unknown",
 "evidence":"brief visible comparison, distinguishing object and robot motion"},
 "candidates":[{"id":"c0", "kind":"advance|reposition|observe", "expected_effect":"brief effect to check in the next view",
 "when_use":"evidence under which this alternative is useful",
 "plan":{"stage":"short stage", "intent":"short public action description", "visual_evidence":"visible support",
 "target":{"kind":"pixel","camera":"external","pixel":[u,v],"offset_m":[dx,dy,dz]},
 "rotation_delta":[0,0,0],"gripper":"open|close|hold","max_motor_steps":4}}]}
Use unique IDs c0,c1,c2, 2..3 candidates; do not treat their order as a ranking.
max_motor_steps must be 1..4. Each translation/offset axis is bounded to +/-0.20m;
For manipulation contact, prefer 1..2 blocks before rechecking the moving surface.
each rotation axis to +/-0.5rad. Do not declare task success; environment checks it.
"""


def short_text(value, name, maximum=600):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("Invalid candidate text: " + name)
    return value


def resolve_candidates(answer, observation, images):
    if not isinstance(answer, dict) or set(answer) != {"assessment", "candidates"}:
        raise ValueError("Expected assessment and candidates")
    assessment = answer["assessment"]
    if (not isinstance(assessment, dict) or set(assessment) != {"progress", "target_visibility", "contact", "evidence"}
            or assessment["progress"] not in PROGRESS or assessment["target_visibility"] not in {"clear", "partial", "occluded"}
            or assessment["contact"] not in CONTACT):
        raise ValueError("Invalid visual assessment")
    short_text(assessment["evidence"], "evidence")
    candidates = answer["candidates"]
    if not isinstance(candidates, list) or not 2 <= len(candidates) <= 3:
        raise ValueError("Expected two or three visual alternatives")
    ids, resolved = set(), []
    for candidate in candidates:
        if (not isinstance(candidate, dict) or set(candidate) != {"id", "kind", "expected_effect", "when_use", "plan"}
                or candidate["id"] not in {"c0", "c1", "c2"} or candidate["id"] in ids
                or candidate["kind"] not in {"advance", "reposition", "observe"}):
            raise ValueError("Invalid or duplicate candidate")
        ids.add(candidate["id"])
        short_text(candidate["expected_effect"], "expected_effect")
        short_text(candidate["when_use"], "when_use")
        plan = resolve_plan(candidate["plan"], observation, images)
        if plan["max_motor_steps"] > 4:
            raise ValueError("Candidate exceeds four short blocks")
        resolved.append({**candidate, "plan": plan})
    return {"assessment": {**assessment, "source": "GPT visual estimate; no measured contact force",
                           "observation_step": observation["step"]}, "candidates": resolved}


def servo_choices(state):
    errors = state["position_error_m"] + state["rotation_error_rad"]
    choices = {axis: ("positive" if error > tolerance else "negative" if error < -tolerance else "hold")
               for axis, error, tolerance in zip(AXES, errors, [.005]*3 + [.025]*3)}
    return {**choices, "gripper": state["gripper_target"]}


def choice_options(bundle):
    options = {}
    for candidate in bundle["candidates"]:
        for profile in ("normal", "cautious"):
            options[candidate["id"] + "_" + profile] = {
                "candidate": candidate["id"], "kind": candidate["kind"],
                "intent": candidate["plan"]["intent"], "expected_effect": candidate["expected_effect"],
                "when_use": candidate["when_use"],
                "profile": "normal bounded motion" if profile == "normal" else "40% translation/rotation magnitude for contact precision"}
    options["reobserve"] = "Hold current gripper command with zero commanded motion for 5 environment steps, then take fresh images and replan."
    return options


def select_candidate(client, state, bundle):
    options = choice_options(bundle)
    instructions = ("Choose ONE executable candidate/profile using the task and temporal visual evidence. "
        "These are visual estimates, not contact-force measurements. Numeric geometry is code-owned. "
        "Prefer actual object progress, not TCP movement. When repeated progress is unchanged or regressing, "
        "prefer a physically different contact/reposition candidate over repeating an ineffective push. "
        "Object stillness during an approach is expected; check approach completion instead. "
        "Reduced speed alone does not fix an ineffective contact location. "
        "Use cautious motion for uncertain contact, narrow gaps or precise placement; normal for clear free-space approach. "
        "Use reobserve when no offered motion is supported by the available view; a useful observe candidate can change the view. "
        "Do not infer hidden object poses or treat option probabilities as success. Candidate order is not preference.")
    question = {"selection": {"type": "choice", "instructions": instructions, "criteria": options}}
    system = "Select one offered selection option. Return only JSON: {\"selection\":\"offered_option_id\"}. No probabilities."
    answer = client.request("candidate_selection", state if client.provider == "jev" else {"state": state, "questions": question},
                            questions=question, system=system)
    if not isinstance(answer, dict) or set(answer) != {"selection"}:
        raise ValueError("Expected one candidate selection")
    if client.provider == "jev":
        selected, probabilities = validate_answer(answer["selection"], options, require_highest=False)
    else:
        selected, probabilities = answer["selection"], {}
        if not isinstance(selected, str) or selected not in options:
            raise ValueError("Unknown selected candidate")
    if selected == "reobserve":
        return None, "hold", selected, probabilities
    cid, profile = selected.rsplit("_", 1)
    candidate = next(c for c in bundle["candidates"] if c["id"] == cid)
    return candidate, profile, selected, probabilities


def plan_candidates(client, observation, images, previous_rgb, history):
    current = {view: annotated_image(packet, observation["tcp"]) for view, packet in images.items()}
    labeled = {"BEFORE_"+view: data for view, data in (previous_rgb or {}).items()}
    labeled.update({"NOW_"+view: data for view, data in current.items()})
    state = {"task": observation["task"], "robot": observation, "history": copy.deepcopy(history[-3:]),
             "comparison": "BEFORE is the previous checkpoint, NOW is current. First checkpoint has no BEFORE.",
             "cameras": {view: {"width": p["width"], "height": p["height"]} for view, p in images.items()}}
    answer = client.request("vision_candidates", state, system=SYSTEM, images=labeled)
    return resolve_candidates(answer, observation, images), current, state


def run_episode(args, case, mode, directory, connections, reference=None):
    from .libero_compare import save_frame, verify_pair, write_json
    wall_time = getattr(args, "budget_mode", "bounded") == "wall-time"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "frames").mkdir()
    (directory / "inputs").mkdir()
    started = time.monotonic()
    worker = Worker(args.worker_python, directory / "worker.log", script=Path(__file__).with_name("libero_worker.py"))
    row = {"id": case["id"], "case": case, "mode": mode, "protocol": PROTOCOL,
           "success": None, "status": "setup_error", "steps": 0, "decisions": [], "frames": [], "api_calls": [], "checkpoints": []}
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
            raise ValueError("Task already successful after reset")
        row["setup_seconds"] = time.monotonic() - started
        rollout_start = time.monotonic()
        budget = RequestBudget(None if wall_time else args.max_calls, args.timeout, args.max_usd)
        planner = ModelClient("chat", connections["chat"], budget)
        provider = "jev" if mode == "gpt6-jev" else "chat"
        selector = ModelClient(provider, connections[provider], budget)
        clients = [planner, selector]
        row["success"], row["status"] = False, "step_budget"
        row["frames"].append(save_frame(directory, packet, 0, depth=True))
        event("reset", metadata=row["metadata"], frame=row["frames"][-1])
        previous_rgb, history, recent, finger = None, [], [], -1.
        while wall_time or row["steps"] < args.max_steps:
            budget.check()
            checkpoint_start = time.monotonic() - rollout_start
            observation = packet["observation"]
            event("request_start", stage="vision_candidates", step=row["steps"], wall_seconds=checkpoint_start)
            bundle, annotated, planner_state = plan_candidates(planner, observation, packet["images"], previous_rgb, history)
            if history:
                history[-1]["observed_after"] = bundle["assessment"]
            checkpoint_id = len(row["checkpoints"])
            for view, img in annotated.items():
                (directory / f"inputs/{checkpoint_id:04d}-{view}.png").write_bytes(img)
            row["frames"][-1] = save_frame(directory, packet, row["frames"][-1]["wall_seconds"], depth=True)
            selection_state = {"task": observation["task"], "visual_assessment": bundle["assessment"],
                "candidate_details": bundle["candidates"], "recent_results": copy.deepcopy(history[-3:]),
                "measured_robot": observation, "measured_recent_motion": recent[-2:],
                "contact_sensor": "unavailable; visual contact is an estimate"}
            event("request_start", stage="candidate_selection", step=row["steps"], wall_seconds=time.monotonic()-rollout_start)
            candidate, profile, selected, probabilities = select_candidate(selector, selection_state, bundle)
            row["api_calls"] = budget.calls
            checkpoint = {"index": checkpoint_id, "step": row["steps"], "start_seconds": checkpoint_start,
                "inference_end_seconds": time.monotonic()-rollout_start, "bundle": bundle, "selection": selected,
                "probabilities": probabilities, "planner_state": planner_state, "selector_state": selection_state,
                "image_inputs": {view: {"path": f"inputs/{checkpoint_id:04d}-{view}.png", "sha256": hashlib.sha256(img).hexdigest()}
                                 for view, img in annotated.items()},
                "previous_checkpoint_index": checkpoint_id-1 if previous_rgb else None}
            row["checkpoints"].append(checkpoint)
            event("checkpoint", **checkpoint)
            write_json(directory / "episode.json", row)
            if candidate is None:
                plan = resolve_plan({"stage": "reobserve", "intent": "Hold and refresh the camera evidence",
                    "visual_evidence": bundle["assessment"]["evidence"], "target": {"kind": "relative", "delta_m": [0,0,0]},
                    "rotation_delta": [0,0,0], "gripper": "hold", "max_motor_steps": 1}, observation, packet["images"])
            else:
                plan = candidate["plan"]
            segment_start, segment_steps, stalled = observation["tcp"], row["steps"], 0
            for cycle in range(plan["max_motor_steps"]):
                # No new request here: a response that consumes the final request
                # slot must still be executable within the time/cost bounds.
                if time.monotonic() >= budget.deadline:
                    raise RuntimeError("time_budget")
                if sum(c.get("estimated_usd") or 0 for c in budget.calls) >= budget.max_usd:
                    raise RuntimeError("cost_budget")
                if not wall_time and row["steps"] >= args.max_steps:
                    break
                observation = packet["observation"]
                state = local_state(observation, plan, finger, recent)
                choices = servo_choices(state)
                action = motor_action(choices, state, repeat=args.action_repeat, scale=args.action_scale)
                if profile == "cautious":
                    action[:6] = [x*.4 for x in action[:6]]
                decision = {"index": len(row["decisions"]), "checkpoint": checkpoint_id,
                    "step": row["steps"], "start_seconds": checkpoint_start if cycle == 0 else time.monotonic()-rollout_start,
                    "inference_end_seconds": time.monotonic()-rollout_start, "stage": plan["stage"], "plan": plan, "state": state,
                    "choices": choices, "probabilities": {}, "action": action, "control_source": "deterministic numeric servo",
                    "selection": selected, "selection_profile": profile, "selection_probabilities": probabilities if cycle == 0 else {},
                    "new_selection": cycle == 0}
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
                decision.update(end_seconds=time.monotonic()-rollout_start, end_step=row["steps"])
                finger = action[-1]
                after = packet["observation"]
                movement = math.dist(before["tcp"], after["tcp"])
                qa, qb = before["quaternion_xyzw"], after["quaternion_xyzw"]
                dot = abs(sum(a*b for a,b in zip(qa,qb))) / (math.sqrt(sum(a*a for a in qa))*math.sqrt(sum(b*b for b in qb)))
                rotation_movement = 2*math.acos(min(1.,dot))
                recent.append({"before_tcp": before["tcp"], "after_tcp": after["tcp"],
                               "delta_m": [b-a for a,b in zip(before["tcp"],after["tcp"])], "action": action})
                stalled = stalled+1 if movement < .001 and rotation_movement < .01 else 0
                row["api_calls"] = budget.calls
                write_json(directory / "episode.json", row)
                if row["success"] or packet["truncated"] or stalled >= 2 or reached(local_state(after, plan, finger, recent)):
                    break
            checkpoint["end_step"] = row["steps"]
            checkpoint["end_seconds"] = time.monotonic()-rollout_start
            history.append({"checkpoint": checkpoint_id, "step_before": segment_steps, "step_after": row["steps"],
                "selection": selected, "intent": plan["intent"], "expected_effect": candidate["expected_effect"] if candidate else "new observation",
                "before_tcp": segment_start, "after_tcp": packet["observation"]["tcp"],
                "tracking_stalled": stalled >= 2, "waypoint_reached": reached(local_state(packet["observation"],plan,finger,recent)),
                "assessment_before_action": bundle["assessment"]})
            previous_rgb = annotated
            write_json(directory / "episode.json", row)
            print(json.dumps({"case": case["id"], "mode": mode, "step": row["steps"], "checkpoint": checkpoint_id,
                "selection": selected, "visual_progress": bundle["assessment"]["progress"], "stage": plan["stage"],
                "requests": len(budget.calls), "success": row["success"]},ensure_ascii=False),flush=True)
            if row["success"] or packet["truncated"]:
                break
        row["rollout_seconds"] = time.monotonic()-rollout_start
    except KeyboardInterrupt:
        row["status"] = "interrupted"
    except Exception as exc:
        row["status"] = str(exc) if str(exc) in {"time_budget", "request_budget", "cost_budget"} else ("setup_error" if row["success"] is None else "runtime_error")
        row["error_type"] = type(exc).__name__
        if isinstance(exc, ValueError):
            row["validation_error"] = str(exc)[:300]
            row["last_answers"] = [c.last_answer for c in clients]
        print(json.dumps({"case":case["id"],"mode":mode,"status":row["status"],"error_type":row["error_type"]}),flush=True)
    finally:
        if budget:
            row.setdefault("rollout_seconds",time.monotonic()-budget.started)
            row["api_calls"] = budget.calls
            if row["decisions"] and "end_step" not in row["decisions"][-1]:
                row["decisions"][-1].update(end_step=row["steps"],end_seconds=row["rollout_seconds"],interrupted_action=True)
        row["wall_seconds"] = time.monotonic()-started
        row["metrics"] = usage_summary(row["api_calls"])
        write_json(directory / "episode.json",row)
        for client in clients:
            client.close()
        worker.close()
        if hasattr(worker, "process"):
            row["worker_returncode"] = worker.process.returncode
        stream.close()
        row["wall_seconds"] = time.monotonic()-started
        write_json(directory / "episode.json",row)
    return row
