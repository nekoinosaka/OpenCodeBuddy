import json

import pytest

from opencode_buddy.opencode_server import OpenCodeServerClient, OpenCodeServerError


class _FakeFetch:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, body, timeout):
        self.calls.append((method, url, body, timeout))
        return self.responses.pop(0)


def test_list_sessions_parses_usage_and_time():
    payload = [
        {
            "id": "ses-1",
            "parentID": None,
            "directory": "/tmp/project",
            "title": "Build the thing",
            "agent": "build",
            "cost": 0.25,
            "tokens": {"input": 100, "output": 42},
            "time": {"created": 1_700_000_000_000, "updated": 1_700_000_010_000},
        },
        {"id": ""},
        "not-a-dict",
    ]
    fetch = _FakeFetch([(200, json.dumps(payload).encode())])
    client = OpenCodeServerClient("http://127.0.0.1:4096", fetch=fetch)

    sessions = client.list_sessions()

    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "ses-1"
    assert session.directory == "/tmp/project"
    assert session.output_tokens == 42
    assert session.input_tokens == 100
    assert session.cost == 0.25
    assert session.created_at == 1_700_000_000.0
    assert session.updated_at == 1_700_000_010.0
    assert fetch.calls[0][0] == "GET"
    assert fetch.calls[0][1] == "http://127.0.0.1:4096/session"


def test_list_sessions_raises_on_bad_status():
    fetch = _FakeFetch([(500, b"boom")])
    client = OpenCodeServerClient("http://127.0.0.1:4096", fetch=fetch)

    with pytest.raises(OpenCodeServerError):
        client.list_sessions()


def test_respond_permission_posts_allowed_response():
    fetch = _FakeFetch([(200, b"true")])
    client = OpenCodeServerClient("http://127.0.0.1:4096", fetch=fetch)

    assert client.respond_permission("per-1", "once") is True

    method, url, body, _ = fetch.calls[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:4096/permission/per-1/reply"
    assert json.loads(body) == {"reply": "once"}


def test_respond_permission_includes_directory_query():
    fetch = _FakeFetch([(200, b"true")])
    client = OpenCodeServerClient("http://127.0.0.1:4096", fetch=fetch)

    assert client.respond_permission("per-1", "always", directory="/tmp/proj") is True

    url = fetch.calls[0][1]
    assert url.startswith("http://127.0.0.1:4096/permission/per-1/reply?")
    assert "directory=%2Ftmp%2Fproj" in url
    assert json.loads(fetch.calls[0][2]) == {"reply": "always"}


def test_respond_permission_rejects_unknown_response():
    client = OpenCodeServerClient("http://127.0.0.1:4096", fetch=_FakeFetch([]))

    with pytest.raises(ValueError):
        client.respond_permission("per-1", "maybe")


def test_health_reports_reachability():
    client = OpenCodeServerClient(
        "http://127.0.0.1:4096", fetch=_FakeFetch([(200, b'{"healthy":true}')])
    )
    assert client.health() is True

    client = OpenCodeServerClient("http://127.0.0.1:4096", fetch=_FakeFetch([(404, b"")]))
    assert client.health() is False
