"""
Words known NOT to be top-level domains — shared by the link validator and the X length counter.

A missing space after an abbreviation glues it to the next word ("U.S.dollars", "e.g.the",
"vs.the"), and the result is shaped exactly like a domain. Whether it IS one depends only on its
last label: X autolinks a span whose tail is a real TLD, in any case ("U.S.markets",
"U.S.Markets", "e.g.bank", "vs.best" — .markets, .bank, .one, .you and .best are all delegated
gTLDs), and nothing else.

So the exemption is decided by what is KNOWN, never by what is missing from a list of TLDs: a
glued abbreviation is plain text only when its tail is in `NON_TLD_TAILS`, and every other tail
is treated as a link (`compliance._is_abbreviation_run`, `post_copy._plain_typo`). The old rule
inverted that — "not in a short TLD list, so not a link" — and let "Stocks in U.S.markets rose."
through the validator while the X counter (correctly) weighed it as a URL.

Every word was checked against the IANA root zone (tlds-alpha-by-domain, version 2026072500):
none is a TLD. Add a word only after checking it there — "market", "markets", "bank", "one",
"you", "best", "fund", "money", "cash", "tax", "company", "jobs", "trade", "loans", "credit",
"capital", "insurance" and "mortgage" ARE TLDs and must never join. Lower case; callers compare
case-insensitively.

Leaf module: stdlib only, imports nothing from the package (both `compliance` and `post_copy`
import it, and `post_copy` already imports `compliance`).
"""

from __future__ import annotations

from typing import FrozenSet

NON_TLD_TAILS: FrozenSet[str] = frozenset("""
the and but for nor yet not its our their this that these those with from into onto than then
when what why who whom whose which while where was were are has had have will can may all any
some each such same own other also just only even still very well yes via per etc of or on an
he she his her him we they them let dollar dollars economy economies stock stocks shares share
price prices cost costs sales fee fees rate rates bonds oil index dow firms firm companies banks
consumers consumer government federal treasury treasuries inflation interest debt job wages wage
housing home rents taxes growth gdp exports imports trades retailers retail
""".split())

#: Delegated TLDs that are ordinary words a glue typo produces. Pinned by the tests as NOT in
#: `NON_TLD_TAILS` (a careless addition would turn a real link into "plain text").
KNOWN_WORD_TLDS: FrozenSet[str] = frozenset({
    "market", "markets", "bank", "one", "you", "best", "fund", "money", "finance", "cash",
    "tax", "company", "jobs", "trade", "loans", "credit", "capital", "insurance", "mortgage",
})


def is_non_tld_tail(label: str) -> bool:
    """`label` (any case) is known not to be a TLD."""
    return label.lower() in NON_TLD_TAILS
