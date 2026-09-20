"""Camera selection reaches episode constructors without rendering or model calls."""
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from embodied_jev import cli, comparison, server


class StubSession:
    created = []

    def __init__(self, **config):
        self.config = copy.deepcopy(config)
        self.camera_views = config.get("camera_views") or []
        self.id = f"camera-test-{len(self.created)}"
        self.cached = None
        self.stopped = False
        self.created.append(self)

    def snapshot(self):
        return {"id": self.id, "camera_views": self.camera_views}

    def camera_snapshot(self):
        return self.cached

    def stop(self):
        self.stopped = True


@pytest.fixture
def app(monkeypatch):
    StubSession.created = []
    monkeypatch.setattr(server, "Session", StubSession)
    return server.create_app()


@pytest.mark.parametrize("views", [None, [], ["external"], ["wrist"], ["external", "wrist"]])
def test_episode_api_forwards_camera_selection_unchanged(app, views):
    with TestClient(app) as client:
        payload = {} if views is None else {"camera_views": views}
        response = client.post("/api/reset", json=payload)
        assert response.status_code == 200
        assert app.state.session.config["camera_views"] == views


@pytest.mark.parametrize("views", [["other"], "wrist", [1], [None]])
def test_invalid_camera_names_are_rejected_before_episode_replacement(app, views):
    original = app.state.session
    with TestClient(app) as client:
        response = client.post("/api/reset", json={"camera_views": views})
        assert response.status_code == 422
        assert app.state.session is original and not original.stopped


@pytest.mark.parametrize("views", [None, [], ["wrist"], ["external", "wrist"]])
def test_comparison_api_forwards_selection_and_every_lane_receives_it(app, monkeypatch, views):
    monkeypatch.setattr(comparison, "Session", StubSession)
    created = []

    class CapturedComparison(comparison.Comparison):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def snapshot(self):
            return {"id": self.id, "config": self.config}

    monkeypatch.setattr(server, "Comparison", CapturedComparison)
    with TestClient(app) as client:
        response = client.post("/api/comparison", json={"camera_views": views,
            "lanes": [{"provider": "baseline"}, {"provider": "baseline"}]})
        assert response.status_code == 200
        result = created[0]
        assert result.config["camera_views"] == views
        assert all(lane["session"].config["camera_views"] == views for lane in result.lanes)
        if views is not None:
            views.append("external")
            assert result.config["camera_views"] != views


@pytest.mark.parametrize("image_name", ["rgb", "depth"])
def test_wrist_only_route_never_returns_top_level_image_as_external(app, image_name):
    session = app.state.session
    session.camera_views = ["wrist"]
    session.cached = {"metadata": {"capture_id": 7}, "rgb": b"top-level-image",
                      "depth": b"top-level-depth", "views": {"wrist": {
                          "rgb": b"wrist-rgb", "depth": b"wrist-depth", "depth_display": b"wrist-display"}}}
    with TestClient(app) as client:
        query = {"episode_id": session.id, "capture_id": "7"}
        response = client.get(f"/api/perception/{image_name}.png", params={**query, "view": "wrist"})
        assert response.status_code == 200
        assert response.content == (b"wrist-rgb" if image_name == "rgb" else b"wrist-display")
        assert client.get(f"/api/perception/{image_name}.png", params={**query, "view": "external"}).status_code == 404
        # A missing view in an explicit cache map stays unavailable even if the
        # configuration originally enabled it; the top-level alias is not used.
        session.camera_views = ["external", "wrist"]
        assert client.get(f"/api/perception/{image_name}.png", params={**query, "view": "external"}).status_code == 404
        session.camera_views = []
        assert client.get(f"/api/perception/{image_name}.png", params={**query, "view": "wrist"}).status_code == 404


def test_external_only_legacy_cache_still_requires_the_enabled_view(app):
    session = app.state.session
    session.camera_views = ["external"]
    session.cached = {"metadata": {"capture_id": 2}, "rgb": b"external", "depth": b"depth"}
    with TestClient(app) as client:
        query = {"episode_id": session.id}
        assert client.get("/api/perception/rgb.png", params=query).content == b"external"
        assert client.get("/api/perception/rgb.png", params={**query, "view": "wrist"}).status_code == 404
        session.camera_views = ["wrist"]
        assert client.get("/api/perception/rgb.png", params=query).status_code == 404


@pytest.mark.parametrize("option,expected", [(None, None), ("none", []), ("external", ["external"]),
    ("wrist", ["wrist"]), ("both", ["external", "wrist"])])
def test_cli_camera_switch_reaches_headless_session_and_summary(monkeypatch, tmp_path, option, expected):
    captured = []
    exported = {"success": False, "interventions": [], "camera_views": expected or [], "model": "mock",
        "model_runtime": None, "policy_version": "test", "model_calls": 0, "input_tokens": 0,
        "output_tokens": 0, "model_latency_ms": [], "wall_seconds": 0, "last_decision": None}

    def run_headless(task, seed, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(export=lambda: exported, status="exhausted", cycles=0, message=None,
            world=SimpleNamespace(max_lift=0, unsafe_contacts=0), worker=SimpleNamespace(is_alive=lambda: False))

    monkeypatch.setattr("embodied_jev.runtime.run_headless", run_headless)
    output = tmp_path / "camera-benchmark.json"
    argv = ["embodied-jev", "benchmark", "--tasks", "transfer", "--seeds", "0", "--output", str(output)]
    if option is not None:
        argv.extend(["--cameras", option])
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    assert len(captured) == 1 and captured[0]["camera_views"] == expected
    assert json.loads(output.read_text())["results"][0]["camera_views"] == exported["camera_views"]
