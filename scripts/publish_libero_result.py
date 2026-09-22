"""Export verified LIBERO records and paired media for the static gallery."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "docs/results/libero-vision"
LABELS = {"gpt6": "纯 GPT-6", "gpt6-jev": "GPT-6 + Jev"}
SUPERVISOR_PROTOCOL = "libero-rgbd-candidate-supervisor-v2"
PROFILES = {"normal": "正常", "cautious": "谨慎（40% 幅度）", "hold": "保持并重观测"}
STATUSES = {"success": "成功", "step_budget": "步数耗尽", "time_budget": "时间预算耗尽",
            "request_budget": "请求预算耗尽", "cost_budget": "费用保护上限"}
TASK_LABELS = {"drawer": "关抽屉", "microwave": "关微波炉", "push-plate": "将盘子推到炉前"}
TASK_TITLES = {"drawer": "关上顶层抽屉", "microwave": "关上微波炉门", "push-plate": "将盘子推到炉前"}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * fraction
    left, right = math.floor(index), math.ceil(index)
    return values[left] + (values[right] - values[left]) * (index - left)


def call_metrics(calls):
    complete = all(c.get("input_tokens") is not None and c.get("output_tokens") is not None for c in calls)
    costs_complete = all(c.get("estimated_usd") is not None for c in calls)
    return {
        "requests": len(calls),
        "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
        "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
        "usage_complete": complete,
        "estimated_usd": sum(c["estimated_usd"] for c in calls) if costs_complete else None,
        "known_cost_subtotal_usd": sum(c.get("estimated_usd") or 0 for c in calls),
        "unpriced_requests": sum(c.get("estimated_usd") is None for c in calls),
        "latency_p50_ms": percentile([c["latency_ms"] for c in calls if c.get("latency_ms") is not None], .5),
        "latency_p95_ms": percentile([c["latency_ms"] for c in calls if c.get("latency_ms") is not None], .95),
        "models": sorted({c["model"] for c in calls if c.get("model")}),
    }


def aggregate(episodes):
    calls = [call for episode in episodes for call in episode["api_calls"]]
    return {
        **call_metrics(calls), "episodes": len(episodes), "successes": sum(e["success"] for e in episodes),
        "steps": sum(e["steps"] for e in episodes),
        "wall_seconds": sum(e["wall_seconds"] for e in episodes),
        "setup_seconds": sum(e["setup_seconds"] for e in episodes),
        "by_provider": {provider: call_metrics([c for c in calls if c["provider"] == provider])
                        for provider in sorted({c["provider"] for c in calls})},
    }


def cost_label(item, digits=5):
    cost = item.get("estimated_usd")
    if cost is None:
        return "≥$" + format(item["known_cost_subtotal_usd"], f".{digits}f")
    return "$" + format(cost, f".{digits}f")


def result_label(episode):
    return STATUSES.get(episode["status"], episode["status"])


def task_label(case, episode=None, *, title=False):
    """Prefer an explicit public name; unknown tasks retain their recorded language."""
    slug = case["id"].split("-init")[0]
    names = TASK_TITLES if title else TASK_LABELS
    metadata = (episode or {}).get("metadata", {})
    candidates = (case.get("display_name"), names.get(slug), metadata.get("language"),
                  metadata.get("task_name"), case["id"])
    label = next(" ".join(value.split()) for value in candidates if isinstance(value, str) and value.strip())
    if title and not label.endswith(("。", ".", "！", "!", "？", "?")):
        label += "。"
    return label


def report_text(protocol, methods, episodes, run, pending=(), *, media_prefix="libero"):
    budget = protocol["budget"]
    supervisor = protocol["version"] == SUPERVISOR_PROTOCOL
    cases = protocol["manifest"]["cases"]
    paired = all(all((mode, case["id"]) in episodes for mode in LABELS) for case in cases)
    wall_time = budget.get("mode") == "wall-time"
    rule = (f"每局成功或运行满 {budget['timeout']:g} 秒停止，无步数／请求上限" if wall_time else
            f"每局最多 {budget['max_steps']} 步、{budget['max_calls']} 次请求、{budget['timeout']:g} 秒")
    headline = "已展示记录：" + "；".join(f"{LABELS[mode]} **{m['successes']}/{m['episodes']}**" for mode, m in methods.items())
    title = "# LIBERO v2：视觉候选与共享伺服" if supervisor else "# LIBERO 真实观测对照"
    lines = [title, "", headline + "。" + rule + "。", ""]
    if not paired:
        lines += ["**当前只展示部分模式或任务，尚不是完整配对对照；不据此比较两组成功率、速度或费用。**", ""]
    if supervisor:
        lines += ["GPT-6 比较前后两次双相机观测，生成 **2–3 个短动作候选**；纯 GPT-6 组由 GPT-6 选择，混合组由 Jev 选择候选、速度档或重新观测。两组使用**同一数值伺服器**执行短动作，再观察物体是否实际推进。轴方向由代码计算，候选概率不代表任务成功率。", ""]
    lines += [
        "| 模式 | 成功 | 总步数 | 请求 | 输入 / 输出 token | 费用估算 | 总耗时 | 请求 p50 / p95 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for mode, m in methods.items():
        usage_note = "（已报告部分）" if not m["usage_complete"] else ""
        lines.append(f"| {LABELS[mode]} | {m['successes']}/{m['episodes']} | {m['steps']} | {m['requests']} | "
            f"{m['input_tokens']:,} / {m['output_tokens']:,}{usage_note} | {cost_label(m)} | "
            f"{m['wall_seconds']:.1f}s | " + " / ".join("—" if m[k] is None else f"{m[k]/1000:.2f}s" for k in ("latency_p50_ms", "latency_p95_ms")) + " |")
    lines += ["", "费用与耗时包含两层全部调用；提前成功和预算耗尽分别记录。不能把未完成任务的低开销等同于完成任务更高效。", "",
              "## 逐局结果", "", "| 任务 | 模式 | 结果 | 步数 | 请求 | 实际耗时 | 费用估算 |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for e in episodes.values():
        task = task_label(e["case"], e).replace("|", "\\|")
        lines.append(f"| {task} | {LABELS[e['mode']]} | {result_label(e)} | {e['steps']} | {e['metrics']['requests']} | "
                     f"{e['wall_seconds']:.1f}s | {cost_label(e['metrics'])} |")
    if pending:
        lines += ["", "未展示记录：" + "；".join(LABELS[e["mode"]] + " / " + e["id"] + "：" + result_label(e) + f"，{e['steps']} 步" for e in pending) + "。这些记录的费用仍计入总开销。"]
    if (run / "retry-costs.json").exists():
        attempts = read(run / "retry-costs.json")["attempts"]
        retries = call_metrics([c for a in attempts for c in a["api_calls"]])
        pending_known = sum(e["metrics"]["known_cost_subtotal_usd"] for e in pending)
        total_known = retries["known_cost_subtotal_usd"] + pending_known + sum(m["known_cost_subtotal_usd"] for m in methods.values())
        transport = sum(a.get("status") == "runtime_error" for a in attempts)
        interrupted = sum(a.get("status") == "interrupted" for a in attempts)
        if pending:
            lines += ["", "未展示记录的已知费用 **≥$" + format(pending_known, ".5f") + "**，计入总开销。"]
        lines += ["", f"另有 **{transport} 次接口／仿真异常、{interrupted} 次主动中断后重测**，不计入上面的展示记录；额外已知费用 **{cost_label(retries)}**。"
                  + "本次测试含重试的已知费用合计 **≥$" + format(total_known, ".5f") + "**，未返回用量的请求仍可能收费。[重试用量](retry-costs.json)"]
        if interrupted:
            lines += ["", "**展示记录包含重测，不能当作首次尝试成功率。** 主动中断记录也保留费用统计。"]
    if (run / "analysis.md").exists():
        lines += ["", "## 轨迹结论", "", (run / "analysis.md").read_text().strip()]
    models = sorted({model for m in methods.values() for model in m["models"]})
    case_description = "、".join(f"{c.get('suite', 'LIBERO')} / 任务 {c.get('task_id', '?')} / 初态 {c.get('init_index', 0)} / seed {c['seed']}" for c in cases)
    scope = ("- 前后双相机 RGB-D 与本体反馈；视觉评估和候选均由 GPT-6 生成，Jev 接收结构化候选、视觉评估及近期动作反馈，不直接看图。两组共享数值伺服和重观测规则。"
             if supervisor else "- 双相机 RGB-D 与本体反馈；不向模型提供物体真值或成功谓词。GPT-6 负责两组视觉规划，局部动作分别由 GPT-6 与 Jev 选择。")
    recordings = []
    for case in cases:
        episode = next((episodes[mode, case["id"]] for mode in LABELS if (mode, case["id"]) in episodes), None)
        if episode:
            recordings.append(f"[{task_label(case, episode)}回放](../../media/{media_prefix}-{case['id'].split('-init')[0]}-comparison.mp4)")
    lines += ["", "## 对照范围", "",
        scope,
        "- " + case_description + ("；已核对配对初态、初始化后状态与模拟器版本一致。" if paired else "；单组或部分记录保留初态与源码哈希，完整配对比较待补齐。") + rule + f"；每局费用准入保护 ${budget['max_usd']:g}。",
        "- 实际返回模型：" + "、".join(models) + f"。延迟含网络等待；总耗时含初始化，{budget['timeout']:g} 秒预算从初始化完成后计时。",
        "- 费用按冻结协议单价估算：GPT-6 每百万输入／输出 token $10 / $50；Jev 为 $0.042 / $0。Jev 有输出 token 统计，但输出免费。中转账单可能不同。",
        "- 若到时截断的请求未返回用量，费用显示 ≥ 已知小计，token 标为已报告部分；不把未知用量当作免费。",
        f"- {len(cases)} 个开发任务的系统实验，未训练 VLA，不是完整 LIBERO 评测。墙钟预算允许不同模式执行不同数量的动作。"]
    if supervisor:
        lines += ["- 不向模型提供物体真值、真实接触力、成功谓词或未来仿真预览。接触和进度是视觉估计；这是 v2 系统实验，不能与旧 v1 结果解释为 Jev 单因素消融。"]
    lines += ["", " · ".join(recordings) + " · [协议与逐局统计](summary.json) · [费用与价格来源](COSTS.json) · [运行方法](../../LIBERO_VISION.md)", ""]
    return "\n".join(lines)


def track(episode, speed):
    decisions = []
    supervisor = episode.get("protocol") == SUPERVISOR_PROTOCOL
    for row in episode["decisions"]:
        if supervisor and not row.get("new_selection"):
            continue
        calls = [c for c in episode["api_calls"] if row["start_seconds"] <= c["start_seconds"] <= row["inference_end_seconds"]]
        decision = {
            "time": row["inference_end_seconds"] / speed, "index": row["index"] + 1,
            "label": " · ".join(k + "=" + v for k, v in row["choices"].items()),
            "stage": row["stage"], "intent": row["plan"]["intent"],
            "evidence": row["plan"]["visual_evidence"],
            "latency_ms": sum(c["latency_ms"] for c in calls),
            "channels": {k: {"choice": v, "probabilities": row.get("probabilities", {}).get(k, {})}
                         for k, v in row["choices"].items()},
            "tcp": row["state"]["robot"]["tcp"],
        }
        if supervisor:
            selection = row["selection"]
            decision.update(index=len(decisions)+1, label="候选选择：" + selection,
                stage=selection, intent=row["plan"]["intent"] + " · 速度档：" + PROFILES.get(row["selection_profile"], row["selection_profile"]),
                evidence=row["plan"]["visual_evidence"] + "；轴方向由共享代码伺服计算。",
                channels={}, probabilities=row.get("selection_probabilities", {}),
                selection=selection, selection_profile=row["selection_profile"],
                control_source=row["control_source"], servo_choices=row["choices"])
        decisions.append(decision)
    return {"id": episode["mode"], "label": LABELS[episode["mode"]], "decisions": decisions}


def publish(run, media, exclude=(), result_slug="libero-vision"):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", result_slug):
        raise ValueError("Result slug must be a lowercase directory name")
    summary = read(run / "summary.json")
    if not summary["complete"] or not summary["sources_unchanged"]:
        raise ValueError("A complete run with unchanged sources is required")
    protocol = summary["protocol"]
    supervisor = protocol["version"] == SUPERVISOR_PROTOCOL
    if supervisor and result_slug == "libero-vision":
        raise ValueError("v2 results require a separate --result-slug, e.g. libero-supervisor-v2")
    prefix = "docs/results/" + result_slug
    media_prefix = "libero" if result_slug == "libero-vision" else result_slug
    catalog_prefix = "libero" if result_slug == "libero-vision" else "libero-v2" if result_slug == "libero-supervisor-v2" else result_slug
    modes = protocol.get("modes", list(LABELS))
    if not modes or len(set(modes)) != len(modes) or any(mode not in LABELS for mode in modes):
        raise ValueError("Expected unique supported experiment modes")
    if not supervisor and set(modes) != set(LABELS):
        raise ValueError("Waypoint-v1 publication requires both modes")
    for name, digest in protocol["source_sha256"].items():
        if sha(run / "reproduction" / name) != digest:
            raise ValueError("Frozen source hash mismatch: " + name)
    episodes = {}
    pending = []
    recorded = set()
    for record in summary["episodes"]:
        episode = read(run / record["episode_path"])
        for key in ("id", "mode", "success", "status", "steps", "metadata", "metrics"):
            if episode[key] != record[key]:
                raise ValueError("Episode/summary mismatch: " + key)
        if episode["status"] not in STATUSES:
            raise ValueError("Refusing to publish incomplete or errored episodes")
        if len(episode["api_calls"]) != episode["metrics"]["requests"]:
            raise ValueError("Request count mismatch")
        key = record["mode"], record["id"]
        if key in recorded:
            raise ValueError("Duplicate episode record")
        recorded.add(key)
        if episode["protocol"] != protocol["version"]:
            raise ValueError("Episode protocol mismatch")
        if record["mode"] + "/" + record["id"] in exclude:
            pending.append({"mode": record["mode"], "id": record["id"], "status": record["status"],
                            "steps": record["steps"], "success": record["success"],
                            "metrics": call_metrics(episode["api_calls"]),
                            "note": "分析后待重测，暂不发布回放；费用计入总开销"})
            continue
        episodes[record["mode"], record["id"]] = episode
    cases = protocol["manifest"]["cases"]
    if recorded != {(mode, case["id"]) for mode in modes for case in cases}:
        raise ValueError("Expected every declared mode for every case")
    if not episodes:
        raise ValueError("At least one publishable episode is required")
    for case in cases:
        available = [episodes[mode, case["id"]]["metadata"] for mode in LABELS if (mode, case["id"]) in episodes]
        if len(available) != 2:
            continue
        a, b = available
        for key in ("case", "initial_state_sha256", "settled_state_sha256", "versions"):
            if a[key] != b[key]:
                raise ValueError("Paired state mismatch: " + key)

    # Validate every recording before replacing any public result.
    for case in cases:
        available_modes = [mode for mode in LABELS if (mode, case["id"]) in episodes]
        if not available_modes:
            continue
        slug = case["id"].split("-init")[0]
        provenance = read(media / slug / "media.json")
        if provenance["video_sha256"] != sha(media / slug / "comparison.mp4"):
            raise ValueError("Video hash mismatch")
        if not (media / slug / "poster.png").is_file():
            raise ValueError("Missing video poster")
        for mode, source in zip(available_modes, provenance["episodes"], strict=True):
            if source["sha256"] != sha(run / mode / case["id"] / "episode.json") or source["mode"] != mode:
                raise ValueError("Video/episode mismatch")
    diagnostic = read(run / "replay-diagnostics.json") if (run / "replay-diagnostics.json").exists() else None
    if diagnostic:
        diagnostic["episodes"] = [e for e in diagnostic["episodes"] if (e["mode"], e["id"]) in episodes]
        if {(e["mode"], e["id"]) for e in diagnostic["episodes"]} != set(episodes):
            raise ValueError("Diagnostic episode mismatch")
        if any(e["max_tcp_replay_error_m"] > 1e-8 for e in diagnostic["episodes"]):
            raise ValueError("Diagnostic replay differs from recorded trajectory")

    methods = {mode: aggregate([e for (m, _), e in episodes.items() if m == mode])
               for mode in LABELS if any(m == mode for m, _ in episodes)}
    target = ROOT / prefix
    target.mkdir(parents=True, exist_ok=True)
    media_target = ROOT / "docs/media"
    media_target.mkdir(parents=True, exist_ok=True)
    published_summary = {**summary, "episodes": [{k: v for k, v in e.items() if k != "checkpoints"}
                                      for e in summary["episodes"] if (e["mode"], e["id"]) in episodes],
                         "publication_complete": not pending, "pending": pending, "methods": methods,
                         "paired_comparison_complete": set(episodes) == {(mode, case["id"]) for mode in LABELS for case in cases},
                         "result_slug": result_slug}
    write(target / "summary.json", published_summary)
    write(target / "COSTS.json", {"pricing": protocol["pricing"], "methods": methods,
                                 "pending_episodes": pending,
                                 "total_known_cost_including_retries_usd": sum(e["metrics"]["known_cost_subtotal_usd"] for e in pending)
                                     + sum(m["known_cost_subtotal_usd"] for m in methods.values())})
    if (run / "retry-costs.json").exists():
        retries = read(run / "retry-costs.json")
        write(target / "retry-costs.json", retries)
        costs = read(target / "COSTS.json")
        costs["retry_attempts"] = call_metrics([c for a in retries["attempts"] for c in a["api_calls"]])
        costs["total_known_cost_including_retries_usd"] = (costs["retry_attempts"]["known_cost_subtotal_usd"]
            + sum(e["metrics"]["known_cost_subtotal_usd"] for e in pending)
            + sum(m["known_cost_subtotal_usd"] for m in methods.values()))
        write(target / "COSTS.json", costs)
    elif (target / "retry-costs.json").exists():
        (target / "retry-costs.json").unlink()

    # Keep only explicit public record fields; no endpoint, credentials, or local paths.
    public = [{k: e[k] for k in ("id", "case", "mode", "protocol", "success", "status", "steps",
              "metadata", "metrics", "setup_seconds", "rollout_seconds", "wall_seconds", "api_calls", "decisions", "checkpoints") if k in e}
              for e in episodes.values()]
    (target / "episodes.json.gz").write_bytes(gzip.compress(json.dumps(public, ensure_ascii=False).encode(), mtime=0))
    experiments = []
    for case in cases:
        slug = case["id"].split("-init")[0]
        pair = [episodes[mode, case["id"]] for mode in LABELS if (mode, case["id"]) in episodes]
        if not pair:
            continue
        title = task_label(case, pair[0], title=True)
        provenance = read(media / slug / "media.json")
        if provenance["video_sha256"] != sha(media / slug / "comparison.mp4"):
            raise ValueError("Video hash mismatch")
        for row, source in zip(pair, provenance["episodes"], strict=True):
            path = run / row["mode"] / row["id"] / "episode.json"
            if source["sha256"] != sha(path) or source["mode"] != row["mode"]:
                raise ValueError("Video/episode mismatch")
        video = f"docs/media/{media_prefix}-{slug}-comparison.mp4"
        poster = f"docs/media/{media_prefix}-{slug}-poster.png"
        shutil.copy2(media / slug / "comparison.mp4", ROOT / video)
        shutil.copy2(media / slug / "poster.png", ROOT / poster)
        clean_media = {**provenance, "episodes": [{k: v for k, v in row.items() if k != "path"} for row in provenance["episodes"]]}
        write(target / (slug + "-media.json"), clean_media)
        metrics = []
        for row in pair:
            label = LABELS[row["mode"]]
            metrics.extend([
                {"label": label + " · 结果", "value": result_label(row)},
                {"label": label + " · 费用估算", "value": cost_label(row["metrics"], 4)},
                {"label": label + " · 实际耗时", "value": format(row["wall_seconds"], ".1f") + "s"},
            ])
        description = ("两组由 GPT-6 比较前后双相机观测并生成 2–3 个候选，再分别由 GPT-6 / Jev 选择候选、速度档或重观测；共享代码伺服执行。"
                       if supervisor else "左：纯 GPT-6；右：GPT-6 视觉规划 + Jev 局部控制。相同初态和预算，用双相机 RGB-D 与本体反馈闭环执行。")
        if len(pair) != 2:
            description = ("当前为 " + LABELS[pair[0]["mode"]] + " 单组开发试跑，尚不构成完整配对比较。" + description
                           if supervisor else "先展示 " + LABELS[pair[0]["mode"]] + " 的录像；另一组尚未展示，此处不作双组成功率或费用比较。")
        experiments.append({
            "id": catalog_prefix + "-" + slug, "category": "libero", "title": ("v2 · " if supervisor else "") + title,
            "kicker": "LIBERO / CANDIDATE SUPERVISOR V2" if supervisor else "LIBERO / RGB-D COLLABORATION",
            "badge": ("v2 · " if supervisor else "") + ("真实对照 · " + str(sum(e["success"] for e in pair)) + "/2 完成" if len(pair) == 2 else "单组试跑 · " + result_label(pair[0])),
            "description": description,
            "video": video, "poster": poster, "download": video,
            "playback": f"{provenance['speed']:g}× 共同墙钟时间，保留模型等待；初始化不在录像中。播放速度可继续调整。",
            "metrics": metrics, "decisions": [], "decision_tracks": [track(e, provenance["speed"]) for e in pair],
            "note": f"LIBERO 开发子集 · 初态 {case.get('init_index', 0)} / seed {case['seed']}。" + "；".join(LABELS[e["mode"]] + "：" + result_label(e) + f"，{e['steps']} 步" for e in pair) + "。两层全部调用按公开单价估算，≥ 表示含未返回用量的请求。" + ("候选概率不是成功率，轴方向由代码伺服计算。" if supervisor else ""),
            "links": [{"label": label, "url": "https://github.com/FBddcz/embodied-jev/blob/main/" + prefix + "/" + name}
                      for label, name in (("实验结果", "RESULTS.md"), ("统计与费用", "summary.json"), ("决策记录", "episodes.json.gz"))],
        })
    write(target / "index.json", {"experiments": experiments})
    (target / "RESULTS.md").write_text(report_text(protocol, methods, episodes, run, pending, media_prefix=media_prefix))
    if diagnostic:
        write(target / "replay-diagnostics.json", diagnostic)
    elif (target / "replay-diagnostics.json").exists():
        (target / "replay-diagnostics.json").unlink()
    print(f"Published {len(episodes)} episodes and {len(experiments)} recordings to {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--exclude", action="append", default=[], help="mode/case pending further analysis, disclosed in the summary")
    parser.add_argument("--result-slug", default="libero-vision", help="Separate result/media namespace, e.g. libero-supervisor-v2")
    args = parser.parse_args()
    publish(args.run, args.media, args.exclude, args.result_slug)
