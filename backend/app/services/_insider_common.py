"""
Shared helpers for insider (Form 4) data: transaction classification AND
name normalization.

Both ``holders_service`` (per-ticker detail view) and ``tracking_service``
(watchlist-wide alerts) need to agree on:
  * which FMP transactionType strings map to buys vs sells
  * which trades carry real signal ("Informative") vs mechanical compensation
    noise like option exercises and tax withholding ("Uninformative")
  * how to render insider names — FMP returns them in messy 'LAST FIRST
    MIDDLE' uppercase form ("ELLISON LAWRENCE JOSEPH"); both surfaces
    should display the natural 'First Middle Last' shape.

Keeping these in one place prevents the alert card, the Holders tab, and
the ticker report's "Insider & Management" section from disagreeing about
the same underlying Form 4 row.
"""

import re
from typing import Optional, Tuple


def normalize_insider_name(raw: Optional[str]) -> str:
    """Convert an FMP-style insider name into a natural 'First Middle Last'.

    FMP's `reportingName` comes in two messy shapes:
      - 'ELLISON LAWRENCE JOSEPH'   (uppercase, space-separated, last-first)
      - 'Ellison, Lawrence Joseph'  (mixed case with a comma)

    Both collapse to 'Lawrence Joseph Ellison'. Single-letter middle tokens
    get a period appended ('Sicilia Michael D' → 'Michael D. Sicilia').
    Falls back to 'Insider' for empty/None so callers can render without a
    None-check.

    Compound last names ('VAN DER BERG ALICE') are not detected — the first
    space-delimited token is treated as the surname. Rare enough to skip the
    extra heuristic.
    """
    if not raw or not raw.strip():
        return "Insider"
    s = raw.strip().rstrip(".")

    def _tok(t: str) -> str:
        t = t.strip(". ,")
        if not t:
            return ""
        if len(t) == 1:
            return t.upper() + "."
        # title() lowercases letters after the first; for "MC"/"MAC"
        # prefixes this gives "Mcdonald"/"Macarthur" — acceptable
        # without a special lookup table.
        return t[0].upper() + t[1:].lower()

    if "," in s:
        last_part, rest = s.split(",", 1)
        first_middle_part = rest
    else:
        parts = s.split()
        if len(parts) < 2:
            return _tok(s) or "Insider"
        last_part = parts[0]
        first_middle_part = " ".join(parts[1:])

    last_titled = _tok(last_part)
    first_middle_titled = " ".join(
        _tok(t) for t in first_middle_part.split() if _tok(t)
    )
    return f"{first_middle_titled} {last_titled}".strip() or "Insider"


def classify_insider_transaction(tx_type: str) -> str:
    """Classify a FMP ``transactionType`` string into one of four labels.

    Only open-market purchases (P) and pure sales (S) are informative.
    Composite sale types (S-Sale+OE, S-Sale+DIS) indicate option exercises
    or RSU dispositions paired with sales — these are uninformative because
    they reflect compensation mechanics, not insider sentiment.

      - P-Purchase           → Informative Buy
      - S-Sale (pure)        → Informative Sell
      - S-Sale+OE / +DIS     → Uninformative Sell
      - A-*/M-*/G-*          → Uninformative Buy (awards, exercises, gifts)
      - F-*/D-*              → Uninformative Sell (tax withholding, disposition)
    """
    tx = (tx_type or "").strip().upper()

    if tx.startswith("P"):
        return "Informative Buy"

    if tx.startswith("S"):
        if "+OE" in tx or "+DIS" in tx or "EXEMPT" in tx:
            return "Uninformative Sell"
        return "Informative Sell"

    if tx.startswith(("A", "M", "G")):
        return "Uninformative Buy"

    if tx.startswith(("F", "D")):
        return "Uninformative Sell"

    return "Uninformative Sell"


# ── Form 4 row predicates (CEO Buys signal, home E2 2026-09-23) ────────
#
# FMP folds every role a reporting person holds into ONE `typeOfOwner` string, officer
# title last: "director, officer: Chief Executive Officer", "director, 10 percent owner,
# officer: President and CEO", "officer: CEO", "10 percent owner". There is no separate
# title field. `ticker_report_data_collector._role_rank` is NOT reused here: it ranks
# "President and CEO" as a president, and importing the agents collector is heavy.

_CEO_RE = re.compile(r"\bceo\b|chief\s+executive", re.I)
# Not the SITTING CEO: a former/retired/incoming one still files Form 4s for a while.
_NOT_SITTING_RE = re.compile(
    r"\b(?:former|retired|previous|past|emeritus|outgoing|elect)\b|\bex[-\s]?(?:ceo|chief)\b", re.I
)
# A title that CONTAINS "CEO" but is someone else's: "Chief of Staff to the CEO", "EVP, Office
# of the CEO", "Deputy CEO", "Spouse of CEO". Adjacency on purpose: "Vice Chairman and CEO"
# IS the CEO, "Vice CEO" is not.
_SUBORDINATE_RE = re.compile(
    r"\b(?:deputy|vice|assistant|associate|regional|divisional|division|segment)[\s-]+"
    r"(?:ceo|chief\s+executive)\b"
    r"|\b(?:to|of)\s+the\s+(?:ceo|chief\s+executive)\b|\bspouse\b",
    re.I,
)
# The CEO of a SEGMENT or a subsidiary, not of the issuer: "CEO, Consumer & Community Banking",
# "President & CEO of Subsidiary Bank", "Chief Executive Officer - Europe". "of the Company"
# stays the company. A comma alone is fine ("CEO, President and Director").
_SEGMENT_RE = re.compile(
    r"(?:\bceo\b|chief\s+executive(?:\s+officer)?)\s*"
    r"(?:[-–—:]\s*\w"
    r"|,\s*(?:consumer|commercial|corporate|global|international|north\s+america|americas|europe"
    r"|emea|asia|apac|wealth|retail|investment|banking|operations|division|segment|group|unit"
    r"|business|subsidiary)\b"
    r"|\s+of\s+(?!the\s+company\b|company\b)\w)",
    re.I,
)
_COMMON_RE = re.compile(r"\bcommon\b|\bordinary\s+shares?\b", re.I)
_NON_COMMON_RE = re.compile(
    r"preferred|warrant|\bnotes?\b|debenture|\boptions?\b|\brights?\b|restricted stock unit",
    re.I,
)
_ROLE_LABEL_MAX = 60


def _officer_title(type_of_owner: str) -> str:
    """The reporting person's OFFICER title, or ``""`` when they are not filing as an officer.

    Only this text may make someone a CEO — never the free-text ``other:`` field on its own
    ("director, other: Retired CEO" and "director, other: Spouse of CEO" are not the CEO).
    Two live shapes (2026-09-23, 3,000 P rows): ``"…officer: <title>"`` for 498 of the 499
    CEO-matching rows, and ``"director, officer, other: President & CEO"`` — the bare officer
    FLAG with its title carried in ``other:`` — which is accepted.
    """
    low = type_of_owner.lower()
    at = low.rfind("officer:")
    if at >= 0:
        title = type_of_owner[at + len("officer:"):]
        cut = title.lower().find("other:")
        if cut >= 0:
            title = title[:cut]
        return " ".join(title.split()).strip(" ,;")
    roles = [part.strip() for part in low.split(",")]
    if "officer" in roles:
        other_at = low.rfind("other:")
        if other_at >= 0:
            return " ".join(type_of_owner[other_at + len("other:"):].split()).strip(" ,;")
    return ""


def is_ceo_role(type_of_owner: object) -> bool:
    """True when a Form 4 ``typeOfOwner`` names the issuer's SITTING CEO / co-CEO.

    Strings only — FMP has sent ``None`` and numbers here, and neither is a role. The match
    runs on the officer title alone (see ``_officer_title``) and rejects former/incoming CEOs,
    someone else's title that merely mentions the CEO, and segment/subsidiary CEOs.
    """
    if not isinstance(type_of_owner, str):
        return False
    title = _officer_title(type_of_owner)
    return (
        bool(title)
        and bool(_CEO_RE.search(title))
        and not _NOT_SITTING_RE.search(title)
        and not _SUBORDINATE_RE.search(title)
        and not _SEGMENT_RE.search(title)
    )


def ceo_role_label(type_of_owner: object) -> str:
    """The officer title to show under a CEO's name ("Chief Executive Officer", "President
    and CEO"), else ``"CEO"``."""
    if isinstance(type_of_owner, str):
        title = _officer_title(type_of_owner)
        if title and _CEO_RE.search(title):
            return title[:_ROLE_LABEL_MAX].rstrip()
    return "CEO"


def is_common_stock(security_name: object) -> bool:
    """True for a common / ordinary share line — not preferred, warrants, notes,
    options, rights or RSUs, which a "CEO bought the stock" headline must not count."""
    if not isinstance(security_name, str):
        return False
    return bool(_COMMON_RE.search(security_name)) and not _NON_COMMON_RE.search(security_name)


def is_informative(classification: str) -> bool:
    """True when the classification carries real insider-sentiment signal."""
    return classification in ("Informative Buy", "Informative Sell")


def action_word(classification: str) -> str:
    """Return ``"bought"`` or ``"sold"`` from a classification label."""
    return "bought" if "Buy" in classification else "sold"


def classify_for_alerts(tx_type: str) -> Tuple[str, bool]:
    """Convenience for the alerts pipeline.

    Returns ``(action_word, is_informative)`` where ``action_word`` is
    ``"bought"`` or ``"sold"``. Callers that want only real signals can
    gate on the second element.
    """
    classification = classify_insider_transaction(tx_type)
    return action_word(classification), is_informative(classification)


# ── Thesis-bullet self-labeling ───────────────────────────────────────
#
# Bull/Bear thesis bullets on the Ticker Report render with NO section header, so
# each must name its own signal. A bullet like "55 sells ($1.9B) vs 1 buy ($112K)
# in 12 months" is unreadable out of context — the reader can't tell it describes
# INSIDER activity (vs institutions, congress, or analysts, which also have
# buyers/sellers). The synthesis prompt asks the model to write "55 insider
# sells…", but the model doesn't reliably comply, and a leading "Insider:" prefix
# can't survive because narrative_prompts._post_process strips leading "Word:"
# labels. So the label is enforced deterministically, inlined before the sell word.

_THESIS_SELL_RE = re.compile(r"\b(?:sell|sale)\w*", re.I)
_THESIS_BUY_RE = re.compile(r"\bbuy\w*", re.I)
# Other "buyers vs sellers" sources — never relabel one of these as insider.
_THESIS_OTHER_SOURCE_RE = re.compile(
    r"congress|senat|repres|\bhouse\b|institution|hedge|analyst|\bfund", re.I
)


def ensure_insider_label(point: str) -> str:
    """Inject "insider" into a thesis bullet that describes insider buy/sell
    activity but never says so, so the bullet stands on its own.

    Conservative — acts ONLY when the bullet pairs a sell-count with a buy-count
    and a number, isn't already labeled "insider", and doesn't name a different
    source (congress / institutions / hedge funds / analysts). Otherwise the
    bullet is returned unchanged. The label is inlined before the sell word
    ("55 sells…" → "55 insider sells…") rather than prefixed, because a leading
    "Insider:" would be stripped by _post_process downstream.
    """
    if not isinstance(point, str) or not point or "insider" in point.lower():
        return point
    if not (
        _THESIS_SELL_RE.search(point)
        and _THESIS_BUY_RE.search(point)
        and any(ch.isdigit() for ch in point)
    ):
        return point
    if _THESIS_OTHER_SOURCE_RE.search(point):
        return point
    m = _THESIS_SELL_RE.search(point)
    if m.start() == 0:
        # Rare: bullet leads with the sell word — prefix instead of inlining.
        return "Insider " + point[0].lower() + point[1:]
    return f"{point[:m.start()]}insider {point[m.start():]}"
