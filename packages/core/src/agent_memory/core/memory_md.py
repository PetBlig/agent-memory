"""The always-injected root index. A hard budget, enforced by dropping the lightest lines.

Rendered from the search index right after a sync, so writing it reads no truth file.
"""

from __future__ import annotations

import dataclasses
import sqlite3

from .config import Config
from .paths import StoreLayout


@dataclasses.dataclass(frozen=True)
class Entry:
    name: str
    location: str
    abstract: str
    weight: float
    updated: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Entry:
        return cls(
            name=str(row["name"]),
            location=str(row["path"]),
            abstract=str(row["abstract"]),
            weight=float(row["weight"]),
            updated=str(row["updated"]),
        )


def render(entries: list[Entry], config: Config) -> str:
    eligible = [entry for entry in entries if entry.weight >= config.recall.memory_md_weight_floor]
    eligible.sort(key=lambda entry: (-entry.weight, entry.updated, entry.name))

    header = config.memory_md.header + "\n\n"
    lines: list[str] = []
    used = len(header.encode("utf-8"))
    for entry in eligible:
        if len(lines) >= config.memory_md.max_lines:
            break
        line = f"- [{entry.name}]({entry.location}) — {entry.abstract}\n"
        cost = len(line.encode("utf-8"))
        if used + cost > config.memory_md.budget_bytes:
            break
        lines.append(line)
        used += cost
    return header + "".join(lines)


def write(layout: StoreLayout, entries: list[Entry]) -> str:
    text = render(entries, layout.config)
    layout.memory_index.write_text(text, encoding="utf-8")
    return text
