"""
Unit tests for the P0-A context-snapshot persistence helpers (chat endpoints) and the
P0-B live-quote grounding line (chat_service).

P0-A — a stock-detail chat grounds on the on-screen snapshot iOS sends in
`request.context`. The STOCK resolver branch is a no-op, so that snapshot is used
transiently and dropped; on a HISTORY REOPEN iOS sends no context and the chat loses
the exact data the user saw. `_effective_context` / `_persist_context_snapshot`
persist it on the session (migration 087) and replay it on reopen.

P0-B — the streamed path renders the deterministic stock-chart card but never fed its
quote to the model, so prose could drift from the card. `_widget_grounding_line` folds
the live quote into the system instruction, guarding the degraded (`or 0`) / NaN cases.

No network / Supabase — the persist test uses a fake client that records calls. Each
helper must NEVER raise on the failure paths (a broken snapshot write can't break a turn;
a degraded widget can't assert a bogus "$0").
"""

import math

import pytest

from app.api.v1.endpoints.chat import _effective_context, _persist_context_snapshot
from app.services.chat_service import ChatService


# ── _effective_context truth table ─────────────────────────────────────────

def test_effective_context_live_overrides_stored():
    # A live turn (iOS ships the fresh snapshot) beats the persisted one.
    assert _effective_context("LIVE", {"context_snapshot": "STORED"}) == "LIVE"


def test_effective_context_reopen_falls_back_to_stored():
    # A history reopen sends no context → replay the persisted snapshot.
    assert _effective_context(None, {"context_snapshot": "STORED"}) == "STORED"


def test_effective_context_both_absent_is_none():
    assert _effective_context(None, {}) is None


def test_effective_context_empty_request_falls_back():
    # Empty-string context is "no snapshot", not a snapshot of "" — fall back.
    assert _effective_context("", {"context_snapshot": "STORED"}) == "STORED"


def test_effective_context_empty_stored_is_none():
    # A stored empty string must not masquerade as grounding.
    assert _effective_context(None, {"context_snapshot": ""}) is None
    assert _effective_context("", {"context_snapshot": ""}) is None


def test_effective_context_missing_column_pre_migration():
    # Pre-migration rows have no context_snapshot key → behaves like today (None).
    assert _effective_context(None, {"stock_id": "AAPL"}) is None


# ── _persist_context_snapshot: guarded, skip-when-unchanged, never-raises ────

class _FakeChain:
    """Records the update payload and .eq() filter; .execute() optionally raises."""
    def __init__(self, recorder, raise_on_execute=False):
        self._rec = recorder
        self._raise = raise_on_execute

    def update(self, payload):
        self._rec["update_payload"] = payload
        return self

    def eq(self, col, val):
        self._rec.setdefault("eq", []).append((col, val))
        return self

    def execute(self):
        self._rec["executed"] = True
        if self._raise:
            raise RuntimeError("column context_snapshot does not exist")
        return self


class _FakeSupabase:
    def __init__(self, raise_on_execute=False):
        self.recorder: dict = {}
        self._raise = raise_on_execute

    def table(self, name):
        self.recorder["table"] = name
        return _FakeChain(self.recorder, self._raise)


def test_persist_writes_new_snapshot():
    sb = _FakeSupabase()
    _persist_context_snapshot(sb, "sess-1", "NEW SNAP", {"context_snapshot": None})
    assert sb.recorder.get("executed") is True
    assert sb.recorder["update_payload"] == {"context_snapshot": "NEW SNAP"}
    assert ("id", "sess-1") in sb.recorder["eq"]
    assert sb.recorder["table"] == "chat_sessions"


def test_persist_skips_when_no_request_context():
    # A reopen turn (context=None) must not write (and must not blank the stored value).
    sb = _FakeSupabase()
    _persist_context_snapshot(sb, "sess-1", None, {"context_snapshot": "KEEP"})
    assert "executed" not in sb.recorder
    _persist_context_snapshot(sb, "sess-1", "", {"context_snapshot": "KEEP"})
    assert "executed" not in sb.recorder


def test_persist_skips_when_unchanged():
    # Live turns resend the same frozen snapshot every message → write once, then no-op.
    sb = _FakeSupabase()
    _persist_context_snapshot(sb, "sess-1", "SAME", {"context_snapshot": "SAME"})
    assert "executed" not in sb.recorder


def test_persist_never_raises_on_db_error():
    # A missing column (deploy raced ahead of the migration) or any DB error must be
    # swallowed (logged) — it can NEVER break the chat turn.
    sb = _FakeSupabase(raise_on_execute=True)
    # Should not raise:
    _persist_context_snapshot(sb, "sess-1", "NEW", {"context_snapshot": None})
    assert sb.recorder.get("executed") is True


# ── _widget_grounding_line (P0-B) ───────────────────────────────────────────

def _widget(**kw):
    base = {"widget_type": "stock_chart", "ticker": "AAPL", "current_price": 189.12}
    base.update(kw)
    return base


def test_widget_line_happy_path():
    line = ChatService._widget_grounding_line(
        _widget(change=2.34, change_percent=1.25, day_high=190.0, day_low=186.5,
                volume=51_000_000, is_market_open=True)
    )
    assert line is not None
    assert "AAPL" in line and "$189.12" in line
    assert "+2.34" in line and "+1.25%" in line
    assert "day range" in line and "$186.50" in line and "$190.00" in line
    assert "51,000,000" in line
    assert "(live)" in line
    # Must instruct the model to prefer these over older figures.
    assert "prefer them" in line.lower() or "current numbers" in line.lower()


def test_widget_line_none_and_wrong_type():
    assert ChatService._widget_grounding_line(None) is None
    assert ChatService._widget_grounding_line({}) is None
    assert ChatService._widget_grounding_line(
        {"widget_type": "market_overview", "current_price": 100}
    ) is None
    assert ChatService._widget_grounding_line("not a dict") is None


def test_widget_line_zero_price_returns_none():
    # _build_stock_widget coerces a null price to 0 — we must NOT assert "$0.00".
    assert ChatService._widget_grounding_line(_widget(current_price=0)) is None
    assert ChatService._widget_grounding_line(_widget(current_price=0.0)) is None


def test_widget_line_nonfinite_price_returns_none():
    assert ChatService._widget_grounding_line(_widget(current_price=float("nan"))) is None
    assert ChatService._widget_grounding_line(_widget(current_price=float("inf"))) is None
    assert ChatService._widget_grounding_line(_widget(current_price=None)) is None


def test_widget_line_subpenny_price_keeps_sigfigs():
    # An OTC/pink-sheet stock at a real sub-penny price must NOT collapse to "$0.00"
    # (P0-B's whole point is card/prose price agreement).
    line = ChatService._widget_grounding_line(
        _widget(current_price=0.0023, change=0.0004, change_percent=21.05)
    )
    assert line is not None
    # Full sig-figs preserved — a regression to a rounded "$0.00" would drop these digits.
    assert "0.0023" in line
    assert "0.0004" in line          # the change clause keeps sig-figs too (signed)
    assert "$0.0023" in line         # rendered as the real price, not collapsed


def test_widget_line_penny_boundary_still_two_decimals():
    # At/above $0.01 we keep the familiar 2-decimal display.
    line = ChatService._widget_grounding_line(_widget(current_price=12.5))
    assert line is not None and "$12.50" in line


def test_widget_line_price_only_when_change_missing():
    # A quote with only a price still produces a valid line (no crash on missing fields).
    line = ChatService._widget_grounding_line(
        {"widget_type": "stock_chart", "ticker": "AAPL", "current_price": 189.12}
    )
    assert line is not None
    assert "$189.12" in line
    # No change/day-range/volume clauses when their fields are absent.
    assert "day range" not in line
    assert "%" not in line


def test_widget_line_skips_partial_and_bad_fields():
    # NaN change, zero day-range, string volume → those clauses drop, price line survives.
    line = ChatService._widget_grounding_line(
        _widget(change=float("nan"), change_percent=1.0, day_high=0, day_low=0,
                volume="garbage", is_market_open=False)
    )
    assert line is not None
    assert "$189.12" in line
    assert "day range" not in line     # 0/0 → skipped
    assert "volume" not in line        # non-numeric → skipped
    assert "%" not in line             # change is NaN → the (chg, pct) clause needs BOTH finite
    assert "(market closed)" in line


def test_widget_line_never_raises_on_garbage():
    # Defensive: exotic inputs must degrade to None/str, never throw.
    for w in ({"widget_type": "stock_chart"},                       # no price key
              {"widget_type": "stock_chart", "current_price": {}},  # unconvertible
              {"widget_type": "stock_chart", "ticker": None, "current_price": 10},
              # OverflowError path: an int too large to convert to float must be
              # swallowed by _fin, not raised (the "never raises" contract).
              {"widget_type": "stock_chart", "current_price": 10 ** 400},
              {"widget_type": "stock_chart", "current_price": 10, "volume": 10 ** 400}):
        out = ChatService._widget_grounding_line(w)
        assert out is None or isinstance(out, str)


# ── _build_system_instruction: replayed-snapshot framing (finding #3) ────────

def _instruction(**kw):
    # Bypass __init__ (which builds the Supabase/Gemini/FMP singletons) — the
    # method only needs the class-level _ASSET_PERSONAS, so an uninitialized
    # instance is enough to exercise the wording.
    svc = ChatService.__new__(ChatService)
    return svc._build_system_instruction(
        "NORMAL", "AAPL", client_context="Price $189 | analyst target $200 | RSI 65", **kw
    )


def test_instruction_live_context_labeled_current():
    text = _instruction(context_is_replayed=False)
    assert "current data visible to the user" in text
    assert "point-in-time" not in text


def test_instruction_replayed_context_labeled_stale():
    text = _instruction(context_is_replayed=True)
    # No longer presented as live; steers the model to tool-verify time-sensitive figures.
    assert "current data visible to the user" not in text
    assert "point-in-time" in text or "out of date" in text
    assert "analyst target" in text.lower() or "live tools" in text.lower()


def test_instruction_replayed_flag_defaults_to_live():
    # Default (omitted) → live framing, so existing callers are unaffected.
    assert "current data visible to the user" in _instruction()
