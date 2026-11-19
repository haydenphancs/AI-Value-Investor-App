"""Personalized explanations: ONE style directive per turn, and no shared answer for a lens.

Audit 2026-11-15, before `CHAT_PERSONALIZATION_ENABLED` goes on in production:

1. The reader's "Length:" / "Language:" lines were appended AFTER the house STYLE rule, so a
   reader who chose "Go deep" + "Technical" got "SHORT, at most 2-3 bullets, plain language"
   and then "thorough, precise terminology", with nothing saying which won. The choices now
   REPLACE the matching sentences of the one directive (`ChatService._answer_style`), and the
   preference block carries no Length/Language line.
2. A lens turn never replays a shared answer: the AI Analyst cache read is skipped (both doors
   read through `_read_deep_dive_cache`); the starter chips are pinned in
   `test_chat_stream_endpoint.py`.
3. A read-back failure after a successful profile write echoed defaults for every field the
   write did not carry, so the Settings row showed the wrong state.

Hermetic: no network, no Supabase (fakes only).
"""

from __future__ import annotations

import ast
import itertools
import logging
from pathlib import Path

import pytest

from app.services.agents import investor_profile_prompt as ipp
from app.services.agents.investor_profile_prompt import (
    ReaderLens,
    render_profile_block,
    resolve_reader_lens,
)
from app.services.chat_service import ChatService
from app.services.user_investor_profile_service import (
    DEFAULTS,
    ProfileUnreadable,
    UserInvestorProfileService,
    would_personalize,
)

FLAG = "app.config.settings.CHAT_PERSONALIZATION_ENABLED"
CONSENTED = "2026-11-15T12:00:00+00:00"

# The house directive, written out by hand. If this test fails, the default answer shape
# changed for EVERY user — which no personalization change may do.
_HISTORICAL_BRIEF_STYLE = (
    "STYLE: Keep every answer SHORT, direct, and friendly. Lead with a 1-2 sentence direct "
    "answer to what was asked, then AT MOST 2-3 brief supporting bullet points, and only when "
    "they truly add value. Never write long, multi-section essays or ## headings. Do NOT dump "
    "everything you know — answer the specific question. Only expand into full detail if the "
    "user explicitly asks for more. Use plain, conversational language. "
)

_DEPTHS = ["brief", "standard", "deep"]
_STYLES = ["plain_language", "balanced", "technical"]


def _svc() -> ChatService:
    return ChatService.__new__(ChatService)


def _profile(**over):
    base = {
        "experience_level": "learning",
        "explanation_style": "balanced",
        "answer_depth": "brief",
        "topics": [],
        "learning_goals": [],
        "follow_signals": [],
        "consented_at": CONSENTED,
    }
    base.update(over)
    return base


def _count_length_directives(instr: str) -> int:
    """Every way a build can state an answer length."""
    markers = (
        "Keep every answer SHORT",
        "Keep answers MODERATE",
        "prefers THOROUGH answers",
        "THIS IS A FULL BRIEF",
        "- Length:",
    )
    return sum(instr.count(m) for m in markers)


# ── the house directive is untouched ─────────────────────────────────────────

def test_the_default_style_is_byte_identical_to_the_historical_directive():
    assert ChatService._BRIEF_STYLE == _HISTORICAL_BRIEF_STYLE
    assert ChatService._answer_style(is_deep_dive=False) == _HISTORICAL_BRIEF_STYLE
    assert ChatService._answer_style(is_deep_dive=True) == ChatService._DEEP_DIVE_STYLE


def test_no_lens_and_a_legacy_string_lens_both_keep_the_house_directive():
    plain = _svc()._build_system_instruction("NORMAL", None)
    legacy = _svc()._build_system_instruction("NORMAL", None, reader_lens="\nLEGACY BLOCK\n")
    for instr in (plain, legacy):
        assert _HISTORICAL_BRIEF_STYLE in instr
        assert _count_length_directives(instr) == 1
    assert "LEGACY BLOCK" in legacy


def test_the_style_tables_cover_exactly_the_non_default_choices():
    """A new depth/style choice with no sentence here would silently fall back to the house
    sentence while the reader's Settings row says Cay AI tailors its answers."""
    assert set(ChatService._STYLE_LENGTH) == set(ipp._DEPTH) - {DEFAULTS["answer_depth"]}
    assert set(ChatService._STYLE_LANGUAGE) == set(ipp._STYLE) - {DEFAULTS["explanation_style"]}


# ── every depth × style combination: exactly one directive, the reader's own ─────

@pytest.mark.parametrize("depth,style", list(itertools.product(_DEPTHS, _STYLES)))
@pytest.mark.parametrize("deep_dive", [False, True])
def test_every_choice_yields_exactly_one_length_directive(monkeypatch, depth, style, deep_dive):
    monkeypatch.setattr(FLAG, True)
    profile = _profile(answer_depth=depth, explanation_style=style, topics=["energy"])
    lens = resolve_reader_lens(profile, "pro")
    assert lens, "a consented Pro reader with a topic must get a lens"
    instr = _svc()._build_system_instruction(
        "ETF" if deep_dive else "NORMAL", "SPY" if deep_dive else None,
        asset_type="ETF" if deep_dive else "NORMAL",
        reader_lens=lens, is_deep_dive=deep_dive,
    )
    assert _count_length_directives(instr) == 1, instr
    assert "- Length:" not in instr and "- Language:" not in instr
    assert instr.count("STYLE") == 1 or deep_dive  # the deep-dive header reads "STYLE —"
    assert "Topics they follow: energy" in instr, "the preference block itself must stay"

    if deep_dive:
        assert ChatService._DEEP_DIVE_STYLE in instr
        language = ChatService._STYLE_LANGUAGE.get(style)
        if language:
            assert "LANGUAGE: " + language in instr
        else:
            assert "LANGUAGE: " not in instr
        return

    if depth == "brief":
        assert ChatService._STYLE_LENGTH_DEFAULT in instr
    else:
        assert ChatService._STYLE_LENGTH[depth] in instr
        assert "AT MOST 2-3" not in instr, "the house length rule survived beside the reader's"
    if style == "technical":
        assert "plain, conversational" not in instr, "the house language rule survived"
        assert ChatService._STYLE_LANGUAGE["technical"] in instr
    elif style == "plain_language":
        assert ChatService._STYLE_LANGUAGE["plain_language"] in instr
    else:
        assert ChatService._STYLE_LANGUAGE_DEFAULT in instr


def test_the_style_choice_sits_where_the_house_directive_always_sat(monkeypatch):
    """Same slot (before ADVICE_BOUNDARY), so the reader's choice is part of the guarded prefix,
    not a late instruction that could read as overriding the guards."""
    from app.services.agents.persona_config import ADVICE_BOUNDARY

    monkeypatch.setattr(FLAG, True)
    lens = resolve_reader_lens(_profile(answer_depth="deep", topics=["energy"]), "pro")
    instr = _svc()._build_system_instruction("NORMAL", None, reader_lens=lens)
    assert instr.index(ChatService._STYLE_LENGTH["deep"]) < instr.index(ADVICE_BOUNDARY)
    assert instr.index(ADVICE_BOUNDARY) < instr.index("Topics they follow:")


# ── the lens object ──────────────────────────────────────────────────────────

def test_a_style_only_profile_is_a_lens_with_no_preference_block(monkeypatch):
    """"Go deep" alone renders no Experience/Topics/Goals line. It must still change the turn
    (the Settings row says so) without emitting an empty USER PREFERENCES header, and without
    the Learn closing sentence, which needs something the reader follows."""
    monkeypatch.setattr(FLAG, True)
    lens = resolve_reader_lens(_profile(answer_depth="deep"), "pro")
    assert lens and lens.block == "" and lens.answer_depth == "deep"
    instr = _svc()._build_system_instruction("BOOK", None, reader_lens=lens)
    assert ChatService._STYLE_LENGTH["deep"] in instr
    assert "USER PREFERENCES (how this reader" not in instr
    assert "connecting the concept to something the reader follows" not in instr


def test_the_full_block_still_states_the_style_choices_for_the_other_readers():
    """`may_apply_profile` and `would_personalize` ask "did they choose anything?" — a reader
    whose only choice is a depth must still count as personalized."""
    profile = _profile(answer_depth="deep")
    assert "- Length:" in render_profile_block(profile)
    assert render_profile_block(profile, include_style_lines=False) == ""
    assert would_personalize(profile)


@pytest.mark.parametrize("value", ["wizard", None, 7, ""])
def test_an_unknown_or_missing_style_value_is_no_choice(monkeypatch, value):
    monkeypatch.setattr(FLAG, True)
    lens = resolve_reader_lens(_profile(answer_depth=value, topics=["energy"]), "pro")
    assert lens and lens.answer_depth is None
    instr = _svc()._build_system_instruction("NORMAL", None, reader_lens=lens)
    assert _HISTORICAL_BRIEF_STYLE in instr


def test_an_unknown_key_on_the_lens_falls_back_to_the_house_sentence():
    """Defence in depth: a lens built by hand with a bad key never drops the directive."""
    instr = _svc()._build_system_instruction(
        "NORMAL", None, reader_lens=ReaderLens(answer_depth="nope", explanation_style="nope"),
    )
    assert _HISTORICAL_BRIEF_STYLE in instr


def test_the_lens_is_falsy_only_when_nothing_would_change():
    assert not ReaderLens()
    assert ReaderLens(block="x") and ReaderLens(answer_depth="deep")
    assert ReaderLens(explanation_style="technical")
    lens = ReaderLens(block="Topics they follow: energy")
    assert "energy" in lens and 42 not in lens and str(lens) == lens.block


@pytest.mark.parametrize("gate", ["flag", "tier", "consent", "empty"])
def test_every_closed_gate_still_yields_no_lens(monkeypatch, gate):
    monkeypatch.setattr(FLAG, gate != "flag")
    profile = _profile(answer_depth="deep", topics=["energy"])
    tier = "free" if gate == "tier" else "pro"
    if gate == "consent":
        profile["consented_at"] = None
    if gate == "empty":
        profile = _profile()  # all defaults: nothing to apply
    assert resolve_reader_lens(profile, tier) is None


def test_memory_facts_keep_the_style_choice(monkeypatch):
    """The endpoint appends the memory block with `dataclasses.replace` — it must not turn the
    lens back into a bare string and lose the reader's depth."""
    import app.api.v1.endpoints.chat as chat_ep

    monkeypatch.setattr(FLAG, True)
    monkeypatch.setattr("app.config.settings.CHAT_MEMORY_FACTS_ENABLED", True)

    class _Profiles:
        def get_profile(self, _uid):
            return _profile(answer_depth="deep", topics=["energy"])

    class _Facts:
        def top_facts(self, _uid):
            return {"ticker_discussed": ["NVDA"]}

    monkeypatch.setattr(
        "app.services.user_investor_profile_service.get_user_investor_profile_service",
        lambda: _Profiles(),
    )
    monkeypatch.setattr(
        "app.services.user_memory_facts_service.get_user_memory_facts_service",
        lambda: _Facts(),
    )
    lens = chat_ep._reader_lens_for({"id": "u1", "tier": "pro"})
    assert isinstance(lens, ReaderLens)
    assert lens.answer_depth == "deep" and "NVDA" in lens and "energy" in lens


# ── no shared AI Analyst brief for a lens turn ────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("lens,expected_reads", [
    (None, 1),
    (ReaderLens(block="Topics they follow: energy"), 0),
    (ReaderLens(explanation_style="technical"), 0),
])
async def test_the_deep_dive_cache_is_never_read_for_a_lens(lens, expected_reads):
    svc = _svc()
    reads = []

    def _check(*a):
        reads.append(a)
        return "CACHED BRIEF"

    svc._check_deep_dive_cache = _check
    got = await svc._read_deep_dive_cache(
        is_deep_dive=True, context="ctx", stock_id="SPY",
        user_message="deep dive", asset_type="ETF", reader_lens=lens,
    )
    assert len(reads) == expected_reads
    assert got == ("CACHED BRIEF" if expected_reads else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [{"is_deep_dive": False}, {"context": ""}, {"stock_id": None}])
async def test_the_deep_dive_cache_keeps_its_old_preconditions(over):
    svc = _svc()
    svc._check_deep_dive_cache = lambda *a: pytest.fail("read without a deep-dive turn")
    kw = dict(is_deep_dive=True, context="ctx", stock_id="SPY", user_message="deep dive",
              asset_type="ETF", reader_lens=None)
    kw.update(over)
    assert await svc._read_deep_dive_cache(**kw) is None


def test_both_doors_read_the_deep_dive_cache_only_through_the_lens_aware_helper():
    """A third read site that calls `_check_deep_dive_cache` directly would bypass the lens
    check — the bug this file exists for. Comments are not code, so the AST is scanned."""
    src = Path(__file__).resolve().parents[1] / "app" / "services" / "chat_service.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    callers = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Attribute) and node.attr == "_check_deep_dive_cache"
                        and fn.name != "_check_deep_dive_cache"):
                    callers.append(fn.name)
    assert callers == ["_read_deep_dive_cache"], callers
    helper_users = {
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(n, ast.Attribute) and n.attr == "_read_deep_dive_cache"
                for n in ast.walk(fn))
    }
    assert {"generate_response", "prepare_stream_generation"} <= helper_users


# ── the read-back fallback after a committed write ────────────────────────────

class _Store(UserInvestorProfileService):
    """The real write path with the Supabase seams replaced.

    `reads` is a script of results for `get_profile`: a dict is returned, an exception raised.
    """

    def __init__(self, reads):
        self.supabase = object()
        self._reads = list(reads)
        self.written = []

    def get_profile(self, user_id):
        nxt = self._reads.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return dict(nxt)

    def _upsert(self, payload):
        self.written.append(dict(payload))


def _stored(**over):
    row = _profile(answer_depth="deep", topics=["energy", "macro"])
    row.update({"profile_version": 3, "has_profile": True,
                "answered_fields": ["answer_depth", "topics"]})
    row.update(over)
    return row


def test_a_consent_toggle_whose_read_back_fails_keeps_the_stored_preferences(caplog):
    store = _Store([_stored(consented_at=None), ProfileUnreadable("blip")])
    with caplog.at_level(logging.WARNING):
        out = store.upsert_profile("u1", {}, consent=True)
    assert out["consented_at"] == store.written[0]["consented_at"]
    assert out["answer_depth"] == "deep" and out["topics"] == ["energy", "macro"]
    assert out["profile_version"] == 3
    assert out["answered_fields"] == ["answer_depth", "topics"]
    assert would_personalize(out), "the row would read 'add your preferences' — the old bug"
    assert "read-back failed" in caplog.text


def test_a_preference_edit_whose_read_back_fails_keeps_the_stored_consent():
    store = _Store([_stored(), ProfileUnreadable("blip")])
    out = store.upsert_profile("u1", {"explanation_style": "technical"})
    assert out["consented_at"] == CONSENTED, "the row would read 'Off' — the old bug"
    assert out["explanation_style"] == "technical"
    assert out["answer_depth"] == "deep", "an untouched field must keep its stored value"
    assert "consented_at" not in store.written[0], "an edit must never touch consent"


def test_a_revoke_whose_read_back_fails_reports_consent_cleared():
    store = _Store([_stored(), ProfileUnreadable("blip")])
    out = store.upsert_profile("u1", {}, consent=False)
    assert out["consented_at"] is None


def test_both_reads_failing_still_answers_and_says_why(caplog):
    """Store reads down around a committed write: the answer degrades (defaults + this
    payload) rather than turning a stored write into a 503, and the log says consent is
    unknown — the client re-reads when the row reappears."""
    store = _Store([ProfileUnreadable("down"), ProfileUnreadable("down")])
    with caplog.at_level(logging.WARNING):
        out = store.upsert_profile("u1", {"topics": ["energy"]})
    assert out["topics"] == ["energy"] and out["consented_at"] is None
    assert out["answered_fields"] == ["topics"]
    assert "consent unknown" in caplog.text
    assert store.written, "a read failure must never block the write"


def test_a_successful_read_back_is_returned_as_stored():
    fresh = _stored(explanation_style="technical")
    store = _Store([_stored(), fresh])
    assert store.upsert_profile("u1", {"explanation_style": "technical"}) == fresh
