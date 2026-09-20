"""Validated parameters for the three built-in physical task templates."""
from __future__ import annotations

import math

SCENE_DEFAULTS = {
    "source_xy": None,
    "target_xy": [.43, .18],
    "barrier_height": .11,
}
SCENE_LIMITS = {
    "name_max_length": 80,
    "x": [.30, .58],
    "y": [-.28, .28],
    "barrier_height": [.02, .16],
    "minimum_gap": .01,
}
SCENE_NAMES = {"transfer": "搬运入盘", "stack": "方块堆叠", "barrier": "越障搬运"}
SOURCE_CENTER = (.43, -.17)
SOURCE_JITTER = .025
BARRIER_XY = (.43, 0.)
BARRIER_HALF_XY = (.115, .018)


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数字")
    return float(value)


def _xy(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} 必须包含 X、Y 两个坐标")
    point = [_number(v, label) for v in value]
    for axis, coordinate in zip(("x", "y"), point):
        low, high = SCENE_LIMITS[axis]
        if not low <= coordinate <= high:
            raise ValueError(f"{label} 的 {axis.upper()} 必须在 {low}–{high} 米之间")
    return point


def _overlap(first, first_half, second, second_half):
    gap = SCENE_LIMITS["minimum_gap"]
    return all(abs(a - b) < ra + rb + gap
               for a, ra, b, rb in zip(first, first_half, second, second_half))


def validate_scene_config(task, scene_config=None):
    """Return a detached, normalized config; never accept goals or executable scene data.

    A missing/null source preserves seeded placement. An explicit source is exact.
    The bounds are an initial envelope; RobotWorld also checks custom IK poses.
    """
    if task not in SCENE_NAMES:
        raise ValueError("Unknown task")
    if scene_config is None:
        scene_config = {}
    if not isinstance(scene_config, dict):
        raise ValueError("scene_config 必须是 JSON 对象")
    if set(scene_config) - {"name", "source_xy", "target_xy", "barrier_height"}:
        raise ValueError("scene_config 仅支持 name、source_xy、target_xy、barrier_height")
    name = scene_config.get("name", SCENE_NAMES[task])
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > SCENE_LIMITS["name_max_length"]:
        raise ValueError("场景名称必须为 1–80 个字符")
    source = scene_config.get("source_xy")
    source = None if source is None else _xy(source, "source_xy")
    target = _xy(scene_config.get("target_xy", SCENE_DEFAULTS["target_xy"]), "target_xy")
    if task == "barrier":
        height = _number(scene_config.get("barrier_height", SCENE_DEFAULTS["barrier_height"]), "barrier_height")
        if not SCENE_LIMITS["barrier_height"][0] <= height <= SCENE_LIMITS["barrier_height"][1]:
            raise ValueError("barrier_height 必须在 0.02–0.16 米之间")
    else:
        if scene_config.get("barrier_height") is not None:
            raise ValueError("只有越障搬运模板支持 barrier_height")
        height = None

    # Include the entire seeded source envelope so every seed fits a saved preset.
    source_center = SOURCE_CENTER if source is None else source
    source_radius = .02 + (SOURCE_JITTER if source is None else 0.)
    source_half = (source_radius, source_radius)
    target_radius = .03 if task == "stack" else .082
    target_half = (target_radius, target_radius)
    if _overlap(source_center, source_half, target, target_half):
        raise ValueError("源方块与目标区域重叠或间距不足，请至少留出 1 厘米间隔")
    if task == "barrier":
        if (_overlap(source_center, source_half, BARRIER_XY, BARRIER_HALF_XY)
                or _overlap(target, target_half, BARRIER_XY, BARRIER_HALF_XY)):
            raise ValueError("源方块或目标托盘与障碍重叠或间距不足")
        if source_center[1] * target[1] >= 0:
            raise ValueError("越障模板的源方块与目标必须位于障碍 Y=0 的两侧")
        jitter = SOURCE_JITTER if source is None else 0.
        for dx in (-jitter, jitter):
            for dy in (-jitter, jitter):
                sx, sy = source_center[0] + dx, source_center[1] + dy
                crossing_x = sx + (target[0] - sx) * (-sy / (target[1] - sy))
                if abs(crossing_x - BARRIER_XY[0]) > BARRIER_HALF_XY[0]:
                    raise ValueError("源到目标的路径绕过了障碍，请让路径穿过障碍宽度范围")
    return {"name": name.strip(), "source_xy": source, "target_xy": target, "barrier_height": height}
