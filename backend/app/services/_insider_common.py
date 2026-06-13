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
