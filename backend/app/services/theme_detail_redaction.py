"""Emerging Frontiers theme detail — what a locked (Free) caller is sent.

Owner request (TestFlight 1.0 (11), 2026-10-04): Free sees a theme's first
``entitlements.THEME_FREE_COMPANY_LIMIT`` companies; the rest of the list is drawn blurred
and opens the plan sheet. The blur is presentation. THIS is the gate: a locked caller is
never sent a withheld company's ticker, name, price, daily move or market cap. iOS draws its
blurred rows from ``locked_constituents_count`` alone, over placeholder text it makes up.

Mirrors ``trillion_club_service.redact_trillion_club_detail`` (the 13F detail's Free cut):
a per-request COPY of the shared cached detail, never an edit of it, and a policy table that
forces a decision for every field of the response.

The list is not the only place a withheld company was named. Each of these is decided below:

* ``changes`` — "What changed this month" names the stocks a review ADDED or brought BACK.
  Those are current members, so such a row ships only when it names a company the caller can
  see; otherwise it is withheld and counted in ``locked_changes_count``. A REMOVED row names a
  stock that left the list, so it stays (unless that ticker is somehow among the withheld
  rows again — a Studio edit after the review).
* ``insight.tickers`` — the "Why it's moving" chips: limited to the companies shown.
* ``insight.headline`` / ``insight.summary`` — the generator is told to name each driver and
  quote its exact % move, and the biggest movers are usually the smaller companies the cut
  withholds (adversarial review 2026-10-06). Stripping names out of prose is not a gate anyone
  could trust, so the prose is replaced by fixed wording that names no company
  (``LOCKED_INSIGHT_*``) UNLESS it provably cannot be about a withheld company — FAIL CLOSED
  (``_prose_is_safe``, 2026-10-07): the shown list is not empty and complete (a degraded build
  resolves none, a partial one omits members whose names cannot then be checked), every driver
  is shown, and either nothing is withheld or no withheld company's ticker or name appears in
  the text (the generator quotes every stock's move and can name a non-driver). Names are
  matched as prose writes them — "Tesla" for "Tesla, Inc.", "Joby" for "Joby Aviation, Inc.",
  "D-Wave" — and a false match only costs the prose.
* ``news`` — every item is fetched for one of the theme's stocks: only items about a company
  shown stay. An item with no ticker cannot be placed, so it goes too (allow-list). A visible
  company's headline that mentions a withheld one in passing is a known, accepted residual.

PURE: no I/O, no Supabase, no FMP — importable from the endpoint and from tests.
"""

from __future__ import annotations

import re
from typing import Any, Dict, FrozenSet, List, Set

from app.schemas.themes_detail import ThemeChangeResponse, ThemeDetailResponse

# A change row with this action names a stock that LEFT the list: a former member, never one
# of the companies a locked caller is denied. Every other action ("added", "returned", and any
# action a later review invents) names a CURRENT member, so it ships only when that member is
# visible — the unknown case falls closed.
_REMOVED = "removed"

# Every field of ThemeDetailResponse and how a locked detail treats it. A field added to the
# model without a decision here fails tests/test_theme_detail_entitlement.py: the gate must be
# decided, not inherited by accident.
THEME_DETAIL_REDACTION_POLICY: Dict[str, str] = {
    "slug": "kept",
    "title": "kept",
    "subtitle": "kept",
    "image_url": "kept",
    "accent_hex": "kept",
    "constituents": "the first `limit` rows (largest market cap first); the rest withheld",
    "updated_on": "kept, unless EVERY change row was withheld: then null, so a build that "
                  "predates the gate never prints 'no changes this month' for a month whose "
                  "changes it was not sent",
    "changes": "added/returned rows only for a visible company; removed rows unless the "
               "ticker is a withheld one",
    "performance": "kept — an equal-weight aggregate of the whole list that names no company",
    "insight": "kept; driver chips limited to visible companies; headline and summary become "
               "LOCKED_INSIGHT_* unless they provably name no withheld company (_prose_is_safe)",
    "news": "only items whose ticker is a visible company",
    "is_locked": "True",
    "tier_required": "the plan that unlocks",
    "locked_constituents_count": "companies withheld (one blurred row each on iOS)",
    "locked_changes_count": "change rows withheld",
}


# What a locked caller reads in place of an insight whose drivers include a withheld company.
# Names no company and no figure.
LOCKED_INSIGHT_HEADLINE = "What moved this theme"
LOCKED_INSIGHT_SUMMARY = (
    "Some of the companies behind this move are in the full list. "
    "Upgrade to see every company and what drove it."
)


# Legal-form words dropped from the END of a company's name before it is matched in prose
# ("Sigma Lithium Corporation" is written "Sigma Lithium", "Alphabet Inc. Class A" "Alphabet",
# "Grupo Mexico S.A.B. de C.V." "Grupo Mexico"). Compared dot-less and lower case, so "Inc."
# and "S.A." need no second spelling. Only the end: a word inside a name is part of it.
_NAME_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
    "holdings", "holding", "group", "sa", "sab", "de", "cv", "nv", "ag", "se", "lp", "llc",
    "pbc", "the", "class", "a", "b", "c",
    # The share-class tail FMP appends to some registrant names ("X-Energy, Inc. Class A
    # Common Stock", "Astera Labs, Inc. Common Stock", "… American Depositary Shares").
    "common", "stock", "ordinary", "shares", "share", "depositary", "american", "receipts",
    "adr", "ads", "units", "voting", "subordinate",
}

# Punctuation that wraps a word of a registrant name and never belongs to what prose writes:
# "Tesla, Inc." is written "Tesla" (adversarial review 2026-10-07: the comma used to stay in
# the pattern, so no "X, Inc." name ever matched), "Kraft Heinz Company (The)" "Kraft Heinz".
_WRAPPING = ",;:()[]\"'“”‘’"

# A leading word shorter than this is never matched alone ("MP", "GE", "C3"): the ticker and
# the full core still are.
_LEADING_WORD_MIN = 3


def _bare(word: str) -> str:
    return word.lower().replace(".", "")


def _name_patterns(company_name: str) -> List["re.Pattern[str]"]:
    """Word-bounded patterns for a company's name in prose.

    * Its CORE, case-insensitive: wrapping punctuation stripped, trailing legal-form words and
      a leading "The" dropped ("The Trade Desk, Inc." → "Trade Desk").
    * Its LEADING word alone — the whole first word ("D-Wave", "T-Mobile", "X-Energy",
      "Amazon.com") and its leading run of letters and digits ("Freeport" for
      "Freeport-McMoRan Inc.", "Amazon", "Joby" for "Joby Aviation, Inc."), each when it has
      ``_LEADING_WORD_MIN``+ characters. CASE-SENSITIVE, as a proper noun (as written,
      capitalised or upper case), so "general" in a sentence never matches General Motors
      while "General" does. A false match only swaps the prose for the locked wording — fail
      closed."""
    words = [w.strip(_WRAPPING) for w in re.split(r"\s+", (company_name or "").strip())]
    core = [w for w in words if w]
    while core and _bare(core[-1]) in _NAME_SUFFIXES:
        core.pop()
    if core and _bare(core[0]) == "the":
        core.pop(0)
    patterns: List["re.Pattern[str]"] = []
    if not core:
        return patterns
    patterns.append(re.compile(r"(?<!\w)" + re.escape(" ".join(core)) + r"(?!\w)", re.I))
    first = core[0].rstrip(".")
    run = re.match(r"[^\W_]+", first)
    leading_words = {first, run.group(0) if run else ""}
    spellings = sorted({
        spelling
        for word in leading_words if len(word) >= _LEADING_WORD_MIN
        for spelling in (word, word[:1].upper() + word[1:], word.capitalize(), word.upper())
    })
    if spellings:
        patterns.append(re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(s) for s in spellings) + r")(?!\w)"))
    return patterns


def _ticker_pattern(ticker: str) -> "re.Pattern[str]":
    """A ticker as prose writes it: upper case as stored, word-bounded, an optional cashtag, and
    a class share's "." and "-" interchangeable ("BRK-B" is written "BRK.B")."""
    body = re.escape(ticker).replace(r"\-", "[.-]").replace(r"\.", "[.-]")
    return re.compile(r"(?<![A-Za-z0-9$])\$?" + body + r"(?![A-Za-z0-9])")


def _prose_is_safe(
    insight: Any, shown: Set[str], withheld: List[Any], unresolved: FrozenSet[str],
) -> bool:
    """FAIL CLOSED: the generated headline/summary ships to a locked caller only when nothing in
    it can be about a withheld company (adversarial review 2026-10-07).

    * An empty visible list never ships it: a degraded build resolves no list, so nothing
      could be checked against it. Nor does a PARTIAL one (``unresolved``: members whose quote
      failed are in no list, so their names cannot be checked either).
    * Every driver must be visible — checked FIRST, whatever else holds.
    * Nothing withheld (a complete theme of ``limit`` companies or fewer) then ships it as
      written: every member the text can name is one the caller sees.
    * Otherwise it needs drivers (an insight with none cannot be checked), and no withheld
      company's ticker (``_ticker_pattern``) or name (``_name_patterns``) in the text — the
      generator quotes every stock's move and can name a non-driver. A withheld company with
      no usable name cannot be checked, so it fails closed too."""
    if not shown or unresolved:
        return False
    drivers = [_key(t) for t in (insight.tickers or []) if _key(t)]
    if any(d not in shown for d in drivers):
        return False
    if not withheld:
        return True
    if not drivers:
        return False
    text = f"{insight.headline or ''}\n{insight.summary or ''}"
    for company in withheld:
        ticker = str(getattr(company, "ticker", "") or "").strip()
        if _key(ticker) in shown:
            continue  # a duplicate row of a company the caller sees (`denied` drops it too)
        if ticker and _ticker_pattern(ticker).search(text):
            return False
        patterns = _name_patterns(str(getattr(company, "company_name", "") or ""))
        if not patterns:
            return False  # no usable name: it cannot be checked
        if any(pattern.search(text) for pattern in patterns):
            return False
    return True


def _key(symbol: Any) -> str:
    """The join key the list is built on (`home_dashboard_service._canonical_symbol`, which
    folds class shares "BRK.B" → "BRK-B"), plus a strip: a chip or a news ticker comes from
    stored JSON that a human can edit."""
    return str(symbol or "").strip().upper().replace(".", "-")


def redact_theme_detail(
    detail: ThemeDetailResponse, *, limit: int, tier_required: str
) -> ThemeDetailResponse:
    """Return a NEW, locked detail for a caller allowed ``limit`` companies. Never mutates
    ``detail``.

    ⚠️ ``detail`` is normally the object in the class-level cache, shared by every caller for
    10 minutes: editing it in place would strip Pro users' lists until the next rebuild.
    Everything is deep-copied first.

    ``is_locked`` is True for EVERY locked-tier caller (it says "this caller is on Free"); the
    two counts say how much was withheld and are 0 when nothing was, so iOS keys the blurred
    rows off the counts, not the flag — a theme with five companies or fewer shows no lock.
    """
    copy = detail.model_copy(deep=True)
    keep = max(0, int(limit))
    visible = copy.constituents[:keep]
    withheld = copy.constituents[keep:]
    shown: Set[str] = {_key(c.ticker) for c in visible} - {""}
    denied: Set[str] = {_key(c.ticker) for c in withheld} - {""} - shown

    changes: List[ThemeChangeResponse] = []
    locked_changes = 0
    for change in copy.changes:
        key = _key(change.ticker)
        allowed = (key not in denied) if change.action == _REMOVED else (key in shown)
        if allowed:
            changes.append(change)
        else:
            locked_changes += 1

    # Read from the ORIGINAL: a private attribute the cached build set (members whose quote did
    # not resolve), never serialised.
    unresolved: FrozenSet[str] = frozenset(getattr(detail, "_unresolved_members", None) or ())

    insight = copy.insight
    if insight is not None:
        update: Dict[str, Any] = {"tickers": [t for t in insight.tickers if _key(t) in shown]}
        if not _prose_is_safe(insight, shown, withheld, unresolved):
            update["headline"] = LOCKED_INSIGHT_HEADLINE
            update["summary"] = LOCKED_INSIGHT_SUMMARY
        insight = insight.model_copy(update=update)

    return ThemeDetailResponse(
        slug=copy.slug,
        title=copy.title,
        subtitle=copy.subtitle,
        image_url=copy.image_url,
        accent_hex=copy.accent_hex,
        constituents=visible,
        updated_on=None if (locked_changes and not changes) else copy.updated_on,
        changes=changes,
        performance=copy.performance,
        insight=insight,
        news=[item for item in copy.news if _key(item.ticker) in shown],
        is_locked=True,
        tier_required=tier_required,
        locked_constituents_count=len(withheld),
        locked_changes_count=locked_changes,
    )
