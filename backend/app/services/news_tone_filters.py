"""Headlines that never count toward the News Tone chart (pure, no I/O).

The News Tone card counts how many headlines about ONE scope Cay AI scored positive /
negative / neutral. A blind re-label of 200 live headlines (2026-10-10: 3 independent
raters, 93% agreement among themselves) found the labeller agreed with them only 70.5%
of the time, and most of the gap was headlines that are not news about the company:

* **Routine institutional holdings filings** — the syndicated "GAMMA Investing LLC Acquires
  7,458 Shares of Meta Platforms, Inc. $META" template (one article per fund per quarter,
  dozens per ticker). All 12 in the sample were labelled directional (9 bullish, because
  "acquires" outnumbers "sells"), and all 12 were neutral to the raters.
* **Law-firm solicitations** — "ROSEN, A LEADING LAW FIRM, Encourages … Investors to Secure
  Counsel", ten wire copies a day after a drop, each a separate bearish headline.

They stay in the feed (a reader may still want them); they are only kept off the chart.
The model is also told they are neutral (``news_cache_service.SENTIMENT_RUBRIC``), but a
deterministic rule does not depend on the model following it.

Both rules are deliberately NARROW, with must-not-match twins in
``tests/test_news_tone_filters.py``: dropping a real story from the chart is worse than
counting one advert. ``theme_insights_service.is_solicitation`` is NOT reused — it was
tuned for theme cards, where a dropped row costs little, and it matches real legal news
("XYZ wins dismissal of securities fraud lawsuit").

Every regex runs on a length-capped title and uses only bounded gaps, so the cost is
linear in the title.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: Titles longer than this are cut before matching (real headlines are < 250 chars).
_MAX_TITLE = 400

# ── Routine holdings filings ───────────────────────────────────────────────────

#: The syndicated template always carries a cashtag ("… Meta Platforms, Inc. $META").
#: Requiring it is what keeps "Amazon Raises Stake in Anthropic" (real news) out.
_CASHTAG_RE = re.compile(r"(?<![\w$])\$[A-Z][A-Z0-9.\-]{0,9}\b")

_HOLDING_NOUN = r"(?:shares|stake|position|holdings|stock\s+position|stock\s+holdings)"
#: "<Fund> Acquires 7,458 Shares of …", "<Fund> Trims Stake in …", "<Fund> Takes $825,000
#: Position in …" — an active verb, then (within a few words) the holding noun.
_ACTIVE_RE = re.compile(
    r"\b(?:acquires|buys|sells|purchases|takes|grows|raises|lifts|boosts|increases|"
    r"trims|cuts|lowers|reduces|decreases|invests|initiates|establishes|adds\s+to)\b"
    r"[^.!?;]{0,40}?\b" + _HOLDING_NOUN + r"\b",
    re.IGNORECASE,
)
#: "… $AAPL Shares Sold by DMC Group LLC", "… $AVGO Stock Position Lessened by Sapient",
#: "… $MSFT Stock Holdings Increased by Occidental …" — the holding noun, then a participle
#: and "by".
_PASSIVE_RE = re.compile(
    r"\b" + _HOLDING_NOUN + r"\s+(?:newly\s+)?"
    r"(?:acquired|bought|purchased|sold|raised|lifted|boosted|increased|grown|trimmed|cut|"
    r"lowered|reduced|decreased|lessened)\s+by\b",
    re.IGNORECASE,
)
#: "New Position" / "Stake" phrasings the active rule cannot reach without a verb.
_HOLDING_PHRASE_RE = re.compile(
    r"\b(?:new|has\s+\$[\d,.]+\s*(?:million|billion|thousand)?)\s+(?:stake|position|"
    r"stock\s+position|holdings)\s+in\b",
    re.IGNORECASE,
)
#: An insider (Form 4) line is NOT this template: the model judges those (a CEO buying is
#: news; the rubric makes a routine plan sale neutral).
_INSIDER_RE = re.compile(
    r"\b(?:insider|director|ceo|cfo|coo|cto|president|chairman|chairwoman|officer|"
    r"evp|svp|founder)\b",
    re.IGNORECASE,
)


def is_routine_holdings_filing(title: Any) -> bool:
    """True for the syndicated "<fund> bought/sold N shares of <company> $TICK" headline.

    The cashtag's POSITION is part of the template: after the verb in the active form
    ("<Fund> Acquires 7,458 Shares of Meta Platforms, Inc. $META"), before the noun in the
    passive one ("Apple Inc. $AAPL Shares Sold by DMC Group LLC"). "Nvidia $NVDA Raises Stake
    in xAI" — a company's own deal, with its cashtag first — is news and does not match."""
    if not isinstance(title, str) or not title:
        return False
    t = title[:_MAX_TITLE]
    if _INSIDER_RE.search(t):
        return False
    for rx, cashtag_after in ((_ACTIVE_RE, True), (_HOLDING_PHRASE_RE, True), (_PASSIVE_RE, False)):
        m = rx.search(t)
        if m is None:
            continue
        span = t[m.end():] if cashtag_after else t[: m.start()]
        if _CASHTAG_RE.search(span):
            return True
    return False


# ── Law-firm solicitations ─────────────────────────────────────────────────────

#: Plaintiff firms and lead-generation services that issue investor solicitations. A name
#: alone marks the headline: these firms' wire releases are all solicitations.
_LAW_FIRM_RE = re.compile(
    r"\b(?:rosen\s+law\s+firm|pomerantz|levi\s*(?:&|and)\s*korsinsky|"
    r"bragar\s+eagel|faruqi\s*(?:&|and)\s*faruqi|bronstein,?\s+gewirtz|glancy\s+prongay|"
    r"kessler\s+topaz|robbins\s+geller|robbins\s+llp|schall\s+law|the\s+gross\s+law\s+firm|"
    r"rigrodsky|kirby\s+mcinerney|johnson\s+fistel|holzer\s*(?:&|and)\s*holzer|"
    r"bernstein\s+liebhard|frank\s+r\.?\s+cruz|hagens\s+berman|portnoy\s+law|djs\s+law|"
    r"block\s*(?:&|and)\s*leviton|edelson\s+lechtzin|wohl\s*(?:&|and)\s*fruchter|"
    r"halper\s+sadeh|monteverde\s*(?:&|and)\s*associates|brodsky\s*(?:&|and)\s*smith|"
    r"ademi\s+(?:firm|llp)|weiss\s+law|rowley\s+law|bfa\s+law|suewallst|"
    r"shareholders\s+foundation|kahn\s+swick|khang\s*(?:&|and)\s*khang|"
    r"howard\s+g\.?\s+smith|lowey\s+dannenberg|bleichmar\s+fonti|saxena\s+white)\b",
    re.IGNORECASE,
)
#: What a solicitation asks investors to do — contact a firm about a lawsuit. Required after
#: the generic phrasings below, so "Elliott urges shareholders to reject the board" (a proxy
#: fight), "Investors urged to diversify" and "Investors who bought Nvidia in 2015 are up
#: 300%" stay news.
_LEGAL_TAIL = (
    r"[^!?;]{0,80}?\b(?:counsel|class\s+action|lawsuit|lead\s+plaintiff|legal\s+(?:rights|options)|"
    r"securities\s+(?:fraud|litigation|claims?))\b"
)
#: A release that OPENS with the firm's name ("ROSEN, A LEADING LAW FIRM, Announces …"). Anchored
#: at the start: "Former DOJ official Jeffrey Rosen, now at XYZ, …" is news about a person.
_LEADING_FIRM_RE = re.compile(r"^\W*rosen(?:\s+law(?:\s+firm)?)?\s*,", re.IGNORECASE)
_HOLDERS = r"(?:investors|shareholders|stockholders)"
#: The solicitation's own wording, aimed at investors.
_SOLICIT_RE = re.compile(
    r"\b(?:encourages?|urges?|reminds?|notifies|alerts?)\b[^.!?;]{0,80}?\b" + _HOLDERS
    + r"\b" + _LEGAL_TAIL
    + r"|\b" + _HOLDERS + r"\b[^.!?;]{0,40}?\b(?:are\s+)?(?:encouraged|urged|reminded|"
    r"notified)\b" + _LEGAL_TAIL
    + r"|\b" + _HOLDERS + r"\s+(?:who|with)\s+(?:lost|losses|suffered|purchased|bought|"
    r"acquired)\b" + _LEGAL_TAIL
    + r"|\blead\s+plaintiff\s+deadline\b"
    r"|\b(?:opportunity|seek)\s+to\s+lead\b" + _LEGAL_TAIL
    + r"|\bsecure\s+counsel\b"
    r"|\b(?:shareholder|investor|stockholder)s?\s+alert\b" + _LEGAL_TAIL
    + r"|\bclass\s+action\s+(?:notice|alert|reminder|deadline)\b"
    r"|\bdeadline\s+alert\b" + _LEGAL_TAIL
    + r"|\bnotice:\s*investors\s+in\b" + _LEGAL_TAIL,
    re.IGNORECASE,
)


def is_law_firm_solicitation(title: Any) -> bool:
    """True for a plaintiff-firm investor solicitation ("Encourages XYZ Investors to …").

    Real legal NEWS — a verdict, a settlement, a dismissal, a regulator's probe, a board's
    rights plan — has none of these phrasings and no firm name, and stays on the chart."""
    if not isinstance(title, str) or not title:
        return False
    t = title[:_MAX_TITLE]
    return bool(_LEADING_FIRM_RE.search(t) or _LAW_FIRM_RE.search(t) or _SOLICIT_RE.search(t))


# ── Duplicate titles ───────────────────────────────────────────────────────────

_UPDATE_PREFIX_RE = re.compile(r"^\s*(?:update(?:d)?\s*\d*\s*[-:–—]|corrected\s*[-:–—])\s*",
                               re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^0-9a-z]+")


def title_key(title: Any) -> Optional[str]:
    """A normalized title, so wire copies of one story ("UPDATE 1-Fed holds rates",
    "Fed Holds Rates!") count once within a batch. None when there is nothing to key on."""
    if not isinstance(title, str):
        return None
    t = _UPDATE_PREFIX_RE.sub("", title[:_MAX_TITLE]).lower()
    key = _NON_WORD_RE.sub(" ", t).strip()
    return key or None


__all__ = ["is_law_firm_solicitation", "is_routine_holdings_filing", "title_key"]
