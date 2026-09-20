from fastapi.testclient import TestClient

from embodied_jev.server import create_app


def test_configuration_reset_and_export_contract(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/api/config").status_code == 200
        assert client.post("/api/reset", json={"task": "unknown"}).status_code == 422
        assert client.post("/api/reset", json={"provider": "jev"}).status_code == 422
        assert client.post("/api/reset", json={"task": "stack", "seed": 2}).status_code == 200
        assert client.get("/api/export").json()["task"] == "stack"
        assert client.get("/api/replay/-1").status_code == 404
        assert client.get("/api/replay/0").json()["qpos"]
        assert client.post("/api/control/start", json={"threshold": 2}).status_code == 422
        assert client.post("/api/control/stop", json={}).status_code == 200
        assert client.post("/api/control/start", json={}).status_code == 409


def test_cross_origin_commands_rejected():
    with TestClient(create_app()) as client:
        response = client.post("/api/control/start", json={}, headers={"Origin": "https://other.invalid"})
        assert response.status_code == 403
        assert client.get("/api/state").json()["status"] == "idle"
