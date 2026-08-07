"""`.single()` made every not-found branch in research.py dead code.

postgrest 1.1.1's `SyncSingleRequestBuilder.execute()` raises `APIError` for ANY non-2xx
(request_builder.py:126), and `.single()` sets `Accept: application/vnd.pgrst.object+json`, which
makes PostgREST answer zero rows with 406/PGRST116. So `result.data` was never reached on a miss:

    result = supabase.table(...).eq("id", report_id).single().execute()
    if not result.data:                      # <- unreachable
        return make_error_response(ErrorCode.REPORT_NOT_FOUND, ...)

Every one of those guards was dead, and a report the user had deleted — or an id from another
account — produced a bare 500 "An internal server error occurred" from the global handler instead
of the structured 404 the iOS client is written against. `TickerReportViewModel` now falls through
to a BILLABLE regeneration only on the documented not-found codes, so a 500 there means the paid
path shows an error instead of silently recovering: the two changes have to agree.

`maybe_single()` is the library's purpose-built fix — same Accept header, but it catches exactly
the "contains 0 rows" APIError and returns None (request_builder.py:131-137).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/research.py"


def _source() -> str:
    if not _SRC.exists():
        pytest.skip(f"{_SRC} not present")
    return _SRC.read_text()


def test_no_bare_single_survives_in_research_endpoints():
    """`.single()` raises rather than returning empty data, so a not-found branch after one is
    unreachable by construction."""
    src = _source()
    bare = [
        i for i, line in enumerate(src.splitlines(), 1)
        if ".single()" in line and ".maybe_single()" not in line
    ]
    assert not bare, (
        f"bare .single() at line(s) {bare} — it raises APIError on zero rows, so the "
        f"`if not result.data` guard below it can never run and the caller gets a 500 "
        f"instead of REPORT_NOT_FOUND. Use .maybe_single()."
    )


def test_the_not_found_guards_are_none_safe():
    """`maybe_single()` returns None (not an empty response) for zero rows, so a guard that
    goes straight to `.data` swaps one AttributeError-500 for another."""
    src = _source()
    offenders = []
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped == "if not result.data:":
            offenders.append(i)
    assert not offenders, (
        f"line(s) {offenders} dereference `.data` on a maybe_single() response that can be "
        f"None. Use `if not result or not result.data:`."
    )


def test_every_maybe_single_is_followed_by_a_none_check():
    """Anti-vacuity + completeness: each call site must actually guard, not just avoid the
    two shapes above."""
    src = _source()
    lines = src.splitlines()
    sites = [i for i, line in enumerate(lines) if ".maybe_single()" in line]
    assert len(sites) >= 5, f"expected the known call sites, found {len(sites)}"

    for i in sites:
        window = "\n".join(lines[i: i + 8])
        guarded = (
            "if not result or not result.data" in window
            or "row.data if row else None" in window
            or "result.data if result else None" in window
        )
        assert guarded, (
            f"maybe_single() at line {i + 1} is not followed by a None-safe guard:\n{window}"
        )


def test_the_report_detail_route_still_returns_a_structured_404():
    """iOS `TickerReportViewModel.reportIsGenuinelyUnavailable` treats REPORT_NOT_FOUND as a
    licence to fall through to a 20-credit regeneration, and everything else as an error. A
    500 here would surface as an error rather than a silent charge — correct, but the user
    loses their report, so the structured code has to survive."""
    src = _source()
    fn = src[src.index("async def get_research_ticker_report("):]
    fn = fn[: fn.index("\n@router")]
    assert "ErrorCode.REPORT_NOT_FOUND" in fn
    assert "ErrorCode.REPORT_NOT_READY" in fn
    assert "ErrorCode.DATA_INCOMPLETE" in fn


def test_postgrest_still_behaves_the_way_this_fix_assumes():
    """Pin the library contract the fix rests on. If a future postgrest makes `single()`
    return empty data instead of raising, this whole file becomes unnecessary — and this test
    is how you find out, rather than by reading five endpoints again."""
    import inspect

    from postgrest._sync.request_builder import (
        SyncMaybeSingleRequestBuilder,
        SyncSingleRequestBuilder,
    )

    single_src = inspect.getsource(SyncSingleRequestBuilder.execute)
    assert "raise APIError" in single_src, (
        "postgrest's single() no longer raises — re-evaluate whether maybe_single() is needed"
    )

    maybe_src = inspect.getsource(SyncMaybeSingleRequestBuilder.execute)
    assert "The result contains 0 rows" in maybe_src and "return None" in maybe_src, (
        "maybe_single() no longer swallows the zero-rows error — the None guards may be wrong"
    )
