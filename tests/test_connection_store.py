from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from embodied_jev.connection_store import FORMAT, SERVICE, SystemConnectionStore, default_config_dir
from embodied_jev.server import create_app


class FakeKeyring:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.fail_get = self.fail_set = self.fail_delete = False

    def get_password(self, service, account):
        self.calls.append(("get", service, account))
        if self.fail_get:
            raise RuntimeError("synthetic-private-key must never leak")
        return self.values.get((service, account))

    def set_password(self, service, account, password):
        self.calls.append(("set", service, account))
        if self.fail_set:
            raise RuntimeError(password)
        self.values[service, account] = password

    def delete_password(self, service, account):
        self.calls.append(("delete", service, account))
        if self.fail_delete:
            raise RuntimeError("synthetic-private-key must never leak")
        self.values.pop((service, account), None)


def settings(**changes):
    return {"url": "https://first.invalid/v1/chat/completions", "model": "test-model",
            "key": "synthetic-private-key", "json_mode": True, **changes}


def payload(**changes):
    return {"provider": "chat", "url": "https://first.invalid/v1", "model": "test-model",
            "api_key": "synthetic-private-key", **changes}


def test_roundtrip_reads_only_own_metadata_refs_and_no_key_is_written(tmp_path):
    backend = FakeKeyring()
    store = SystemConnectionStore(tmp_path, backend=backend)
    assert store.save_connection("chat", settings())["persistent"]
    profile = {**settings(key="profile-synthetic-secret"), "provider": "chat", "id": "ab12cd34ef56", "name": "平台一"}
    assert store.save_profile(profile)["persistent"]
    metadata = json.loads(store.path.read_text())
    assert metadata["format"] == FORMAT
    raw = store.path.read_text()
    assert "synthetic-private-key" not in raw and "profile-synthetic-secret" not in raw
    assert '"key"' not in raw and '"api_key"' not in raw
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    backend.calls.clear()
    restored = SystemConnectionStore(tmp_path, backend=backend).load()
    assert restored["connections"]["chat"]["key"] == "synthetic-private-key"
    assert restored["profiles"][profile["id"]]["key"] == "profile-synthetic-secret"
    references = {item["secret_ref"] for section in ("connections", "profiles") for item in metadata[section].values()}
    assert {account for operation, service, account in backend.calls if operation == "get" and service == SERVICE} == references
    assert all(operation == "get" and service == SERVICE for operation, service, account in backend.calls)
    assert not list(tmp_path.glob(".connections-*.tmp"))


def test_failed_metadata_commit_cannot_bind_old_endpoint_to_new_key(tmp_path, monkeypatch):
    backend = FakeKeyring()
    store = SystemConnectionStore(tmp_path, backend=backend)
    store.save_connection("chat", settings())
    original_bytes = store.path.read_bytes()
    original_refs = dict(backend.values)

    def fail_write(value):
        raise OSError("synthetic-private-key disk failed")

    monkeypatch.setattr(store, "_write_metadata", fail_write)
    result = store.save_connection("chat", settings(url="https://second.invalid/v1/chat/completions", key="new-synthetic-key"))
    assert not result["persistent"] and result["mode"] == "memory_only"
    assert "synthetic-private-key" not in json.dumps(result) and "new-synthetic-key" not in json.dumps(result)
    assert store.path.read_bytes() == original_bytes and backend.values == original_refs
    restored = SystemConnectionStore(tmp_path, backend=backend).load()["connections"]["chat"]
    assert restored["url"].startswith("https://first.invalid") and restored["key"] == "synthetic-private-key"


def test_successful_update_commits_new_ref_before_removing_old_key(tmp_path):
    backend = FakeKeyring()
    store = SystemConnectionStore(tmp_path, backend=backend)
    store.save_connection("chat", settings())
    old_ref = json.loads(store.path.read_text())["connections"]["chat"]["secret_ref"]
    backend.calls.clear()
    assert store.save_connection("chat", settings(url="https://second.invalid/v1/chat/completions", key="new-synthetic-key"))["persistent"]
    new_ref = json.loads(store.path.read_text())["connections"]["chat"]["secret_ref"]
    assert new_ref != old_ref and (SERVICE, old_ref) not in backend.values
    assert backend.calls[0] == ("set", SERVICE, new_ref)
    assert backend.calls[-1] == ("delete", SERVICE, old_ref)
    assert SystemConnectionStore(tmp_path, backend=backend).load()["connections"]["chat"]["key"] == "new-synthetic-key"


def test_os_rejection_uses_memory_without_plaintext_fallback(tmp_path):
    backend = FakeKeyring()
    backend.fail_set = True
    store = SystemConnectionStore(tmp_path, backend=backend)
    result = store.save_connection("chat", settings())
    assert not result["persistent"] and not store.path.exists()
    assert "synthetic-private-key" not in json.dumps(result)
    assert all(path.name == ".connections.lock" and "synthetic-private-key" not in path.read_text()
               for path in tmp_path.iterdir())


def test_startup_rejection_does_not_delete_keys_and_keeps_environment_fallback(tmp_path, monkeypatch):
    backend = FakeKeyring()
    SystemConnectionStore(tmp_path, backend=backend).save_connection("chat", settings())
    backend.calls.clear()
    backend.fail_get = True
    monkeypatch.setenv("EMBODIED_API_BASE", "https://environment.invalid/v1")
    monkeypatch.setenv("EMBODIED_API_MODEL", "environment-model")
    monkeypatch.setenv("EMBODIED_API_KEY", "environment-synthetic-key")
    with TestClient(create_app(store=SystemConnectionStore(tmp_path, backend=backend))) as client:
        current = client.get("/api/connections").json()["chat"]
        assert current["url"] == "https://environment.invalid/v1/chat/completions"
        assert current["key_configured"] and not current["storage"]["persistent"]
    assert all(operation == "get" for operation, _, _ in backend.calls)
    assert backend.values


@pytest.mark.parametrize("tamper", ["plaintext", "foreign_ref"])
def test_invalid_metadata_is_not_a_general_keychain_reader_or_plaintext_key_import(tmp_path, tamper):
    backend = FakeKeyring()
    store = SystemConnectionStore(tmp_path, backend=backend)
    store.save_connection("chat", settings())
    metadata = json.loads(store.path.read_text())
    if tamper == "plaintext":
        metadata["connections"]["chat"]["api_key"] = "do-not-import-secret"
    else:
        metadata["connections"]["chat"]["secret_ref"] = "someone-elses-account"
    store.path.write_text(json.dumps(metadata))
    backend.calls.clear()
    restored = SystemConnectionStore(tmp_path, backend=backend).load()
    assert restored["connections"] == {} and not restored["storage"]["persistent"]
    assert backend.calls == []


def test_plaintext_keyring_backend_is_never_accepted(tmp_path, monkeypatch):
    backend = type("PlaintextKeyring", (), {"__module__": "keyrings.alt.file"})()
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_keyring=lambda: backend))
    store = SystemConnectionStore(tmp_path)
    assert not store.status()["persistent"]
    assert not store.save_connection("chat", settings())["persistent"]
    assert not store.path.exists()


def test_saved_connections_and_profiles_restore_after_server_restart(tmp_path):
    backend = FakeKeyring()
    with TestClient(create_app(store=SystemConnectionStore(tmp_path, backend=backend))) as client:
        saved = client.post("/api/connections", json=payload()).json()
        profile = client.post("/api/model-profiles", json={**payload(), "name": "平台二", "url": "https://profile.invalid/v1"}).json()
        assert saved["storage"]["persistent"] and profile["storage"]["persistent"]
    with TestClient(create_app(store=SystemConnectionStore(tmp_path, backend=backend))) as client:
        connection = client.get("/api/connections").json()["chat"]
        assert connection["key_configured"] and connection["storage"]["persistent"]
        profiles = client.get("/api/model-profiles").json()
        assert profiles["storage"]["persistent"] and profiles["profiles"][0]["id"] == profile["id"]
        for path in ("/api/connections", "/api/model-profiles", "/api/state", "/api/export"):
            assert "synthetic-private-key" not in client.get(path).text
        changed = client.post("/api/connections", json=payload(url="https://new.invalid/v1", api_key="")).json()
        assert changed["storage"]["persistent"] and not changed["key_configured"]
    with TestClient(create_app(store=SystemConnectionStore(tmp_path, backend=backend))) as client:
        connection = client.get("/api/connections").json()["chat"]
        assert not connection["key_configured"] and connection["url"] == "https://new.invalid/v1/chat/completions"


def test_failed_save_keeps_current_memory_and_last_committed_disk(tmp_path, monkeypatch):
    backend = FakeKeyring()
    store = SystemConnectionStore(tmp_path, backend=backend)
    with TestClient(create_app(store=store)) as client:
        client.post("/api/connections", json=payload())
        monkeypatch.setattr(store, "_write_metadata", lambda value: (_ for _ in ()).throw(OSError("synthetic-private-key")))
        failed = client.post("/api/connections", json=payload(url="https://new.invalid/v1", api_key="replacement-secret"))
        assert failed.status_code == 200 and not failed.json()["storage"]["persistent"]
        assert "synthetic-private-key" not in failed.text and "replacement-secret" not in failed.text
        assert client.get("/api/connections").json()["chat"]["url"].startswith("https://new.invalid")
    restored = SystemConnectionStore(tmp_path, backend=backend).load()["connections"]["chat"]
    assert restored["url"].startswith("https://first.invalid") and restored["key"] == "synthetic-private-key"


@pytest.mark.parametrize("target", ["connection", "profile"])
def test_other_saved_key_cannot_be_pasted_into_public_metadata(tmp_path, target):
    backend = FakeKeyring()
    store = SystemConnectionStore(tmp_path, backend=backend)
    with TestClient(create_app(store=store)) as client:
        client.post("/api/connections", json=payload())
        before = store.path.read_bytes()
        if target == "connection":
            response = client.post("/api/connections", json=payload(model="model-synthetic-private-key", api_key="another-secret"))
        else:
            response = client.post("/api/model-profiles", json={**payload(api_key="another-secret"), "name": "synthetic-private-key"})
        assert response.status_code == 422 and "synthetic-private-key" not in response.text
        assert store.path.read_bytes() == before


def test_keychain_permission_wait_does_not_block_robot_controls(tmp_path):
    entered, release = threading.Event(), threading.Event()
    backend = FakeKeyring()
    original = backend.set_password

    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    backend.set_password = delayed
    with TestClient(create_app(store=SystemConnectionStore(tmp_path, backend=backend))) as client, ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(client.post, "/api/connections", json=payload())
        try:
            assert entered.wait(5)
            stopped = pool.submit(client.post, "/api/control/stop", json={}).result(timeout=2)
            assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
        finally:
            release.set()
        assert saving.result(timeout=5).json()["storage"]["persistent"]


def test_interleaved_store_instances_do_not_restore_deleted_old_references(tmp_path):
    backend = FakeKeyring()
    first = SystemConnectionStore(tmp_path, backend=backend)
    first.save_connection("chat", settings())
    stale = SystemConnectionStore(tmp_path, backend=backend)
    stale.load()
    old_ref = stale.metadata["connections"]["chat"]["secret_ref"]
    first.save_connection("chat", settings(key="rotated-synthetic-key", url="https://rotated.invalid/v1/chat/completions"))
    assert (SERVICE, old_ref) not in backend.values
    profile = {**settings(key="profile-key"), "provider": "chat", "id": "ab12cd34ef56", "name": "独立平台"}
    assert stale.save_profile(profile)["persistent"]
    restored = SystemConnectionStore(tmp_path, backend=backend).load()
    assert restored["connections"]["chat"]["url"].startswith("https://rotated.invalid")
    assert restored["connections"]["chat"]["key"] == "rotated-synthetic-key"
    assert restored["profiles"][profile["id"]]["key"] == "profile-key"


def test_load_holds_file_lock_until_referenced_key_is_read(tmp_path):
    backend = FakeKeyring()
    writer = SystemConnectionStore(tmp_path, backend=backend)
    writer.save_connection("chat", settings())
    old_ref = writer.metadata["connections"]["chat"]["secret_ref"]
    reader = SystemConnectionStore(tmp_path, backend=backend)
    entered, release, writer_entered = threading.Event(), threading.Event(), threading.Event()
    original_get, original_set = backend.get_password, backend.set_password

    def blocked_read(service, account):
        entered.set()
        assert release.wait(5)
        return original_get(service, account)

    def observed_write(*args):
        writer_entered.set()
        return original_set(*args)

    backend.get_password, backend.set_password = blocked_read, observed_write
    with ThreadPoolExecutor(max_workers=2) as pool:
        loading = pool.submit(reader.load)
        assert entered.wait(5)
        saving = pool.submit(writer.save_connection, "chat", settings(key="new-synthetic-key"))
        try:
            assert not writer_entered.wait(.15)
            assert (SERVICE, old_ref) in backend.values
        finally:
            release.set()
        assert loading.result(timeout=5)["connections"]["chat"]["key"] == "synthetic-private-key"
        assert saving.result(timeout=5)["persistent"]
    assert (SERVICE, old_ref) not in backend.values
    assert SystemConnectionStore(tmp_path, backend=backend).load()["connections"]["chat"]["key"] == "new-synthetic-key"


def test_platform_configuration_paths_are_outside_the_working_directory():
    home = Path("/synthetic-user")
    assert default_config_dir("darwin", {}, home) == home / "Library/Application Support/EmbodiedJev"
    assert default_config_dir("win32", {"LOCALAPPDATA": "/local-app-data"}, home) == Path("/local-app-data/EmbodiedJev")
    assert default_config_dir("linux", {"XDG_CONFIG_HOME": "/user-config"}, home) == Path("/user-config/embodied-jev")
    assert default_config_dir("linux", {"XDG_CONFIG_HOME": "relative-path"}, home) == home / ".config/embodied-jev"


@pytest.mark.parametrize("flag", ["argument", "environment"])
def test_cli_memory_mode_never_initializes_system_store(monkeypatch, flag):
    from embodied_jev.cli import main
    captured = []
    monkeypatch.setattr("embodied_jev.connection_store.SystemConnectionStore", lambda: pytest.fail("Must not inspect real credentials"))
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: captured.append((app, kwargs)))
    monkeypatch.setattr("embodied_jev.eventlog.configure_logging", lambda *args: None)
    monkeypatch.delenv("EMBODIED_JEV_PERSISTENCE", raising=False)
    if flag == "environment":
        monkeypatch.setenv("EMBODIED_JEV_PERSISTENCE", "memory")
    monkeypatch.setattr(sys, "argv", ["embodied-jev", "serve"] + (["--memory-only"] if flag == "argument" else []))
    main()
    assert captured[0][0].state.store is None
    assert captured[0][1]["host"] == "127.0.0.1"
    captured[0][0].state.session.stop()
