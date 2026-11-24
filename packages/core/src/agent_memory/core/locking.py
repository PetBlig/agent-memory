"""Pipeline-level serialization. Every writer passes through one advisory lock; each session's
distillation also holds a claim, so one backlog is never distilled twice at once."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import time
from collections.abc import Iterator

from .errors import LockTimeoutError
from .paths import StoreLayout

CLAIM_SUFFIX = ".claim"


@contextlib.contextmanager
def store_lock(layout: StoreLayout) -> Iterator[None]:
    layout.state_dir.mkdir(parents=True, exist_ok=True)
    timeout = layout.config.storage.lock_timeout_seconds
    poll = layout.config.storage.lock_poll_seconds
    deadline = time.monotonic() + timeout
    with layout.lock_file.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(f"store lock busy for {timeout}s") from None
                time.sleep(poll)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def session_claim(layout: StoreLayout, session: str) -> Iterator[bool]:
    folder = layout.state_dir / layout.config.write.claim_dirname
    folder.mkdir(parents=True, exist_ok=True)
    claim = folder / (hashlib.sha256(session.encode("utf-8")).hexdigest() + CLAIM_SUFFIX)
    with claim.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
