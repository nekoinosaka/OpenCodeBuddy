from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Optional

from .catalog import SessionRecord
from .opencode_server import OpenCodeServerClient
from .text_width import clip_text_by_width

_LATEST_MESSAGE_LIMIT = 160


class OpenCodeSessionWatcher:
    """Read OpenCode server sessions as read-only buddy sessions.

    It projects the documented ``GET /session`` response (per-session tokens,
    cost, directory, title) into the catalog. Live events still take precedence
    for the active session.
    """

    def __init__(
        self,
        client: OpenCodeServerClient,
        *,
        status_provider: Optional[Callable[[], Mapping[str, str]]] = None,
        active_window_seconds: float = 300.0,
        completed_window_seconds: float = 120.0,
        max_sessions: int = 200,
    ) -> None:
        self.client = client
        self.status_provider = status_provider
        self.active_window_seconds = active_window_seconds
        self.completed_window_seconds = completed_window_seconds
        self.max_sessions = max(0, int(max_sessions))

    def poll(self, now: Optional[float] = None) -> list[SessionRecord]:
        now_ts = 0.0 if now is None else float(now)
        try:
            summaries = self.client.list_sessions()
        except Exception:
            return []
        status = self._status()
        records: list[SessionRecord] = []
        for summary in summaries:
            record = self._to_record(summary, now=now_ts, status=status)
            if record is not None:
                records.append(record)
        records.sort(key=lambda session: session.last_activity_at, reverse=True)
        return records[: self.max_sessions] if self.max_sessions else []

    def _status(self) -> Mapping[str, str]:
        if self.status_provider is None:
            return {}
        try:
            return self.status_provider()
        except Exception:
            return {}

    def _to_record(self, summary, *, now: float, status: Mapping[str, str]) -> Optional[SessionRecord]:
        last_activity = summary.updated_at or summary.created_at
        if last_activity <= 0:
            return None
        running = status.get(summary.session_id) in {"busy", "retry"}
        age = max(0.0, now - last_activity)
        if running:
            state = "running"
        elif age <= self.completed_window_seconds:
            state = "completed"
        elif age <= self.active_window_seconds:
            state = "recent"
        else:
            return None
        title = clip_text_by_width(summary.title, _LATEST_MESSAGE_LIMIT, ellipsis="...") if summary.title else ""
        return SessionRecord(
            session_id=summary.session_id,
            source="subagent" if summary.parent_id else "opencode",
            originator="opencode",
            cwd=summary.directory,
            state=state,
            last_activity_at=last_activity,
            latest_message=title,
            entries=[title] if title else [],
            tokens_total=summary.output_tokens,
            tokens_session=summary.output_tokens,
            control_capability="readonly",
            pending_prompt=None,
        )
