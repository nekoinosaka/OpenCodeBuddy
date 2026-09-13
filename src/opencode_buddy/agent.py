from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Optional

from . import runtime
from .agent_runtime import (
    AgentProcessLock,
    cleanup_stale_ota_runtime,
    require_current_user_peer,
    restrict_unix_socket,
)
from .activity_heartbeat import ActivityHeartbeat
from .ble_transport import BleBuddyTransport
from .catalog import SessionCatalog, SessionPrompt, SessionRecord
from .events import (
    AgentOutput,
    ApprovalRequest,
    ApprovalRequestResolved,
    QuestionResolved,
    TokenUsage,
    TurnState,
)
from .opencode_events import OpenCodeEventAdapter
from .opencode_server import OpenCodeServerClient, default_server_url
from .opencode_session_watcher import OpenCodeSessionWatcher
from .runtime import logs_dir as runtime_logs_dir
from .runtime import socket_path as runtime_socket_path
from .runtime import state_path as runtime_state_path
from .state_store import BridgeStateStore, PersistedState
from .text_width import clip_text_by_width
from .token_heartbeat import TokenHeartbeat
from .ota_coordination import OtaAgentSession, OtaCoordinator
from .ota_protocol import valid_ota_nonce
from .ota_release import (
    OtaImageInfo,
    build_ota_release,
    cleanup_ota_image_snapshot,
    inspect_esp32s3_application_image,
    snapshot_ota_image,
)
from .ota_server import OtaHttpsServer
from .ota_trust import require_existing_ota_trust

_SUMMARY_LIMIT = 44
_ENTRY_LIMIT = 160
_PROMPT_HINT_LIMIT = 160
_COMPLETED_TURN_DEDUPE_LIMIT = 256

_LOG = logging.getLogger(__name__)


def default_socket_path(state_path: Path) -> Path:
    if Path(state_path) == runtime_state_path():
        return runtime_socket_path()
    return state_path.parent / "agent.sock"


def default_log_dir(state_path: Path) -> Path:
    if Path(state_path) == runtime_state_path():
        return runtime_logs_dir()
    return state_path.parent / "logs"


class AgentClientError(RuntimeError):
    pass


class AgentClient:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path

    async def request(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        except OSError as exc:
            raise AgentClientError(str(exc)) from exc

        try:
            writer.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
            await writer.drain()
            raw = await reader.readline()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        if not raw:
            raise AgentClientError("Agent closed the connection without a response")
        response = json.loads(raw.decode("utf-8"))
        if not response.get("ok", False):
            raise AgentClientError(str(response.get("error", "Unknown agent error")))
        return response


@dataclass
class OpenCodeSessionRuntime:
    control_id: str
    workdir: Path
    session_id: Optional[str] = None
    state: str = "recent"
    latest_message: str = ""
    last_activity_at: float = 0.0
    tokens_total: int = 0
    tokens_session: int = 0
    heartbeat_tokens_total: Optional[int] = None
    pending_prompt: Optional[SessionPrompt] = None
    entries: Deque[str] = field(default_factory=lambda: deque(maxlen=3))

    def apply(self, event: object, *, now: float) -> None:
        thread_id = getattr(event, "thread_id", None)
        if thread_id:
            self.session_id = str(thread_id)
        self.last_activity_at = now

        if isinstance(event, TurnState):
            self.state = "running" if event.active else "completed"
            if event.active and not self.latest_message:
                self.latest_message = "OpenCode is working"
            return

        if isinstance(event, AgentOutput):
            if event.text.strip():
                entry = clip_text_by_width(event.text, _ENTRY_LIMIT, ellipsis="...")
                self.entries.appendleft(entry)
                self.latest_message = clip_text_by_width(entry, _SUMMARY_LIMIT, ellipsis="...")
                if self.state not in {"running", "waiting"}:
                    self.state = "recent"
            return

        if isinstance(event, TokenUsage):
            if event.total_tokens is not None:
                self.tokens_total = max(0, event.total_tokens)
            if event.tokens_today is not None:
                self.tokens_session = max(0, event.tokens_today)
            if event.heartbeat_total_tokens is not None:
                self.heartbeat_tokens_total = max(0, event.heartbeat_total_tokens)
            return

        if isinstance(event, ApprovalRequest):
            hint = clip_text_by_width(event.hint or event.command or event.reason, _PROMPT_HINT_LIMIT, ellipsis="...")
            self.pending_prompt = SessionPrompt(request_id=event.request_id, tool=event.tool, hint=hint)
            self.state = "waiting"
            self.latest_message = clip_text_by_width("approve: " + hint, _SUMMARY_LIMIT, ellipsis="...")
            self.entries.appendleft(self.latest_message)
            return

        if isinstance(event, ApprovalRequestResolved):
            if self.pending_prompt is None or self.pending_prompt.request_id != str(event.request_id):
                return
            self.pending_prompt = None
            self.state = "running"
            return

        raise TypeError("Unsupported OpenCode event: {!r}".format(type(event)))

    def close(self, *, now: float) -> None:
        self.last_activity_at = now
        if self.state not in {"waiting", "completed"}:
            self.state = "completed"
        self.pending_prompt = None

    def to_record(self) -> Optional[SessionRecord]:
        if not self.session_id:
            return None
        return SessionRecord(
            session_id=self.session_id,
            source="opencode",
            originator="opencode-buddy",
            cwd=str(self.workdir),
            state=self.state,
            last_activity_at=self.last_activity_at,
            latest_message=self.latest_message,
            entries=list(self.entries),
            tokens_total=self.tokens_total,
            tokens_session=self.tokens_session,
            control_capability="managed",
            pending_prompt=self.pending_prompt,
            heartbeat_tokens_total=self.heartbeat_tokens_total,
        )


class BuddyAgent:
    def __init__(
        self,
        state_path: Path,
        *,
        socket_path: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None,
        readonly_poll_interval: float = 2.0,
        keepalive_interval: float = 10.0,
        reconnect_interval: float = 5.0,
        session_watcher: Optional[Any] = None,
        server_client: Optional[OpenCodeServerClient] = None,
        ble_factory: Optional[Callable[..., BleBuddyTransport]] = None,
        ota_image_inspector: Callable[[Path], OtaImageInfo] = inspect_esp32s3_application_image,
        ota_trust_loader: Callable[[], Any] = require_existing_ota_trust,
        ota_server_factory: Callable[..., Any] = OtaHttpsServer,
        ota_release_builder: Callable[..., Any] = build_ota_release,
        ota_nonce_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
        ota_status_timeout: float = 15.0,
        ota_confirm_timeout: float = 180.0,
        ota_install_timeout: float = 600.0,
        opencode_permission_timeout: float = 60.0,
        opencode_connect_wait: float = 3.0,
    ) -> None:
        self.state_path = state_path
        self.socket_path = socket_path or default_socket_path(state_path)
        self._process_lock = AgentProcessLock(self.state_path.parent / "agent.lock")
        self.clock = clock or time.time
        self.readonly_poll_interval = readonly_poll_interval
        self.keepalive_interval = keepalive_interval
        self.reconnect_interval = reconnect_interval
        self.store = BridgeStateStore(state_path)
        self.catalog = SessionCatalog()
        self._server_client = server_client or OpenCodeServerClient(default_server_url())
        self._session_watcher = session_watcher
        self._session_watcher_injected = session_watcher is not None
        self._opencode_server_url: Optional[str] = None
        if self._session_watcher is None and os.environ.get("OPENCODE_SERVER_URL"):
            self._session_watcher = OpenCodeSessionWatcher(self._server_client)
        self._ble_factory = ble_factory or BleBuddyTransport
        self._request_to_control: dict[str, str] = {}
        self._opencode_adapter = OpenCodeEventAdapter()
        self._opencode_runtime: dict[str, OpenCodeSessionRuntime] = {}
        self._opencode_permission_timeout = opencode_permission_timeout
        self._opencode_connect_wait = opencode_connect_wait
        self._opencode_permission_waiters: dict[str, asyncio.Future[str]] = {}
        self._opencode_question_waiters: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._active_question: Optional[dict[str, object]] = None
        self._tasks: list[asyncio.Task[None]] = []
        self._server: Optional[asyncio.AbstractServer] = None
        self._stopped: Optional[asyncio.Event] = None
        self._stop_requested = False
        self._ble: Optional[BleBuddyTransport] = None
        self._ble_connected = False
        self._last_payload: Optional[dict[str, object]] = None
        self._completion_seq = self.store.load().completion_seq
        self._completed_turn_order: Deque[tuple[str, str]] = deque()
        self._completed_turn_keys: set[tuple[str, str]] = set()
        self._readonly_completion_initialized = False
        self._activity_heartbeat = ActivityHeartbeat()
        self._token_heartbeat = TokenHeartbeat()
        self._readonly_activity_seen: dict[str, float] = {}
        self._ota_session_lock: Optional[asyncio.Lock] = None
        self._ota_session_lock_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ota_claim_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ota_session_active = False
        self._ota_image_inspector = ota_image_inspector
        self._ota_snapshot_root = runtime.ota_snapshots_dir()
        self._ota_nonce_factory = ota_nonce_factory
        self._ota_state: Optional[OtaAgentSession] = None
        self._ota_task: Optional[asyncio.Task[None]] = None
        self._ota_coordinator = OtaCoordinator(
            get_ble=lambda: self._ble,
            is_ble_connected=lambda: self._ble_connected,
            ota_session=self._claimed_ota_session,
            trust_loader=ota_trust_loader,
            server_factory=ota_server_factory,
            release_builder=ota_release_builder,
            status_timeout=ota_status_timeout,
            confirm_timeout=ota_confirm_timeout,
            install_timeout=ota_install_timeout,
            reconnect_interval=reconnect_interval,
        )

    @property
    def ota_session_active(self) -> bool:
        return self._ota_session_active

    def _ota_lock_for_running_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._ota_session_lock is None or self._ota_session_lock_loop is not loop:
            if self._ota_session_active:
                raise RuntimeError("OTA session is active on another event loop")
            self._ota_session_lock = asyncio.Lock()
            self._ota_session_lock_loop = loop
        return self._ota_session_lock

    @contextlib.asynccontextmanager
    async def ota_session(self):
        async with self._ota_lock_for_running_loop():
            self._ota_session_active = True
            self._ota_claim_loop = asyncio.get_running_loop()
            try:
                yield
            finally:
                self._ota_session_active = False
                self._ota_claim_loop = None

    async def _claim_ota_session(self) -> None:
        if self._ota_session_active:
            raise RuntimeError("a firmware update is already active")
        lock = self._ota_lock_for_running_loop()
        if lock.locked():
            raise RuntimeError("a firmware update is already active")
        await lock.acquire()
        self._ota_session_active = True
        self._ota_claim_loop = asyncio.get_running_loop()

    def _release_claimed_ota_session(self) -> None:
        loop = asyncio.get_running_loop()
        if not self._ota_session_active or self._ota_claim_loop is not loop:
            raise RuntimeError("OTA session release attempted from another event loop")
        lock = self._ota_session_lock
        if lock is None or not lock.locked():
            raise RuntimeError("OTA session lock is not held")
        self._ota_session_active = False
        self._ota_claim_loop = None
        lock.release()

    @contextlib.asynccontextmanager
    async def _claimed_ota_session(self):
        if (
            not self._ota_session_active
            or self._ota_claim_loop is not asyncio.get_running_loop()
        ):
            raise RuntimeError("OTA session was not atomically claimed")
        yield

    async def run(self) -> None:
        if self._stopped is not None:
            raise RuntimeError("buddy agent is already running")
        self._process_lock.acquire()
        try:
            ota_root = self.state_path.parent / "ota"
            cleanup_stale_ota_runtime(
                snapshots_root=ota_root / "snapshots",
                sessions_root=ota_root / "private" / "sessions",
                releases_root=ota_root / "releases",
            )
            self._stop_requested = False
            stopped = asyncio.Event()
            self._stopped = stopped
            try:
                self.socket_path.parent.mkdir(parents=True, exist_ok=True)
                if self.socket_path.exists():
                    self.socket_path.unlink()

                self._server = await asyncio.start_unix_server(
                    self._handle_client,
                    path=str(self.socket_path),
                )
                restrict_unix_socket(self.socket_path)
                self._tasks = [
                    asyncio.create_task(self._readonly_loop(), name="readonly"),
                    asyncio.create_task(self._ble_loop(), name="ble"),
                    asyncio.create_task(self._keepalive_loop(), name="keepalive"),
                ]
                for task in self._tasks:
                    task.add_done_callback(lambda _: stopped.set())
                await self._publish_state(force=True)
                await stopped.wait()
                failed = next(
                    (task for task in self._tasks if task.done()),
                    None,
                )
                if failed is not None and not self._stop_requested:
                    if failed.cancelled():
                        raise RuntimeError(
                            "{} background task was cancelled".format(
                                failed.get_name()
                            )
                        )
                    try:
                        failed.result()
                    except Exception:
                        _LOG.exception(
                            "%s background task failed", failed.get_name()
                        )
                        raise
                    raise RuntimeError(
                        "{} background task exited unexpectedly".format(
                            failed.get_name()
                        )
                    )
            finally:
                try:
                    await self.shutdown()
                finally:
                    self._stopped = None
        finally:
            self._process_lock.release()

    async def shutdown(self) -> None:
        if self._ota_state is not None and not self._ota_state.terminal:
            self._ota_state.cancel_event.set()
        if self._ota_task is not None and not self._ota_task.done():
            self._ota_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ota_task
        self._ota_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self._request_to_control.clear()
        for waiter in list(self._opencode_permission_waiters.values()):
            if not waiter.done():
                waiter.cancel()
        self._opencode_permission_waiters.clear()
        for waiter in list(self._opencode_question_waiters.values()):
            if not waiter.done():
                waiter.cancel()
        self._opencode_question_waiters.clear()
        self._active_question = None
        self._opencode_runtime.clear()
        if self._ble is not None:
            with contextlib.suppress(Exception):
                await self._ble.disconnect()
        self._ble = None
        self._ble_connected = False
        if self.socket_path.exists():
            self.socket_path.unlink()
        snapshot = self._snapshot()
        self._persist(snapshot, agent_running=False)

    def status_payload(self) -> dict[str, object]:
        current = self.store.load()
        snapshot = self._snapshot()
        return {
            "agent_running": True,
            "buddy_connected": self._ble_connected,
            "paired_device_id": current.paired_device_id,
            "paired_device_name": current.paired_device_name,
            "socket_path": str(self.socket_path),
            "opencode_server_url": self._opencode_server_url,
            "snapshot": snapshot.as_ble_payload(),
            "sessions": [session.as_dict() for session in self.catalog.sessions(now=self.clock())],
            "ota": self._ota_state.public_payload(include_identity=False)
            if self._ota_state else None,
        }

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        response: dict[str, object]
        try:
            require_current_user_peer(writer.get_extra_info("socket"))
            raw = await reader.readline()
            if not raw:
                return
            payload = json.loads(raw.decode("utf-8"))
            response = await self._handle_command(payload)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        try:
            writer.write(
                (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")
            )
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _handle_command(self, payload: dict[str, object]) -> dict[str, object]:
        command = str(payload.get("cmd", ""))
        if command == "ping":
            return {"ok": True}
        if command == "status":
            return {"ok": True, "state": self.status_payload()}
        if command == "sessions":
            return {"ok": True, "sessions": self.status_payload()["sessions"]}
        if command == "hello":
            return self._handle_hello(payload)
        if command == "notify":
            return await self._opencode_notify(payload)
        if command == "permission_ask":
            return await self._opencode_permission_ask(payload)
        if command == "question_ask":
            return await self._opencode_question_ask(payload)
        if command == "ota_begin":
            return await self._begin_ota_update(payload)
        if command == "ota_status":
            return self._ota_command_status(payload)
        if command == "ota_cancel":
            return await self._cancel_ota_update(payload)
        if command == "stop":
            self._stop_requested = True
            if self._stopped is not None:
                self._stopped.set()
            return {"ok": True}
        raise AgentClientError("Unknown agent command: {}".format(command))

    async def _begin_ota_update(self, payload: dict[str, object]) -> dict[str, object]:
        if self._ota_task is not None and not self._ota_task.done():
            raise RuntimeError("a firmware update is already active")
        if any(runtime.pending_prompt is not None for runtime in self._opencode_runtime.values()):
            raise RuntimeError("finish the active approval before updating firmware")
        if not self._ble_connected or self._ble is None:
            raise RuntimeError("OpenCode Buddy is not connected over Bluetooth")
        raw_path = payload.get("firmware")
        if not isinstance(raw_path, str) or not raw_path or len(raw_path) > 4096 or "\x00" in raw_path:
            raise ValueError("firmware application image path is invalid")
        await self._claim_ota_session()
        snapshot: Optional[Path] = None
        try:
            snapshot = snapshot_ota_image(
                Path(raw_path).expanduser(), self._ota_snapshot_root
            )
            inspected = self._ota_image_inspector(snapshot)
            nonce = self._ota_nonce_factory()
            # build_ota_offer performs the same nonce/generation bounds used by the
            # device parser before any network endpoint is created.
            if not valid_ota_nonce(nonce):
                raise RuntimeError("OTA nonce generator returned an invalid value")
            session = OtaAgentSession(
                nonce=nonce,
                generation=1,
                image_path=inspected.path,
                version=inspected.version,
            )
            self._ota_state = session
            self._ota_task = asyncio.create_task(
                self._run_ota_update(session, inspected)
            )
            return {"ok": True, "ota": session.public_payload()}
        except BaseException:
            if snapshot is not None:
                with contextlib.suppress(Exception):
                    cleanup_ota_image_snapshot(snapshot)
            self._release_claimed_ota_session()
            raise

    def _ota_command_status(self, payload: dict[str, object]) -> dict[str, object]:
        session = self._ota_state
        if session is None:
            raise RuntimeError("no firmware update session exists")
        if payload.get("nonce") != session.nonce:
            raise RuntimeError("firmware update session does not match")
        return {"ok": True, "ota": session.public_payload()}

    async def _cancel_ota_update(self, payload: dict[str, object]) -> dict[str, object]:
        session = self._ota_state
        if session is None:
            return {"ok": True}
        if payload.get("nonce") != session.nonce:
            raise RuntimeError("firmware update session does not match")
        applied = await self._ota_coordinator.cancel(session)
        return {
            "ok": True,
            "cancel_applied": applied,
            "ota": session.public_payload(),
        }

    async def _run_ota_update(
        self, session: OtaAgentSession, inspected: OtaImageInfo
    ) -> None:
        try:
            await self._ota_coordinator.run(session, inspected)
        finally:
            with contextlib.suppress(Exception):
                cleanup_ota_image_snapshot(inspected.path)
            self._release_claimed_ota_session()

    async def _readonly_loop(self) -> None:
        while not self._stop_requested:
            await self._poll_opencode_sessions()
            await asyncio.sleep(self.readonly_poll_interval)

    async def _poll_opencode_sessions(self) -> None:
        if self._session_watcher is None:
            return
        try:
            readonly = await asyncio.to_thread(
                self._session_watcher.poll,
                now=self.clock(),
            )
        except Exception:
            _LOG.debug("OpenCode session poll failed", exc_info=True)
            return
        self._apply_readonly_sessions(readonly)
        await self._publish_state()

    def _refresh_readonly_state(self) -> None:
        if self._session_watcher is None:
            return
        try:
            readonly = self._session_watcher.poll(now=self.clock())
        except Exception:
            return
        self._apply_readonly_sessions(readonly)

    def _apply_readonly_sessions(self, readonly: list[SessionRecord]) -> None:
        readonly_ids = {session.session_id for session in readonly}
        for session in readonly:
            previous = self._readonly_activity_seen.get(session.session_id)
            if previous is None or session.last_activity_at > previous:
                self._activity_heartbeat.record(session.last_activity_at)
            self._readonly_activity_seen[session.session_id] = session.last_activity_at
        self._readonly_activity_seen = {
            session_id: timestamp
            for session_id, timestamp in self._readonly_activity_seen.items()
            if session_id in readonly_ids
        }
        self._record_readonly_completions(readonly)
        self.catalog.replace_readonly(readonly)

    async def _ble_loop(self) -> None:
        while not self._stop_requested:
            current = self.store.load()
            paired_device_id = current.paired_device_id
            paired_device_name = current.paired_device_name
            if not paired_device_id:
                self._ble_connected = False
                await asyncio.sleep(self.reconnect_interval)
                continue
            if self._ble is None or self._ble.device_id != paired_device_id:
                if self._ble is not None:
                    with contextlib.suppress(Exception):
                        await self._ble.disconnect()
                self._ble = self._ble_factory(
                    paired_device_id,
                    device_name=paired_device_name,
                    on_permission=self._handle_device_permission,
                    on_question=self._handle_device_question,
                )
                self._ble_connected = False
            if self._ble_connected and not getattr(self._ble, "is_connected", True):
                self._ble_connected = False
            if not self._ble_connected:
                try:
                    await self._ble.connect()
                    self._ble_connected = True
                    await self._publish_state(force=True)
                except Exception:
                    self._ble_connected = False
                    if self._ble is not None:
                        with contextlib.suppress(Exception):
                            await self._ble.disconnect()
                    self._ble = None
            await asyncio.sleep(self.reconnect_interval)

    async def _keepalive_loop(self) -> None:
        while not self._stop_requested:
            await asyncio.sleep(self.keepalive_interval)
            await self._publish_state(force=True)

    def _record_completed_turn(self, event: object) -> None:
        if not isinstance(event, TurnState) or event.active or event.status != "completed":
            return
        self._record_completion_key((event.thread_id, event.turn_id), notify=True)

    def _record_readonly_completions(self, sessions: list[SessionRecord]) -> None:
        if not self._readonly_completion_initialized:
            for session in sessions:
                if session.source != "subagent" and session.completed_turn_id:
                    self._record_completion_key(
                        (session.session_id, session.completed_turn_id),
                        notify=False,
                    )
            self._readonly_completion_initialized = True
            return

        for session in sessions:
            if (
                session.source != "subagent"
                and session.state == "completed"
                and session.completed_turn_id
            ):
                self._record_completion_key(
                    (session.session_id, session.completed_turn_id),
                    notify=True,
                )

    def _record_completion_key(self, key: tuple[str, str], *, notify: bool) -> None:
        if key in self._completed_turn_keys:
            return
        if len(self._completed_turn_order) >= _COMPLETED_TURN_DEDUPE_LIMIT:
            expired = self._completed_turn_order.popleft()
            self._completed_turn_keys.remove(expired)
        self._completed_turn_order.append(key)
        self._completed_turn_keys.add(key)
        if notify:
            self._completion_seq = (self._completion_seq + 1) & 0xFFFFFFFF

    def _handle_hello(self, payload: dict[str, object]) -> dict[str, object]:
        self._ensure_server_url(payload.get("serverUrl"))
        return {"ok": True}

    def _ensure_server_url(self, url: object) -> None:
        if not isinstance(url, str) or not url or url == self._opencode_server_url:
            return
        self._opencode_server_url = url
        self._server_client = OpenCodeServerClient(url)

    async def _opencode_notify(self, payload: dict[str, object]) -> dict[str, object]:
        self._ensure_server_url(payload.get("serverUrl"))
        events = self._opencode_adapter.handle(payload.get("event"))
        for event in events:
            await self._handle_opencode_event(event)
        return {"ok": True}

    async def _opencode_permission_ask(self, payload: dict[str, object]) -> dict[str, object]:
        permission = payload.get("permission")
        if not isinstance(permission, dict):
            return {"ok": True, "decision": "ask"}
        request_id = str(permission.get("id", ""))
        if not request_id:
            return {"ok": True, "decision": "ask"}
        if not self._ble_connected or self._ble is None:
            # The BLE loop reconnects every few seconds; give it a moment so a
            # transient drop doesn't push the prompt back to the terminal.
            deadline = time.monotonic() + self._opencode_connect_wait
            while (not self._ble_connected or self._ble is None) and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
        if not self._ble_connected or self._ble is None:
            return {"ok": True, "decision": "ask"}
        if self.catalog.session_for_request(request_id) is None:
            for event in self._opencode_adapter.handle(
                {"type": "permission.updated", "properties": permission}
            ):
                await self._handle_opencode_event(event)
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._opencode_permission_waiters[request_id] = waiter
        try:
            decision = await asyncio.wait_for(waiter, timeout=self._opencode_permission_timeout)
        except asyncio.TimeoutError:
            decision = "ask"
        finally:
            self._opencode_permission_waiters.pop(request_id, None)
            await self._resolve_opencode_permission(request_id)
        directory = self._opencode_adapter.directory(str(permission.get("sessionID", "")))
        return {"ok": True, "decision": decision, "directory": directory}

    async def _handle_opencode_event(self, event: object) -> None:
        now = self.clock()
        self._activity_heartbeat.record(now)
        if isinstance(event, ApprovalRequestResolved):
            await self._resolve_opencode_permission(event.request_id)
            return
        if isinstance(event, QuestionResolved):
            await self._resolve_opencode_question(event.request_id)
            return
        session_id = getattr(event, "thread_id", None)
        if not session_id:
            return
        session_id = str(session_id)
        workdir = Path(self._opencode_adapter.directory(session_id) or ".")
        runtime = self._opencode_runtime.get(session_id)
        if runtime is None:
            runtime = OpenCodeSessionRuntime(control_id=session_id, workdir=workdir)
            self._opencode_runtime[session_id] = runtime
        elif workdir != Path(".") and runtime.workdir != workdir:
            runtime.workdir = workdir
        runtime.apply(event, now=now)
        record = runtime.to_record()
        if record is not None:
            self.catalog.upsert(record)
        if isinstance(event, ApprovalRequest):
            self._request_to_control[event.request_id] = session_id
        self._record_completed_turn(event)
        await self._publish_state()

    async def _resolve_opencode_permission(self, request_id: str) -> None:
        control_id = self._request_to_control.pop(str(request_id), None)
        if control_id is not None:
            runtime = self._opencode_runtime.get(control_id)
            if runtime is not None and runtime.pending_prompt is not None:
                runtime.apply(ApprovalRequestResolved(request_id=request_id), now=self.clock())
                record = runtime.to_record()
                if record is not None:
                    self.catalog.upsert(record)
        self.catalog.resolve_prompt(str(request_id))
        waiter = self._opencode_permission_waiters.get(str(request_id))
        if waiter is not None and not waiter.done():
            waiter.set_result("ask")
        await self._publish_state()

    async def _handle_device_permission(self, request_id: str, decision: str) -> None:
        waiter = self._opencode_permission_waiters.get(str(request_id))
        if waiter is None or waiter.done():
            return
        waiter.set_result(decision if decision in {"once", "deny", "always"} else "once")

    async def _opencode_question_ask(self, payload: dict[str, object]) -> dict[str, object]:
        request_id = str(payload.get("request_id", ""))
        options_raw = payload.get("options")
        options = (
            [str(option) for option in options_raw]
            if isinstance(options_raw, list)
            else []
        )
        if not request_id or not options:
            return {"ok": True, "decision": "ask"}
        if not self._ble_connected or self._ble is None:
            deadline = time.monotonic() + self._opencode_connect_wait
            while (
                not self._ble_connected or self._ble is None
            ) and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
        if not self._ble_connected or self._ble is None:
            return {"ok": True, "decision": "ask"}
        index = int(payload.get("index", 0) or 0)
        total = int(payload.get("total", 1) or 1)
        device_id = request_id if total <= 1 else "{}#{}".format(request_id, index)
        self._active_question = {
            "id": device_id,
            "header": clip_text_by_width(
                str(payload.get("header", "") or ""), 30, ellipsis="…"
            ),
            "text": clip_text_by_width(
                str(payload.get("question", "") or ""), 120, ellipsis="…"
            ),
            "options": [
                clip_text_by_width(option, 28, ellipsis="…") for option in options[:6]
            ],
            "multiple": bool(payload.get("multiple")),
            "index": index,
            "total": total,
        }
        waiter: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._opencode_question_waiters[device_id] = waiter
        try:
            result = await asyncio.wait_for(
                waiter, timeout=self._opencode_permission_timeout
            )
        except asyncio.TimeoutError:
            result = {"decision": "ask"}
        finally:
            self._opencode_question_waiters.pop(device_id, None)
            if (
                self._active_question is not None
                and self._active_question.get("id") == device_id
            ):
                self._active_question = None
            await self._publish_state()
        if result.get("reject"):
            return {"ok": True, "reject": True}
        if result.get("decision") == "ask":
            return {"ok": True, "decision": "ask"}
        answers = result.get("answers")
        return {"ok": True, "answers": answers if isinstance(answers, list) else []}

    async def _handle_device_question(
        self, device_id: str, answers: list, reject: bool
    ) -> None:
        waiter = self._opencode_question_waiters.get(str(device_id))
        if waiter is None or waiter.done():
            return
        if reject:
            waiter.set_result({"reject": True})
        else:
            waiter.set_result({"answers": [str(answer) for answer in answers]})

    async def _resolve_opencode_question(self, request_id: str) -> None:
        prefix = "{}#".format(request_id)
        for device_id in list(self._opencode_question_waiters):
            if device_id != request_id and not device_id.startswith(prefix):
                continue
            waiter = self._opencode_question_waiters.get(device_id)
            if waiter is not None and not waiter.done():
                waiter.set_result({"decision": "ask"})
        await self._publish_state()

    async def _publish_state(self, *, force: bool = False) -> None:
        snapshot = self._snapshot()
        payload = snapshot.as_ble_payload()
        self._persist(snapshot, agent_running=True)
        if force or payload != self._last_payload:
            if not self._ota_session_active:
                self._last_payload = payload
            if not self._ota_session_active and self._ble is not None and self._ble_connected:
                try:
                    await self._ble.send_snapshot(snapshot)
                except Exception:
                    self._ble_connected = False
                    with contextlib.suppress(Exception):
                        await self._ble.disconnect()
                    self._persist(snapshot, agent_running=True)

    def _snapshot(self):
        now = self.clock()
        sessions = self.catalog.sessions(now=now)
        session_ids = {session.session_id for session in sessions}
        for stale in [sid for sid in list(self._opencode_runtime) if sid not in session_ids]:
            self._opencode_runtime.pop(stale, None)
            self._opencode_adapter.forget(stale)
        for session in sessions:
            heartbeat_total = session.heartbeat_tokens_total
            if heartbeat_total is None and session.control_capability == "readonly":
                heartbeat_total = session.tokens_total
            if heartbeat_total is None:
                continue
            self._token_heartbeat.observe(
                session.session_id,
                heartbeat_total,
                now,
            )
        self._token_heartbeat.retain_sessions(session_ids)
        return replace(
            self.catalog.snapshot(now=now),
            question=dict(self._active_question) if self._active_question else None,
            completion_seq=self._completion_seq,
            activity20=self._activity_heartbeat.mask(now),
            token20v1=(
                self._token_heartbeat.encoded(now)
                if self._token_heartbeat.available
                else None
            ),
        )

    def _persist(self, snapshot: Any, *, agent_running: bool) -> None:
        current = self.store.load()
        sessions = [session.as_dict() for session in self.catalog.sessions(now=self.clock())]
        active_thread_id = sessions[0]["session_id"] if sessions else None
        self.store.save(
            PersistedState(
                paired_device_id=current.paired_device_id,
                paired_device_name=current.paired_device_name,
                tokens_today=snapshot.tokens_today,
                tokens_date=current.tokens_date,
                tokens_total=snapshot.tokens,
                completion_seq=self._completion_seq,
                active_thread_id=active_thread_id,
                buddy_connected=self._ble_connected,
                last_msg=snapshot.msg,
                snapshot=snapshot.as_ble_payload(),
                sessions=sessions,
                agent_running=agent_running,
                setup_version=current.setup_version,
                helper_app_path=current.helper_app_path,
                service_installed=current.service_installed,
            )
        )


async def wait_for_agent(socket_path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    client = AgentClient(socket_path)
    while time.monotonic() < deadline:
        try:
            await client.request({"cmd": "ping"})
            return
        except AgentClientError:
            await asyncio.sleep(0.1)
    raise AgentClientError("Timed out waiting for buddy agent")


def spawn_agent_process(state_path: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "opencode_buddy",
        "--state-path",
        str(state_path),
        "agent",
    ]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
