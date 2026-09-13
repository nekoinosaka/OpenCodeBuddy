from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, runtime, setup_flow
from .agent import (
    AgentClient,
    AgentClientError,
    BuddyAgent,
    default_log_dir,
    default_socket_path,
    spawn_agent_process,
    wait_for_agent,
)
from .ble_transport import BleBuddyTransport, NativeBleHelperError, _native_helper_app_path
from .launchd import (
    install_launchd_service,
    launchd_label,
    launchd_plist_path,
    launchd_service_status,
    render_launchd_plist,
    uninstall_launchd_service,
)
from .opencode_server import default_server_url
from .state_store import BridgeStateStore, PersistedState
from . import windows_service


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _install_agent_service(state_path: Path) -> None:
    """Install the per-user background agent for the current platform."""

    if _is_windows():
        repo_root = Path(__file__).resolve().parents[2]
        log_dir = default_log_dir(state_path)
        log_dir.mkdir(parents=True, exist_ok=True)
        windows_service.install_windows_service(
            python_executable=sys.executable,
            state_path=state_path,
            repo_root=repo_root,
            log_dir=log_dir,
        )
        return
    _install_launchd_service(state_path)


def _uninstall_agent_service() -> None:
    if _is_windows():
        windows_service.uninstall_windows_service()
        return
    uninstall_launchd_service(launchd_plist_path())


def _agent_service_status() -> dict:
    if _is_windows():
        return windows_service.windows_service_status()
    return launchd_service_status(launchd_label())


def _agent_service_location() -> str:
    if _is_windows():
        return f"Task Scheduler task: {windows_service.windows_task_name()}"
    return str(launchd_plist_path())


def default_state_path() -> Path:
    return runtime.state_path()


class _OpenCodeBuddyArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        text = super().format_help()
        lines = [line for line in text.splitlines() if "==SUPPRESS==" not in line]
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def build_parser() -> argparse.ArgumentParser:
    parser = _OpenCodeBuddyArgumentParser(
        prog="opencode-buddy",
        description="Install, pair, and maintain OpenCode Buddy for OpenCode approvals.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--state-path", type=Path, default=default_state_path())
    subparsers = parser.add_subparsers(dest="command", metavar="{doctor,repair,firmware,uninstall}")
    parser.set_defaults(command="default", device=None, timeout=4.0)

    doctor = subparsers.add_parser("doctor", help="Diagnose the current OpenCode Buddy setup")
    doctor.add_argument("--json", action="store_true", help="Print raw machine-readable diagnostics")
    subparsers.add_parser("repair", help="Repair or finish the local OpenCode Buddy setup")
    firmware = subparsers.add_parser("firmware", help="Install signed StickS3 firmware updates")
    firmware_commands = firmware.add_subparsers(dest="firmware_command", required=True)
    firmware_update = firmware_commands.add_parser(
        "update", help="Push an application firmware image over local Wi-Fi"
    )
    firmware_update.add_argument(
        "--firmware",
        type=Path,
        help="application firmware.bin (development override)",
    )
    uninstall = subparsers.add_parser("uninstall", help="Remove OpenCode Buddy from this Mac")
    uninstall.add_argument("--yes", action="store_true", help="Skip the interactive confirmation")

    pair = subparsers.add_parser("pair", help=argparse.SUPPRESS)
    pair.add_argument("--device", help="Exact device name to bind to")
    pair.add_argument("--timeout", type=float, default=4.0)

    subparsers.add_parser("agent", help=argparse.SUPPRESS)
    subparsers.add_parser("install-opencode-plugin", help=argparse.SUPPRESS)
    subparsers.add_parser("status", help=argparse.SUPPRESS)
    subparsers.add_parser("sessions", help=argparse.SUPPRESS)
    subparsers.add_parser("service-install", help=argparse.SUPPRESS)
    subparsers.add_parser("service-uninstall", help=argparse.SUPPRESS)
    subparsers.add_parser("service-status", help=argparse.SUPPRESS)
    return parser


async def _pair(args: argparse.Namespace) -> int:
    if await _ota_conflict_active(args.state_path):
        print("A firmware update is active. Wait for it to finish before pairing.", file=sys.stderr)
        return 1
    store = BridgeStateStore(args.state_path)
    matches = await BleBuddyTransport.discover(timeout=args.timeout)
    if args.device:
        matches = [match for match in matches if match.name == args.device]
    if not matches:
        print("No OpenCode Buddy device found. Power on the StickS3 and try again.", file=sys.stderr)
        return 1
    selected = _select_device(matches)
    await _pair_selected_device(store, selected)
    print(f"Paired {selected.name} ({selected.device_id}) and synced time")
    return 0


def _status(args: argparse.Namespace) -> int:
    live = _agent_status(args.state_path)
    if live is not None:
        print(json.dumps(live["state"], indent=2, sort_keys=True))
        return 0
    state = BridgeStateStore(args.state_path).load()
    print(json.dumps(state.__dict__, indent=2, sort_keys=True))
    return 0


def _sessions(args: argparse.Namespace) -> int:
    live = _agent_sessions(args.state_path)
    if live is not None:
        print(json.dumps(live["sessions"], indent=2, sort_keys=True))
        return 0
    state = BridgeStateStore(args.state_path).load()
    print(json.dumps(state.sessions, indent=2, sort_keys=True))
    return 0


def _doctor(args: argparse.Namespace) -> int:
    payload = _doctor_payload(args)
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(_render_doctor(payload))
    return 0


def _repair(args: argparse.Namespace) -> int:
    return asyncio.run(_setup(args, repair=True))


def _uninstall(args: argparse.Namespace) -> int:
    if not getattr(args, "yes", False):
        answer = input("Remove OpenCode Buddy from this Mac? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled.")
            return 0

    _uninstall_agent_service()
    setup_flow.opencode_plugin_path().unlink(missing_ok=True)
    runtime_root = runtime.runtime_root()
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    print(f"Removed OpenCode Buddy from {runtime_root}")
    return 0


async def _setup(args: argparse.Namespace, *, repair: bool = False) -> int:
    if await _ota_conflict_active(args.state_path):
        print("A firmware update is active. Wait for it to finish before repairing.", file=sys.stderr)
        return 1
    state_path = Path(args.state_path)
    store = BridgeStateStore(state_path)
    current = store.load()

    helper_app_path: Path | None = None
    if not _is_windows():
        try:
            helper_app_path = setup_flow.ensure_helper_app_installed()
        except (NativeBleHelperError, subprocess.CalledProcessError, OSError) as exc:
            print(f"Native BLE helper is unavailable: {exc}", file=sys.stderr)
            print("Run `opencode-buddy repair` after the helper bundle is available.", file=sys.stderr)
            return 1

    try:
        setup_flow.ensure_firmware_artifact_installed()
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Bundled firmware is unavailable: {exc}", file=sys.stderr)
        print(
            "Reinstall OpenCode Buddy with its firmware package, then run `opencode-buddy repair`.",
            file=sys.stderr,
        )
        return 1

    selected = await _resolve_selected_device(args, current)
    if selected is None:
        return 1

    await _pair_selected_device(store, selected)
    plugin_path = setup_flow.install_opencode_plugin()
    _install_agent_service(state_path)

    current = store.load()
    next_state = replace(
        current,
        setup_version=setup_flow.SETUP_VERSION,
        helper_app_path=str(helper_app_path) if helper_app_path else current.helper_app_path,
        service_installed=_agent_service_status()["loaded"],
    )
    store.save(next_state)

    if not setup_flow.is_setup_complete(store.load()):
        print("OpenCode Buddy setup is still incomplete. Run `opencode-buddy doctor` for details.", file=sys.stderr)
        return 1

    action = "Repaired" if repair else "Installed"
    print(f"{action} OpenCode Buddy.")
    print(f"Device: {selected.name} ({selected.device_id})")
    print(f"OpenCode plugin: {plugin_path}")
    print("Next: restart opencode, then use it normally.")
    return 0


def _default_firmware_image() -> Path:
    installed = runtime.default_firmware_path()
    if installed.is_file() and not installed.is_symlink():
        return installed
    raise FileNotFoundError(
        "installed firmware application image is missing; run `opencode-buddy repair`"
    )


async def _request_ota_cancel_bounded(
    state_path: Path, nonce: str
) -> dict[str, object] | None:
    request = asyncio.create_task(
        asyncio.wait_for(
            _agent_request(
                state_path,
                {"cmd": "ota_cancel", "nonce": nonce},
            ),
            timeout=2.0,
        )
    )
    try:
        response = await asyncio.shield(request)
    except (Exception, asyncio.CancelledError):
        return None
    return response if isinstance(response, dict) else None


def _print_ota_interrupt_result(response: dict[str, object] | None) -> None:
    if response is not None and response.get("cancel_applied") is True:
        print("Firmware update cancelled on OpenCode Buddy.", file=sys.stderr)
        return
    ota = response.get("ota") if response is not None else None
    phase = ota.get("phase") if isinstance(ota, dict) else None
    if phase in {"boot-committed", "restarting", "boot-health"}:
        print(
            "Firmware is already committed; the update continues on OpenCode Buddy.",
            file=sys.stderr,
        )
    else:
        print(
            "Cancellation was not confirmed; the update may still be continuing on OpenCode Buddy.",
            file=sys.stderr,
        )


async def _firmware_update(args: argparse.Namespace) -> int:
    if _is_windows():
        print(
            "Firmware updates over Wi-Fi are not supported on Windows yet. "
            "Flash from macOS, or use the USB recovery image.",
            file=sys.stderr,
        )
        return 1
    try:
        image = Path(args.firmware).expanduser() if args.firmware else _default_firmware_image()
        await _ensure_agent_running(args.state_path)
        response = await _agent_request(
            args.state_path,
            {"cmd": "ota_begin", "firmware": str(image)},
        )
        ota = response["ota"]
        nonce = str(ota["nonce"])
        last_phase = ""
        last_bucket = -1
        while True:
            phase = str(ota.get("phase", ""))
            percent = int(ota.get("percent", 0))
            bucket = percent // 10
            if phase != last_phase or bucket != last_bucket:
                if phase == "await-confirm":
                    print("Update ready. On OpenCode Buddy, Press A to install or B to cancel.")
                elif phase in {"download", "readback"}:
                    label = "Downloading" if phase == "download" else "Verifying flash"
                    print(f"{label}: {percent}%")
                elif phase == "restarting":
                    print("Firmware committed. Waiting for OpenCode Buddy to restart...")
                elif phase == "boot-health":
                    print("OpenCode Buddy restarted. Verifying boot health...")
                elif phase == "preparing":
                    print("Preparing signed local firmware update...")
                last_phase, last_bucket = phase, bucket
            if bool(ota.get("terminal", False)):
                if bool(ota.get("success", False)):
                    print(f"Firmware {ota.get('version', '')} is running and healthy.")
                    return 0
                error = str(ota.get("error", "update-failed"))
                print(f"Firmware update failed: {error}.", file=sys.stderr)
                return 1
            await asyncio.sleep(0.25)
            response = await _agent_request(
                args.state_path,
                {"cmd": "ota_status", "nonce": nonce},
            )
            ota = response["ota"]
    except asyncio.CancelledError:
        response = None
        if "nonce" in locals():
            response = await _request_ota_cancel_bounded(args.state_path, nonce)
        _print_ota_interrupt_result(response)
        return 130
    except KeyboardInterrupt:
        response = None
        if "nonce" in locals():
            response = await _request_ota_cancel_bounded(args.state_path, nonce)
        _print_ota_interrupt_result(response)
        return 130
    except (AgentClientError, KeyError, TypeError, ValueError, OSError) as exc:
        message = str(exc)
        if "trust" in message.lower():
            message += " Run `python3 scripts/generate-ota-trust.py` only for the USB trust bootstrap."
        elif "firmware image" in message.lower() or "no such file" in message.lower():
            message += " Build the app image with `scripts/build-firmware-release.sh`."
        print(f"Cannot start firmware update: {message}", file=sys.stderr)
        return 1


def _default_status(args: argparse.Namespace) -> int:
    payload = _doctor_payload(args)
    device_name = payload["paired_device_name"] or "Unknown"
    device_id = payload["paired_device_id"] or "-"
    agent_text = "running" if payload["agent_running"] else "installed"
    print("OpenCode Buddy is ready.")
    print(f"Device: {device_name} ({device_id})")
    print(f"Agent: {agent_text}")
    print(f"OpenCode plugin: {payload['opencode_plugin_path']}")
    print("Next: restart opencode and use it normally. Use `opencode-buddy doctor` if anything looks wrong.")
    return 0


async def _agent_command(args: argparse.Namespace) -> int:
    agent = BuddyAgent(Path(args.state_path))
    try:
        await agent.run()
    except KeyboardInterrupt:
        pass
    return 0


def _install_opencode_plugin(_: argparse.Namespace) -> int:
    destination = setup_flow.install_opencode_plugin()
    print(f"Installed OpenCode bridge plugin at {destination}")
    print("Restart opencode for the plugin to take effect.")
    return 0


def _service_install(args: argparse.Namespace) -> int:
    _install_agent_service(Path(args.state_path))
    store = BridgeStateStore(args.state_path)
    current = store.load()
    store.save(replace(current, service_installed=True))
    print(f"Installed background service at {_agent_service_location()}")
    return 0


def _service_uninstall(args: argparse.Namespace) -> int:
    _uninstall_agent_service()
    store = BridgeStateStore(args.state_path)
    current = store.load()
    store.save(replace(current, service_installed=False))
    print(f"Removed background service at {_agent_service_location()}")
    return 0


def _service_status(_: argparse.Namespace) -> int:
    print(json.dumps(_agent_service_status(), indent=2, sort_keys=True))
    return 0


def _is_setup_complete(args: argparse.Namespace) -> bool:
    return setup_flow.is_setup_complete(BridgeStateStore(args.state_path).load())


def _doctor_payload(args: argparse.Namespace) -> dict[str, object]:
    state_path = Path(args.state_path)
    state = BridgeStateStore(state_path).load()
    socket_path = default_socket_path(state_path)
    live = _agent_status(state_path)
    service_status = _agent_service_status()
    helper_app = state.helper_app_path
    helper_error = None
    if _is_windows():
        # Windows uses the portable bleak backend; there is no native helper.
        helper_app = None
    elif helper_app:
        helper_path = Path(helper_app)
        if not (helper_path / "Contents" / "MacOS" / "OpenCodeBuddyBLEHelper").exists():
            helper_error = f"Helper bundle is missing or incomplete at {helper_path}"
    else:
        try:
            helper_app = str(_native_helper_app_path())
        except (NativeBleHelperError, subprocess.CalledProcessError) as exc:
            helper_error = str(exc)

    plugin_path = setup_flow.opencode_plugin_path()

    return {
        "setup_complete": setup_flow.is_setup_complete(
            replace(
                state,
                helper_app_path=str(helper_app or state.helper_app_path),
                service_installed=state.service_installed and service_status["loaded"],
            )
        ),
        "paired_device_id": state.paired_device_id,
        "paired_device_name": state.paired_device_name,
        "agent_socket_path": str(socket_path),
        "agent_running": live is not None,
        "snapshot": live["state"]["snapshot"] if live is not None else state.snapshot,
        "service": service_status,
        "launchd": service_status,
        "native_helper_app": helper_app,
        "native_helper_error": helper_error,
        "opencode_plugin_path": str(plugin_path),
        "opencode_plugin_installed": plugin_path.is_file(),
        "opencode_plugin_current": setup_flow.opencode_plugin_is_current(plugin_path),
        "opencode_server_url": default_server_url(),
        "service_installed": state.service_installed,
    }


def _render_doctor(payload: dict[str, object]) -> str:
    problems = _doctor_problems(payload)
    lines = []
    if problems:
        lines.append("OpenCode Buddy needs attention.")
        for index, problem in enumerate(problems, start=1):
            lines.append(f"{index}. Problem: {problem['problem']}")
            lines.append(f"   Reason: {problem['reason']}")
            lines.append(f"   Next: {problem['next']}")
    else:
        lines.append("OpenCode Buddy is ready.")
        lines.append(
            "Next: restart opencode and use it normally. Use `opencode-buddy repair` if approvals stop showing up."
        )

    lines.append(f"Device: {payload['paired_device_name'] or '-'} ({payload['paired_device_id'] or '-'})")
    lines.append(f"Agent: {'running' if payload['agent_running'] else 'not running'}")
    service_label = "Task Scheduler" if _is_windows() else "Launchd"
    lines.append(f"{service_label}: {'loaded' if payload['launchd']['loaded'] else 'not loaded'}")
    lines.append(f"OpenCode plugin: {payload['opencode_plugin_path']}")
    lines.append(f"OpenCode server: {payload['opencode_server_url']}")
    if payload["native_helper_app"]:
        lines.append(f"Helper: {payload['native_helper_app']}")
    return "\n".join(lines)


def _doctor_problems(payload: dict[str, object]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    if not payload["paired_device_id"]:
        problems.append(
            {
                "problem": "No StickS3 is paired yet.",
                "reason": "Setup never finished a successful hardware buddy pairing.",
                "next": "Power on the device and run `opencode-buddy repair`.",
            }
        )
    if not payload["opencode_plugin_installed"]:
        problems.append(
            {
                "problem": "The OpenCode bridge plugin is not installed.",
                "reason": "OpenCode will not forward session events or approvals without it.",
                "next": "Run `opencode-buddy install-opencode-plugin`, then restart opencode.",
            }
        )
    elif not payload.get("opencode_plugin_current", True):
        problems.append(
            {
                "problem": "The OpenCode bridge plugin is out of date.",
                "reason": "The installed plugin differs from the one bundled with this build.",
                "next": "Run `opencode-buddy install-opencode-plugin`, then restart opencode.",
            }
        )
    if payload["native_helper_error"]:
        problems.append(
            {
                "problem": "The native Bluetooth helper is unavailable.",
                "reason": str(payload["native_helper_error"]),
                "next": "Restore the helper bundle, then run `opencode-buddy repair`.",
            }
        )
    if not payload["launchd"]["loaded"]:
        problems.append(
            {
                "problem": "The background agent is not installed or not loaded.",
                "reason": (
                    f"Task Scheduler is not currently serving "
                    f"`{windows_service.windows_task_name()}`."
                    if _is_windows()
                    else "Launchd is not currently serving `com.opencodebuddy.agent`."
                ),
                "next": "Run `opencode-buddy repair` to reinstall the service.",
            }
        )
    elif not payload["agent_running"]:
        last_exit_status = payload["launchd"].get("last_exit_status")
        status = "unknown" if last_exit_status is None else str(last_exit_status)
        problems.append(
            {
                "problem": "The background agent is repeatedly exiting.",
                "reason": (
                    "The background service is loaded, but the agent is not running "
                    f"(last exit status: {status})."
                    if _is_windows()
                    else (
                        "Launchd is loaded, but the agent is not running "
                        f"(last exit status: {status})."
                    )
                ),
                "next": "Run `opencode-buddy repair`; if it recurs, inspect the launchd error log.",
            }
        )
    return problems


def _select_device(matches) -> object:
    if len(matches) == 1:
        return matches[0]

    print("Multiple OpenCode Buddy devices found:")
    for index, match in enumerate(matches, start=1):
        print(f"{index}. {match.name} ({match.device_id})")

    while True:
        raw = input("Choose a device number: ").strip()
        if raw.isdigit():
            selected_index = int(raw)
            if 1 <= selected_index <= len(matches):
                return matches[selected_index - 1]
        print(f"Enter a number between 1 and {len(matches)}.", file=sys.stderr)


async def _pair_selected_device(store: BridgeStateStore, selected: object) -> None:
    transport = BleBuddyTransport(selected.device_id, device_name=selected.name)
    await transport.connect()
    await transport.send_time_sync()
    await asyncio.sleep(0.25)
    await transport.disconnect()
    current = store.load()
    store.save(
        replace(
            current,
            paired_device_id=selected.device_id,
            paired_device_name=selected.name,
            buddy_connected=False,
        )
    )


async def _resolve_selected_device(args: argparse.Namespace, current: PersistedState):
    if current.paired_device_id and current.paired_device_name:
        return argparse.Namespace(device_id=current.paired_device_id, name=current.paired_device_name)

    matches = await BleBuddyTransport.discover(timeout=getattr(args, "timeout", 4.0))
    if getattr(args, "device", None):
        matches = [match for match in matches if match.name == args.device]
    if not matches:
        print("No OpenCode Buddy device found. Power on the StickS3 and run `opencode-buddy repair`.", file=sys.stderr)
        return None
    return _select_device(matches)


def _install_launchd_service(state_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    log_dir = default_log_dir(state_path)
    log_dir.mkdir(parents=True, exist_ok=True)
    plist_text = render_launchd_plist(
        python_executable=sys.executable,
        state_path=state_path,
        repo_root=repo_root,
        log_dir=log_dir,
    )
    install_launchd_service(launchd_plist_path(), plist_text)


async def _ensure_agent_running(state_path) -> None:
    state_path = Path(state_path)
    socket_path = default_socket_path(state_path)
    client = AgentClient(socket_path)
    try:
        await client.request({"cmd": "ping"})
        return
    except AgentClientError:
        spawn_agent_process(state_path)
        await wait_for_agent(socket_path)


async def _agent_request(state_path, payload):
    state_path = Path(state_path)
    client = AgentClient(default_socket_path(state_path))
    return await client.request(payload)


def _agent_status(state_path):
    try:
        return asyncio.run(_agent_request(state_path, {"cmd": "status"}))
    except AgentClientError:
        return None


async def _ota_conflict_active(state_path) -> bool:
    try:
        live = await _agent_request(state_path, {"cmd": "status"})
    except AgentClientError:
        return False
    ota = live.get("state", {}).get("ota")
    return isinstance(ota, dict) and not bool(ota.get("terminal", False))


def _agent_sessions(state_path):
    try:
        return asyncio.run(_agent_request(state_path, {"cmd": "sessions"}))
    except AgentClientError:
        return None


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "default":
        if _is_setup_complete(args):
            return _default_status(args)
        result = _setup(args)
        return asyncio.run(result) if inspect.isawaitable(result) else result
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "repair":
        return _repair(args)
    if args.command == "firmware" and args.firmware_command == "update":
        return asyncio.run(_firmware_update(args))
    if args.command == "uninstall":
        return _uninstall(args)
    if args.command == "pair":
        return asyncio.run(_pair(args))
    if args.command == "agent":
        return asyncio.run(_agent_command(args))
    if args.command == "install-opencode-plugin":
        return _install_opencode_plugin(args)
    if args.command == "status":
        return _status(args)
    if args.command == "sessions":
        return _sessions(args)
    if args.command == "service-install":
        return _service_install(args)
    if args.command == "service-uninstall":
        return _service_uninstall(args)
    if args.command == "service-status":
        return _service_status(args)
    parser.error(f"unknown command: {args.command}")
    return 2


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
