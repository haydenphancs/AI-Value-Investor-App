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

        async def generate_followup_suggestions(self, question, answer):
            return ["Chip one?", "Chip two?", "Chip three?"]

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
async def test_a_degraded_answer_is_never_stored(monkeypatch):
    """The tool-less fallback answered with none of its live data. The live endpoint
    REFUNDS such a turn; a warmed copy would be replayed — and charged — all day."""
    class _Svc:
        async def generate_response(self, **kwargs):
            return {"content": "z" * 200, "tokens_used": 1, "degraded": "no_tools"}

    monkeypatch.setattr("app.services.chat_service.ChatService", _Svc)
    # An OBSERVABLE tripwire: a raising `get_supabase` would be swallowed by `_warm_one`'s
    # store `except` and converted into the very `False` this asserts — a vacuous guard.
    writes: list = []
    monkeypatch.setattr(
        warm, "get_supabase",
        lambda: SimpleNamespace(table=lambda t: SimpleNamespace(
            upsert=lambda row, on_conflict=None: SimpleNamespace(
                execute=lambda: writes.append(row) or SimpleNamespace(data=[])
            )
        )),
    )
    assert await warm._warm_one("What topics are hot today?", "2026-09-10") is False
    assert writes == [], "a degraded answer must never reach the table"


@pytest.mark.asyncio
async def test_a_chip_that_keeps_refusing_is_parked_after_three_strikes(monkeypatch):
    """A refused chip stores no row, so the daily cap (stored rows) cannot bound its
    retries: 64 passes/day would each pay a Gemini call for the same failing question."""
    calls = {"n": 0}

    class _Svc:
        async def generate_response(self, **kwargs):
            calls["n"] += 1
            return {"content": "z" * 200, "tokens_used": 1, "degraded": "no_tools"}

    monkeypatch.setattr("app.services.chat_service.ChatService", _Svc)
    monkeypatch.setattr(warm, "_warmed_hashes", lambda day: set())
    monkeypatch.setattr(warm, "_today_et", lambda: "2026-09-10")
    import app.services.chat_starters_service as starters_mod
    monkeypatch.setattr(
        starters_mod, "get_chat_starters_service",
        lambda: SimpleNamespace(get_starters=_async(SimpleNamespace(
            global_starters=[SimpleNamespace(text="What topics are hot today?")]
        ))),
    )
    warm._refusals.clear()
    for _ in range(5):
        assert await warm.warm_todays_starters() == 0
    assert calls["n"] == warm._MAX_WARM_REFUSALS, calls
    # A new day forgets the strikes.
    monkeypatch.setattr(warm, "_today_et", lambda: "2026-09-11")
    await warm.warm_todays_starters()
    assert calls["n"] == warm._MAX_WARM_REFUSALS + 1


def _async(value):
    async def _f(*a, **k):
        return value
    return _f


@pytest.mark.asyncio
async def test_the_warm_row_stores_two_follow_up_chips(monkeypatch):
    """The replay path reuses these; a row with `[]` paid a live suggestions call per tap."""
    class _Svc:
        async def generate_response(self, **kwargs):
            return {"content": "x" * 200, "tokens_used": 10}

        async def generate_followup_suggestions(self, question, answer):
            return ["Chip one?", "  ", "Chip two?", "Chip three?"]

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
    assert written["suggestions"] == ["Chip one?", "Chip two?"]


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


# ── 7. The tape-bound chips are not a once-a-day answer (2026-09-16) ─────────
#
# The loop runs from 04:00 ET. At 04:05 on a Monday the screener still reports FRIDAY's
# close, so "What tickers are hot today?" warmed then was Friday's leaderboard, replayed
# (and charged) as "today" until midnight. And a hot-ticker row warmed at 09:35 stood at
# 15:50 with a 09:35 price under a green "Live" dot.

from datetime import datetime, timedelta, timezone


def _chips(*pairs):
    return SimpleNamespace(global_starters=[SimpleNamespace(text=t, kind=k) for t, k in pairs])


def _wire(monkeypatch, starters, *, already, phase, calls):
    monkeypatch.setattr(
        "app.services.chat_starters_service.get_chat_starters_service",
        lambda: SimpleNamespace(get_starters=AsyncMock(return_value=starters)),
    )
    monkeypatch.setattr(warm, "_warmed_hashes", lambda day: dict(already))
    monkeypatch.setattr(warm, "session_phase", lambda now=None: phase)
    monkeypatch.setattr(warm, "_today_et", lambda: "2026-09-14")

    async def _one(q, day):
        calls.append(q)
        return True

    monkeypatch.setattr(warm, "_warm_one", _one)
    warm._refusals.clear()
    warm._rewarms.clear()


def _ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["premarket", "closed"])
async def test_a_tape_bound_chip_is_not_warmed_before_the_regular_session(monkeypatch, phase):
    """04:05 ET Monday: the fixed asks, the hot slots and the trending chip wait; the
    evergreen question is warmed as before."""
    calls = []
    _wire(monkeypatch, _chips(
        ("What tickers are hot today?", "fixed"),
        ("Why is NVDA up 14% today?", "hot_ticker"),
        ("Why is Technology leading today?", "hot_sector"),
        ("What's driving AI Infrastructure today?", "hot_topic"),
        ("Why is everyone talking about TSLA?", "trending"),
        ("What is a P/E ratio?", "evergreen"),
    ), already={}, phase=phase, calls=calls)
    assert await warm.warm_todays_starters() == 1
    assert calls == ["What is a P/E ratio?"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["regular", "afterhours"])
async def test_a_tape_bound_chip_is_warmed_once_the_tape_is_todays(monkeypatch, phase):
    calls = []
    _wire(monkeypatch, _chips(
        ("What tickers are hot today?", "fixed"),
        ("What is a P/E ratio?", "evergreen"),
    ), already={}, phase=phase, calls=calls)
    assert await warm.warm_todays_starters() == 2
    assert set(calls) == {"What tickers are hot today?", "What is a P/E ratio?"}


@pytest.mark.asyncio
async def test_a_stale_tape_row_is_rewarmed_during_the_session_and_a_fresh_one_is_not(monkeypatch):
    """Through the regular session a tape-bound row older than the TTL is regenerated;
    one inside the TTL, and an evergreen row of ANY age, cost nothing."""
    monkeypatch.setattr(warm.settings, "CHAT_STARTER_WARM_TAPE_TTL_SECONDS", 3600)
    calls = []
    hot, fresh, evergreen = ("What tickers are hot today?", "Why is NVDA up 14% today?",
                             "What is a P/E ratio?")
    _wire(monkeypatch, _chips((hot, "fixed"), (fresh, "hot_ticker"), (evergreen, "evergreen")),
          already={warm.question_hash(hot): _ago(3601),
                   warm.question_hash(fresh): _ago(600),
                   warm.question_hash(evergreen): _ago(6 * 3600)},
          phase="regular", calls=calls)
    assert await warm.warm_todays_starters() == 1
    assert calls == [hot]
    assert warm._rewarms == {"2026-09-14": 1}


@pytest.mark.asyncio
async def test_no_rewarm_after_the_close(monkeypatch):
    """After hours the tape is static: the last regular-session write stands, and the
    evening is not spent re-paying Gemini for the same close."""
    calls = []
    hot = "What tickers are hot today?"
    _wire(monkeypatch, _chips((hot, "fixed")),
          already={warm.question_hash(hot): _ago(5 * 3600)}, phase="afterhours", calls=calls)
    assert await warm.warm_todays_starters() == 0
    assert calls == []


@pytest.mark.asyncio
async def test_an_unreadable_created_at_keeps_the_once_a_day_behaviour(monkeypatch):
    """None / garbage must neither re-warm on every pass (a budget leak) nor crash."""
    calls = []
    hot = "What tickers are hot today?"
    for stamp in (None, "", "not-a-timestamp", 12345):
        calls.clear()
        _wire(monkeypatch, _chips((hot, "fixed")),
              already={warm.question_hash(hot): stamp}, phase="regular", calls=calls)
        assert await warm.warm_todays_starters() == 0, stamp
        assert calls == [], stamp


@pytest.mark.asyncio
async def test_rewarms_count_against_the_daily_cap(monkeypatch):
    """A re-warm replaces a row, so a cap that counts ROWS would never see it — and the
    cap is a Gemini-spend bound."""
    monkeypatch.setattr(warm.settings, "CHAT_STARTER_WARM_DAILY_CAP", 2)
    calls = []
    hot, new = "What tickers are hot today?", "What is a P/E ratio?"
    _wire(monkeypatch, _chips((hot, "fixed")),
          already={warm.question_hash(hot): _ago(7200)}, phase="regular", calls=calls)
    assert await warm.warm_todays_starters() == 1          # 1 row + 1 rewarm = cap
    assert warm._rewarms == {"2026-09-14": 1}
    # Same day, a new evergreen chip appears: 1 stored + 1 rewarm leaves NO room.
    monkeypatch.setattr(
        "app.services.chat_starters_service.get_chat_starters_service",
        lambda: SimpleNamespace(get_starters=AsyncMock(return_value=_chips((hot, "fixed"), (new, "evergreen")))),
    )
    monkeypatch.setattr(warm, "_warmed_hashes", lambda day: {warm.question_hash(hot): _ago(1)})
    calls.clear()
    assert await warm.warm_todays_starters() == 0
    assert calls == []


def test_row_age_reads_every_stamp_shape_and_refuses_none_of_them_loudly():
    now = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
    assert warm._row_age_seconds("2026-09-14T14:00:00+00:00", now) == 3600
    assert warm._row_age_seconds("2026-09-14T14:00:00Z", now) == 3600
    assert warm._row_age_seconds("2026-09-14T14:00:00.123456+00:00", now) == pytest.approx(3599.88, abs=0.01)
    assert warm._row_age_seconds("2026-09-14T14:00:00", now) == 3600, "naive = UTC"
    assert warm._row_age_seconds(datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc), now) == 3600
    assert warm._row_age_seconds("2026-09-14T16:00:00+00:00", now) == 0.0, "future stamp clamps"
    for bad in (None, "", "garbage", 42, object()):
        assert warm._row_age_seconds(bad, now) is None, bad


@pytest.mark.asyncio
async def test_the_stored_row_stamps_created_at_itself(monkeypatch):
    """An upsert onto the existing key UPDATES the row and the column default only fires
    on INSERT — a re-warmed row would otherwise keep its first write's stamp forever."""
    class _Svc:
        async def generate_response(self, **kwargs):
            return {"content": "x" * 200, "tokens_used": 1}

        async def generate_followup_suggestions(self, *a, **k):
            return []

    monkeypatch.setattr("app.services.chat_service.ChatService", _Svc)
    rows = []

    class _Table:
        def upsert(self, row, on_conflict=None):
            rows.append(row)
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    monkeypatch.setattr(warm, "get_supabase", lambda: SimpleNamespace(table=lambda n: _Table()))
    assert await warm._warm_one("What tickers are hot today?", "2026-09-14") is True
    stamp = rows[0]["created_at"]
    assert warm._row_age_seconds(stamp) is not None and warm._row_age_seconds(stamp) < 5


# ── the read side ──

def _lookup_db(monkeypatch, row):
    class _Q:
        def __init__(self):
            self._row = row

        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self

        def execute(self):
            return SimpleNamespace(data=[self._row] if self._row else [])

    monkeypatch.setattr(warm, "get_supabase", lambda: SimpleNamespace(table=lambda n: _Q()))


@pytest.mark.asyncio
async def test_lookup_refuses_a_tape_row_older_than_twice_the_ttl_during_the_session(monkeypatch):
    monkeypatch.setattr(warm.settings, "CHAT_STARTER_WARM_TAPE_TTL_SECONDS", 3600)
    monkeypatch.setattr(warm, "_today_et", lambda: "2026-09-14")
    monkeypatch.setattr(warm, "session_phase", lambda now=None: "regular")
    _lookup_db(monkeypatch, {"answer": "a" * 200, "widget": None, "suggestions": [],
                             "created_at": _ago(7201)})
    assert await warm.lookup("What tickers are hot today?") is None
    # The same age on an EVERGREEN question is served: its answer does not go stale.
    assert (await warm.lookup("What is a P/E ratio?"))["answer"] == "a" * 200


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["afterhours", "closed", "premarket"])
async def test_lookup_serves_an_old_tape_row_when_the_tape_is_static(monkeypatch, phase):
    """After the close the 15:05 answer IS the day's answer; refusing it would make every
    evening tap pay a live Gemini turn for the same numbers."""
    monkeypatch.setattr(warm.settings, "CHAT_STARTER_WARM_TAPE_TTL_SECONDS", 3600)
    monkeypatch.setattr(warm, "_today_et", lambda: "2026-09-14")
    monkeypatch.setattr(warm, "session_phase", lambda now=None: phase)
    _lookup_db(monkeypatch, {"answer": "a" * 200, "widget": None, "suggestions": [],
                             "created_at": _ago(5 * 3600)})
    assert (await warm.lookup("What tickers are hot today?"))["answer"] == "a" * 200


@pytest.mark.asyncio
async def test_lookup_serves_a_tape_row_inside_twice_the_ttl(monkeypatch):
    monkeypatch.setattr(warm.settings, "CHAT_STARTER_WARM_TAPE_TTL_SECONDS", 3600)
    monkeypatch.setattr(warm, "_today_et", lambda: "2026-09-14")
    monkeypatch.setattr(warm, "session_phase", lambda now=None: "regular")
    _lookup_db(monkeypatch, {"answer": "a" * 200, "widget": None, "suggestions": [],
                             "created_at": _ago(7000)})
    assert (await warm.lookup("What tickers are hot today?"))["answer"] == "a" * 200


@pytest.mark.asyncio
@pytest.mark.parametrize("age,stale", [(10, False), (899, False), (901, True), (5 * 3600, True)])
async def test_lookup_flags_a_widget_older_than_the_quote_cadence(monkeypatch, age, stale):
    monkeypatch.setattr(warm.settings, "CHAT_STARTER_WIDGET_MAX_AGE_SECONDS", 900)
    monkeypatch.setattr(warm, "_today_et", lambda: "2026-09-14")
    monkeypatch.setattr(warm, "session_phase", lambda now=None: "regular")
    widget = {"widget_type": "stock_chart", "ticker": "NVDA", "current_price": 100.0,
              "is_market_open": True}
    _lookup_db(monkeypatch, {"answer": "a" * 200, "widget": widget, "suggestions": [],
                             "created_at": _ago(age)})
    out = await warm.lookup("What is a P/E ratio?")
    assert out["widget"] == widget
    assert out["widget_stale"] is stale


@pytest.mark.asyncio
async def test_lookup_treats_an_unreadable_age_as_a_stale_widget_but_a_fresh_answer(monkeypatch):
    """No stamp = cannot prove the card is fresh → refresh it; the ANSWER keeps the
    once-a-day behaviour (an unreadable stamp must not force every tap live)."""
    monkeypatch.setattr(warm, "_today_et", lambda: "2026-09-14")
    monkeypatch.setattr(warm, "session_phase", lambda now=None: "regular")
    widget = {"widget_type": "stock_chart", "ticker": "NVDA"}
    _lookup_db(monkeypatch, {"answer": "a" * 200, "widget": widget, "suggestions": []})
    out = await warm.lookup("What tickers are hot today?")
    assert out["answer"] == "a" * 200 and out["widget_stale"] is True
    _lookup_db(monkeypatch, {"answer": "a" * 200, "widget": None, "suggestions": []})
    assert (await warm.lookup("What tickers are hot today?"))["widget_stale"] is False
