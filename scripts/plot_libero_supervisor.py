"""Plot one completed, paired LIBERO v2 development experiment from public records.

Reads only summary.json, COSTS.json and replay-diagnostics.json. No simulator,
credentials or model connection is loaded. Install matplotlib before rendering.

Figure contract: a quantitative 2x2 grid of observed task outcomes, total wall
time, total cost of both model layers, and full object-progress traces. This is one
development trial per mode, with no uncertainty estimate or significance test.
Lower spending on a failed task must not be presented as improved efficiency.
Export: 183 mm wide, white background, editable SVG/PDF text and 300 dpi PNG.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "libero-rgbd-candidate-supervisor-v2"
MODES = ("gpt6", "gpt6-jev")
LABELS = {"gpt6": "纯 GPT-6", "gpt6-jev": "GPT-6 + Jev"}
COLORS = {"gpt6": "#688648", "gpt6-jev": "#8972A8"}
STATUSES = {"success": "成功", "step_budget": "未完成 · 步数耗尽",
            "time_budget": "未完成 · 时间耗尽", "request_budget": "未完成 · 请求耗尽",
            "cost_budget": "未完成 · 费用上限"}
INK, MUTED, GRID = "#213746", "#627480", "#DFE6E8"


def number(value, name, *, nonnegative=True):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or nonnegative and value < 0):
        raise ValueError("Invalid measured value: " + name)
    return value


def cost_value(metrics):
    known = number(metrics["known_cost_subtotal_usd"], "known cost")
    exact = metrics.get("estimated_usd")
    if exact is None:
        return known, True
    exact = number(exact, "estimated cost")
    if not math.isclose(exact, known, abs_tol=1e-8, rel_tol=1e-8):
        raise ValueError("Exact cost and known subtotal differ")
    return exact, False


def money(value, lower_bound=False):
    return ("≥" if lower_bound else "") + f"${value:.4f}"


def vector(value, length, name):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("Invalid measured vector: " + name)
    return [number(item, name, nonnegative=False) for item in value]


def progress_trace(diagnostic, task):
    states = diagnostic["states"]
    if task == "microwave":
        names = [name for name in states[0]["joints"] if "microjoint" in name]
        if len(names) != 1:
            raise ValueError("Expected one unambiguous microwave hinge")
        joint = names[0]
        angles = [math.degrees(number(s["joints"][joint], "door angle", nonnegative=False)) for s in states]
        return {"door_joint": joint, "door_angle_degrees": angles, "progress_values": angles}
    names = list(states[0].get("object_progress", {}))
    if len(names) != 1:
        raise ValueError("Expected one unambiguous tracked plate object")
    name = names[0]
    target = diagnostic["object_targets"][name]
    if not target.get("position_definition") or not target.get("distance_definition"):
        raise ValueError("Object-progress geometry definitions are required")
    records = []
    for state in states:
        if set(state.get("object_progress", {})) != {name}:
            raise ValueError("Tracked object must be present at every environment step")
        record = state["object_progress"][name]
        if record["status"] != "available" or not isinstance(record["target_site"], str) or not record["target_site"]:
            raise ValueError("Plate target-region diagnostics are unavailable")
        position = vector(record["body_position_world_m"], 3, "plate world position")
        center = vector(record["target_center_world_m"], 3, "target-region center")
        half_size = vector(record["target_region_half_size_m"], 3, "target-region half size")
        bounds = record["target_region_world_xy_bounds_m"]
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError("Target-region world XY bounds are required")
        bounds = [vector(bound, 2, "target-region world XY bounds") for bound in bounds]
        distance = number(record["distance_to_target_region_xy_m"], "plate-to-region distance")
        records.append({"step": state["step"], "body_position_world_m": position,
            "target_center_world_m": center, "target_site": record["target_site"],
            "target_region_half_size_m": half_size, "target_region_world_xy_bounds_m": bounds,
            "distance_to_target_region_xy_m": distance})
    initial = records[0]
    for record in records:
        if record["target_site"] != initial["target_site"] or record["target_site"] != target["target_site"]:
            raise ValueError("Plate target region must remain static during replay")
        for key in ("target_center_world_m", "target_region_half_size_m"):
            if any(not math.isclose(a, b, abs_tol=1e-9) for a, b in zip(record[key], initial[key])):
                raise ValueError("Plate target-region geometry changed during replay")
        if any(not math.isclose(a, b, abs_tol=1e-9) for a, b in zip(
                sum(record["target_region_world_xy_bounds_m"], []), sum(initial["target_region_world_xy_bounds_m"], []))):
            raise ValueError("Plate target-region bounds changed during replay")
    return {"tracked_object": name, "object_trace": records,
            "object_targets": diagnostic.get("object_targets", {}),
            "progress_values": [record["distance_to_target_region_xy_m"] * 1000 for record in records]}


def load_sources(directory):
    files = {name: directory / name for name in ("summary.json", "COSTS.json", "replay-diagnostics.json")}
    content = {name: path.read_bytes() for name, path in files.items()}
    summary, costs, replay = (json.loads(content[name]) for name in files)
    if not all(summary.get(key) is True for key in
               ("complete", "sources_unchanged", "publication_complete", "paired_comparison_complete")):
        raise ValueError("A completed, source-verified and fully paired publication is required")
    if summary.get("pending") or costs.get("pending_episodes"):
        raise ValueError("Pending or excluded episodes cannot be plotted as a final pair")
    protocol = summary["protocol"]
    if protocol["version"] != PROTOCOL or set(protocol["modes"]) != set(MODES):
        raise ValueError("Expected both modes under the candidate-supervisor-v2 protocol")
    cases = protocol["manifest"]["cases"]
    if len(cases) != 1:
        raise ValueError("This figure requires one paired LIBERO development case")
    case = cases[0]
    task = {("libero_90", 33): "microwave", ("libero_goal", 5): "push-plate"}.get((case.get("suite"), case.get("task_id")))
    if task is None:
        raise ValueError("Supported progress diagnostics: LIBERO-90 microwave 33 or LIBERO-Goal push-plate 5")
    if len(summary["episodes"]) != 2 or len(replay["episodes"]) != 2:
        raise ValueError("Expected exactly two episodes and two replay diagnostics")
    episodes = {e["mode"]: e for e in summary["episodes"]}
    diagnostics = {e["mode"]: e for e in replay["episodes"]}
    if set(episodes) != set(MODES) or set(diagnostics) != set(MODES):
        raise ValueError("Each mode must occur exactly once")
    a, b = (episodes[mode]["metadata"] for mode in MODES)
    for key in ("case", "initial_state_sha256", "settled_state_sha256", "versions",
                "libero_revision", "camera_size", "camera_transform", "controller",
                "action_output_min", "action_output_max"):
        if a[key] != b[key]:
            raise ValueError("Paired setup mismatch: " + key)
    rows = []
    for mode in MODES:
        episode, diagnostic = episodes[mode], diagnostics[mode]
        metrics = costs["methods"][mode]
        if metrics != summary["methods"][mode]:
            raise ValueError("Summary and cost table disagree for " + mode)
        if episode["id"] != case["id"] or diagnostic["id"] != case["id"]:
            raise ValueError("Episode or diagnostic case mismatch")
        if episode["case"] != case or episode["protocol"] != PROTOCOL:
            raise ValueError("Episode case/protocol mismatch")
        if type(episode["success"]) is not bool or episode["status"] not in STATUSES:
            raise ValueError("Episode must have a completed outcome")
        if episode["success"] != (episode["status"] == "success") or diagnostic["success"] != episode["success"]:
            raise ValueError("Episode success, status and replay outcome disagree")
        if number(diagnostic["max_tcp_replay_error_m"], "replay error") > 1e-8:
            raise ValueError("Replay trajectory does not match recorded execution")
        if task == "push-plate" and (diagnostic.get("replay_complete") is not True
                                     or diagnostic.get("replay_verified_steps") != episode["steps"]):
            raise ValueError("Every plate replay step must be verified against the recorded trajectory")
        if metrics["episodes"] != 1 or metrics["successes"] != int(episode["success"]):
            raise ValueError("Aggregate must describe this single episode")
        for key in ("wall_seconds", "setup_seconds", "steps"):
            if not math.isclose(number(metrics[key], key), number(episode[key], key), abs_tol=1e-8):
                raise ValueError("Aggregate and episode differ: " + key)
        if metrics["requests"] != episode["metrics"]["requests"]:
            raise ValueError("Aggregate and episode request counts differ")
        value, lower = cost_value(metrics)
        episode_value, episode_lower = cost_value(episode["metrics"])
        if lower != episode_lower or not math.isclose(value, episode_value, abs_tol=1e-8):
            raise ValueError("Aggregate and episode costs differ")
        providers = metrics["by_provider"]
        if set(providers) != ({"chat", "jev"} if mode == "gpt6-jev" else {"chat"}):
            raise ValueError("Both model layers must be included in provider accounting")
        if sum(number(p["requests"], "provider requests") for p in providers.values()) != metrics["requests"]:
            raise ValueError("Provider request totals differ")
        if not math.isclose(sum(cost_value(p)[0] for p in providers.values()), value, abs_tol=1e-8):
            raise ValueError("Provider cost totals differ")
        states = diagnostic["states"]
        if [s["step"] for s in states] != list(range(episode["steps"] + 1)):
            raise ValueError("Object trace must contain every recorded environment step")
        progress = progress_trace(diagnostic, task)
        rows.append({"mode": mode, "label": LABELS[mode], "success": episode["success"],
            "status": episode["status"], "steps": episode["steps"], "requests": metrics["requests"],
            "wall_seconds": episode["wall_seconds"], "setup_seconds": episode["setup_seconds"],
            "rollout_seconds": number(episode["rollout_seconds"], "rollout time"),
            "cost_usd": value, "cost_is_lower_bound": lower,
            "provider_costs": {provider: {"usd": cost_value(item)[0], "lower_bound": cost_value(item)[1],
                                         "requests": item["requests"]} for provider, item in providers.items()},
            "environment_steps": [s["step"] for s in states], **progress})
    if not math.isclose(rows[0]["progress_values"][0], rows[1]["progress_values"][0], abs_tol=1e-7):
        raise ValueError("Initial object-progress measurements differ")
    if task == "push-plate":
        if rows[0]["tracked_object"] != rows[1]["tracked_object"]:
            raise ValueError("Paired replay tracked different objects")
        first, second = (row["object_trace"][0] for row in rows)
        if first["target_site"] != second["target_site"]:
            raise ValueError("Paired plate target sites differ")
        for key in ("body_position_world_m", "target_center_world_m", "target_region_half_size_m"):
            if any(not math.isclose(a, b, abs_tol=1e-9) for a, b in zip(first[key], second[key])):
                raise ValueError("Paired initial object geometry differs: " + key)
        if any(not math.isclose(a, b, abs_tol=1e-9) for a, b in zip(
                sum(first["target_region_world_xy_bounds_m"], []), sum(second["target_region_world_xy_bounds_m"], []))):
            raise ValueError("Paired target-region bounds differ")
    retry = costs.get("retry_attempts")
    retry_value, retry_lower = cost_value(retry) if retry else (0., False)
    total = number(costs["total_known_cost_including_retries_usd"], "all-attempt cost")
    if not math.isclose(total, sum(row["cost_usd"] for row in rows) + retry_value, abs_tol=1e-8):
        raise ValueError("All-attempt cost does not reconcile with displayed pair and retries")
    return {"format": "libero-supervisor-v2-figure-source-v1", "protocol": PROTOCOL, "case": case, "task": task,
        "task_label": "关微波炉" if task == "microwave" else "将盘子推到炉前",
        "input_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in content.items()},
        "paired_initial_state_sha256": a["initial_state_sha256"],
        "paired_settled_state_sha256": a["settled_state_sha256"],
        "rows": rows, "retry_cost_usd": retry_value, "retry_cost_is_lower_bound": retry_lower,
        "all_attempts_known_cost_usd": total,
        "all_attempts_cost_is_lower_bound": retry_lower or any(row["cost_is_lower_bound"] for row in rows),
        "figure_contract": {"archetype": "quantitative grid", "backend": "python",
            "conclusion": "Interpret each measured outcome together with spending, time and actual object progress.",
            "n": "One development episode per mode; same initial state and budget.",
            "statistics": "Raw observations only; no averaging, confidence intervals or significance tests.",
            "trace": "All environment steps; radians to degrees or metres to millimetres; no smoothing or resampling.",
            "truth_boundary": "Object positions and joint angles are post-hoc diagnostics, never model inputs.",
            "cost": "Both model layers; retries in footnote only, excluded from per-mode bars.",
            "exports": "183 mm wide; editable SVG/PDF; 300 dpi PNG."}}


def configure_plotting(font_path=None):
    try:
        import matplotlib
    except ImportError as exc:
        raise RuntimeError("matplotlib is required: install it into the Python environment used to run this script") from exc
    matplotlib.use("Agg")
    from matplotlib import font_manager
    import matplotlib.pyplot as plt

    candidates = [font_path] if font_path else [Path(p) for p in (
        "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf")]
    selected = None
    required = "关微波炉门开度结果费用秒成功观测谨慎盘子参考点距目标区域"
    for path in candidates:
        if path and path.is_file():
            glyphs = font_manager.get_font(str(path)).get_charmap()
            if all(ord(character) in glyphs for character in required):
                selected = path
                break
    if selected is None:
        raise RuntimeError("A Chinese font is required; pass --font PATH to PingFang, Noto Sans CJK or another CJK font")
    font_manager.fontManager.addfont(str(selected))
    family = font_manager.FontProperties(fname=selected).get_name()
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": [family, "Arial", "DejaVu Sans"],
        "font.size": 8, "axes.titlesize": 10, "axes.labelsize": 8, "xtick.labelsize": 7,
        "ytick.labelsize": 8, "text.color": INK, "axes.labelcolor": INK,
        "xtick.color": MUTED, "ytick.color": INK, "axes.edgecolor": GRID,
        "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": .65,
        "legend.frameon": False, "svg.fonttype": "none", "pdf.fonttype": 42,
        "axes.unicode_minus": False, "text.parse_math": False,
    })
    return plt, family


def plot(source, output, *, font_path=None):
    plt, family = configure_plotting(font_path)
    rows = source["rows"]
    plate = source["task"] == "push-plate"
    fig = plt.figure(figsize=(7.2, 6.25), facecolor="white")
    grid = fig.add_gridspec(2, 2, left=.11, right=.97, bottom=.22, top=.82, hspace=.58, wspace=.43)
    axes = [fig.add_subplot(grid[i // 2, i % 2]) for i in range(4)]
    fig.text(.045, .958, source["task_label"] + " · GPT-6 / Jev 候选选择对照", fontsize=13, weight="bold", va="top")
    case = source["case"]
    fig.text(.045, .908, f"同一初态 {case['init_index']} · seed {case['seed']} · 每组 1 局开发实验 · v2 共享数值伺服", fontsize=8, color=MUTED)

    for index, (axis, title) in enumerate(zip(axes, ("实际结果", "总耗时", "两层总费用", "盘子推进进展" if plate else "门开度进展"))):
        axis.set_title(title, loc="left", pad=12, weight="bold")
        axis.text(-.22, 1.075, chr(ord("a") + index), transform=axis.transAxes, weight="bold", fontsize=11)

    axis = axes[0]
    axis.axis("off")
    for i, row in enumerate(rows):
        y = .82 - .49 * i
        axis.text(.0, y, row["label"], transform=axis.transAxes, weight="bold", color=COLORS[row["mode"]], fontsize=9)
        axis.text(.0, y-.17, STATUSES[row["status"]], transform=axis.transAxes, fontsize=11,
                  weight="bold", color=INK)
        axis.text(.0, y-.31, f"{row['steps']} 个环境步 · {row['requests']} 次请求", transform=axis.transAxes, fontsize=8, color=MUTED)

    for axis, field, xlabel in ((axes[1], "wall_seconds", "实际墙钟时间（秒，含初始化和全部等待）"),
                               (axes[2], "cost_usd", "费用估算（USD，含两层全部请求）")):
        values = [row[field] for row in rows]
        axis.set_axisbelow(True)
        axis.grid(axis="x", color=GRID, linewidth=.6)
        axis.spines["left"].set_visible(False)
        axis.barh([0, 1], values, color=[COLORS[row["mode"]] for row in rows], height=.42)
        for i, row in enumerate(rows):
            value = values[i]
            label = f"{value:.1f}s" if field == "wall_seconds" else money(value, row["cost_is_lower_bound"])
            if field == "cost_usd" and row["cost_is_lower_bound"]:
                axis.patches[i].set_hatch("///")
                axis.patches[i].set_edgecolor(INK)
            axis.text(value + max(max(values), .001)*.035, i, label, va="center", fontsize=8, weight="bold")
        axis.set_yticks([0, 1], [row["label"] for row in rows])
        axis.set_ylim(1.65, -.65)
        axis.set_xlim(0, max(max(values), .001)*1.4)
        axis.set_xlabel(xlabel, labelpad=8, fontsize=7)
        axis.tick_params(axis="y", length=0)

    axis = axes[3]
    all_progress = [value for row in rows for value in row["progress_values"]]
    for i, row in enumerate(rows):
        axis.plot(row["environment_steps"], row["progress_values"], color=COLORS[row["mode"]],
                  linewidth=1.6, linestyle="-" if i == 0 else "--", label=row["label"])
        axis.scatter([row["environment_steps"][-1]], [row["progress_values"][-1]],
                     s=22, marker="o" if i == 0 else "s", color=COLORS[row["mode"]], zorder=3)
    axis.axhline(0, color=MUTED, linewidth=.6, linestyle=":")
    margin = max(1., max(all_progress) - min(0., min(all_progress)))
    axis.set_ylim(min(0., min(all_progress)) - margin*.04, max(0., max(all_progress)) + margin*.16)
    axis.set_xlim(0, max(row["steps"] for row in rows)*1.03 or 1)
    axis.set_xlabel("环境步数（不代表实际耗时）", fontsize=7, labelpad=8)
    axis.set_ylabel("盘子参考点距目标区（mm）" if plate else "门铰链角度（°；0° 为关闭）", fontsize=7)
    axis.grid(color=GRID, linewidth=.6)
    axis.legend(loc="upper right", fontsize=7, handlelength=2)

    retry = money(source["retry_cost_usd"], source["retry_cost_is_lower_bound"])
    total = money(source["all_attempts_known_cost_usd"], source["all_attempts_cost_is_lower_bound"])
    footnotes = [
        "每组 n=1，无误差条；未完成任务的低开销不能解释为效率提升。",
        f"重试额外费用 {retry}，不并入模式柱；本次所有尝试合计 {total}。≥ 表示用量不完整。",
        ("参考点为物体坐标原点；事后复现的距离为 0 不等于成功，成功由环境判定。" if plate
         else "成功由环境判定；门开度来自事后动作复现，未输入模型。费用按冻结单价估算。"),
    ]
    for i, note in enumerate(footnotes):
        fig.text(.045, .108 - i*.027, note, fontsize=7, color=MUTED)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output.with_suffix(".png"), dpi=300, facecolor="white")
    fig.savefig(output.with_suffix(".svg"), facecolor="white")
    fig.savefig(output.with_suffix(".pdf"), facecolor="white")
    plt.close(fig)
    return family


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "docs/results/libero-supervisor-v2")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/media/libero-supervisor-v2-summary", help="Output stem without extension")
    parser.add_argument("--font", type=Path, help="CJK font path; defaults to supported macOS/Linux fonts")
    parser.add_argument("--check-only", action="store_true", help="Validate final source data without importing plotting packages or writing files")
    args = parser.parse_args()
    source = load_sources(args.results)
    if args.check_only:
        print("Validated completed paired v2 records, full object-progress traces and all-attempt cost accounting")
        return
    family = plot(source, args.output, font_path=args.font)
    source["rendered_font_family"] = family
    (args.results / "figure-source.json").write_text(json.dumps(source, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(f"Exported {args.output}.{{png,svg,pdf}} and figure-source.json; font={family}")


if __name__ == "__main__":
    main()
