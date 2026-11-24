"""One session, one distillation at a time.

Each boundary launches a distillation. Two runs over one backlog ask the reasoner twice and
queue on the writer lock; on a slow store they pile up long enough to time out a sleep. The
claim must never cost knowledge: material archived while a run holds the claim is distilled
by that run.
"""

import json

import pytest
from agent_memory.adapters import capture as capture_module
from agent_memory.cli.main import main
from agent_memory.core import locking
from agent_memory.core.watermark import Watermark

LINES = [
    "user: we moved the deploy to Fridays",
    "assistant: noted",
]
LATER = [
    "user: and the queue timeout is 30 seconds now",
    "assistant: noted",
]
REPLY = json.dumps(
    {
        "type": "fact",
        "fields": {"project": "deploy", "subject": "deploy day"},
        "abstract": "Deploys happen on Fridays",
        "provenance": ["0"],
    }
)


def _executor(monkeypatch, reply):
    monkeypatch.setattr("agent_memory.executor.distiller.distiller", lambda config: reply)


def _distill(store, capsys, session="hooked"):
    code = main(["--store", str(store.root), "--json", "distill", "--session", session])
    assert code == 0
    return json.loads(capsys.readouterr().out)["distilled"]


def test_a_claimed_session_is_skipped_without_asking_the_reasoner(store, monkeypatch, capsys):
    capture_module.capture(store, "hooked", LINES)
    asked = []
    _executor(monkeypatch, lambda prompt: asked.append(prompt) or REPLY)
    with locking.session_claim(store.layout, "hooked") as claimed:
        assert claimed
        reports = _distill(store, capsys)
    assert asked == []
    assert [report["session"] for report in reports] == ["hooked"]
    assert reports[0]["skipped"]
    assert Watermark(store.layout).read("hooked").distilled == 0


def test_material_archived_during_a_run_is_distilled_by_that_run(store, monkeypatch, capsys):
    capture_module.capture(store, "hooked", LINES)
    boundary_fired = []

    def reasoner(prompt):
        if not boundary_fired:
            capture_module.capture(store, "hooked", LINES + LATER)
            boundary_fired.append(1)
        return REPLY

    _executor(monkeypatch, reasoner)
    _distill(store, capsys)
    assert Watermark(store.layout).read("hooked").distilled == len(LINES + LATER)


def test_catch_up_stops_after_the_configured_rounds(store, monkeypatch, capsys):
    store.config.write.distill_catch_up_rounds = 1
    store.config.save(store.root)
    capture_module.capture(store, "hooked", LINES)
    transcript = list(LINES)

    def reasoner(prompt):
        transcript.extend(LATER)
        capture_module.capture(store, "hooked", list(transcript))
        return REPLY

    _executor(monkeypatch, reasoner)
    reports = _distill(store, capsys)
    assert len(reports) == 1 + store.config.write.distill_catch_up_rounds
    assert Watermark(store.layout).read("hooked").distilled < len(transcript)


def test_claims_of_different_sessions_do_not_block_each_other(store, monkeypatch, capsys):
    capture_module.capture(store, "other", LINES)
    _executor(monkeypatch, lambda prompt: REPLY)
    with locking.session_claim(store.layout, "hooked") as claimed:
        assert claimed
        reports = _distill(store, capsys, session="other")
    assert not reports[0].get("skipped")
    assert Watermark(store.layout).read("other").distilled == len(LINES)


def test_a_failed_run_releases_its_claim(store, monkeypatch):
    capture_module.capture(store, "hooked", LINES)

    def reasoner(prompt):
        raise RuntimeError("reasoner crashed")

    _executor(monkeypatch, reasoner)
    with pytest.raises(RuntimeError):
        main(["--store", str(store.root), "--json", "distill", "--session", "hooked"])
    with locking.session_claim(store.layout, "hooked") as claimed:
        assert claimed


def test_a_session_name_cannot_claim_outside_the_claim_directory(store):
    with locking.session_claim(store.layout, "../../escape") as claimed:
        assert claimed
    assert not (store.root.parent / "escape").exists()
    assert not any(store.root.parent.glob("escape*"))
