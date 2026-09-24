"""Copy rules for "Trillion-Dollar Club Bets" — ONE list, used by the seed script
(`scripts/seed_trillion_club.py`, before anything is written) and by the request path
(`trillion_club_service.stake_problem`, which drops a row an editor changed in Studio).

Two copies of these patterns had already drifted: the request path did not ban "bought",
"worth", "endorsed" or a leading "Follow …", so a Studio edit the seed script would have
refused could reach the screen. Import from here; never re-declare.

Word-boundary regexes on purpose: "bet" must not match "Alphabet", "hot" not "Photonics",
and the NOUNS "holdings" / "follow-on offering" are fine — only the imperative
follow / copy / mirror phrasing ("copy their trades") is banned.

Follow / copy / mirror, precisely (tests pin both directions):

* anywhere, the verb + a determiner or pronoun: "copy their trades", "mirroring these moves";
* at the start of the field or of a sentence, the bare IMPERATIVE + an object that names
  who or what to copy: a club member or famous investor ("Follow Berkshire", "Copy Buffett"),
  an investor word ("Mirror insiders"), someone's moves ("Copy Ackman's portfolio",
  "Mirror trades") or a destination ("Follow Pelosi into chip stocks").

A leading NOUN is not an instruction: "Follow-on offering closed in Jan 2026.", "Mirror
Biologics", "Copy.ai". The rule used to be "any field that STARTS with the word", which refused
those — at read time a false positive silently drops a real stake, so the object is required.
"""
from __future__ import annotations

import re
from typing import Any, Tuple

# Who an instruction to follow would name: club members and the investors behind them.
_FOLLOW_WHO = (
    r"berkshire|buffett|warren|munger|abel|nvidia|apple|alphabet|google|microsoft|amazon|"
    r"tsmc|spacex|meta|broadcom|tesla|musk|micron|lilly|amd|aramco|samsung|"
    r"investors?|insiders?|funds?|whales?|billionaires?|giants?|us|him|them"
)
# What of theirs: bare after the verb ("Copy trades") or after a possessive ("Ackman's stakes").
_FOLLOW_MOVES_BARE = r"lead|moves|trades|picks|playbook|strateg(?:y|ies)|buys"
_FOLLOW_MOVES = (
    _FOLLOW_MOVES_BARE
    + r"|stakes?|holdings|positions?|portfolios?|investments?|bets?|allocations?"
)

BANNED_COPY = re.compile(
    r"\b(?:picks|smart\s+money|conviction|bullish|bearish|hot|loaded\s+up|"
    r"vote\s+of\s+confidence|endorse(?:d|s|ment|ments)?|secret|hidden|bets?|worth|bought)\b"
    r"|\w-backed\b"
    r"|\b(?:follow|copy|mirror)(?:s|ing)?\s+(?:this|these|that|their|its|his|her|the|them|"
    r"our|what)\b"
    # The leading imperative + an object (see the module docstring). `\s+` after the verb is
    # what lets "Follow-on" and "Copy.ai" through.
    r"|(?:^|[.!?:;]\s)[\s\"'“‘(\[]*(?:follow|copy|mirror)\s+(?:"
    rf"(?:{_FOLLOW_WHO})\b"
    rf"|[\w.&-]+['’]s\s+(?:{_FOLLOW_MOVES})\b"
    rf"|(?:{_FOLLOW_MOVES_BARE})\b"
    r"|[\w.&'’-]+\s+(?:into|onto)\b)",
    re.IGNORECASE,
)

# A background line states what happened (past tense). A forecast or a motive is not a fact
# a filing can support.
FORECAST_COPY = re.compile(
    r"\b(?:will|expects?|expected|anticipates?|plans?\s+to|intends?\s+to|forecasts?|"
    r"likely|should|aims?\s+to|hopes?\s+to|to\s+(?:bet|position|capitalize))\b",
    re.IGNORECASE,
)

# Every stake field that reaches the screen as text.
STAKE_TEXT_FIELDS: Tuple[str, ...] = (
    "investee_name", "background", "ownership_basis", "local_listing", "source_title",
)


def contains_banned_copy(text: Any) -> bool:
    """Pure: does ``text`` hold wording this section may never show? Non-strings → False."""
    return isinstance(text, str) and BANNED_COPY.search(text) is not None


def contains_forecast(text: Any) -> bool:
    """Pure: does ``text`` state a forecast or a motive? Non-strings → False."""
    return isinstance(text, str) and FORECAST_COPY.search(text) is not None
