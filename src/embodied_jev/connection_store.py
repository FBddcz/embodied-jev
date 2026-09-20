"""Persist public connection metadata separately from OS-managed credentials."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import uuid

from filelock import FileLock

SERVICE = "EmbodiedJev"
FORMAT = "embodied-jev-connections-v1"
PROVIDERS = {"jev", "chat", "local", "claude"}
_AUTO = object()


def memory_storage(message=None):
    return {"persistent": False, "mode": "memory_only", "message": message or
            "仅保留在当前服务内存；刷新页面不会丢失，服务重启后需重新配置。"}


def default_config_dir(platform=None, environ=None, home=None):
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if platform == "darwin":
        return home / "Library" / "Application Support" / SERVICE
    if platform == "win32":
        configured = Path(environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return (configured if configured.is_absolute() else home / "AppData" / "Local") / SERVICE
    configured = Path(environ.get("XDG_CONFIG_HOME", home / ".config"))
    return (configured if configured.is_absolute() else home / ".config") / "embodied-jev"


def _system_backend():
    import keyring
    backend = keyring.get_keyring()
    allowed = {"keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService", "keyring.backends.kwallet"}
    if type(backend).__module__ not in allowed:
        # In particular, never accept keyrings.alt plaintext/encrypted file fallbacks
        # or a chainer that could silently route credentials to one of those files.
        raise RuntimeError("No supported OS credential backend")
    return backend


class SystemConnectionStore:
    def __init__(self, config_dir=None, *, backend=_AUTO):
        self.directory = Path(config_dir) if config_dir is not None else default_config_dir()
        self.path = self.directory / "connections.json"
        self.lock = threading.RLock()
        self.metadata = {"format": FORMAT, "connections": {}, "profiles": {}}
        self.loaded = False
        self.backend = None
        self._status = memory_storage()
        try:
            self.backend = _system_backend() if backend is _AUTO else backend
            if self.backend is None:
                raise RuntimeError("No credential backend")
            self._status = self._persistent_status("配置将保存在本机系统钥匙串与应用配置目录，服务重启后可恢复。")
        except Exception:
            self._status = self._unavailable()

    @staticmethod
    def _persistent_status(message="配置已安全保存到本机；Key 位于系统钥匙串，服务重启后可恢复。"):
        return {"persistent": True, "mode": "system_keyring", "message": message}

    @staticmethod
    def _unavailable():
        return memory_storage("系统钥匙串不可用或访问被拒绝；本次配置仅保留内存，重启后本次修改不会恢复，未写入明文 Key。")

    def status(self):
        with self.lock:
            return dict(self._status)

    @staticmethod
    def _validate_item(identity, item, *, profile=False):
        common = {"url", "model", "json_mode", "secret_ref"}
        allowed = common | ({"id", "name", "provider"} if profile else set())
        if not isinstance(item, dict) or set(item) != allowed:
            raise ValueError("Invalid metadata fields")
        provider = item.get("provider") if profile else identity
        if provider not in PROVIDERS:
            raise ValueError("Invalid provider")
        if profile and (not re.fullmatch(r"[a-f0-9]{12}", identity) or item["id"] != identity
                        or not isinstance(item["name"], str) or not 1 <= len(item["name"]) <= 80):
            raise ValueError("Invalid profile")
        if not isinstance(item["model"], str) or not 1 <= len(item["model"]) <= 256 or type(item["json_mode"]) is not bool:
            raise ValueError("Invalid model metadata")
        parsed = urlsplit(item["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid endpoint metadata")
        if provider == "jev" and item["url"] != "https://api.typesafe.ai/v1/systemone":
            raise ValueError("Invalid Jev endpoint")
        prefix = f"profile:{identity}:" if profile else f"connection:{identity}:"
        if item["secret_ref"] is not None and (not isinstance(item["secret_ref"], str)
                or not re.fullmatch(re.escape(prefix) + r"[a-f0-9]{32}", item["secret_ref"])):
            raise ValueError("Invalid credential reference")

    def _check_location(self):
        directory = self.directory.resolve()
        if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
            raise ValueError("Application metadata must stay outside repositories")

    def _read_metadata(self):
        self._check_location()
        if not self.path.exists():
            return {"format": FORMAT, "connections": {}, "profiles": {}}
        if self.path.is_symlink() or self.path.stat().st_size > 1024 * 1024:
            raise ValueError("Invalid metadata file")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"format", "connections", "profiles"} or value["format"] != FORMAT:
            raise ValueError("Invalid metadata format")
        for section in ("connections", "profiles"):
            if not isinstance(value[section], dict):
                raise ValueError("Invalid metadata section")
            for identity, item in value[section].items():
                self._validate_item(identity, item, profile=section == "profiles")
        return value

    @contextmanager
    def _metadata_lock(self):
        self._check_location()
        lock_path = self.directory / ".connections.lock"
        if self.path.is_symlink() or lock_path.is_symlink():
            raise ValueError("Refusing symlink storage")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(lock_path, timeout=2, mode=0o600):
            yield

    def load(self):
        with self.lock:
            result = {"connections": {}, "profiles": {}, "connection_storage": {}, "profile_storage": {}}
            try:
                # Hold the same inter-process lock while retrieving referenced
                # credentials, so another server cannot rotate/delete them midway.
                with self._metadata_lock():
                    self.metadata = self._read_metadata()
                    self.loaded = True
                    self._status = self._persistent_status() if self.backend is not None else self._unavailable()
                    failed = False
                    for section in ("connections", "profiles"):
                        for identity, item in self.metadata[section].items():
                            restored = {key: value for key, value in item.items() if key != "secret_ref"}
                            key = ""
                            available = self.backend is not None
                            if item["secret_ref"] is not None:
                                try:
                                    if self.backend is None:
                                        raise RuntimeError("No credential backend")
                                    key = self.backend.get_password(SERVICE, item["secret_ref"])
                                    if not isinstance(key, str) or not key:
                                        raise ValueError("Credential unavailable")
                                except Exception:
                                    key, available, failed = "", False, True
                            restored["key"] = key
                            status = self._persistent_status() if available else self._unavailable()
                            if section == "profiles" or available:
                                result[section][identity] = restored
                            # Unreadable saved defaults must not shadow environment fallback.
                            result["profile_storage" if section == "profiles" else "connection_storage"][identity] = status
                    if failed:
                        self._status = self._unavailable()
            except Exception:
                self._status = memory_storage("本机配置文件无效或无法读取；已使用内存模式，不会读取其中的钥匙串引用或覆盖该文件。")
                self.loaded = False
                return {**result, "storage": self.status()}
            return {**result, "storage": self.status()}

    def _write_metadata(self, value):
        self._check_location()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("Refusing symlink metadata")
        descriptor, temporary = tempfile.mkstemp(prefix=".connections-", suffix=".tmp", dir=self.directory)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = None
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _save(self, section, identity, value):
        with self.lock:
            if self.backend is None:
                self._status = self._unavailable()
                return self.status()
            new_reference = None
            committed = False
            try:
                with self._metadata_lock():
                    # Never commit an instance's cached copy over another server's
                    # changes. Refresh under the lock before generating a new ref.
                    self.metadata = self._read_metadata()
                    self.loaded = True
                    previous = self.metadata[section].get(identity)
                    key = value.get("key", "")
                    if not isinstance(key, str):
                        raise ValueError("Invalid credential")
                    public_fields = ("id", "name", "provider", "url", "model", "json_mode") if section == "profiles" else ("url", "model", "json_mode")
                    item = {name: value[name] for name in public_fields}
                    if key:
                        prefix = "profile" if section == "profiles" else "connection"
                        new_reference = f"{prefix}:{identity}:{uuid.uuid4().hex}"
                    item["secret_ref"] = new_reference
                    self._validate_item(identity, item, profile=section == "profiles")
                    if key and key in json.dumps(item, ensure_ascii=False):
                        raise ValueError("Credential was pasted into public metadata")
                    updated = deepcopy(self.metadata)
                    updated[section][identity] = item
                    if new_reference is not None:
                        self.backend.set_password(SERVICE, new_reference, key)
                    # Commit metadata only after the new, independent credential exists.
                    # A failed file write cannot bind an old endpoint to a new Key.
                    self._write_metadata(updated)
                    committed = True
                    self.metadata = updated
                    self._status = self._persistent_status()
                    if previous and previous["secret_ref"] is not None:
                        try:
                            self.backend.delete_password(SERVICE, previous["secret_ref"])
                        except Exception:
                            self._status = self._persistent_status("当前配置已安全保存；旧系统凭证未能清理，不影响当前配置。")
            except Exception:
                if new_reference is not None and not committed:
                    try:
                        self.backend.delete_password(SERVICE, new_reference)
                    except Exception:
                        pass
                self._status = self._unavailable()
                return self.status()
            return self.status()

    def save_connection(self, provider, value):
        return self._save("connections", provider, value)

    def save_profile(self, value):
        return self._save("profiles", value["id"], value)
