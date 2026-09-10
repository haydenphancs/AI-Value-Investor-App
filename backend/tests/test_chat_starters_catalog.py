"""The authored starter-question catalogue.

Two copies of one file — `backend/data/` (vendored for Railway) and the iOS resource
bundle (the offline fallback) — so the first thing asserted is that they have not drifted.
Everything else pins a property the runtime silently depends on: a duplicate collapses in
the iOS `ForEach`, an unfilled `{symbol}` is dropped by the client and shortens the row,
and a pool below the rotation threshold stops being disjoint day to day.
"""

import hashlib
import json
from pathlib import Path

import pytest

from app.services.chat_starters_service import _DETAIL_SCOPES, _DETAIL_SLOTS, _GLOBAL_SLOTS
from app.services.daily_rotation import _MIN_WINDOWS_FOR_SEAM_REPAIR, normalize_pool

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_COPY = _ROOT / "backend" / "data" / "chat_starters.json"
_IOS_COPY = (
    _ROOT / "frontend" / "ios" / "ios" / "Resources" / "ChatStarters" / "chat_starters.json"
)

#: Mirrors the CHECK in migration 161.
_MAX_DB_CHARS = 140
#: A chip is a one-line pill. Well under the DB bound, and the bound that actually
#: governs whether the row reads as a row of chips or a wall of text.
_MAX_CHIP_CHARS = 70

_PAYLOAD = json.loads(_BACKEND_COPY.read_text(encoding="utf-8"))
_GLOBAL = _PAYLOAD["global"]
_DETAIL = _PAYLOAD["detail"]


def test_the_two_copies_are_byte_identical():
    """The iOS bundle is the offline fallback; drift means the app shows a different
    set from the server for as long as nobody notices."""
    backend = _BACKEND_COPY.read_bytes()
    ios = _IOS_COPY.read_bytes()
    assert hashlib.sha256(backend).hexdigest() == hashlib.sha256(ios).hexdigest(), (
        "backend/data/chat_starters.json and the iOS Resources copy have diverged — "
        "re-copy one over the other; they are one authored file with two homes."
    )


def test_the_global_pool_is_large_enough_to_rotate_properly():
    """Below `k * 4` the walk drops to its small-pool mode and consecutive days can
    share questions. `daily_rotation` documents the threshold; this pins the pool to
    the right side of it."""
    floor = _GLOBAL_SLOTS * _MIN_WINDOWS_FOR_SEAM_REPAIR
    assert len(normalize_pool(_GLOBAL)) >= floor, (
        f"the global pool needs at least {floor} questions for day-to-day disjointness"
    )
    assert len(normalize_pool(_GLOBAL)) >= 100, "the tester asked for ~100"


@pytest.mark.parametrize("scope", _DETAIL_SCOPES)
def test_each_detail_pool_is_large_enough_to_rotate_properly(scope):
    floor = _DETAIL_SLOTS * _MIN_WINDOWS_FOR_SEAM_REPAIR
    assert len(normalize_pool(_DETAIL[scope])) >= floor, (
        f"{scope} pool needs at least {floor} templates for day-to-day disjointness"
    )


def test_every_question_survives_normalisation():
    """`normalize_pool` dedupes case- and whitespace-insensitively. A collision here
    means the authored count is a lie and the walk has fewer questions than intended."""
    for scope, items in [("global", _GLOBAL), *((s, _DETAIL[s]) for s in _DETAIL_SCOPES)]:
        assert len(normalize_pool(items)) == len(items), (
            f"{scope} contains duplicates after case/whitespace normalisation"
        )


def test_every_question_is_a_question():
    for scope, items in [("global", _GLOBAL), *((s, _DETAIL[s]) for s in _DETAIL_SCOPES)]:
        bad = [q for q in items if not q.strip().endswith("?")]
        assert not bad, f"{scope}: not phrased as questions: {bad}"


def test_every_question_fits_a_chip_and_the_column():
    for scope, items in [("global", _GLOBAL), *((s, _DETAIL[s]) for s in _DETAIL_SCOPES)]:
        # The template's braces are replaced by a symbol at render time, so measure the
        # worst realistic case rather than the raw string.
        rendered = [q.replace("{symbol}", "WWWWW") for q in items]
        too_long = [q for q in rendered if len(q) > _MAX_CHIP_CHARS]
        assert not too_long, f"{scope}: too long for a chip: {too_long}"
        over_db = [q for q in items if len(q) > _MAX_DB_CHARS]
        assert not over_db, f"{scope}: violates migration 161's CHECK: {over_db}"


def test_global_questions_carry_no_placeholder():
    """There is no symbol to substitute on the general chat screen, and iOS drops any
    string still containing a brace — so a stray placeholder silently deletes the chip."""
    bad = [q for q in _GLOBAL if "{" in q or "}" in q]
    assert not bad, f"global questions must not contain a placeholder: {bad}"


@pytest.mark.parametrize("scope", _DETAIL_SCOPES)
def test_every_detail_template_has_exactly_one_symbol_placeholder(scope):
    bad = [q for q in _DETAIL[scope] if q.count("{symbol}") != 1]
    assert not bad, f"{scope}: need exactly one {{symbol}}: {bad}"
    stray = [q for q in _DETAIL[scope] if q.replace("{symbol}", "").count("{") or
             q.replace("{symbol}", "").count("}")]
    assert not stray, f"{scope}: unknown placeholder would drop the chip client-side: {stray}"


def test_no_evergreen_question_names_a_ticker():
    """A hardcoded ticker is what made the shipped set stale.

    The old default was "Should I buy #AAPL?" — a company chosen a year before the
    tester saw it. The live slots are where a symbol belongs; the evergreen pool must
    stay true indefinitely.
    """
    import re

    # An all-caps run of 2-5 letters as a standalone word, which is what a ticker looks
    # like. Sentence-initial words are excluded by requiring the whole token to be caps.
    pattern = re.compile(r"(?<![A-Za-z])(?:\$|#)?[A-Z]{2,5}(?![A-Za-z])")
    allowed = {"I", "AI", "P", "E", "S", "B", "US", "ETF", "IPO", "CEO", "SEC", "FDA",
               "TTM", "EPS", "ROIC", "DCF", "EV", "P/E", "P/B", "P/S", "M&A", "13F"}
    offenders = []
    for question in _GLOBAL:
        for token in pattern.findall(question):
            if token.lstrip("$#") not in allowed:
                offenders.append((question, token))
    assert not offenders, f"evergreen questions must not name a ticker: {offenders}"


def test_no_evergreen_question_asks_for_a_personal_recommendation():
    """The app's own suggested prompts must not invite personalised advice.

    Terms §2 promises output that is "general and impersonal… not adapted to your
    portfolio, holdings, financial situation, risk tolerance, or objectives", and
    `persona_config.ADVICE_BOUNDARY` says the same. The set this replaces opened with
    "Should I buy #AAPL?" — the app itself proposing the one question its own terms
    disclaim. Educational framing is not a style preference here.
    """
    lowered = [q.casefold() for q in _GLOBAL]
    banned = ("should i buy", "should i sell", "should i invest",
              "is it a good buy", "what should i buy", "will it go up",
              "is it a buy", "should i hold")
    offenders = [q for q in lowered if any(phrase in q for phrase in banned)]
    assert not offenders, (
        f"evergreen questions must not solicit personal recommendations: {offenders}"
    )


def test_the_catalogue_scan_is_not_vacuous():
    """Every assertion above iterates real, non-trivial content."""
    assert len(_GLOBAL) > 50
    assert set(_DETAIL) == set(_DETAIL_SCOPES)
    assert all(len(_DETAIL[s]) > 10 for s in _DETAIL_SCOPES)
    # And prove the ticker scan catches one.
    import re
    pattern = re.compile(r"(?<![A-Za-z])(?:\$|#)?[A-Z]{2,5}(?![A-Za-z])")
    assert pattern.findall("Should I buy #AAPL?") == ["#AAPL"]
