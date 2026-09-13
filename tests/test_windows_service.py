from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from opencode_buddy import windows_service

_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _xml_for(tmp_path: Path) -> str:
    return windows_service.render_windows_task_xml(
        python_executable=r"C:\Python\python.exe",
        state_path=tmp_path / "state.json",
        repo_root=tmp_path / "repo",
        log_dir=tmp_path / "logs",
    )


def test_task_xml_is_well_formed_and_targets_the_agent(tmp_path: Path):
    xml = _xml_for(tmp_path)
    root = ET.fromstring(xml.encode("utf-16"))
    assert root.tag.endswith("Task")

    command = root.find(".//t:Actions/t:Exec/t:Command", _NS)
    arguments = root.find(".//t:Actions/t:Exec/t:Arguments", _NS)
    working_dir = root.find(".//t:Actions/t:Exec/t:WorkingDirectory", _NS)
    assert command is not None and command.text == r"C:\Python\python.exe"
    assert arguments is not None
    assert "-m opencode_buddy" in arguments.text
    assert str(tmp_path / "state.json") in arguments.text
    assert arguments.text.strip().endswith("agent")
    assert working_dir is not None and working_dir.text == str(tmp_path / "repo")
    # Restart-on-failure mirrors launchd KeepAlive.
    assert root.find(".//t:Settings/t:RestartOnFailure", _NS) is not None


def test_install_writes_task_and_runs_it(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []
    tasks: dict[str, bytes] = {}

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        if "/XML" in command:
            xml_path = Path(command[command.index("/XML") + 1])
            tasks["xml"] = xml_path.read_bytes()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(windows_service.subprocess, "run", fake_run)

    windows_service.install_windows_service(
        python_executable="python.exe",
        state_path=tmp_path / "state.json",
        repo_root=tmp_path / "repo",
        log_dir=tmp_path / "logs",
        schtasks_bin="schtasks",
    )

    assert any(cmd[:2] == ["schtasks", "/Create"] for cmd in calls)
    assert any(cmd[:2] == ["schtasks", "/Run"] for cmd in calls)
    assert "opencode_buddy" in tasks["xml"].decode("utf-16")


def test_status_parses_running_task(monkeypatch):
    output = "TaskName:  \\OpenCodeBuddyAgent\nStatus:    Running\nLast Result:  0\n"

    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(windows_service.subprocess, "run", fake_run)

    status = windows_service.windows_service_status(schtasks_bin="schtasks")
    assert status["loaded"] is True
    assert status["running"] is True
    assert status["last_exit_status"] == 0


def test_status_reports_missing_task(monkeypatch):
    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="ERROR: The system cannot find the file specified."
        )

    monkeypatch.setattr(windows_service.subprocess, "run", fake_run)

    status = windows_service.windows_service_status(schtasks_bin="schtasks")
    assert status["loaded"] is False
    assert status["running"] is False


def test_status_degrades_when_schtasks_is_unavailable(monkeypatch):
    def fake_run(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(windows_service.subprocess, "run", fake_run)

    status = windows_service.windows_service_status(schtasks_bin="schtasks")
    assert status["loaded"] is False
    assert status["running"] is False
    assert "schtasks unavailable" in status["raw"]


def test_install_surfaces_a_readable_error_when_schtasks_is_unavailable(
    monkeypatch, tmp_path: Path
):
    def fake_run(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(windows_service.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        windows_service.install_windows_service(
            python_executable="python.exe",
            state_path=tmp_path / "state.json",
            repo_root=tmp_path / "repo",
            log_dir=tmp_path / "logs",
            schtasks_bin="schtasks",
        )
    assert "could not register the background task" in str(excinfo.value)
