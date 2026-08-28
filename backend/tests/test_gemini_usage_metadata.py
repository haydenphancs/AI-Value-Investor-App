"""Token-accounting extraction from Gemini responses (`gemini.py`).

Why this file exists: `cached_content_token_count` is the ONLY signal that
Gemini's prefix cache served part of a request at a 75% discount, and nothing
in the app read it. Every prompt-cost decision was guesswork.

These are pure transform tests — no network, no SDK. They feed hand-built
stand-ins for the SDK's response objects into the extractors and assert the
DEGRADED behaviour is correct (None / skip), never a wrong number. The outlier
cases are the point: this code sits on the response path of every user-facing
Gemini call, so a malformed usage field must never raise.
"""

import logging

import pytest

from app.integrations.gemini import (
    _coerce_token_count,
    _log_gemini_usage,
    _response_tokens,
    _response_usage,
    _StreamUsage,
)


class _Usage:
    """Stand-in for the SDK's usage_metadata; only the attrs passed are set."""

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


class _Resp:
    def __init__(self, usage_metadata=None):
        self.usage_metadata = usage_metadata


def _full(total=100, prompt=80, cached=60, output=20, thoughts=0):
    return _Resp(_Usage(
        total_token_count=total,
        prompt_token_count=prompt,
        cached_content_token_count=cached,
        candidates_token_count=output,
        thoughts_token_count=thoughts,
    ))


# ── _coerce_token_count ───────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (0, 0),
    (1234, 1234),
    (12.9, 12),           # float truncates rather than raising
    ("512", 512),         # numeric string is still a count
    (None, None),
    ("", None),
    ("abc", None),
    ([], None),
    ({}, None),
    (object(), None),
])
def test_coerce_handles_every_input_shape(value, expected):
    assert _coerce_token_count(value) == expected


def test_coerce_rejects_bool_despite_int_subclass():
    """`True` would otherwise be reported as 1 token — a plausible-looking lie."""
    assert _coerce_token_count(True) is None
    assert _coerce_token_count(False) is None


def test_coerce_survives_non_finite_floats():
    """int(nan) raises ValueError; int(inf) raises OverflowError, which is NOT
    a ValueError — catching only (TypeError, ValueError) would crash here."""
    assert _coerce_token_count(float("nan")) is None
    assert _coerce_token_count(float("inf")) is None
    assert _coerce_token_count(float("-inf")) is None


# ── _response_usage ───────────────────────────────────────────────

def test_usage_extracts_all_five_fields():
    assert _response_usage(_full()) == {
        "total": 100, "prompt": 80, "cached": 60, "output": 20, "thoughts": 0,
    }


def test_usage_missing_metadata_yields_all_none():
    assert _response_usage(_Resp(None)) == {
        "total": None, "prompt": None, "cached": None, "output": None,
        "thoughts": None,
    }


def test_usage_on_object_without_the_attribute_at_all():
    """Stream chunks routinely carry no usage_metadata attribute whatsoever."""
    assert _response_usage(object()) == {
        "total": None, "prompt": None, "cached": None, "output": None,
        "thoughts": None,
    }
    assert _response_usage(None)["total"] is None


def test_usage_partial_metadata_keeps_present_fields():
    """A chunk carrying only prompt+cached must not blank them out."""
    usage = _response_usage(_Resp(_Usage(prompt_token_count=80, cached_content_token_count=60)))
    assert usage == {
        "total": None, "prompt": 80, "cached": 60, "output": None, "thoughts": None,
    }


def test_usage_never_returns_the_shared_empty_dict():
    """A caller mutating the result must not corrupt the module-level default."""
    first = _response_usage(_Resp(None))
    first["total"] = 999
    assert _response_usage(_Resp(None))["total"] is None


def test_usage_zero_cached_is_preserved_not_coerced_to_none():
    """0 means 'measured, and the cache did not hit' — materially different
    from None ('not reported'). Collapsing them would hide the finding."""
    assert _response_usage(_full(cached=0))["cached"] == 0


# ── _response_tokens (the pre-existing wrapper, six call sites) ────

def test_response_tokens_still_returns_total():
    assert _response_tokens(_full(total=4242)) == 4242


def test_response_tokens_none_when_absent():
    assert _response_tokens(_Resp(None)) is None
    assert _response_tokens(_Resp(_Usage(prompt_token_count=10))) is None


# ── _StreamUsage ──────────────────────────────────────────────────

def test_stream_usage_empty_stream_is_all_zero():
    assert _StreamUsage().totals() == {
        "total": 0, "prompt": 0, "cached": 0, "output": 0, "thoughts": 0,
    }


def test_stream_usage_takes_last_reading_within_a_round_not_the_sum():
    """Counts are CUMULATIVE per chunk within one response. Summing them would
    over-report by roughly the chunk count."""
    usage = _StreamUsage()
    usage.observe(_full(total=10, prompt=8, cached=4, output=2))
    usage.observe(_full(total=30, prompt=8, cached=4, output=22))
    usage.observe(_full(total=50, prompt=8, cached=4, output=42))
    assert usage.totals() == {
        "total": 50, "prompt": 8, "cached": 4, "output": 42, "thoughts": 0,
    }


def test_stream_usage_sums_across_rounds():
    """Across agentic rounds the per-round totals ARE additive."""
    usage = _StreamUsage()
    usage.observe(_full(total=50, prompt=40, cached=30, output=10))
    usage.commit_round()
    usage.observe(_full(total=70, prompt=60, cached=30, output=10))
    usage.commit_round()
    assert usage.totals() == {
        "total": 120, "prompt": 100, "cached": 60, "output": 20, "thoughts": 0,
    }


def test_stream_usage_ignores_chunks_with_no_usage():
    """Most chunks carry no usage_metadata; they must not blank the last reading."""
    usage = _StreamUsage()
    usage.observe(_full(total=50, prompt=40, cached=30, output=10))
    usage.observe(_Resp(None))
    usage.observe(object())
    assert usage.totals()["total"] == 50


def test_stream_usage_totals_is_idempotent():
    """`totals()` commits an open round; calling it twice must not double-count
    (the `finally` in stream_agentic can run after an explicit commit)."""
    usage = _StreamUsage()
    usage.observe(_full(total=50, prompt=40, cached=30, output=10))
    assert usage.totals() == usage.totals()


def test_stream_usage_commit_round_is_safe_when_nothing_was_observed():
    """A round that errored before its first chunk still hits commit_round()."""
    usage = _StreamUsage()
    usage.commit_round()
    usage.commit_round()
    usage.observe(_full(total=7, prompt=5, cached=0, output=2))
    usage.commit_round()
    assert usage.totals()["total"] == 7


def test_stream_usage_tolerates_garbage_fields():
    usage = _StreamUsage()
    usage.observe(_Resp(_Usage(
        total_token_count="not-a-number",
        prompt_token_count=float("inf"),
        cached_content_token_count=None,
        candidates_token_count=True,
    )))
    assert usage.totals() == {
        "total": 0, "prompt": 0, "cached": 0, "output": 0, "thoughts": 0,
    }


# ── thoughts_token_count (the report thinking-budget work) ────────

def test_thoughts_tokens_are_captured():
    """Thinking bills at the OUTPUT rate, so an uncapped reasoning step is a real
    cost line. Without this field there is no way to confirm from production logs
    that a cap took effect — see SYSTEM_DESIGN_GUIDELINES 9b.7."""
    assert _response_usage(_full(thoughts=391))["thoughts"] == 391


def test_thoughts_absent_degrades_to_none_not_zero():
    """A model that does not report the field must read None ('not reported'),
    never 0 ('measured, and it did not think') — collapsing them would make an
    uncapped call indistinguishable from a capped one."""
    usage = _response_usage(_Resp(_Usage(candidates_token_count=20)))
    assert usage["thoughts"] is None
    assert usage["output"] == 20


def test_thoughts_accumulate_across_agentic_rounds():
    usage = _StreamUsage()
    usage.observe(_full(thoughts=30))
    usage.commit_round()
    usage.observe(_full(thoughts=12))
    assert usage.totals()["thoughts"] == 42


# ── _log_gemini_usage ─────────────────────────────────────────────

def test_log_emits_greppable_marker_and_cache_percentage(caplog):
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        _log_gemini_usage(
            {"total": 100, "prompt": 80, "cached": 60, "output": 20},
            call_site="stream_text", model="gemini-2.5-flash", tag="sess-1",
        )
    record = caplog.text
    assert "GEMINI_USAGE" in record
    assert "cached_tok=60" in record
    assert "cached_pct=75.0" in record
    assert "call_site=stream_text" in record
    assert "tag=sess-1" in record


def test_log_zero_prompt_does_not_divide_by_zero(caplog):
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        _log_gemini_usage(
            {"total": 0, "prompt": 0, "cached": 0, "output": 0},
            call_site="stream_text", model="m",
        )
    assert "cached_pct=0.0" in caplog.text


def test_log_never_raises_on_a_malformed_usage_dict(caplog):
    """Telemetry must not be able to break a user-facing call."""
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        _log_gemini_usage({}, call_site="x", model="m")
        _log_gemini_usage({"prompt": "junk", "cached": None}, call_site="x", model="m")
    assert "GEMINI_USAGE" in caplog.text or "GEMINI_USAGE log failed" in caplog.text


# ── Wiring: the log must fire on EVERY exit path of the two streams ──
#
# The extractors above being correct is worthless if the `finally` does not run.
# These drive the real generators against a stubbed SDK, reusing the fake shapes
# from test_gemini_streaming.py.

from app.integrations import gemini as gem  # noqa: E402


class _Part:
    def __init__(self, text, thought=False):
        self._t, self.thought = text, thought

    @property
    def text(self):
        if self._t is None:
            raise ValueError("no text in this part")
        return self._t


class _Chunk:
    """One streamed chunk; `usage` mirrors the SDK's per-chunk usage_metadata."""

    def __init__(self, *parts, usage=None):
        self.candidates = [type("C", (), {"content": type("Ct", (), {"parts": list(parts)})()})()]
        if usage is not None:
            self.usage_metadata = usage


class _Models:
    def __init__(self, chunks, raise_at=None):
        self._chunks, self._raise_at = chunks, raise_at

    async def generate_content_stream(self, *, model, contents, config):
        chunks, raise_at = self._chunks, self._raise_at

        async def _gen():
            for i, c in enumerate(chunks):
                if raise_at is not None and i == raise_at:
                    raise RuntimeError("boom mid-stream")
                yield c

        return _gen()


def _client(models=None, chats=None):
    c = gem.GeminiClient.__new__(gem.GeminiClient)
    c.model_name = "gemini-2.5-flash"
    c._temperature, c._max_tokens = 0.7, 128
    c._client = type("Cl", (), {"aio": type("Aio", (), {"models": models, "chats": chats})()})()
    gem._quota_circuit.record_success()
    return c


def _usage(total=100, prompt=80, cached=60, output=20):
    return _Usage(
        total_token_count=total, prompt_token_count=prompt,
        cached_content_token_count=cached, candidates_token_count=output,
    )


@pytest.mark.asyncio
async def test_stream_text_logs_usage_on_success(caplog):
    c = _client(models=_Models([
        _Chunk(_Part("Apple ")),
        _Chunk(_Part("is solid."), usage=_usage(total=100, prompt=80, cached=60, output=20)),
    ]))
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        out = [pair async for pair in c.stream_text("prompt", usage_tag="sess-42")]
    assert out == [("answer", "Apple "), ("answer", "is solid.")]
    assert "GEMINI_USAGE call_site=stream_text" in caplog.text
    assert "cached_tok=60" in caplog.text
    assert "cached_pct=75.0" in caplog.text
    assert "tag=sess-42" in caplog.text


@pytest.mark.asyncio
async def test_stream_text_logs_usage_even_when_the_stream_raises(caplog):
    """An outage still burned the input tokens — the turn that cost money without
    delivering an answer is exactly the one worth seeing."""
    c = _client(models=_Models(
        [_Chunk(_Part("partial"), usage=_usage(total=50, prompt=40, cached=0, output=10)),
         _Chunk(_Part("never"))],
        raise_at=1,
    ))
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        with pytest.raises(RuntimeError):
            [pair async for pair in c.stream_text("prompt")]
    assert "GEMINI_USAGE call_site=stream_text" in caplog.text
    assert "prompt_tok=40" in caplog.text


@pytest.mark.asyncio
async def test_stream_text_logs_usage_when_the_consumer_disconnects(caplog):
    """A client disconnect closes the async generator via GeneratorExit. Logging
    only on the happy path would hide every abandoned (but billed) turn."""
    c = _client(models=_Models([
        _Chunk(_Part("one"), usage=_usage(total=50, prompt=40, cached=20, output=10)),
        _Chunk(_Part("two")),
        _Chunk(_Part("three")),
    ]))
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        agen = c.stream_text("prompt")
        assert await agen.__anext__() == ("answer", "one")
        await agen.aclose()          # consumer walked away mid-stream
    assert "GEMINI_USAGE call_site=stream_text" in caplog.text
    assert "prompt_tok=40" in caplog.text


class _Chat:
    """Multi-round agentic chat stub: one scripted chunk-list per round."""

    def __init__(self, rounds):
        self._rounds, self._i = rounds, 0

    async def send_message_stream(self, message):
        chunks = self._rounds[min(self._i, len(self._rounds) - 1)]
        self._i += 1

        async def _gen():
            for c in chunks:
                yield c

        return _gen()


@pytest.mark.asyncio
async def test_stream_agentic_logs_usage_on_the_early_return_path(caplog):
    """`stream_agentic` returns from INSIDE the loop when no tool was called —
    a bare post-loop log would never run on the most common path."""
    chats = type("Ch", (), {
        "create": staticmethod(lambda **kw: _Chat([
            [_Chunk(_Part("done."), usage=_usage(total=90, prompt=70, cached=35, output=20))],
        ]))
    })()
    c = _client(chats=chats)
    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        out = [e async for e in c.stream_agentic("p", tools=[], tool_handlers={}, usage_tag="t1")]
    assert out == [("answer", "done.")]
    assert "GEMINI_USAGE call_site=stream_agentic" in caplog.text
    assert "prompt_tok=70" in caplog.text
    assert "cached_pct=50.0" in caplog.text


@pytest.mark.asyncio
async def test_stream_agentic_sums_rounds_rather_than_reporting_only_the_last(caplog):
    """Two tool rounds then an answer: input tokens are spent EVERY round, so a
    last-round-wins reading would under-report a 3-round turn by ~2/3."""
    class _FC:
        name, args = "get_stock_chart_data", {"symbol": "AAPL"}

    class _ToolPart:
        function_call = _FC()
        thought = False

        @property
        def text(self):
            raise ValueError("no text")

    rounds = [
        [_Chunk(_ToolPart(), usage=_usage(total=100, prompt=100, cached=0, output=0))],
        [_Chunk(_Part("final."), usage=_usage(total=150, prompt=120, cached=0, output=30))],
    ]
    chats = type("Ch", (), {"create": staticmethod(lambda **kw: _Chat(rounds))})()
    c = _client(chats=chats)

    async def _handler(args):
        return {"price": 1.0}

    with caplog.at_level(logging.INFO, logger="app.integrations.gemini"):
        out = [e async for e in c.stream_agentic(
            "p", tools=[], tool_handlers={"get_stock_chart_data": _handler},
        )]
    assert ("answer", "final.") in out
    assert "prompt_tok=220" in caplog.text   # 100 + 120, not 120
    assert "total_tok=250" in caplog.text
