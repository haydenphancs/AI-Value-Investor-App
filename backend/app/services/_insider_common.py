"""
Shared helpers for classifying insider (Form 4) transactions.

Both ``holders_service`` (per-ticker detail view) and ``tracking_service``
(watchlist-wide alerts) need to agree on:
  * which FMP transactionType strings map to buys vs sells
  * which trades carry real signal ("Informative") vs mechanical compensation
    noise like option exercises and tax withholding ("Uninformative")

Keeping the classifier in one place prevents the alert card and the Holders
tab from disagreeing about the same underlying Form 4 row.
"""

from typing import Tuple


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
