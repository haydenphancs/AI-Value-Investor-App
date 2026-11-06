"""A financial industry falling under the 10-company peer floor is never silent (2026-11-05).

A Financial Services company reads its own industry's median from 10 companies or has no peer,
and since the own-industry moat change the moat follows the same rule. Financial - Data & Stock
Exchanges sits at exactly 10 on production. One ticker lost in a quarterly run stays inside the
INCOMPLETE line, so its cells are rewritten at 9, the run succeeds, and ten large caps go
unmeasured until the next chain with nothing said. `_PeerFloorWatch` says it.

These run the REAL recompute (fetch through upsert) over a fake table, so what the watch reads
is what the run is about to overwrite. Only FMP, Supabase and Telegram are faked. Also here:
`owner_alert.send_owner_alert` and `/admin/industry-moat-benchmarks-status`.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import admin
from app.integrations.telegram import TelegramRateLimitException
from app.services import industry_benchmark_service as ibs
from app.services import owner_alert
from app.services.industry_benchmark_service import (
    IndustryBenchmarkRecomputeIncomplete,
    IndustryBenchmarkService,
    _floor_cell,
    _PeerFloorWatch,
    _stored_years,
    _under_floor_rows,
)
from app.services.marketing import review_service, telegram_pacing
from app.services.sector_benchmark_service import (
    FETCH_ERRORS_KEY,
    FETCH_FAILED_CALLS_KEY,
    SectorBenchmarkService,
)

_LOGGER = "app.services.industry_benchmark_service"
_FS = "Financial Services"
_EXCH = "Financial - Data & Stock Exchanges"
_ROSTER = ["SPGI", "CME", "ICE", "MCO", "NDAQ", "COIN", "MSCI", "TRU", "FDS", "MORN"]
_YEARS = ("2023", "2024", "2025")
_WRITTEN_AT = "2026-10-21T04:00:00+00:00"   # after those years completed, so a reader sees them


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _Query:
    def __init__(self, db: "_Db") -> None:
        self.db = db
        self.filters: dict = {}
        self.span = None
        self.pending = None

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, start, end):
        self.span = (start, end)
        return self

    def upsert(self, rows, on_conflict=None):
        self.pending = rows
        return self

    def execute(self):
        if self.pending is not None:
            self.db.events.append("upsert")
            for row in self.pending:
                self.db.rows[_key(row)] = dict(row)
            return SimpleNamespace(data=self.pending)
        self.db.events.append("read")
        if self.db.read_error is not None:
            raise self.db.read_error
        found = [
            r for r in self.db.rows.values()
            if all(r.get(k) == v for k, v in self.filters.items())
        ]
        start, end = self.span or (0, len(found))
        return SimpleNamespace(data=found[start:end + 1])


class _Db:
    def __init__(self) -> None:
        self.rows: dict = {}
        self.events: list = []
        self.read_error = None

    def table(self, _name):
        return _Query(self)

    def seed(self, industry, metric, cells, *, sector=_FS, computed_at=_WRITTEN_AT):
        for year, n in cells.items():
            row = {
                "sector": sector, "industry": industry, "metric_name": metric,
                "period_type": "annual", "period_label": year, "median_value": 0.5,
                "sample_size": n, "computed_at": computed_at,
            }
            self.rows[_key(row)] = row

    def n(self, industry, metric, year):
        for row in self.rows.values():
            if (row["industry"], row["metric_name"], row["period_label"]) == (industry, metric, year):
                return row["sample_size"]
        return None


def _key(row):
    return (
        row["sector"], row["industry"], row["metric_name"], row["period_type"], row["period_label"],
    )


def _company(years=_YEARS, failed=None):
    """One company's statements: a gross margin per year, or a failed fetch of kind `failed`."""
    if failed:
        return {
            "ratios_annual": [], "income_annual": [], FETCH_ERRORS_KEY: [failed],
            FETCH_FAILED_CALLS_KEY: {"ratios_annual": failed},
        }
    return {
        "ratios_annual": [
            {"date": f"{y}-12-31", "grossProfitMargin": 0.5, "revenuePerShare": 10.0} for y in years
        ],
    }


def _service(db, universe, companies, monkeypatch):
    """The real service over `db`; `companies` maps a ticker to `_company()` or an exception, and
    a ticker it leaves out answers every year."""
    svc = IndustryBenchmarkService.__new__(IndustryBenchmarkService)
    svc.supabase = db
    svc._calendar_quarter_blocked = False
    svc._sb = SectorBenchmarkService.__new__(SectorBenchmarkService)

    async def fetch(ticker, _al, _ql):
        answer = companies.get(ticker, _company())
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def universe_for_run(_file):
        return universe

    svc._sb._fetch_company_data = fetch
    monkeypatch.setattr(svc, "_universe_for_run", universe_for_run)
    monkeypatch.setattr(svc, "_sector_is_fresh", lambda _s, _h: False)
    monkeypatch.setattr(ibs, "BATCH_DELAY_SECONDS", 0)
    return svc


@pytest.fixture
def alerts(monkeypatch):
    sent: list = []

    async def fake(text, *, what, loud=True):
        sent.append(text)
        return True

    monkeypatch.setattr(ibs, "send_owner_alert", fake)
    return sent


def _roster(tickers=_ROSTER):
    return [(t, 1e9 - i) for i, t in enumerate(tickers)]


def _exchanges(tickers=_ROSTER):
    return [(_FS, [(_EXCH, _roster(tickers))])]


def _errors(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def _floor_errors(caplog):
    return [m for m in _errors(caplog) if m.startswith("PEER FLOOR")]


# ── The owner's four cases ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exactly_ten_is_not_under_the_floor_and_reads_nothing(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    svc = _service(db, _exchanges(), {}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert db.n(_EXCH, "gross_margin", "2025") == 10
    assert not _floor_errors(caplog) and alerts == []
    assert summary["peer_floor_newly_under"] == {} and summary["peer_floor_alert_sent"] is None
    assert "read" not in db.events   # a clean industry costs no read


@pytest.mark.asyncio
async def test_nine_from_ten_names_industry_metric_and_tickers(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    svc = _service(db, _exchanges(), {"TRU": _company(failed="rate_limited")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)

    assert db.n(_EXCH, "gross_margin", "2025") == 9    # the write is not held up
    lines = _floor_errors(caplog)
    assert len(lines) == 1, "one ERROR line per run"
    line = lines[0]
    assert _EXCH in line and "gross_margin" in line and "2023, 2024, 2025" in line
    assert "9 companies, was 10" in line and "TRU (rate_limited)" in line
    assert all(t in line for t in _ROSTER), "every ticker of the industry is named"
    assert summary["peer_floor_newly_under"] == {_EXCH: 3}
    assert summary["peer_floor_alert_sent"] is True
    assert len(alerts) == 1
    text = alerts[0]
    assert _EXCH in text and "TRU (rate_limited)" in text and "down to 9" in text
    assert "1 metric(s), 2023-2025" in text and "Price unrated" in text


@pytest.mark.parametrize("was", [9, 7])
@pytest.mark.asyncio
async def test_an_industry_already_under_the_floor_says_nothing(monkeypatch, alerts, caplog, was):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: was for y in _YEARS})
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert db.n(_EXCH, "gross_margin", "2025") == 9
    assert not _floor_errors(caplog) and alerts == []
    assert summary["peer_floor_newly_under"] == {}


@pytest.mark.asyncio
async def test_a_failed_stored_read_lists_every_cell_as_possibly_new(monkeypatch, alerts, caplog):
    """"New" cannot be told from "already under", so it says so. The write still happens."""
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    db.read_error = RuntimeError("supabase down")
    svc = _service(db, _exchanges(), {"TRU": _company(failed="unavailable")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)

    assert db.n(_EXCH, "gross_margin", "2025") == 9
    assert summary["peer_floor_newly_under"] == {}
    assert summary["peer_floor_baseline_unread"] == [_EXCH]
    line = _floor_errors(caplog)[0]
    assert "POSSIBLY NEW" in line and "RuntimeError: supabase down" in line and "was unknown" in line
    assert any("could not read the stored cells" in m for m in _errors(caplog))
    assert len(alerts) == 1 and "Possibly not new" in alerts[0] and _EXCH in alerts[0]


@pytest.mark.asyncio
async def test_a_failed_fmp_read_names_the_lost_ticker(monkeypatch, alerts, caplog):
    """A ticker whose fetch raised is in no company map at all: it is still named."""
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    svc = _service(db, _exchanges(), {"MORN": RuntimeError("boom")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert "MORN (fetch failed)" in _floor_errors(caplog)[0]


# ── What counts as newly under ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_lost_tickers_stay_inside_the_loss_line_and_are_both_named(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    lost = {"TRU": _company(failed="rate_limited"), "FDS": _company(failed="unavailable")}
    svc = _service(db, _exchanges(), lost, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER), pytest.raises(IndustryBenchmarkRecomputeIncomplete):
        await svc.recompute_all(skip_if_fresh_hours=24)   # 20% of its sector lost: lossy
    line = _floor_errors(caplog)[0]
    assert "8 companies, was 10" in line
    assert "TRU (rate_limited)" in line and "FDS (unavailable)" in line
    assert len(alerts) == 1, "reported before the run raises"


@pytest.mark.asyncio
async def test_a_new_year_is_judged_against_the_year_before_it(monkeypatch, alerts, caplog):
    """The first run after a year completes: no stored row, and the company has no peer."""
    db = _Db()
    db.seed(_EXCH, "gross_margin", {"2023": 10, "2024": 10})
    svc = _service(db, _exchanges(), {"TRU": _company(years=("2023", "2024"))}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert "2025: 9 companies, was 10 in 2024" in _floor_errors(caplog)[0]


@pytest.mark.asyncio
async def test_a_new_year_under_a_year_already_under_says_nothing(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {"2023": 9, "2024": 9})
    svc = _service(db, _exchanges(), {"TRU": _company(years=("2023", "2024"))}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert not _floor_errors(caplog) and alerts == []


def test_a_metric_with_no_stored_year_fell_from_nothing():
    row = {"metric_name": "gross_margin", "period_label": "2025", "sample_size": 9}
    assert _floor_cell(row, {}) is None
    assert _floor_cell(row, {"gross_margin": {2026: 12}}) is None   # only an OLDER year counts


def test_a_stored_row_no_screen_serves_is_no_baseline():
    """A "2025" written from early filers reads n=7 and is hidden. Taken at face value it would
    make a real fall read as "already under"."""
    stored = _stored_years([
        {"metric_name": "gross_margin", "period_label": "2025", "median_value": 0.5,
         "sample_size": 7, "computed_at": "2025-11-01T00:00:00+00:00"},
        {"metric_name": "gross_margin", "period_label": "2024", "median_value": 0.5,
         "sample_size": 10, "computed_at": _WRITTEN_AT},
    ])
    assert stored == {"gross_margin": {2024: 10}}
    row = {"metric_name": "gross_margin", "period_label": "2025", "sample_size": 9}
    assert _floor_cell(row, stored)["was_year"] == 2024


@pytest.mark.parametrize("bad", [None, True, "10", -1, 10.5])
def test_a_malformed_stored_count_is_no_baseline(bad):
    assert _stored_years([{
        "metric_name": "gross_margin", "period_label": "2024", "median_value": 0.5,
        "sample_size": bad, "computed_at": _WRITTEN_AT,
    }]) == {}


def test_only_the_newest_three_years_of_annual_rows_are_read():
    def row(label, period_type="annual", n=9):
        return {"metric_name": "gross_margin", "period_type": period_type,
                "period_label": label, "sample_size": n}

    rows = [row(str(y)) for y in range(2012, 2026)] + [
        row("Q2'26", "calendar_quarter"), row("2024", n=10),
    ]
    assert sorted(r["period_label"] for r in _under_floor_rows(rows)) == ["2023", "2024", "2025"]
    assert _under_floor_rows([]) == []


def test_a_metric_that_means_nothing_for_the_industry_is_not_read():
    """An insurer's interest coverage is written and never served: production held one such
    cell falling 10 → 9 in Insurance - Specialty."""
    reads: list = []

    def read(sector, industry):
        reads.append(industry)
        return [{"metric_name": "interest_coverage", "period_label": "2025", "median_value": 3.0,
                 "sample_size": 10, "computed_at": _WRITTEN_AT}]

    row = {"metric_name": "interest_coverage", "period_type": "annual", "period_label": "2025",
           "sample_size": 9}
    watch = _PeerFloorWatch("fiscal", dry_run=False)
    watch.check(read, lambda *_a: set(), _FS, "Insurance - Specialty", _roster(), {}, [row])
    assert reads == [] and watch.fallen == [] and watch.unread == [] and watch.unchecked == []

    # The same fall on a broker, whose interest coverage does mean something, is reported.
    watch = _PeerFloorWatch("fiscal", dry_run=False)
    watch.check(read, lambda *_a: set(), _FS, "Insurance - Brokers", _roster(), {}, [row])
    assert reads == ["Insurance - Brokers"] and len(watch.fallen) == 1


@pytest.mark.asyncio
async def test_a_cell_under_the_sample_minimum_is_not_rewritten_so_nothing_fell(monkeypatch, alerts, caplog):
    """Six refusals leave 4 values: no row is written, the n=10 row stands, readers keep a peer."""
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    lost = {t: _company(failed="refused") for t in _ROSTER[:6]}
    svc = _service(db, _exchanges(), lost, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeSkipped):
            await svc.recompute_all(skip_if_fresh_hours=24)   # nothing written at all
    assert db.n(_EXCH, "gross_margin", "2025") == 10
    assert not _floor_errors(caplog) and alerts == []


@pytest.mark.asyncio
async def test_an_industry_over_the_loss_line_keeps_its_rows_and_is_not_reported(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    banks = [f"B{i}" for i in range(30)]
    universe = [(_FS, [(_EXCH, _roster()), ("Banks - Regional", _roster(banks))])]
    lost = {t: _company(failed="rate_limited") for t in ("TRU", "FDS", "MORN")}
    svc = _service(db, universe, lost, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER), pytest.raises(IndustryBenchmarkRecomputeIncomplete):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert db.n(_EXCH, "gross_margin", "2025") == 10   # withheld (P4-1)
    assert not _floor_errors(caplog) and alerts == []


@pytest.mark.asyncio
async def test_an_operating_industry_is_never_watched(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed("Software", "gross_margin", {y: 10 for y in _YEARS}, sector="Technology")
    svc = _service(db, [("Technology", [("Software", _roster())])], {"TRU": _company(failed="refused")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert db.n("Software", "gross_margin", "2025") == 9
    assert not _floor_errors(caplog) and alerts == [] and "read" not in db.events


@pytest.mark.asyncio
async def test_mortgage_reits_are_watched_because_the_moat_keeps_them_to_their_own_peers(
    monkeypatch, alerts, caplog,
):
    db = _Db()
    db.seed("REIT - Mortgage", "gross_margin", {y: 10 for y in _YEARS}, sector="Real Estate")
    universe = [("Real Estate", [("REIT - Mortgage", _roster())])]
    svc = _service(db, universe, {"TRU": _company(failed="refused")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert "REIT - Mortgage" in _floor_errors(caplog)[0]


# ── How the run reports ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_stored_cells_are_read_before_they_are_overwritten(monkeypatch, alerts):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    await svc.recompute_all(skip_if_fresh_hours=24)
    assert db.events[0] == "read" and "upsert" in db.events[1:]


@pytest.mark.asyncio
async def test_a_trail_is_logged_before_the_write_for_a_killed_process(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    seen_before_write: list = []
    original = IndustryBenchmarkService._emit

    def emit(self, rows, label, dry_run, *, marker_last=False):
        seen_before_write.extend(r.getMessage() for r in caplog.records)
        return original(self, rows, label, dry_run, marker_last=marker_last)

    monkeypatch.setattr(IndustryBenchmarkService, "_emit", emit)
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert any("about to write 3 cell(s)" in m and "TRU (refused)" in m for m in seen_before_write)


@pytest.mark.asyncio
async def test_a_same_day_retry_does_not_repeat_the_alert(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    await svc.recompute_all(skip_if_fresh_hours=24)
    await svc.recompute_all(skip_if_fresh_hours=24)
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_a_dry_run_warns_and_tells_nobody(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(dry_run=True)
    assert db.n(_EXCH, "gross_margin", "2025") == 10 and "upsert" not in db.events
    assert not _floor_errors(caplog) and alerts == []
    warned = [r.getMessage() for r in caplog.records if "dry run, nothing written" in r.getMessage()]
    assert len(warned) == 1 and _EXCH in warned[0]
    assert any("would write 3 cell(s)" in r.getMessage() for r in caplog.records)
    assert summary["peer_floor_newly_under"] == {_EXCH: 3}


@pytest.mark.asyncio
async def test_a_run_that_raises_still_reports_and_names_a_retried_sector(monkeypatch, alerts, caplog):
    """The floor's own sector is unsettled, so the note says a retry may bring it back."""
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    banks = [f"B{i}" for i in range(30)]
    universe = [(_FS, [(_EXCH, _roster()), ("Banks - Regional", _roster(banks))])]
    lost = {"TRU": _company(failed="refused")}
    lost.update({t: _company(failed="unavailable") for t in banks[:12]})
    svc = _service(db, universe, lost, monkeypatch)
    with pytest.raises(IndustryBenchmarkRecomputeIncomplete):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert len(alerts) == 1 and "did not finish Financial Services" in alerts[0]


@pytest.mark.asyncio
async def test_another_sectors_failure_is_not_blamed_on_the_floor(monkeypatch, alerts):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    universe = [
        (_FS, [(_EXCH, _roster())]),
        ("Technology", [("Software", _roster([f"S{i}" for i in range(6)]))]),
    ]
    svc = _service(db, universe, {"TRU": _company(failed="refused")}, monkeypatch)
    original = svc._rows_from_values

    def rows(sector, industry, values, now):
        if sector == "Technology":
            raise RuntimeError("boom")
        return original(sector, industry, values, now)

    monkeypatch.setattr(svc, "_rows_from_values", rows)
    with pytest.raises(IndustryBenchmarkRecomputeIncomplete):
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert len(alerts) == 1 and "did not finish" not in alerts[0]


@pytest.mark.asyncio
async def test_a_failed_alert_never_replaces_what_the_run_raises(monkeypatch, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})

    async def broken(_text, *, what, loud=True):
        raise RuntimeError("telegram bug")

    monkeypatch.setattr(ibs, "send_owner_alert", broken)
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["peer_floor_alert_sent"] is False
    assert len(_floor_errors(caplog)) == 2      # the account, then the failed report
    assert "the report itself failed" in _floor_errors(caplog)[1]


@pytest.mark.asyncio
async def test_an_undelivered_alert_shows_in_the_summary(monkeypatch, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})

    async def undelivered(_text, *, what, loud=True):
        return False

    monkeypatch.setattr(ibs, "send_owner_alert", undelivered)
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["peer_floor_alert_sent"] is False


@pytest.mark.asyncio
async def test_a_crashed_check_never_stops_the_write_and_is_reported(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})

    def broken(_rows):
        raise RuntimeError("a bug in the check")

    monkeypatch.setattr(ibs, "_under_floor_rows", broken)
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert db.n(_EXCH, "gross_margin", "2025") == 9
    assert summary["peer_floor_unchecked"] == [_EXCH]
    assert any("peer floor check FAILED" in m for m in _errors(caplog))
    assert "CHECK FAILED" in _floor_errors(caplog)[0]
    assert len(alerts) == 1 and "check failed" in alerts[0] and "0 financial" not in alerts[0]


@pytest.mark.asyncio
async def test_failing_to_name_tickers_keeps_the_fall(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)

    def broken(*_a):
        raise RuntimeError("no names today")

    monkeypatch.setattr(svc, "_cell_members", broken)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["peer_floor_newly_under"] == {_EXCH: 3}
    line = _floor_errors(caplog)[0]
    assert "missing unknown" in line and all(t in line for t in _ROSTER)


@pytest.mark.asyncio
async def test_a_cancelled_run_flushes_its_error_line(monkeypatch, alerts, caplog):
    """A deploy between sectors: the retry reads the 9 as already under, so this is the last word."""
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    universe = [(_FS, [(_EXCH, _roster())]), ("Technology", [("Software", _roster(["S"]))])]
    svc = _service(db, universe, {"TRU": _company(failed="refused")}, monkeypatch)
    calls = []

    def fresh(_sector, _hours):
        calls.append(_sector)
        if len(calls) == 2:      # outside `_compute_sector`, the loop's own await
            raise asyncio.CancelledError()
        return False

    monkeypatch.setattr(svc, "_sector_is_fresh", fresh)
    with caplog.at_level(logging.INFO, logger=_LOGGER), pytest.raises(asyncio.CancelledError):
        await svc.recompute_all(skip_if_fresh_hours=24)
    line = _floor_errors(caplog)[0]
    assert "when this run was cancelled" in line and "TRU (refused)" in line
    assert alerts == []


@pytest.mark.asyncio
async def test_the_named_industries_path_is_watched_too(monkeypatch, alerts, caplog):
    db = _Db()
    db.seed(_EXCH, "gross_margin", {y: 10 for y in _YEARS})
    svc = _service(db, _exchanges(), {"TRU": _company(failed="refused")}, monkeypatch)
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        summary = await svc.recompute_all(industries=[_EXCH])
    assert summary["peer_floor_newly_under"] == {_EXCH: 3}
    assert "fiscal-industries run" in _floor_errors(caplog)[0] and len(alerts) == 1


def test_the_grouping_keeps_one_lost_ticker_to_one_clause():
    cells = [
        {"metric": m, "year": y, "n": 9, "was": 10, "was_year": y, "missing": "TRU (refused)"}
        for m in ("roe", "gross_margin") for y in (2024, 2025)
    ]
    assert ibs._cell_groups(cells) == [
        "2024, 2025: 9 companies, was 10, missing TRU (refused): gross_margin, roe"
    ]


def test_the_alert_text_stays_inside_telegrams_limit_with_many_industries():
    watch = _PeerFloorWatch("fiscal", dry_run=False)
    for i in range(400):
        watch.fallen.append({
            "sector": _FS, "industry": f"Industry {i}", "roster": _ROSTER,
            "cells": [{"metric": "roe", "year": 2025, "n": 9, "was": 10, "was_year": 2025,
                       "missing": "TRU (refused)"}],
        })
    assert len(owner_alert._fit(watch._telegram_text([]))) <= owner_alert.MAX_ALERT_CHARS


# ── owner_alert ───────────────────────────────────────────────────────────────


@pytest.fixture
def bot(monkeypatch):
    sent: list = []

    class Pacer:
        turns = 0

        async def wait(self):
            Pacer.turns += 1

    async def send(chat_id, text, *, disable_notification=False):
        sent.append((chat_id, text, disable_notification))
        return {"ok": True}

    monkeypatch.setattr(review_service, "is_configured", lambda: True)
    monkeypatch.setattr(review_service, "review_chat_id", lambda: 77)
    monkeypatch.setattr(telegram_pacing, "PACER", Pacer())
    monkeypatch.setattr(telegram_pacing, "_rate_limited_until", 0.0)
    monkeypatch.setattr(owner_alert.telegram, "send_message", send)
    return SimpleNamespace(sent=sent, pacer=Pacer)


@pytest.mark.asyncio
async def test_owner_alert_goes_through_the_shared_pacer_loud(bot):
    assert await owner_alert.send_owner_alert("hello\nthere", what="test alert") is True
    assert bot.sent == [(77, "hello\nthere", False)] and bot.pacer.turns == 1


@pytest.mark.asyncio
async def test_owner_alert_keeps_its_text_in_the_log_when_the_bot_is_off(bot, monkeypatch, caplog):
    monkeypatch.setattr(review_service, "is_configured", lambda: False)
    with caplog.at_level(logging.INFO, logger="app.services.owner_alert"):
        assert await owner_alert.send_owner_alert("the message", what="test alert") is False
    assert bot.sent == [] and any(
        "NOT SENT" in r.getMessage() and "the message" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_owner_alert_never_sends_into_a_closed_chat(bot, monkeypatch, caplog):
    monkeypatch.setattr(telegram_pacing, "is_rate_limited", lambda: True)
    with caplog.at_level(logging.INFO, logger="app.services.owner_alert"):
        assert await owner_alert.send_owner_alert("the message", what="test alert") is False
    assert bot.sent == [] and any("the message" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_owner_alert_records_a_429_for_every_sender(bot, monkeypatch, caplog):
    noted: list = []

    async def flood(*_a, **_k):
        raise TelegramRateLimitException("slow down", method="sendMessage", retry_after=30)

    monkeypatch.setattr(owner_alert.telegram, "send_message", flood)
    monkeypatch.setattr(telegram_pacing, "note_rate_limit", noted.append)
    with caplog.at_level(logging.INFO, logger="app.services.owner_alert"):
        assert await owner_alert.send_owner_alert("the message", what="test alert") is False
    assert noted == [30]


@pytest.mark.asyncio
async def test_owner_alert_never_raises(bot, monkeypatch, caplog):
    async def boom(*_a, **_k):
        raise ValueError("no")

    monkeypatch.setattr(owner_alert.telegram, "send_message", boom)
    with caplog.at_level(logging.INFO, logger="app.services.owner_alert"):
        assert await owner_alert.send_owner_alert("the message", what="test alert") is False
    assert any("the message" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_owner_alert_lets_a_cancel_through(bot, monkeypatch):
    async def cancelled(*_a, **_k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(owner_alert.telegram, "send_message", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await owner_alert.send_owner_alert("the message", what="test alert")


@pytest.mark.parametrize("text", ["", "   ", None])
@pytest.mark.asyncio
async def test_owner_alert_refuses_empty_text(bot, text):
    assert await owner_alert.send_owner_alert(text, what="test alert") is False
    assert bot.sent == []


@pytest.mark.asyncio
async def test_owner_alert_cuts_a_long_message_and_says_so(bot):
    assert await owner_alert.send_owner_alert("x" * 9000, what="test alert") is True
    body = bot.sent[0][1]
    assert len(body) <= owner_alert.MAX_ALERT_CHARS and body.endswith("Railway log.")


# ── /admin/industry-moat-benchmarks-status ────────────────────────────────────


_ADMIN = {"id": "aaaabbbb-cccc-4ddd-8eee-ffff00001111", "is_admin": True}


class _StatusQuery:
    def __init__(self, rows):
        self.rows, self.span = rows, None

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, start, end):
        self.span = (start, end)
        return self

    def execute(self):
        start, end = self.span or (0, 999)
        return SimpleNamespace(data=self.rows[start:min(end + 1, start + 1000)], count=len(self.rows))


def _install_status(monkeypatch, rows):
    import app.database

    monkeypatch.setattr(
        app.database, "get_supabase",
        lambda: SimpleNamespace(table=lambda _name: _StatusQuery(rows)),
    )


def _moat_row(industry, pillar, version):
    return {"industry": industry, "pillar_name": pillar, "sample_size": 20,
            "computed_at": "2026-11-06T04:00:00+00:00", "model_version": version}


@pytest.mark.asyncio
async def test_status_counts_each_vintage_per_industry(monkeypatch):
    from app.services.industry_moat_benchmark_service import MODEL_VERSION

    old = "moat_v1.2026-10-28"
    _install_status(monkeypatch, [
        _moat_row("Software", "Brand Power", MODEL_VERSION),
        _moat_row("Software", "Cost Advantage", MODEL_VERSION),
        _moat_row("Banks", "Brand Power", old),
        _moat_row("Banks", "Cost Advantage", MODEL_VERSION),
        _moat_row("Ancient", "Brand Power", None),
    ])
    out = await admin.industry_moat_benchmarks_status(x_admin_token=None, user=_ADMIN)
    assert out["current_model_version"] == MODEL_VERSION
    assert out["model_version_counts"] == {MODEL_VERSION: 3, old: 1, "unstamped": 1}
    assert out["industry_model_versions"] == {
        "Ancient": {"unstamped": 1},
        "Banks": {MODEL_VERSION: 1, old: 1},
        "Software": {MODEL_VERSION: 2},
    }
    assert out["industries_on_older_versions"] == ["Ancient", "Banks"]
    assert out["distinct_industries"] == 3 and out["total_rows"] == 5
    assert out["pillar_coverage"] == {"Brand Power": 3, "Cost Advantage": 2}


@pytest.mark.asyncio
async def test_status_reads_past_postgrests_page(monkeypatch):
    """A confirmation that stops at 1,000 rows would call a finished refresh unfinished."""
    rows = [_moat_row(f"Industry {i // 5}", f"Pillar {i % 5}", "v") for i in range(1300)]
    _install_status(monkeypatch, rows)
    out = await admin.industry_moat_benchmarks_status(x_admin_token=None, user=_ADMIN)
    assert out["model_version_counts"] == {"v": 1300} and out["distinct_industries"] == 260


@pytest.mark.asyncio
async def test_status_on_an_empty_table(monkeypatch):
    _install_status(monkeypatch, [])
    out = await admin.industry_moat_benchmarks_status(x_admin_token=None, user=_ADMIN)
    assert out["model_version_counts"] == {} and out["industries_on_older_versions"] == []


# ── Text the owner reads ──────────────────────────────────────────────────────


def _repo() -> Path:
    return Path(__file__).resolve().parents[2]


def test_the_refresh_route_quotes_the_measured_time():
    source = (_repo() / "backend/app/api/v1/endpoints/admin.py").read_text()
    assert "60-90" not in source and "14.5 minutes" in source and "about 15 minutes" in source


def test_the_owner_task_list_says_what_to_do_about_the_alert():
    text = (_repo() / "documents/OWNER_TASKS.md").read_text()
    assert "Telegram `🔴 Peer floor`, or Railway ERROR `PEER FLOOR`" in text
