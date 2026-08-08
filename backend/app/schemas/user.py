"""User request/response schemas matching DB users + user_credits tables."""

from pydantic import BaseModel
from typing import Optional


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    tier: str = "free"
    created_at: str
    updated_at: Optional[str] = None


class UserCreditsResponse(BaseModel):
    """Credit balance across BOTH pools.

    `user_credits` holds a granted pool (monthly tier allocation, use-it-or-lose-it) and a
    purchased pool (consumable IAP credit packs, which App Store Guideline 3.1.1 forbids
    expiring). The three original fields report the COMBINED position:

        total     = granted total + purchased total
        used      = granted used  + purchased used
        remaining = spendable = (granted + purchased) remaining

    They are combined rather than granted-only because three separate iOS decoders read this
    shape — `CreditInfo` (AppState.swift), `BackendCreditsResponse` (TaskPollingManager.swift)
    and the `CreditBalance` converter (ResearchModels.swift) — and two of them hard-`decode`
    all three keys. Reporting granted-only here would show 0 to a user holding 500 purchased
    credits and leave the Generate button disabled: the feature failing silently. For the same
    reason all three MUST stay present and non-optional.

    ⚠️ `resets_at` describes the GRANTED pool only. Anything rendering it next to `remaining`
    ("Renews Aug 31") is telling the user their purchased credits expire, which is exactly what
    3.1.1 forbids — use `purchased_remaining` to qualify that copy.
    """

    total: int
    used: int
    remaining: int
    resets_at: Optional[str] = None

    # Breakdown, so the UI can say "50 monthly + 250 purchased" and label the reset date
    # honestly. Defaulted: they are absent until migration 117 is applied, and older iOS
    # builds decode this response without them.
    granted_remaining: int = 0
    purchased_remaining: int = 0
    # The GRANTED pool's own totals. Needed because a "used / total" fraction is only
    # meaningful within one pool: `total` and `used` above are lifetime-inclusive of every
    # pack the user has ever bought, so a Profile bar drawn from them shows a monthly quota
    # that never fills and can never be read as "you have used 40 of your 50 this month".
    granted_total: int = 0
    granted_used: int = 0


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
