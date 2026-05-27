"""Transcript signals — Phase 3B extraction of moat-relevant numbers
from earnings-call transcript text.

The ticker-report collector already pulls the latest earnings transcript
per ticker (used today for TAM extraction by AI Stage A and for guidance
language). This service extends that pipeline by extracting two
quantitative signals via deterministic regex (no LLM call):

  1. **Net Revenue / Net Dollar Retention (NRR / NDR)** — used by the
     Switching Costs pillar. NRR >100% = customers expanding their
     spend over time = strong switching costs. <90% = leakage. The
     SaaS-canonical signal.

  2. **User / customer count** — used by the Network Effects pillar.
     A high or growing platform user base is direct evidence of
     network compounding. Captured as a raw count + unit (M/B).

Why regex (not Gemini): the patterns are stereotyped — companies that
report these metrics say it the same way every quarter ("we delivered
NRR of 115% in the quarter"; "330 million monthly active users").
Regex covers ~80% of phrasings at zero cost. The 20% miss rate
manifests as None — the pillar's metric count drops, possibly below
the 2-metric threshold, and the grounded Gemini fallback (Phase 3D)
picks up the slack with web-cited research. Honest degradation.

A future enhancement (deferred) could add a Gemini JSON-extraction
pass for the 20% of transcripts where regex misses something obvious
— but that adds AI cost per ticker and isn't necessary for v1.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Patterns ────────────────────────────────────────────────────────────
#
# Each pattern targets a single common phrasing. They're all
# case-insensitive (`re.IGNORECASE`) and anchored loosely enough to
# tolerate punctuation noise but tightly enough that "retention" in a
# different context (e.g. employee retention) doesn't accidentally match.

# Net Revenue Retention / Net Dollar Retention — typical CFO phrasings:
#   "NRR of 115%", "net dollar retention of 110%", "NDR was 108%",
#   "net revenue retention came in at 112%", "expansion NRR reached 120%"
_NRR_PATTERNS = [
    re.compile(
        r"\b(?:NRR|NDR)\b[^%]{0,60}?(\d{2,3}(?:\.\d+)?)\s*%",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bnet\s+(?:dollar|revenue)\s+retention\b[^%]{0,80}?(\d{2,3}(?:\.\d+)?)\s*%",
        re.IGNORECASE,
    ),
]

# Customer / user count — typical phrasings:
#   "330 million monthly active users"
#   "2.5 billion users"
#   "500K paying subscribers"
#   "active users grew to 100 million"    (keyword-then-number order)
#   "MAU reached 450 million"
# Captures (number, unit_letter) — unit is M (million), B (billion),
# K (thousand). Multipliers: K=1e3, M=1e6, B=1e9.
_USER_KEYWORDS = (
    r"(?:monthly\s+active\s+users|daily\s+active\s+users|MAU|DAU|"
    r"active\s+users|paying\s+(?:customers|subscribers|users)|"
    r"paid\s+(?:customers|subscribers|users)|subscribers|users)"
)
_USER_COUNT_PATTERNS = [
    # Number first, then keyword: "330 million MAU"
    re.compile(
        rf"(\d+(?:\.\d+)?)\s*(million|billion|thousand|M|B|K)\s+{_USER_KEYWORDS}",
        re.IGNORECASE,
    ),
    # Keyword first, then number within ~80 chars: "MAU reached 100 million"
    re.compile(
        rf"{_USER_KEYWORDS}[\s\S]{{0,80}}?(\d+(?:\.\d+)?)\s*(million|billion|thousand|M|B|K)\b",
        re.IGNORECASE,
    ),
]

# Churn rate — best-effort. Captures things like "monthly churn of 1.5%",
# "annual logo churn of 5%". Lower = stickier.
_CHURN_PATTERN = re.compile(
    r"\b(?:annual|monthly|quarterly)?\s*(?:logo\s+|customer\s+|revenue\s+)?churn"
    r"(?:\s+rate)?\b[^%]{0,40}?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)


_UNIT_MULTIPLIERS = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
    "THOUSAND": 1_000,
    "MILLION": 1_000_000,
    "BILLION": 1_000_000_000,
}


@dataclass
class TranscriptSignals:
    """Quantitative signals extracted from an earnings transcript.
    Fields are None when the regex didn't find a match — the caller
    drops that signal from the pillar's input list (no fabrication).
    """
    nrr_pct: Optional[float] = None             # e.g. 115.0 for 115% NRR
    nrr_quote: Optional[str] = None             # verbatim sentence
    user_count: Optional[int] = None            # raw count (e.g. 330_000_000)
    user_count_quote: Optional[str] = None
    churn_pct: Optional[float] = None           # monthly or annual — caller can disambiguate via quote
    churn_quote: Optional[str] = None

    def has_any_signal(self) -> bool:
        return any([
            self.nrr_pct is not None,
            self.user_count is not None,
            self.churn_pct is not None,
        ])


# ── Public API ─────────────────────────────────────────────────────────


def extract_signals(transcript_text: str) -> TranscriptSignals:
    """Run regex extraction over a transcript text and return whatever
    quantitative signals matched. Empty / non-string inputs return an
    all-None result (no errors raised).
    """
    if not transcript_text or not isinstance(transcript_text, str):
        return TranscriptSignals()

    out = TranscriptSignals()

    # NRR / NDR — first match wins. Bound to [50, 200] to filter
    # obviously-wrong matches (e.g., percentages from unrelated context).
    for pattern in _NRR_PATTERNS:
        match = pattern.search(transcript_text)
        if match is None:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 50.0 <= value <= 200.0:
            out.nrr_pct = value
            out.nrr_quote = _grab_sentence(transcript_text, match.start())
            break

    # User count — first match wins. Cap raw value to 10B to filter
    # obvious typos (a $10B revenue mistakenly tagged as users etc.).
    for pattern in _USER_COUNT_PATTERNS:
        match = pattern.search(transcript_text)
        if match is None:
            continue
        try:
            raw = float(match.group(1))
        except (TypeError, ValueError):
            continue
        unit = (match.group(2) or "").upper()
        # Normalize "M" / "MILLION" / etc.
        first_letter = unit[0] if unit else ""
        if unit in _UNIT_MULTIPLIERS:
            multiplier = _UNIT_MULTIPLIERS[unit]
        elif first_letter in _UNIT_MULTIPLIERS:
            multiplier = _UNIT_MULTIPLIERS[first_letter]
        else:
            continue
        value = int(raw * multiplier)
        if 1_000 <= value <= 10_000_000_000:
            out.user_count = value
            out.user_count_quote = _grab_sentence(transcript_text, match.start())
            break

    # Churn — bound to [0, 30] %. Higher than 30% is almost always a
    # different metric (e.g., "revenue churned 50% YoY" referring to
    # something else).
    churn_match = _CHURN_PATTERN.search(transcript_text)
    if churn_match is not None:
        try:
            value = float(churn_match.group(1))
            if 0.0 <= value <= 30.0:
                out.churn_pct = value
                out.churn_quote = _grab_sentence(
                    transcript_text, churn_match.start(),
                )
        except (TypeError, ValueError):
            pass

    return out


# ── Helpers ────────────────────────────────────────────────────────────


def _grab_sentence(text: str, match_pos: int, max_len: int = 240) -> str:
    """Pull the surrounding sentence as evidence for the audit log.
    Walks back to a sentence terminator (or start-of-text), forward to
    the next terminator, and clips to `max_len` characters.
    """
    if not text or match_pos < 0 or match_pos >= len(text):
        return ""
    # Walk back to find sentence start.
    start = match_pos
    for i in range(match_pos, max(match_pos - 200, -1), -1):
        if text[i] in ".!?\n":
            start = i + 1
            break
        if i == 0:
            start = 0
            break
    # Walk forward to find sentence end.
    end = match_pos
    for i in range(match_pos, min(match_pos + 200, len(text))):
        if text[i] in ".!?\n":
            end = i + 1
            break
        end = i + 1
    sentence = text[start:end].strip()
    if len(sentence) > max_len:
        sentence = sentence[:max_len] + "…"
    return sentence


# ── Scoring helpers — used by moat_scoring_service ─────────────────────


def nrr_to_sub_score(nrr_pct: Optional[float]) -> Optional[float]:
    """Map NRR % to a 0-10 Switching-Costs sub-score.

    Anchors (industry-standard SaaS bands):
        130%+  → 10.0 (elite expansion — Snowflake/Datadog/MongoDB territory)
        120%   → 9.0
        115%   → 8.0
        110%   → 7.0   (median best-in-class SaaS)
        100%   → 5.0   (flat — no net expansion or contraction)
         95%   → 4.0
         90%   → 3.0
         80%   → 1.5
         70%   → 0.0   (collapse)
    """
    if nrr_pct is None:
        return None
    # Linear interpolation between anchor points keeps the formula
    # transparent and auditable. Cap at [0, 10].
    if nrr_pct >= 130.0:
        return 10.0
    if nrr_pct <= 70.0:
        return 0.0
    # Piece-wise linear between 70 and 130.
    if nrr_pct >= 100.0:
        # Above 100: +1.0 per +5 percentage points up to 130.
        return round(5.0 + (nrr_pct - 100.0) / 5.0, 1)
    # Below 100: -1.0 per -5 percentage points down to 70.
    return round(5.0 - (100.0 - nrr_pct) / 5.0, 1)


def user_count_to_sub_score(user_count: Optional[int]) -> Optional[float]:
    """Map raw user count to a 0-10 Network-Effects sub-score using a
    logarithmic scale anchored at major platform breakpoints.

    Anchors:
        1B+    → 10.0 (Google / Facebook / TikTok scale)
        500M   → 9.0
        100M   → 7.5  (Netflix, Spotify-tier)
         10M   → 5.5  (Robinhood, mid-tier consumer)
          1M   → 4.0
        100K   → 2.5
         10K   → 1.0
        <10K   → 0.0  (no measurable network)

    A future enhancement (deferred) would compare user-count GROWTH
    YoY rather than absolute size — that's a stronger network-effect
    signal but requires multi-quarter transcript history.
    """
    if user_count is None or user_count <= 0:
        return None
    import math
    # log10 scale: 1M users → 6, 100M → 8, 1B → 9, 10B → 10
    log = math.log10(user_count)
    if log >= 9.0:    # 1B+
        return min(10.0, round(10.0 + (log - 9.0) * 0.5, 1))
    if log >= 8.0:    # 100M to 1B
        return round(7.5 + (log - 8.0) * 1.5, 1)
    if log >= 7.0:    # 10M to 100M
        return round(5.5 + (log - 7.0) * 2.0, 1)
    if log >= 6.0:    # 1M to 10M
        return round(4.0 + (log - 6.0) * 1.5, 1)
    if log >= 5.0:    # 100K to 1M
        return round(2.5 + (log - 5.0) * 1.5, 1)
    if log >= 4.0:    # 10K to 100K
        return round(1.0 + (log - 4.0) * 1.5, 1)
    return 0.0
