"""Behavior tests for Quorum's Hermes plugin and dashboard API."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _api_module():
    return _load(
        "test_quorum_dashboard_api",
        REPO_ROOT / "plugins" / "quorum" / "dashboard" / "plugin_api.py",
    )


def _client(module) -> TestClient:
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/quorum")
    return TestClient(app)


def test_dashboard_settings_are_profile_scoped_and_have_no_disable_switch(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("display:\n  language: en\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = _api_module()
    client = _client(module)

    response = client.put(
        "/api/plugins/quorum/settings",
        json={"default_policy": "balanced", "cloud_consent": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "default_policy": "balanced",
        "cloud_consent": True,
        "session_override_count": 0,
    }
    written = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert written["quorum"] == {"default_policy": "balanced", "cloud_consent": True}
    assert "enabled" not in written["quorum"]


def test_dashboard_rejects_unknown_policy(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = _api_module()

    response = _client(module).put(
        "/api/plugins/quorum/settings",
        json={"default_policy": "unrestricted", "cloud_consent": True},
    )

    assert response.status_code == 422


def test_dashboard_rejects_attempted_enforcement_toggle(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = _api_module()

    response = _client(module).put(
        "/api/plugins/quorum/settings",
        json={"default_policy": "private", "cloud_consent": False, "enabled": False},
    )

    assert response.status_code == 422


def test_inspection_projects_metadata_and_never_exposes_prompt_content(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = _api_module()
    dispatch = SimpleNamespace(
        get_status=lambda: {"available": True, "default_policy": "private", "event_count": 1},
        list_events=lambda **_: [
            {
                "id": 7,
                "timestamp": "2026-08-03T01:02:03Z",
                "allowed": False,
                "policy": "private",
                "reach": "cloud",
                "provider": "local",
                "model": "safe-model",
                "sensitive_categories": ("credential",),
                "prompt": "must never leave the backend API",
                "messages": [{"role": "user", "content": "secret"}],
            }
        ],
    )
    monkeypatch.setattr(module, "_load_dispatch_module", lambda: dispatch)

    response = _client(module).get("/api/plugins/quorum/overview?limit=10")

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["available"] is True
    assert body["inspection"]["durable"] is False
    assert body["inspection"]["events"] == [
        {
            "id": 7,
            "timestamp": "2026-08-03T01:02:03Z",
            "policy": "private",
            "reach": "cloud",
            "provider": "local",
            "model": "safe-model",
            "allowed": False,
            "decision": "blocked",
            "sensitive_categories": ["credential"],
        }
    ]


def test_missing_dispatch_guard_is_reported_without_claiming_enforcement(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = _api_module()

    def unavailable():
        raise ImportError("stock Hermes")

    monkeypatch.setattr(module, "_load_dispatch_module", unavailable)
    body = _client(module).get("/api/plugins/quorum/overview").json()

    assert body["status"] == {"available": False, "reason": "ImportError"}
    assert body["inspection"]["available"] is False
    assert body["inspection"]["durable"] is False


def test_policy_config_failure_never_renders_as_available(tmp_path, monkeypatch):
    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    module = _api_module()
    dispatch = SimpleNamespace(
        get_status=lambda: {
            "mandatory": True,
            "fail_closed": True,
            "settings_error": "config could not be read",
        },
        list_events=lambda **_: [],
    )
    monkeypatch.setattr(module, "_load_dispatch_module", lambda: dispatch)

    status = _client(module).get("/api/plugins/quorum/overview").json()["status"]

    assert status["available"] is False
    assert status["health"] == "degraded"
    assert status["reason"] == "config_unavailable"
    assert status["fail_closed"] is True


def test_general_plugin_registers_the_real_slash_command_contract():
    module = _load("test_quorum_general_plugin", REPO_ROOT / "plugins" / "quorum" / "__init__.py")
    registrations = []

    class Context:
        def register_command(self, *args, **kwargs):
            registrations.append((args, kwargs))

    module.register(Context())

    assert len(registrations) == 1
    args, kwargs = registrations[0]
    assert args[0] == "quorum"
    assert callable(args[1])
    assert kwargs["args_hint"] == "[status]"


def test_general_plugin_is_honest_on_stock_hermes(monkeypatch):
    module = _load("test_quorum_general_plugin_stock", REPO_ROOT / "plugins" / "quorum" / "__init__.py")
    monkeypatch.setattr(module, "_runtime_status", lambda: {"available": False, "reason": "ImportError"})

    result = module._handle_quorum("status")

    assert "stock Hermes" in result
    assert "best-effort" in result
    assert "fail-closed enforcement" in result


def test_bundled_quorum_plugin_loads_from_default_config(tmp_path, monkeypatch):
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_cli.plugins import PluginManager

    hermes_home = tmp_path / "profile-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    assert "quorum" in DEFAULT_CONFIG["plugins"]["enabled"]
    manager = PluginManager()
    manager.discover_and_load()

    listing = {item["key"]: item for item in manager.list_plugins()}
    assert listing["quorum"]["enabled"] is True
    assert "quorum" in manager._plugin_commands
