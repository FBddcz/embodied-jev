"""Build the gallery using only explicitly selected published records."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/FBddcz/embodied-jev"

# Resolve beside this script so CLI and import-based build checks use the same validator.
_community_spec = importlib.util.spec_from_file_location("community_entries", Path(__file__).with_name("community_entries.py"))
_community = importlib.util.module_from_spec(_community_spec)
_community_spec.loader.exec_module(_community)


def build(output):
    output = Path(output).resolve()
    if output == ROOT or ROOT in output.parents and "dist" not in output.relative_to(ROOT).parts:
        raise ValueError("Build inside dist/ or a separate output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "style.css", "app.js", "favicon.svg"):
        shutil.copy2(ROOT / "site" / name, output / name)
    (output / ".nojekyll").touch()

    def asset(relative):
        source = ROOT / relative
        source.resolve().relative_to((ROOT / "docs").resolve())
        dest = output / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return relative

    def link(label, path):
        return {"label": label, "url": f"{REPO}/blob/main/{path}"}

    prefix = "docs/results/metaworld-hierarchy-v2-2026-09-21"
    comparison = json.loads((ROOT / prefix / "comparison.json").read_text())
    costs = json.loads((ROOT / prefix / "COSTS.json").read_text())["methods"]
    metrics = []
    for method in comparison["methods"]:
        label = "Jev" if method["policy"] == "jev" else "GPT-6"
        metrics.extend([{"label": label + " · 成功", "value": f"{method['successes']}/{method['planned']}"},
                        {"label": label + " · 费用估算", "value": "$" + f"{costs[method['policy']]['estimated_total_cost_usd']:.4f}"},
                        {"label": label + " · 请求 p50", "value": f"{method['latency_p50_ms']/1000:.2f}s"}])
    experiments = [{"id": "metaworld-hierarchy", "category": "metaworld", "title": "六局任务，同屏对照。",
        "kicker": "META-WORLD / HIERARCHICAL", "description": "到达、推物、抓放 × 两个种子。Jev 与 GPT-6 使用相同状态、子目标接口和预算，原生环境判定成功。",
        "badge": "已完成 · 每模型 6 局", "video": asset(prefix + "/figures/hierarchy-grid.mp4"),
        "poster": asset(prefix + "/figures/hierarchy-grid.png"), "chart": asset(prefix + "/comparison.png"),
        "download": asset(prefix + "/figures/hierarchy-grid-fast.mp4"),
        "playback": "动作回放 · 省略请求等待；视频播放速度不代表推理速度。", "metrics": metrics, "decisions": [],
        "note": "两者均为 5/6；都未完成 push-v3 seed 0。输入是仿真结构化状态。仅为开发子集，不是完整 Meta-World 排名。",
        "links": [link("实验结果", prefix + "/RESULTS.md"), link("费用口径", prefix + "/COSTS.md"), link("结构化数据", prefix + "/comparison.json")]}]
    demos = [
        ("jev-hierarchical", "Jev：一步一步搬运。", "JEV / SUBGOAL + MOTOR", "jev-hierarchical-live-2026-09-20", "状态与接触反馈 · 子目标和各轴概率", "下放时失抓，物体落入托盘后撤离。终态通过不等于稳定精确放置。", "docs/JEV_EVALUATION.md"),
        ("vision-transfer", "看图，抓取，再放下。", "GPT-6 / DUAL-CAMERA VISION", "planning-vision-gpt6-v2-cameras-transfer", "原始双相机 RGB · GPT-6 短步决策", "相机画面来自真正发送给模型的观测；不提供物体和目标坐标。", "docs/PLANNING_RESULTS.md"),
        ("vision-recovery", "目标移动以后。", "GPT-6 / VISUAL RECOVERY", "planning-vision-gpt6-v2-live-target-shift-run1", "双相机视觉 · 中途目标扰动", "第 20 步后托盘平移 6 cm；包含实际失抓与重新抓取过程。", "docs/PLANNING_RESULTS.md")]
    for media, title, kicker, record, description, note, doc in demos:
        wrapper = json.loads(gzip.decompress((ROOT / "docs/results" / (record + ".json.gz")).read_bytes()))
        episode = wrapper.get("episode", wrapper)
        decisions = []
        for index, h in enumerate(episode["history"]):
            decision = h["decision"]
            intent = h.get("intent") or {}
            extra = sum(2 for item in episode.get("interventions", []) if item["after_cycle"] < h["cycle"])
            decisions.append({"time": 2 + index * 1.2 + extra, "index": index + 1, "label": h["label"],
                "stage": intent.get("choice", "视觉短步"), "intent": decision.get("intent", h["label"]),
                "evidence": decision.get("visual_evidence", "当前机器人状态与接触反馈"),
                "latency_ms": decision.get("latency_ms", 0) + intent.get("latency_ms", 0),
                "channels": decision.get("channel_decisions", {}), "probabilities": intent.get("probabilities", {}),
                "tcp": h["after"]["tcp"]})
        experiments.append({"id": media, "category": "panda", "title": title, "kicker": kicker, "description": description,
            "badge": "真实记录 · 单局演示", "video": asset(f"docs/media/{media}.mp4"),
            "poster": asset(f"docs/media/{media}.png"), "download": f"docs/media/{media}.mp4",
            "playback": "动作回放 · 每个决策展示 1.2 秒，省略模型等待。", "note": note,
            "metrics": [{"label": "结果", "value": "完成" if episode["success"] else "未完成"},
                        {"label": "实际耗时", "value": f"{episode['wall_seconds']:.1f}s"},
                        {"label": "模型调用", "value": str(episode["model_calls"])},
                        {"label": "控制步数", "value": str(len(episode["history"]))}],
            "decisions": decisions, "links": [link("实验说明", doc), link("原始数据", f"docs/results/{record}.json.gz")]})
    published = ROOT / "docs/results/libero-vision/index.json"
    if published.exists():
        legacy = [item for item in json.loads(published.read_text())["experiments"]
                  if item["id"] in {"libero-drawer", "libero-microwave"}]
        for item in legacy:
            for field in ("video", "poster", "chart", "download"):
                if item.get(field):
                    asset(item[field])
        experiments = legacy + experiments
    supervised = ROOT / "docs/results/libero-supervisor-plate/index.json"
    if supervised.exists():
        latest = json.loads(supervised.read_text())["experiments"]
        for item in latest:
            for field in ("video", "poster", "chart", "download"):
                if item.get(field):
                    asset(item[field])
        replaced = {item["id"] for item in latest}
        experiments = latest + [item for item in experiments if item["id"] not in replaced]
    experiments.extend(_community.load_entries(ROOT))
    catalog = {"format": "embodied-jev-gallery-v1", "repository": REPO, "experiments": experiments,
               "categories": [{"id": "all", "label": "全部实验"}, {"id": "libero", "label": "LIBERO · 视觉协作"},
                              {"id": "metaworld", "label": "Meta-World · 标准任务"}, {"id": "panda", "label": "Panda · 机制演示"}]}
    (output / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")))
    print(f"Built {len(experiments)} recorded experiments → {output}")
    return catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/site")
    build(parser.parse_args().output)
