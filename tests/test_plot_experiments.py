import pytest

from scripts.plot_experiments import read_rows
from scripts.benchmark_server import ServerBenchmark


def row(**changes):
    result = {"task": "transfer", "seed": 0, "status": "completed", "success": True,
              "cycles": 1, "model_calls": 1, "input_tokens": 12, "output_tokens": 3,
              "wall_seconds": 1.5, "model_latency_ms": [1200]}
    result.update(changes)
    return result


def test_plot_rows_preserve_unknown_usage_instead_of_treating_it_as_zero():
    rows = read_rows({"episodes": [row(input_tokens=None), row(seed=1, output_tokens=None)]})
    assert rows[0]["input_tokens"] is None
    assert rows[1]["output_tokens"] is None


@pytest.mark.parametrize("value", [True, -1, "12"])
def test_plot_rows_still_reject_invalid_reported_usage(value):
    with pytest.raises(ValueError, match="Invalid measured value"):
        read_rows({"episodes": [row(input_tokens=value)]})


def test_benchmark_aggregate_does_not_sum_incomplete_token_totals(tmp_path, monkeypatch):
    benchmark = ServerBenchmark(tmp_path, "fixture", "commit")
    benchmark.episodes = [
        {"success": True, "model_calls": 1, "input_tokens": 12, "output_tokens": 3,
         "model_latency_ms": [100], "forbidden_contact": False},
        {"success": False, "model_calls": 1, "input_tokens": None, "output_tokens": 5,
         "model_latency_ms": [200], "forbidden_contact": False},
    ]
    saved = {}
    monkeypatch.setattr(benchmark, "save", lambda name, value: saved.update(summary=value))
    benchmark.save_summary()
    aggregate = saved["summary"]["aggregate"]
    assert aggregate["input_tokens"] is None
    assert aggregate["output_tokens"] == 8
