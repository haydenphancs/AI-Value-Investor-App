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
    # degraded=False: the router ANSWERED, it just selected nothing usable. That is a
    # real classification resolving to `general`, not a transport failure — the
    # distinction is what `select_model` keys the cheap model off.
    assert r == {
        "specialists": ["general"], "mode": "single", "labels": ["General"], "degraded": False,
    }


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


# ── `degraded` flag ─────────────────────────────────────────────────────────
#
# A genuine `general` classification and a router FAILURE both produce
# specialists == ["general"], and `select_model` has to tell them apart: during a
# Gemini outage every turn falls back, and without this flag the whole product
# would silently downgrade to the cheap model at exactly the moment it is least
# healthy.

@pytest.mark.asyncio
async def test_successful_route_is_not_degraded():
    g = _FakeGemini('{"specialists": ["valuation"], "cross_domain": false}')
    assert (await chat_router.route_question(g, "is it cheap?"))["degraded"] is False


@pytest.mark.asyncio
async def test_genuine_general_classification_is_not_degraded():
    g = _FakeGemini('{"specialists": ["general"], "cross_domain": false}')
    r = await chat_router.route_question(g, "hello")
    assert r["specialists"] == ["general"] and r["degraded"] is False


@pytest.mark.parametrize("gemini", [
    _FakeGemini("not json at all"),
    _FakeGemini(raises=True),
    _FakeGemini('{"specialists": []}'),
    _FakeGemini('{"specialists": ["banana"]}'),
])
@pytest.mark.asyncio
async def test_failure_paths_are_marked_degraded_or_classified(gemini):
    """Transport/JSON failures degrade; an empty-or-unknown lens list is still a
    real classification that legitimately resolves to `general`."""
    r = await chat_router.route_question(gemini, "q")
    assert r["specialists"] == ["general"]
    assert isinstance(r["degraded"], bool)


@pytest.mark.asyncio
async def test_transport_failure_specifically_is_degraded():
    assert (await chat_router.route_question(_FakeGemini(raises=True), "q"))["degraded"] is True


@pytest.mark.asyncio
async def test_empty_message_shortcut_is_degraded():
    """No classification happened, so it must not license the cheap model."""
    assert (await chat_router.route_question(_FakeGemini(), "   "))["degraded"] is True


# ── select_model — the routing POLICY (pure) ────────────────────────────────

FLAG = "app.config.settings.CHAT_MODEL_ROUTING_ENABLED"


def _route(specialist="education", mode="single", degraded=False):
    return {"specialists": [specialist], "mode": mode, "labels": ["X"], "degraded": degraded}


def _pick(route, *, ticker=False, context=False):
    return chat_router.select_model(route, has_ticker=ticker, has_client_context=context)


def _expensive():
    from app.config import settings
    return settings.GEMINI_MODEL


def _cheap():
    from app.config import settings
    return settings.CHAT_CHEAP_MODEL


def test_routing_disabled_always_uses_the_flagship(monkeypatch):
    monkeypatch.setattr(FLAG, False)
    assert _pick(_route("education")) == _expensive()
    assert _pick(_route("general")) == _expensive()


@pytest.mark.parametrize("specialist", ["education", "general"])
def test_conceptual_ticker_less_question_uses_the_cheap_model(monkeypatch, specialist):
    monkeypatch.setattr(FLAG, True)
    assert _pick(_route(specialist)) == _cheap()


def test_degraded_route_never_uses_the_cheap_model(monkeypatch):
    """THE fail-closed case: a Gemini outage makes every turn look like `general`."""
    monkeypatch.setattr(FLAG, True)
    assert _pick(chat_router._fallback()) == _expensive()
    assert _pick(_route("general", degraded=True)) == _expensive()


def test_route_without_a_degraded_key_is_treated_as_degraded(monkeypatch):
    """A hand-built route dict has not proven a classification happened."""
    monkeypatch.setattr(FLAG, True)
    assert _pick({"specialists": ["education"], "mode": "single"}) == _expensive()


@pytest.mark.parametrize("ticker,context", [(True, False), (False, True), (True, True)])
def test_a_ticker_or_on_screen_data_forces_the_flagship(monkeypatch, ticker, context):
    """'What does this P/E mean?' on a STOCK screen classifies as `education` but
    still reasons over a live grounding block and calls tools."""
    monkeypatch.setattr(FLAG, True)
    assert _pick(_route("education"), ticker=ticker, context=context) == _expensive()


def test_synthesize_mode_keeps_the_flagship(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    assert _pick(_route("general", mode="synthesize")) == _expensive()


def test_only_conceptual_lenses_are_eligible(monkeypatch):
    """Exhaustive over the real registry so a NEW specialist is expensive by
    default rather than silently inheriting the discount."""
    monkeypatch.setattr(FLAG, True)
    for key in SPECIALIST_KEYS:
        expected = _cheap() if key in {"education", "general"} else _expensive()
        assert _pick(_route(key)) == expected, key


@pytest.mark.parametrize("route", [
    {},
    {"specialists": [], "mode": "single", "degraded": False},
    {"specialists": None, "mode": "single", "degraded": False},
    {"specialists": ["education", "general"], "mode": "single", "degraded": False},
    {"specialists": ["education"], "mode": None, "degraded": False},
    {"specialists": ["EDUCATION"], "mode": "single", "degraded": False},
])
def test_malformed_routes_fall_back_to_the_flagship(monkeypatch, route):
    """Every unknown resolves to the better model: a wrong cheap answer is read by
    a user, a wrong expensive one costs a fraction of a cent."""
    monkeypatch.setattr(FLAG, True)
    assert _pick(route) == _expensive()


def test_select_model_does_not_mutate_the_route(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    route = _route("education")
    before = dict(route)
    _pick(route)
    assert route == before
