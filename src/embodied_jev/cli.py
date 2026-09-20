import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="EmbodiedJev / Xingzhi")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8090)
    serve.add_argument("--log-file", help="Optional rotating JSONL event log, e.g. runs/server.jsonl")
    serve.add_argument("--memory-only", action="store_true", help="Do not read or write system credentials or saved connection metadata")
    bench = sub.add_parser("benchmark")
    bench.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    bench.add_argument("--tasks", nargs="+", choices=["transfer", "stack", "barrier"])
    bench.add_argument("--preset", help="Task/scene JSON exported from the extensions page")
    bench.add_argument("--output", default="runs/benchmark.json")
    bench.add_argument("--provider", choices=["baseline", "minicpm", "chat", "claude", "jev", "local"], default="baseline")
    bench.add_argument("--threshold", type=float, default=.55)
    bench.add_argument("--max-cycles", type=int, default=30)
    bench.add_argument("--timeout", type=float, default=600)
    bench.add_argument("--observation-mode", choices=["privileged", "rgbd", "vision"], default="privileged")
    bench.add_argument("--cameras", choices=["none", "external", "wrist", "both"],
                       help="Enabled camera views; omitted uses the observation mode's default")
    bench.add_argument("--control-mode", choices=["skills", "incremental"], default="skills")
    bench.add_argument("--shuffle-candidates", action="store_true")
    bench.add_argument("--intervention", help='JSON: {"kind":"object_shift","after_cycle":5,"delta_xy":[0.04,0]}')
    sub.add_parser("warmup", help="Load MiniCPM5-2B and make a real two-candidate decision")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from .eventlog import configure_logging
        from .server import create_app
        configure_logging(args.log_file)
        store = None
        if not args.memory_only and os.getenv("EMBODIED_JEV_PERSISTENCE") != "memory":
            from .connection_store import SystemConnectionStore
            store = SystemConnectionStore()
        uvicorn.run(create_app(store=store), host="127.0.0.1", port=args.port, access_log=False)
    elif args.command == "warmup":
        from .policies import DecisionPolicy, minicpm_status
        policy = DecisionPolicy("minicpm")
        result = policy.choose({"purpose": "Model loading test; no robot motion"},
            "Select ready to indicate readiness.", {"ready": "Ready", "hold": "Hold"}, "ready", [])
        print(json.dumps({"runtime": minicpm_status(), "decision": result}, ensure_ascii=False, indent=2))
    else:
        from .runtime import run_headless
        camera_views = {"none": [], "external": ["external"], "wrist": ["wrist"],
                        "both": ["external", "wrist"]}.get(args.cameras)
        try:
            from .runtime import validate_intervention
            intervention = validate_intervention(json.loads(args.intervention)) if args.intervention else None
        except (ValueError, TypeError) as exc:
            parser.error(str(exc))
        preset = None
        if args.preset:
            from .presets import load_preset
            try:
                preset = load_preset(args.preset)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            if args.tasks and args.tasks != [preset["task"]]:
                parser.error("--tasks must match the preset's physical task template")
        tasks = args.tasks or ([preset["task"]] if preset else ["transfer", "stack", "barrier"])
        results = []
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            for seed in args.seeds:
                session = run_headless(task, seed, provider=args.provider, threshold=args.threshold,
                                       max_cycles=args.max_cycles, timeout=args.timeout,
                                       observation_mode=args.observation_mode,
                                       camera_views=camera_views,
                                       control_mode=args.control_mode, intervention=intervention,
                                       shuffle_candidates=args.shuffle_candidates,
                                       scene_config=preset["scene_config"] if preset else None,
                                       user_context=preset["user_context"] if preset else None)
                exported = session.export()
                row = {"task": task, "seed": seed, "success": exported["success"], "status": session.status,
                       "observation_mode": args.observation_mode,
                       "camera_views": exported["camera_views"],
                       "control_mode": args.control_mode, "interventions": exported["interventions"],
                       "cycles": session.cycles, "max_lift_m": session.world.max_lift,
                       "forbidden_contact_steps": session.world.unsafe_contacts, "message": session.message,
                       **{key: exported[key] for key in ("model", "model_runtime", "policy_version", "model_calls", "input_tokens",
                                                        "output_tokens", "model_latency_ms", "wall_seconds", "last_decision")}}
                episode = output.with_name(f"{output.stem}-{task}-{seed}.json")
                episode.write_text(json.dumps(exported, ensure_ascii=False, indent=2))
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                output.write_text(json.dumps({"provider": args.provider, "threshold": args.threshold,
                    "max_cycles": args.max_cycles, "preset": preset, "results": results}, ensure_ascii=False, indent=2))
                if session.worker.is_alive():
                    raise TimeoutError("Inference is still stopping; aborting remaining episodes")


if __name__ == "__main__":
    main()
