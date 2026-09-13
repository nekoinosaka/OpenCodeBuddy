import asyncio

from opencode_buddy.agent import BuddyAgent


class _CapturingBle:
    def __init__(self, device_id="device-1") -> None:
        self.device_id = device_id
        self.sent_payloads = []
        self.disconnected = False

    async def send_snapshot(self, snapshot) -> None:
        self.sent_payloads.append(snapshot.as_ble_payload())

    async def disconnect(self) -> None:
        self.disconnected = True


def _permission_payload(request_id="per-1", session_id="ses-1"):
    return {
        "id": request_id,
        "sessionID": session_id,
        "type": "bash",
        "title": "rm -rf /tmp/foo",
        "pattern": "rm *",
    }


def test_notify_updates_the_snapshot(tmp_path):
    async def exercise():
        agent = BuddyAgent(tmp_path / "state.json", clock=lambda: 100.0)
        agent._ble = _CapturingBle()
        agent._ble_connected = True
        await agent._handle_command(
            {
                "cmd": "notify",
                "event": {
                    "type": "session.status",
                    "properties": {"sessionID": "ses-1", "status": {"type": "busy"}},
                },
            }
        )
        return agent

    agent = asyncio.run(exercise())

    assert agent._snapshot().running == 1
    assert agent._snapshot().total == 1


def test_permission_ask_without_ble_falls_back_to_host_prompt(tmp_path):
    async def exercise():
        agent = BuddyAgent(tmp_path / "state.json", clock=lambda: 100.0)
        agent._ble_connected = False
        return await agent._handle_command(
            {"cmd": "permission_ask", "permission": _permission_payload()}
        )

    assert asyncio.run(exercise()) == {"ok": True, "decision": "ask"}


def test_device_approval_resolves_permission_ask(tmp_path):
    async def exercise():
        agent = BuddyAgent(
            tmp_path / "state.json",
            clock=lambda: 100.0,
            opencode_permission_timeout=5.0,
        )
        agent._ble = _CapturingBle()
        agent._ble_connected = True
        task = asyncio.create_task(
            agent._handle_command(
                {"cmd": "permission_ask", "permission": _permission_payload()}
            )
        )
        for _ in range(100):
            if agent._opencode_permission_waiters:
                break
            await asyncio.sleep(0.01)
        waiting_snapshot = agent._snapshot()
        await agent._handle_device_permission("per-1", "once")
        response = await task
        return response, waiting_snapshot, agent._snapshot()

    response, waiting, resolved = asyncio.run(exercise())

    assert response == {"ok": True, "decision": "once"}
    assert waiting.waiting == 1
    assert waiting.prompt == {"id": "per-1", "tool": "bash", "hint": "rm -rf /tmp/foo"}
    assert resolved.waiting == 0
    assert resolved.prompt is None


def test_device_denial_resolves_permission_ask(tmp_path):
    async def exercise():
        agent = BuddyAgent(
            tmp_path / "state.json",
            clock=lambda: 100.0,
            opencode_permission_timeout=5.0,
        )
        agent._ble = _CapturingBle()
        agent._ble_connected = True
        task = asyncio.create_task(
            agent._handle_command(
                {"cmd": "permission_ask", "permission": _permission_payload()}
            )
        )
        for _ in range(100):
            if agent._opencode_permission_waiters:
                break
            await asyncio.sleep(0.01)
        await agent._handle_device_permission("per-1", "deny")
        return await task

    assert asyncio.run(exercise()) == {"ok": True, "decision": "deny"}


def test_permission_ask_times_out_to_host_prompt(tmp_path):
    async def exercise():
        agent = BuddyAgent(
            tmp_path / "state.json",
            clock=lambda: 100.0,
            opencode_permission_timeout=0.05,
        )
        agent._ble = _CapturingBle()
        agent._ble_connected = True
        return await agent._handle_command(
            {"cmd": "permission_ask", "permission": _permission_payload()}
        )

    assert asyncio.run(exercise()) == {"ok": True, "decision": "ask"}


def test_permission_replied_notify_resolves_pending_prompt(tmp_path):
    async def exercise():
        agent = BuddyAgent(
            tmp_path / "state.json",
            clock=lambda: 100.0,
            opencode_permission_timeout=5.0,
        )
        agent._ble = _CapturingBle()
        agent._ble_connected = True
        task = asyncio.create_task(
            agent._handle_command(
                {"cmd": "permission_ask", "permission": _permission_payload()}
            )
        )
        for _ in range(100):
            if agent._opencode_permission_waiters:
                break
            await asyncio.sleep(0.01)
        await agent._handle_command(
            {
                "cmd": "notify",
                "event": {
                    "type": "permission.replied",
                    "properties": {
                        "sessionID": "ses-1",
                        "permissionID": "per-1",
                        "response": "once",
                    },
                },
            }
        )
        return await task

    assert asyncio.run(exercise()) == {"ok": True, "decision": "ask"}


def test_hello_builds_the_server_session_watcher(tmp_path):
    async def exercise():
        agent = BuddyAgent(tmp_path / "state.json", clock=lambda: 1.0)
        assert agent._session_watcher is None
        await agent._handle_command({"cmd": "hello", "serverUrl": "http://127.0.0.1:4096"})
        return agent

    agent = asyncio.run(exercise())

    assert agent._session_watcher is not None
    assert agent._server_client.base_url == "http://127.0.0.1:4096"


def test_injected_session_watcher_is_not_replaced_by_hello(tmp_path):
    sentinel = object()

    async def exercise():
        agent = BuddyAgent(tmp_path / "state.json", session_watcher=sentinel, clock=lambda: 1.0)
        await agent._handle_command({"cmd": "hello", "serverUrl": "http://127.0.0.1:4096"})
        return agent

    agent = asyncio.run(exercise())

    assert agent._session_watcher is sentinel


def test_device_always_decision_passes_through(tmp_path):
    async def exercise():
        agent = BuddyAgent(
            tmp_path / "state.json",
            clock=lambda: 100.0,
            opencode_permission_timeout=5.0,
        )
        agent._ble = _CapturingBle()
        agent._ble_connected = True
        task = asyncio.create_task(
            agent._handle_command(
                {"cmd": "permission_ask", "permission": _permission_payload()}
            )
        )
        for _ in range(100):
            if agent._opencode_permission_waiters:
                break
            await asyncio.sleep(0.01)
        await agent._handle_device_permission("per-1", "always")
        return await task

    assert asyncio.run(exercise()) == {"ok": True, "decision": "always"}
