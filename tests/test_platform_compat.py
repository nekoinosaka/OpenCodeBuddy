from __future__ import annotations

import os
from pathlib import Path

from opencode_buddy import platform_compat


def test_user_name_prefers_standard_environment_variables(monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    assert platform_compat.user_name() == "OpenCode"

    monkeypatch.setenv("USERNAME", "windows-user")
    assert platform_compat.user_name() == "windows-user"

    monkeypatch.setenv("USER", "posix-user")
    assert platform_compat.user_name() == "posix-user"


def test_lock_file_reports_contention_and_recovers(tmp_path: Path):
    lock_path = tmp_path / "agent.lock"
    first = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    second = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        assert platform_compat.lock_file(first, blocking=False) is True
        assert platform_compat.lock_file(second, blocking=False) is False
        platform_compat.unlock_file(first)
        assert platform_compat.lock_file(second, blocking=False) is True
        platform_compat.unlock_file(second)
    finally:
        os.close(first)
        os.close(second)


def test_fchmod_is_tolerant_when_unsupported(monkeypatch, tmp_path: Path):
    descriptor = os.open(tmp_path / "file", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        monkeypatch.delattr(os, "fchmod", raising=False)
        platform_compat.fchmod(descriptor, 0o600)  # must not raise
    finally:
        os.close(descriptor)
