"""Response shapes for the credit history statement (Account → Credit History).

⚠️ EVERY field here is decoded by iOS. A rename or a nullability change that iOS does
not mirror is a decode CRASH in production, not a missing row — see
`tests/test_credit_history_schema_parity.py`, which is the guard rail for exactly that.

Why the wire carries display-ready `title`/`subtitle`/`kind` rather than the raw
`credit_transactions.reason` for iOS to switch on: `reason` is unconstrained text with
no CHECK constraint, one value is *composed at runtime* (`chat_degraded_{suffix}`,
`endpoints/chat.py`), and new reasons arrive with a backend deploy. A mapping that lived
in Swift would render a blank row on every already-shipped build the moment a new reason
appeared, forever. Mapping server-side means a new reason is correct in old apps — the
same release-independence the Learn content pipeline relies on.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class CreditTransactionResponse(BaseModel):
    """One movement in the ledger, already described in the user's language."""

    # `credit_transactions.id` is a bigint. Serialized as a STRING so iOS gets a stable
    # `Identifiable` key without depending on integer width, matching the notification
    # inbox DTO.
    id: str

    created_at: Optional[str] = None

    #: Signed, exactly as stored. Negative = credits left the account.
    delta: int = 0

    kind: str = Field(
        default="other",
        description="spend | refund | grant | purchase | revoke | other",
    )

    #: What it was for, in the user's language, e.g. "Deep research report".
    title: str = ""

    #: The specific thing, when the ledger can name one — usually a ticker. Never a raw
    #: `ref_id`: those are session uuids, Apple transaction ids and ET month stamps, none
    #: of which mean anything to a reader.
    subtitle: Optional[str] = None

    #: Set ONLY when the pool split says something the user needs (purchased credits
    #: moved). A granted-only movement — and a pre-migration-118 row with an unknown
    #: 0/0 split — leaves this None rather than stating something it cannot support.
    pool_note: Optional[str] = None

    #: This debit was later reversed by a refund row. Without it a cache-hit chat turn
    #: renders as an unexplained −1 sitting next to a +1.
    is_reversed: bool = False

    #: The raw ledger reason. Carried for support/telemetry, not for iOS to branch on.
    reason: str = ""


class CreditHistoryResponse(BaseModel):
    items: List[CreditTransactionResponse] = []
    #: Keyset cursor: the `id` of the last item on this page. Null = no more pages.
    #: Cursored on `id` rather than `created_at` because `created_at` defaults to
    #: `now()`, which is TRANSACTION time — `ensure_credit_period` firing inside a
    #: `spend_credits` call writes two rows with an identical timestamp, and a
    #: timestamp cursor would silently drop one at a page boundary.
    next_cursor: Optional[str] = None
