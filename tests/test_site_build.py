"""Published LIBERO upgrades preserve unrelated records and existing URLs' targets."""
import gzip
import importlib.util
import json
from pathlib import Path

import pytest


def fixture_file(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    return path


@pytest.mark.parametrize("with_supervisor,with_plate,with_community", [
    (False, False, False), (True, False, False), (False, True, False),
    (True, True, False), (False, False, True),
])
def test_site_build_hides_retired_v2_microwave_and_preserves_successful_v1_and_new_plate(tmp_path, monkeypatch, with_supervisor, with_plate, with_community):
    spec = importlib.util.spec_from_file_location("site_builder", Path(__file__).parents[1] / "scripts/build_site.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / "source"
    monkeypatch.setattr(builder, "ROOT", source)
    for name in ("index.html", "style.css", "app.js", "favicon.svg"):
        fixture_file(source, "site/" + name, b"fixture")
    prefix = "docs/results/metaworld-hierarchy-v2-2026-09-21/"
    fixture_file(source, prefix + "comparison.json", {"methods": []})
    fixture_file(source, prefix + "COSTS.json", {"methods": {}})
    for asset in ("figures/hierarchy-grid.mp4", "figures/hierarchy-grid.png", "comparison.png", "figures/hierarchy-grid-fast.mp4"):
        fixture_file(source, prefix + asset, b"fixture")
    for media, record in (
        ("jev-hierarchical", "jev-hierarchical-live-2026-09-20"),
        ("vision-transfer", "planning-vision-gpt6-v2-cameras-transfer"),
        ("vision-recovery", "planning-vision-gpt6-v2-live-target-shift-run1"),
    ):
        fixture_file(source, "docs/results/" + record + ".json.gz", gzip.compress(json.dumps({
            "history": [], "success": True, "wall_seconds": 1, "model_calls": 0,
        }).encode()))
        for suffix in (".mp4", ".png"):
            fixture_file(source, "docs/media/" + media + suffix, b"fixture")

    def record(identifier):
        item = {"id": identifier, "category": "libero"}
        for field, suffix in (("video", ".mp4"), ("poster", ".png")):
            item[field] = "docs/media/" + identifier + suffix
            fixture_file(source, item[field], identifier.encode())
        item["download"] = item["video"]
        return item

    drawer, microwave = record("libero-drawer"), record("libero-microwave")
    old_index = fixture_file(source, "docs/results/libero-vision/index.json", {"experiments": [drawer, microwave]})
    old_bytes = old_index.read_bytes()
    if with_supervisor:
        latest = record("libero-v2-microwave")
        fixture_file(source, "docs/results/libero-supervisor-v2/index.json", {"experiments": [latest]})
    if with_plate:
        plate = record("libero-supervisor-plate-push-plate")
        fixture_file(source, "docs/results/libero-supervisor-plate/index.json", {"experiments": [plate]})
    if with_community:
        example = json.loads((Path(__file__).parents[1] / "community/examples/entry.example.json").read_text())
        fixture_file(source, "community/entries/" + example["id"] + ".json", example)
    output = tmp_path / "site"
    catalog = builder.build(output)
    ids = [item["id"] for item in catalog["experiments"]]
    assert len(ids) == len(set(ids)) == 6 + int(with_plate) + int(with_community)
    assert {"libero-drawer", "metaworld-hierarchy", "jev-hierarchical", "vision-transfer", "vision-recovery"} <= set(ids)
    assert next(item for item in catalog["experiments"] if item["id"] == "libero-drawer") == drawer
    assert "libero-microwave" in ids
    assert "libero-v2-microwave" not in ids
    assert next(item for item in catalog["experiments"] if item["id"] == "libero-microwave") == microwave
    assert (output / microwave["video"]).read_bytes() == b"libero-microwave"
    if with_supervisor:
        assert not (output / latest["video"]).exists()
    assert ("libero-supervisor-plate-push-plate" in ids) is with_plate
    if with_plate:
        assert ids[0] == "libero-supervisor-plate-push-plate"
        assert next(item for item in catalog["experiments"] if item["id"] == plate["id"]) == plate
        assert (output / plate["video"]).read_bytes() == b"libero-supervisor-plate-push-plate"
    assert json.loads((output / "catalog.json").read_text()) == catalog
    assert old_index.read_bytes() == old_bytes
    if with_community:
        item = catalog["experiments"][-1]
        assert item["origin"] == "community"
        assert item["author"]["github"] == example["author"]["github"]
        assert item["video"] == example["video_url"]
        assert item["metrics"][-1]["value"] == "未提供"
        assert not list(output.rglob("*.webm"))  # Contributor media stays external.
