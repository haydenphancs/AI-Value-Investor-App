"""User request/response schemas matching DB users + user_credits tables."""

from pydantic import BaseModel, Field
from typing import List, Optional


class UserResponse(BaseModel):
    """The signed-in user's profile.

    ⚠️ `has_password` / `auth_providers` MUST stay Optional with a None default. They are
    sourced from the `account_auth_methods` RPC (migration 156), which is applied by hand, so
    the code has to deploy cleanly ahead of the migration — and a probe failure must degrade to
    "unknown" rather than to a wrong answer. iOS reads None as "keep doing what you did before":
    show the classic Change Password row. See `services/auth_methods_service.py`.
    """

    id: str
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    tier: str = "free"
    created_at: str
    updated_at: Optional[str] = None
    # None = unknown, NOT False. False means "this account provably has no password", which is
    # what turns the settings row into "Set a Password".
    has_password: Optional[bool] = None
    # e.g. ["apple"], ["google"], ["email", "google"]. Display only — it names the sign-in
    # method in the UI copy and is never the has-password signal (see migration 156's header).
    auth_providers: Optional[List[str]] = None


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
#:
#: Still load-bearing after `avatar_url` stopped being client-writable: `store_avatar`
#: asserts the URL it CONSTRUCTS against this before writing it, so a future storage-host
#: change that blows past 2048 fails loudly here instead of silently truncating in a column
#: that has no CHECK (`users.avatar_url` is a bare `text`).
AVATAR_URL_MAX_LENGTH = 2048

#: Largest avatar we will STORE, measured on the decoded JPEG.
#:
#: The client sends a 512x512 q0.8 JPEG, which measures ~40-90 KB in practice, so 384 KB is
#: ~4x headroom for a pathological photo rather than a limit real users meet.
#:
#: ⚠️ This is enforced in `avatar_service.decode_and_validate`, NOT as a `max_length` on the
#: base64 field below — and that is deliberate. A Pydantic length error is raised BEFORE the
#: handler runs, so it surfaces as a 422 carrying the validator's own text ("String should
#: have at most N characters") instead of AVATAR_TOO_LARGE. The typed code would have been
#: unreachable for the exact input it was written for.
AVATAR_MAX_BYTES = 384 * 1024


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

    ⚠️ `avatar_url` is NOT here any more, and must not come back. It is SERVER-OWNED: the only
    writers are `POST /users/me/avatar` (which constructs the URL from bytes it validated and
    stored) and `DELETE /users/me/avatar`. While it was client-writable, any holder of a valid
    token could point their own avatar at an arbitrary URL — including one inside our own
    private Storage bucket, which the read path then SIGNS. Removing the field is what makes the
    signer's "only this caller's own object prefix" rule an invariant rather than a hope.
    """

    display_name: Optional[str] = Field(
        default=None, min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH
    )


class UpdateAvatarRequest(BaseModel):
    """`POST /users/me/avatar` — the profile picture, base64-encoded.

    Base64-in-JSON rather than multipart, because `APIClient.buildRequest` on iOS hardcodes
    `Content-Type: application/json` and `httpBody = encoder.encode(body)` for the single funnel
    behind every request the app makes. A 512x512 q0.8 JPEG is ~40-90 KB, so base64's +33% is
    ~120 KB — comfortably inside `main._MAX_JSON_BODY_BYTES` (1 MiB), which makes changing that
    funnel (the highest-blast-radius file in the client) unnecessary for ~90 KB of pixels.

    ⚠️ Deliberately UNBOUNDED here. See `AVATAR_MAX_BYTES`: a `max_length` would be enforced by
    Pydantic before the handler and would preempt AVATAR_TOO_LARGE with a raw validator string.
    The size decision belongs to `avatar_service.decode_and_validate`; the body-cap middleware
    is the outer guard.
    """

    image_base64: str = Field(min_length=1)

