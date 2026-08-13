"""Cached rolling history summary (`ChatService._condense_history`) — Phase 1b.

`_condense_history` used to fire a flash-lite call on EVERY turn once a chat grew
past `_RECENT_TURNS`, re-deriving nearly identical bullets from a barely-changed
older slice — and doing it SERIALLY, in front of the first token. The summary is
now cached on the session (migration 130) and refreshed only once
`CHAT_SUMMARY_REFRESH_AFTER_MESSAGES` older-slice messages are newer than the
stored watermark.

The assertion that matters throughout is `gemini.calls == 0` on a cache hit: a
"working" cache that still calls the model has saved nothing, and the returned
block would look identical either way.

No network, no Supabase (testing.md): the service is built via `object.__new__`
and both collaborators are stubs.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.chat_service import ChatService

BASE = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


class _Gemini:
    def __init__(self, text="- summarised bullet", raises=False):
        self._text, self._raises, self.calls = text, raises, 0

    async def generate_text(self, prompt, model_name=None, **kw):
        self.calls += 1
        if self._raises:
            raise RuntimeError("summariser down")
        return {"text": self._text}


class _Supabase:
    """Minimal chat_sessions stub: one row, recording reads and writes."""

    def __init__(self, row=None, read_raises=False, write_raises=False):
        self.row = row
        self.read_raises, self.write_raises = read_raises, write_raises
        self.reads, self.writes = 0, []
        self._pending_update = None

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        self._pending_update = None
        return self

    def update(self, payload):
        self._pending_update = payload
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._pending_update is not None:
            if self.write_raises:
                raise RuntimeError("column memory_summary does not exist")
            self.writes.append(self._pending_update)
            return type("R", (), {"data": []})()
        self.reads += 1
        if self.read_raises:
            raise RuntimeError("column memory_summary does not exist")
        return type("R", (), {"data": [self.row] if self.row else []})()


def _svc(gemini, supabase=None):
    s = object.__new__(ChatService)
    s.gemini = gemini
    s.supabase = supabase
    s.fmp = None
    return s


def _msgs(n, start=BASE, step_seconds=60):
    """n messages, oldest first, one minute apart."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}",
         "created_at": (start + timedelta(seconds=i * step_seconds)).isoformat()}
        for i in range(n)
    ]


def _row(summary="- cached bullet", upto=None):
    return {"memory_summary": summary,
            "memory_summary_upto": upto.isoformat() if upto else None}


# ── _parse_ts ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("2026-08-13T12:00:00+00:00", BASE),
    ("2026-08-13T12:00:00.123456+00:00", BASE.replace(microsecond=123456)),
    ("2026-08-13T12:00:00Z", BASE),                       # Z suffix
    ("2026-08-13T12:00:00", BASE),                        # naive → assumed UTC
    ("  2026-08-13T12:00:00+00:00  ", BASE),              # whitespace
    (BASE, BASE),                                          # already a datetime
])
def test_parse_ts_accepts_every_shape_postgres_emits(value, expected):
    assert ChatService._parse_ts(value) == expected


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-date", 12345, [], {}, object()])
def test_parse_ts_returns_none_rather_than_raising(value):
    assert ChatService._parse_ts(value) is None


def test_parse_ts_always_returns_an_aware_datetime():
    """Comparing a naive and an aware datetime is a TypeError, and this runs on
    the answer path."""
    for value in ("2026-08-13T12:00:00", "2026-08-13T12:00:00Z", BASE.replace(tzinfo=None)):
        assert ChatService._parse_ts(value).tzinfo is not None


# ── short chats: nothing to summarise ───────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_history_returns_empty_and_never_calls_out():
    g, db = _Gemini(), _Supabase()
    assert await _svc(g, db)._condense_history([], session_id="s1") == ""
    assert g.calls == 0 and db.reads == 0


@pytest.mark.asyncio
async def test_history_at_or_below_recent_window_is_verbatim():
    g, db = _Gemini(), _Supabase()
    out = await _svc(g, db)._condense_history(_msgs(ChatService._RECENT_TURNS), session_id="s1")
    assert out.startswith("CONVERSATION HISTORY:")
    assert g.calls == 0, "no summary needed"
    assert db.reads == 0, "no cache lookup needed"


# ── cache miss → generate + persist ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_cached_summary_generates_and_persists():
    g, db = _Gemini(), _Supabase(row=None)
    history = _msgs(12)
    out = await _svc(g, db)._condense_history(history, session_id="s1")
    assert g.calls == 1
    assert "EARLIER CONVERSATION (summary):" in out
    assert len(db.writes) == 1
    # Watermark = created_at of the NEWEST message inside the older slice.
    older = history[:-ChatService._RECENT_TURNS]
    assert db.writes[0]["memory_summary_upto"] == older[-1]["created_at"]
    assert db.writes[0]["memory_summary"] == "- summarised bullet"


@pytest.mark.asyncio
async def test_cached_summary_with_null_watermark_regenerates():
    g = _Gemini()
    db = _Supabase(row=_row(summary="- cached", upto=None))
    await _svc(g, db)._condense_history(_msgs(12), session_id="s1")
    assert g.calls == 1, "an unusable watermark must not license a reuse"


# ── cache hit → reuse, and crucially spend nothing ──────────────────────────

@pytest.mark.asyncio
async def test_fully_covered_slice_reuses_without_calling_the_model():
    history = _msgs(12)
    older = history[:-ChatService._RECENT_TURNS]
    g = _Gemini()
    db = _Supabase(row=_row(upto=ChatService._parse_ts(older[-1]["created_at"])))
    out = await _svc(g, db)._condense_history(history, session_id="s1")
    assert g.calls == 0, "THE point of the cache"
    assert "- cached bullet" in out
    assert db.writes == [], "a reuse must not rewrite the row"


@pytest.mark.asyncio
async def test_reuses_while_uncovered_stays_below_the_threshold():
    from app.config import settings
    history = _msgs(12)
    older = history[:-ChatService._RECENT_TURNS]
    # Watermark just below the last (threshold - 1) messages → uncovered == 3.
    behind = settings.CHAT_SUMMARY_REFRESH_AFTER_MESSAGES - 1
    upto = ChatService._parse_ts(older[-(behind + 1)]["created_at"])
    g = _Gemini()
    db = _Supabase(row=_row(upto=upto))
    await _svc(g, db)._condense_history(history, session_id="s1")
    assert g.calls == 0


@pytest.mark.asyncio
async def test_regenerates_once_the_threshold_is_reached():
    from app.config import settings
    history = _msgs(20)
    older = history[:-ChatService._RECENT_TURNS]
    behind = settings.CHAT_SUMMARY_REFRESH_AFTER_MESSAGES
    upto = ChatService._parse_ts(older[-(behind + 1)]["created_at"])
    g = _Gemini()
    db = _Supabase(row=_row(upto=upto))
    await _svc(g, db)._condense_history(history, session_id="s1")
    assert g.calls == 1, "exactly at the threshold must refresh"
    assert len(db.writes) == 1


@pytest.mark.asyncio
async def test_unparseable_timestamp_counts_as_uncovered():
    """We cannot prove a message with a broken created_at is in the summary, so it
    must push toward regenerating rather than silently serving a stale block."""
    from app.config import settings
    history = _msgs(12)
    for m in history[-(ChatService._RECENT_TURNS + settings.CHAT_SUMMARY_REFRESH_AFTER_MESSAGES):
                     -ChatService._RECENT_TURNS]:
        m["created_at"] = "corrupted"
    older = history[:-ChatService._RECENT_TURNS]
    upto = ChatService._parse_ts(older[0]["created_at"])
    g = _Gemini()
    db = _Supabase(row=_row(upto=upto))
    await _svc(g, db)._condense_history(history, session_id="s1")
    assert g.calls == 1


# ── degradation: every failure keeps the turn alive ─────────────────────────

@pytest.mark.asyncio
async def test_cache_read_failure_regenerates_rather_than_raising():
    """Safe to deploy BEFORE migration 130: the missing column raises here."""
    g, db = _Gemini(), _Supabase(read_raises=True)
    out = await _svc(g, db)._condense_history(_msgs(12), session_id="s1")
    assert g.calls == 1
    assert "EARLIER CONVERSATION (summary):" in out


@pytest.mark.asyncio
async def test_cache_write_failure_still_returns_the_answer_block():
    g, db = _Gemini(), _Supabase(row=None, write_raises=True)
    out = await _svc(g, db)._condense_history(_msgs(12), session_id="s1")
    assert "EARLIER CONVERSATION (summary):" in out


@pytest.mark.asyncio
async def test_summariser_failure_falls_back_to_recent_only():
    g, db = _Gemini(raises=True), _Supabase(row=None)
    out = await _svc(g, db)._condense_history(_msgs(12), session_id="s1")
    assert out.startswith("CONVERSATION HISTORY:")
    assert db.writes == [], "nothing to cache when generation failed"


@pytest.mark.asyncio
async def test_empty_summary_text_is_not_cached():
    g, db = _Gemini(text="   "), _Supabase(row=None)
    out = await _svc(g, db)._condense_history(_msgs(12), session_id="s1")
    assert out.startswith("CONVERSATION HISTORY:")
    assert db.writes == []


@pytest.mark.asyncio
async def test_no_session_id_skips_the_cache_entirely():
    g, db = _Gemini(), _Supabase(row=_row(upto=BASE))
    out = await _svc(g, db)._condense_history(_msgs(12), session_id=None)
    assert db.reads == 0 and db.writes == []
    assert g.calls == 1
    assert "EARLIER CONVERSATION (summary):" in out


@pytest.mark.asyncio
async def test_no_supabase_client_still_works():
    g = _Gemini()
    out = await _svc(g, None)._condense_history(_msgs(12), session_id="s1")
    assert g.calls == 1 and "EARLIER CONVERSATION (summary):" in out


@pytest.mark.asyncio
async def test_missing_created_at_everywhere_does_not_crash():
    history = [{"role": "user", "content": f"m{i}"} for i in range(12)]
    g, db = _Gemini(), _Supabase(row=None)
    out = await _svc(g, db)._condense_history(history, session_id="s1")
    assert "EARLIER CONVERSATION (summary):" in out
    # No parseable watermark → nothing worth caching, so no write.
    assert db.writes == []
