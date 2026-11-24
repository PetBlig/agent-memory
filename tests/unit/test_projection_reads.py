"""Projection cost follows the changes, not the store size.

Behind an on-access scanner every file open costs milliseconds, so a projection that reads
the whole store after each write makes a large store unusable. A changed file must still
never be missed: the signature carries change time, which ordinary tools cannot restore.
"""

import collections
import os
import pathlib
import sqlite3

import pytest
from agent_memory.core.manage import Manage
from agent_memory.core.recall import Recall

NOTES = 12


def _fill(store, count=NOTES):
    store.record_many(
        [
            {"abstract": f"Deploy note {index} about queue drains", "type": "fact"}
            for index in range(count)
        ]
    )


@pytest.fixture
def opened(monkeypatch):
    paths: list[pathlib.Path] = []
    read_text = pathlib.Path.read_text
    read_bytes = pathlib.Path.read_bytes

    def tracked_text(self, *args, **kwargs):
        paths.append(self)
        return read_text(self, *args, **kwargs)

    def tracked_bytes(self, *args, **kwargs):
        paths.append(self)
        return read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", tracked_text)
    monkeypatch.setattr(pathlib.Path, "read_bytes", tracked_bytes)
    return paths


def _memory_reads(store, paths):
    truth = set(store.layout.truth_files())
    return [path for path in paths if path in truth]


def _fresh(store):
    from agent_memory.core.store import Store

    return Store(store.root, config=store.config, clock=store.clock, agent="test-agent")


def test_a_projection_after_one_write_reads_only_that_file(store, opened):
    _fill(store)
    reopened = _fresh(store)
    opened.clear()
    written = reopened.record(abstract="Drain window is ninety seconds", type="fact")
    assert set(_memory_reads(reopened, opened)) == {written.path}


def test_projection_reads_do_not_grow_with_the_store(tmp_path, config, clock, opened):
    from agent_memory.core.store import Store

    def reads_for(count):
        store = Store(tmp_path / f"store-{count}", config=config, clock=clock, agent="t")
        store.init()
        _fill(store, count)
        reopened = _fresh(store)
        opened.clear()
        reopened.record(abstract="Drain window is ninety seconds", type="fact")
        return len(_memory_reads(reopened, opened))

    assert reads_for(NOTES) == reads_for(NOTES * 3)


def test_an_edit_that_restores_size_and_modification_time_is_still_detected(store):
    _fill(store)
    target = store.records()[0]
    before = target.path.stat()
    text = target.path.read_text(encoding="utf-8")
    edited = text.replace("about queue", "about QUEUE")
    assert len(edited) == len(text)
    target.path.write_text(edited, encoding="utf-8")
    os.utime(target.path, ns=(before.st_atime_ns, before.st_mtime_ns))
    report = _fresh(store).sync_index()
    assert str(target.path.relative_to(store.root)) in report.reindexed
    assert "QUEUE" in _fresh(store).find(target.name).abstract


def test_an_index_from_before_signatures_still_syncs_every_change(store):
    _fill(store)
    with sqlite3.connect(store.layout.index_db) as connection:
        connection.execute("ALTER TABLE files DROP COLUMN signature")
    target = store.records()[0]
    target.path.write_text(
        target.to_text().replace("about queue", "about harbour"), encoding="utf-8"
    )
    report = _fresh(store).sync_index()
    assert report.reindexed == (str(target.path.relative_to(store.root)),)
    assert target.name in {hit.name for hit in Recall(store).recall("harbour")}


def test_listing_records_twice_reads_each_unchanged_file_once(store, opened):
    _fill(store)
    reopened = _fresh(store)
    opened.clear()
    first = reopened.records()
    second = reopened.records()
    assert [record.name for record in first] == [record.name for record in second]
    reads = _memory_reads(reopened, opened)
    assert len(reads) == len(set(reads))


def test_a_listed_record_reflects_an_edit_made_outside_store(store):
    _fill(store)
    reopened = _fresh(store)
    target = reopened.records()[0]
    target.path.write_text(
        target.to_text().replace("about queue", "about harbour"), encoding="utf-8"
    )
    assert "harbour" in {record.name: record for record in reopened.records()}[target.name].abstract


def test_a_listed_record_mutated_by_its_caller_does_not_leak_into_the_next_listing(store):
    _fill(store)
    reopened = _fresh(store)
    target = reopened.records()[0]
    original = target.abstract
    target.abstract = "mutated in memory only"
    assert {record.name: record for record in reopened.records()}[target.name].abstract == original


def test_sleep_rereads_a_file_only_after_rewriting_it(store, opened, monkeypatch):
    _fill(store, NOTES * 2)
    reopened = _fresh(store)
    rewrites: collections.Counter[pathlib.Path] = collections.Counter()
    replace = os.replace

    def tracked_replace(source, target, *args, **kwargs):
        rewrites[pathlib.Path(target)] += 1
        return replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", tracked_replace)
    opened.clear()
    Manage(reopened).sleep()
    reads = collections.Counter(_memory_reads(reopened, opened))
    assert reads
    assert all(count <= 1 + rewrites[path] for path, count in reads.items())
