"""Export verified LIBERO records and paired media for the static gallery."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "docs/results/libero-vision"
LABELS = {"gpt6": "纯 GPT-6", "gpt6-jev": "GPT-6 + Jev"}
STATUSES = {"success": "成功", "step_budget": "步数耗尽", "time_budget": "时间预算耗尽",
            "request_budget": "请求预算耗尽", "cost_budget": "费用保护上限"}


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
        "latency_p50_ms": percentile([c["latency_ms"] for c in calls], .5),
        "latency_p95_ms": percentile([c["latency_ms"] for c in calls], .95),
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


def report_text(protocol, methods, episodes, run, pending=()):
    budget = protocol["budget"]
    wall_time = budget.get("mode") == "wall-time"
    rule = (f"每局成功或运行满 {budget['timeout']:g} 秒停止，无步数／请求上限" if wall_time else
            f"每局最多 {budget['max_steps']} 步、{budget['max_calls']} 次请求、{budget['timeout']:g} 秒")
    headline = "已展示记录：" + "；".join(f"{LABELS[mode]} **{m['successes']}/{m['episodes']}**" for mode, m in methods.items())
    lines = ["# LIBERO 真实观测对照", "", headline + "。" + rule + "。", "",
        "| 模式 | 成功 | 总步数 | 请求 | 输入 / 输出 token | 费用估算 | 总耗时 | 请求 p50 / p95 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for mode, m in methods.items():
        usage_note = "（已报告部分）" if not m["usage_complete"] else ""
        lines.append(f"| {LABELS[mode]} | {m['successes']}/{m['episodes']} | {m['steps']} | {m['requests']} | "
            f"{m['input_tokens']:,} / {m['output_tokens']:,}{usage_note} | {cost_label(m)} | "
            f"{m['wall_seconds']:.1f}s | {m['latency_p50_ms']/1000:.2f}s / {m['latency_p95_ms']/1000:.2f}s |")
    lines += ["", "费用与耗时包含两层全部调用；提前成功和预算耗尽分别记录。不能把未完成任务的低开销等同于完成任务更高效。", "",
              "## 逐局结果", "", "| 任务 | 模式 | 结果 | 步数 | 请求 | 实际耗时 | 费用估算 |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for e in episodes.values():
        task = "关抽屉" if e["id"].startswith("drawer") else "关微波炉"
        lines.append(f"| {task} | {LABELS[e['mode']]} | {result_label(e)} | {e['steps']} | {e['metrics']['requests']} | "
                     f"{e['wall_seconds']:.1f}s | {cost_label(e['metrics'])} |")
    if (run / "retry-costs.json").exists():
        attempts = read(run / "retry-costs.json")["attempts"]
        retries = call_metrics([c for a in attempts for c in a["api_calls"]])
        pending_known = sum(e["metrics"]["known_cost_subtotal_usd"] for e in pending)
        total_known = retries["known_cost_subtotal_usd"] + pending_known + sum(m["known_cost_subtotal_usd"] for m in methods.values())
        transport = sum(a.get("status") == "runtime_error" for a in attempts)
        interrupted = sum(a.get("status") == "interrupted" for a in attempts)
        if pending:
            lines += ["", "待重测的混合组微波炉已知费用 **≥$" + format(pending_known, ".5f") + "**，计入总开销。"]
        lines += ["", f"另有 **{transport} 次通信异常、{interrupted} 次主动中断后重测**，不计入上面的展示记录；额外已知费用 **{cost_label(retries)}**。"
                  + "本次测试含重试的已知费用合计 **≥$" + format(total_known, ".5f") + "**，未返回用量的请求仍可能收费。[重试用量](retry-costs.json)"]
        if interrupted:
            lines += ["", "**展示记录包含重测，不能当作首次尝试成功率。** 混合组微波炉曾在未完成且未耗尽时间预算时主动中断重测。"]
    if (run / "analysis.md").exists():
        lines += ["", "## 轨迹结论", "", (run / "analysis.md").read_text().strip()]
    models = sorted({model for m in methods.values() for model in m["models"]})
    lines += ["", "## 对照范围", "",
        "- 双相机 RGB-D 与本体反馈；不向模型提供物体真值或成功谓词。GPT-6 负责两组视觉规划，局部动作分别由 GPT-6 与 Jev 选择。",
        "- libero_90 任务 0 / 33，初态 0、seed 0；配对初态、初始化后状态与模拟器版本一致。" + rule + f"；每局费用准入保护 ${budget['max_usd']:g}。",
        "- 实际返回模型：" + "、".join(models) + "。延迟含网络等待；总耗时含初始化，1200 秒预算从初始化完成后计时。",
        "- 费用按冻结协议单价估算：GPT-6 每百万输入／输出 token $10 / $50；Jev 为 $0.042 / $0。Jev 有输出 token 统计，但输出免费。中转账单可能不同。",
        "- 若到时截断的请求未返回用量，费用显示 ≥ 已知小计，token 标为已报告部分；不把未知用量当作免费。",
        "- 两个开发任务的系统对照，未训练 VLA，不是完整 LIBERO 评测。墙钟预算允许不同模式执行不同数量的动作。", "",
        "[关抽屉回放](../../media/libero-drawer-comparison.mp4) · [关微波炉回放](../../media/libero-microwave-comparison.mp4) · [协议与逐局统计](summary.json) · [费用与价格来源](COSTS.json) · [运行方法](../../LIBERO_VISION.md)", ""]
    return "\n".join(lines)


def track(episode, speed):
    decisions = []
    for row in episode["decisions"]:
        calls = [c for c in episode["api_calls"] if row["start_seconds"] <= c["start_seconds"] <= row["inference_end_seconds"]]
        decisions.append({
            "time": row["inference_end_seconds"] / speed, "index": row["index"] + 1,
            "label": " · ".join(k + "=" + v for k, v in row["choices"].items()),
            "stage": row["stage"], "intent": row["plan"]["intent"],
            "evidence": row["plan"]["visual_evidence"],
            "latency_ms": sum(c["latency_ms"] for c in calls),
            "channels": {k: {"choice": v, "probabilities": row.get("probabilities", {}).get(k, {})}
                         for k, v in row["choices"].items()},
            "tcp": row["state"]["robot"]["tcp"],
        })
    return {"id": episode["mode"], "label": LABELS[episode["mode"]], "decisions": decisions}


def publish(run, media, exclude=()):
    summary = read(run / "summary.json")
    if not summary["complete"] or not summary["sources_unchanged"]:
        raise ValueError("A complete run with unchanged sources is required")
    protocol = summary["protocol"]
    for name, digest in protocol["source_sha256"].items():
        if sha(run / "reproduction" / name) != digest:
            raise ValueError("Frozen source hash mismatch: " + name)
    episodes = {}
    pending = []
    for record in summary["episodes"]:
        episode = read(run / record["episode_path"])
        for key in ("id", "mode", "success", "status", "steps", "metadata", "metrics"):
            if episode[key] != record[key]:
                raise ValueError("Episode/summary mismatch: " + key)
        if episode["status"] not in STATUSES:
            raise ValueError("Refusing to publish incomplete or errored episodes")
        if len(episode["api_calls"]) != episode["metrics"]["requests"]:
            raise ValueError("Request count mismatch")
        if record["mode"] + "/" + record["id"] in exclude:
            pending.append({"mode": record["mode"], "id": record["id"], "status": record["status"],
                            "steps": record["steps"], "success": record["success"],
                            "metrics": call_metrics(episode["api_calls"]),
                            "note": "分析后待重测，暂不发布回放；费用计入总开销"})
            continue
        episodes[record["mode"], record["id"]] = episode
    cases = protocol["manifest"]["cases"]
    if len(episodes) + len(pending) != len(cases) * len(LABELS):
        raise ValueError("Expected both modes for every case")
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
        slug = case["id"].split("-init")[0]
        provenance = read(media / slug / "media.json")
        if provenance["video_sha256"] != sha(media / slug / "comparison.mp4"):
            raise ValueError("Video hash mismatch")
        if not (media / slug / "poster.png").is_file():
            raise ValueError("Missing video poster")
        modes = [mode for mode in LABELS if (mode, case["id"]) in episodes]
        for mode, source in zip(modes, provenance["episodes"], strict=True):
            if source["sha256"] != sha(run / mode / case["id"] / "episode.json") or source["mode"] != mode:
                raise ValueError("Video/episode mismatch")
    diagnostic = read(run / "replay-diagnostics.json") if (run / "replay-diagnostics.json").exists() else None
    if diagnostic:
        diagnostic["episodes"] = [e for e in diagnostic["episodes"] if (e["mode"], e["id"]) in episodes]
        if {(e["mode"], e["id"]) for e in diagnostic["episodes"]} != set(episodes):
            raise ValueError("Diagnostic episode mismatch")
        if any(e["max_tcp_replay_error_m"] > 1e-8 for e in diagnostic["episodes"]):
            raise ValueError("Diagnostic replay differs from recorded trajectory")

    methods = {mode: aggregate([e for (m, _), e in episodes.items() if m == mode]) for mode in LABELS}
    target = ROOT / PREFIX
    target.mkdir(parents=True, exist_ok=True)
    media_target = ROOT / "docs/media"
    media_target.mkdir(parents=True, exist_ok=True)
    published_summary = {**summary, "episodes": [e for e in summary["episodes"] if (e["mode"], e["id"]) in episodes],
                         "publication_complete": not pending, "pending": pending, "methods": methods}
    write(target / "summary.json", published_summary)
    write(target / "COSTS.json", {"pricing": protocol["pricing"], "methods": methods,
                                 "pending_episodes": pending})
    if (run / "retry-costs.json").exists():
        retries = read(run / "retry-costs.json")
        write(target / "retry-costs.json", retries)
        costs = read(target / "COSTS.json")
        costs["retry_attempts"] = call_metrics([c for a in retries["attempts"] for c in a["api_calls"]])
        costs["total_known_cost_including_retries_usd"] = (costs["retry_attempts"]["known_cost_subtotal_usd"]
            + sum(e["metrics"]["known_cost_subtotal_usd"] for e in pending)
            + sum(m["known_cost_subtotal_usd"] for m in methods.values()))
        write(target / "COSTS.json", costs)

    # Keep only explicit public record fields; no endpoint, credentials, or local paths.
    public = [{k: e[k] for k in ("id", "case", "mode", "protocol", "success", "status", "steps",
              "metadata", "metrics", "setup_seconds", "rollout_seconds", "wall_seconds", "api_calls", "decisions")}
              for e in episodes.values()]
    (target / "episodes.json.gz").write_bytes(gzip.compress(json.dumps(public, ensure_ascii=False).encode(), mtime=0))
    experiments = []
    for case in cases:
        slug = case["id"].split("-init")[0]
        title = {"drawer": "关上顶层抽屉。", "microwave": "关上微波炉门。"}[slug]
        pair = [episodes[mode, case["id"]] for mode in LABELS if (mode, case["id"]) in episodes]
        provenance = read(media / slug / "media.json")
        if provenance["video_sha256"] != sha(media / slug / "comparison.mp4"):
            raise ValueError("Video hash mismatch")
        for row, source in zip(pair, provenance["episodes"], strict=True):
            path = run / row["mode"] / row["id"] / "episode.json"
            if source["sha256"] != sha(path) or source["mode"] != row["mode"]:
                raise ValueError("Video/episode mismatch")
        video = f"docs/media/libero-{slug}-comparison.mp4"
        poster = f"docs/media/libero-{slug}-poster.png"
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
        experiments.append({
            "id": "libero-" + slug, "category": "libero", "title": title,
            "kicker": "LIBERO / RGB-D COLLABORATION", "badge": "真实对照 · " + str(sum(e["success"] for e in pair)) + "/2 完成" if len(pair) == 2 else "纯 GPT-6 成功 · 混合组重测中",
            "description": "左：纯 GPT-6；右：GPT-6 视觉规划 + Jev 局部控制。相同初态和预算，用双相机 RGB-D 与本体反馈闭环执行。" if len(pair) == 2 else "先展示纯 GPT-6 的成功录像。混合组在 1200 秒内未完成，正在分析与重测；此处不作双组成功率或费用比较。",
            "video": video, "poster": poster, "download": video,
            "playback": "8× 共同墙钟时间，保留模型等待；初始化不在录像中。播放速度可继续调整。",
            "metrics": metrics, "decisions": [], "decision_tracks": [track(e, provenance["speed"]) for e in pair],
            "note": "LIBERO 开发子集 · 初态 0 / seed 0。" + "；".join(LABELS[e["mode"]] + "：" + result_label(e) + f"，{e['steps']} 步" for e in pair) + "。全部调用按公开单价估算，≥ 表示含未返回用量的请求。",
            "links": [{"label": label, "url": "https://github.com/FBddcz/embodied-jev/blob/main/" + PREFIX + "/" + name}
                      for label, name in (("实验结果", "RESULTS.md"), ("统计与费用", "summary.json"), ("决策记录", "episodes.json.gz"))],
        })
    write(target / "index.json", {"experiments": experiments})
    (target / "RESULTS.md").write_text(report_text(protocol, methods, episodes, run, pending))
    if pending:
        report_path = target / "RESULTS.md"
        report = report_path.read_text()
        report_path.write_text(report.replace("# LIBERO 真实观测对照", "# LIBERO 真实观测对照\n\n**阶段发布：前三局成功录像。当前四局结果为纯 GPT-6 2/2、混合组 1/2；混合组微波炉在 1120 步、1200 秒内未完成，分析后待重测。以下表格只统计已展示的三局，不代表完整四局成功率。**", 1))
    if diagnostic:
        write(target / "replay-diagnostics.json", diagnostic)
    elif (target / "replay-diagnostics.json").exists():
        (target / "replay-diagnostics.json").unlink()
    print(f"Published {len(episodes)} episodes and {len(experiments)} paired recordings to {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--exclude", action="append", default=[], help="mode/case pending further analysis, disclosed in the summary")
    args = parser.parse_args()
    publish(args.run, args.media, args.exclude)
