from opencode_buddy.events import (
    AgentOutput,
    ApprovalRequest,
    ApprovalRequestResolved,
    QuestionResolved,
    TokenUsage,
    TurnState,
)
from opencode_buddy.opencode_events import OpenCodeEventAdapter


def test_session_status_busy_is_an_active_turn():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {
            "type": "session.status",
            "properties": {"sessionID": "ses-1", "status": {"type": "busy"}},
        }
    )
    assert events == [TurnState(thread_id="ses-1", turn_id="busy", active=True)]


def test_session_idle_completes_the_turn():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {"type": "session.idle", "properties": {"sessionID": "ses-1"}}
    )
    assert events == [
        TurnState(thread_id="ses-1", turn_id="", active=False, status="completed")
    ]


def test_session_meta_records_directory():
    adapter = OpenCodeEventAdapter()
    adapter.handle(
        {
            "type": "session.updated",
            "properties": {
                "info": {"id": "ses-1", "directory": "/tmp/project", "title": "demo"}
            },
        }
    )
    assert adapter.directory("ses-1") == "/tmp/project"


def test_permission_updated_maps_to_approval_request():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {
            "type": "permission.updated",
            "properties": {
                "id": "per-1",
                "sessionID": "ses-1",
                "type": "bash",
                "title": "rm -rf /tmp/foo",
                "pattern": "rm *",
                "callID": "call-1",
            },
        }
    )
    assert events == [
        ApprovalRequest(
            thread_id="ses-1",
            turn_id="call-1",
            request_id="per-1",
            command="rm -rf /tmp/foo",
            cwd="",
            reason="rm -rf /tmp/foo",
            tool="bash",
            hint="rm -rf /tmp/foo",
        )
    ]


def test_permission_replied_resolves_the_request():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {
            "type": "permission.replied",
            "properties": {
                "sessionID": "ses-1",
                "permissionID": "per-1",
                "response": "once",
            },
        }
    )
    assert events == [ApprovalRequestResolved(request_id="per-1")]


def test_assistant_token_usage_is_cumulative_across_messages():
    adapter = OpenCodeEventAdapter()
    first = adapter.handle(
        {
            "type": "message.updated",
            "properties": {
                "info": {
                    "id": "msg-1",
                    "sessionID": "ses-1",
                    "role": "assistant",
                    "tokens": {"output": 10},
                }
            },
        }
    )
    second = adapter.handle(
        {
            "type": "message.updated",
            "properties": {
                "info": {
                    "id": "msg-2",
                    "sessionID": "ses-1",
                    "role": "assistant",
                    "tokens": {"output": 5},
                }
            },
        }
    )
    assert first == [
        TokenUsage(
            thread_id="ses-1",
            total_tokens=10,
            tokens_today=10,
            heartbeat_total_tokens=10,
        )
    ]
    assert second == [
        TokenUsage(
            thread_id="ses-1",
            total_tokens=15,
            tokens_today=15,
            heartbeat_total_tokens=15,
        )
    ]


def test_text_part_becomes_agent_output():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "type": "text",
                    "sessionID": "ses-1",
                    "messageID": "msg-1",
                    "text": "Reading the config file",
                }
            },
        }
    )
    assert events == [AgentOutput(thread_id="ses-1", text="Reading the config file")]


def test_synthetic_text_part_is_ignored():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "type": "text",
                    "sessionID": "ses-1",
                    "messageID": "msg-1",
                    "text": "hidden",
                    "synthetic": True,
                }
            },
        }
    )
    assert events == []


def test_unknown_event_is_ignored():
    adapter = OpenCodeEventAdapter()
    assert adapter.handle({"type": "file.edited", "properties": {}}) == []
    assert adapter.handle("not-an-event") == []


def test_permission_asked_maps_to_approval_request():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {
            "type": "permission.asked",
            "properties": {
                "id": "per-2",
                "sessionID": "ses-2",
                "type": "edit",
                "title": "src/main.ts",
                "pattern": "**/*.ts",
                "callID": "call-2",
            },
        }
    )
    assert events == [
        ApprovalRequest(
            thread_id="ses-2",
            turn_id="call-2",
            request_id="per-2",
            command="src/main.ts",
            cwd="",
            reason="src/main.ts",
            tool="edit",
            hint="src/main.ts",
        )
    ]


def test_question_replied_resolves_the_request():
    adapter = OpenCodeEventAdapter()
    assert adapter.handle(
        {
            "type": "question.replied",
            "properties": {"sessionID": "ses-1", "requestID": "que-1", "answers": [["A"]]},
        }
    ) == [QuestionResolved(request_id="que-1")]


def test_question_rejected_resolves_the_request():
    adapter = OpenCodeEventAdapter()
    assert adapter.handle(
        {
            "type": "question.rejected",
            "properties": {"sessionID": "ses-1", "requestID": "que-2"},
        }
    ) == [QuestionResolved(request_id="que-2")]


def test_permission_hint_prefers_metadata_command():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {
            "type": "permission.asked",
            "properties": {
                "id": "p1",
                "sessionID": "s",
                "type": "bash",
                "title": "",
                "metadata": {"command": "rm -rf /tmp/x"},
            },
        }
    )
    assert events[0].hint == "rm -rf /tmp/x"


def test_permission_hint_falls_back_to_title_then_pattern_list():
    adapter = OpenCodeEventAdapter()
    with_title = adapter.handle(
        {
            "type": "permission.asked",
            "properties": {
                "id": "p2",
                "sessionID": "s",
                "type": "external_directory",
                "title": "Access external directory",
                "pattern": ["/a/*", "/b/*"],
            },
        }
    )
    assert with_title[0].hint == "Access external directory"

    pattern_only = adapter.handle(
        {
            "type": "permission.asked",
            "properties": {
                "id": "p3",
                "sessionID": "s",
                "type": "external_directory",
                "pattern": ["/a/*", "/b/*"],
            },
        }
    )
    assert pattern_only[0].hint == "/a/*, /b/*"


def test_permission_asked_real_shape_uses_permission_and_metadata():
    adapter = OpenCodeEventAdapter()
    events = adapter.handle(
        {
            "type": "permission.asked",
            "properties": {
                "id": "per_1",
                "sessionID": "ses_1",
                "permission": "external_directory",
                "patterns": ["/System/Library/CoreServices/*"],
                "metadata": {
                    "command": "cat /System/Library/CoreServices/SystemVersion.plist",
                    "patterns": ["/System/Library/CoreServices/*"],
                },
                "always": ["/System/Library/CoreServices/*"],
                "tool": {"messageID": "msg_1", "callID": "call_1"},
            },
        }
    )
    assert events[0].tool == "external_directory"
    assert events[0].turn_id == "call_1"
    assert events[0].hint == "cat /System/Library/CoreServices/SystemVersion.plist"
