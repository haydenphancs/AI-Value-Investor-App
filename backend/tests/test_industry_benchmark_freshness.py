"""Freshness-probe + upsert-failure tests for the industry/TTM benchmark recompute.

Confirmed bugs covered:
  * HIGH — `_sector_is_fresh` had NO period_type filter, so a fresh weekly 'ttm' write
    made the quarterly FISCAL recompute skip every sector (chart history + growth series
    frozen). Fixed by `.eq("period_type", "annual")`, mirroring `_ttm_sector_is_fresh`.
  * MED — `_upsert` swallowed batch failures, so a sector whose industry rows failed
    mid-write still got a fresh '' aggregate timestamp and was wrongly skipped on resume.
    Fixed by re-raising; the per-sector guard then aborts BEFORE writing the '' row.

Self-contained: a tiny chainable fake Supabase, no live DB.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.industry_benchmark_service import IndustryBenchmarkService


def _iso(dt):
    return dt.isoformat()


# ── Chainable fake Supabase for the freshness SELECT ────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _SelectChain:
    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls
        self._eqs = {}

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._eqs[col] = val
        self._calls.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        out = [r for r in self._rows if all(r.get(c) == v for c, v in self._eqs.items())]
        out.sort(key=lambda r: r.get("computed_at", ""), reverse=True)
        return _Resp(out[:1])


class _FreshnessSupabase:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def table(self, _name):
        return _SelectChain(self._rows, self.calls)


def _svc_with_rows(rows):
    svc = IndustryBenchmarkService.__new__(IndustryBenchmarkService)
    svc.supabase = _FreshnessSupabase(rows)
    return svc


# ── _sector_is_fresh: must ignore TTM rows (HIGH bug) ───────────────

def test_sector_is_fresh_ignores_recent_ttm_rows():
    now = datetime.now(timezone.utc)
    rows = [
        {"sector": "Technology", "industry": "", "period_type": "annual",
         "computed_at": _iso(now - timedelta(hours=48))},   # STALE fiscal
        {"sector": "Technology", "industry": "", "period_type": "ttm",
         "computed_at": _iso(now - timedelta(hours=1))},     # fresh TTM (must be ignored)
    ]
    svc = _svc_with_rows(rows)
    assert svc._sector_is_fresh("Technology", 24) is False   # would be True pre-fix
    assert ("period_type", "annual") in svc.supabase.calls


def test_sector_is_fresh_true_when_annual_recent():
    now = datetime.now(timezone.utc)
    rows = [{"sector": "Technology", "industry": "", "period_type": "annual",
             "computed_at": _iso(now - timedelta(hours=1))}]
    assert _svc_with_rows(rows)._sector_is_fresh("Technology", 24) is True


def test_sector_is_fresh_no_rows_is_false():
    assert _svc_with_rows([])._sector_is_fresh("Technology", 24) is False


def test_sector_is_fresh_malformed_timestamp_is_false():
    rows = [{"sector": "Technology", "industry": "", "period_type": "annual",
             "computed_at": "not-a-date"}]
    assert _svc_with_rows(rows)._sector_is_fresh("Technology", 24) is False


def test_sector_is_fresh_zero_hours_is_false():
    assert _svc_with_rows([])._sector_is_fresh("Technology", 0) is False
    assert _svc_with_rows([])._sector_is_fresh("Technology", None) is False


# ── _ttm_sector_is_fresh: the already-correct mirror (cross-check) ──

def test_ttm_sector_is_fresh_filters_to_ttm():
    now = datetime.now(timezone.utc)
    # Only an annual row exists → the TTM probe must NOT consider it fresh.
    rows = [{"sector": "Technology", "industry": "", "period_type": "annual",
             "computed_at": _iso(now - timedelta(hours=1))}]
    svc = _svc_with_rows(rows)
    assert svc._ttm_sector_is_fresh("Technology", 24) is False
    assert ("period_type", "ttm") in svc.supabase.calls
    # A fresh ttm row → fresh.
    svc2 = _svc_with_rows([{"sector": "Technology", "industry": "", "period_type": "ttm",
                            "computed_at": _iso(now - timedelta(hours=1))}])
    assert svc2._ttm_sector_is_fresh("Technology", 24) is True


# ── _upsert: raise on batch failure (no silent partial write) ───────

class _UpsertChain:
    def __init__(self, counter, recorded, fail_on):
        self._counter = counter
        self._recorded = recorded
        self._fail_on = fail_on
        self._batch = None

    def upsert(self, batch, on_conflict=None):
        self._batch = batch
        return self

    def execute(self):
        self._counter[0] += 1
        self._recorded.append(list(self._batch))
        if self._counter[0] == self._fail_on:
            raise RuntimeError("supabase boom")
        return _Resp([])


class _UpsertSupabase:
    def __init__(self, fail_on):
        self.counter = [0]
        self.recorded = []
        self._fail_on = fail_on

    def table(self, _name):
        return _UpsertChain(self.counter, self.recorded, self._fail_on)


def test_upsert_raises_on_batch_failure_and_stops():
    svc = IndustryBenchmarkService.__new__(IndustryBenchmarkService)
    svc.supabase = _UpsertSupabase(fail_on=2)
    rows = [{"i": i} for i in range(250)]   # 3 batches: 100, 100, 50
    with pytest.raises(RuntimeError):
        svc._upsert(rows)
    # Batch 1 committed, batch 2 raised, batch 3 NEVER attempted.
    assert svc.supabase.counter[0] == 2


# ── Partial-failure: sector '' aggregate not written → not marked fresh ──

class _SelectiveUpsertSupabase:
    """Upsert that raises whenever a batch contains a row for `fail_industry`."""

    def __init__(self, fail_industry):
        self._fail_industry = fail_industry
        self.written_industries = []

    def table(self, _name):
        outer = self

        class _T:
            def upsert(self, batch, on_conflict=None):
                self._batch = batch
                return self

            def execute(self):
                if any(r.get("industry") == outer._fail_industry for r in self._batch):
                    raise RuntimeError(f"boom {outer._fail_industry}")
                outer.written_industries.extend(r.get("industry") for r in self._batch)
                return _Resp([])

        return _T()


@pytest.mark.asyncio
async def test_recompute_ttm_partial_failure_does_not_write_sector_aggregate(monkeypatch):
    svc = IndustryBenchmarkService.__new__(IndustryBenchmarkService)
    svc.supabase = _SelectiveUpsertSupabase(fail_industry="IndA")

    # 1 sector, 2 industries; IndA is processed first and its upsert raises.
    monkeypatch.setattr(svc, "_load_universe", lambda: [
        ("Technology", [("IndA", [("AAA", 1.0)]), ("IndB", [("BBB", 1.0)])]),
    ])

    async def fake_ttm_vals(ticker_caps, sem):
        return {"pe_ratio": [10.0, 11.0, 12.0, 13.0, 14.0]}   # >= MIN_SAMPLE_SIZE

    monkeypatch.setattr(svc, "_industry_ttm_values", fake_ttm_vals)

    summary = await svc.recompute_all_ttm(skip_if_fresh_hours=0)

    # IndA upsert raised → the sector aborts BEFORE the '' aggregate is written,
    # so the sector never looks "fresh" and will be retried in full next run.
    assert "" not in svc.supabase.written_industries
    assert summary["mode"] == "ttm"
