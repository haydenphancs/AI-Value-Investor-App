"""Auth request/response schemas matching frontend APIEndpoint.swift."""

from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional

# Minimum password length. NIST SP 800-63B guidance favours length over composition
# rules, so there is deliberately no "must contain a symbol" requirement — those push
# users toward predictable substitutions without adding real entropy.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128  # bcrypt truncates past 72 bytes; cap well below any DoS range


def _validate_password_strength(value: str) -> str:
    """Shared password rule for every endpoint that SETS a password."""
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
    return value


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str

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
