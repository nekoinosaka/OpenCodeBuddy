from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Tuple

IS_WINDOWS = sys.platform.startswith("win")

_LOOPBACK_HOST = "127.0.0.1"

ConnectionHandler = Callable[
    [asyncio.StreamReader, asyncio.StreamWriter], Awaitable[Any]
]


async def start_local_server(
    path: Path, handler: ConnectionHandler
) -> asyncio.AbstractServer:
    """Bind the single local agent control endpoint.

    POSIX uses an AF_UNIX socket. Windows uses a loopback TCP listener with an
    auto-assigned port recorded in a private endpoint file, because asyncio's
    default Proactor event loop cannot serve AF_UNIX.
    """

    path = Path(path)
    if not IS_WINDOWS:
        return await asyncio.start_unix_server(handler, path=str(path))

    server = await asyncio.start_server(handler, host=_LOOPBACK_HOST, port=0)
    socket = server.sockets[0]
    port = int(socket.getsockname()[1])
    _write_endpoint(path, {"host": _LOOPBACK_HOST, "port": port})
    return server


async def open_local_connection(
    path: Path,
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a client connection to the local agent control endpoint."""

    path = Path(path)
    if not IS_WINDOWS:
        return await asyncio.open_unix_connection(str(path))

    endpoint = _read_endpoint(path)
    return await asyncio.open_connection(endpoint["host"], int(endpoint["port"]))


def restrict_local_endpoint(path: Path) -> None:
    """Limit the local control endpoint to its owner."""

    path = Path(path)
    if IS_WINDOWS:
        # The endpoint file only carries host/port under the user profile; keep
        # it as tight as the platform allows.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return

    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError("buddy agent socket is unavailable") from exc
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError("buddy agent socket must be a real Unix socket")
    os.chmod(path, 0o600, follow_symlinks=False)


def _write_endpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _read_endpoint(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"buddy agent endpoint is unavailable: {path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OSError(f"buddy agent endpoint is invalid: {path}") from exc
    host = payload.get("host")
    port = payload.get("port")
    if not isinstance(host, str) or not isinstance(port, int):
        raise OSError(f"buddy agent endpoint is invalid: {path}")
    return {"host": host, "port": port}
