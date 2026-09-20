"""Probability panels for recorded hierarchical control; never calls a policy."""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

from render_demo import Demo, SIZE, FPS, BG, INK, MUTED, GREEN, AMBER, wrap


SUBGOALS = {
    "approach": "接近与对齐", "grasp": "闭爪抓取", "lift": "抬升物体", "carry": "移向目标",
    "lower": "下放物体", "release": "松爪放置", "withdraw": "向上撤离", "finish": "完成",
}
AXES = ("negative", "hold", "positive")
FINGERS = ("open", "hold", "close")
CHOICES = {"negative": "负方向", "hold": "保持", "positive": "正方向", "open": "张开", "close": "闭合"}


class HierarchicalDemo(Demo):
    def __init__(self, episode, title, font):
        if (episode.get("task") != "transfer" or episode.get("control_mode") != "hierarchical"
                or episode.get("observation_mode") not in {"privileged", "rgbd"} or not episode.get("history")):
            raise ValueError("Expected a non-empty hierarchical transfer episode with measured coordinates")
        self.episode, self.title = episode, title
        self.model_label = episode.get("model") or episode["provider"]
        self.fonts = {size: ImageFont.truetype(font, size) for size in (15, 17, 20, 23, 28, 34)}
        self.checked = 0
        self.gif_speed = 5
        self.contact_lost = any(step["before"]["held"] and not step["after"]["held"]
                                and step["action"].get("gripper") != "open" for step in episode["history"])
        for step in episode["history"]:
            self.validate_decision(step["intent"], set(SUBGOALS))
            channels = step["decision"]["channel_decisions"]
            if set(channels) != {"x", "y", "z", "gripper"}:
                raise ValueError("Missing recorded motor channel")
            for name, answer in channels.items():
                self.validate_decision(answer, set(FINGERS if name == "gripper" else AXES))
                if answer["choice"] != step["action"]["channels"][name]:
                    raise ValueError("Recorded motor choice and executed command differ")
        self.setup_scene(episode)

    @staticmethod
    def validate_decision(answer, options):
        if answer["choice"] not in options:
            raise ValueError("Unknown recorded choice")
        probabilities = answer.get("probabilities") or {}
        if probabilities and (set(probabilities) != options or any(
                type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
                for p in probabilities.values()) or abs(sum(probabilities.values()) - 1) > .05):
            raise ValueError("Invalid recorded probability distribution")

    def choice_row(self, draw, x, y, width, label, option, answer, *, height=31):
        chosen = option == answer["choice"]
        colour = GREEN if chosen else MUTED
        if chosen:
            draw.rounded_rectangle((x - 5, y - 2, x + width + 5, y + height - 3), radius=5, fill="#e3f0e9")
        draw.text((x, y), label, font=self.fonts[17], fill=colour)
        probability = (answer.get("probabilities") or {}).get(option)
        score = f"{probability:.1%}" if probability is not None else "已选" if chosen else "—"
        draw.text((x + width - self.fonts[17].getlength(score), y), score, font=self.fonts[17], fill=colour)
        if probability is not None:
            draw.rectangle((x, y + height - 9, x + width, y + height - 6), fill="#e3eae5")
            if probability:
                draw.rectangle((x, y + height - 9, x + width * probability, y + height - 6), fill=colour)

    def caption(self, draw, text, x, y, width, *, size=20, colour=INK, max_lines=3):
        lines = wrap(text, self.fonts[size], width)
        if len(lines) > max_lines:
            raise ValueError("Caption would overflow its panel")
        for i, line in enumerate(lines):
            draw.text((x, y + i * (size + 9)), line, font=self.fonts[size], fill=colour)

    def panel(self, step, cycle, *, intro=False, final=False):
        episode = self.episode
        image = Image.new("RGB", SIZE, BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1280, 84), fill=INK)
        draw.text((24, 13), "行知 · EmbodiedJev", font=self.fonts[28], fill="white")
        draw.text((335, 17), self.title, font=self.fonts[23], fill="white")
        source = "仿真坐标 + 接触反馈" if episode["observation_mode"] == "privileged" else "RGB-D 检测坐标 + 接触反馈"
        subtitle = f"{self.model_label} · {source} · {episode['model_calls']} 次请求 · 实际 {episode['wall_seconds']:.2f} 秒"
        draw.text((24, 57), subtitle, font=self.fonts[17], fill="#d2e5db")
        draw.text((1110, 22), f"{cycle:02d} / {len(episode['history'])}", font=self.fonts[34], fill="white")
        draw.text((24, 102), "机械臂动作回放", font=self.fonts[23], fill=INK)
        draw.text((240, 107), "保存姿态重绘 · 此画面未发给模型", font=self.fonts[17], fill=MUTED)
        intent = step["intent"]
        draw.rounded_rectangle((816, 100, 1256, 430), radius=12, fill="white", outline="#d7e2d9")
        draw.text((834, 114), "01  模型选择子目标", font=self.fonts[23], fill=GREEN)
        draw.text((1110, 119), f"{intent['latency_ms']:.0f} ms", font=self.fonts[17], fill=MUTED)
        for i, (option, label) in enumerate(SUBGOALS.items()):
            self.choice_row(draw, 834, 157 + i * 32, 400, label, option, intent)
        channels = step["decision"]["channel_decisions"]
        draw.text((816, 449), "02  模型选择四个动作通道", font=self.fonts[23], fill=GREEN)
        draw.text((816, 482), f"一次请求 · {step['decision']['latency_ms']:.0f} ms · 各通道概率独立展示", font=self.fonts[17], fill=MUTED)
        for i, (name, label) in enumerate((("x", "X 方向"), ("y", "Y 方向"), ("z", "Z 方向"), ("gripper", "夹爪"))):
            x, y = 816 + i % 2 * 226, 516 + i // 2 * 164
            draw.rounded_rectangle((x, y, x + 214, y + 152), radius=9, fill="white", outline="#d7e2d9")
            draw.text((x + 13, y + 12), label, font=self.fonts[20], fill=INK)
            for j, option in enumerate(FINGERS if name == "gripper" else AXES):
                self.choice_row(draw, x + 13, y + 45 + j * 34, 187, CHOICES[option], option, channels[name])
        draw.rounded_rectangle((24, 738, 792, 1036), radius=12, fill="white", outline="#d7e2d9")
        subgoal = SUBGOALS[intent["choice"]]
        label = "最终动作" if final else "首步决策" if intro else "本步决策"
        draw.text((42, 756), f"{label}：{subgoal}", font=self.fonts[28], fill=INK)
        delta = step["action"]["delta_xyz"]
        action = "   ".join(f"{axis} {value * 1000:+.1f} mm" for axis, value in zip("XYZ", delta))
        draw.text((42, 808), action, font=self.fonts[23], fill=GREEN)
        draw.text((42, 846), f"夹爪：{CHOICES[channels['gripper']['choice']]}    每轴幅度上限：12 mm", font=self.fonts[20], fill=INK)
        draw.text((42, 890), "子目标、方向、夹爪由模型选；代码计算误差与步幅。", font=self.fonts[20], fill=MUTED)
        draw.text((42, 925), "有任务知识指导 · 无规则代选 · 模型未接收图像", font=self.fonts[20], fill=MUTED)
        draw.text((42, 978), "右侧数值来自真实 API；不合成整步或任务成功概率。", font=self.fonts[20], fill=MUTED)
        draw.rounded_rectangle((816, 858, 1256, 1036), radius=12, fill="white", outline="#d7e2d9")
        after = step["after"]
        lost = step["before"]["held"] and not after["held"] and step["action"].get("gripper") != "open"
        caught = not step["before"]["held"] and after["held"]
        if final:
            if not episode["success"]:
                title, message = "本局未完成", "没有满足物理成功条件；保留本局失败。"
            elif self.contact_lost:
                title, message = "终态通过，过程曾失抓", "发生过非主动松爪的接触丢失；最终方块稳定、夹爪张开并撤离。"
            else:
                title, message = "终态通过", "方块在目标上稳定，夹爪张开并撤离；小样本不代表通用成功率。"
        elif intro:
            title, message = "每步重新判断", "先选子目标，再选四个动作通道；执行短步后读取新的状态。"
        elif lost:
            title, message = "下放时失去抓持", "双侧接触消失，方块落到托盘上。下一步模型会读到新状态。"
        elif caught:
            title, message = "建立双侧接触", "本步闭爪后夹住方块；接下来继续根据实际接触选择子目标。"
        else:
            title = "执行后物理反馈"
            message = ("当前持物" if after["held"] else "当前未持物") + (" · 目标有支撑接触" if after["support_contact"] else " · 目标尚无支撑接触")
            message += f"。末端高度 {after['tcp'][2] * 1000:.0f} mm。"
        draw.text((833, 875), title, font=self.fonts[23], fill=AMBER if lost or final else GREEN)
        self.caption(draw, message, 833, 913, 405, max_lines=4)
        draw.rectangle((24, 1049, 1256, 1053), fill="#d5e1d8")
        draw.rectangle((24, 1049, 24 + int(1232 * cycle / len(episode['history'])), 1053), fill=GREEN)
        draw.text((24, 1061), f"真实记录回放 · 已省略 API 等待 · GIF 为 MP4 的 {self.gif_speed:g} 倍速 · 回放速度不代表实际推理速度", font=self.fonts[15], fill=MUTED)
        return image

    def frames(self):
        episode, records = self.episode, self.episode["frames"]
        first = episode["history"][0]
        base = self.panel(first, 0, intro=True)
        for _ in range(FPS * 2):
            yield self.composite(base, records[0])
        for step in episode["history"]:
            cycle = step["cycle"]
            recorded = [frame for frame in records if frame["cycle"] == cycle]
            if not recorded:
                raise ValueError(f"No recorded motion for cycle {cycle}")
            base = self.panel(step, cycle)
            lost = step["before"]["held"] and not step["after"]["held"] and step["action"].get("gripper") != "open"
            caught = not step["before"]["held"] and step["after"]["held"]
            if lost or caught:
                self.moments.append({"cycle": cycle, "event": "contact_lost" if lost else "grasp_contact"})
            for i in range(24):
                frame = recorded[round(i / 23 * (len(recorded) - 1))]
                note = "下放中失抓：方块落到托盘上" if lost else "闭爪后建立双侧接触" if caught else None
                yield self.composite(base, frame, note if i >= 18 else None)
        final = self.panel(episode["history"][-1], len(episode["history"]), final=True)
        for _ in range(FPS * 3):
            yield self.composite(final, records[-1], "终态通过：方块稳定、夹爪张开并撤离" if episode["success"] else "本局未完成")
