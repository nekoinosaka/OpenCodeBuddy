from opencode_buddy.opencode_server import OpenCodeSessionSummary
from opencode_buddy.opencode_session_watcher import OpenCodeSessionWatcher


class _FakeClient:
    def __init__(self, summaries):
        self.summaries = summaries

    def list_sessions(self):
        return list(self.summaries)


def _summary(session_id, *, parent_id=None, updated_at=100.0, output_tokens=10, title="Title"):
    return OpenCodeSessionSummary(
        session_id=session_id,
        parent_id=parent_id,
        directory="/tmp/project",
        title=title,
        agent="build",
        created_at=updated_at - 5,
        updated_at=updated_at,
        output_tokens=output_tokens,
        input_tokens=1,
        cost=0.0,
    )


def test_running_status_overrides_age():
    client = _FakeClient([_summary("ses-1", updated_at=100.0)])
    watcher = OpenCodeSessionWatcher(client, status_provider=lambda: {"ses-1": "busy"})

    records = watcher.poll(now=100.0)

    assert len(records) == 1
    assert records[0].state == "running"
    assert records[0].control_capability == "readonly"


def test_completed_and_recent_states_follow_windows():
    client = _FakeClient([_summary("ses-1", updated_at=100.0)])
    watcher = OpenCodeSessionWatcher(
        client,
        active_window_seconds=300.0,
        completed_window_seconds=120.0,
    )

    assert watcher.poll(now=150.0)[0].state == "completed"
    assert watcher.poll(now=400.0)[0].state == "recent"
    assert watcher.poll(now=1000.0) == []


def test_subagent_sessions_are_marked_and_tokens_projected():
    client = _FakeClient(
        [_summary("ses-sub", parent_id="ses-parent", output_tokens=99, title="Explore")]
    )
    watcher = OpenCodeSessionWatcher(client)

    record = watcher.poll(now=100.0)[0]

    assert record.source == "subagent"
    assert record.tokens_total == 99
    assert record.tokens_session == 99
    assert record.latest_message == "Explore"
    assert record.entries == ["Explore"]


def test_poll_failure_is_swallowed():
    class _Broken:
        def list_sessions(self):
            raise RuntimeError("server down")

    watcher = OpenCodeSessionWatcher(_Broken())

    assert watcher.poll(now=100.0) == []
