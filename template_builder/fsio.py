"""Crash-safe file primitives — the one place persistence mechanics live.

Every module that stores state (templates, matters, journals, skills) writes
through here, so the whole system shares two guarantees:

- `atomic_write_text`: readers never observe a partial file. The text goes to
  a sibling temp file, is fsynced, then renamed over the target — a crash or
  power failure leaves either the old file or the new one, never a torn mix.
- `locked`: read-modify-write sections on the same path are serialised across
  processes (web server, CLI, two requests) with an advisory lock, so
  concurrent edits queue instead of silently overwriting each other.
"""

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def atomic_write_text(path: str | Path, text: str) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tb-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


@contextmanager
def locked(path: str | Path):
    """Hold an exclusive advisory lock for a read-modify-write of `path`.

    The lock lives on a sibling `<path>.lock` file (never the data file, so
    atomic renames don't disturb it). Blocks until the current holder exits.
    Not re-entrant: a function that takes the lock must not call another that
    takes the same lock — keep locking at entry points only.
    """
    lock_path = Path(f"{path}.lock")
    with open(lock_path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
