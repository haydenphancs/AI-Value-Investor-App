"""User request/response schemas matching DB users + user_credits tables."""

from pydantic import BaseModel, Field
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


#: Longest display name we will store. Generous for a real name, and small enough that the
#: value is safe to render in a fixed-height row, log, and embed in a support report.
DISPLAY_NAME_MAX_LENGTH = 64
#: Practical ceiling for an avatar URL. Well above any real CDN URL.
AVATAR_URL_MAX_LENGTH = 2048


class UpdateProfileRequest(BaseModel):
    """`PATCH /users/me`.

    ⚠️ BOTH FIELDS ARE BOUNDED, and that is not decoration. `users.display_name` is a bare
    `text` column with no CHECK, `model_dump(exclude_none=True)` fed the value straight into
    the UPDATE, and the request-body cap middleware in `main.py` covers only `/me/settings`.
    So any caller holding a valid token could store a multi-megabyte display name — which is
    then re-read by `get_current_user` on EVERY authenticated request (it does `select("*")`)
    and echoed into the profile response, the report PDF byline, and the support report. One
    write, permanent cost on every subsequent request by that user.

    `min_length=1` on the name because `exclude_none=True` only drops `None`: an empty string
    is a real value and would have blanked the name to "" — which the iOS side then renders as
    an empty row rather than falling back to "Investor" (that fallback is `nil`-only).
    """

    display_name: Optional[str] = Field(
        default=None, min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH
    )
    avatar_url: Optional[str] = Field(default=None, max_length=AVATAR_URL_MAX_LENGTH)
