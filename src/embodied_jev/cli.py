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
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from .server import create_app
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
    else:
        from .runtime import run_headless
        results = []
        for task in args.tasks:
            for seed in args.seeds:
                session = run_headless(task, seed)
                row = {"task": task, "seed": seed, "success": session.world.success(), "status": session.status,
                       "cycles": session.cycles, "max_lift_m": session.world.max_lift,
                       "forbidden_contact_steps": session.world.unsafe_contacts, "message": session.message}
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"provider": "deterministic_baseline", "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
