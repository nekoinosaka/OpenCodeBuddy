from __future__ import annotations

import errno
import os
import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_POSIX = os.name == "posix"


def user_name(fallback: str = "OpenCode") -> str:
    """Best-effort current user name across macOS, Linux and Windows."""

    for key in ("USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return fallback


def supports_dir_fd() -> bool:
    """True when the platform can open/stat relative to a directory fd."""

    return (
        os.open in getattr(os, "supports_dir_fd", set())
        and os.stat in getattr(os, "supports_dir_fd", set())
    )


def fchmod(descriptor: int, mode: int) -> None:
    """Set permissions on an open descriptor, where the platform supports it."""

    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, mode)
        return
    # Windows has no fchmod; the runtime tree already lives under the user
    # profile, and permissions there are ACL-based rather than POSIX mode bits.


def lock_file(descriptor: int, *, blocking: bool) -> bool:
    """Take an exclusive lock on an open descriptor.

    Returns ``True`` when the lock is held and ``False`` when a non-blocking
    attempt found the lock held elsewhere. Other failures raise ``OSError``.
    """

    if IS_WINDOWS:
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(descriptor, mode, 1)
        except OSError as exc:
            if not blocking and exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        return True

    import fcntl

    operation = fcntl.LOCK_EX
    if not blocking:
        operation |= fcntl.LOCK_NB
    try:
        fcntl.flock(descriptor, operation)
    except OSError as exc:
        if not blocking and exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise
    return True


def unlock_file(descriptor: int) -> None:
    """Release a lock previously taken with :func:`lock_file`."""

    if IS_WINDOWS:
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)
