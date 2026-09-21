"""Replay a recorded Meta-World comparison as a compact 2 x 6 GIF/MP4.

Run with the original Meta-World Python environment. Replays only saved actions,
checks every observed state and success flag, and never imports a model client.
The frozen benchmark worker and source traces must accompany the input batch.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import subprocess
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont


TASKS = [(task, seed) for task in ("reach-v3", "push-v3", "pick-place-v3") for seed in (0, 1)]
LABELS = {"reach-v3": "到达", "push-v3": "推物", "pick-place-v3": "抓放"}
PHASES = {"reach": "接近目标", "hold": "保持", "approach": "对齐 / 接近",
          "grasp": "闭爪抓取", "lift": "抬升", "carry": "搬向目标",
          "lower_goal": "下放", "finish": "完成", "behind": "移到物体后方",
          "push": "推动", "align": "重新对齐", "lower": "下降到接触高度"}
CHOICES = {"positive": "+", "negative": "−", "hold": "保持", "open": "张开", "close": "闭合"}
BG, INK, MUTED, LINE = "#f3f5f8", "#1c2a3b", "#617186", "#dce3eb"
GREEN, BLUE, AMBER, RED = "#19765a", "#3c63a8", "#ac7112", "#b24a45"
PANEL_W, PANEL_H, GAP, MARGIN = 236, 378, 10, 20
SIZE = (MARGIN * 2 + PANEL_W * 6 + GAP * 5, 916)
SCENE_SIZE = (220, 166)
FPS, HORIZON = 10, 200
CAMERA = {"lookat": [0., .73, .15], "distance": .95, "azimuth": 160., "elevation": -40.}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_state(actual, expected, where):
    """Compare all recorded fields, including fingers, contacts and previous state."""
    if isinstance(expected, dict):
        if actual.keys() != expected.keys():
            raise ValueError(f"State keys differ at {where}")
        return max((check_state(actual[k], v, f"{where}.{k}") for k, v in expected.items()), default=0.)
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise ValueError(f"State length differs at {where}")
        return max((check_state(a, b, where) for a, b in zip(actual, expected)), default=0.)
    if type(expected) in (int, float):
        error = abs(actual - expected)
        if not math.isfinite(error) or error > 1e-8:
            raise ValueError(f"Replay differs at {where}: {error}")
        return error
    if actual != expected:
        raise ValueError(f"Replay differs at {where}: {actual!r} != {expected!r}")
    return 0.


def replay(batch, worker, policy, task, seed):
    case = f"{task}-{seed}"
    summary_path = batch / policy / "summary.json"
    summary = json.loads(summary_path.read_text())
    episode = next(e for e in summary["episodes"] if e["id"] == case)
    trace = batch / policy / f"{case}-trace.jsonl"
    rows = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    if len(rows) != episode["steps"] or not rows:
        raise ValueError(f"Trace length disagrees with summary: {policy}/{case}")
    if summary["source_sha256"]["benchmark_worker.py"] != digest(batch / "reproduction/benchmark_worker.py"):
        raise ValueError("Frozen worker hash disagrees with the experiment")
    for package, key in (("metaworld", "version"), ("mujoco", "mujoco")):
        if importlib.metadata.version(package) != episode["metadata"][key]:
            raise ValueError(f"Replay requires original {package} version")
    env = worker.MetaWorld({"task": task, "seed": seed}, HORIZON, control_mode="hierarchical")
    renderer = None
    try:
        initial = env.reset()["observation"]
        initial_hash = hashlib.sha256(json.dumps(initial, sort_keys=True).encode()).hexdigest()
        if initial_hash != episode["initial_observation_sha256"]:
            raise ValueError(f"Initial state hash differs: {policy}/{case}")
        maximum = check_state(initial, rows[0]["observation"], f"{case}.initial")
        raw = env.env.unwrapped
        renderer = mujoco.Renderer(raw.model, width=SCENE_SIZE[0], height=SCENE_SIZE[1])
        camera = mujoco.MjvCamera()
        camera.lookat[:] = CAMERA["lookat"]
        camera.distance, camera.azimuth, camera.elevation = (CAMERA[k] for k in ("distance", "azimuth", "elevation"))

        def capture():
            renderer.update_scene(raw.data, camera=camera)
            return Image.fromarray(renderer.render().copy())

        scenes = [capture()]
        decisions = []
        current = None
        previous = initial
        for n, row in enumerate(rows):
            if row["step"] != n or not row["executed"]:
                raise ValueError(f"Non-contiguous executed trace: {policy}/{case}")
            maximum = max(maximum, check_state(previous, row["observation"], f"{case}.{n}.before"))
            if row.get("decisions"):
                current = row
                if set(row["decisions"]) != {"x", "y", "z", "gripper"}:
                    raise ValueError("Missing recorded motor choice")
            if current is None:
                raise ValueError("Missing first decision")
            decisions.append(current)
            after = env.step(row["action"])
            maximum = max(maximum, check_state(after["observation"], row["after"], f"{case}.{n}.after"))
            if any(after[k] != row["evaluation"][k] for k in ("success", "terminated", "truncated")):
                raise ValueError(f"Replay evaluation differs: {policy}/{case}/{n}")
            previous = after["observation"]
            scenes.append(capture())
        if bool(rows[-1]["evaluation"]["success"]) != bool(episode["success"]):
            raise ValueError("Terminal success differs from the summary")
        metadata = {"policy": policy, "case": case, "model": episode["model"],
                    "steps": len(rows), "status": episode["status"], "error_type": episode.get("error_type"),
                    "trace": f"{policy}/{trace.name}", "trace_sha256": digest(trace),
                    "summary_sha256": digest(summary_path), "initial_observation_sha256": initial_hash,
                    "max_observation_numeric_error": maximum, "all_evaluations_match": True}
        print(f"{policy}/{case}: {len(rows)} steps checked, max error {maximum:g}", flush=True)
        return dict(episode=episode, rows=rows, decisions=decisions, scenes=scenes, metadata=metadata)
    finally:
        if renderer is not None:
            renderer.close()
        env.close()


class Grid:
    def __init__(self, episodes, font):
        self.episodes = episodes
        self.fonts = {s: ImageFont.truetype(font, s) for s in (11, 12, 13, 14, 15, 18, 24)}

    def text(self, draw, xy, value, size=13, color=INK, width=None):
        if width is not None and self.fonts[size].getlength(value) > width:
            raise ValueError(f"Label does not fit ({width}px): {value}")
        draw.text(xy, value, font=self.fonts[size], fill=color)

    def panel(self, image, record, x, y, step, color):
        d = ImageDraw.Draw(image)
        ep, rows = record["episode"], record["rows"]
        n = min(step, len(rows))
        terminal = n == len(rows)
        status, status_color = "执行中", color
        if terminal:
            if ep["success"]:
                status, status_color = "成功", GREEN
            elif ep.get("error_type") in {"ReadTimeout", "ConnectTimeout"}:
                status, status_color = "请求超时", AMBER
            elif ep["status"] == "step_limit":
                status, status_color = "步数耗尽", RED
            else:
                status, status_color = "运行中断", AMBER
        d.rounded_rectangle((x, y, x + PANEL_W, y + PANEL_H), radius=10, fill="white", outline=LINE)
        d.rounded_rectangle((x + 8, y + 8, x + 89, y + 29), radius=5, fill=status_color)
        self.text(d, (x + 16, y + 10), status, 13, "white")
        self.text(d, (x + 123, y + 11), f"步 {n:03d} / 200", 12, MUTED)
        image.paste(record["scenes"][n], (x + 8, y + 37))
        row = record["decisions"][max(0, n - 1)]
        goal = row["subgoal"]
        phase = PHASES.get(goal["choice"], goal["choice"])
        prefix = "末次" if terminal else "子目标"
        self.text(d, (x + 10, y + 213), f"{prefix} · {phase}", 14, color, 167)
        p = goal.get("selected_probability")
        if p is not None:
            self.text(d, (x + 184, y + 214), f"{p:.0%}", 12, color)
        for i, (channel, label) in enumerate((("x", "X"), ("y", "Y"), ("z", "Z"), ("gripper", "爪"))):
            answer = row["decisions"][channel]
            yy = y + 239 + i * 22
            self.text(d, (x + 10, yy), f"{label}  {CHOICES[answer['choice']]}", 13, INK, 70)
            options = ("open", "hold", "close") if channel == "gripper" else ("negative", "hold", "positive")
            probabilities = answer.get("probabilities") or {}
            for j, option in enumerate(options):
                xx = x + 82 + 45 * j
                selected = option == answer["choice"]
                d.rounded_rectangle((xx, yy, xx + 41, yy + 17), radius=3,
                                    fill=color if selected else "#edf1f6")
                value = f"{probabilities[option]:.0%}" if option in probabilities else CHOICES[option]
                self.text(d, (xx + (41 - self.fonts[11].getlength(value)) / 2, yy + 1), value,
                          11, "white" if selected else MUTED)
        obs = rows[n - 1]["after"] if n else rows[0]["observation"]
        tracked = obs["tcp"] if ep["task"] == "reach-v3" else obs["object_slots"][0]["position"]
        distance = float(np.linalg.norm(np.array(tracked) - obs["goal"])) * 1000
        contact = "双指接触" if obs["manipulation"]["bilateral_contact"] else "无双指接触"
        self.text(d, (x + 10, y + 331), f"距目标 {distance:.0f} mm · {contact}", 11, MUTED, 217)
        # The four motor entries share one request; never sum their latency.
        latency = (goal["latency_ms"] + row["decisions"]["x"]["latency_ms"]) / 1000
        self.text(d, (x + 10, y + 351), f"决策 {row['decision_index'] + 1:02d} · 两层调用 {latency:.2f}s", 11, MUTED, 217)
        d.rectangle((x + 10, y + 372, x + 226, y + 374), fill=LINE)
        if n:
            d.rectangle((x + 10, y + 372, x + 10 + 216 * n / HORIZON, y + 374), fill=status_color)

    def frame(self, step):
        image = Image.new("RGB", SIZE, BG)
        d = ImageDraw.Draw(image)
        self.text(d, (20, 13), "Meta-World · 六局并排对照", 24)
        self.text(d, (SIZE[0] - 420, 23), "统一视角 · 真实选择 · 按环境步同步", 15, MUTED)
        self.text(d, (20, 43), "原始动作重放 · 省略 API 等待，播放速度不代表推理速度 · 成功或中断后保持末帧", 12, MUTED)
        for column, (task, seed) in enumerate(TASKS):
            xx = MARGIN + column * (PANEL_W + GAP)
            self.text(d, (xx + 6, 60), f"{LABELS[task]} · seed {seed}", 15)
        self.text(d, (20, 87), "Jev  ·  真实候选概率", 14, GREEN)
        self.text(d, (290, 88), "XYZ 概率顺序 − / 保持 / +；夹爪 张开 / 保持 / 闭合", 12, MUTED)
        self.text(d, (20, 504), "GPT-6 Astra  ·  记录选择（接口未返回候选概率）", 14, BLUE)
        for row_index, yy, color in ((0, 111, GREEN), (1, 528, BLUE)):
            for column in range(6):
                self.panel(image, self.episodes[row_index * 6 + column],
                           MARGIN + column * (PANEL_W + GAP), yy, step, color)
        return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Output basename")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--font", default="/System/Library/Fonts/STHeiti Medium.ttc")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    outputs = {ext: args.output.with_suffix(ext) for ext in (".mp4", ".gif", ".png", ".json")}
    if not args.overwrite and any(p.exists() for p in outputs.values()):
        parser.error("Output exists; pass --overwrite explicitly")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    worker_path = args.batch / "reproduction/benchmark_worker.py"
    spec = importlib.util.spec_from_file_location("frozen_worker", worker_path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    episodes = [replay(args.batch, worker, policy, task, seed)
                for policy in ("jev", "chat") for task, seed in TASKS]
    for i in range(6):
        if episodes[i]["metadata"]["initial_observation_sha256"] != episodes[i + 6]["metadata"]["initial_observation_sha256"]:
            raise ValueError("Paired models have different initial states")
    grid = Grid(episodes, args.font)
    frame_steps = [0] * FPS + list(range(1, HORIZON + 1)) + [HORIZON] * (FPS * 3)
    command = [args.ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{SIZE[0]}x{SIZE[1]}", "-r", str(FPS), "-i", "-", "-an",
               "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(outputs[".mp4"])]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for step in frame_steps:
            frame = grid.frame(step)
            process.stdin.write(frame.tobytes())
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("MP4 encoder failed")
    grid.frame(HORIZON).save(outputs[".png"])
    subprocess.run([args.ffmpeg, "-v", "error", "-y", "-i", str(outputs[".mp4"]),
                    "-filter_complex", "split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3",
                    "-loop", "0", str(outputs[".gif"])], check=True)
    metadata = {"layout": "2 rows x 6 columns; Jev then GPT-6 Astra", "source_batch": args.batch.name,
                "columns": [f"{task}-{seed}" for task, seed in TASKS], "size": SIZE,
                "frames_before_gif_optimization": len(frame_steps), "fps": FPS,
                "duration_seconds": len(frame_steps) / FPS, "model_calls_during_render": 0,
                "replay": "Original seeds and recorded actions; every observation and evaluation verified. No inferred continuation after termination.",
                "timing": "10 environment steps per playback second; API waiting omitted, terminal scenes held. Not a model-speed comparison.",
                "camera": CAMERA,
                "probabilities": "Jev: actual API probabilities. GPT: choices only; no invented probabilities.",
                "worker_sha256": digest(worker_path), "renderer_sha256": digest(Path(__file__)),
                "episodes": [e["metadata"] for e in episodes],
                "artifacts": {p.name: {"sha256": digest(p), "bytes": p.stat().st_size}
                              for ext, p in outputs.items() if ext != ".json"}}
    outputs[".json"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"outputs": [str(p) for p in outputs.values()], "seconds": metadata["duration_seconds"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
