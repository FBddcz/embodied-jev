"""Contributions stay attributed, untrusted, and separate from project scores."""
import importlib.util
import json
from pathlib import Path
import re

import pytest


spec = importlib.util.spec_from_file_location("community_entries", Path(__file__).parents[1] / "scripts/community_entries.py")
community = importlib.util.module_from_spec(spec)
spec.loader.exec_module(community)


def entry():
    return {
        "schema_version": 1, "id": "alice-libero-plate",
        "author": {"name": "Alice", "github": "alice"}, "date": "2026-09-22",
        "task": "Push plate, task 1, seed 0", "environment": "libero", "method": "GPT-6 + Jev",
        "result": "partial", "protocol": "https://github.com/alice/reproduction/blob/main/PROTOCOL.md",
        "reproduction_url": "https://github.com/alice/reproduction/blob/main/README.md#run",
        "source_url": "https://github.com/alice/reproduction/tree/0123456789abcdef",
        "video_url": "https://alice.github.io/reproduction/replay.mp4",
        "metrics": {"steps": 30, "wall_seconds": 123.4, "estimated_cost_usd": None},
    }


def write_entry(root, value, name=None):
    path = root / "community/entries" / (name or value["id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def test_catalog_retains_attribution_and_unknown_cost_without_claiming_verification(tmp_path):
    write_entry(tmp_path, entry())
    item, = community.load_entries(tmp_path)
    assert item["id"] == "community-alice-libero-plate"
    assert item["origin"] == "community"
    assert item["author"] == {"name": "Alice", "github": "alice", "url": "https://github.com/alice"}
    assert item["date"] == "2026-09-22" and item["task"] == entry()["task"]
    assert item["result"] == "partial" and item["category"] == "libero"
    assert item["metrics"][-1] == {"label": "费用估算", "value": "未提供"}
    assert "不计入" in item["note"] and "独立复核" in item["note"]
    assert item["decisions"] == [] and item["poster"] == ""


def test_examples_are_valid_but_never_published(tmp_path):
    example = Path(__file__).parents[1] / "community/examples/entry.example.json"
    value = json.loads(example.read_text())
    community.validate_entry(value)
    (tmp_path / "community/examples").mkdir(parents=True)
    (tmp_path / "community/examples/entry.example.json").write_text(json.dumps(value))
    assert community.load_entries(tmp_path) == []


def test_json_schema_matches_required_fields_and_unknown_cost_contract():
    schema = json.loads((Path(__file__).parents[1] / "community/schema.json").read_text())
    assert set(schema["required"]) == community.REQUIRED
    assert set(schema["properties"]) == community.REQUIRED | community.OPTIONAL
    assert set(schema["properties"]["environment"]["enum"]) == community.ENVIRONMENTS
    assert set(schema["properties"]["result"]["enum"]) == set(community.RESULTS)
    assert set(schema["properties"]["author"]["required"]) == {"name", "github"}
    metrics = schema["properties"]["metrics"]
    for field in ("steps", "wall_seconds", "estimated_cost_usd"):
        assert "null" in metrics["properties"][field]["type"]
    assert metrics["if"]["required"] == ["estimated_cost_usd"]
    assert metrics["if"]["properties"]["estimated_cost_usd"] == {"type": "number"}
    assert metrics["then"]["required"] == ["cost_basis"]


@pytest.mark.parametrize("field,url,accepted", [
    ("video_url", "https://github.com/user-attachments/assets/12345678-abcd", True),
    ("poster_url", "https://github.com/user-attachments/assets/12345678-abcd", True),
    ("video_url", "https://raw.githubusercontent.com/alice/repo/main/replay.MP4", True),
    ("poster_url", "https://alice.github.io/repo/poster.jpeg", True),
    ("poster_url", "https://alice.github.io/repo/replay.mp4", False),
    ("video_url", "https://alice.github.io/repo/poster.png", False),
    ("video_url", "https://github.com/alice/repo/blob/main/replay.mp4", False),
])
def test_schema_and_validator_agree_on_attachments_and_media_types(field, url, accepted):
    schema = json.loads((Path(__file__).parents[1] / "community/schema.json").read_text())
    media_type = schema["properties"][field]["$ref"].rsplit("/", 1)[-1]
    patterns = [schema["$defs"]["mediaUrl"]["pattern"], schema["$defs"][media_type]["allOf"][1]["pattern"]]
    assert all(re.search(pattern, url) for pattern in patterns) == accepted
    record = entry()
    record[field] = url
    if accepted:
        community.validate_entry(record)
    else:
        with pytest.raises(ValueError, match=field):
            community.validate_entry(record)


@pytest.mark.parametrize("field", sorted(community.REQUIRED))
def test_required_information_cannot_be_omitted(field):
    value = entry()
    del value[field]
    with pytest.raises(ValueError, match="missing fields"):
        community.validate_entry(value)


@pytest.mark.parametrize("field,value", [
    ("date", "2026-02-30"), ("date", "20260922"), ("date", "2026-9-22"),
    ("id", "../outside"), ("id", "Alice"), ("result", "unknown"),
    ("environment", "unrecognized"), ("schema_version", True),
    ("environment", []), ("result", {}),
])
def test_invalid_identity_date_and_result(field, value):
    record = entry()
    record[field] = value
    with pytest.raises(ValueError):
        community.validate_entry(record)


@pytest.mark.parametrize("github", ["https://github.com/alice", "@alice", "alice/other", "alice--other", " alice", "alice\n"])
def test_author_profile_is_derived_from_a_username(github):
    record = entry()
    record["author"]["github"] = github
    with pytest.raises(ValueError, match="author.github"):
        community.validate_entry(record)


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///tmp/video.mp4", "http://alice.github.io/video.mp4",
    "https://localhost/video.mp4", "https://127.0.0.1/video.mp4", "https://[::1]/video.mp4",
    "https://alice:secret@github.com/user-attachments/assets/123",
    "https://alice.github.io:443/video.mp4", "https://github.com.evil.test/user-attachments/assets/123",
    "https://alice.github.io.evil.test/video.mp4", "https://example.com/video.mp4",
    "https://alice.github.io/../video.mp4", "https://alice.github.io/%252e%252e/video.mp4",
    "https://alice.github.io/%0avideo.mp4", "https://alice.github.io/video.mp4?token=secret",
    "https://github.com/alice/repo/blob/main/video.mp4", "https://alice.github.io/page.html",
    "https://alice.github.io/video.mp4#section", "https://alice.github.io/%invalid.mp4",
])
def test_media_rejects_private_malformed_or_nonmedia_urls(url):
    record = entry()
    record["video_url"] = url
    with pytest.raises(ValueError, match="video_url"):
        community.validate_entry(record)


@pytest.mark.parametrize("url", [
    "https://github.com/user-attachments/assets/12345678-abcd-abcd-abcd-123456789012",
    "https://github.com/alice/repro/raw/refs/heads/main/replay.mp4",
    "https://raw.githubusercontent.com/alice/repro/main/replay.webm",
    "https://user-images.githubusercontent.com/12345/12345-replay.mp4",
    "https://alice.github.io/repro/replay.mp4",
])
def test_public_direct_media_locations(url):
    record = entry()
    record["video_url"] = url
    assert community.validate_entry(record)["video_url"] == url


@pytest.mark.parametrize("url", [
    "https://github.com/login", "https://github.com/user-attachments/assets/123",
    "https://raw.githubusercontent.com/alice/repro/main/protocol.md",
    "https://github.com@localhost/alice/repo", "https://github.com/alice/repo?token=secret",
])
def test_reproduction_links_are_readable_repository_links(url):
    record = entry()
    record["reproduction_url"] = url
    with pytest.raises(ValueError, match="reproduction_url"):
        community.validate_entry(record)


@pytest.mark.parametrize("metrics", [
    {"steps": True}, {"steps": 1.5}, {"steps": -1}, {"wall_seconds": float("nan")},
    {"estimated_cost_usd": float("inf")}, {"estimated_cost_usd": .01},
    {"unknown_metric": 123}, {"wall_seconds": "123"},
])
def test_metrics_do_not_accept_untraceable_costs_or_invalid_numbers(metrics):
    record = entry()
    record["metrics"] = metrics
    with pytest.raises(ValueError, match="metrics"):
        community.validate_entry(record)


def test_zero_cost_requires_and_displays_explanation():
    record = entry()
    record["metrics"] = {"estimated_cost_usd": 0, "cost_basis": "Local inference; API charge is zero."}
    community.validate_entry(record)
    item = community.gallery_item(record)
    assert item["metrics"][-1]["value"] == "$0.0000"
    assert "Local inference" in item["note"]


def test_filename_must_match_id(tmp_path):
    write_entry(tmp_path, entry(), "other.json")
    with pytest.raises(ValueError, match="filename"):
        community.load_entries(tmp_path)


def test_symlink_files_and_directories_cannot_include_external_content(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    target = write_entry(elsewhere, entry())
    root = tmp_path / "root"
    directory = root / "community/entries"
    directory.mkdir(parents=True)
    symlink = directory / target.name
    symlink.symlink_to(target)
    with pytest.raises(ValueError, match="regular"):
        community.load_entries(root)
    symlink.unlink()
    directory.rmdir()
    directory.symlink_to(target.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        community.load_entries(root)


def test_duplicate_json_keys_cannot_hide_result(tmp_path):
    path = write_entry(tmp_path, entry())
    path.write_text(path.read_text().replace('"result": "partial"', '"result": "success", "result": "partial"'))
    with pytest.raises(ValueError, match="duplicate JSON key"):
        community.load_entries(tmp_path)


def test_dangling_entries_symlink_cannot_hide_invalid_directory(tmp_path):
    (tmp_path / "community").mkdir()
    (tmp_path / "community/entries").symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        community.load_entries(tmp_path)


@pytest.mark.parametrize("field,value", [
    ("author", []), ("environment", {}), ("result", []), ("metrics", None),
    ("video_url", {}), ("metrics", {"steps": 10 ** 400}),
    ("metrics", {"estimated_cost_usd": 10 ** 400, "cost_basis": "invalid huge number"}),
])
def test_malformed_json_fields_report_clear_errors_with_filename(tmp_path, field, value):
    record = entry()
    record[field] = value
    write_entry(tmp_path, record)
    with pytest.raises(ValueError, match=r"community/entries/alice-libero-plate\.json:"):
        community.load_entries(tmp_path)


def test_deeply_nested_json_reports_a_validation_error(tmp_path):
    path = write_entry(tmp_path, entry())
    path.write_text("[" * 1500 + "0" + "]" * 1500)
    with pytest.raises(ValueError, match=r"community/entries/alice-libero-plate\.json:"):
        community.load_entries(tmp_path)


def test_newest_first_and_no_unknown_fields(tmp_path):
    older = entry()
    older.update(id="alice-old", date="2026-09-20")
    write_entry(tmp_path, older)
    write_entry(tmp_path, entry())
    assert [item["date"] for item in community.load_entries(tmp_path)] == ["2026-09-22", "2026-09-20"]
    newer = entry()
    newer["verified"] = True
    write_entry(tmp_path, newer)
    with pytest.raises(ValueError, match="unknown fields"):
        community.load_entries(tmp_path)
