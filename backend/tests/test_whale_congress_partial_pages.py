"""A paginated congressional fetch must FAIL CLOSED when it loses a page.

`_fetch_congress_pages` fans `house-latest` / `senate-latest` out over up to 30 pages with
`asyncio.gather(..., return_exceptions=True)`. It used to log a failed page and then drop
it, returning a list the caller could not distinguish from a complete one:

    for r in results:
        if isinstance(r, list):
            all_trades.extend(r)
        elif isinstance(r, Exception):
            logger.warning(f"{endpoint} page fetch error: {r}")
    return all_trades

Two consequences, both silent:

1. **Data loss.** The member's trades on that page vanish from the aggregation and
   `_persist` writes the truncated set OVER the good stored snapshot.
2. **Spurious rewrites.** `_congressional_raw_hash` hashes the member's own disclosures
   (see test_whale_congress_hash_stability.py), so a missing page changes the hash and
   forces the full `_persist` write path again on the next sweep — the very churn that
   file exists to stop.

This path was LATENT when found (2026-08-21: zero page-fetch errors observed), which is
exactly why it needs a test rather than a log line.

The contract now:

    every page OK                     → merged list
    some OK, some failed              → FMPPartialPageException (.partial = what arrived)
    all failed, all 403/404           → []   (endpoint not on this plan; honest empty)
    all failed, anything else         → FMPPartialPageException (.partial = [])

Pure logic — no network. Run via `python -m pytest` from backend/.
"""

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.integrations.fmp import (  # noqa: E402
    FMPClient,
    FMPException,
    FMPPartialPageException,
    FMPRateLimitException,
    FMPUnavailableException,
)


# ── Helpers ──────────────────────────────────────────────────────────────────────────

def _rows(page: int, n: int = 3):
    """`n` disclosures tagged with the page they came from, so loss is detectable."""
    return [
        {
            "transactionDate": f"2026-0{(page % 9) + 1}-0{i + 1}",
            "disclosureDate": f"2026-0{(page % 9) + 1}-1{i}",
            "symbol": f"P{page}S{i}",
            "type": "Purchase",
            "amount": "$1,001 - $15,000",
            "owner": "Self",
            "assetDescription": f"Page {page} row {i}",
            "office": "Nancy Pelosi",
            "firstName": "Nancy",
            "lastName": "Pelosi",
        }
        for i in range(n)
    ]


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://financialmodelingprep.com/stable/house-latest")
    return httpx.HTTPStatusError(
        f"{status}", request=req, response=httpx.Response(status, request=req)
    )


def _client(page_behavior) -> FMPClient:
    """FMPClient whose per-page HTTP layer is `page_behavior(page) -> rows | Exception`.

    Patches `_make_request_impl`, NOT `_make_request` — so the real failure-counting
    wrapper still runs and `request_failures` moves exactly as it would in production.
    That counter is what `_hydrate_one` diffs to tell an outage from an empty filer.
    """
    c = FMPClient()

    async def _impl(endpoint, params=None):
        outcome = page_behavior((params or {}).get("page"))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    c._make_request_impl = _impl  # type: ignore[method-assign]
    return c


def _pages(**by_page):
    """`_pages(p0=..., p1=...)` → behavior fn; unnamed pages return []."""
    table = {int(k[1:]): v for k, v in by_page.items()}
    return lambda page: table.get(page, [])


# ── 1. All pages succeed ─────────────────────────────────────────────────────────────

def test_all_pages_succeed_returns_every_row():
    c = _client(_pages(p0=_rows(0), p1=_rows(1), p2=_rows(2), p3=_rows(3)))
    out = asyncio.run(c._fetch_congress_pages("house-latest", 1000))

    assert len(out) == 12
    assert {r["symbol"] for r in out} == {
        f"P{p}S{i}" for p in range(4) for i in range(3)
    }
    assert c.request_failures == 0


def test_all_pages_succeed_but_genuinely_empty_is_still_an_empty_list():
    """An honest empty must NOT be confused with a lost page — it does not raise."""
    c = _client(lambda page: [])
    assert asyncio.run(c._fetch_congress_pages("senate-latest", 1000)) == []
    assert c.request_failures == 0


def test_page_count_follows_the_limit_and_caps_at_30():
    seen = []

    def behavior(page):
        seen.append(page)
        return []

    asyncio.run(_client(behavior)._fetch_congress_pages("house-latest", 1000))
    assert sorted(seen) == [0, 1, 2, 3]            # ceil(1000/250)

    seen.clear()
    asyncio.run(_client(behavior)._fetch_congress_pages("house-latest", 7500))
    assert sorted(seen) == list(range(30))         # ceil(7500/250), capped at 30


def test_degenerate_limits_do_not_divide_or_raise():
    """Boundary inputs on the page-count arithmetic. `limit<=0` means zero pages, which
    must be an empty list, not a spurious "all pages failed"."""
    c = _client(lambda page: _rows(page))
    assert asyncio.run(c._fetch_congress_pages("house-latest", 0)) == []
    assert asyncio.run(c._fetch_congress_pages("house-latest", -5)) == []
    assert len(asyncio.run(c._fetch_congress_pages("house-latest", 1))) == 3  # one page


def test_a_single_page_fetch_that_fails_is_reported_as_partial_not_empty():
    c = _client(lambda page: FMPRateLimitException("429"))
    with pytest.raises(FMPPartialPageException) as ei:
        asyncio.run(c._fetch_congress_pages("house-latest", 1))
    assert (ei.value.pages_failed, ei.value.pages_total) == (1, 1)


# ── 2. One page fails ────────────────────────────────────────────────────────────────

def test_one_failed_page_raises_instead_of_returning_a_short_list():
    c = _client(
        _pages(p0=_rows(0), p1=_rows(1), p2=asyncio.TimeoutError(), p3=_rows(3))
    )
    with pytest.raises(FMPPartialPageException) as ei:
        asyncio.run(c._fetch_congress_pages("house-latest", 1000))

    e = ei.value
    assert e.pages_failed == 1
    assert e.pages_total == 4
    assert e.endpoint == "house-latest"
    # The rows that DID arrive ride along — for display, never for a write.
    assert len(e.partial) == 9
    assert not any(r["symbol"].startswith("P2") for r in e.partial)
    # And the production failure counter moved, so `_hydrate_one`'s outage diff sees it.
    assert c.request_failures == 1


def test_a_lost_page_is_typed_as_transient_upstream_not_a_bug():
    """Subclassing FMPUnavailableException keeps it WARNING-level / off the on-call page,
    and makes every existing `except FMPUnavailableException` handler correct for free."""
    assert issubclass(FMPPartialPageException, FMPUnavailableException)
    assert issubclass(FMPPartialPageException, FMPException)


def test_first_page_lost_still_raises_even_though_later_pages_are_full():
    """Page 0 carries the NEWEST disclosures — losing it silently would age the member's
    snapshot backwards while looking like a complete fetch."""
    c = _client(_pages(p0=_http_status_error(429), p1=_rows(1), p2=_rows(2), p3=_rows(3)))
    with pytest.raises(FMPPartialPageException) as ei:
        asyncio.run(c._fetch_congress_pages("house-latest", 1000))
    assert ei.value.pages_failed == 1
    assert len(ei.value.partial) == 9


def test_a_single_403_among_healthy_pages_is_partial_not_endpoint_unavailable():
    """The quiet-empty escape hatch is for a plan limitation, which fails EVERY page. One
    403 beside three good pages is a lost page, and must not borrow that exemption."""
    c = _client(_pages(p0=_rows(0), p1=_http_status_error(403), p2=_rows(2), p3=_rows(3)))
    with pytest.raises(FMPPartialPageException) as ei:
        asyncio.run(c._fetch_congress_pages("house-latest", 1000))
    assert ei.value.pages_failed == 1


def test_a_non_list_body_counts_as_a_lost_page():
    """FMP sometimes answers an error as a dict. `isinstance(r, list)` skipped it and
    `isinstance(r, Exception)` did not log it — it fell through BOTH arms silently."""
    c = _client(_pages(p0=_rows(0), p1={"Error Message": "Limit Reach"}, p2=_rows(2), p3=_rows(3)))
    with pytest.raises(FMPPartialPageException) as ei:
        asyncio.run(c._fetch_congress_pages("house-latest", 1000))
    assert ei.value.pages_failed == 1
    assert len(ei.value.partial) == 9


def test_several_pages_lost_reports_the_true_count():
    c = _client(
        _pages(p0=_rows(0), p1=FMPRateLimitException("429"), p2=asyncio.TimeoutError(), p3=_rows(3))
    )
    with pytest.raises(FMPPartialPageException) as ei:
        asyncio.run(c._fetch_congress_pages("senate-latest", 1000))
    assert ei.value.pages_failed == 2
    assert ei.value.pages_total == 4
    assert len(ei.value.partial) == 6


# ── 3. All pages fail ────────────────────────────────────────────────────────────────

def test_all_pages_fail_on_rate_limit_raises_with_empty_partial():
    c = _client(lambda page: FMPRateLimitException("quota exhausted"))
    with pytest.raises(FMPPartialPageException) as ei:
        asyncio.run(c._fetch_congress_pages("house-latest", 1000))

    e = ei.value
    assert e.pages_failed == 4 and e.pages_total == 4
    assert e.partial == []
    assert c.request_failures == 4


def test_all_pages_fail_with_403_returns_empty_not_an_exception():
    """A 403/404 on EVERY page is the documented "endpoint not on this plan" case. It has
    always meant honest-empty here; raising for it would put every congressional surface
    into a permanent degraded loop on a downgraded plan."""
    c = _client(lambda page: _http_status_error(403))
    assert asyncio.run(c._fetch_congress_pages("senate-latest", 1000)) == []


def test_all_pages_fail_with_404_returns_empty():
    c = _client(lambda page: _http_status_error(404))
    assert asyncio.run(c._fetch_congress_pages("senate-latest", 1000)) == []


def test_all_pages_fail_with_500_does_not_borrow_the_403_exemption():
    c = _client(lambda page: _http_status_error(500))
    with pytest.raises(FMPPartialPageException):
        asyncio.run(c._fetch_congress_pages("house-latest", 1000))


def test_mixed_403_and_timeout_all_failing_still_raises():
    """Not every failure was a plan limitation, so this is an outage, not an empty feed."""
    c = _client(
        _pages(
            p0=_http_status_error(403), p1=_http_status_error(403),
            p2=asyncio.TimeoutError(), p3=_http_status_error(403),
        )
    )
    with pytest.raises(FMPPartialPageException) as ei:
        asyncio.run(c._fetch_congress_pages("house-latest", 1000))
    assert ei.value.pages_failed == 4
    assert ei.value.partial == []


# ── 4. The public getters must not swallow it back into [] ───────────────────────────

@pytest.mark.parametrize(
    "method,args",
    [
        ("get_senate_latest", (1000,)),
        ("get_house_latest", (1000,)),
        ("get_senate_trades_by_name", ("Nancy Pelosi",)),
        ("get_house_trades_by_name", ("Nancy Pelosi",)),
    ],
)
def test_public_getters_propagate_a_lost_page(method, args):
    """Every one of these ends in `except Exception: return []`. Without an explicit
    re-raise the new exception is swallowed right back into the silent truncation."""
    c = _client(lambda page: _rows(page) if page != 1 else asyncio.TimeoutError())
    with pytest.raises(FMPPartialPageException):
        asyncio.run(getattr(c, method)(*args))


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_senate_latest", (1000,)),
        ("get_house_latest", (1000,)),
        ("get_senate_trades_by_name", ("Nancy Pelosi",)),
        ("get_house_trades_by_name", ("Nancy Pelosi",)),
    ],
)
def test_public_getters_still_return_empty_when_the_endpoint_is_off_plan(method, args):
    c = _client(lambda page: _http_status_error(403))
    assert asyncio.run(getattr(c, method)(*args)) == []


def test_by_name_filtering_still_works_on_a_complete_fetch():
    """The fail-closed change must not disturb the happy path's client-side filter."""
    mine = _rows(0)
    theirs = [dict(r, office="Ted Cruz", firstName="Ted", lastName="Cruz") for r in _rows(1)]
    c = _client(_pages(p0=mine, p1=theirs))
    out = asyncio.run(c.get_house_trades_by_name("Nancy Pelosi", limit=500))
    assert len(out) == 3
    assert all(r["office"] == "Nancy Pelosi" for r in out)


# ── 5. The hydrator skips the whale rather than persisting a gap ─────────────────────

def _hydrator(fmp):
    """A WhaleHydrator without `__init__` — that would open a live Supabase client."""
    from scripts.hydrate_whales import WhaleHydrator

    h = object.__new__(WhaleHydrator)
    h.fmp = fmp
    h.gemini = MagicMock()
    h.force = False
    h.dry_run = False
    h.sb = MagicMock()
    h.stats = {"processed": 0, "skipped": 0, "errors": 0, "no_data": 0, "upstream_failed": 0}
    h._profile_cache = {}
    return h


@pytest.mark.parametrize("chamber", ["house", "senate"])
def test_process_congressional_returns_none_on_a_lost_page(chamber):
    h = _hydrator(_client(lambda page: _rows(page) if page != 2 else asyncio.TimeoutError()))
    out = asyncio.run(h._process_congressional("w-1", "Nancy Pelosi", chamber))

    assert out is None, "a truncated feed must not become a persistable snapshot"
    # And nothing was read or written on the way out — not even the prev-snapshot query,
    # which is the first step of the write path.
    h.sb.table.assert_not_called()


def test_process_congressional_still_builds_a_snapshot_when_every_page_is_healthy():
    """Guards against the fix degenerating into "always return None"."""
    h = _hydrator(_client(_pages(p0=_rows(0), p1=_rows(1))))
    h.sb.table.return_value.select.return_value.eq.return_value.neq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

    out = asyncio.run(h._process_congressional("w-1", "Nancy Pelosi", "house"))

    assert out is not None
    assert out["is_congress"] is True
    assert out["raw_hash"]
    assert out["holdings"]


def test_process_congressional_returns_none_on_a_genuinely_empty_feed():
    """Unchanged behavior — an empty filer and a lost page both skip, but only the lost
    page logs INCOMPLETE, and only the empty one leaves `request_failures` still."""
    fmp = _client(lambda page: [])
    h = _hydrator(fmp)
    assert asyncio.run(h._process_congressional("w-1", "Nancy Pelosi", "house")) is None
    assert fmp.request_failures == 0


# ── 6. Source guards — comment-stripped and brace-bounded ────────────────────────────

def _fn_source(path: Path, signature: str) -> str:
    """One function body, docstrings and comments stripped.

    Stripping is load-bearing: the docstring below quotes the OLD drop-the-page loop
    verbatim to explain it, so an un-stripped scan would match that prose and pass after
    a revert. (Mutation-checked by hand: reverting the loop fails these.)
    """
    src = path.read_text()
    start = src.index(signature)
    nxt = src.index("\n    async def ", start + len(signature))
    body = src[start:nxt]
    body = re.sub(r'"""(?:.|\n)*?"""', "", body)
    body = re.sub(r"(?m)^\s*#.*$", "", body)
    return body


_FMP = Path(__file__).resolve().parents[1] / "app" / "integrations" / "fmp.py"
_HYDRATE = Path(__file__).resolve().parents[1] / "scripts" / "hydrate_whales.py"


def test_fetch_congress_pages_still_raises_rather_than_dropping():
    body = _fn_source(_FMP, "    async def _fetch_congress_pages(")
    assert "raise FMPPartialPageException" in body, (
        "the silent-truncation loop is back — a lost page will be persisted over good data"
    )
    assert "return all_trades" in body    # the every-page-OK path must still exist


def test_hydrator_still_catches_the_partial_exception():
    body = _fn_source(_HYDRATE, "    async def _process_congressional(")
    assert "except FMPPartialPageException" in body


def test_hydrators_partial_branch_only_logs_and_returns():
    """Bound the HANDLER, not the whole function: `len(e.partial)` in the log line is
    fine, aggregating those rows is the data loss. Scanning the function as a whole
    cannot tell the two apart."""
    body = _fn_source(_HYDRATE, "    async def _process_congressional(")
    handler = body[body.index("except FMPPartialPageException"):]
    handler = handler[: handler.index("return None") + len("return None")]

    assert "_aggregate_congressional" not in handler, (
        "the partial rows are being aggregated — that is exactly the truncation"
    )
    assert "raw_trades" not in handler, (
        "the partial rows are being bound to raw_trades and will flow into _persist"
    )
    assert "self.sb" not in handler, "the skip path must not touch the database"
