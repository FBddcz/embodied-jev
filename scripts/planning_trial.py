"""Run one incremental planning trial, preserving failures and camera evidence.

Uses environment model configuration unless --saved-connection explicitly loads
the connection saved in the local UI. Real model calls may incur API charges.
Existing output directories are refused; there is no automatic retry/fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from embodied_jev.runtime import Session, validate_intervention


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=["chat", "claude", "baseline"], default="chat")
    parser.add_argument("--saved-connection", action="store_true")
    parser.add_argument("--observation", choices=["vision", "rgbd", "privileged"], default="vision")
    parser.add_argument("--cameras", choices=["none", "external", "wrist", "both"])
    parser.add_argument("--task", choices=["transfer", "stack", "barrier"], default="transfer")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-cycles", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--intervention", help="JSON with kind, after_cycle and delta_xy")
    parser.add_argument("--shuffle-candidates", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_cycles <= 200 or not 0 < args.timeout <= 3600:
        parser.error("Use 1–200 actions and a timeout in (0, 3600] seconds")
    if args.output.exists():
        parser.error("Output already exists; choose a new directory")
    try:
        intervention = validate_intervention(json.loads(args.intervention)) if args.intervention else None
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    cameras = {"none": [], "external": ["external"], "wrist": ["wrist"],
               "both": ["external", "wrist"]}.get(args.cameras)
    connection = None
    if args.saved_connection:
        from embodied_jev.connection_store import SystemConnectionStore
        connection = SystemConnectionStore().load().get("connections", {}).get(args.provider)
        if not connection:
            parser.error("No saved connection for this provider")
    settings = dict(task=args.task, seed=args.seed, provider=args.provider, preview=True,
                    threshold=0, max_cycles=args.max_cycles, speed=0,
                    control_mode="incremental", observation_mode=args.observation,
                    camera_views=cameras, intervention=intervention,
                    shuffle_candidates=args.shuffle_candidates)
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1] / "src" / "embodied_jev"
    provenance = {"settings": settings, "wall_timeout_seconds": args.timeout,
                  "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted(source.glob("*.py"))}}
    (args.output / "settings.json").write_text(json.dumps(provenance, indent=2) + "\n")
    session = None
    try:
        session = Session(**settings, connection=connection)
        session.start()
        deadline, seen = time.monotonic() + args.timeout, 0
        while session.worker.is_alive():
            session.worker.join(.1)
            with session.lock:
                for row in session.history[seen:]:
                    print(json.dumps({"cycle": row["cycle"], "choice": row["decision"]["choice"],
                        "executed": row["executed"], "intent": row["decision"].get("intent"),
                        "visual_evidence": row["decision"].get("visual_evidence"),
                        "tcp": row["after"]["tcp"], "held": row["after"]["held"],
                        "rejection": row.get("rejection")}, ensure_ascii=False), flush=True)
                if len(session.history) != seen:
                    seen = len(session.history)
                    (args.output / "episode.json").write_text(json.dumps(session.export(), ensure_ascii=False))
            if time.monotonic() > deadline and session.worker.is_alive():
                session.stop()
                session.worker.join(65)
                provenance["runner_timeout"] = True
                break
    except Exception as exc:
        # Upstream exceptions can include a private URL or response body.
        provenance["runner_error_type"] = type(exc).__name__
        print(json.dumps({"runner_error_type": type(exc).__name__}), flush=True)
    finally:
        if session:
            if session.worker and session.worker.is_alive():
                session.stop()
                session.worker.join(65)
            exported = session.export()
            (args.output / "episode.json").write_text(json.dumps(exported, ensure_ascii=False))
            (args.output / "cameras.zip").write_bytes(session.camera_archive())
            print(json.dumps({key: exported[key] for key in
                ("id", "status", "success", "model_calls", "input_tokens", "output_tokens",
                 "wall_seconds", "message")}, ensure_ascii=False), flush=True)
            session.stop()
        (args.output / "settings.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
