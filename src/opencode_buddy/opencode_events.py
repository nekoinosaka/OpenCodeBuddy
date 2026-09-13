from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .events import (
    AgentOutput,
    ApprovalRequest,
    ApprovalRequestResolved,
    TokenUsage,
    TurnState,
)


@dataclass
class OpenCodeSessionMeta:
    directory: str = ""
    title: str = ""


def _as_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class OpenCodeEventAdapter:
    """Translate OpenCode bus events into the buddy's internal event model.

    A plugin running inside opencode forwards raw bus events here and asks
    this process to answer permission requests. The adapter keeps the small
    amount of cross-event state needed to turn per-message token counters into
    cumulative session totals.
    """

    def __init__(self) -> None:
        self._meta: dict[str, OpenCodeSessionMeta] = {}
        self._assistant_output: dict[str, dict[str, int]] = {}

    def directory(self, session_id: str) -> str:
        meta = self._meta.get(str(session_id))
        return meta.directory if meta is not None else ""

    def forget(self, session_id: str) -> None:
        self._meta.pop(str(session_id), None)
        self._assistant_output.pop(str(session_id), None)

    def handle(self, event: object) -> list[object]:
        if not isinstance(event, dict):
            return []
        properties = event.get("properties")
        if not isinstance(properties, dict):
            return []
        kind = event.get("type")
        if kind in {"session.created", "session.updated"}:
            return self._session_meta(properties)
        if kind == "session.status":
            return self._session_status(properties)
        if kind == "session.idle":
            return self._session_idle(properties)
        if kind == "message.updated":
            return self._message_updated(properties)
        if kind == "message.part.updated":
            return self._part_updated(properties)
        if kind == "permission.updated":
            return self._permission_updated(properties)
        if kind == "permission.replied":
            return self._permission_replied(properties)
        if kind == "session.error":
            return self._session_error(properties)
        return []

    def _session_meta(self, properties: dict) -> list[object]:
        info = properties.get("info")
        if not isinstance(info, dict):
            return []
        session_id = str(info.get("id", ""))
        if not session_id:
            return []
        existing = self._meta.get(session_id) or OpenCodeSessionMeta()
        directory = info.get("directory")
        title = info.get("title")
        self._meta[session_id] = OpenCodeSessionMeta(
            directory=str(directory) if isinstance(directory, str) and directory else existing.directory,
            title=str(title) if isinstance(title, str) and title else existing.title,
        )
        return []

    def _session_status(self, properties: dict) -> list[object]:
        session_id = str(properties.get("sessionID", ""))
        status = properties.get("status")
        if not session_id or not isinstance(status, dict):
            return []
        kind = status.get("type")
        if kind == "busy":
            return [TurnState(thread_id=session_id, turn_id="busy", active=True)]
        if kind == "retry":
            return [
                TurnState(
                    thread_id=session_id,
                    turn_id="retry",
                    active=True,
                    status="retry",
                )
            ]
        if kind == "idle":
            return [
                TurnState(
                    thread_id=session_id,
                    turn_id="",
                    active=False,
                    status="completed",
                )
            ]
        return []

    def _session_idle(self, properties: dict) -> list[object]:
        session_id = str(properties.get("sessionID", ""))
        if not session_id:
            return []
        return [
            TurnState(
                thread_id=session_id,
                turn_id="",
                active=False,
                status="completed",
            )
        ]

    def _message_updated(self, properties: dict) -> list[object]:
        info = properties.get("info")
        if not isinstance(info, dict) or info.get("role") != "assistant":
            return []
        session_id = str(info.get("sessionID", ""))
        message_id = str(info.get("id", ""))
        if not session_id or not message_id:
            return []
        tokens = info.get("tokens")
        output_tokens = _as_int(tokens.get("output")) if isinstance(tokens, dict) else None
        if output_tokens is None:
            return []
        session_totals = self._assistant_output.setdefault(session_id, {})
        session_totals[message_id] = output_tokens
        total = sum(session_totals.values())
        return [
            TokenUsage(
                thread_id=session_id,
                total_tokens=total,
                tokens_today=total,
                heartbeat_total_tokens=total,
            )
        ]

    def _part_updated(self, properties: dict) -> list[object]:
        part = properties.get("part")
        if not isinstance(part, dict):
            return []
        session_id = str(part.get("sessionID", ""))
        if not session_id:
            return []
        part_type = part.get("type")
        if part_type == "text":
            if part.get("synthetic") or part.get("ignored"):
                return []
            text = part.get("text")
            if not isinstance(text, str) or not text.strip():
                return []
            return [AgentOutput(thread_id=session_id, text=text)]
        if part_type == "tool":
            state = part.get("state")
            if not isinstance(state, dict) or state.get("status") != "running":
                return []
            tool = str(part.get("tool", "tool"))
            title = state.get("title")
            detail = str(title) if isinstance(title, str) and title else tool
            return [AgentOutput(thread_id=session_id, text=detail)]
        return []

    def _permission_updated(self, properties: dict) -> list[object]:
        request_id = str(properties.get("id", ""))
        session_id = str(properties.get("sessionID", ""))
        if not request_id or not session_id:
            return []
        tool = str(properties.get("type", "tool") or "tool")
        title = properties.get("title")
        pattern = properties.get("pattern")
        hint = str(title) if isinstance(title, str) and title else ""
        if not hint and isinstance(pattern, str) and pattern:
            hint = pattern
        if not hint:
            hint = tool
        return [
            ApprovalRequest(
                thread_id=session_id,
                turn_id=str(properties.get("callID", "") or ""),
                request_id=request_id,
                command=hint,
                cwd="",
                reason=hint,
                tool=tool,
                hint=hint,
            )
        ]

    def _permission_replied(self, properties: dict) -> list[object]:
        request_id = str(properties.get("permissionID", ""))
        if not request_id:
            return []
        return [ApprovalRequestResolved(request_id=request_id)]

    def _session_error(self, properties: dict) -> list[object]:
        session_id = str(properties.get("sessionID", ""))
        error = properties.get("error")
        if not session_id or not isinstance(error, dict):
            return []
        message = _error_message(error)
        if not message:
            return []
        return [AgentOutput(thread_id=session_id, text=message)]


def _error_message(error: dict) -> str:
    for key in ("message", "name"):
        value = error.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = error.get("data")
    if isinstance(data, dict):
        value = data.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
