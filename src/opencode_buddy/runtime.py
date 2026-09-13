from __future__ import annotations

from pathlib import Path


def runtime_root() -> Path:
    return Path.home() / ".opencode-buddy"


def state_path() -> Path:
    return runtime_root() / "state.json"


def logs_dir() -> Path:
    return runtime_root() / "logs"


def helper_dir() -> Path:
    return runtime_root() / "helper"


def helper_app_path() -> Path:
    return helper_dir() / "OpenCodeBuddyBLEHelper.app"


def socket_path() -> Path:
    return runtime_root() / "agent.sock"


def ota_dir() -> Path:
    return runtime_root() / "ota"


def ota_private_dir() -> Path:
    return ota_dir() / "private"


def ota_public_dir() -> Path:
    return ota_dir() / "public"


def ota_releases_dir() -> Path:
    return ota_dir() / "releases"


def ota_sessions_dir() -> Path:
    return ota_private_dir() / "sessions"


def ota_snapshots_dir() -> Path:
    return ota_dir() / "snapshots"


def firmware_dir() -> Path:
    return runtime_root() / "firmware"


def default_firmware_path() -> Path:
    return firmware_dir() / "opencode-buddy-sticks3-app.bin"
