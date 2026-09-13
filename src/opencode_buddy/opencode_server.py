from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

_DEFAULT_URL = "http://127.0.0.1:4096"

Fetcher = Callable[[str, str, Optional[bytes], float], "tuple[int, bytes]"]


class OpenCodeServerError(RuntimeError):
    pass


def default_server_url() -> str:
    return os.environ.get("OPENCODE_SERVER_URL", "").strip() or _DEFAULT_URL


@dataclass(frozen=True)
class OpenCodeSessionSummary:
    session_id: str
    parent_id: Optional[str]
    directory: str
    title: str
    agent: str
    created_at: float
    updated_at: float
    output_tokens: int
    input_tokens: int
    cost: float


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _millis_to_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) / 1000.0


def _default_fetch(method: str, url: str, body: Optional[bytes], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
    if password:
        import base64

        username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read() if exc.fp is not None else b""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OpenCodeServerError(str(exc)) from exc


class OpenCodeServerClient:
    """Minimal HTTP client for a standalone ``opencode serve`` instance.

    Live events and permission replies are handled entirely by the in-process
    plugin. This client is only used by the optional session watcher when
    ``OPENCODE_SERVER_URL`` points at a server that is reachable over HTTP
    (a standalone ``opencode serve``); the TUI's embedded server is not.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: float = 3.0,
        fetch: Optional[Fetcher] = None,
    ) -> None:
        self.base_url = (base_url or default_server_url()).rstrip("/")
        self.timeout = timeout
        self._fetch = fetch or _default_fetch

    def health(self) -> bool:
        try:
            status, _ = self._fetch("GET", f"{self.base_url}/global/health", None, self.timeout)
        except OpenCodeServerError:
            return False
        return status == 200

    def list_sessions(self) -> list[OpenCodeSessionSummary]:
        status, body = self._fetch("GET", f"{self.base_url}/session", None, self.timeout)
        if status != 200:
            raise OpenCodeServerError(f"session list failed with status {status}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenCodeServerError("session list returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise OpenCodeServerError("session list is not an array")
        summaries: list[OpenCodeSessionSummary] = []
        for item in payload:
            summary = _session_summary(item)
            if summary is not None:
                summaries.append(summary)
        return summaries

def _session_summary(item: object) -> Optional[OpenCodeSessionSummary]:
    if not isinstance(item, dict):
        return None
    session_id = item.get("id")
    if not isinstance(session_id, str) or not session_id:
        return None
    tokens = item.get("tokens") if isinstance(item.get("tokens"), dict) else {}
    time_block = item.get("time") if isinstance(item.get("time"), dict) else {}
    parent_id = item.get("parentID")
    return OpenCodeSessionSummary(
        session_id=session_id,
        parent_id=parent_id if isinstance(parent_id, str) and parent_id else None,
        directory=str(item.get("directory", "") or ""),
        title=str(item.get("title", "") or ""),
        agent=str(item.get("agent", "") or ""),
        created_at=_millis_to_seconds(time_block.get("created")),
        updated_at=_millis_to_seconds(time_block.get("updated")),
        output_tokens=_as_int(tokens.get("output")),
        input_tokens=_as_int(tokens.get("input")),
        cost=_as_float(item.get("cost")),
    )
