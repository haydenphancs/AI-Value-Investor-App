"""One upstream incident must not spend the month's Sentry quota.

On 2026-09-04 a single day of FMP 402s filed ~5,500 events into the `python-fastapi`
project (`quote` 2,292, `batch-quote` 1,016, `historical-chart/5min` 473 …) and the project
has accepted nothing since — so the tracebacks production emitted every hour for the next
week were invisible, and the outage that silenced the alarm was the one the alarm existed
to report. `_sentry_group_is_flooding` keeps the first N of every distinct failure per hour
and drops the repeats, announcing the drop once so suppression is never silent.

The grouping must be COARSE — a per-symbol storm has a different message every time, so
grouping on the full message would mint one bucket per symbol and cap nothing.
"""

from __future__ import annotations

import logging

import pytest

import app.main as main_mod


@pytest.fixture(autouse=True)
def _fresh_window(monkeypatch):
    main_mod._sentry_group_counts.clear()
    monkeypatch.setattr(main_mod, "_sentry_window_started", 0.0, raising=False)
    yield
    main_mod._sentry_group_counts.clear()


def _event(msg, exc_type="HTTPStatusError", logger_name="app.integrations.fmp"):
    return {"logger": logger_name, "logentry": {"message": msg},
            "exception": {"values": [{"type": exc_type}]}}


def test_a_per_symbol_storm_collapses_into_one_group():
    """The real shape: the message differs per symbol, the failure does not."""
    keys = {main_mod._sentry_group_key(_event(f"FMP API request failed: quote for {s}"))
            for s in ("AAPL", "MSFT", "NVDA", "TSLA")}
    assert len(keys) == 1, keys
    # …but a genuinely different failure keeps its own bucket.
    assert main_mod._sentry_group_key(_event("FMP API request failed: quote for AAPL")) != \
        main_mod._sentry_group_key(_event("Future exception was never retrieved",
                                          exc_type="ValueError", logger_name="asyncio"))


def test_real_production_message_shapes_group_as_the_failures_they_are():
    """Drawn verbatim from the 2026-09-04 Sentry storm and the 2026-09-11 Railway log."""
    real = [
        ("FMP API request failed: quote — Client error '402 Payment Required' for url "
         "'https://financialmodelingprep.com/stable/quote?symbol=AAPL'", "HTTPStatusError", "app.integrations.fmp"),
        ("FMP API request failed: quote — Client error '402 Payment Required' for url "
         "'https://financialmodelingprep.com/stable/quote?symbol=TSLA'", "HTTPStatusError", "app.integrations.fmp"),
        ("warm_ticker_collection failed for ^GSPC: ValueError: No company profile found for ticker: ^GSPC",
         "ValueError", "app.services.ticker_data_cache"),
        ("warm_ticker_collection failed for BTCUSD: ValueError: No company profile found for ticker: BTCUSD",
         "ValueError", "app.services.ticker_data_cache"),
        ("Account deletion failed at the auth step for user=3ce71aaf", "AuthApiError",
         "app.api.v1.endpoints.users"),
        ("Token refresh failed: Signature has expired.", "Exception", "app.api.v1.endpoints.auth"),
    ]
    keys = [main_mod._sentry_group_key(_event(m, t, lg)) for m, t, lg in real]
    assert keys[0] == keys[1], "the 2,292-event FMP 402 storm must be ONE bucket"
    assert keys[2] == keys[3], "the per-symbol prewarm failure must be ONE bucket"
    assert len(set(keys)) == 4, f"four distinct failures expected, got {len(set(keys))}"
    # SNAKE_CASE error codes must never be normalised into each other.
    assert main_mod._sentry_group_key(_event("AUTH_REQUIRED on /x")) != \
        main_mod._sentry_group_key(_event("AUTH_FORBIDDEN on /x"))


def test_the_first_n_of_a_group_pass_then_the_rest_are_dropped(caplog):
    cap = main_mod._SENTRY_GROUP_CAP
    with caplog.at_level(logging.WARNING, logger="app.main"):
        passed = sum(0 if main_mod._sentry_group_is_flooding(
            _event(f"FMP API request failed: quote for SYM{i}")) else 1
            for i in range(cap * 5))
    assert passed == cap, f"expected exactly {cap} to pass, got {passed}"
    announcements = [r for r in caplog.records if "flood cap" in r.getMessage()]
    assert len(announcements) == 1, "the drop must be announced exactly once per window"
    assert str(cap) in announcements[0].getMessage()


def test_a_different_group_keeps_its_own_budget():
    cap = main_mod._SENTRY_GROUP_CAP
    for i in range(cap * 2):
        main_mod._sentry_group_is_flooding(_event(f"noisy {i}"))
    assert main_mod._sentry_group_is_flooding(_event("noisy 999")) is True
    # A genuinely new failure is still reported even while the noisy one is capped.
    assert main_mod._sentry_group_is_flooding(
        _event("Account deletion failed", exc_type="AuthApiError",
               logger_name="app.api.v1.endpoints.users")) is False


def test_the_window_rolls(monkeypatch):
    cap = main_mod._SENTRY_GROUP_CAP
    for i in range(cap + 5):
        main_mod._sentry_group_is_flooding(_event("same failure"))
    assert main_mod._sentry_group_is_flooding(_event("same failure")) is True
    monkeypatch.setattr(main_mod.time, "monotonic",
                        lambda: main_mod._sentry_window_started + main_mod._SENTRY_CAP_WINDOW_SECONDS + 1)
    assert main_mod._sentry_group_is_flooding(_event("same failure")) is False, \
        "a new hour must restore reporting"


def test_a_malformed_event_never_raises():
    """before_send runs inside the SDK; an exception here would lose the event AND the
    error about losing it."""
    for bad in ({}, {"exception": {}}, {"exception": {"values": []}},
                {"logentry": {"message": None}}, {"message": 12345},
                {"logger": None, "exception": {"values": [{}]}}):
        assert main_mod._sentry_group_is_flooding(bad) in (True, False)


def test_before_send_is_wired_to_the_cap():
    """Anti-vacuity: the cap must actually be consulted by the hook Sentry calls."""
    import ast
    src = open(main_mod.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_sentry_before_send")
    body = ast.get_source_segment(src, fn)
    assert "_sentry_group_is_flooding(event)" in body and "return None" in body
    assert body.index("_sentry_group_is_flooding") < body.index("scrub_sentry_event"), \
        "drop before scrubbing — no point scrubbing an event that is about to be discarded"


# ── The UNHANDLED-exception shape, which the tests above never built ─────────────────


def _unhandled(exc: BaseException) -> dict:
    """The real event `FastApiIntegration` produces — via sentry_sdk's own builder, so the
    shape cannot drift away from what production files."""
    from sentry_sdk.utils import event_from_exception

    event, _hint = event_from_exception((type(exc), exc, exc.__traceback__))
    return event


def _raise(fn):
    try:
        fn()
    except Exception as e:          # noqa: BLE001 — we want the traceback attached
        return e
    raise AssertionError("the helper did not raise")


def test_the_unhandled_exception_shape_carries_no_logger_or_message():
    """Anti-vacuity: if this ever gains a `logger`/`logentry`, the guard below is moot."""
    ev = _unhandled(_raise(lambda: 1 / 0))
    assert "logger" not in ev and "logentry" not in ev and "message" not in ev, (
        f"the shape changed: {sorted(ev)}"
    )
    assert ev.get("exception", {}).get("values"), "no exception values to group on"


def test_two_distinct_crashes_of_the_same_type_are_NOT_one_bucket():
    """The key was `exc_type + logger + message`, and an unhandled exception has neither of
    the last two — so it degenerated to "ValueError||" for every ValueError in the process.

    Consequence: after one endpoint filed 20 ValueError 500s in an hour (the NaN /
    `allow_nan=False` class this codebase keeps hitting), a DIFFERENT, previously-unseen
    ValueError crash anywhere else was silently dropped for the rest of the window — and
    the one-per-bucket announcement had already been spent on the unrelated storm.
    """
    def _moat():
        raise ValueError("moat radar divide by zero for NVDA")

    def _etf():
        raise ValueError("could not parse expense ratio from etf info")

    a = main_mod._sentry_group_key(_unhandled(_raise(_moat)))
    b = main_mod._sentry_group_key(_unhandled(_raise(_etf)))
    assert a != b, f"two unrelated crashes share one flood bucket: {a!r}"
    assert a.startswith("ValueError|") and b.startswith("ValueError|")


def test_the_same_crash_repeated_still_collapses_into_one_bucket():
    """The control. Without it the test above passes on a key that never groups anything,
    which would defeat the cap entirely."""
    def _moat(sym):
        raise ValueError(f"moat radar divide by zero for {sym}")

    keys = {
        main_mod._sentry_group_key(_unhandled(_raise(lambda s=s: _moat(s))))
        for s in ("NVDA", "AAPL", "MSFT")
    }
    assert len(keys) == 1, f"a per-symbol storm minted {len(keys)} buckets: {keys}"


def test_an_explicit_null_logentry_does_not_raise_inside_before_send():
    """`.get("logentry", {})` returns None for an explicit null and the chained `.get`
    then raises — inside `before_send`, on the error-reporting path itself."""
    assert isinstance(
        main_mod._sentry_group_key({"logentry": None, "exception": {"values": []}}), str
    )
    assert isinstance(main_mod._sentry_group_key({"exception": {"values": [None]}}), str)

