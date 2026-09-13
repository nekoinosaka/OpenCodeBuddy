import plistlib
from types import SimpleNamespace
from pathlib import Path

from opencode_buddy import launchd


def test_launchd_label_is_stable():
    assert launchd.launchd_label() == "com.opencodebuddy.agent"


def test_launchd_plist_path_uses_launch_agents_home():
    expected = Path.home() / "Library" / "LaunchAgents" / "com.opencodebuddy.agent.plist"

    assert launchd.launchd_plist_path() == expected


def test_render_launchd_plist_contains_expected_values(tmp_path):
    state_path = tmp_path / "state.json"
    repo_root = Path("/Users/tester/Documents/OpenCodeBuddy")
    log_dir = tmp_path / "logs"

    plist_text = launchd.render_launchd_plist(
        python_executable="/Users/tester/Documents/OpenCodeBuddy/.venv/bin/python",
        state_path=state_path,
        repo_root=repo_root,
        log_dir=log_dir,
    )

    payload = plistlib.loads(plist_text.encode("utf-8"))

    assert payload["Label"] == "com.opencodebuddy.agent"
    assert payload["ProgramArguments"] == [
        "/Users/tester/Documents/OpenCodeBuddy/.venv/bin/python",
        "-m",
        "opencode_buddy",
        "--state-path",
        str(state_path),
        "agent",
    ]
    assert payload["WorkingDirectory"] == str(repo_root)
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["StandardOutPath"] == str(log_dir / "com.opencodebuddy.agent.stdout.log")
    assert payload["StandardErrorPath"] == str(log_dir / "com.opencodebuddy.agent.stderr.log")


def test_install_launchd_service_writes_plist_and_bootstraps(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)
    monkeypatch.setattr(launchd.os, "getuid", lambda: 501)

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.opencodebuddy.agent.plist"

    launchd.install_launchd_service(plist_path, "<plist />", launchctl_bin="/bin/launchctl")

    assert plist_path.read_text(encoding="utf-8") == "<plist />"
    assert calls == [
        (
            ["/bin/launchctl", "bootout", "gui/501", str(plist_path)],
            {"check": False, "capture_output": True, "text": True},
        ),
        (
            ["/bin/launchctl", "bootstrap", "gui/501", str(plist_path)],
            {"check": True, "capture_output": True, "text": True},
        ),
    ]


def test_uninstall_launchd_service_boots_out_and_removes_plist(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)
    monkeypatch.setattr(launchd.os, "getuid", lambda: 501)

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.opencodebuddy.agent.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text("<plist />", encoding="utf-8")

    launchd.uninstall_launchd_service(plist_path, launchctl_bin="/bin/launchctl")

    assert not plist_path.exists()
    assert calls == [
        (
            ["/bin/launchctl", "bootout", "gui/501", str(plist_path)],
            {"check": False, "capture_output": True, "text": True},
        ),
    ]


def test_launchd_service_status_parses_launchctl_list_output(monkeypatch):
    output = """
{
    "Label" = "com.opencodebuddy.agent";
    "LastExitStatus" = 15;
    "PID" = 43439;
};
""".strip()

    def fake_run(command, **kwargs):
        assert command == ["/bin/launchctl", "list", "com.opencodebuddy.agent"]
        assert kwargs == {"capture_output": True, "text": True}
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)

    assert launchd.launchd_service_status("com.opencodebuddy.agent", launchctl_bin="/bin/launchctl") == {
        "label": "com.opencodebuddy.agent",
        "loaded": True,
        "running": True,
        "pid": 43439,
        "last_exit_status": 15,
        "returncode": 0,
        "raw": output,
    }


def test_launchd_service_status_reports_missing_service(monkeypatch):
    output = 'Could not find service "com.opencodebuddy.agent" in domain for port'

    def fake_run(command, **kwargs):
        assert command == ["/bin/launchctl", "list", "com.opencodebuddy.agent"]
        assert kwargs == {"capture_output": True, "text": True}
        return SimpleNamespace(returncode=113, stdout="", stderr=output)

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)

    assert launchd.launchd_service_status("com.opencodebuddy.agent", launchctl_bin="/bin/launchctl") == {
        "label": "com.opencodebuddy.agent",
        "loaded": False,
        "running": False,
        "pid": None,
        "last_exit_status": None,
        "returncode": 113,
        "raw": output,
    }
