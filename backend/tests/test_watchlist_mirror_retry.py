"""The watchlist → active-group mirror is RETRIED, because nothing repairs it afterwards.

`_write_through_to_active_portfolio` degraded any failure to a WARNING that ended
"GET /portfolios will backfill it". That premise was false: `_backfill_lone_empty_portfolio`
runs only for a user with EXACTLY ONE group holding ZERO items that was never edited — a
single mirrored ticker disarms it for good (`if only.items: return`) — and there is no other
reconciler of `watchlist_items` → `portfolio_items`. Every non-Tracking add path (the
detail-screen star, Updates → Manage Assets, onboarding) relies solely on this mirror, so one
transient Supabase 520 on the upsert stranded the ticker: on the watchlist (star filled, so
the star could not re-add it) but in no group, hence invisible on Home, Updates AND Tracking,
permanently.

The fix wraps the idempotent sequence in `retry_idempotent_sync` and makes the log honest.
Pinned here:
  * a transient first-attempt failure is retried and the row LANDS;
  * a deterministic failure (23505) is NOT retried, and never raises;
  * retries exhausted still never fails the add, and the log says there is no repair;
  * the "will backfill" claim is gone from the source;
  * the premise — a lone group holding one mirrored ticker is NOT backfilled — still holds,
    so the retry is load-bearing and not belt-and-braces.

No network / Supabase. `time.sleep` in the retry helper is patched out.
"""
from __future__ import annotations

import ast
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.api.v1.endpoints.portfolios as pf
import app.api.v1.endpoints.watchlist as wl
import app.utils.supabase_errors as supabase_errors

_USER = "acct-1"


class _Edge520(Exception):
    """Shaped like postgrest's APIError on a Cloudflare edge page: an INT `.code`."""

    def __init__(self):
        super().__init__("JSON could not be generated")
        self.code = 520


class _Dup23505(Exception):
    def __init__(self):
        super().__init__('duplicate key value violates unique constraint "x"')
        self.code = "23505"


class _Q:
    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self._op, self._payload, self._filters, self._limit = "select", None, {}, None

    def select(self, *_a): self._op = "select"; return self
    def eq(self, c, v): self._filters[c] = v; return self
    def limit(self, n): self._limit = n; return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        self._op, self._payload = "upsert", payload
        return self

    def execute(self):
        rows = self.sb.store.setdefault(self.table, [])
        if self._op == "upsert":
            self.sb.upsert_attempts += 1
            if self.sb.upsert_failures:
                raise self.sb.upsert_failures.pop(0)
            p = self._payload
            if not any(r.get("portfolio_id") == p["portfolio_id"] and r.get("ticker") == p["ticker"]
                       for r in rows):
                rows.append(dict(p))
            return type("R", (), {"data": []})()
        if self.table == "portfolios" and self.sb.select_failures:
            self.sb.select_attempts += 1
            raise self.sb.select_failures.pop(0)
        matched = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        if self._limit is not None:
            matched = matched[: self._limit]
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _SB:
    def __init__(self, *, upsert_failures=(), select_failures=()):
        self.store = {
            "portfolios": [{"id": "g1", "user_id": _USER, "name": "Holdings", "is_active": True}],
            "portfolio_items": [{"portfolio_id": "g1", "ticker": "AAPL", "position": 0}],
        }
        self.upsert_failures = list(upsert_failures)
        self.select_failures = list(select_failures)
        self.upsert_attempts = 0
        self.select_attempts = 0

    def table(self, name):
        return _Q(self, name)

    def rpc(self, *_a, **_k):  # never reached: the group is active
        raise AssertionError("ensure_active_portfolio must not be called when a group is active")

    def items(self):
        return sorted(r["ticker"] for r in self.store["portfolio_items"])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The retry helper looks `time.sleep` up on its module at call time (its own comment
    says so), so this patch removes the 0.25 s + 0.5 s backoff from the test."""
    monkeypatch.setattr(supabase_errors.time, "sleep", lambda _s: None)


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_fake_really_fails_the_first_upsert():
    sb = _SB(upsert_failures=[_Edge520()])
    with pytest.raises(_Edge520):
        sb.table("portfolio_items").upsert(
            {"portfolio_id": "g1", "ticker": "IRDM", "position": 1}).execute()
    assert sb.items() == ["AAPL"]


def test_a_520_int_code_is_classified_transient_and_23505_is_not():
    """If the classifier changed, every retry assertion below would pass or fail for the
    wrong reason."""
    assert supabase_errors.is_transient_supabase_error(_Edge520()) is True
    assert supabase_errors.is_transient_supabase_error(_Dup23505()) is False


# ── the retry ────────────────────────────────────────────────────────────────

def test_a_transient_first_attempt_is_retried_and_the_row_lands():
    """The reported failure: one Cloudflare 520 on the upsert. The row must land."""
    sb = _SB(upsert_failures=[_Edge520()])

    wl._write_through_to_active_portfolio(sb, _USER, "IRDM")

    assert sb.items() == ["AAPL", "IRDM"], "the mirror gave up on the first blip"
    assert sb.upsert_attempts == 2


def test_two_transient_failures_are_still_recovered():
    """Boundary: attempts = 3, so two failures then success is the last chance."""
    sb = _SB(upsert_failures=[_Edge520(), _Edge520()])
    wl._write_through_to_active_portfolio(sb, _USER, "IRDM")
    assert sb.items() == ["AAPL", "IRDM"]
    assert sb.upsert_attempts == 3


def test_a_transient_failure_on_the_active_group_READ_is_retried_too():
    """The whole sequence is inside the retry, not just the upsert: a blip on the first
    read used to abandon the mirror just the same."""
    sb = _SB(select_failures=[_Edge520()])
    wl._write_through_to_active_portfolio(sb, _USER, "IRDM")
    assert sb.items() == ["AAPL", "IRDM"]
    assert sb.select_attempts == 1 and sb.upsert_attempts == 1


def test_a_deterministic_failure_is_not_retried_and_never_raises(caplog):
    """23505 is `is_unique_violation`, deliberately disjoint from transient: retrying a
    constraint violation is a correctness hazard. One attempt, no raise, an ERROR line."""
    sb = _SB(upsert_failures=[_Dup23505()])
    with caplog.at_level(logging.WARNING):
        wl._write_through_to_active_portfolio(sb, _USER, "IRDM")  # must not raise
    assert sb.upsert_attempts == 1
    assert sb.items() == ["AAPL"]
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR and "IRDM" in r.getMessage()]
    assert errs, "a non-transient mirror failure must be an ERROR with the ticker + user"
    assert _USER in errs[0].getMessage()


def test_retries_exhausted_never_fails_the_add_and_says_there_is_no_repair(caplog):
    """Three transient failures in a row: the watchlist row is already committed, so the
    add must still succeed — but the log must no longer promise a backfill that does not
    exist, and must name the only recovery (re-add from Tracking)."""
    sb = _SB(upsert_failures=[_Edge520(), _Edge520(), _Edge520()])
    with caplog.at_level(logging.WARNING):
        wl._write_through_to_active_portfolio(sb, _USER, "IRDM")  # must not raise
    assert sb.upsert_attempts == 3
    assert sb.items() == ["AAPL"]
    final = [r for r in caplog.records if "IRDM" in r.getMessage() and "retries" in r.getMessage()]
    assert final, "the exhausted-retries outcome must be logged with the ticker"
    msg = final[-1].getMessage()
    assert final[-1].levelno == logging.WARNING, "a transient that outlived the retries is WARNING"
    assert "backfill" not in msg.lower(), "the false 'GET /portfolios will backfill it' claim is back"
    assert "re-add" in msg.lower() and "Tracking" in msg


def test_a_re_add_of_a_mirrored_ticker_is_still_a_no_op():
    """Negative control: `ignore_duplicates` semantics survive the wrapping — the existing
    row keeps its position and nothing is duplicated."""
    sb = _SB()
    wl._write_through_to_active_portfolio(sb, _USER, "AAPL")
    assert sb.items() == ["AAPL"]
    assert sb.upsert_attempts == 1


# ── source guards ────────────────────────────────────────────────────────────

def _mirror_fn() -> ast.FunctionDef:
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints"
           / "watchlist.py").read_text(encoding="utf-8")
    return next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "_write_through_to_active_portfolio")


def test_the_mirror_goes_through_the_idempotent_retry_helper():
    fn = _mirror_fn()
    called = {getattr(n.func, "id", None) or getattr(n.func, "attr", None)
              for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "retry_idempotent_sync" in called, (
        "the mirror is no longer retried — one transient 520 strands the ticker for good"
    )


def test_the_mirror_no_longer_claims_a_backfill_will_repair_it():
    """The LOG format strings only — what an operator reads at 2am. (The docstring quotes
    the old sentence on purpose, to say it was false, so it is excluded.) A comment cannot
    satisfy or fail this: only string constants inside `logger.*(...)` calls are read."""
    fn = _mirror_fn()
    log_strings = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and getattr(n.func.value, "id", None) == "logger":
            log_strings += [c.value for c in ast.walk(n)
                            if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    # The exhausted-retries message is built in a `detail = (...)` name first; read those too.
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "detail" for t in n.targets):
            log_strings += [c.value for c in ast.walk(n.value)
                            if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    assert log_strings, "guard is stale — no logger calls found in the mirror"
    joined = " ".join(log_strings)
    assert "will backfill" not in joined, "the false 'GET /portfolios will backfill it' line is back"
    assert "NO automatic repair" in joined


# ── the premise: nothing else heals a mirrored-but-partial group ─────────────

def test_a_lone_group_holding_one_mirrored_ticker_is_not_backfilled():
    """Why the retry is load-bearing. A never-edited lone group with ONE item next to a
    three-ticker watchlist must be left alone by `_backfill_lone_empty_portfolio` (the
    narrow scope is deliberate — see its docstring), so a failed mirror has no repair."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    group = pf.PortfolioResponse(
        id="g1", name="Holdings", sort_order=0,
        items=[pf.PortfolioItemResponse(ticker="NVDA")],
        created_at=now, updated_at=now, is_active=True,
    )

    class _Never:
        def table(self, _n):
            raise AssertionError("the backfill must not even read the watchlist here")

    out = pf._backfill_lone_empty_portfolio(_Never(), _USER, [group])
    assert out == [group]
