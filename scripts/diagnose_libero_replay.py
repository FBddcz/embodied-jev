"""Replay recorded actions for post-hoc diagnostics; never sends truth to models."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run", type=Path, required=True)
parser.add_argument("--libero-root", type=Path, required=True)
parser.add_argument("--config-dir", type=Path, required=True)
args = parser.parse_args()
RUN = args.run.resolve()
protocol = json.loads((RUN / "protocol.json").read_text())

sys.path.insert(0, str(RUN / "reproduction"))
from libero_worker import VisionEnvironment

reports = []
for path in sorted(RUN.glob("*/*/episode.json")):
    row = json.loads(path.read_text())
    env = VisionEnvironment({"libero_root": str(args.libero_root.resolve()),
        "config_dir": str(args.config_dir.resolve()), "case": row["case"],
        "horizon": 1_000_000_000 if protocol["budget"].get("mode") == "wall-time" else protocol["budget"]["max_steps"], "camera_size": protocol["budget"]["camera_size"], "settle_steps": 10})
    try:
        assert env.metadata["settled_state_sha256"] == row["metadata"]["settled_state_sha256"]
        sim = env.env.sim
        names = [name for name in sim.model.joint_names if "top_level" in name or "microjoint" in name]
        def joint_state():
            return {name: float(sim.data.get_joint_qpos(name)) for name in names}
        states = [{"step": 0, "joints": joint_state()}]
        errors = []
        frames = {f["step"]: f for f in row["frames"]}
        for decision in row["decisions"]:
            for step in range(decision["step"]+1, decision["end_step"]+1):
                result = env.step(decision["action"], capture=False)
                errors.append(float(np.max(np.abs(np.array(result["observation"]["tcp"])-frames[step]["observation"]["tcp"]))))
                states.append({"step": step, "joints": joint_state()})
        report = {"mode": row["mode"], "id": row["id"], "success": bool(env.env.check_success()),
                  "max_tcp_replay_error_m": max(errors, default=0), "states": states}
        reports.append(report)
        print(json.dumps({**{k:v for k,v in report.items() if k != "states"},
            "initial": states[0], "mid": states[min(100, len(states)-1)], "final": states[-1],
            "range": {name: [min(s["joints"][name] for s in states), max(s["joints"][name] for s in states)] for name in names}}), flush=True)
    finally:
        env.close()
target = RUN / "replay-diagnostics.json"
target.write_text(json.dumps({"scope": "Post-hoc action replay; joint truth never sent to models", "episodes": reports},indent=2)+"\n")
print(target)
