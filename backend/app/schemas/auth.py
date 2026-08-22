"""Auth request/response schemas matching frontend APIEndpoint.swift."""

from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional

# The display-name bound lives with the profile schema that owns the column, and is imported
# rather than restated: signup and PATCH /users/me write the SAME `users.display_name`, so two
# independent literals would drift and leave the unauthenticated route as the loose one.
from app.schemas.user import DISPLAY_NAME_MAX_LENGTH

# Minimum password length. NIST SP 800-63B guidance favours length over composition
# rules, so there is deliberately no "must contain a symbol" requirement — those push
# users toward predictable substitutions without adding real entropy.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128  # bcrypt truncates past 72 bytes; cap well below any DoS range


#: Human-readable statement of the rule, used by the API error AND mirrored on the iOS sign-up
#: form. One string so the two can never describe different rules to the same user.
PASSWORD_RULE_TEXT = (
    f"Password must be at least {PASSWORD_MIN_LENGTH} characters and include an uppercase "
    "letter, a lowercase letter, a number, and a symbol."
)


def _validate_password_strength(value: str) -> str:
    """Shared password rule for every endpoint that SETS a password.

    ⚠️ THIS MIRRORS A SUPABASE SETTING, and must never be STRICTER than it.
    Auth → Sign In/Providers → Email is configured with
    `Password requirements = lowercase, uppercase letters, digits and symbols` and
    `Minimum password length = 8`. GoTrue enforces that server-side whatever we do here.

    Checking it here as well is not redundant — it is what stops the app ACCEPTING a password
    the provider will refuse. Before this, iOS gated on `count >= 8` and this function checked
    length only, so `abcdefgh` sailed through both and was rejected by GoTrue at the last
    moment, for a rule the user had never been shown.

    "Symbol" is deliberately ANY NON-ALPHANUMERIC character, which is a superset of GoTrue's
    own symbol list. The asymmetry is the safety property: a superset can never reject
    something the provider would have accepted (a false rejection the user cannot diagnose,
    because our message would name a rule they appear to satisfy). The reverse — a password we
    accept and GoTrue refuses, e.g. one whose only symbol is exotic — is still handled, and now
    handled well: it surfaces as AUTH_PASSWORD_REJECTED carrying the provider's own reasons.

    ⚠️ Applies ONLY to endpoints that SET a password (sign-up, reset, change). `SignInRequest`
    has no such validator and must never grow one: existing accounts predate this rule, and
    enforcing it at sign-in would lock out every user whose password does not satisfy it —
    turning a hardening change into an outage.
    """
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
        )
    if len(value) > PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"Password must be {PASSWORD_MAX_LENGTH} characters or fewer."
        )
    if value.strip() != value:
        raise ValueError("Password can't start or end with a space.")

    # Reported as ONE message naming every unmet class, not the first failure. Revealing them
    # one at a time makes the user resubmit repeatedly to discover a rule we already knew in
    # full — and each round trip costs a slot against the registration rate limiter.
    missing = []
    if not any(c.isupper() for c in value):
        missing.append("an uppercase letter")
    if not any(c.islower() for c in value):
        missing.append("a lowercase letter")
    if not any(c.isdigit() for c in value):
        missing.append("a number")
    if not any(not c.isalnum() for c in value):
        missing.append("a symbol")
    if missing:
        raise ValueError("Password must include " + ", ".join(missing) + ".")
    return value


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
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
    # Supabase recovery OTP. 6 digits today; accept a small range rather than pinning an
    # exact length so a provider-side change doesn't 422 every user.
    code: str = Field(min_length=6, max_length=12)
    new_password: str

    @field_validator("code")
    @classmethod
    def _code_digits(cls, v: str) -> str:
        cleaned = v.strip().replace(" ", "").replace("-", "")
        if not cleaned.isdigit():
            raise ValueError("Code must be the digits from your email.")
        return cleaned

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class ChangePasswordRequest(BaseModel):
    """Change the password of an already signed-in user."""

    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


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
    access_token: str
    refresh_token: str
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
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
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
    id_token: str = Field(min_length=16)
    # Apple's flow binds a nonce to the token to prevent replay. Required for Apple when the
    # client generated one; Supabase verifies the pairing.
    nonce: Optional[str] = None
    # Apple only returns the display name on the FIRST authorization, so the client passes
    # it through for us to persist. Never used as an identity claim.
    display_name: Optional[str] = None


class SessionExchangeRequest(BaseModel):
    """Exchange a Supabase-issued access token for app tokens.

    Used by the web-based OAuth flow (Google via `ASWebAuthenticationSession`), where the
    provider round-trip ends with Supabase's own JWT rather than a provider id_token.
    """

    supabase_access_token: str = Field(min_length=16)


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str


class AuthUserResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    tier: str = "free"
