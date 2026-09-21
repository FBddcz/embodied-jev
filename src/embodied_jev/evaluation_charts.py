"""Strict paired benchmark summaries and figures from recorded evaluation reports.

No simulator or model is invoked here. Missing measurements remain unavailable;
the renderer never manufactures measurements to complete a panel.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics


PROTOCOL_KEYS = ("max_steps", "max_calls", "timeout", "threshold", "action_scale",
                 "control_mode", "observation_mode", "action_repeat")
BASELINES = {"baseline", "scripted", "noop"}
COLORS = ("#427A99", "#AE7D57", "#7C709F", "#687F75")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _number(value, field, *, integer=False, nullable=False):
    if nullable and value is None:
        return None
    if (type(value) not in ((int,) if integer else (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"Invalid nonnegative measurement: {field}")
    return value


def _task(case):
    return case.get("task") or f"{case['suite']}/{case['task_id']}"


def percentile(values, fraction):
    """Linear empirical quantile; interpolation is explicit for small samples."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def wilson(successes, n):
    if not n:
        return None
    z, p = 1.959963984540054, successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0., center - radius), min(1., center + radius)]


def _success_summary(rows):
    scored = [r for r in rows if r["success"] is not None]
    wins = sum(r["success"] for r in scored)
    missing = sum(not r["recorded"] for r in rows)
    unscored = len(rows) - missing - len(scored)
    # Runtime errors retain their official outcome, but an interrupted sample
    # must not be advertised as a complete benchmark score.
    complete = len(scored) == len(rows) and not any(
        r["status"] in {"setup_error", "runtime_error"} for r in rows)
    return {"planned": len(rows), "recorded": len(rows) - missing,
            "scored": len(scored), "missing": missing, "unscored": unscored,
            "successes": wins, "complete": complete,
            "success_rate": wins / len(rows) if complete else None,
            "verified_success_fraction": wins / len(rows),
            "observed_success_rate": wins / len(scored) if scored else None,
            "wilson_95": wilson(wins, len(rows)) if complete else None}


def _validate_manifest(report):
    manifest = report.get("manifest")
    if (not isinstance(manifest, dict) or manifest.get("format") != "embodied-jev-suite-v1"
            or manifest.get("backend") not in {"builtin", "metaworld", "libero"}
            or not isinstance(manifest.get("cases"), list) or not manifest["cases"]):
        raise ValueError("Report requires a nonempty frozen benchmark manifest")
    seen, trials = set(), set()
    for case in manifest["cases"]:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or type(case.get("seed")) is not int):
            raise ValueError("Invalid manifest case")
        try:
            trial = (_task(case), case["seed"], case.get("init_index"))
        except KeyError as exc:
            raise ValueError("Invalid manifest task") from exc
        if case["id"] in seen or trial in trials:
            raise ValueError("Duplicate manifest case or trial")
        seen.add(case["id"])
        trials.add(trial)
    if _hash(manifest) != report.get("manifest_sha256"):
        raise ValueError("Manifest hash does not match its contents")
    source = report.get("source_sha256")
    if not isinstance(source, dict) or not source or not all(_valid_hash(v) for v in source.values()):
        raise ValueError("Report is missing valid source hashes")
    return manifest


def _episode(case, raw, policy, label):
    row = {"label": label, "policy": policy, "id": case["id"], "task": _task(case),
           "seed": case["seed"], "init_index": case.get("init_index"),
           "recorded": raw is not None, "success": None, "status": "missing",
           "steps": None, "model_calls": None, "http_requests": None,
           "wall_seconds": None, "simulated_seconds": None, "input_tokens": None,
           "output_tokens": None, "api_calls": []}
    if raw is None:
        return row
    if raw.get("success") is not None and type(raw.get("success")) is not bool:
        raise ValueError("Episode success must be official true, false, or null")
    if raw.get("task") not in (None, _task(case)) or raw.get("seed") not in (None, case["seed"]):
        raise ValueError("Episode task or seed differs from frozen manifest")
    if not isinstance(raw.get("status"), str):
        raise ValueError("Episode requires a status")
    initial = raw.get("initial_observation_sha256", raw.get("scene_hash"))
    if raw.get("success") is not None and not _valid_hash(initial):
        raise ValueError("Scored episode is missing its initial observation hash")
    if initial is not None and not _valid_hash(initial):
        raise ValueError("Invalid initial observation hash")
    calls = raw.get("api_calls", [])
    if not isinstance(calls, list):
        raise ValueError("api_calls must be a list")
    clean_calls = []
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("Invalid API request measurement")
        clean = {"latency_ms": _number(call.get("latency_ms"), "latency_ms", nullable=True),
                 "http_status": call.get("http_status"), "model": call.get("model")}
        if clean["http_status"] is not None and (
                type(clean["http_status"]) is not int or not 100 <= clean["http_status"] <= 599):
            raise ValueError("Invalid HTTP status")
        if clean["model"] is not None and not isinstance(clean["model"], str):
            raise ValueError("Invalid resolved model name")
        for side in ("input", "output"):
            clean[f"{side}_tokens"] = _number(call.get(f"{side}_tokens"), side, integer=True, nullable=True)
        if isinstance(call.get("error_type"), str):
            clean["error_type"] = call["error_type"]
        clean_calls.append(clean)
    attempts = _number(raw.get("model_calls", 0), "model_calls", integer=True)
    if len(clean_calls) > attempts:
        raise ValueError("HTTP request count exceeds recorded model call attempts")
    if policy in BASELINES and (attempts or clean_calls):
        raise ValueError("A baseline report must not contain model calls")
    row.update(success=raw.get("success"), status=raw["status"], model_calls=attempts,
               http_requests=len(clean_calls), api_calls=clean_calls,
               initial_observation_sha256=initial, metadata=raw.get("metadata", {}))
    for key in ("steps", "wall_seconds", "setup_seconds", "rollout_seconds", "simulated_seconds"):
        row[key] = _number(raw.get(key), key, integer=key == "steps", nullable=True)
    for side in ("input", "output"):
        values = [c[f"{side}_tokens"] for c in clean_calls if c[f"{side}_tokens"] is not None]
        # A task that failed before any model request has no measured token
        # usage. Only explicitly labelled non-model baselines have known zero.
        complete = len(values) == attempts and (attempts > 0 or policy in BASELINES)
        row[f"{side}_reported_calls"] = len(values)
        row[f"{side}_reported_tokens"] = sum(values)
        row[f"{side}_complete"] = complete
        row[f"{side}_tokens"] = sum(values) if complete else None
        if (attempts > 0 or policy in BASELINES) and raw.get(f"{side}_tokens") is not None and raw[f"{side}_tokens"] != row[f"{side}_tokens"]:
            raise ValueError("Episode token total disagrees with complete request measurements")
    hz = row["metadata"].get("action_spec", {}).get("control_hz")
    if row["simulated_seconds"] is None and row["steps"] is not None and hz is not None:
        _number(hz, "control_hz")
        if not hz:
            raise ValueError("control_hz must be positive")
        row["simulated_seconds"] = row["steps"] / hz
    return row


def compare_reports(reports, *, allow_baseline=False):
    """Validate comparable provenance, then retain every planned trial in output."""
    if not 2 <= len(reports) <= len(COLORS):
        raise ValueError("Supply two to four independent reports")
    manifests = [_validate_manifest(r) for r in reports]
    first = reports[0]
    if any(m != manifests[0] for m in manifests[1:]):
        raise ValueError("Reports use different frozen manifests")
    if any(r["source_sha256"] != first["source_sha256"] for r in reports[1:]):
        raise ValueError("Reports use different source hashes")
    configurations = [r.get("configuration", {}) for r in reports]
    for config in configurations:
        if any(k not in config for k in PROTOCOL_KEYS):
            raise ValueError("Report is missing a protocol setting")
    protocol = {k: configurations[0][k] for k in PROTOCOL_KEYS}
    provider_fields = {"policy", "configured_model", "connection_source"}
    comparable = [{k: value for k, value in config.items()
                   if k not in provider_fields and not k.startswith("_")} for config in configurations]
    if any(config != comparable[0] for config in comparable[1:]):
        raise ValueError("Observation, action, or budget protocol differs between reports")
    identities, fingerprints, methods, rows = set(), set(), [], []
    cases = manifests[0]["cases"]
    for report, config in zip(reports, configurations):
        if report.get("format") != "embodied-jev-evaluation-v1" or report.get("synthetic"):
            raise ValueError("Only recorded embodied-jev evaluation reports are supported")
        fingerprint = _hash(report)
        if fingerprint in fingerprints:
            raise ValueError("Duplicate report supplied")
        fingerprints.add(fingerprint)
        policy = config.get("policy")
        if policy not in {"jev", "chat"} | BASELINES:
            raise ValueError("Unsupported policy; supply an explicitly identified model or baseline")
        if policy in BASELINES and not allow_baseline:
            raise ValueError("Baselines require --allow-baseline and are explicitly labelled")
        raw_rows = report.get("episodes")
        if not isinstance(raw_rows, list) or not raw_rows:
            raise ValueError("Unrun report: there are no recorded episodes")
        by_id = {}
        for raw in raw_rows:
            if not isinstance(raw, dict) or raw.get("id") not in {c["id"] for c in cases}:
                raise ValueError("Episode is outside the frozen manifest")
            if raw["id"] in by_id:
                raise ValueError("Duplicate episode would inflate the sample size")
            by_id[raw["id"]] = raw
        label = "Jev" if policy == "jev" else "Chat" if policy == "chat" else f"{policy} [baseline]"
        configured = config.get("configured_model")
        resolved = sorted({c["model"] for r in raw_rows for c in r.get("api_calls", []) if c.get("model")})
        identity = (policy, configured, tuple(resolved))
        if identity in identities:
            raise ValueError("Duplicate method: repeated runs cannot be independent comparators")
        identities.add(identity)
        if any(m["label"] == label for m in methods):
            # Same-provider checkpoint comparisons require distinct model labels.
            label += " · " + str(configured or ", ".join(resolved))
        normalized = [_episode(case, by_id.get(case["id"]), policy, label) for case in cases]
        api = [call for row in normalized for call in row["api_calls"]]
        if policy not in BASELINES and not api:
            raise ValueError("No measured HTTP requests: cannot draw model comparison from an unrun or legacy report")
        success = _success_summary(normalized)
        delays = [c["latency_ms"] for c in api if c["latency_ms"] is not None]
        failures = {}
        for row in normalized:
            if row["success"] is not True:
                failures[row["status"]] = failures.get(row["status"], 0) + 1
        methods.append({"label": label, "policy": policy, "configured_model": configured,
            "resolved_models": resolved, "report_sha256": fingerprint, **success,
            "http_requests": len(api), "model_calls": sum(r["model_calls"] or 0 for r in normalized),
            "latency_n": len(delays), "latency_p50_ms": percentile(delays, .5),
            "latency_p95_ms": percentile(delays, .95), "failures": failures,
            "token_usage": {side: {"reported_calls": sum(c[f"{side}_tokens"] is not None for c in api),
                "reported_tokens": sum(c[f"{side}_tokens"] or 0 for c in api),
                "complete_episodes": sum(bool(r.get(f"{side}_complete")) for r in normalized)}
                for side in ("input", "output")},
            "per_task": {task: _success_summary([r for r in normalized if r["task"] == task])
                         for task in dict.fromkeys(r["task"] for r in normalized)}})
        rows.extend(normalized)
    paired = 0
    for case in cases:
        group = [r for r in rows if r["id"] == case["id"]]
        initialized = [r for r in group if r.get("initial_observation_sha256")]
        if len({r["initial_observation_sha256"] for r in initialized}) > 1:
            raise ValueError(f"Paired initial observations differ for {case['id']}")
        if initialized and any(r["metadata"] != initialized[0]["metadata"] for r in initialized[1:]):
            raise ValueError(f"Simulator metadata or action specification differs for {case['id']}")
        paired += len(initialized) == len(reports)
    return {"format": "embodied-jev-comparison-v1", "manifest": manifests[0],
        "manifest_sha256": first["manifest_sha256"], "source_sha256": first["source_sha256"],
        "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "protocol": protocol, "complete": all(m["complete"] for m in methods),
        "paired_initial_states": paired, "planned_pairs": len(cases),
        "scope": "Frozen paired subset; not a full official benchmark leaderboard score",
        "token_source": "Provider-reported usage; missing usage is unavailable, not zero or estimated billing",
        "latency_source": "Client wall time around each HTTP request, including failed requests; not server compute time",
        "methods": methods, "episodes": rows}


def load_comparison(paths, *, allow_baseline=False):
    resolved = [Path(p).resolve() for p in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate report path")
    return compare_reports([json.loads(p.read_text()) for p in resolved], allow_baseline=allow_baseline)


def _csv(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_data(comparison, output):
    """Write clean measurements and hashes, never connections or response bodies."""
    (output / "comparison.json").write_text(json.dumps(comparison, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    _csv(output / "episodes.csv", [{k: v for k, v in row.items() if k not in {"api_calls", "metadata"}}
                                  for row in comparison["episodes"]])
    _csv(output / "api_calls.csv", [{"label": row["label"], "id": row["id"], "task": row["task"],
        "seed": row["seed"], "request_index": index, **call}
        for row in comparison["episodes"] for index, call in enumerate(row["api_calls"])])
    summary_keys = ("planned", "recorded", "scored", "missing", "unscored", "successes", "complete",
                    "success_rate", "verified_success_fraction", "observed_success_rate")
    _csv(output / "task_summary.csv", [{"label": method["label"], "task": task,
        **{key: summary[key] for key in summary_keys},
        "wilson_95_low": summary["wilson_95"][0] if summary["wilson_95"] else None,
        "wilson_95_high": summary["wilson_95"][1] if summary["wilson_95"] else None}
        for method in comparison["methods"] for task, summary in {"All tasks": method, **method["per_task"]}.items()])


def draw(comparison, output, *, fixture=False):
    """Render six distinct measurements; fixture watermark is for private QA only."""
    if not comparison.get("episodes") or not any(m.get("http_requests", 0) for m in comparison.get("methods", [])):
        raise ValueError("No measured HTTP requests; there are no model results to render")
    import matplotlib as mpl
    mpl.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    mpl.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.size": 7, "axes.titlesize": 8, "axes.labelsize": 7, "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5, "svg.fonttype": "none", "pdf.fonttype": 42,
        "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
        "axes.linewidth": .7, "savefig.facecolor": "white"})
    fig, axes = plt.subplots(3, 2, figsize=(7.205, 7.638))  # 183 × 194 mm
    fig.subplots_adjust(left=.11, right=.975, top=.84, bottom=.13, hspace=.74, wspace=.36)
    methods, rows = comparison["methods"], comparison["episodes"]
    labels = [m["label"] for m in methods]
    cases = comparison["manifest"]["cases"]
    tasks = list(dict.fromkeys(_task(c) for c in cases))
    positions = {c["id"]: tasks.index(_task(c)) + (i - (len([x for x in cases if _task(x) == _task(c)]) - 1) / 2) * .12
                 for task in tasks for i, c in enumerate(x for x in cases if _task(x) == task)}
    method_offset = [(i - (len(methods) - 1) / 2) * .035 for i in range(len(methods))]
    marker = ("o", "s", "^", "D")
    ax = axes[0, 0]
    categories = ["All tasks"] + tasks
    for mi, method in enumerate(methods):
        for ti, task in enumerate(categories):
            stat = method if task == "All tasks" else method["per_task"][task]
            value = stat["verified_success_fraction"] * 100
            y = ti + (mi - (len(methods) - 1) / 2) * .6 / max(1, len(methods) - 1)
            interval = stat["wilson_95"]
            if interval:
                ax.plot([100 * interval[0], 100 * interval[1]], [y, y], color=COLORS[mi], lw=1)
            ax.plot(value, y, marker[mi], ms=4, color=COLORS[mi],
                    markerfacecolor=COLORS[mi] if stat["complete"] else "white")
            suffix = "*" if not stat["complete"] else ""
            ax.text(105, y, f"{stat['successes']}/{stat['planned']}{suffix}", va="center", color=COLORS[mi], fontsize=6.5)
    ax.set(yticks=range(len(categories)), yticklabels=[t.removesuffix("-v3") for t in categories],
           xlim=(-3, 125), xticks=[0, 50, 100], xlabel="Verified successes / planned episodes (%)")
    ax.invert_yaxis()
    ax.set_title("Task success · Wilson 95% intervals", loc="left")
    ax = axes[0, 1]
    for mi, method in enumerate(methods):
        subset = [r for r in rows if r["label"] == method["label"]]
        for side_i, side in enumerate(("input", "output")):
            values = [r for r in subset if r[f"{side}_tokens"] is not None]
            x = side_i + (mi - (len(methods) - 1) / 2) * .22
            for ri, row in enumerate(values):
                jitter = (cases.index(next(c for c in cases if c["id"] == row["id"])) - (len(cases) - 1) / 2) * .012
                ax.scatter(x + jitter, row[f"{side}_tokens"], s=13, marker=marker[mi],
                           color=COLORS[mi], alpha=.65, linewidths=0)
            if values:
                median = statistics.median(r[f"{side}_tokens"] for r in values)
                ax.plot([x - .065, x + .065], [median, median], color=COLORS[mi], lw=1.4)
            missing = len(subset) - len(values)
            if missing:
                ax.text(x, -.15 - mi * .095, f"{missing} N/A", ha="center", va="top",
                        transform=ax.get_xaxis_transform(), color=COLORS[mi], fontsize=6)
    ax.set(xticks=[0, 1], xticklabels=["Input", "Output"], ylabel="Provider-reported tokens / episode", xlim=(-.5, 1.5))
    ax.set_title("API-reported usage · dots = episodes; line = median", loc="left")
    ax = axes[1, 0]
    for mi, method in enumerate(methods):
        for key, mark in (("latency_p50_ms", marker[mi]), ("latency_p95_ms", "|")):
            value = method[key]
            if value is not None:
                ax.plot(value / 1000, mi, mark, color=COLORS[mi], ms=5 if mark != "|" else 10)
        if method["latency_n"]:
            ax.plot([method["latency_p50_ms"] / 1000, method["latency_p95_ms"] / 1000], [mi, mi], color=COLORS[mi], lw=.8)
        else:
            ax.text(.02, mi, "N/A", transform=ax.get_yaxis_transform(), color=COLORS[mi])
        ax.text(.98, mi + .18, f"n={method['latency_n']} requests", ha="right",
                transform=ax.get_yaxis_transform(), fontsize=6, color=COLORS[mi])
    ax.set(yticks=range(len(methods)), yticklabels=[m["label"] for m in methods],
           xlabel="HTTP request wall time (s)", ylim=(-.7, len(methods) - .3))
    ax.set_xlim(left=0)
    ax.set_title("API latency · marker p50; tick p95", loc="left")
    def paired_panel(ax, field, title, ylabel, budget=None):
        for case in cases:
            group = [next(r for r in rows if r["id"] == case["id"] and r["label"] == label) for label in labels]
            if all(r[field] is not None for r in group):
                ax.plot([positions[case["id"]] + method_offset[i] for i in range(len(methods))],
                        [r[field] for r in group], color="#CBCBCB", lw=.7, zorder=1)
        for mi, method in enumerate(methods):
            valid = [r for r in rows if r["label"] == method["label"] and r[field] is not None]
            ax.scatter([positions[r["id"]] + method_offset[mi] for r in valid], [r[field] for r in valid],
                       color=COLORS[mi], marker=marker[mi], s=16, edgecolors="white", linewidths=.3, zorder=2)
            count = len(cases) - len(valid)
            if count:
                ax.text(.02, .96 - mi * .11, f"{method['label']}: {count} N/A", transform=ax.transAxes,
                        color=COLORS[mi], fontsize=6, va="top")
        if budget is not None:
            ax.axhline(budget, color="#8A8A8A", ls="--", lw=.6)
        ax.set(xticks=range(len(tasks)), xticklabels=[t.removesuffix("-v3") for t in tasks],
               ylabel=ylabel, xlim=(-.45, len(tasks) - .55))
        ax.tick_params(axis="x", labelrotation=20 if len(tasks) > 3 else 0)
        ax.set_title(title, loc="left")
    paired_panel(axes[1, 1], "wall_seconds", "Episode time · includes setup and teardown", "Wall time (s)")
    paired_panel(axes[2, 0], "http_requests", "HTTP requests · dashed = call-attempt budget", "Requests / episode", comparison["protocol"]["max_calls"])
    paired_panel(axes[2, 1], "steps", "Environment steps · dashed = step budget", "Native environment steps", comparison["protocol"]["max_steps"])
    for letter, ax in zip("abcdef", axes.flat):
        ax.text(-.12, 1.17, letter, transform=ax.transAxes, weight="bold", fontsize=9)
        ax.grid(axis="y" if ax not in (axes[0, 0], axes[1, 0]) else "x", color="#EAEAEA", lw=.5, zorder=0)
        if ax not in (axes[0, 0], axes[1, 0]):
            ax.set_ylim(bottom=0)
    backend = comparison["manifest"]["backend"]
    state = "PARTIAL" if not comparison["complete"] else "COMPLETE SUBSET"
    fig.suptitle(f"{backend} · paired model evaluation · {state}", x=.08, ha="left", y=.98, fontsize=11, weight="bold")
    fig.text(.08, .944, f"{len(cases)} planned episodes / method · {comparison['manifest'].get('split', 'unspecified')} split · "
             f"{comparison['paired_initial_states']}/{len(cases)} matched initial states", fontsize=7)
    handles = [Line2D([0], [0], marker=marker[i], color=COLORS[i], lw=0, markersize=5,
               label=m["label"] + (" · " + ", ".join(m["resolved_models"]) if m["resolved_models"] else ""))
               for i, m in enumerate(methods)]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(.07, .934), fontsize=6.5)
    fig.text(.08, .045, "Official environment success; custom subset, not a leaderboard score. Dots share task/seed; grey lines pair methods.\n"
             "* Partial: verified/planned is a lower bound; no CI or complete-score claim. Missing usage = N/A, never zero.\n"
             "Latency includes network and failed HTTP requests. Wall time differs from simulated time. See source data and figure notes.",
             fontsize=6.5, linespacing=1.45)
    if fixture:
        fig.text(.5, .5, "TEST FIXTURE\nNOT MODEL RESULTS", ha="center", va="center", fontsize=34,
                 color="#A00000", alpha=.28, rotation=25, weight="bold")
    fig.savefig(output / "comparison.svg")
    fig.savefig(output / "comparison.pdf")
    fig.savefig(output / "comparison.png", dpi=600)
    plt.close(fig)


def write_notes(comparison, output):
    lines = ["# Paired evaluation figure notes", "",
        "This quantitative grid compares task outcomes, resource use, and request delay under one frozen protocol. "
        "It does not combine these measurements into an accuracy or intelligence score.", "",
        f"Coverage: {'complete subset' if comparison['complete'] else 'partial; unfinished/error cases retained'}. "
        f"Matched initial states: {comparison['paired_initial_states']}/{comparison['planned_pairs']}.", "",
        "- Task success comes from the benchmark environment. Each replicate is one listed task/initial state/seed; "
        "the frozen manifest defines the split. For complete groups, bars/points show successes divided by planned episodes "
        "with a binomial Wilson 95% interval. For partial groups, hollow points show verified successes divided by planned "
        "episodes, a lower bound; no confidence interval or complete success-rate claim is shown. The JSON also retains "
        "the observed scored-only fraction, which is not used to hide missing cases.",
        "- Token dots show episodes with complete provider-reported input or output usage; horizontal marks are medians. "
        "Missing usage is N/A. Partial reported totals and exact request coverage remain in the source data. Different "
        "providers may tokenize differently; these are usage counters, not equal units of computation or a billing estimate.",
        "- API latency is measured client wall time per actual HTTP request, including error requests. p50 and p95 use "
        "linear interpolation of sorted measurements; n counts requests with measured latency. They are descriptive "
        "quantiles, not confidence intervals or server-only inference time.",
        "- Wall time includes simulator setup and teardown. Simulator time, when available, is exported separately. "
        "Paired plots connect matching task/seed observations. Missing values are retained and labelled, not imputed.",
        "- Request count is actual HTTP requests; the dashed cap is the model-attempt budget. These can differ after "
        "pre-request validation failures. Native steps use the benchmark controller frequency, preserved in metadata.",
        "- No hypothesis test or multiple-comparison correction is applied. A small frozen subset is not a full official "
        "benchmark score or evidence of unseen-task generalization.", "",
        "## Source and export audit", "",
        f"Manifest SHA-256: `{comparison['manifest_sha256']}`.",
        "Report hashes use sorted-key UTF-8 JSON, matching the evaluator's manifest-hash convention; "
        "they are hashes of report contents, not original file whitespace. The renderer source hash is recorded separately.",
        "`comparison.json` preserves source-code and report hashes, the protocol, paired coverage and all planned cases. "
        "`episodes.csv`, `api_calls.csv`, and `task_summary.csv` contain panel source measurements. Empty CSV fields are unavailable.",
        "The Python renderer exports an approximately 183 × 194 mm figure, editable SVG text, TrueType PDF text, "
        "and a 600 dpi PNG. All panels use the same method color and marker. No raster image manipulation is involved.",
        "The source preflight may warn that TIFF is absent: PNG is the requested raster preview and SVG/PDF provide "
        "the editable vector exports; this bundle is not a journal-specific submission package.",
        "Rendering is an automated export, not visual approval. Inspect the exported figure at final size before publication.", ""]
    for method in comparison["methods"]:
        lines += [f"- {method['label']}: {method['successes']}/{method['planned']} verified successes; "
            f"{method['missing']} missing, {method['unscored']} unscored; "
            f"{method['http_requests']} HTTP requests; latency n={method['latency_n']}. "
            f"Input usage reported for {method['token_usage']['input']['reported_calls']} requests; "
            f"output usage for {method['token_usage']['output']['reported_calls']}. "
            f"Failure outcomes: {json.dumps(method['failures'], sort_keys=True)}."]
    (output / "FIGURE_NOTES.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="+", required=True, help="Matched evaluation summary.json files")
    parser.add_argument("--output", required=True, help="New output directory; never overwritten")
    parser.add_argument("--allow-baseline", action="store_true", help="Explicitly include labelled zero-model baselines")
    parser.add_argument("--data-only", action="store_true", help="Validate and export source data without rendering")
    args = parser.parse_args(argv)
    comparison = load_comparison(args.reports, allow_baseline=args.allow_baseline)
    if not args.data_only:
        try:
            import matplotlib  # noqa: F401 -- fail before creating incomplete output
        except ImportError as exc:
            raise RuntimeError("Python matplotlib is required for figures; use a plotting environment or --data-only") from exc
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    write_data(comparison, output)
    write_notes(comparison, output)
    if not args.data_only:
        draw(comparison, output)
    print(json.dumps({"complete": comparison["complete"], "planned_pairs": comparison["planned_pairs"],
                      "paired_initial_states": comparison["paired_initial_states"], "rendered": not args.data_only}))
    return comparison


if __name__ == "__main__":
    main()
