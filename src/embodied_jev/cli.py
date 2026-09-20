import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="EmbodiedJev / Xingzhi")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8090)
    bench = sub.add_parser("benchmark")
    bench.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    bench.add_argument("--tasks", nargs="+", choices=["transfer", "stack", "barrier"], default=["transfer", "stack", "barrier"])
    bench.add_argument("--output", default="runs/benchmark.json")
    bench.add_argument("--provider", choices=["baseline", "minicpm", "chat", "claude", "jev", "local"], default="baseline")
    bench.add_argument("--threshold", type=float, default=.55)
    bench.add_argument("--max-cycles", type=int, default=30)
    bench.add_argument("--timeout", type=float, default=600)
    sub.add_parser("warmup", help="Load MiniCPM5-2B and make a real two-candidate decision")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from .server import create_app
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
    elif args.command == "warmup":
        from .policies import DecisionPolicy, minicpm_status
        policy = DecisionPolicy("minicpm")
        result = policy.choose({"purpose": "Model loading test; no robot motion"},
            "Select ready to indicate readiness.", {"ready": "Ready", "hold": "Hold"}, "ready", [])
        print(json.dumps({"runtime": minicpm_status(), "decision": result}, ensure_ascii=False, indent=2))
    else:
        from .runtime import run_headless
        results = []
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        for task in args.tasks:
            for seed in args.seeds:
                session = run_headless(task, seed, provider=args.provider, threshold=args.threshold,
                                       max_cycles=args.max_cycles, timeout=args.timeout)
                exported = session.export()
                row = {"task": task, "seed": seed, "success": exported["success"], "status": session.status,
                       "cycles": session.cycles, "max_lift_m": session.world.max_lift,
                       "forbidden_contact_steps": session.world.unsafe_contacts, "message": session.message,
                       **{key: exported[key] for key in ("model", "model_runtime", "policy_version", "model_calls", "input_tokens",
                                                        "output_tokens", "model_latency_ms", "wall_seconds", "last_decision")}}
                episode = output.with_name(f"{output.stem}-{task}-{seed}.json")
                episode.write_text(json.dumps(exported, ensure_ascii=False, indent=2))
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                output.write_text(json.dumps({"provider": args.provider, "threshold": args.threshold,
                    "max_cycles": args.max_cycles, "results": results}, ensure_ascii=False, indent=2))
                if session.worker.is_alive():
                    raise TimeoutError("Inference is still stopping; aborting remaining episodes")


if __name__ == "__main__":
    main()
