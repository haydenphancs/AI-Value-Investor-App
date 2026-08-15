"""A re-seed must never move a Money Move's publication date.

`seed_money_moves.py` derived `published_at` as `now - publishedDaysAgo` and wrote it
unconditionally through `upsert(rows, on_conflict="id")`. Because the offset is RELATIVE, every
run re-dated the whole catalog. All 16 authored offsets are 0-13 days, so the library
perpetually claimed to have been published within the last fortnight: re-seed in October and an
article the reader finished in August returns stamped "Today".

That was invisible while nothing selected the column. Commit `1abefef9` made `published_at` the
label rendered on every card AND the sort key behind the row's "Most Recent" heading, so the
jump became user-facing — a read article reappearing at the head of the row as if brand new.

These tests execute `_resolve_published_at` directly rather than scanning source, because the
whole defect lives in WHICH BRANCH runs, and a source scan cannot see that.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "scripts"))

seed = pytest.importorskip("seed_money_moves")


@pytest.fixture(autouse=True)
def _clean_registry():
    """The module-level cache is global; a leaked entry would make these tests pass in
    isolation and fail together (or worse, vice versa)."""
    seed._EXISTING_PUBLISHED_AT.clear()
    yield
    seed._EXISTING_PUBLISHED_AT.clear()


# --- branch 2: the fix itself -------------------------------------------------------------

def test_a_reseed_preserves_the_stored_date():
    """THE REGRESSION. An article already published keeps its original timestamp."""
    seed._EXISTING_PUBLISHED_AT["the-fall-of-enron"] = "2026-08-03T14:31:12+00:00"

    got = seed._resolve_published_at(
        {"slug": "the-fall-of-enron", "publishedDaysAgo": 12}, "the-fall-of-enron"
    )

    assert got == "2026-08-03T14:31:12+00:00"


def test_the_stored_date_wins_even_when_the_offset_would_give_a_different_day():
    """Guards against a "preserve only if they agree" reading, which would preserve nothing:
    the offset is relative, so it disagrees with the stored value on every day but one."""
    stored = "2025-01-01T00:00:00+00:00"
    seed._EXISTING_PUBLISHED_AT["visa-vs-mastercard"] = stored

    got = seed._resolve_published_at(
        {"slug": "visa-vs-mastercard", "publishedDaysAgo": 10}, "visa-vs-mastercard"
    )

    assert got == stored
    assert "2025-01-01" in got, "a 10-day offset must not have been able to overwrite 2025"


def test_reseeding_the_whole_catalog_moves_no_date():
    """The end-to-end shape of a real re-run: 13 existing articles, 3 new ones."""
    existing = {f"article-{i}": f"2026-08-{i:02d}T12:00:00+00:00" for i in range(1, 14)}
    seed._EXISTING_PUBLISHED_AT.update(existing)

    for slug, stored in existing.items():
        assert seed._resolve_published_at({"slug": slug, "publishedDaysAgo": 5}, slug) == stored

    for slug in ("nvidias-ai-dominance", "the-fall-of-sears", "amd-vs-intel-the-cpu-wars"):
        got = seed._resolve_published_at({"slug": slug, "publishedDaysAgo": 1}, slug)
        assert got not in existing.values()
        assert got.startswith(str(datetime.now(timezone.utc).year))


# --- branch 1: an explicit pinned date ----------------------------------------------------

def test_an_explicit_published_at_wins_over_the_offset():
    got = seed._resolve_published_at(
        {"slug": "x", "publishedAt": "2024-03-09T08:00:00Z", "publishedDaysAgo": 400}, "x"
    )
    assert got == "2024-03-09T08:00:00Z"


def test_an_explicit_published_at_wins_over_the_stored_value():
    """Priority order matters: pinning a corrected date in the JSON must be able to override
    a wrong one already in the database, or there is no way to fix a bad date."""
    seed._EXISTING_PUBLISHED_AT["x"] = "2026-08-01T00:00:00+00:00"

    got = seed._resolve_published_at(
        {"slug": "x", "publishedAt": "2024-03-09T08:00:00Z"}, "x"
    )

    assert got == "2024-03-09T08:00:00Z"


@pytest.mark.parametrize("junk", ["", "   ", None, 20260809, [], {}])
def test_an_unusable_explicit_value_falls_through_instead_of_being_written(junk):
    """An empty string or a non-string must not be written to a timestamptz column — it fails
    the insert for the WHOLE batch, not just that row."""
    seed._EXISTING_PUBLISHED_AT["x"] = "2026-08-01T00:00:00+00:00"

    got = seed._resolve_published_at(
        {"slug": "x", "publishedAt": junk, "publishedDaysAgo": 3}, "x"
    )

    assert got == "2026-08-01T00:00:00+00:00"


def test_an_explicit_value_is_trimmed():
    got = seed._resolve_published_at({"slug": "x", "publishedAt": " 2024-03-09T08:00:00Z\n"}, "x")
    assert got == "2024-03-09T08:00:00Z"


# --- branch 3: first publish --------------------------------------------------------------

def test_a_brand_new_slug_still_derives_from_the_offset():
    """The existing behaviour, preserved — a first publish has nothing to preserve."""
    before = datetime.now(timezone.utc) - timedelta(days=2)

    got = seed._resolve_published_at({"slug": "brand-new", "publishedDaysAgo": 2}, "brand-new")

    parsed = datetime.fromisoformat(got)
    assert abs((parsed - before).total_seconds()) < 120


def test_a_missing_offset_defaults_to_three_days():
    got = seed._resolve_published_at({"slug": "brand-new"}, "brand-new")
    parsed = datetime.fromisoformat(got)
    delta = (datetime.now(timezone.utc) - parsed).days
    assert delta == 3


def test_a_row_that_exists_with_a_null_date_derives_and_says_so(capsys):
    """The one branch that can still move a date a reader saw. It must not be silent —
    a Studio-inserted row with a NULL date is otherwise indistinguishable in the log from a
    normal preserve."""
    seed._EXISTING_PUBLISHED_AT["studio-row"] = None

    got = seed._resolve_published_at(
        {"slug": "studio-row", "publishedDaysAgo": 6}, "studio-row"
    )

    out = capsys.readouterr().out
    assert "studio-row" in out
    assert "no published_at" in out
    assert "publishedDaysAgo=6" in out
    assert datetime.fromisoformat(got) < datetime.now(timezone.utc)


def test_a_first_publish_is_not_warned_about(capsys):
    """Anti-vacuity partner: the warning must be specific to the NULL-row case, not printed
    for every article — a message on all 16 rows is noise nobody reads."""
    seed._resolve_published_at({"slug": "brand-new", "publishedDaysAgo": 1}, "brand-new")
    assert capsys.readouterr().out == ""


# --- the abort ----------------------------------------------------------------------------

def test_main_aborts_when_the_existing_dates_cannot_be_read(monkeypatch):
    """Degrading to "no existing dates" would take the derive branch for all 16 articles and
    silently re-date the entire catalog — the exact bug, triggered by a transient Supabase 520.
    It must abort instead."""

    class _Boom:
        def table(self, _name):
            raise RuntimeError("Cloudflare 520")

    monkeypatch.setattr(seed, "get_supabase", lambda: _Boom())

    with pytest.raises(SystemExit) as excinfo:
        seed.main()

    assert "re-date" in str(excinfo.value)
    assert "RuntimeError" in str(excinfo.value)
