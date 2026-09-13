import subprocess
import sys

import pytest

from opencode_buddy.native_helper_build import (
    build_bundled_native_helper,
    cleanup_bundled_native_helper_build,
)


@pytest.mark.skipif(sys.platform != "darwin", reason="native helper is macOS-only")
def test_bundled_native_helper_targets_the_supported_macos_floor():
    app = build_bundled_native_helper()
    try:
        executable = app / "Contents" / "MacOS" / "CodeBuddyBLEHelper"
        load_commands = subprocess.run(
            ["otool", "-l", str(executable)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    finally:
        cleanup_bundled_native_helper_build(app)

    assert "minos 13.0" in load_commands
