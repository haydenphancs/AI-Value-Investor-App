"""Guards for the pre-computed suggestion-chip answers (migration 162).

Three classes of thing are pinned here, and each is a defect this repo has already shipped
in some other form:

  1. **Impersonality.** One row serves every caller. `chat_starters_service`'s own header
     records why the QUESTIONS must be impersonal; an ANSWER is far likelier to carry
     something personal, and `redact_signals()` is per-request — so a Pro-gated signal that
     reached this table would be served to Free users with no filter left to catch it.
  2. **The day boundary.** ET, matching the rotation. UTC here would roll the answers and
     the questions over at different moments, spending each evening serving yesterday's
     answers under today's questions.
  3. **Refusing to store a bad answer.** A truncated deep-dive answer was once written into
     a 24h cache and replayed, making three retests read byte-identical output and look like
     the fix had not landed.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import chat_starter_warm_service as warm


# ── 1. The key ───────────────────────────────────────────────────────────────

def test_the_key_is_stable_across_cosmetic_differences():
    """A chip whose text differs only in case, spacing or a curly apostrophe must hit its
    own warmed row — otherwise the pre-warm silently stops working for the chips most likely
    to contain punctuation."""
    base = warm.question_hash("Why is NAVN down 22% today?")
    assert warm.question_hash("why is navn down 22% today?") == base
    assert warm.question_hash("  Why is NAVN   down 22% today?  ") == base
    assert warm.question_hash("Why is NAVN down 22% today?") == base


def test_different_questions_do_not_collide():
    """Anti-vacuity: a hash that normalised everything to one value would satisfy the test
    above and serve one answer for every chip."""
    assert warm.question_hash("What tickers are hot today?") != warm.question_hash(
        "What topics are hot today?"
    )
    assert warm.question_hash("Why is NAVN up 22% today?") != warm.question_hash(
        "Why is NAVN down 22% today?"
    )


# ── 2. The day boundary ──────────────────────────────────────────────────────

def test_the_day_boundary_is_ET_and_shared_with_the_rotation():
    """Not `date.today()`, not UTC. The chips roll on the ET trading day, and an answer that
    rolled on a different boundary would be served under a question it does not answer.

    Asserted on the SOURCE because the two agreeing today proves nothing — they agree for
    most of every UTC day even when the implementation is wrong.
    """
    src = inspect.getsource(warm._today_et)
    assert "trading_date_et" in src, (
        "_today_et must delegate to push_dispatch_service.trading_date_et, the same ET "
        "boundary the rotation uses"
    )
    assert "utcnow" not in src and "date.today" not in src

    from app.services.push_dispatch_service import trading_date_et

    assert warm._today_et() == trading_date_et()


# ── 3. Impersonality — the security-shaped one ───────────────────────────────

@pytest.mark.asyncio
async def test_the_warm_job_generates_with_no_user_identity(monkeypatch):
    """🔒 Every personalisation lever must be explicitly OFF on this path.

    `CHAT_PERSONALIZATION_ENABLED` being False today is a config value, not a guarantee, and
    a row written with a user's reader lens or memory facts would then be replayed to every
    other caller who taps the same chip.
    """
    seen = {}

    class _Svc:
        async def generate_response(self, **kwargs):
            seen.update(kwargs)
            return {"content": "x" * 200, "tokens_used": 10}

    monkeypatch.setattr("app.services.chat_service.ChatService", _Svc)
    written = {}
    monkeypatch.setattr(
        warm, "get_supabase",
        lambda: SimpleNamespace(table=lambda t: SimpleNamespace(
            upsert=lambda row, on_conflict=None: SimpleNamespace(
                execute=lambda: written.update(row) or SimpleNamespace(data=[])
            )
        )),
    )

    assert await warm._warm_one("What topics are hot today?", "2026-09-10") is True

    assert seen["user_id"] is None, "a shared row must not be generated for a user"
    assert seen["reader_lens"] is None
    assert seen["stock_id"] is None
    assert seen["context"] is None
    assert seen["context_type"] is None
    assert seen["reference_id"] is None


def test_the_warm_job_passes_every_personalisation_lever_explicitly():
    """Source-level companion to the test above.

    The behavioural test only proves the values are None — which a future refactor could
    achieve by DELETING the arguments and letting defaults supply them. That would pass while
    silently re-coupling the shared row to whatever the defaults become.
    """
    src = inspect.getsource(warm._warm_one)
    body = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    for kwarg in ("user_id=None", "reader_lens=None", "stock_id=None", "context=None"):
        assert kwarg in body, f"{kwarg} must be passed explicitly, not left to a default"


# ── 4. Refusing to store a bad answer ────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["", "   ", "I cannot help with that.", "x" * 20])
async def test_a_short_or_refusing_answer_is_never_stored(answer, monkeypatch):
    """Storing one pins that failure for the whole ET day, for every user who taps the chip."""
    class _Svc:
        async def generate_response(self, **kwargs):
            return {"content": answer, "tokens_used": 1}

    monkeypatch.setattr("app.services.chat_service.ChatService", _Svc)
    monkeypatch.setattr(
        warm, "get_supabase",
        lambda: (_ for _ in ()).throw(AssertionError("a write was attempted")),
    )
    assert await warm._warm_one("What topics are hot today?", "2026-09-10") is False


@pytest.mark.asyncio
async def test_a_runaway_answer_is_never_stored(monkeypatch):
    """A starter chip is a one-line question. A multi-kilobyte reply means `_BRIEF_STYLE` was
    ignored, and persisting it renders a wall of text on every tap for the rest of the day."""
    class _Svc:
        async def generate_response(self, **kwargs):
            return {"content": "y" * (warm._MAX_ANSWER_CHARS + 1), "tokens_used": 1}

    monkeypatch.setattr("app.services.chat_service.ChatService", _Svc)
    monkeypatch.setattr(
        warm, "get_supabase",
        lambda: (_ for _ in ()).throw(AssertionError("a write was attempted")),
    )
    assert await warm._warm_one("What topics are hot today?", "2026-09-10") is False


@pytest.mark.asyncio
async def test_a_generation_failure_is_not_fatal(monkeypatch):
    class _Svc:
        async def generate_response(self, **kwargs):
            raise RuntimeError("gemini down")

    monkeypatch.setattr("app.services.chat_service.ChatService", _Svc)
    assert await warm._warm_one("What topics are hot today?", "2026-09-10") is False


# ── 5. The read path never breaks a turn ─────────────────────────────────────

@pytest.mark.asyncio
async def test_lookup_degrades_to_none_when_the_table_is_missing(monkeypatch):
    """Migration 162 is applied by hand, so the table genuinely does not exist yet in some
    environments. A miss must mean "answer live", never a 500 on a credit-charged turn."""
    monkeypatch.setattr(
        warm, "get_supabase",
        lambda: (_ for _ in ()).throw(RuntimeError('relation "chat_starter_answers" does not exist')),
    )
    assert await warm.lookup("What topics are hot today?") is None


@pytest.mark.asyncio
async def test_lookup_refuses_a_row_too_short_to_serve(monkeypatch):
    """Defence in depth against a row that should never have been written."""
    monkeypatch.setattr(
        warm, "get_supabase",
        lambda: SimpleNamespace(table=lambda t: SimpleNamespace(
            select=lambda *a: SimpleNamespace(
                eq=lambda *a, **k: SimpleNamespace(
                    eq=lambda *a, **k: SimpleNamespace(
                        limit=lambda n: SimpleNamespace(
                            execute=lambda: SimpleNamespace(data=[{"answer": "no."}])
                        )
                    )
                )
            )
        )),
    )
    assert await warm.lookup("What topics are hot today?") is None


@pytest.mark.asyncio
async def test_the_kill_switch_disables_both_halves(monkeypatch):
    monkeypatch.setattr(warm.settings, "CHAT_STARTER_WARM_ENABLED", False)
    assert await warm.lookup("What topics are hot today?") is None
    assert await warm.warm_todays_starters() == 0


# ── 6. Scope: global chips only ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_global_chips_are_warmed(monkeypatch):
    """Detail-screen chips are per-symbol templates, so warming them is (watchlist size x
    chips per screen) answers a day rather than eight — a different feature with a different
    cost, deliberately out of scope."""
    starters = SimpleNamespace(
        global_starters=[SimpleNamespace(text="What topics are hot today?")],
        detail_starters=SimpleNamespace(ticker=["Is {symbol} a good buy?"]),
    )
    monkeypatch.setattr(
        "app.services.chat_starters_service.get_chat_starters_service",
        lambda: SimpleNamespace(get_starters=AsyncMock(return_value=starters)),
    )
    monkeypatch.setattr(warm, "_warmed_hashes", lambda day: set())
    warmed_questions = []

    async def _one(q, day):
        warmed_questions.append(q)
        return True

    monkeypatch.setattr(warm, "_warm_one", _one)
    assert await warm.warm_todays_starters() == 1
    assert warmed_questions == ["What topics are hot today?"]


@pytest.mark.asyncio
async def test_an_already_warmed_chip_costs_nothing(monkeypatch):
    """Self-limiting by construction — the steady state must make zero Gemini calls."""
    q = "What topics are hot today?"
    starters = SimpleNamespace(global_starters=[SimpleNamespace(text=q)])
    monkeypatch.setattr(
        "app.services.chat_starters_service.get_chat_starters_service",
        lambda: SimpleNamespace(get_starters=AsyncMock(return_value=starters)),
    )
    monkeypatch.setattr(warm, "_warmed_hashes", lambda day: {warm.question_hash(q)})
    attempted = []

    async def _record(question, day):
        attempted.append(question)
        return True

    monkeypatch.setattr(warm, "_warm_one", _record)
    assert await warm.warm_todays_starters() == 0
    assert attempted == [], "an already-warmed chip was regenerated"


@pytest.mark.asyncio
async def test_a_failed_warmed_set_read_skips_rather_than_rewriting_everything(monkeypatch):
    """The dangerous direction. Treating a read failure as "nothing is warmed" would
    regenerate the whole set on every pass and burn a day's budget in an hour."""
    starters = SimpleNamespace(
        global_starters=[SimpleNamespace(text=f"Q{i}?") for i in range(8)]
    )
    monkeypatch.setattr(
        "app.services.chat_starters_service.get_chat_starters_service",
        lambda: SimpleNamespace(get_starters=AsyncMock(return_value=starters)),
    )

    def _boom(day):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(warm, "_warmed_hashes", _boom)

    # Recorded, NOT raised. `warm_todays_starters` gathers with `return_exceptions=True`, so
    # an AssertionError from inside the mock is swallowed and the count still comes back 0 —
    # the assertion would pass on the very mutation it exists to catch. (Found by mutation
    # testing this file; the first version of this test did exactly that.)
    attempted = []

    async def _record(q, day):
        attempted.append(q)
        return True

    monkeypatch.setattr(warm, "_warm_one", _record)
    assert await warm.warm_todays_starters() == 0
    assert attempted == [], (
        f"regenerated {len(attempted)} question(s) despite not knowing what was already "
        "warmed — that burns a day's budget in one pass"
    )


@pytest.mark.asyncio
async def test_the_daily_cap_bounds_distinct_questions(monkeypatch):
    """The chip set drifts intraday, so the number of DISTINCT questions in a day is not
    simply eight. Without a ceiling a churning hot-ticker slot could warm all day."""
    monkeypatch.setattr(warm.settings, "CHAT_STARTER_WARM_DAILY_CAP", 3)
    starters = SimpleNamespace(
        global_starters=[SimpleNamespace(text=f"Q{i}?") for i in range(8)]
    )
    monkeypatch.setattr(
        "app.services.chat_starters_service.get_chat_starters_service",
        lambda: SimpleNamespace(get_starters=AsyncMock(return_value=starters)),
    )
    monkeypatch.setattr(warm, "_warmed_hashes", lambda day: {"a", "b"})  # 2 already used
    calls = []

    async def _one(q, day):
        calls.append(q)
        return True

    monkeypatch.setattr(warm, "_warm_one", _one)
    assert await warm.warm_todays_starters() == 1, "cap 3 minus 2 already warmed = 1"
    assert len(calls) == 1
