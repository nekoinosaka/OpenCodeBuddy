from pathlib import Path

from opencode_buddy import runtime


def test_runtime_root_uses_opencode_buddy_home():
    assert runtime.runtime_root() == Path.home() / ".opencode-buddy"


def test_runtime_paths_are_derived_from_runtime_root():
    root = Path.home() / ".opencode-buddy"

    assert runtime.state_path() == root / "state.json"
    assert runtime.logs_dir() == root / "logs"
    assert runtime.helper_app_path() == root / "helper" / "OpenCodeBuddyBLEHelper.app"
    assert runtime.socket_path() == root / "agent.sock"
    assert runtime.ota_snapshots_dir() == root / "ota" / "snapshots"
    assert runtime.ota_sessions_dir() == root / "ota" / "private" / "sessions"
