"""The earnings pass must cap WATCHED symbols, not the market-wide calendar.

`get_earnings_calendar` returns every company reporting in a four-day window — hundreds on
a heavy day. The pass sliced that to `MAX_SYMBOLS_PER_PASS` BEFORE asking who was watching,
so whether a user heard about their own holding came down to where FMP happened to place it
in the response — and `run.success = True` then stamped the day complete, so nothing
retried it (found 2026-09-12).
"""

from __future__ import annotations

import pytest

import app.services.notification_senders.earnings_sender as es


def test_only_watched_rows_survive_the_filter():
    rows = [("AAPL", "2026-09-13", "bmo"), ("ZZZZ", "2026-09-13", "amc"),
            ("msft", "2026-09-13", "bmo")]
    assert es._only_watched(rows, {"AAPL", "MSFT"}) == [rows[0], rows[2]], \
        "the filter must be case-insensitive and preserve the selector's order"
    assert es._only_watched(rows, set()) == []
    assert es._only_watched([], {"AAPL"}) == []


def test_a_read_failure_fails_the_pass_so_the_day_is_retried(monkeypatch):
    """An unreadable watchlist is not evidence that a symbol is unwatched — AND it must
    not look like a successful run of zero.

    Returning an empty set here notified nobody (correct) but let the caller fall through
    to `run.success = True`, which stamps `run_day` and stops every later hourly wake from
    retrying. One transient 520 therefore cost every user their whole day of earnings
    notifications. Raising is what `run_earnings_notifications`'s own docstring calls the
    retry mechanism.
    """
    class _SB:
        def rpc(self, *a, **k):
            raise RuntimeError("permission denied")

    monkeypatch.setattr("app.database.get_supabase", lambda: _SB())
    with pytest.raises(RuntimeError):
        es._watched_tickers()


@pytest.mark.asyncio
async def test_a_watched_read_failure_does_not_stamp_the_day_complete(monkeypatch):
    """End-to-end: the pass must leave `run.success` False so `finish()` does not stamp
    `run_day`, and it must dispatch nothing."""
    import contextlib
    import app.services.notification_senders.earnings_sender as mod

    class _Run:
        def __init__(self):
            self.success = False
            self.notified = 0
            self.cursor = None
            self.error = None

    run = _Run()

    @contextlib.asynccontextmanager
    async def _claim(_job):
        yield run

    class _FMP:
        async def get_earnings_calendar(self, **kw):
            # A heavy reporting day — the pass has real work it is about to lose.
            return [
                {"symbol": "AAPL", "date": "2026-09-13", "epsActual": None},
                {"symbol": "NVDA", "date": "2026-09-12", "epsActual": 1.2,
                 "epsEstimated": 1.0},
            ]

    dispatched = []

    async def _no_dispatch(**kw):
        dispatched.append(kw)
        return 1

    monkeypatch.setattr(mod, "claimed_job", _claim)
    monkeypatch.setattr(mod, "get_fmp_client", lambda: _FMP())
    monkeypatch.setattr(mod, "_dispatch", _no_dispatch)

    class _SB:
        def rpc(self, *a, **k):
            raise RuntimeError("520 from the edge")

    monkeypatch.setattr("app.database.get_supabase", lambda: _SB())

    with pytest.raises(RuntimeError):
        await mod.run_earnings_notifications()

    assert run.success is False, (
        "a failed watched-ticker read stamped the ET day COMPLETE — every later wake "
        "that day returns at `if run is None` and nobody is ever notified"
    )
    assert dispatched == [], "nothing may be sent when the watched set is unknown"


def test_the_watched_set_is_upper_cased(monkeypatch):
    class _SB:
        def rpc(self, name, params):
            assert name == "get_top_watchlist_tickers"
            return type("E", (), {"execute": lambda _s: type("R", (), {
                "data": [{"ticker": "aapl"}, {"ticker": "MSFT"}, {"ticker": None}]})()})()

    monkeypatch.setattr("app.database.get_supabase", lambda: _SB())
    assert es._watched_tickers() == {"AAPL", "MSFT"}


def test_the_cap_is_applied_after_the_filter_not_before():
    """The ordering that is the whole bug: with 500 companies reporting and 2 watched,
    both watched ones must be notified."""
    import ast
    import inspect
    import re

    src = inspect.getsource(es)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and "run_earnings" in n.name)
    body = ast.get_source_segment(src, fn)
    code = "\n".join(re.sub(r"#.*$", "", l) for l in body.splitlines())
    filt = code.index("_only_watched(select_upcoming")
    cap = code.index("[:MAX_SYMBOLS_PER_PASS]")
    assert filt < cap, (
        "the market-wide calendar is still truncated before anyone checks who is watching"
    )
    assert "select_upcoming(rows, today)[:MAX_SYMBOLS_PER_PASS]" not in code


def test_a_truncated_watched_set_is_announced():
    """Brace-bounded and comment-stripped, per `.claude/rules/testing.md` §3.

    This read `"per-pass cap is" in src and "logger.warning" in src` over the WHOLE
    module. `logger.warning` appears independently in `_watched_tickers`, so that half was
    satisfied unconditionally, and the other half is a substring a COMMENT next to the fix
    satisfies just as well — the canonical vacuity this repo keeps getting bitten by.
    Replacing the call with `pass  # the per-pass cap is applied here` kept it green.
    """
    import ast
    import inspect
    import re

    src = inspect.getsource(es)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "run_earnings_notifications")
    body = ast.get_source_segment(src, fn)
    assert body, "could not isolate run_earnings_notifications"
    code = "\n".join(re.sub(r"#.*$", "", line) for line in body.splitlines())

    # The announcement must be a real WARNING CALL inside this function, guarded on the
    # cap actually having bitten — not a string that happens to live in the file.
    warns = [
        n for n in ast.walk(ast.parse(ast.get_source_segment(src, fn)))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == "warning"
    ]
    texts = [
        a.value for w in warns for a in w.args
        if isinstance(a, ast.Constant) and isinstance(a.value, str)
    ]
    assert any("per-pass cap" in t for t in texts), (
        "the truncation is not announced by a logger.warning INSIDE "
        "run_earnings_notifications — silently dropping watched companies is how this "
        "went unnoticed"
    )
    assert "MAX_SYMBOLS_PER_PASS" in code
