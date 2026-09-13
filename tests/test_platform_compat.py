from __future__ import annotations

import os
from pathlib import Path

import pytest

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


def test_posix_mode_bits_are_only_meaningful_on_posix():
    assert platform_compat.posix_mode_bits_are_meaningful() == (os.name == "posix")


def test_firmware_permission_gate_is_skipped_when_mode_bits_are_not_meaningful(
    monkeypatch, tmp_path: Path
):
    from opencode_buddy import ota_release

    image = tmp_path / "firmware.bin"
    image.write_bytes(b"\x00" * 64)

    monkeypatch.setattr(ota_release, "posix_mode_bits_are_meaningful", lambda: False)
    with pytest.raises(ValueError) as excinfo:
        ota_release.inspect_esp32s3_application_image(image)
    assert "permissions" not in str(excinfo.value)
