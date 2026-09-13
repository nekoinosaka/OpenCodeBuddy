from __future__ import annotations

import contextlib
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

TASK_NAME = "OpenCodeBuddyAgent"


def windows_task_name() -> str:
    return TASK_NAME


def _run_schtasks(args: list[str], *, schtasks_bin: str, check: bool = False):
    # Windows console tools emit OEM/ANSI bytes (often GBK on zh-CN). Decode
    # leniently so a reader thread can never raise UnicodeDecodeError, which
    # would otherwise leave stdout/stderr as None.
    return subprocess.run(
        [schtasks_bin, *args],
        check=check,
        capture_output=True,
        text=True,
        errors="replace",
    )


def render_windows_task_xml(
    *,
    python_executable: str,
    state_path: Path,
    repo_root: Path,
    log_dir: Path,
) -> str:
    """Build a Task Scheduler definition for the background bridge agent.

    Mirrors the launchd job: start at logon, restart on failure, and never time
    out. ``log_dir`` is accepted for parity with the launchd renderer; the agent
    writes its own logs under the runtime root.
    """

    arguments = (
        f'-m opencode_buddy --state-path "{state_path}" agent'
    )
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>OpenCode Buddy background bridge</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>999</Count>\n"
        "    </RestartOnFailure>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{escape(python_executable)}</Command>\n"
        f"      <Arguments>{escape(arguments)}</Arguments>\n"
        f"      <WorkingDirectory>{escape(str(repo_root))}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def install_windows_service(
    *,
    python_executable: str,
    state_path: Path,
    repo_root: Path,
    log_dir: Path,
    schtasks_bin: str = "schtasks",
) -> None:
    xml = render_windows_task_xml(
        python_executable=python_executable,
        state_path=state_path,
        repo_root=repo_root,
        log_dir=log_dir,
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".xml", encoding="utf-16", delete=False
    ) as handle:
        handle.write(xml)
        xml_path = Path(handle.name)
    try:
        _run_schtasks(
            ["/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
            schtasks_bin=schtasks_bin,
            check=True,
        )
    finally:
        with contextlib.suppress(OSError):
            xml_path.unlink()
    _run_schtasks(["/Run", "/TN", TASK_NAME], schtasks_bin=schtasks_bin)


def uninstall_windows_service(schtasks_bin: str = "schtasks") -> None:
    _run_schtasks(["/End", "/TN", TASK_NAME], schtasks_bin=schtasks_bin)
    _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"], schtasks_bin=schtasks_bin)


def windows_service_status(schtasks_bin: str = "schtasks") -> dict:
    try:
        completed = _run_schtasks(
            ["/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
            schtasks_bin=schtasks_bin,
        )
    except OSError as exc:
        # `doctor` must stay usable even when schtasks is unavailable or
        # blocked by policy; report the service as unloaded rather than crash.
        return {
            "label": TASK_NAME,
            "loaded": False,
            "running": False,
            "pid": None,
            "last_exit_status": None,
            "returncode": None,
            "raw": f"schtasks unavailable: {exc}",
        }
    raw_output = (completed.stdout or completed.stderr or "").strip()
    loaded = completed.returncode == 0
    status_text = _value_for(raw_output, "Status") if loaded else None
    last_result = _int_for(raw_output, "Last Result") if loaded else None
    running = bool(status_text) and status_text.strip().lower() == "running"
    return {
        "label": TASK_NAME,
        "loaded": loaded,
        "running": running,
        "pid": None,
        "last_exit_status": last_result,
        "returncode": completed.returncode,
        "raw": raw_output,
    }


def _value_for(output: str, key: str) -> Optional[str]:
    prefix = f"{key}:"
    for line in output.splitlines():
        if line.strip().lower().startswith(prefix.lower()):
            return line.split(":", 1)[1].strip()
    return None


def _int_for(output: str, key: str) -> Optional[int]:
    raw = _value_for(output, key)
    if raw is None:
        return None
    with contextlib.suppress(ValueError):
        return int(raw)
    return None
