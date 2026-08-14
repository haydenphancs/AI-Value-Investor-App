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
