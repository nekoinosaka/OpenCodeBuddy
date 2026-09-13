from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from opencode_buddy import local_ipc


@pytest.mark.skipif(local_ipc.IS_WINDOWS, reason="AF_UNIX specific assertions")
def test_restrict_local_endpoint_rejects_non_socket(tmp_path: Path):
    regular = tmp_path / "agent.sock"
    regular.write_text("not a socket", encoding="utf-8")
    with pytest.raises(RuntimeError):
        local_ipc.restrict_local_endpoint(regular)


def test_local_endpoint_round_trip():
    # AF_UNIX paths are limited to ~104 bytes, so keep the root short.
    root = Path(tempfile.mkdtemp(prefix="ocb-", dir="/tmp"))
    endpoint = root / "agent.sock"

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        line = await reader.readline()
        writer.write(b"echo:" + line)
        await writer.drain()
        writer.close()

    async def exercise():
        server = await local_ipc.start_local_server(endpoint, handler)
        try:
            local_ipc.restrict_local_endpoint(endpoint)
            reader, writer = await local_ipc.open_local_connection(endpoint)
            writer.write(b"ping\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), timeout=5.0)
            writer.close()
            return response
        finally:
            server.close()
            await server.wait_closed()

    try:
        assert asyncio.run(exercise()) == b"echo:ping\n"
    finally:
        shutil.rmtree(root, ignore_errors=True)
