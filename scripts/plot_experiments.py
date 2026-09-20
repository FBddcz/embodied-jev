"""Plot measured episode outcomes, latency and usage from a benchmark report.

Install .[plots], then run:
  python scripts/plot_experiments.py --report docs/results/REPORT.json

All attempted trials and API latency observations are retained. This command
reads report data only; it does not load credentials or call a model.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
import numpy as np

TASKS = {"transfer": "Transfer", "stack": "Stack", "barrier": "Barrier"}
TEAL, ORANGE, INK = "#167D6B", "#C7813F", "#253738"


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid measured value: {label}")
    return value


def read_rows(report):
    rows = report["episodes"]
    if not rows:
        raise ValueError("The report contains no measured trials")
    seen = set()
    for row in rows:
        key = (row["task"], row["seed"])
        if row["task"] not in TASKS or key in seen:
            raise ValueError("Unexpected task or duplicate task/seed trial")
        seen.add(key)
        if type(row["success"]) is not bool:
            raise ValueError("Success must come from the physical result")
        for field in ("cycles", "model_calls", "input_tokens", "output_tokens", "wall_seconds"):
            number(row[field], field)
        for latency in row["model_latency_ms"]:
            number(latency, "model_latency_ms")
        if len(row["model_latency_ms"]) != row["model_calls"]:
            raise ValueError("Each attempted API call must have a recorded latency")
    return sorted(rows, key=lambda row: (list(TASKS).index(row["task"]), row["seed"]))


def write_sources(rows, output):
    fields = ["task", "seed", "status", "success", "cycles", "model_calls", "input_tokens",
              "output_tokens", "wall_seconds", "sim_seconds", "resolved_model", "episode_file"]
    with output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)
    with output.with_name(output.name + "-latencies.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["task", "seed", "call_index", "latency_seconds"])
        for row in rows:
            for index, latency in enumerate(row["model_latency_ms"], 1):
                writer.writerow([row["task"], row["seed"], index, latency / 1000])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Output filename stem; defaults to the report stem")
    parser.add_argument("--title", default="GPT-6 Astra · embodied decision trials")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = read_rows(report)
    output = args.output or args.report.with_suffix("")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_sources(rows, output)

    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
        "font.size": 7, "axes.titlesize": 8, "axes.labelsize": 7,
        "axes.titleweight": "bold", "text.color": INK,
        "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": 0.6, "legend.frameon": False,
        "svg.fonttype": "none", "pdf.fonttype": 42,
    })
    # Final report width: approximately 183 mm. All formats use this layout.
    fig = plt.figure(figsize=(7.2, 6.22), facecolor="white")
    grid = fig.add_gridspec(2, 2, left=.11, right=.97, bottom=.16, top=.84,
                           hspace=.53, wspace=.38, height_ratios=[1, 1])
    axes = [fig.add_subplot(grid[index // 2, index % 2]) for index in range(4)]
    successes = sum(row["success"] for row in rows)
    fig.text(.055, .965, args.title, size=12, weight="bold", va="top")
    fig.text(.055, .915, f"{successes}/{len(rows)} trials completed  |  measured outcomes, time and API usage",
             size=8, color=TEAL)

    task_names = [task for task in TASKS if any(row["task"] == task for row in rows)]
    seeds = sorted({row["seed"] for row in rows})
    lookup = {(row["task"], row["seed"]): row for row in rows}
    ax = axes[0]
    ax.set_title("a  Task completion", loc="left", pad=12)
    for y, task in enumerate(task_names):
        for x, seed in enumerate(seeds):
            row = lookup.get((task, seed))
            color = "#EDF1F0" if row is None else "#D6EEE6" if row["success"] else "#F4E3D0"
            ax.add_patch(Rectangle((x - .47, y - .45), .94, .9, facecolor=color, edgecolor="white"))
            if row is None:
                label = "Not run"
            else:
                label = "PASS" if row["success"] else row["status"].upper()
                label += f"\n{row['cycles']} actions"
            ax.text(x, y, label, ha="center", va="center", fontsize=6.5)
    ax.set(xlim=(-.5, len(seeds) - .5), ylim=(len(task_names) - .5, -.5),
           xticks=range(len(seeds)), xticklabels=[f"Seed {seed}" for seed in seeds],
           yticks=range(len(task_names)), yticklabels=[TASKS[task] for task in task_names])
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax = axes[1]
    ax.set_title("b  Elapsed time per trial", loc="left", pad=12)
    markers = ["o", "s", "^"]
    for index, seed in enumerate(seeds):
        data = [row for row in rows if row["seed"] == seed]
        positions = [task_names.index(row["task"]) + (index - (len(seeds) - 1) / 2) * .16 for row in data]
        ax.scatter(positions, [row["wall_seconds"] for row in data], s=24,
                   marker=markers[index % len(markers)], color=TEAL,
                   facecolors=TEAL if index != 1 else "white", linewidths=.8, label=f"Seed {seed}", zorder=3)
    ax.set(xticks=range(len(task_names)), xticklabels=[TASKS[task] for task in task_names],
           ylabel="Elapsed seconds (includes API wait)", ylim=(0, max(row["wall_seconds"] for row in rows) * 1.2 or 1))
    ax.grid(axis="y", color="#E6ECEA", linewidth=.5)
    ax.legend(fontsize=6, ncol=len(seeds), loc="upper left", borderaxespad=0, handletextpad=.3, columnspacing=.7)

    short = [f"{TASKS[row['task']][0]}{row['seed']}" for row in rows]
    ax = axes[2]
    ax.set_title("c  Every recorded API call", loc="left", pad=12)
    for index, row in enumerate(rows):
        values = np.asarray(row["model_latency_ms"], dtype=float) / 1000
        if len(values):
            # Deterministic horizontal spacing changes positions only, not data.
            offsets = np.linspace(-.18, .18, len(values))
            ax.scatter(index + offsets, values, s=7, color=TEAL, alpha=.48, linewidths=0)
            ax.plot([index - .25, index + .25], [np.median(values)] * 2, color=INK, linewidth=1)
    ax.set(xticks=range(len(rows)), xticklabels=short, ylabel="API latency (seconds)", ylim=(0, None))
    ax.grid(axis="y", color="#E6ECEA", linewidth=.5)
    ax.text(0, -.22, "Dots: calls · line: median within each trial", transform=ax.transAxes, size=6)

    ax = axes[3]
    ax.set_title("d  Token usage reported by the API", loc="left", pad=12)
    input_tokens = np.asarray([row["input_tokens"] for row in rows], dtype=float) / 1000
    output_tokens = np.asarray([row["output_tokens"] for row in rows], dtype=float) / 1000
    ax.bar(range(len(rows)), input_tokens, width=.62, color=TEAL, label="Input")
    ax.bar(range(len(rows)), output_tokens, bottom=input_tokens, width=.62, color=ORANGE, label="Output")
    ax.set(xticks=range(len(rows)), xticklabels=short, ylabel="Tokens (thousands)",
           ylim=(0, max(input_tokens + output_tokens) * 1.25 or 1))
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#E6ECEA", linewidth=.5)
    ax.legend(handles=[Patch(color=TEAL, label="Input"), Patch(color=ORANGE, label="Output")],
              fontsize=6, loc="upper left", ncol=2)
    ax.text(0, -.22, "T: transfer · S: stack · B: barrier; digit: seed", transform=ax.transAxes, size=6)

    calls = sum(row["model_calls"] for row in rows)
    tokens_in = sum(row["input_tokens"] for row in rows)
    tokens_out = sum(row["output_tokens"] for row in rows)
    fig.text(.055, .055, f"{calls} API calls · {tokens_in:,} input / {tokens_out:,} output tokens. All attempted trials retained.", size=6)
    fig.text(.055, .027, "Known geometry/contact states; bounded action menu. Descriptive development trials, not a general model ranking.", size=6)
    fig.savefig(output.with_suffix(".svg"), facecolor="white")
    fig.savefig(output.with_suffix(".pdf"), facecolor="white")
    fig.savefig(output.with_suffix(".png"), dpi=600, facecolor="white")
    plt.close(fig)
    print(json.dumps({"trials": len(rows), "successes": successes, "calls": calls,
                      "input_tokens": tokens_in, "output_tokens": tokens_out,
                      "outputs": [output.with_suffix('.' + ext).name for ext in ('svg', 'pdf', 'png', 'csv')]}, indent=2))


if __name__ == "__main__":
    main()
