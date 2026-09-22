"""Validate contributed recordings and map them to the static gallery catalog.

This module reads local JSON only. It never fetches contributor links, executes
code, or treats a submitted result as an independently verified benchmark score.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = {"libero", "metaworld", "panda", "other"}
RESULTS = {"success": "成功", "failure": "未完成", "partial": "部分完成"}
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?\Z")
GITHUB_USER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
REQUIRED = {"schema_version", "id", "author", "date", "task", "environment", "method",
            "result", "protocol", "reproduction_url", "source_url", "video_url"}
OPTIONAL = {"title", "description", "poster_url", "metrics", "playback"}


def _text(value, label, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label}: expected nonempty text, at most {maximum} characters")
    if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label}: no surrounding whitespace or control characters")
    return value


def _url(value, label, *, media=None):
    value = _text(value, label, 2048)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label}: invalid URL") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or port is not None or parsed.query
            or "\\" in value or re.search(r"%(?![0-9a-fA-F]{2})", value)):
        raise ValueError(f"{label}: use a public HTTPS URL without credentials, port or query")
    path = parsed.path
    # Repeated encoding must not conceal traversal, controls, or backslashes.
    for _ in range(4):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    if ("%" in path or "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(p in {".", ".."} for p in path.split("/"))):
        raise ValueError(f"{label}: invalid URL path")
    parts = [p for p in path.split("/") if p]
    host = parsed.hostname.lower()
    if media is None:
        # The repository, file, issue, or discussion remains inspectable on GitHub.
        if host != "github.com" or len(parts) < 2 or not GITHUB_USER.fullmatch(parts[0]):
            raise ValueError(f"{label}: use a readable https://github.com/OWNER/REPO URL")
        if parts[0].lower() in {"login", "settings", "user-attachments", "features", "topics", "search"}:
            raise ValueError(f"{label}: expected a repository URL")
        return value
    if parsed.fragment:
        raise ValueError(f"{label}: media URLs must not contain fragments")
    attachment = host == "github.com" and len(parts) == 3 and parts[:2] == ["user-attachments", "assets"]
    github_raw = host == "github.com" and len(parts) >= 5 and parts[2] == "raw"
    raw_content = host == "raw.githubusercontent.com" and len(parts) >= 4
    legacy_asset = host == "user-images.githubusercontent.com" and len(parts) >= 2
    pages = bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\.github\.io", host)) and bool(parts)
    if not (attachment or github_raw or raw_content or legacy_asset or pages):
        raise ValueError(f"{label}: use GitHub attachments, raw content, or a github.io media URL")
    suffixes = (".mp4", ".webm") if media == "video" else (".png", ".jpg", ".jpeg", ".webp")
    if not attachment and not path.lower().endswith(suffixes):
        raise ValueError(f"{label}: expected a direct {'/'.join(suffixes)} file")
    return value


def validate_entry(entry, *, filename=None):
    """Validate one contribution. No link accessibility or author identity is inferred."""
    if not isinstance(entry, dict):
        raise ValueError("entry: expected a JSON object")
    missing, unknown = REQUIRED - entry.keys(), entry.keys() - REQUIRED - OPTIONAL
    if missing or unknown:
        raise ValueError(f"entry: missing fields {sorted(missing)}; unknown fields {sorted(unknown)}")
    if type(entry["schema_version"]) is not int or entry["schema_version"] != 1:
        raise ValueError("schema_version: expected 1")
    slug = _text(entry["id"], "id", 80)
    if not SLUG.fullmatch(slug) or (filename is not None and filename != slug + ".json"):
        raise ValueError("id: use a lowercase slug matching the JSON filename")
    author = entry["author"]
    if not isinstance(author, dict) or set(author) != {"name", "github"}:
        raise ValueError("author: requires exactly name and github")
    _text(author["name"], "author.name", 100)
    github = _text(author["github"], "author.github", 39)
    if not GITHUB_USER.fullmatch(github) or "--" in github:
        raise ValueError("author.github: provide the GitHub username, without @ or a URL")
    recorded = _text(entry["date"], "date", 10)
    try:
        parsed_date = date.fromisoformat(recorded)
    except ValueError as exc:
        raise ValueError("date: expected a real calendar date YYYY-MM-DD") from exc
    if parsed_date.isoformat() != recorded:
        raise ValueError("date: expected YYYY-MM-DD")
    for field, maximum in (("task", 200), ("method", 200), ("title", 120),
                           ("description", 1200), ("playback", 500)):
        if field in entry:
            _text(entry[field], field, maximum)
    if not isinstance(entry["environment"], str) or entry["environment"] not in ENVIRONMENTS:
        raise ValueError("environment: expected libero, metaworld, panda or other")
    if not isinstance(entry["result"], str) or entry["result"] not in RESULTS:
        raise ValueError("result: expected success, failure or partial")
    for field in ("protocol", "reproduction_url", "source_url"):
        _url(entry[field], field)
    _url(entry["video_url"], "video_url", media="video")
    if "poster_url" in entry:
        _url(entry["poster_url"], "poster_url", media="image")
    metrics = entry.get("metrics", {})
    if not isinstance(metrics, dict) or set(metrics) - {"steps", "wall_seconds", "estimated_cost_usd", "cost_basis"}:
        raise ValueError("metrics: unknown metric fields")
    for field in ("steps", "wall_seconds", "estimated_cost_usd"):
        value = metrics.get(field)
        try:
            valid_number = type(value) in (int, float) and math.isfinite(value) and value >= 0
        except OverflowError:
            valid_number = False
        if value is not None and not valid_number:
            raise ValueError(f"metrics.{field}: expected a finite nonnegative number or null (unknown)")
        if field == "steps" and value is not None and type(value) is not int:
            raise ValueError("metrics.steps: expected an integer or null")
    if metrics.get("estimated_cost_usd") is not None:
        _text(metrics.get("cost_basis"), "metrics.cost_basis", 500)
    elif "cost_basis" in metrics:
        _text(metrics["cost_basis"], "metrics.cost_basis", 500)
    return entry


def gallery_item(entry):
    author = {**entry["author"], "url": "https://github.com/" + entry["author"]["github"]}
    metrics = [{"label": "作者报告的结果", "value": RESULTS[entry["result"]]}]
    values = entry.get("metrics", {})
    for key, label in (("steps", "控制步数"), ("wall_seconds", "实际耗时"), ("estimated_cost_usd", "费用估算")):
        value = values.get(key)
        display = "未提供" if value is None else (f"${value:.4f}" if key == "estimated_cost_usd" else
                f"{value:g}s" if key == "wall_seconds" else str(value))
        metrics.append({"label": label, "value": display})
    return {
        "id": "community-" + entry["id"], "origin": "community", "category": entry["environment"],
        "author": author, "date": entry["date"], "task": entry["task"], "method": entry["method"],
        "result": entry["result"], "protocol": entry["protocol"],
        "reproduction_url": entry["reproduction_url"], "source_url": entry["source_url"],
        "title": entry.get("title", entry["task"]), "kicker": "COMMUNITY / " + entry["environment"].upper(),
        "description": entry.get("description", entry["method"]),
        "badge": f"社区复现 · {author['name']} · {entry['date']}",
        "video": entry["video_url"], "poster": entry.get("poster_url", ""),
        "download": entry["video_url"], "metrics": metrics, "decisions": [],
        "playback": entry.get("playback", "作者提供的录像；播放速度和剪辑方式请参见复现说明。"),
        "note": "社区作者自行报告，尚未经项目独立复核；不计入项目官方实验统计。" +
                (" 费用口径：" + values["cost_basis"] if values.get("cost_basis") else ""),
        "links": [{"label": "作者 @" + author["github"], "url": author["url"]},
                  {"label": "复现步骤", "url": entry["reproduction_url"]},
                  {"label": "代码与结果", "url": entry["source_url"]},
                  {"label": "实验协议", "url": entry["protocol"]}],
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_entries(root=ROOT):
    """Return validated gallery items, newest first; examples are never ingested."""
    root = Path(root).resolve()
    directory = root / "community" / "entries"
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("community/entries must be a real directory inside the repository")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("community/entries must be a real directory inside the repository")
    entries = []
    for path in sorted(directory.iterdir()):
        if path.name == ".gitkeep":
            continue
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ValueError(f"community/entries/{path.name}: expected a regular .json file")
        if path.stat().st_size > 100_000:
            raise ValueError(f"{path.name}: entry exceeds 100 KB")
        try:
            entry = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
            validate_entry(entry, filename=path.name)
            entries.append(gallery_item(entry))
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise ValueError(f"community/entries/{path.name}: {exc}") from exc
    return sorted(entries, key=lambda entry: (entry["date"], entry["id"]), reverse=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true", help="validate all accepted entry files")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        entries = load_entries(args.root)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Community validation failed: {exc}\n")
    print(f"Validated {len(entries)} community entries (examples excluded).")


if __name__ == "__main__":
    main()
