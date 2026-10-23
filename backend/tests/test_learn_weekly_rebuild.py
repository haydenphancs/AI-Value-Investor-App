"""`scripts/learn_weekly_rebuild.py` — the owner's manual reopen of a closed weekly piece. It writes
production, so the guards matter: only REBUILDABLE statuses, an expired piece needs a new hold,
nothing is written without `--yes`, and a lost compare-and-swap writes nothing. The ledger is
stubbed; nothing here reaches Supabase."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.learn_weekly import contracts as C, ledger

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "learn_weekly_rebuild.py"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("learn_weekly_rebuild_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load()


def piece(status=C.FAILED, **extra):
    row = {"id": "11111111-1111-1111-1111-111111111111", "period_key": "2026-W43:deep_dive",
           "status": status, "review_round": 1, "last_error": "writer_failures 3/3",
           "topic": {"version": 1, "candidates": [{"key": "a"}, {"key": "b"}], "chosen_index": 0}}
    row.update(extra)
    return row


@pytest.mark.parametrize("status", sorted(C.REBUILDABLE_STATUSES - {C.EXPIRED}))
def test_a_rebuildable_piece_goes_back_with_the_next_round(status):
    f = R.plan_rebuild(piece(status), next_topic=False, publish_at=None, now=NOW, hold_hours=48)
    assert f["review_round"] == 2 and f["lease_until"] is None and f["progress"] == {}
    assert f["last_error"] == "owner_rebuild" and "topic" not in f and "hold_until" not in f
    assert f["writer_failures"] == 0 and f["content_rejections"] == 0, "the owner's override resets the caps"
    # The REAL ledger validator accepts the fields (no mocked transition can hide a type error).
    ledger._validate_piece_fields(f)


@pytest.mark.parametrize("status", [C.PLANNED, C.BUILDING, C.IN_REVIEW, C.APPROVED, C.PUBLISHING,
                                    C.PUBLISHED, C.REHEARSED, C.UNPUBLISHED])
def test_a_live_or_in_flight_piece_is_refused(status):
    with pytest.raises(SystemExit):
        R.plan_rebuild(piece(status), next_topic=False, publish_at=None, now=NOW, hold_hours=48)


def test_an_expired_piece_needs_a_new_future_hold():
    with pytest.raises(SystemExit, match="needs --publish-at"):
        R.plan_rebuild(piece(C.EXPIRED), next_topic=False, publish_at=None, now=NOW, hold_hours=48)
    with pytest.raises(SystemExit, match="in the past"):
        R.plan_rebuild(piece(C.EXPIRED), next_topic=False, publish_at=NOW - timedelta(minutes=1),
                       now=NOW, hold_hours=48)
    at = NOW + timedelta(hours=10)
    f = R.plan_rebuild(piece(C.EXPIRED), next_topic=False, publish_at=at, now=NOW, hold_hours=48)
    assert f["scheduled_publish_at"] == at and f["hold_until"] == at + timedelta(hours=48)
    normalised = ledger._validate_piece_fields(f)       # the check that once crashed every run
    assert normalised["scheduled_publish_at"].startswith("2026-10-24T22:00:00")


def test_next_topic_moves_to_the_runner_up_and_stops_at_the_last():
    f = R.plan_rebuild(piece(), next_topic=True, publish_at=None, now=NOW, hold_hours=48)
    assert f["topic"]["chosen_index"] == 1 and f["topic"]["owner_pick"] == 1
    assert f["topic"]["auto_picked"] is False
    last = piece(topic={"version": 1, "candidates": [{"key": "a"}, {"key": "b"}], "chosen_index": 1})
    with pytest.raises(SystemExit, match="no candidate after"):
        R.plan_rebuild(last, next_topic=True, publish_at=None, now=NOW, hold_hours=48)


@pytest.mark.parametrize("junk", [None, True, "0", -5])
def test_junk_review_round_or_chosen_index_never_crashes(junk):
    f = R.plan_rebuild(piece(review_round=None, topic={"version": 1, "candidates": [{"k": 1}],
                                                       "chosen_index": junk}),
                       next_topic=True, publish_at=None, now=NOW, hold_hours=48)
    assert f["review_round"] == 1 and f["topic"]["chosen_index"] == 0


def test_a_naive_publish_at_is_refused():
    with pytest.raises(SystemExit, match="timezone offset"):
        R._parse_when("2026-10-24T13:00:00")


def test_main_writes_nothing_without_yes_and_reports_a_lost_cas(monkeypatch, capsys):
    calls = {"cancel": 0, "transition": 0}
    monkeypatch.setattr(ledger, "get_piece_sync", lambda key: piece())
    monkeypatch.setattr(ledger, "cancel_open_jobs_sync",
                        lambda pid, reason="": calls.__setitem__("cancel", calls["cancel"] + 1) or [])

    def lost(pid, *, from_statuses, to_status, fields=None, **_):
        calls["transition"] += 1
        assert set(from_statuses) == set(C.REBUILDABLE_STATUSES) and to_status == C.PLANNED
        return None

    monkeypatch.setattr(ledger, "transition_sync", lost)
    assert R.main(["--period", "2026-W43:deep_dive"]) == 0
    assert calls == {"cancel": 0, "transition": 0} and "nothing written" in capsys.readouterr().out
    assert R.main(["--period", "2026-W43:deep_dive", "--yes"]) == 1
    # A lost swap writes NOTHING: the open jobs are cancelled only after the swap succeeded.
    assert calls == {"cancel": 0, "transition": 1} and "lost compare-and-swap" in capsys.readouterr().out


def test_main_with_publish_at_runs_the_real_validator_and_cancels_after_the_swap(monkeypatch, capsys):
    order = []
    monkeypatch.setattr(ledger, "get_piece_sync", lambda key: piece(C.EXPIRED))
    monkeypatch.setattr(ledger, "cancel_open_jobs_sync", lambda pid, reason="": order.append("cancel") or [])

    def swap(pid, *, from_statuses, to_status, fields=None, **_):
        ledger._validate_piece_fields(fields)          # the real check, unmocked
        order.append("transition")
        return {**piece(C.EXPIRED), "status": C.PLANNED}

    monkeypatch.setattr(ledger, "transition_sync", swap)
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    assert R.main(["--period", "2026-W43:deep_dive", "--publish-at", future, "--yes"]) == 0
    assert order == ["transition", "cancel"] and "reopened: planned" in capsys.readouterr().out


def test_main_with_no_row(monkeypatch, capsys):
    monkeypatch.setattr(ledger, "get_piece_sync", lambda key: None)
    assert R.main(["--period", "2026-W43:deep_dive", "--yes"]) == 1
    assert "no learn_weekly_pieces row" in capsys.readouterr().out
