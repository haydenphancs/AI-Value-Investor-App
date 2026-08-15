"""Classification mapping + backfill for `watchlist_items`.

Guards the defect where `GET /api/v1/tracking/assets` reported `"sector": null` for every
watchlist-added ticker: `POST /api/v1/watchlist` fetched the full FMP company profile and
then read only `companyName`/`image` off it, discarding sector/industry/country/marketCap/
beta, and nothing downstream ever filled them in.

Two halves, both pinned here:
  1. `classification_from_profile` — the shared profile → column mapping, including the
     omission rule that keeps a partial FMP response from blanking stored enrichment.
  2. `TrackingService._backfill_classification` — heals rows already stored NULL from the
     shared `company_profile_cache`, so the fix needs no migration.
"""

import asyncio
from typing import Any, Dict, List

import pytest

from app.services._classification_common import classification_from_profile


# ── 1. The mapping ──────────────────────────────────────────────────────────────

FULL_PROFILE = {
    "companyName": "Archer Aviation Inc.",
    "image": "https://images.financialmodelingprep.com/symbol/ACHR.png",
    "sector": "Industrials",
    "industry": "Aerospace & Defense",
    "country": "US",
    "marketCap": 5028538760.0,
    "beta": 2.41,
}


def test_a_full_profile_maps_every_classification_column():
    assert classification_from_profile(FULL_PROFILE) == {
        "sector": "Industrials",
        "industry": "Aerospace & Defense",
        "country": "US",
        "market_cap": 5028538760.0,
        "beta": 2.41,
    }


def test_company_name_and_image_are_not_classification_columns():
    """They belong to the caller's own payload; leaking them here would double-write."""
    out = classification_from_profile(FULL_PROFILE)
    assert "company_name" not in out and "companyName" not in out
    assert "logo_url" not in out and "image" not in out


@pytest.mark.parametrize("profile", [None, {}, [], "not-a-dict", 0])
def test_a_missing_or_malformed_profile_degrades_to_empty(profile):
    """Never raises: every caller is on a request path where a bad profile must degrade."""
    assert classification_from_profile(profile) == {}


def test_unresolved_fields_are_OMITTED_never_mapped_to_none():
    """The omission rule.

    Callers splat this into an insert/upsert payload. A key resolved to None would
    overwrite good stored enrichment on a re-add whose FMP fetch partially failed, and
    would defeat the `country` column's 'US' default — the "$0.00 / Other-sector after
    re-add" bug.
    """
    out = classification_from_profile({"sector": "Technology"})
    assert out == {"sector": "Technology"}
    assert None not in out.values()


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_strings_are_treated_as_unknown_not_persisted(blank):
    """FMP sends "" and whitespace for unknowns; storing those makes a row look classified."""
    out = classification_from_profile(
        {"sector": blank, "industry": blank, "country": blank}
    )
    assert out == {}


def test_legacy_mktCap_key_is_read_when_marketCap_is_absent():
    assert classification_from_profile({"mktCap": 1234.0})["market_cap"] == 1234.0


def test_marketCap_wins_over_legacy_mktCap_when_both_present():
    out = classification_from_profile({"marketCap": 999.0, "mktCap": 111.0})
    assert out["market_cap"] == 999.0


def test_a_zero_market_cap_is_kept_not_swallowed_by_falsiness():
    """`profile.get("marketCap") or profile.get("mktCap")` silently dropped 0.0."""
    assert classification_from_profile({"marketCap": 0.0})["market_cap"] == 0.0


def test_a_zero_beta_is_kept():
    """Genuinely 0.0 for some instruments — must test against None, not falsiness."""
    assert classification_from_profile({"beta": 0.0})["beta"] == 0.0


def test_a_negative_beta_is_kept():
    assert classification_from_profile({"beta": -0.35})["beta"] == -0.35


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), "abc", "", None, [], {}]
)
def test_non_finite_and_unparseable_numerics_are_dropped(bad):
    """Postgres `numeric` refuses NaN and `inf` serializes to invalid JSON.

    An unguarded `float(...)` turned one weird ticker into a 500 on a hot read path.
    """
    out = classification_from_profile({"marketCap": bad, "beta": bad})
    assert "market_cap" not in out
    assert "beta" not in out


def test_booleans_are_rejected_as_numerics():
    """`float(True) == 1.0` would persist a market cap of $1 for a malformed row."""
    out = classification_from_profile({"marketCap": True, "beta": False})
    assert "market_cap" not in out and "beta" not in out


def test_numeric_strings_are_accepted():
    """FMP has served numbers as strings on some cached shapes."""
    out = classification_from_profile({"marketCap": "5028538760", "beta": "2.41"})
    assert out["market_cap"] == 5028538760.0
    assert out["beta"] == 2.41


def test_the_result_is_a_plain_writable_dict():
    """Callers do `data.update(...)`; a mapping proxy or shared instance would surprise."""
    a = classification_from_profile(FULL_PROFILE)
    b = classification_from_profile(FULL_PROFILE)
    a["sector"] = "MUTATED"
    assert b["sector"] == "Industrials"


# ── 2. The backfill ─────────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, store: "_FakeSupabase", table: str):
        self._s, self._t, self._filters = store, table, {}
        self._payload: Dict[str, Any] = {}
        self._op = "select"

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, _col, vals):
        self._filters["__in__"] = [v.upper() for v in vals]
        return self

    def execute(self):
        if self._t == "company_profile_cache":
            if self._s.profile_read_raises:
                raise RuntimeError("cloudflare 520")
            wanted = set(self._filters.get("__in__", []))
            return type("R", (), {"data": [
                r for r in self._s.profiles if r["ticker"].upper() in wanted
            ]})()
        if self._op == "update":
            if self._s.write_raises:
                raise RuntimeError("permission denied for table watchlist_items")
            self._s.writes.append((self._filters.get("ticker"), dict(self._payload)))
        return type("R", (), {"data": []})()


class _FakeSupabase:
    def __init__(self, profiles: List[Dict[str, Any]]):
        self.profiles = profiles
        self.writes: List[Any] = []
        self.profile_read_raises = False
        self.write_raises = False

    def table(self, name):
        return _FakeQuery(self, name)


def _run_backfill(watchlist, profiles, *, read_raises=False, write_raises=False):
    from app.services import tracking_service as ts

    fake = _FakeSupabase(profiles)
    fake.profile_read_raises = read_raises
    fake.write_raises = write_raises
    original = ts.get_supabase
    ts.get_supabase = lambda: fake
    try:
        svc = ts.TrackingService.__new__(ts.TrackingService)  # skip FMP client init
        asyncio.run(svc._backfill_classification("user-1", watchlist))
    finally:
        ts.get_supabase = original
    return fake


def test_backfill_heals_a_null_sector_from_the_shared_profile_cache():
    wl = [{"ticker": "JOBY", "user_id": "user-1", "sector": None, "country": None}]
    fake = _run_backfill(wl, [
        {"ticker": "JOBY", "profile_json": {"sector": "Industrials", "country": "US"}}
    ])
    assert wl[0]["sector"] == "Industrials", "must mutate in place so THIS request is healed"
    assert wl[0]["country"] == "US"
    assert fake.writes == [("JOBY", {"sector": "Industrials", "country": "US"})]


def test_backfill_does_not_overwrite_an_existing_country():
    """`country` has a 'US' default, so a stored value is real data."""
    wl = [{"ticker": "SHOP", "user_id": "user-1", "sector": None, "country": "CA"}]
    fake = _run_backfill(wl, [
        {"ticker": "SHOP", "profile_json": {"sector": "Technology", "country": "US"}}
    ])
    assert wl[0]["country"] == "CA"
    assert fake.writes == [("SHOP", {"sector": "Technology"})]


def test_backfill_skips_rows_that_are_already_classified():
    wl = [{"ticker": "ORCL", "user_id": "user-1", "sector": "Technology"}]
    fake = _run_backfill(wl, [
        {"ticker": "ORCL", "profile_json": {"sector": "SOMETHING ELSE"}}
    ])
    assert wl[0]["sector"] == "Technology"
    assert fake.writes == []


def test_backfill_is_a_noop_on_an_empty_watchlist():
    fake = _run_backfill([], [])
    assert fake.writes == []


@pytest.mark.parametrize("profile_json", [None, {}, {"sector": ""}, {"sector": "   "}])
def test_backfill_leaves_the_row_null_when_the_cache_has_nothing_usable(profile_json):
    wl = [{"ticker": "XYZ", "user_id": "user-1", "sector": None}]
    fake = _run_backfill(wl, [{"ticker": "XYZ", "profile_json": profile_json}])
    assert wl[0]["sector"] is None
    assert fake.writes == []


def test_backfill_tolerates_a_ticker_absent_from_the_cache():
    wl = [{"ticker": "NEWCO", "user_id": "user-1", "sector": None}]
    fake = _run_backfill(wl, [])
    assert wl[0]["sector"] is None
    assert fake.writes == []


def test_a_profile_cache_read_failure_degrades_and_never_raises():
    """Cosmetic enrichment on a hot read path must not 500 the whole tracking feed."""
    wl = [{"ticker": "JOBY", "user_id": "user-1", "sector": None}]
    fake = _run_backfill(wl, [
        {"ticker": "JOBY", "profile_json": {"sector": "Industrials"}}
    ], read_raises=True)
    assert wl[0]["sector"] is None
    assert fake.writes == []


def test_a_write_back_failure_still_serves_the_healed_value_for_this_request():
    """The in-memory patch is the user-visible half; the write is only an optimisation."""
    wl = [{"ticker": "JOBY", "user_id": "user-1", "sector": None}]
    _run_backfill(wl, [
        {"ticker": "JOBY", "profile_json": {"sector": "Industrials"}}
    ], write_raises=True)
    assert wl[0]["sector"] == "Industrials"


def test_backfill_scopes_the_write_to_the_requesting_user():
    """`watchlist_items` is partitioned per user/install; an unscoped update is a leak."""
    wl = [{"ticker": "JOBY", "user_id": "user-1", "sector": None}]

    from app.services import tracking_service as ts
    fake = _FakeSupabase([{"ticker": "JOBY", "profile_json": {"sector": "Industrials"}}])
    seen = {}
    original_table = fake.table

    def _table(name):
        q = original_table(name)
        original_execute = q.execute

        def _execute():
            if q._op == "update":
                seen.update(q._filters)
            return original_execute()
        q.execute = _execute
        return q

    fake.table = _table
    original = ts.get_supabase
    ts.get_supabase = lambda: fake
    try:
        svc = ts.TrackingService.__new__(ts.TrackingService)
        asyncio.run(svc._backfill_classification("user-1", wl))
    finally:
        ts.get_supabase = original

    assert seen.get("user_id") == "user-1"
    assert seen.get("ticker") == "JOBY"


def test_backfill_deduplicates_tickers_before_querying():
    """Two portfolios can hold the same ticker; the IN list must not repeat it."""
    wl = [
        {"ticker": "JOBY", "user_id": "user-1", "sector": None},
        {"ticker": "JOBY", "user_id": "user-1", "sector": None},
    ]
    from app.services import tracking_service as ts
    fake = _FakeSupabase([{"ticker": "JOBY", "profile_json": {"sector": "Industrials"}}])
    captured = []
    original_table = fake.table

    def _table(name):
        q = original_table(name)
        original_in = q.in_

        def _in(col, vals):
            if name == "company_profile_cache":
                captured.append(list(vals))
            return original_in(col, vals)
        q.in_ = _in
        return q

    fake.table = _table
    original = ts.get_supabase
    ts.get_supabase = lambda: fake
    try:
        svc = ts.TrackingService.__new__(ts.TrackingService)
        asyncio.run(svc._backfill_classification("user-1", wl))
    finally:
        ts.get_supabase = original

    assert captured and len(captured[0]) == len(set(captured[0])) == 1
    assert all(item["sector"] == "Industrials" for item in wl)
