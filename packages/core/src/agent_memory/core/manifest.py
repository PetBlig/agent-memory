"""Content-hash bookkeeping: what the index already knows, so a write reindexes only itself.

A file signature lets a sync skip reading a file the index already holds; change time is
in it because ordinary tools cannot restore it after an edit.
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import sqlite3


@dataclasses.dataclass(frozen=True)
class ManifestDelta:
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def touched(self) -> tuple[str, ...]:
        return self.added + self.changed

    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.removed)


def content_hash(text: str, prefix_length: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:prefix_length]


def file_signature(path: pathlib.Path) -> str | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return f"{stat.st_size}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"


class Manifest:
    def __init__(self, connection: sqlite3.Connection, prefix_length: int):
        self._connection = connection
        self._prefix_length = prefix_length

    def known(self) -> dict[str, str]:
        rows = self._connection.execute("SELECT path, content_hash FROM files").fetchall()
        return {row["path"]: row["content_hash"] for row in rows}

    def signed(self) -> dict[str, tuple[str, str]]:
        rows = self._connection.execute(
            "SELECT path, content_hash, signature FROM files WHERE signature IS NOT NULL"
        ).fetchall()
        return {row["path"]: (row["signature"], row["content_hash"]) for row in rows}

    def diff(self, present: dict[str, str]) -> ManifestDelta:
        known = self.known()
        added = tuple(sorted(path for path in present if path not in known))
        changed = tuple(
            sorted(
                path for path, digest in present.items() if path in known and known[path] != digest
            )
        )
        removed = tuple(sorted(path for path in known if path not in present))
        return ManifestDelta(added=added, changed=changed, removed=removed)

    def record(self, path: str, name: str, digest: str, at: str, signature: str | None) -> None:
        self._connection.execute(
            "INSERT INTO files(path, name, content_hash, indexed_at, signature) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET name=excluded.name, "
            "content_hash=excluded.content_hash, indexed_at=excluded.indexed_at, "
            "signature=excluded.signature",
            (path, name, digest, at, signature),
        )

    def sign(self, path: str, signature: str | None) -> None:
        self._connection.execute("UPDATE files SET signature = ? WHERE path = ?", (signature, path))

    def forget(self, path: str) -> None:
        self._connection.execute("DELETE FROM files WHERE path = ?", (path,))

    def hash_file(self, path: pathlib.Path) -> str:
        return content_hash(path.read_text(encoding="utf-8"), self._prefix_length)
