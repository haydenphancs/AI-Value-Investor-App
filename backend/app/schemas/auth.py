"""Auth request/response schemas matching frontend APIEndpoint.swift."""

import string
import unicodedata

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional

# The display-name bound lives with the profile schema that owns the column, and is imported
# rather than restated: signup and PATCH /users/me write the SAME `users.display_name`, so two
# independent literals would drift and leave the unauthenticated route as the loose one.
from app.schemas.user import DISPLAY_NAME_MAX_LENGTH

# ── The rule for a NEW password — GoTrue's own, exactly (2026-11-26) ───────────
# Minimum length, counted in characters (GoTrue counts bytes, and a character is at least one
# byte, so this can never be looser than the provider).
PASSWORD_MIN_LENGTH = 8
# GoTrue refuses a password over 72 BYTES of UTF-8 with a 400 `validation_failed` ("Password
# cannot be longer than 72 characters" — verified against the live stack; bcrypt reads only 72
# bytes). Counted in BYTES here because that is what it measures: one emoji is four, an
# accented letter two, so a 40-character password can already be too long.
PASSWORD_MAX_BYTES = 72
# The four classes GoTrue checks, as the EXACT ASCII sets its "lowercase, uppercase letters,
# digits and symbols" setting uses. The symbol set is GoTrue's 32-character list — which is
# precisely ASCII punctuation (`string.punctuation`; pinned equal in the tests). Anything else
# (a space inside the password, an accented letter, an emoji, a non-ASCII digit) is ALLOWED in
# the password but counts toward NO class: GoTrue refuses "Aa1 bbbbbbbb" as weak, because its
# only "symbol" is a space (verified live).
PASSWORD_LOWERCASE = string.ascii_lowercase
PASSWORD_UPPERCASE = string.ascii_uppercase
PASSWORD_DIGITS = string.digits
PASSWORD_SYMBOLS = "!@#$%^&*()_+-=[]{};':\"|<>?,./`~\\"
_LOWER_SET = frozenset(PASSWORD_LOWERCASE)
_UPPER_SET = frozenset(PASSWORD_UPPERCASE)
_DIGIT_SET = frozenset(PASSWORD_DIGITS)
_SYMBOL_SET = frozenset(PASSWORD_SYMBOLS)
_CLASS_CHARS = _LOWER_SET | _UPPER_SET | _DIGIT_SET | _SYMBOL_SET

#: The too-long refusal, worded for the person who typed 40 characters and an emoji.
PASSWORD_TOO_LONG_MESSAGE = (
    f"Password is too long: {PASSWORD_MAX_BYTES} bytes at most "
    "(emoji and accented letters use more than one)."
)
# Ceiling on a password PRESENTED for verification (sign-in, current-password on change).
# Generous — legacy accounts predate today's rule and must keep signing in — but
# bounded: an unbounded field was JSON-encoded and forwarded to GoTrue synchronously ON
# the event loop (`_BLOCKING_BY_DESIGN`), so a 2 MB "password" from an unauthenticated
# caller stalled every other request for the whole upstream round trip.
SIGN_IN_SECRET_MAX_LENGTH = 1024
# Provider / session tokens: an Apple id_token is ~1 KiB, a Supabase JWT ~1 KiB, a refresh
# token a few dozen bytes. 8 KiB leaves room for any claim set without admitting a payload.
TOKEN_MAX_LENGTH = 8192
# Sign in with Apple's authorization code is a short opaque string (~65 characters today);
# 1 KiB leaves room for a format change without admitting a payload.
AUTHORIZATION_CODE_MAX_LENGTH = 1024
# The provider name a social sign-in carries is TRUNCATED to DISPLAY_NAME_MAX_LENGTH, not refused
# (OAuthSignInRequest); this raw bound only stops a payload.
OAUTH_DISPLAY_NAME_RAW_MAX_LENGTH = 512

# One-time codes (signup confirmation, password recovery). The live project sends 8 digits; the
# range is deliberately wider so a dashboard change to the OTP length never 422s every user.
# The bounds apply to the DIGITS left after normalisation, not to the raw input: the raw field
# is only bounded (OTP_RAW_MAX_LENGTH) so a pasted "1234 5678" or "1234-5678" still fits.
OTP_MIN_DIGITS = 6
OTP_MAX_DIGITS = 12
OTP_RAW_MAX_LENGTH = 32
_OTP_SEPARATORS = frozenset("-\u2010\u2011\u2012\u2013\u2014\u2212")

# ⚠️ SECRETS NEVER REPR (SEC-1). Every request model below is a local in its handler's frame,
# and Sentry ships frame locals: a pydantic model is serialised as its repr, e.g.
# "SignUpRequest(email='***@***', password='Hunter2!xyz')". The key-based scrubbers never see
# a value hiding inside a repr string, so a GoTrue 429 on sign-up put plaintext passwords and
# reset codes into Sentry. `repr=False` drops the field from __repr__/__str__ ONLY — it is
# still validated, still readable as an attribute and still in `model_dump()`, so every call
# that forwards it to GoTrue is unchanged. tests/test_auth_confirmation_and_oauth.py walks
# every model in this file and fails on a secret that reprs.


#: Human-readable statement of the rule, mirrored by the iOS checklist
#: (`Views/Molecules/PasswordRequirementsView.swift`). One sentence so the two cannot describe
#: different rules to the same user.
PASSWORD_RULE_TEXT = (
    f"Password must be at least {PASSWORD_MIN_LENGTH} characters ({PASSWORD_MAX_BYTES} bytes at "
    "most) and include an uppercase letter (A–Z), a lowercase letter (a–z), a number (0–9) and "
    "a symbol such as ! @ # $. Spaces, accented letters and emoji don't count toward these."
)


def _normalize_otp_code(value: str) -> str:
    """The digits of an emailed one-time code, normalised, or ValueError.

    ONE rule for every route that takes a code (reset, set-password, confirm-email):
      * whitespace anywhere and dashes are dropped — people paste "1234 5678" / "1234-5678";
      * any Unicode DECIMAL digit becomes its ASCII digit — an Arabic-Indic or full-width
        keyboard types "١٢٣٤٥٦٧٨", which GoTrue would compare as a different string and refuse
        as an invalid code the user cannot see anything wrong with;
      * anything else (letters, superscripts, punctuation) is refused here, before the code is
        sent upstream;
      * the DIGIT count must be OTP_MIN_DIGITS..OTP_MAX_DIGITS. The old rule bounded only the
        raw length, so "1-2-3-" (6 characters, 3 digits) passed and reached GoTrue.
    """
    if not isinstance(value, str):
        raise ValueError("Code must be the digits from your email.")
    digits = []
    for ch in value:
        if ch.isspace() or ch in _OTP_SEPARATORS:
            continue
        if ch.isdecimal():
            digits.append(str(unicodedata.decimal(ch)))
            continue
        raise ValueError("Code must be the digits from your email.")
    cleaned = "".join(digits)
    if not (OTP_MIN_DIGITS <= len(cleaned) <= OTP_MAX_DIGITS):
        raise ValueError("Code must be the digits from your email.")
    return cleaned


def _validate_password_strength(value: str) -> str:
    """Shared password rule for every endpoint that SETS a password.

    ⚠️ THIS IS GOTRUE'S RULE, EXACTLY — not a superset of it (reversed on purpose, 2026-11-26).
    Auth → Sign In/Providers → Email is configured with
    `Password requirements = lowercase, uppercase letters, digits and symbols` and
    `Minimum password length = 8`, and GoTrue itself caps a password at 72 bytes.

    Checking it here is what stops the app ACCEPTING a password the provider will refuse, and
    on two routes that is not a cosmetic difference: reset- and set-password run `verify_otp`
    FIRST, which spends the single-use emailed code, and only then write the password. A
    password we passed and GoTrue refused burned the code (PW-1), with three recovery emails an
    hour per address — a few spaced passphrases ("Blue Sky 2025") locked recovery for an hour.

    This used to define "symbol" as ANY non-alphanumeric character and "uppercase" as any
    `isupper()`, a deliberate SUPERSET meant never to reject what GoTrue accepts. The cost of
    that asymmetry turned out to be the worse one: a space, "€" or "É" passed here and failed
    there, AFTER the code was spent. So the classes are now GoTrue's own ASCII sets
    (`PASSWORD_LOWERCASE` / `_UPPERCASE` / `_DIGITS` / `_SYMBOLS`). Other characters are still
    allowed inside a password; they just count toward no class, and the message says so.
    The HIBP "pwned" refusal cannot be checked ahead of time, so the after-verify
    AUTH_PASSWORD_REJECTED path stays.

    Order: too short, too long (bytes), padded, then EVERY missing class in one message.

    ⚠️ Applies ONLY to endpoints that SET a password (sign-up, reset, change, set-password, and
    confirm-email's set-password branch — called there by hand, never on its schema).
    `SignInRequest` has no such validator and must never grow one: existing accounts predate
    this rule, and enforcing it at sign-in would lock out every user whose password does not
    satisfy it. A LENGTH ceiling does apply at sign-in (`SIGN_IN_SECRET_MAX_LENGTH`), which is a
    different thing: it is far above 72 bytes, so it cannot lock anyone out, and it turns a
    multi-megabyte "password" into a 422 with no upstream call.
    """
    if not isinstance(value, str):
        raise ValueError("Password must be text.")
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
        )
    # Characters first (cheap, and anything over 72 characters is over 72 bytes), so a
    # 1 MiB body is never UTF-8 encoded just to be refused.
    if len(value) > PASSWORD_MAX_BYTES or len(value.encode("utf-8", "surrogatepass")) > PASSWORD_MAX_BYTES:
        raise ValueError(PASSWORD_TOO_LONG_MESSAGE)
    if value.strip() != value:
        raise ValueError("Password can't start or end with a space.")

    # Reported as ONE message naming every unmet class, not the first failure. Revealing them
    # one at a time makes the user resubmit repeatedly to discover a rule we already knew in
    # full — and each round trip costs a slot against the registration rate limiter.
    present = set(value)
    missing = []
    if not present & _UPPER_SET:
        missing.append("an uppercase letter (A–Z)")
    if not present & _LOWER_SET:
        missing.append("a lowercase letter (a–z)")
    if not present & _DIGIT_SET:
        missing.append("a number (0–9)")
    if not present & _SYMBOL_SET:
        missing.append("a symbol such as ! @ # $")
    if missing:
        listed = missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " and " + missing[-1]
        message = f"Password must include {listed}."
        # Only when it explains something: the password holds a character the user may think
        # counts (a space, "É", "€", "٣") but GoTrue does not.
        if present - _CLASS_CHARS:
            message += " Spaces, accented letters and emoji don't count toward these."
        raise ValueError(message)
    return value


class SignInRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=SIGN_IN_SECRET_MAX_LENGTH, repr=False)


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(repr=False)
    # Same bound as `UpdateProfileRequest.display_name`, and imported from there so the two
    # cannot drift: this is the SAME column, reached by an UNAUTHENTICATED route, so leaving it
    # open here would make the bound on the authenticated one pointless.
    display_name: str = Field(min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class ForgotPasswordRequest(BaseModel):
    """Start a reset. Responds identically whether or not the account exists — see the
    endpoint docstring on account enumeration."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Finish a reset using the one-time code from the email."""

    email: EmailStr
    # Supabase recovery OTP (8 digits as configured today). `_normalize_otp_code` enforces
    # OTP_MIN_DIGITS..OTP_MAX_DIGITS digits rather than pinning one length, so a provider-side
    # change doesn't 422 every user.
    code: str = Field(min_length=OTP_MIN_DIGITS, max_length=OTP_RAW_MAX_LENGTH, repr=False)
    new_password: str = Field(repr=False)

    @field_validator("code")
    @classmethod
    def _code_digits(cls, v: str) -> str:
        return _normalize_otp_code(v)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class ChangePasswordRequest(BaseModel):
    """Change the password of an already signed-in user."""

    current_password: str = Field(max_length=SIGN_IN_SECRET_MAX_LENGTH, repr=False)
    new_password: str = Field(repr=False)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class SetPasswordRequest(BaseModel):
    """Create a FIRST password for a signed-in account that has none (Apple/Google sign-in).

    No `current_password`, because there is none — and no `email` either: it is resolved from
    the token's subject, never from the body, so a caller cannot aim the write at another
    account. The emailed recovery `code` is the proof of mailbox control; the bearer token alone
    is deliberately not enough, or a stolen access token would be sufficient to take permanent
    ownership of the account, which is exactly what change-password's current-password
    requirement exists to prevent.
    """

    # Same shape as ResetPasswordRequest.code — it IS a Supabase recovery OTP, obtained from the
    # unchanged `POST /auth/forgot-password`.
    code: str = Field(min_length=OTP_MIN_DIGITS, max_length=OTP_RAW_MAX_LENGTH, repr=False)
    new_password: str = Field(repr=False)

    @field_validator("code")
    @classmethod
    def _code_digits(cls, v: str) -> str:
        return _normalize_otp_code(v)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class ConfirmEmailRequest(BaseModel):
    """Confirm a password signup with the code from the Confirm-signup email, and sign in.

    `password` is the one the user typed on the sign-up or sign-in form, held in memory by the
    app across the code step. It is deliberately NOT strength-validated here: it may be the
    account's EXISTING password, which can predate today's rule (the same reason
    `SignInRequest` has no validator). It is checked against the rule only when it is about to
    be WRITTEN — `/auth/confirm-email`'s set-password step (the stored password differs, or the
    sign-in probe failed upstream).

    `min_length=1` is not a strength rule: an empty password can never match a stored one nor be
    set, and refusing it here (422) happens BEFORE the single-use code is spent.
    """

    email: EmailStr
    code: str = Field(min_length=OTP_MIN_DIGITS, max_length=OTP_RAW_MAX_LENGTH, repr=False)
    password: str = Field(min_length=1, max_length=SIGN_IN_SECRET_MAX_LENGTH, repr=False)

    @field_validator("code")
    @classmethod
    def _code_digits(cls, v: str) -> str:
        return _normalize_otp_code(v)


class ChangeEmailRequest(BaseModel):
    """Start changing the signed-in account's email address (audit CC-09).

    The bearer token alone is never enough — a stolen access token would otherwise move the
    account to the thief's mailbox, and every reset code with it. So exactly ONE proof comes
    with the request:
      * `current_password` — an account with a password, re-checked by a real sign-in; or
      * `code` — the recovery code `POST /auth/forgot-password` emails to the CURRENT address
        (an Apple/Google account has no password; the same proof `/auth/set-password` takes).
    The current address is resolved from the token's subject, never the body.

    Supabase's Secure email change then mails a code to BOTH addresses, and
    `POST /auth/change-email/confirm` takes the two.
    """

    new_email: EmailStr
    current_password: Optional[str] = Field(
        default=None, min_length=1, max_length=SIGN_IN_SECRET_MAX_LENGTH, repr=False,
    )
    code: Optional[str] = Field(
        default=None, min_length=OTP_MIN_DIGITS, max_length=OTP_RAW_MAX_LENGTH, repr=False,
    )

    @field_validator("code")
    @classmethod
    def _code_digits(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _normalize_otp_code(v)

    @model_validator(mode="after")
    def _exactly_one_proof(self) -> "ChangeEmailRequest":
        if (self.current_password is None) == (self.code is None):
            raise ValueError("send exactly one of current_password or code")
        return self


class ConfirmEmailChangeRequest(BaseModel):
    """Finish an email change with the two codes Supabase's Secure email change sent: one to
    the current address, one to the new one. Both addresses are read from the account itself
    (the token's subject), never from the body."""

    current_code: str = Field(min_length=OTP_MIN_DIGITS, max_length=OTP_RAW_MAX_LENGTH, repr=False)
    new_code: str = Field(min_length=OTP_MIN_DIGITS, max_length=OTP_RAW_MAX_LENGTH, repr=False)

    @field_validator("current_code", "new_code")
    @classmethod
    def _code_digits(cls, v: str) -> str:
        return _normalize_otp_code(v)


#: `EmailChangeResponse.status` values. iOS decodes this field; a new value needs an iOS branch
#: first (the decoder treats an unknown one as "codes sent", the safe reading).
EMAIL_CHANGE_CODES_SENT = "codes_sent"
EMAIL_CHANGE_CHANGED = "changed"


class EmailChangeResponse(BaseModel):
    """Both email-change steps answer with this shape.

    `status`:
      * "codes_sent" — `/auth/change-email`: check both inboxes. Also the answer when another
        account already uses the address: no email goes out then, but the response is the same,
        so this route never tells anyone which addresses have accounts.
      * "changed" — `/auth/change-email/confirm` (or a project with email confirmation off): the
        account's address is now `email`.
    `email` is the address the user asked for ("codes_sent") or the account's new address
    ("changed"). The caller's session is untouched either way: nothing is re-minted.
    """

    status: str
    email: str
    message: str


class MessageResponse(BaseModel):
    """Generic acknowledgement for flows that must not leak whether an account exists."""

    message: str


class PasswordChangedResponse(BaseModel):
    """Acknowledgement for `POST /auth/change-password`, WITH replacement credentials.

    Changing a password stamps `users.password_changed_at`, and every token minted before that
    instant is then rejected (migration 105) — including the one the caller just used. So the
    request that changed the password logged the caller out of their own session: the very next
    authenticated call 401'd, and the app dropped them to the signed-out state seconds after a
    successful password change, which reads as "the change failed".

    These tokens are minted AFTER the stamp, so `iat >= password_changed_at` and this session
    survives while every OTHER device is still evicted — which is the entire point of the
    feature. Additive fields, so an older client that ignores them still decodes this fine.
    """

    message: str
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    user_id: str


class SignUpResponse(BaseModel):
    """Result of /auth/register.

    Email confirmation is REQUIRED (policy: option A), so a normal signup returns
    `confirmation_required = True` and NO tokens — the account exists but can't be used
    until the address is confirmed. Previously this endpoint minted app JWTs immediately
    regardless of confirmation, so anyone could hold an account on an address they don't own.

    The token fields exist because a project with email confirmation switched OFF (local dev,
    or self-hosted) legitimately returns a usable session. Rather than pretending otherwise,
    the client checks `confirmation_required` and behaves accordingly.
    """

    confirmation_required: bool
    message: str
    access_token: Optional[str] = Field(default=None, repr=False)
    refresh_token: Optional[str] = Field(default=None, repr=False)
    token_type: str = "bearer"
    user_id: Optional[str] = None


class ResendConfirmationRequest(BaseModel):
    """Re-send the signup confirmation email."""

    email: EmailStr


class OAuthSignInRequest(BaseModel):
    """Native social sign-in.

    `id_token` is the provider's OpenID identity token, obtained on-device (Sign in with
    Apple via `ASAuthorizationAppleIDProvider`). It is verified by Supabase against the
    provider's public keys — the backend never trusts it directly.

    Emails from Apple and Google arrive ALREADY VERIFIED, which is why this path is exempt
    from the email-confirmation gate that applies to password signups.
    """

    provider: str = Field(pattern="^(apple|google)$")
    id_token: str = Field(min_length=16, max_length=TOKEN_MAX_LENGTH, repr=False)
    # Apple's flow binds a nonce to the token to prevent replay. Required for Apple when the
    # client generated one; Supabase verifies the pairing.
    nonce: Optional[str] = Field(default=None, max_length=TOKEN_MAX_LENGTH, repr=False)
    # Apple only returns the display name on the FIRST authorization, so the client passes
    # it through for us to persist. Never used as an identity claim. Same STORED bound as the
    # column's other writers (`get_current_user` re-reads this row on every request) — but
    # TRUNCATED to it, not refused: a provider name over 64 characters used to 422 the whole
    # sign-in, and the retry carried no name (Apple sends it once), so the user could neither get
    # in on that tap nor keep their name. The raw bound only stops a payload.
    display_name: Optional[str] = Field(default=None, max_length=OAUTH_DISPLAY_NAME_RAW_MAX_LENGTH)
    # Sign in with Apple's single-use authorization code (~5 minutes), OPTIONAL: builds 1.0–1.03
    # never send it and must keep decoding. Exchanged inside the same request for a refresh token
    # that account deletion can revoke (SOC-04, `services/apple_sign_in_service.py`). Ignored for
    # Google. A credential: repr=False, and `log_redaction` redacts the key by name.
    authorization_code: Optional[str] = Field(
        default=None, max_length=AUTHORIZATION_CODE_MAX_LENGTH, repr=False,
    )

    @field_validator("display_name")
    @classmethod
    def _cap_display_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()[:DISPLAY_NAME_MAX_LENGTH].strip()
        return trimmed or None


class SessionExchangeRequest(BaseModel):
    """Exchange a Supabase-issued access token for app tokens.

    Used by the web-based OAuth flow (Google via `ASWebAuthenticationSession`), where the
    provider round-trip ends with Supabase's own JWT rather than a provider id_token.
    """

    supabase_access_token: str = Field(min_length=16, max_length=TOKEN_MAX_LENGTH, repr=False)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(max_length=TOKEN_MAX_LENGTH, repr=False)


class TokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    token_type: str = "bearer"
    user_id: str
    # The install's lockout exemption for this address (`core.security.create_device_proof`).
    # Issued by the flows that VERIFIED a credential (password sign-in, provider sign-in,
    # session exchange); absent on a refresh and on `/auth/confirm-email` (an emailed code is
    # not a verified credential, auth.md §3a). Optional so an older client's decoder never
    # sees a required key it does not know.
    device_token: Optional[str] = Field(default=None, repr=False)


class AuthUserResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    tier: str = "free"
