"""Quorum-managed Windows tools must follow the active product state root."""

from __future__ import annotations

import os


def test_managed_tool_path_uses_active_quorum_home(monkeypatch, tmp_path):
    from hermes_cli import stdio

    quorum_home = tmp_path / "profile" / ".quorum"
    expected = [
        os.path.join(str(quorum_home), "git", "cmd"),
        os.path.join(str(quorum_home), "git", "bin"),
        os.path.join(str(quorum_home), "git", "usr", "bin"),
        os.path.join(str(quorum_home), "hermes-agent", "venv", "Scripts"),
    ]
    monkeypatch.setenv("HERMES_HOME", str(quorum_home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PATH", os.path.join(str(tmp_path), "system-bin"))
    monkeypatch.setattr(stdio, "is_windows", lambda: True)
    monkeypatch.setattr(stdio.os.path, "isdir", lambda candidate: candidate in expected)

    stdio._augment_path_with_known_tools()

    entries = os.environ["PATH"].split(os.pathsep)
    assert entries[: len(expected)] == expected
    assert not any(f"{os.sep}hermes{os.sep}git" in entry.casefold() for entry in entries)
