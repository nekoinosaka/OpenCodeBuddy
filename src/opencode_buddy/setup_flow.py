from __future__ import annotations

import os
import shutil
import tempfile
import subprocess
import uuid
from importlib import resources
from pathlib import Path

from . import runtime
from .native_helper_build import (
    build_bundled_native_helper,
    cleanup_bundled_native_helper_build,
)
from .ota_release import inspect_esp32s3_application_image
from .state_store import PersistedState

SETUP_VERSION = 1


def ensure_helper_app_installed(destination: Path | None = None) -> Path:
    destination = runtime.helper_app_path() if destination is None else destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = build_bundled_native_helper()
    staging = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    backup = destination.parent / f".{destination.name}.backup-{uuid.uuid4().hex}"
    moved_old = False
    try:
        shutil.copytree(source, staging, symlinks=False)
        executable = staging / "Contents" / "MacOS" / "OpenCodeBuddyBLEHelper"
        if not executable.is_file() or executable.is_symlink() or not os.access(executable, os.X_OK):
            raise RuntimeError("built native BLE helper executable is invalid")
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(staging)],
            check=True,
            capture_output=True,
            text=True,
        )
        if destination.exists() or destination.is_symlink():
            os.replace(destination, backup)
            moved_old = True
        try:
            os.replace(staging, destination)
        except BaseException:
            if moved_old:
                os.replace(backup, destination)
                moved_old = False
            raise
        if moved_old:
            shutil.rmtree(backup)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and destination.exists():
            shutil.rmtree(backup, ignore_errors=True)
        cleanup_bundled_native_helper_build(source)


def opencode_plugin_dir() -> Path:
    return Path.home() / ".config" / "opencode" / "plugins"


def opencode_plugin_path() -> Path:
    return opencode_plugin_dir() / "opencode-buddy.js"


def bundled_opencode_plugin_text() -> str:
    source = resources.files("opencode_buddy").joinpath(
        "opencode_plugin", "opencode-buddy.js"
    )
    with source.open("r", encoding="utf-8") as handle:
        return handle.read()


def opencode_plugin_is_current(destination: Path | None = None) -> bool:
    destination = (
        opencode_plugin_path() if destination is None else Path(destination)
    )
    try:
        return destination.read_text(encoding="utf-8") == bundled_opencode_plugin_text()
    except OSError:
        return False


def install_opencode_plugin(destination: Path | None = None) -> Path:
    destination = (
        opencode_plugin_path()
        if destination is None
        else Path(destination)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = bundled_opencode_plugin_text()
    if not destination.exists() or destination.read_text(encoding="utf-8") != text:
        destination.write_text(text, encoding="utf-8")
    return destination


def bundled_firmware_resource():
    return resources.files("opencode_buddy").joinpath(
        "firmware", "opencode-buddy-sticks3-app.bin"
    )


def ensure_firmware_artifact_installed(
    source: Path | None = None, destination: Path | None = None
) -> Path:
    destination = runtime.default_firmware_path() if destination is None else Path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        if source is None:
            resource = bundled_firmware_resource()
            try:
                source_handle = resource.open("rb")
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    "bundled firmware application image is missing from the Python package"
                ) from exc
        else:
            source = Path(source)
            if source.is_symlink():
                raise ValueError(
                    f"firmware image must be a regular non-symlink file: {source}"
                )
            if not source.is_file():
                raise FileNotFoundError(f"firmware application image is missing: {source}")
            source_handle = source.open("rb")
        with source_handle:
            with os.fdopen(descriptor, "wb", closefd=True) as destination_handle:
                descriptor = -1
                shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
        temporary.chmod(0o600)
        inspect_esp32s3_application_image(temporary)
        temporary.chmod(0o644)
        os.replace(temporary, destination)
        return destination
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def is_setup_complete(state: PersistedState) -> bool:
    if state.setup_version < SETUP_VERSION:
        return False
    if not state.paired_device_id:
        return False
    if not state.helper_app_path or not Path(state.helper_app_path).exists():
        return False
    if not opencode_plugin_path().is_file():
        return False
    if not state.service_installed:
        return False
    return True
