"""Contract fixtures only; no performance measurements or real model calls."""
import copy
import csv
import hashlib
import json

import pytest

from embodied_jev import evaluation_charts as charts


def report(policy="jev"):
    """Explicit test data for aggregation; never a user-facing benchmark result."""
    manifest = {"format": "embodied-jev-suite-v1", "name": "contract-fixture",
        "backend": "metaworld", "split": "development", "cases": [
            {"id": "reach-0", "task": "reach-v3", "seed": 0},
            {"id": "reach-1", "task": "reach-v3", "seed": 1}]}
    config = {"policy": policy, "configured_model": "fixture-" + policy,
        "max_steps": 200, "max_calls": 40, "timeout": 300,
        "threshold": 0, "action_scale": .5, "control_mode": "normalized_xyz_gripper",
        "observation_mode": "privileged", "action_repeat": 5,
        "connection_source": "saved"}
    episodes = []
    for case in manifest["cases"]:
        episodes.append({**case, "status": "success", "success": True, "steps": 10,
            "model_calls": 2, "wall_seconds": 3., "model_latency_ms": [10000., 20000.],
            "input_tokens": 20, "output_tokens": 4,
            "initial_observation_sha256": hashlib.sha256(case["id"].encode()).hexdigest(),
            "metadata": {"success_source": "official info.success", "action_spec": {"control_hz": 80}},
            "api_calls": [{"latency_ms": delay, "http_status": 200,
                           "input_tokens": 10, "output_tokens": 2, "model": "fixture-" + policy}
                          for delay in (100, 300)]})
    return {"format": "embodied-jev-evaluation-v1", "manifest": manifest,
        "manifest_sha256": charts._hash(manifest), "source_sha256": {"evaluation.py": "a" * 64},
        "configuration": config, "episodes": episodes}


def test_complete_comparison_uses_http_latency_and_official_counts():
    data = charts.compare_reports([report(), report("chat")])
    method = data["methods"][0]
    assert data["complete"] and data["paired_initial_states"] == 2
    assert method["successes"] == method["planned"] == 2
    assert method["success_rate"] == 1
    assert method["wilson_95"][0] == pytest.approx(.3423802)
    assert method["latency_n"] == 4
    assert method["latency_p50_ms"] == 200
    assert method["latency_p95_ms"] == 300
    assert data["episodes"][0]["simulated_seconds"] == .125
    assert method["token_usage"]["input"]["reported_tokens"] == 40


def test_partial_report_does_not_turn_missing_case_into_100_percent():
    partial = report()
    partial["episodes"].pop()
    data = charts.compare_reports([partial, report("chat")])
    method = data["methods"][0]
    assert not data["complete"] and data["paired_initial_states"] == 1
    assert method["observed_success_rate"] == 1
    assert method["success_rate"] is None and method["wilson_95"] is None
    assert method["verified_success_fraction"] == .5 and method["missing"] == 1
    missing = data["episodes"][1]
    assert missing["status"] == "missing" and missing["input_tokens"] is None
    assert method["failures"] == {"missing": 1}


def test_setup_error_retains_unscored_case_and_unpaired_coverage():
    failed = report()
    failed["episodes"][1] = {"id": "reach-1", "success": None, "status": "setup_error",
                             "model_calls": 0, "input_tokens": 0, "output_tokens": 0}
    data = charts.compare_reports([failed, report("chat")])
    method = data["methods"][0]
    assert method["unscored"] == 1 and not method["complete"]
    assert method["failures"] == {"setup_error": 1}
    assert data["paired_initial_states"] == 1
    assert data["episodes"][1]["input_tokens"] is None
    assert not data["episodes"][1]["input_complete"]


def test_runtime_error_keeps_consumed_usage_and_failed_http_latency():
    failed = report()
    row = failed["episodes"][1]
    row.update(success=False, status="runtime_error")
    row["api_calls"][1].update(http_status=503, latency_ms=1000, error_type="HTTPStatusError")
    data = charts.compare_reports([failed, report("chat")])
    method = data["methods"][0]
    assert method["http_requests"] == 4 and method["latency_n"] == 4
    assert method["latency_p95_ms"] == pytest.approx(895)
    assert method["token_usage"]["input"]["reported_tokens"] == 40
    assert method["failures"] == {"runtime_error": 1} and not data["complete"]


def test_missing_usage_is_unknown_and_partial_totals_remain_auditable():
    incomplete = report()
    row = incomplete["episodes"][0]
    row["api_calls"][0]["input_tokens"] = None
    row["input_tokens"] = None
    data = charts.compare_reports([incomplete, report("chat")])
    episode = data["episodes"][0]
    assert episode["input_tokens"] is None and not episode["input_complete"]
    assert episode["input_reported_tokens"] == 10 and episode["input_reported_calls"] == 1
    assert episode["output_tokens"] == 4 and episode["output_complete"]
    assert data["methods"][0]["token_usage"]["input"]["reported_calls"] == 3


def test_pre_request_failure_is_not_reported_as_complete_usage():
    incomplete = report()
    row = incomplete["episodes"][0]
    row.update(model_calls=3, input_tokens=None, output_tokens=None)
    data = charts.compare_reports([incomplete, report("chat")])
    assert data["episodes"][0]["http_requests"] == 2
    assert data["episodes"][0]["input_tokens"] is None
    assert data["methods"][0]["model_calls"] == 5


@pytest.mark.parametrize("key", charts.PROTOCOL_KEYS)
def test_all_action_observation_and_budget_settings_must_match(key):
    changed = report("chat")
    changed["configuration"][key] = "different"
    with pytest.raises(ValueError, match="protocol differs"):
        charts.compare_reports([report(), changed])


def test_additional_protocol_fields_cannot_silently_differ():
    changed = report("chat")
    changed["configuration"]["camera_profile"] = "different-input-rights"
    with pytest.raises(ValueError, match="protocol differs"):
        charts.compare_reports([report(), changed])


def test_renderer_refuses_empty_measurement_bundle_before_importing_matplotlib(tmp_path):
    with pytest.raises(ValueError, match="No measured HTTP"):
        charts.draw({"episodes": [], "methods": []}, tmp_path)


@pytest.mark.parametrize("change, message", [
    (lambda r: r["source_sha256"].update({"evaluation.py": "b" * 64}), "source hashes"),
    (lambda r: r["episodes"][0].update(initial_observation_sha256="b" * 64), "initial observations differ"),
    (lambda r: r["episodes"][0]["metadata"]["action_spec"].update(control_hz=20), "metadata"),
    (lambda r: r["manifest"].update(name="tampered"), "Manifest hash"),
    (lambda r: r["episodes"].append(copy.deepcopy(r["episodes"][0])), "Duplicate episode"),
    (lambda r: r.update(episodes=[]), "Unrun report"),
    (lambda r: r["episodes"][0].pop("initial_observation_sha256"), "initial observation hash"),
    (lambda r: r["episodes"][0].update(seed=77), "task or seed"),
    (lambda r: r["episodes"][0].update(input_tokens=0), "token total disagrees"),
    (lambda r: r.update(synthetic=True), "Only recorded"),
])
def test_incomparable_or_untrusted_reports_are_rejected(change, message):
    changed = report("chat")
    change(changed)
    with pytest.raises(ValueError, match=message):
        charts.compare_reports([report(), changed])


def test_duplicate_and_legacy_nonmetered_reports_are_rejected():
    with pytest.raises(ValueError, match="Duplicate report"):
        charts.compare_reports([report(), report()])
    legacy = report("chat")
    for row in legacy["episodes"]:
        row.pop("api_calls")
        row.update(input_tokens=None, output_tokens=None)
    with pytest.raises(ValueError, match="No measured HTTP"):
        charts.compare_reports([report(), legacy])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_invalid_latency_values_are_rejected(value):
    invalid = report()
    invalid["episodes"][0]["api_calls"][0]["latency_ms"] = value
    with pytest.raises(ValueError, match="nonnegative measurement"):
        charts.compare_reports([invalid, report("chat")])


def test_baseline_requires_explicit_labelled_opt_in():
    baseline = report("scripted")
    for row in baseline["episodes"]:
        row.update(model_calls=0, api_calls=[], input_tokens=0, output_tokens=0)
    # Its different controller cannot masquerade as a matched model comparison.
    with pytest.raises(ValueError, match="allow-baseline"):
        charts.compare_reports([report(), baseline])
    result = charts.compare_reports([report(), baseline], allow_baseline=True)
    method = result["methods"][1]
    assert "[baseline]" in method["label"] and method["latency_p50_ms"] is None
    assert method["http_requests"] == 0


def test_exports_keep_missing_rows_and_exclude_connections(tmp_path):
    first, second = report(), report("chat")
    first["configuration"]["_connection"] = {"key": "never-export-this", "url": "private.invalid"}
    first["episodes"].pop()
    paths = [tmp_path / "first.json", tmp_path / "second.json"]
    for path, value in zip(paths, (first, second)):
        path.write_text(json.dumps(value))
    output = tmp_path / "comparison"
    charts.main(["--reports", *map(str, paths), "--output", str(output), "--data-only"])
    text = (output / "comparison.json").read_text()
    assert "never-export-this" not in text and "private.invalid" not in text
    records = list(csv.DictReader((output / "episodes.csv").open()))
    assert len(records) == 4
    assert records[1]["status"] == "missing" and records[1]["input_tokens"] == ""
    task_rows = list(csv.DictReader((output / "task_summary.csv").open()))
    assert len(task_rows) == 4 and "api_calls" not in task_rows[0]
    assert "partial" in (output / "FIGURE_NOTES.md").read_text()
    with pytest.raises(FileExistsError):
        charts.main(["--reports", *map(str, paths), "--output", str(output), "--data-only"])
    with pytest.raises(ValueError, match="Duplicate report path"):
        charts.load_comparison([paths[0], paths[0]])


def test_reject_unrun_before_creating_output(tmp_path):
    unrun = report()
    unrun["episodes"] = []
    path = tmp_path / "unrun.json"
    other = tmp_path / "other.json"
    path.write_text(json.dumps(unrun))
    other.write_text(json.dumps(report("chat")))
    output = tmp_path / "should-not-exist"
    with pytest.raises(ValueError, match="Unrun"):
        charts.main(["--reports", str(path), str(other), "--output", str(output), "--data-only"])
    assert not output.exists()
