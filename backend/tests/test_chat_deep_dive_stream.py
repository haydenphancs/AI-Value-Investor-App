"""The "AI Analyst" button on the path users actually take.

`_is_deep_dive_request` was consulted ONLY by the non-streaming `generate_response`, and
streaming is on by default (`ChatViewModel.streamingEnabled = true`). So on every real tap:

  * the 24h `market_deep_dive_cache` was never read and never written — a cache nobody hit;
  * there was nowhere to hang a deep-dive answer format, leaving the button's "comprehensive
    analysis" prompt fighting a global STYLE rule that capped answers at 2-3 bullets.

No network, no Supabase, no Gemini.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints.chat import _replay_cached_answer
from app.services.chat_service import ChatService


# The two prompts the app actually ships on its AI Analyst buttons.
_CRYPTO_TAP = ("Give me a comprehensive Deep Analysis of BTCUSD. Analyze the current price "
               "action, market position, key risks, and outlook.")
_INDEX_TAP = ("Give me a comprehensive Market Deep Dive of ^GSPC. Analyze the current "
              "valuation, breadth and sector rotation, and the macro risks.")
_ETF_TAP = ("Give me a comprehensive Deep Analysis of SPY. Cover what it holds, how "
            "concentrated it is, cost, how it has tracked its benchmark, and the key risks.")


@pytest.mark.parametrize("prompt,symbol", [
    (_CRYPTO_TAP, "BTCUSD"), (_INDEX_TAP, "^GSPC"), (_ETF_TAP, "SPY"),
])
def test_every_shipped_button_prompt_is_recognised(prompt, symbol):
    """If a button's wording drifts out of the trigger set, it silently loses both the cache
    and the structured format — with no error anywhere. Pin the shipped strings."""
    assert ChatService._is_deep_dive_request(False, symbol, prompt) is True


def test_an_ordinary_question_is_not_a_deep_dive():
    """Anti-vacuity: a predicate that returned True for everything would pass the table above."""
    for q in ("What is the P/E?", "Hi", "why did it drop today", "what are the top holdings"):
        assert ChatService._is_deep_dive_request(False, "BTCUSD", q) is False


# ── The streaming prep carries the deep-dive state ──────────────────────────

async def _prep(svc, message, *, cached=None):
    """Drive `prepare_stream_generation` with every I/O collaborator stubbed out."""
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value=None)
    svc._deterministic_widget = AsyncMock(return_value=None)
    svc._build_sources = lambda *a, **k: None
    svc._check_deep_dive_cache = lambda *a, **k: cached
    return await svc.prepare_stream_generation(
        session_id="s", user_message=message, session_type="STOCK",
        stock_id="BTCUSD", context="the crypto screen", context_type="CRYPTO",
        reference_id="BTCUSD",
    )


@pytest.mark.asyncio
async def test_stream_prep_reports_the_deep_dive_and_formats_for_it(monkeypatch):
    monkeypatch.setattr(
        "app.services.chat_context_resolver.get_chat_context_resolver",
        lambda: type("R", (), {"resolve": AsyncMock(return_value="grounding")})(),
    )
    prep = await _prep(ChatService.__new__(ChatService), _CRYPTO_TAP)
    assert prep["is_deep_dive"] is True
    assert "FULL BRIEF" in prep["system_instruction"]
    assert "AT MOST 2-3 brief" not in prep["system_instruction"]
    # Carried so the endpoint can write the answer back under the same key it looked up.
    assert prep["deep_dive_context"]


@pytest.mark.asyncio
async def test_an_ordinary_streamed_turn_keeps_the_brief_style(monkeypatch):
    monkeypatch.setattr(
        "app.services.chat_context_resolver.get_chat_context_resolver",
        lambda: type("R", (), {"resolve": AsyncMock(return_value="grounding")})(),
    )
    prep = await _prep(ChatService.__new__(ChatService), "What is the market cap?")
    assert prep["is_deep_dive"] is False
    assert "AT MOST 2-3 brief" in prep["system_instruction"]
    assert prep["deep_dive_cached"] is None
    assert prep["deep_dive_context"] is None


@pytest.mark.asyncio
async def test_a_cached_brief_is_surfaced_to_the_endpoint(monkeypatch):
    """The hit is what makes a second tap free — the endpoint replays it with no Gemini call."""
    monkeypatch.setattr(
        "app.services.chat_context_resolver.get_chat_context_resolver",
        lambda: type("R", (), {"resolve": AsyncMock(return_value="grounding")})(),
    )
    prep = await _prep(ChatService.__new__(ChatService), _CRYPTO_TAP, cached="a stored brief")
    assert prep["deep_dive_cached"] == "a stored brief"


# ── Replaying a cached brief ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replay_reassembles_the_text_exactly():
    text = "".join(f"section {i} body. " for i in range(120))
    events = [ev async for ev in _replay_cached_answer(text)]
    assert {kind for kind, _ in events} == {"answer"}
    assert "".join(payload for _, payload in events) == text


@pytest.mark.asyncio
async def test_replay_chunks_rather_than_sending_one_wall_of_text():
    """iOS meters the reveal per chunk; one 4KB frame lands as an instant wall."""
    events = [ev async for ev in _replay_cached_answer("x" * 2000)]
    assert len(events) > 1


@pytest.mark.asyncio
async def test_replay_of_empty_text_yields_nothing():
    """Must not emit an empty frame — the endpoint treats a blank stream as a failure."""
    assert [ev async for ev in _replay_cached_answer("")] == []


# ── The output ceiling must fit the format we asked for ─────────────────────

def test_a_deep_dive_gets_a_larger_output_ceiling():
    """Found on a live SPY tap, not in review: under the ordinary 1200-token cap the brief was
    cut off MID-SENTENCE, leaving a dangling `**` that rendered as literal asterisks.

    `gemini-2.5-flash` counts THINKING in `output_tok`, and the agentic round spent 683 of the
    budget on thinking + tool calls before the prose began. A format directive the token budget
    cannot hold is worse than no directive — it produces a confidently truncated answer.
    """
    from app.config import settings
    from app.services.chat_service import _chat_output_cap

    assert _chat_output_cap(True) == settings.CHAT_DEEP_DIVE_MAX_OUTPUT_TOKENS
    assert _chat_output_cap(False) == settings.CHAT_MAX_OUTPUT_TOKENS
    # The whole point: the deep-dive ceiling must actually be higher.
    assert settings.CHAT_DEEP_DIVE_MAX_OUTPUT_TOKENS > settings.CHAT_MAX_OUTPUT_TOKENS


def test_the_ordinary_ceiling_is_not_raised_for_everyone():
    """The 1200 cap is a deliberate per-turn cost control (reports keep 8192, chat does not).
    Raising it globally to fit one button would move the cost base of every turn."""
    from app.config import settings
    assert settings.CHAT_MAX_OUTPUT_TOKENS == 1200


@pytest.mark.asyncio
async def test_the_synthesis_path_also_honours_the_deep_dive_ceiling(monkeypatch):
    """The endpoint's cap never reached `stream_synthesis` — it had `CHAT_MAX_OUTPUT_TOKENS`
    hard-coded internally, so a deep dive routed to a specialist silently kept the 1200 ceiling.

    Found ONLY at runtime: the endpoint logged `cap=3500` while the answer still truncated at
    ~220 characters, because the route was a specialist one and never used that number.
    """
    from app.config import settings
    from app.services.chat_service import ChatService

    caps: list = []

    class _Gem:
        async def stream_agentic(self, *a, **kw):
            caps.append(kw.get("max_output_tokens"))
            yield "answer", "specialist view"

        async def stream_text(self, *a, **kw):
            caps.append(kw.get("max_output_tokens"))
            yield "answer", "merged"

    svc = ChatService.__new__(ChatService)
    svc.gemini = _Gem()
    prep = {"prompt": "p", "system_instruction": "sys", "is_deep_dive": True}
    route = {"specialists": ["macro"], "labels": ["Macro"], "mode": "synthesize"}
    _ = [ev async for ev in svc.stream_synthesis(prep, "deep dive", route, [], {})]

    # The MERGE — the answer the user actually reads — must get the larger ceiling.
    assert settings.CHAT_DEEP_DIVE_MAX_OUTPUT_TOKENS in caps
    # The per-specialist runs deliberately keep the ordinary cap: their text is truncated to
    # 1200 chars when merged, so a larger budget there is spent and then thrown away.
    assert settings.CHAT_MAX_OUTPUT_TOKENS in caps


@pytest.mark.asyncio
async def test_an_ordinary_synthesis_turn_keeps_the_ordinary_ceiling(monkeypatch):
    from app.config import settings
    from app.services.chat_service import ChatService

    caps: list = []

    class _Gem:
        async def stream_agentic(self, *a, **kw):
            caps.append(kw.get("max_output_tokens"))
            yield "answer", "v"

        async def stream_text(self, *a, **kw):
            caps.append(kw.get("max_output_tokens"))
            yield "answer", "m"

    svc = ChatService.__new__(ChatService)
    svc.gemini = _Gem()
    prep = {"prompt": "p", "system_instruction": "sys", "is_deep_dive": False}
    route = {"specialists": ["macro"], "labels": ["Macro"], "mode": "synthesize"}
    _ = [ev async for ev in svc.stream_synthesis(prep, "what is the p/e", route, [], {})]

    assert caps and set(caps) == {settings.CHAT_MAX_OUTPUT_TOKENS}
