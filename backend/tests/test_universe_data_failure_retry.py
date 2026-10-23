"""A failed universe download must be RETRIED, not remembered until restart.

`load_universe` memoises its answer in a module dict with no TTL — right for a 400 KB file
that changes quarterly. But both failure branches (download returned None; payload
unreadable) wrote `_cache[filename] = []` into that same dict, so ONE Storage blip at boot
emptied industry benchmarks / moat / dossier / competitor peers for the life of the
process. `[]` there is byte-identical to "this file has no industries" — the cached-failure
shape the rebuild has now hit three times.

Failures are memoised separately, for `_FAILURE_RETRY_SECONDS`, and cleared on the next
success; the successful-path memo is untouched.

Hermetic: the downloader is stubbed; the on-disk directory is a tmpdir.
"""
from __future__ import annotations

import json

import pytest

from app.services import universe_data as ud


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    ud.reset_cache_for_tests()
    monkeypatch.setenv(ud._ENV_DIR, str(tmp_path))
    yield
    ud.reset_cache_for_tests()


def _payload():
    return {
        "ticker_count": 1,
        "industries": [{"industry": "Software", "sector": "Technology",
                        "tickers": ["AAPL"], "market_caps": {"AAPL": 1.0}}],
    }


class _Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(ud.time, "monotonic", c)
    return c


def _flaky_downloader(outcomes):
    """Yields each outcome in order (None = failure, bytes = success), counting calls."""
    calls = {"n": 0}

    def _dl(_f):
        calls["n"] += 1
        out = outcomes[min(calls["n"], len(outcomes)) - 1]
        return out

    return _dl, calls


def test_a_download_failure_is_retried_after_the_window(monkeypatch, clock):
    dl, calls = _flaky_downloader([None, json.dumps(_payload()).encode()])
    monkeypatch.setattr(ud, "_download_from_storage", dl)

    assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == []
    assert calls["n"] == 1

    clock.now += ud._FAILURE_RETRY_SECONDS + 1
    out = ud.load_universe(ud.INDUSTRY_UNIVERSE)
    assert [i["industry"] for i in out] == ["Software"], "the boot-time failure was permanent"
    assert calls["n"] == 2


def test_within_the_window_a_failure_is_not_refetched_on_every_request(monkeypatch, clock):
    """Herd guard: four services read this on request paths; a Storage outage must not
    become one download attempt per request."""
    dl, calls = _flaky_downloader([None, None, None])
    monkeypatch.setattr(ud, "_download_from_storage", dl)
    for _ in range(5):
        assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == []
    assert calls["n"] == 1


def test_a_success_clears_the_failure_memo_and_is_memoised_forever(monkeypatch, clock):
    dl, calls = _flaky_downloader([None, json.dumps(_payload()).encode(), None])
    monkeypatch.setattr(ud, "_download_from_storage", dl)
    ud.load_universe(ud.INDUSTRY_UNIVERSE)                     # fail
    clock.now += ud._FAILURE_RETRY_SECONDS + 1
    assert ud.load_universe(ud.INDUSTRY_UNIVERSE)               # success
    clock.now += ud._FAILURE_RETRY_SECONDS * 10
    assert ud.load_universe(ud.INDUSTRY_UNIVERSE)               # still served from memory
    assert calls["n"] == 2


def test_an_unreadable_payload_is_also_retried(monkeypatch, clock, tmp_path):
    """The second failure branch: the file is present but malformed. A later redeploy of
    the object must be picked up without a restart."""
    bad = tmp_path / ud.INDUSTRY_UNIVERSE
    bad.write_text("{not json", encoding="utf-8")
    assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == []
    bad.write_text(json.dumps(_payload()), encoding="utf-8")
    assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == [], "inside the window: still degraded"
    clock.now += ud._FAILURE_RETRY_SECONDS + 1
    assert [i["industry"] for i in ud.load_universe(ud.INDUSTRY_UNIVERSE)] == ["Software"]


def test_reset_clears_the_failure_memo_too(monkeypatch, clock):
    dl, calls = _flaky_downloader([None, json.dumps(_payload()).encode()])
    monkeypatch.setattr(ud, "_download_from_storage", dl)
    ud.load_universe(ud.INDUSTRY_UNIVERSE)
    ud.reset_cache_for_tests()
    assert ud.load_universe(ud.INDUSTRY_UNIVERSE), "reset must forget the failure as well"


# ── the peer memo downstream must not outlive the universe's own failure window ──
#
# `_industry_universe_peers` memoises per industry with NO TTL. During a Storage blip
# `load_universe` answers `[]`, and memoising that made the industry peerless for the
# life of the process even after `universe_data` recovered — the retry window above was
# defeated one layer up. An empty answer is never memoised now.

def test_an_empty_peer_answer_is_not_memoised(monkeypatch):
    from app.services.agents import ticker_report_data_collector as col

    col._INDUSTRY_PEERS_CACHE.clear()
    answers = [[], [{"industry": "Semiconductors", "market_caps": {"NVDA": 3e12, "AMD": 2e11}}]]
    monkeypatch.setattr(col, "load_universe", lambda _name: answers.pop(0))
    assert col._industry_universe_peers("Semiconductors", set()) == []
    assert "Semiconductors" not in col._INDUSTRY_PEERS_CACHE, "an outage was memoised as 'no peers'"
    assert col._industry_universe_peers("Semiconductors", set()) == ["NVDA", "AMD"]
    assert col._INDUSTRY_PEERS_CACHE["Semiconductors"] == ["NVDA", "AMD"]
    col._INDUSTRY_PEERS_CACHE.clear()


# ── A transient Storage failure is retried once (2026-10-23) ────────────────────────
#
# Production boot: benchmark_universe.json downloaded, then the industry_universe.json GET hung
# for storage3's 20 s timeout and raised ReadTimeout while the file sat intact in the bucket.
# The single attempt emptied competitor peers for five minutes and opened three Sentry issues.

import logging

import httpx


class _FakeBucket:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def download(self, _name):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


def _storage(monkeypatch, outcomes) -> _FakeBucket:
    import app.database as db
    import app.utils.supabase_errors as se

    bucket = _FakeBucket(outcomes)

    class _Client:
        class storage:
            @staticmethod
            def from_(_bucket):
                return bucket

    monkeypatch.setattr(db, "get_supabase", lambda: _Client())
    monkeypatch.setattr(se.time, "sleep", lambda _s: None)
    return bucket


def test_a_read_timeout_is_retried_once_and_loads_quietly(monkeypatch, caplog):
    body = json.dumps(_payload()).encode()
    bucket = _storage(monkeypatch, [httpx.ReadTimeout("The read operation timed out"), body])
    caplog.set_level(logging.WARNING)
    assert [i["industry"] for i in ud.load_universe(ud.INDUSTRY_UNIVERSE)] == ["Software"]
    assert bucket.calls == 2
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "a recovered blip still paged"
    assert any("universe download industry_universe.json" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    assert not ud.is_unavailable(ud.INDUSTRY_UNIVERSE)


def test_two_timeouts_still_fail_loudly_and_start_the_window(monkeypatch, caplog, clock):
    bucket = _storage(monkeypatch, [httpx.ReadTimeout("t1"), httpx.ReadTimeout("t2")])
    caplog.set_level(logging.ERROR)
    assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == []
    assert bucket.calls == 2
    assert any("could not fetch industry_universe.json" in r.getMessage() for r in caplog.records)
    assert ud.is_unavailable(ud.INDUSTRY_UNIVERSE)
    assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == [] and bucket.calls == 2   # inside the window


def test_a_deterministic_storage_error_is_not_retried(monkeypatch, caplog):
    from storage3.exceptions import StorageApiError

    bucket = _storage(monkeypatch, [StorageApiError("Object not found", "not_found", 404)])
    caplog.set_level(logging.ERROR)
    assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == []
    assert bucket.calls == 1
    assert any("could not fetch" in r.getMessage() for r in caplog.records)


def test_is_unavailable_means_the_last_read_failed(monkeypatch, clock):
    dl, _ = _flaky_downloader([None, json.dumps(_payload()).encode()])
    monkeypatch.setattr(ud, "_download_from_storage", dl)
    assert not ud.is_unavailable(ud.INDUSTRY_UNIVERSE), "never attempted is not a hole"
    ud.load_universe(ud.INDUSTRY_UNIVERSE)
    assert ud.is_unavailable(ud.INDUSTRY_UNIVERSE)
    clock.now += ud._FAILURE_RETRY_SECONDS + 1
    ud.load_universe(ud.INDUSTRY_UNIVERSE)
    assert not ud.is_unavailable(ud.INDUSTRY_UNIVERSE)
    ud.reset_cache_for_tests()
    assert not ud.is_unavailable(ud.INDUSTRY_UNIVERSE)


# ── The report collector: off the event loop, and a hole is not shared-cached ──────

def test_an_unavailable_universe_is_recorded_as_a_transient_hole(monkeypatch, caplog):
    from types import SimpleNamespace

    from app.services.agents import ticker_report_data_collector as col

    monkeypatch.setattr(ud, "_download_from_storage", lambda _f: None)   # Storage down
    col._INDUSTRY_PEERS_CACHE.clear()
    assert col._industry_universe_peers("Aerospace & Defense", set()) == []
    out = SimpleNamespace(degraded_sections=[])
    caplog.set_level(logging.WARNING)
    col._note_universe_hole(out, "RKLB", "Aerospace & Defense")
    assert out.degraded_sections == ["industry_universe:transient"]
    assert any("ticker=RKLB" in r.getMessage() for r in caplog.records)


def test_a_lookup_that_never_read_the_universe_is_not_a_hole():
    """Review of the first cut: a stubbed or cached peer lookup never reaches universe_data,
    and "not loaded yet" must not read as "unavailable" (14 pass-two tests caught it)."""
    from types import SimpleNamespace

    from app.services.agents import ticker_report_data_collector as col

    out = SimpleNamespace(degraded_sections=[])
    col._note_universe_hole(out, "V", "Credit Services")
    assert out.degraded_sections == []


def test_an_industry_the_universe_lacks_is_not_a_hole(monkeypatch):
    from types import SimpleNamespace

    from app.services.agents import ticker_report_data_collector as col

    monkeypatch.setattr(ud, "_download_from_storage", lambda _f: json.dumps(_payload()).encode())
    col._INDUSTRY_PEERS_CACHE.clear()
    assert col._industry_universe_peers("Shell Companies", set()) == []      # absent industry
    out = SimpleNamespace(degraded_sections=[])
    col._note_universe_hole(out, "XYZ", "Shell Companies")
    assert out.degraded_sections == []
    col._INDUSTRY_PEERS_CACHE.clear()


def _code_only(src: str) -> str:
    import re
    return "\n".join(re.sub(r"\s#.*$", "", l) for l in src.splitlines() if not l.lstrip().startswith("#"))


def test_the_peer_lookup_runs_off_the_event_loop_and_notes_a_hole():
    import inspect
    import re

    from app.services.agents import ticker_report_data_collector as col

    body = _code_only(inspect.getsource(col.TickerReportDataCollector._fetch_dependent))
    assert re.search(r"await\s+asyncio\.to_thread\(\s*_industry_universe_peers\s*,", body), (
        "a cold universe read is a blocking Storage download — it must not run on the loop")
    assert not re.search(r"(?<!\.to_thread\()\b_industry_universe_peers\(", body), (
        "a direct (blocking) call of the peer lookup is back")
    assert "_note_universe_hole(out, ticker, industry)" in body
