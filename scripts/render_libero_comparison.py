"""Render a paired LIBERO recording on a shared wall clock, including model waits."""
from __future__ import annotations
import argparse
import bisect
from collections import OrderedDict
import hashlib
import json
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

WIDTH, HEIGHT = 1280, 960
BG, PANEL, TEXT, MUTED = "#101f2a", "#182d3a", "#edf3ef", "#a4b6c1"
COLORS = ("#b3d591", "#baa6db")
SUPERVISOR_PROTOCOL = "libero-rgbd-candidate-supervisor-v2"
PROFILES = {"normal": "正常", "cautious": "谨慎 · 40% 幅度", "hold": "保持并重观测"}


def font(size):
    for path in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.truetype("DejaVuSans.ttf", size)


FONTS = {n: font(n) for n in (13, 15, 17, 19, 22, 28)}


def wrap(draw, text, x, y, width, *, size=17, color=TEXT, lines=2):
    line, row = "", 0
    for char in str(text):
        if draw.textlength(line + char, font=FONTS[size]) > width:
            draw.text((x, y + row * (size+6)), line, font=FONTS[size], fill=color)
            row += 1
            line = ""
            if row >= lines:
                return
        line += char
    if line and row < lines:
        draw.text((x, y + row * (size+6)), line, font=FONTS[size], fill=color)


class Episode:
    def __init__(self, root):
        self.root = root
        self.row = json.loads((root / "episode.json").read_text())
        self.frames = self.row["frames"]
        self.times = [f["wall_seconds"] for f in self.frames]
        self.decisions = self.row["decisions"]
        self.supervisor = self.row.get("protocol") == SUPERVISOR_PROTOCOL
        self.checkpoints = {c["index"]: c for c in self.row.get("checkpoints", [])}
        self.duration = self.row.get("rollout_seconds", self.times[-1])
        self.cache = OrderedDict()
        self.points = [f["observation"]["tcp"] for f in self.frames]

    def image(self, relative):
        if relative not in self.cache:
            self.cache[relative] = Image.open(self.root / relative).convert("RGB")
            if len(self.cache) > 32:
                self.cache.popitem(last=False)
        return self.cache[relative]

    def state(self, elapsed):
        idx = max(0, bisect.bisect_right(self.times, elapsed)-1)
        completed = [d for d in self.decisions if d["inference_end_seconds"] <= elapsed]
        decision = completed[-1] if completed else None
        active = [c for c in self.row["api_calls"] if c["start_seconds"] <= elapsed < c["end_seconds"]]
        calls = [c for c in self.row["api_calls"] if c["end_seconds"] <= elapsed]
        return idx, decision, active[0] if active else None, calls


def plot(draw, episode, index, bounds, axes, rect, color, target=None):
    x, y, w, h = rect
    draw.rectangle((x, y, x+w, y+h), fill="#10232f", outline="#314956")
    for frac in (.25, .5, .75):
        draw.line((x+frac*w,y,x+frac*w,y+h), fill="#253e4b")
        draw.line((x,y+frac*h,x+w,y+frac*h), fill="#253e4b")
    lo, hi = bounds
    def project(p):
        return (x+10+(p[axes[0]]-lo[0])/(hi[0]-lo[0])*(w-20),
                y+h-10-(p[axes[1]]-lo[1])/(hi[1]-lo[1])*(h-20))
    points = [project(p) for p in episode.points[:index+1]]
    if len(points) > 1:
        draw.line(points, fill=color, width=2)
    px, py = points[-1]
    draw.ellipse((px-4,py-4,px+4,py+4), fill="#ffffff")
    if target and all(lo[i] <= target[axis] <= hi[i] for i, axis in enumerate(axes)):
        px, py = project(target)
        draw.line((px-5,py,px+5,py), fill="#f2ce82", width=2)
        draw.line((px,py-5,px,py+5), fill="#f2ce82", width=2)


def render(pair, output, speed=8., fps=15):
    global WIDTH
    if not 1 <= len(pair) <= 2:
        raise ValueError("Expected one episode or a paired comparison")
    WIDTH = 640 * len(pair)
    episodes = [Episode(path) for path in pair]
    if len({e.row.get("protocol") for e in episodes}) != 1:
        raise ValueError("Cannot render different control protocols as a paired comparison")
    a, b = episodes[0].row["metadata"], episodes[-1].row["metadata"]
    if any(a[k] != b[k] for k in ("case", "initial_state_sha256", "settled_state_sha256", "versions")):
        raise ValueError("Cannot render non-matching initial states or simulator versions")
    output.mkdir(parents=True, exist_ok=False)
    end = max(e.duration for e in episodes)
    points = [p for e in episodes for p in e.points]
    bounds = {}
    for axes in ((0,1), (0,2)):
        lo = [min(p[i] for p in points)-.04 for i in axes]
        hi = [max(p[i] for p in points)+.04 for i in axes]
        bounds[axes] = (lo, hi)
    count = math.ceil(end / speed * fps) + fps * 2
    video = output / "comparison.mp4"
    encoder = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo", "-s", f"{WIDTH}x{HEIGHT}", "-pix_fmt", "rgb24",
        "-r", str(fps), "-i", "-", "-an", "-vcodec", "libx264", "-preset", "fast",
        "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)], stdin=subprocess.PIPE)
    try:
        for frame_id in range(count):
            elapsed = min(end, frame_id / fps * speed)
            image = Image.new("RGB", (WIDTH, HEIGHT), BG)
            draw = ImageDraw.Draw(image)
            draw.text((26, 16), "行知 · LIBERO " + ("v2 候选选择" if episodes[0].supervisor else "真实观测对照"), font=FONTS[28], fill=TEXT)
            draw.text((26, 59), f"共同墙钟时间 · {speed:g}× 播放，保留模型等待 · {elapsed:.1f} / {end:.1f} s",
                      font=FONTS[17], fill=MUTED)
            for side, episode in enumerate(episodes):
                x, color = 20+side*640, COLORS[side]
                index, decision, active, calls = episode.state(elapsed)
                current = episode.frames[index]
                label = "纯 GPT-6" if episode.row["mode"] == "gpt6" else "GPT-6 + Jev"
                done = elapsed >= episode.duration
                status = ("成功" if episode.row["success"] else "未完成") if done else (
                    "等待 GPT-6 视觉候选" if active and active["stage"] == "vision_candidates" else
                    "等待 GPT-6 视觉规划" if active and active["stage"] == "vision_plan" else
                    "等待 " + ("Jev" if active["provider"] == "jev" else "GPT-6") +
                    (" 候选选择" if episode.supervisor else " 局部决策") if active else
                    "代码伺服执行" if episode.supervisor else "执行动作")
                draw.rounded_rectangle((x, 96, x+620, 910), radius=8, fill=PANEL)
                draw.text((x+14, 109), label + "  /  seed " + str(episode.row["case"]["seed"]), font=FONTS[22], fill=color)
                counts = {p:sum(c["provider"] == p for c in calls) for p in ("chat","jev")}
                draw.text((x+14, 143), f"{min(elapsed,episode.duration):.1f}s · step {current['step']} · GPT {counts['chat']} / Jev {counts['jev']}",
                          font=FONTS[15], fill=MUTED)
                draw.text((x+14, 169), status, font=FONTS[17], fill="#f2ce82" if active else color)
                external = episode.image(current["images"]["external"]["rgb"]).resize((440,440), Image.Resampling.LANCZOS)
                image.paste(external, (x+14, 204))
                wrist = episode.image(current["images"]["wrist"]["rgb"]).resize((148,148), Image.Resampling.LANCZOS)
                image.paste(wrist, (x+462,204))
                draw.text((x+466, 363), "腕部相机", font=FONTS[15], fill=MUTED)
                stage = decision["stage"] if decision else "等待首个视觉目标"
                wrap(draw, stage, x+465, 396, 144, size=15 if episode.supervisor else 17,
                     color=color, lines=2 if episode.supervisor else 3)
                if decision and episode.supervisor:
                    selection = decision["selection"]
                    draw.text((x+465, 446), "候选 " + selection, font=FONTS[13], fill=color)
                    draw.text((x+465, 469), PROFILES.get(decision["selection_profile"], decision["selection_profile"]),
                              font=FONTS[13], fill=MUTED)
                    checkpoint = episode.checkpoints.get(decision.get("checkpoint"), {})
                    probabilities = decision.get("selection_probabilities") or checkpoint.get("probabilities", {})
                    draw.text((x+465, 493), "候选选项概率" if probabilities else "接口未返回概率", font=FONTS[13], fill=MUTED)
                    for i, (option, probability) in enumerate(probabilities.items()):
                        draw.text((x+465, 516+i*16), f"{option}: {probability:.0%}", font=FONTS[13],
                                  fill=color if option == selection else MUTED)
                    if probabilities:
                        draw.text((x+465, 633), "概率 ≠ 成功率", font=FONTS[13], fill=MUTED)
                elif decision:
                    choices = decision.get("choices", {})
                    for i, key in enumerate(("x","y","z","rx","ry","rz","gripper")):
                        name = key + ": " + choices.get(key, "—")
                        draw.text((x+465, 478+i*21), name, font=FONTS[13], fill=MUTED)
                draw.text((x+14, 658), "XY 俯视轨迹", font=FONTS[15], fill=MUTED)
                draw.text((x+322, 658), "XZ 高度轨迹", font=FONTS[15], fill=MUTED)
                target = decision.get("plan", {}).get("target_xyz") if decision else None
                plot(draw, episode, index, bounds[(0,1)], (0,1), (x+14,688,294,112), color, target)
                plot(draw, episode, index, bounds[(0,2)], (0,2), (x+322,688,288,112), color, target)
                purpose = decision.get("plan",{}).get("intent","从双相机图像确定下一阶段目标。") if decision else "从双相机图像确定下一阶段目标。"
                wrap(draw, purpose, x+14, 813, 590, size=17, lines=1 if episode.supervisor else 2)
                if decision and episode.supervisor:
                    servo = "代码伺服：" + " · ".join(k + "=" + v for k, v in decision.get("choices", {}).items())
                    wrap(draw, servo, x+14, 839, 590, size=13, color=MUTED, lines=2)
                draw.text((x+14, 880), "白点：当前 TCP  ·  彩线：真实轨迹  ·  金色十字：模型目标", font=FONTS[13], fill=MUTED)
            draw.text((26, 925), "RGB-D + 本体反馈 · 无物体真值 · 官方成功判定 · 小样本开发实验", font=FONTS[15], fill=MUTED)
            if frame_id == 0:
                image.save(output / "poster.png")
            encoder.stdin.write(image.tobytes())
    finally:
        encoder.stdin.close()
    if encoder.wait():
        raise RuntimeError("Video encoder failed")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    metadata = {"speed": speed, "fps": fps, "duration_seconds": count/fps,
                "wall_seconds": end, "timeline": "shared rollout wall time including all model waits; setup excluded",
                "video_sha256": digest,
                "episodes": [{"path": str(e.root / "episode.json"), "sha256": hashlib.sha256((e.root/"episode.json").read_bytes()).hexdigest(),
                              "mode":e.row["mode"],"success":e.row["success"]} for e in episodes]}
    (output / "media.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"video":str(video),"seconds":count/fps,"wall_seconds":end}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--speed", type=float, default=8)
    args = parser.parse_args()
    if not 1 <= args.speed <= 32:
        parser.error("Speed must be 1..32")
    render(args.episodes, args.output, args.speed)
