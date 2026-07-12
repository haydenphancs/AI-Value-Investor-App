"""Offline tests for the multi-agent chat router + specialist registry (Phase 3).

The router NEVER raises — every failure path must degrade to the general specialist in single mode,
so routing can't break the chat. The registry is a pure keyed lookup with a loud general fallback.
No network: gemini.generate_json is a fake.
"""

import pytest

from app.services.agents import chat_router
from app.services.agents.chat_specialists import (
    SPECIALIST_KEYS,
    apply_specialist,
    get_specialist,
)


class _FakeGemini:
    def __init__(self, text=None, raises=False):
        self._text = text
        self._raises = raises
        self.calls = 0

    async def generate_json(self, prompt, system_instruction=None, model_name=None):
        self.calls += 1
        if self._raises:
            raise RuntimeError("router backend down")
        return {"text": self._text}


# ── Specialist registry ─────────────────────────────────────────────────────

def test_get_specialist_known_unknown_case():
    assert get_specialist("valuation").key == "valuation"
    assert get_specialist("VALUATION").key == "valuation"
    assert get_specialist("  Macro  ").key == "macro"
    assert get_specialist("banana").key == "general"   # unknown → general
    assert get_specialist("").key == "general"
    assert get_specialist(None).key == "general"       # type: ignore[arg-type]


def test_apply_specialist_appends_focus_but_general_is_unchanged():
    base = "You are Cay AI. Be concise."
    val = apply_specialist(base, "valuation")
    assert val.startswith(base) and "VALUATION lens" in val
    assert apply_specialist(base, "general") == base   # general → no extension
    assert apply_specialist(base, "banana") == base    # unknown → general → no extension


def test_specialist_keys_cover_registry():
    for k in SPECIALIST_KEYS:
        assert get_specialist(k).key == k
    assert SPECIALIST_KEYS[-1] == "general"


# ── Router ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_single_focused_lens():
    g = _FakeGemini('{"specialists": ["valuation"], "cross_domain": false}')
    r = await chat_router.route_question(g, "Is AAPL cheap right now?")
    assert r["specialists"] == ["valuation"]
    assert r["mode"] == "single"
    assert r["labels"] == ["Valuation"]


@pytest.mark.asyncio
async def test_router_cross_domain_synthesize():
    g = _FakeGemini('{"specialists": ["valuation", "fundamentals"], "cross_domain": true}')
    r = await chat_router.route_question(g, "Is NVDA a good long-term buy?")
    assert r["specialists"] == ["valuation", "fundamentals"]
    assert r["mode"] == "synthesize"
    assert len(r["labels"]) == 2


@pytest.mark.asyncio
async def test_router_cross_domain_flag_but_single_lens_stays_single():
    # cross_domain true but only one valid lens → single (synthesis needs >1).
    g = _FakeGemini('{"specialists": ["macro"], "cross_domain": true}')
    r = await chat_router.route_question(g, "how's the market")
    assert r["specialists"] == ["macro"] and r["mode"] == "single"


@pytest.mark.asyncio
async def test_router_drops_invalid_and_dedups_lenses():
    g = _FakeGemini('{"specialists": ["banana", "valuation", "valuation", "sentiment"], "cross_domain": true}')
    r = await chat_router.route_question(g, "q")
    assert r["specialists"] == ["valuation", "sentiment"]   # banana dropped, dedup, order kept
    assert r["mode"] == "synthesize"


@pytest.mark.asyncio
async def test_router_caps_at_three():
    g = _FakeGemini('{"specialists": ["valuation","fundamentals","macro","sentiment"], "cross_domain": true}')
    r = await chat_router.route_question(g, "q")
    assert len(r["specialists"]) == 3


@pytest.mark.asyncio
async def test_router_empty_specialists_falls_back_general():
    g = _FakeGemini('{"specialists": [], "cross_domain": false}')
    r = await chat_router.route_question(g, "q")
    assert r == {"specialists": ["general"], "mode": "single", "labels": ["General"]}


@pytest.mark.asyncio
async def test_router_bad_json_falls_back():
    g = _FakeGemini("not json at all")
    r = await chat_router.route_question(g, "q")
    assert r["specialists"] == ["general"] and r["mode"] == "single"


@pytest.mark.asyncio
async def test_router_exception_falls_back():
    g = _FakeGemini(raises=True)
    r = await chat_router.route_question(g, "q")
    assert r["specialists"] == ["general"] and r["mode"] == "single"


@pytest.mark.asyncio
async def test_router_empty_message_skips_llm():
    g = _FakeGemini('{"specialists": ["valuation"]}')
    r = await chat_router.route_question(g, "   ")
    assert r["specialists"] == ["general"]
    assert g.calls == 0   # no LLM call for an empty question
