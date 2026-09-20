"""Make an annotated MP4/GIF from a saved episode and its original camera ZIP.

No model calls or physics steps are made. The large view redraws recorded qpos;
the two smaller views show the exact PNGs sent to the model for that decision.
Playback gives each decision equal screen time and omits API waiting.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import imageio_ffmpeg
import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from embodied_jev.physics import ASSETS, build_scene

SIZE = (1280, 1080)
FPS = 20
BG, INK, MUTED = "#f3f6f3", "#173b32", "#62766d"
GREEN, AMBER = "#2d7c65", "#b27618"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def find_font(value):
    if value:
        return value
    for name in ("/System/Library/Fonts/STHeiti Medium.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "C:/Windows/Fonts/msyh.ttc"):
        if Path(name).is_file():
            return name
    raise ValueError("Pass --font with a Chinese-capable TrueType/OpenType font")


def wrap(text, font, width):
    lines, line = [], ""
    for char in text:
        if char == "\n" or font.getlength(line + char) > width:
            lines.append(line)
            line = "" if char == "\n" else char
        else:
            line += char
    if line:
        lines.append(line)
    return lines


class Demo:
    def __init__(self, episode, archive, title, font):
        if (episode.get("task") != "transfer" or episode.get("control_mode") != "incremental"
                or episode.get("observation_mode") != "vision" or not episode.get("history")):
            raise ValueError("Expected a non-empty transfer episode with incremental vision control")
        self.episode, self.archive, self.title = episode, archive, title
        model = episode.get("model") or episode.get("provider") or "Unknown model"
        self.model_label = "GPT-6 Astra" if model in {"gpt-6-astra", "gpt-6-astra-2026-09-03"} else model
        self.fonts = {size: ImageFont.truetype(font, size) for size in (15, 17, 20, 23, 28, 34)}
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["episode_id"] != episode["id"]:
            raise ValueError("The episode and camera archive have different IDs")
        self.images = {}
        self.checked = 0
        for row in manifest["frames"]:
            pixels = archive.read(row["file"])
            if digest(pixels) != row["sha256"] or len(pixels) != row["byte_length"]:
                raise ValueError("Camera archive hash/length mismatch")
            self.images[(str(row["capture_id"]), row["view"])] = (row, pixels)
        for step in episode["history"]:
            for row in step["decision_inputs"]["action"]["images"]:
                saved, _ = self.images[(str(row["capture_id"]), row["view"])]
                if row["sha256"] != saved["sha256"]:
                    raise ValueError("Model input does not match the archived pixels")
                self.checked += 1
        xml, target, _ = build_scene(episode["task"], episode["seed"], episode["scene_config"])
        if digest(xml.replace(str(ASSETS), "ASSETS").encode()) != episode["scene_hash"]:
            raise ValueError("Scene changed; render from the episode's original code version")
        model = mujoco.MjModel.from_xml_string(xml)
        target_ids = [i for i in range(model.ngeom) if model.geom(i).name == "support" or
                      (model.geom(i).name or "").startswith("tray_wall_")]
        self.world = SimpleNamespace(model=model, data=mujoco.MjData(model), target=target,
                                     target_geom_ids=target_ids)
        self.initial_target = self.world.target.copy()
        self.target_positions = self.world.model.geom_pos[self.world.target_geom_ids].copy()
        self.world.model.vis.global_.offwidth = max(768, self.world.model.vis.global_.offwidth)
        self.world.model.vis.global_.offheight = max(576, self.world.model.vis.global_.offheight)
        self.renderer = mujoco.Renderer(self.world.model, width=768, height=576)
        self.camera = mujoco.MjvCamera()
        self.camera.lookat[:] = [.43, 0, .035]
        self.camera.distance, self.camera.azimuth, self.camera.elevation = 1., 180., -65.
        self.last_key = self.last_pixels = None
        self.moments = []

    def scene(self, frame):
        target = frame.get("evaluation_target", self.initial_target)
        key = (tuple(frame["qpos"]), tuple(target))
        if key != self.last_key:
            self.world.data.qpos[:] = frame["qpos"]
            self.world.data.time = frame["time"]
            self.world.model.geom_pos[self.world.target_geom_ids] = (
                self.target_positions + np.asarray(target) - self.initial_target)
            mujoco.mj_forward(self.world.model, self.world.data)
            self.renderer.update_scene(self.world.data, self.camera)
            self.last_pixels = Image.fromarray(self.renderer.render().copy())
            self.last_key = key
        return self.last_pixels

    def base(self, capture, cycle, intent, *, banner=None, final=False, step=None):
        image = Image.new("RGB", SIZE, BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1280, 84), fill=INK)
        draw.text((24, 13), "行知 · EmbodiedJev", font=self.fonts[28], fill="white")
        draw.text((335, 17), self.title, font=self.fonts[23], fill="white")
        subtitle = f"{self.model_label} · 双相机原始图像 · {len(self.episode['history'])} 步 · 实验原始耗时 {self.episode['wall_seconds']:.0f} 秒"
        draw.text((26, 57), subtitle, font=self.fonts[17], fill="#d2e5db")
        progress = f"{cycle:02d} / {len(self.episode['history'])}"
        draw.text((1115, 22), progress, font=self.fonts[34], fill="white")
        draw.text((24, 102), "机械臂动作回放", font=self.fonts[23], fill=INK)
        draw.text((240, 107), "按保存的姿态重绘", font=self.fonts[17], fill=MUTED)
        purpose = "最终观测" if final else f"本步模型输入 · 第 {capture} 帧"
        draw.text((816, 103), purpose, font=self.fonts[20], fill=INK)
        for view, y, label in (("external", 152, "外部相机 · 固定机位"),
                                ("wrist", 522, "腕部相机 · 随手移动")):
            draw.text((816, y - 24), label, font=self.fonts[17], fill=MUTED)
            pixels = self.images[(str(capture), view)][1]
            with Image.open(io.BytesIO(pixels)) as original:
                image.paste(original.convert("RGB").resize((440, 330), Image.Resampling.LANCZOS), (816, y))
        colour = AMBER if banner else GREEN
        draw.rounded_rectangle((24, 738, 792, 1036), radius=12, fill="white", outline="#d7e2d9")
        if step:
            decision = step["decision"]
            draw.text((42, 752), "候选动作 → 模型选择", font=self.fonts[23], fill=GREEN)
            timing = f"本次调用 {decision['latency_ms'] / 1000:.2f} 秒"
            draw.text((540, 758), timing, font=self.fonts[17], fill=MUTED)
            probabilities = decision.get("probabilities") or {}
            for index, candidate in enumerate(step["candidates"]):
                x, y = 42 + (index % 3) * 246, 792 + (index // 3) * 34
                chosen = candidate["id"] == decision["choice"]
                fill, ink = (GREEN, "white") if chosen else ("#edf2ee", MUTED)
                draw.rounded_rectangle((x, y, x + 232, y + 28), radius=5, fill=fill)
                label = candidate["label"]
                delta = candidate.get("delta_xyz")
                if delta and any(delta):
                    axis = next(i for i, value in enumerate(delta) if value)
                    label = f"{'XYZ'[axis]} {delta[axis] * 1000:+.0f} mm"
                draw.text((x + 9, y + 5), label, font=self.fonts[17], fill=ink)
                score = probabilities.get(candidate["id"])
                mark = f"{score:.1%}" if score is not None else "已选" if chosen else ""
                draw.text((x + 160, y + 5), mark, font=self.fonts[17], fill=ink)
            explanation = "模型返回的候选概率" if probabilities else "候选概率：此接口未提供"
        else:
            draw.text((42, 752), banner or ("任务完成" if final else "演示说明"), font=self.fonts[23], fill=colour)
            draw.text((42, 807), "相机观察 → 有限候选 → 模型选择 → 物理反馈", font=self.fonts[23], fill=INK)
            draw.text((42, 858), "每一步的候选、选择与反馈均来自实验记录。", font=self.fonts[20], fill=MUTED)
            draw.text((42, 910), f"实际模型：{self.model_label} · 高亮所选动作", font=self.fonts[20], fill=MUTED)
            explanation = "演示说明" if not final else "完成判定"
        draw.rounded_rectangle((816, 870, 1256, 1036), radius=12, fill="white", outline="#d7e2d9")
        draw.text((832, 885), explanation, font=self.fonts[20], fill=colour)
        lines = wrap(intent, self.fonts[20], 405)
        if len(lines) > 4:
            raise ValueError("Caption needs more room; do not silently truncate model output")
        for i, line in enumerate(lines):
            draw.text((832, 920 + i * 26), line, font=self.fonts[20], fill=INK)
        draw.rectangle((24, 1049, 1256, 1053), fill="#d5e1d8")
        draw.rectangle((24, 1049, 24 + int(1232 * cycle / len(self.episode["history"])), 1053), fill=colour)
        draw.text((24, 1061), "真实记录回放 · 已省略模型等待 · 相机为决策前采样 · 绿色表示选中，不表示概率 100%", font=self.fonts[15], fill=MUTED)
        return image

    def composite(self, base, frame, note=None):
        image = base.copy()
        image.paste(self.scene(frame), (24, 142))
        draw = ImageDraw.Draw(image)
        if note:
            draw.rounded_rectangle((40, 658, 770, 704), radius=8, fill=INK)
            draw.text((57, 669), note, font=self.fonts[23], fill="white")
        return image

    def frames(self):
        episode, records = self.episode, self.episode["frames"]
        first = episode["history"][0]
        first_capture = first["before"]["perception"]["capture_id"]
        intro = self.base(first_capture, 0, "模型看图后，每次选择一个 XYZ 短步或夹爪动作。", banner="演示说明")
        for _ in range(FPS * 2):
            yield self.composite(intro, records[0])
        for step in episode["history"]:
            cycle = step["cycle"]
            before = step["before"]["perception"]["capture_id"]
            after = step["after"]["perception"]["capture_id"]
            recorded = [f for f in records if f["cycle"] == cycle and
                        f["observation"]["perception"]["capture_id"] in (before, after)]
            if not recorded:
                raise ValueError(f"No recorded motion for cycle {cycle}")
            start_time = step["before"]["sim_seconds"] + records[0]["time"]
            previous = [f for f in records if abs(f["time"] - start_time) < 1e-4 and
                        f["observation"]["perception"]["capture_id"] == before]
            if previous:
                recorded.insert(0, previous[-1])
            base = self.base(before, cycle, step["decision"].get("intent") or step["label"], step=step)
            lost = step["before"]["held"] and not step["after"]["held"] and step["action"].get("gripper") != "open"
            caught = not step["before"]["held"] and step["after"]["held"]
            held = "持物" if step["after"]["held"] else "未持物"
            state = "已执行" if step.get("executed", True) else "被安全检查拒绝"
            note = "双指接触丢失：模型将在下一步重新观察" if lost else "双指接触建立：已夹住方块" if caught else f"物理反馈：{state} · {held} · 继续观察并选择下一步"
            if lost or caught:
                self.moments.append({"cycle": cycle, "event": "contact_lost" if lost else "grasp_contact"})
            # Equal 1.2 s screen time per decision, using only recorded poses.
            for i in range(24):
                frame = recorded[round(i / 23 * (len(recorded) - 1))]
                yield self.composite(base, frame, note if i >= 18 else None)
            for event in episode.get("interventions", []):
                if event["after_cycle"] != cycle:
                    continue
                if event["kind"] != "target_shift":
                    raise ValueError("This demo renderer currently annotates target_shift only")
                following = episode["history"][cycle]
                capture = following["before"]["perception"]["capture_id"]
                frame = next(f for f in records if f["observation"]["perception"]["capture_id"] == capture)
                dx, dy = event["delta_xy"]
                message = f"外部移动托盘：X {dx * 100:+.0f} cm，Y {dy * 100:+.0f} cm。"
                card = self.base(capture, cycle, message, banner="扰动测试 · 这是外部移动，不是机械臂动作")
                self.moments.append({"cycle": cycle, "event": "target_shift", "delta_xy": [dx, dy]})
                for _ in range(FPS * 2):
                    yield self.composite(card, frame, "扰动已发生：模型接下来会收到新的相机画面")
        last = episode["history"][-1]
        caption = "方块在托盘内保持稳定，夹爪松开并撤离；物理判定通过。" if episode["success"] else "本回合未通过物理成功判定，失败保留在实验记录中。"
        final = self.base(last["after"]["perception"]["capture_id"], len(episode["history"]), caption, final=True)
        for _ in range(FPS * 3):
            yield self.composite(final, records[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--cameras", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Output filename stem, without extension")
    parser.add_argument("--title", required=True)
    parser.add_argument("--font")
    parser.add_argument("--gif-speed", type=float, default=5, help="GIF speed relative to the MP4 (default: 5)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.gif_speed <= 20:
        parser.error("--gif-speed must be between 1 and 20")
    outputs = {ext: args.output.with_suffix(ext) for ext in (".mp4", ".gif", ".png", ".json")}
    if not args.overwrite and any(p.exists() for p in outputs.values()):
        parser.error("Output exists; choose another stem or use --overwrite")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw = args.episode.read_bytes()
    episode = json.loads(gzip.decompress(raw) if args.episode.suffix == ".gz" else raw)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
               "-video_size", f"{SIZE[0]}x{SIZE[1]}", "-framerate", str(FPS), "-i", "pipe:0", "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(outputs[".mp4"])]
    with zipfile.ZipFile(args.cameras) as archive:
        demo = Demo(episode, archive, args.title, find_font(args.font))
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        count = 0
        try:
            for image in demo.frames():
                if count == 0:
                    image.save(outputs[".png"])
                process.stdin.write(image.tobytes())
                count += 1
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError("MP4 encoding failed")
        finally:
            demo.renderer.close()
            if process.poll() is None:
                process.terminate()
                process.wait()
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(outputs[".mp4"]),
            "-filter_complex", f"setpts=PTS/{args.gif_speed},fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3",
            "-loop", "0", str(outputs[".gif"])], check=True)
        metadata = {"episode_id": episode["id"], "episode_file": args.episode.name,
            "episode_file_sha256": digest(raw), "camera_archive": args.cameras.name,
            "camera_archive_sha256": digest(args.cameras.read_bytes()),
            "input_images_verified": demo.checked, "frames": count, "fps": FPS,
            "duration_seconds": count / FPS, "original_wall_seconds": episode["wall_seconds"],
            "gif_speed": args.gif_speed, "gif_nominal_duration_seconds": count / FPS / args.gif_speed,
            "decision_panel": "Recorded candidate order, selected action and API latency. Only provider-returned probabilities are shown; absent probabilities are explicitly labelled.",
            "model_calls_during_export": 0, "physics_steps_during_export": 0,
            "playback": "Recorded qpos only; no interpolation; 1.2 seconds per decision; model waits omitted.",
            "camera_panels": "Exact archived decision-input PNGs, resized for display; final panel shows final observation.",
            "moments": demo.moments,
            "outputs": {p.name: {"sha256": digest(p.read_bytes()), "bytes": p.stat().st_size}
                        for ext, p in outputs.items() if ext != ".json"}}
        outputs[".json"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"episode": episode["id"], "duration": count / FPS,
                          "files": {p.name: p.stat().st_size for p in outputs.values()}}))


if __name__ == "__main__":
    main()
