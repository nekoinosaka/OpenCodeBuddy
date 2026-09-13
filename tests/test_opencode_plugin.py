from opencode_buddy import setup_flow


def test_install_opencode_plugin_writes_the_bridge(tmp_path):
    destination = tmp_path / "plugins" / "code-buddy.js"

    result = setup_flow.install_opencode_plugin(destination)

    assert result == destination
    text = destination.read_text(encoding="utf-8")
    assert "CodeBuddyBridge" in text
    assert "permission.asked" in text
    assert "permission_ask" in text
    assert "permission?.reply" in text
    assert "serverUrl" in text
    assert '"always"' in text
    assert '"always"' in text


def test_install_opencode_plugin_is_idempotent(tmp_path):
    destination = tmp_path / "plugins" / "code-buddy.js"

    setup_flow.install_opencode_plugin(destination)
    first = destination.read_text(encoding="utf-8")
    setup_flow.install_opencode_plugin(destination)

    assert destination.read_text(encoding="utf-8") == first


def test_opencode_plugin_is_current_tracks_the_bundled_copy(tmp_path):
    destination = tmp_path / "plugins" / "code-buddy.js"

    assert setup_flow.opencode_plugin_is_current(destination) is False

    setup_flow.install_opencode_plugin(destination)
    assert setup_flow.opencode_plugin_is_current(destination) is True

    destination.write_text("// stale\n", encoding="utf-8")
    assert setup_flow.opencode_plugin_is_current(destination) is False
