"""Who actually gets a personalized answer (Phase 4).

The gate is the whole feature's safety story, so it is tested at the ENDPOINT seam
(`chat._reader_lens_for`) rather than only on the pure function underneath it: the pure
gate being correct is worthless if the endpoint reads the profile before consulting it,
or lets a store failure take down the turn.

Modelled on test_signals_entitlement.py.
"""

import pytest

from app.api.v1.endpoints import chat as chat_ep

FLAG = "app.config.settings.CHAT_PERSONALIZATION_ENABLED"
CONSENTED = "2026-08-13T00:00:00+00:00"


def _install(monkeypatch, profile=None, raises=False):
    """Stub the profile service; record whether it was consulted at all."""
    calls = {"n": 0}

    class _Svc:
        def get_profile(self, user_id):
            calls["n"] += 1
            if raises:
                raise RuntimeError("profile store down")
            return dict(profile or {})

    monkeypatch.setattr(
        "app.services.user_investor_profile_service.get_user_investor_profile_service",
        lambda: _Svc(),
    )
    return calls


def _profile(**over):
    base = {
        "experience_level": "new",
        "topics": ["dividends"],
        "learning_goals": [],
        "follow_signals": [],
        "explanation_style": "balanced",
        "answer_depth": "brief",
        "consented_at": CONSENTED,
    }
    base.update(over)
    return base


@pytest.mark.parametrize("tier", ["pro", "premium"])
def test_entitled_consented_reader_gets_a_lens(monkeypatch, tier):
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile())
    lens = chat_ep._reader_lens_for({"id": "u1", "tier": tier})
    assert lens and "dividends" in lens


@pytest.mark.parametrize("tier", ["free", None, "", "wizard"])
def test_unentitled_tier_gets_none(monkeypatch, tier):
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile())
    assert chat_ep._reader_lens_for({"id": "u1", "tier": tier}) is None


def test_guest_gets_none(monkeypatch):
    """A guest identity hardcodes tier=free, so this is belt and braces."""
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile())
    assert chat_ep._reader_lens_for({"id": "g1", "tier": "free", "is_guest": True}) is None


def test_feature_flag_off_gets_none(monkeypatch):
    monkeypatch.setattr(FLAG, False)
    _install(monkeypatch, _profile())
    assert chat_ep._reader_lens_for({"id": "u1", "tier": "premium"}) is None


def test_unconsented_profile_gets_none(monkeypatch):
    """Profiles captured before the amended Terms existed must not be applied."""
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile(consented_at=None))
    assert chat_ep._reader_lens_for({"id": "u1", "tier": "premium"}) is None


def test_empty_profile_gets_none(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile(
        experience_level="learning", topics=[], learning_goals=[],
    ))
    assert chat_ep._reader_lens_for({"id": "u1", "tier": "premium"}) is None


# ── the endpoint seam specifically ──────────────────────────────────────────

def test_the_profile_is_not_even_read_when_it_cannot_apply(monkeypatch):
    """Cost + latency: the common case is the feature off or a free caller, and neither
    may pay a Supabase round trip on the answer path."""
    monkeypatch.setattr(FLAG, False)
    calls = _install(monkeypatch, _profile())
    chat_ep._reader_lens_for({"id": "u1", "tier": "premium"})
    assert calls["n"] == 0, "read the profile despite the feature being off"

    monkeypatch.setattr(FLAG, True)
    calls = _install(monkeypatch, _profile())
    chat_ep._reader_lens_for({"id": "u1", "tier": "free"})
    assert calls["n"] == 0, "read the profile for a caller who can never use it"


def test_a_store_failure_degrades_instead_of_breaking_the_turn(monkeypatch):
    """Personalization is a presentation nicety; a profile-store hiccup must produce a
    normal answer, never a failed chat turn."""
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, raises=True)
    assert chat_ep._reader_lens_for({"id": "u1", "tier": "premium"}) is None


def test_a_missing_user_dict_does_not_raise(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile())
    assert chat_ep._reader_lens_for({}) is None


# ── Memory facts ride the SAME gate (Phase 7) ────────────────────────────────
#
# Memory is OBSERVED rather than stated, so it must not accumulate — or be applied — for
# a reader who declined personalization. Both sides consult the same stored consent, so
# the read and the write can never disagree about who opted in.

MEM_FLAG = "app.config.settings.CHAT_MEMORY_FACTS_ENABLED"


def _install_facts(monkeypatch, facts=None, recorded=None):
    class _Svc:
        def top_facts(self, user_id, limit=8):
            return dict(facts or {})

        def record(self, user_id, pairs):
            if recorded is not None:
                recorded.append((user_id, list(pairs)))
            return len(list(pairs))

    monkeypatch.setattr(
        "app.services.user_memory_facts_service.get_user_memory_facts_service",
        lambda: _Svc(),
    )


def test_memory_is_appended_to_the_lens_when_enabled(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _install(monkeypatch, _profile())
    _install_facts(monkeypatch, {"ticker_discussed": ["NVDA"]})
    lens = chat_ep._reader_lens_for({"id": "u1", "tier": "pro"})
    assert lens and "NVDA" in lens and "ASKING ABOUT" in lens


def test_memory_flag_off_leaves_the_preference_block_intact(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, False)
    _install(monkeypatch, _profile())
    _install_facts(monkeypatch, {"ticker_discussed": ["NVDA"]})
    lens = chat_ep._reader_lens_for({"id": "u1", "tier": "pro"})
    assert lens and "NVDA" not in lens, "memory leaked in with its flag off"
    assert "dividends" in lens, "the preference block should be unaffected"


def test_no_memory_without_consent(monkeypatch):
    """The preference gate refuses first, so memory is never even read."""
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _install(monkeypatch, _profile(consented_at=None))
    _install_facts(monkeypatch, {"ticker_discussed": ["NVDA"]})
    assert chat_ep._reader_lens_for({"id": "u1", "tier": "pro"}) is None


# ── recording ────────────────────────────────────────────────────────────────

def test_recording_stores_the_ticker_and_the_routed_theme(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _install(monkeypatch, _profile())
    recorded = []
    _install_facts(monkeypatch, recorded=recorded)
    chat_ep._record_memory_facts(
        {"id": "u1", "tier": "premium"}, "NVDA", {"specialists": ["valuation"]},
    )
    assert recorded and recorded[0][0] == "u1"
    assert ("ticker_discussed", "NVDA") in recorded[0][1]
    assert ("question_theme", "valuation") in recorded[0][1]


@pytest.mark.parametrize("user,stock,route", [
    ({"id": "g1", "tier": "free", "is_guest": True}, "NVDA", {"specialists": ["valuation"]}),
    ({"id": "u1", "tier": "free"}, "NVDA", {"specialists": ["valuation"]}),
    ({"id": "u1", "tier": "pro"}, None, None),          # nothing observable this turn
    ({"id": "u1", "tier": "pro"}, None, {"specialists": []}),
])
def test_recording_is_skipped_when_it_should_not_happen(monkeypatch, user, stock, route):
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _install(monkeypatch, _profile())
    recorded = []
    _install_facts(monkeypatch, recorded=recorded)
    chat_ep._record_memory_facts(user, stock, route)
    assert recorded == []


def test_recording_skipped_without_consent(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _install(monkeypatch, _profile(consented_at=None))
    recorded = []
    _install_facts(monkeypatch, recorded=recorded)
    chat_ep._record_memory_facts({"id": "u1", "tier": "pro"}, "NVDA", {"specialists": ["macro"]})
    assert recorded == [], "recorded memory for a reader who never consented"


def test_recording_never_raises(monkeypatch):
    """It runs AFTER the answer is persisted — a failure here must be invisible."""
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _install(monkeypatch, raises=True)
    chat_ep._record_memory_facts({"id": "u1", "tier": "pro"}, "NVDA", {"specialists": ["macro"]})


# ── The wire field that REPORTS the gate ──────────────────────────────────────
#
# Everything above pins the runtime gate. Nothing pinned the field that tells the USER
# what the gate decided, and the two drifted: `_profile_response` computed
# `applied = signals_unlocked(tier) and not is_empty_profile(profile)` — the tier and
# non-empty arms only. With the feature flag off (the shipped default) a consented Pro
# reader was told "On — Cay AI tailors how it explains things" while nothing was tailored.
#
# `applied` now delegates to `may_apply_profile`, so these assertions and the ones above
# are the same predicate seen from two sides. Keep them in one file for that reason.

from app.api.v1.endpoints.users import _profile_response  # noqa: E402


def _applied(profile, tier):
    return _profile_response(dict(profile), {"id": "u1", "tier": tier}).applied


def test_applied_is_false_while_the_feature_flag_is_off(monkeypatch):
    """THE regression. Every other arm passes; only the flag is off."""
    monkeypatch.setattr(FLAG, False)
    assert _applied(_profile(), "pro") is False, (
        "reported applied=True with CHAT_PERSONALIZATION_ENABLED off — the UI would "
        "claim answers are tailored while the feature does nothing"
    )


def test_applied_is_false_without_consent(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    assert _applied(_profile(consented_at=None), "pro") is False


@pytest.mark.parametrize("tier", ["free", None, "", "wizard"])
def test_applied_is_false_for_an_unentitled_tier(monkeypatch, tier):
    monkeypatch.setattr(FLAG, True)
    assert _applied(_profile(), tier) is False


def test_applied_is_false_for_an_all_default_profile(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    empty = _profile(experience_level="learning", topics=[], learning_goals=[])
    assert _applied(empty, "pro") is False


@pytest.mark.parametrize("tier", ["pro", "premium"])
def test_applied_is_true_only_when_all_four_arms_pass(monkeypatch, tier):
    monkeypatch.setattr(FLAG, True)
    assert _applied(_profile(), tier) is True


def test_applied_agrees_with_the_runtime_gate_it_reports(monkeypatch):
    """The invariant, stated directly: the wire field and the chat path never disagree.

    A future edit that re-derives `applied` locally would pass every case above while
    still being able to drift on a fifth arm added to `may_apply_profile`. This asserts
    equivalence across the whole grid instead of enumerating outcomes.
    """
    from app.services.agents.investor_profile_prompt import may_apply_profile

    for flag in (True, False):
        monkeypatch.setattr(FLAG, flag)
        for tier in ("pro", "premium", "free", None, "wizard"):
            for prof in (
                _profile(),
                _profile(consented_at=None),
                _profile(experience_level="learning", topics=[], learning_goals=[]),
            ):
                assert _applied(prof, tier) is may_apply_profile(dict(prof), tier), (
                    f"wire field disagrees with the runtime gate: flag={flag} tier={tier!r}"
                )


# ── The async seam: same verdict, without stalling the event loop ─────────────
#
# `_reader_lens_for` is synchronous and does a Supabase round trip (two with memory on).
# Both call sites are `async def` on the answer path, so calling it directly blocks the
# loop for every concurrent request. `_reader_lens_for_async` moves the read to a thread —
# but only AFTER a pure gate, so the common case (feature off, or a free/guest caller)
# still costs nothing at all. That second half is the part worth pinning: a wrapper that
# always hops is a regression for the majority of traffic.

import asyncio  # noqa: E402


def _no_thread(monkeypatch):
    """Make any `asyncio.to_thread` call an error, and report whether one was attempted."""
    hops = {"n": 0}

    async def _boom(fn, *a, **kw):
        hops["n"] += 1
        raise AssertionError("asyncio.to_thread called on a turn that cannot personalize")

    monkeypatch.setattr(asyncio, "to_thread", _boom)
    return hops


@pytest.mark.asyncio
async def test_async_lens_costs_nothing_when_the_feature_is_off(monkeypatch):
    monkeypatch.setattr(FLAG, False)
    calls = _install(monkeypatch, _profile())
    _no_thread(monkeypatch)
    assert await chat_ep._reader_lens_for_async({"id": "u1", "tier": "pro"}) is None
    assert calls["n"] == 0, "read the profile with the feature off"


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["free", None, "", "wizard"])
async def test_async_lens_costs_nothing_for_an_unentitled_caller(monkeypatch, tier):
    monkeypatch.setattr(FLAG, True)
    calls = _install(monkeypatch, _profile())
    _no_thread(monkeypatch)
    assert await chat_ep._reader_lens_for_async({"id": "u1", "tier": tier}) is None
    assert calls["n"] == 0, "read the profile for a caller who can never apply it"


@pytest.mark.asyncio
async def test_async_lens_matches_the_sync_implementation(monkeypatch):
    """Same verdict, so the sync function's own tests still cover this path."""
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile())
    user = {"id": "u1", "tier": "pro"}
    assert await chat_ep._reader_lens_for_async(user) == chat_ep._reader_lens_for(user)


@pytest.mark.asyncio
async def test_async_lens_really_uses_a_thread_when_it_reads(monkeypatch):
    """The whole point: the blocking read happens OFF the event loop.

    Without this, `_reader_lens_for_async` could be 'simplified' to a direct call and every
    assertion above would still pass — the bug it exists to prevent is invisible to a
    correctness test.
    """
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile())
    hopped = {"n": 0}
    real = asyncio.to_thread

    async def _counting(fn, *a, **kw):
        hopped["n"] += 1
        return await real(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", _counting)
    lens = await chat_ep._reader_lens_for_async({"id": "u1", "tier": "pro"})
    assert lens and "dividends" in lens
    assert hopped["n"] == 1, "the profile read did not go through a thread"


@pytest.mark.asyncio
async def test_async_lens_never_raises_on_a_malformed_identity(monkeypatch):
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, _profile())
    assert await chat_ep._reader_lens_for_async({}) is None


@pytest.mark.asyncio
async def test_async_lens_survives_a_store_failure(monkeypatch):
    """A profile-store outage degrades to an impersonal answer, as on the sync path."""
    monkeypatch.setattr(FLAG, True)
    _install(monkeypatch, raises=True)
    assert await chat_ep._reader_lens_for_async({"id": "u1", "tier": "pro"}) is None


# ── The WRITE side must also stay off the event loop ─────────────────────────
#
# `_reader_lens_for_async` fixed the read. The write is worse and was missed: one
# `_record_memory_facts` call is up to ~7 sequential blocking Supabase round trips (profile
# read, then a select + upsert per fact, then the eviction select + delete). Run inline from
# an `async def` that stalls the whole worker for every concurrent request.


@pytest.mark.asyncio
async def test_memory_write_costs_nothing_when_the_feature_is_off(monkeypatch):
    monkeypatch.setattr(MEM_FLAG, False)
    _no_thread(monkeypatch)
    await chat_ep._record_memory_facts_async({"id": "u1", "tier": "pro"}, "NVDA", {"specialists": ["macro"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [
    {"id": "g1", "tier": "free", "is_guest": True},
    {"id": "u1", "tier": "free"},
    {"id": "u1", "tier": None},
])
async def test_memory_write_costs_nothing_for_an_ineligible_caller(monkeypatch, user):
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _no_thread(monkeypatch)
    await chat_ep._record_memory_facts_async(user, "NVDA", {"specialists": ["macro"]})


@pytest.mark.asyncio
async def test_memory_write_really_uses_a_thread(monkeypatch):
    """The point of the wrapper. Without this it could be 'simplified' back to a direct
    call and every other assertion here would still pass."""
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _install(monkeypatch, _profile())
    recorded = []
    _install_facts(monkeypatch, recorded=recorded)
    hopped = {"n": 0}
    real = asyncio.to_thread

    async def _counting(fn, *a, **kw):
        hopped["n"] += 1
        return await real(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", _counting)
    await chat_ep._record_memory_facts_async(
        {"id": "u1", "tier": "pro"}, "NVDA", {"specialists": ["valuation"]}
    )
    assert hopped["n"] == 1, "the blocking memory write did not go through a thread"
    assert recorded, "nothing was recorded — the wrapper swallowed the write"


@pytest.mark.asyncio
async def test_memory_write_never_raises(monkeypatch):
    """It runs after the answer is delivered; a failure must stay invisible."""
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    _install(monkeypatch, raises=True)
    await chat_ep._record_memory_facts_async({"id": "u1", "tier": "pro"}, "NVDA", {"specialists": ["macro"]})


def test_a_general_only_turn_does_not_pay_a_profile_read(monkeypatch):
    """`general` is the router's fallback AND its degraded result, and is deliberately not a
    stored theme — so a ticker-less general turn builds a non-empty `pairs` that validates to
    nothing. Checking `pairs` before validating meant paying a Supabase profile round trip,
    on a very common path, to write nothing at all."""
    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr(MEM_FLAG, True)
    calls = _install(monkeypatch, _profile())
    recorded = []
    _install_facts(monkeypatch, recorded=recorded)
    chat_ep._record_memory_facts({"id": "u1", "tier": "pro"}, None, {"specialists": ["general"]})
    assert calls["n"] == 0, "read the profile for a turn that can never record anything"
    assert recorded == []
