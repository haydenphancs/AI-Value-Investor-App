"""The page the Confirm-signup email's LINK opens: `GET/POST /auth/confirm` on caydexinvest.com.

WHY A LINK AT ALL. The email carries an 8-digit code for this app, and a link for installs
that predate the code screen (1.02 / 1.03 have none — owner decision 2026-11-26, "Code + link").

WHY NOT SUPABASE'S OWN `{{ .ConfirmationURL }}` (CC-02). That link goes through GoTrue's
`/verify` and redirects to the Site URL with `#access_token=…&refresh_token=…` in the fragment —
an implicit-flow Supabase session parked in the address bar and the (iCloud-synced) browser
history, which `/auth/session-exchange` turns into a lasting Caydex session. The email instead
links here with only `token_hash`; the backend redeems it SERVER-SIDE and discards the session.

WHY A BUTTON (a form POST) AND NOT A GET THAT VERIFIES. Mail scanners (Outlook Safe Links and
similar) prefetch every link in a message. A GET that consumed the single-use token would
confirm the address and then show the real user "expired". The GET only draws a static page
with a "Confirm my email" button; nothing upstream is called until a person submits it. The
failure copy still says "if you already confirmed, just sign in", because a scanner that
submits forms, or a second tap, can spend the token first.

THE RULES THIS PAGE KEEPS (marketing.md §8 — caydexinvest.com is the passkey domain):
  * static, code-authored copy; no script, no external resource, no model text, no market data;
  * the only request value ever drawn is `token_hash`, only in the hidden form field, and only
    after it matched `TOKEN_HASH_RE` (hex) — anything else gets the failure page;
  * `type` must be exactly "signup": never "email", which GoTrue also matches against a
    RECOVERY token, and never "recovery";
  * `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, the landing CSP with
    `form-action 'self'` (the one thing this page needs that the landing does not);
  * the POST is limited per IP in the protected pool, and its body is read with a hard cap;
  * this module's own log lines never carry the token, the session or the address, and its
    frames' locals are dropped from Sentry events (`log_redaction._CREDENTIAL_FRAME_MODULES`).

WHAT IS NOT IN OUR HANDS. The GET's URL carries `token_hash`. Our access-log line is redacted
(`app.log_redaction`), but anything in front of the app — Railway's edge/proxy logs, a mail
scanner's own records — may keep the full URL. The hash's safety rests on it being single-use,
short-lived (the OTP expiry) and worthless once the address is confirmed, not on nobody ever
seeing it. Never claim more than that here.

THE LINK'S KNOWN RESIDUAL (BE-AUTH-02). It confirms on the token alone and keeps whatever
password GoTrue holds — the FIRST registrant's. The code route closes that (it re-proves or SETS
the typed password); this page cannot without a password field. So the success copy names
Forgot password, and the prompt says "Didn't sign up? Close this page".
"""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from supabase import Client

from app.core.security import rate_limiter, trusted_client_ip
from app.database import get_auth_client, run_gotrue
from app.dependencies import forward_client_ip_to_gotrue

logger = logging.getLogger(__name__)

router = APIRouter()

CONFIRM_PATH = "/auth/confirm"
#: GoTrue's email token hash is a hex digest (sha224 → 56 characters today). Bounded both ways
#: so neither a stub nor a megabyte of hex is ever drawn into the page or sent upstream.
TOKEN_HASH_RE = re.compile(r"^[A-Fa-f0-9]{20,128}$")
#: The ONLY OTP type this page redeems.
CONFIRM_TYPE = "signup"
#: The form is two short fields; anything bigger is not our form.
MAX_FORM_BYTES = 2048
#: POST limiter: per trusted client IP, protected pool (it guards a credential-adjacent verb).
POST_LIMIT_MAX = 30
POST_LIMIT_WINDOW_SECONDS = 600

_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "site" / "email_confirm.html"

# The landing page's CSP (`app/main.py::_LANDING_CSP`) with `form-action 'self'` instead of
# 'none': the confirm button is a same-origin form POST, and nothing else is allowed.
PAGE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_PAGE_HEADERS = {
    "Content-Security-Policy": PAGE_CSP,
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    # The URL carries the token hash; it must never ride a Referer header anywhere.
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}

# ── Copy (code-owned; every string is HTML-escaped when drawn) ─────────────────
HEADING_PROMPT = "Confirm your email"
COPY_PROMPT = "Tap the button to confirm the email address for your Caydex account."
COPY_PROMPT_NOTE = (
    "Didn't sign up for Caydex? Close this page. Nothing changes unless you tap the button."
)
BUTTON_LABEL = "Confirm my email"

HEADING_SUCCESS = "Email confirmed"
# The link confirms on the token alone and KEEPS whatever password GoTrue holds — the first
# registrant's (BE-AUTH-02). Someone who re-registered with a new password (CC-03) would find
# their newer one refused, so the page names the way out. (The code route avoids this by setting
# the typed password; a password field here needs an owner review — this is the passkey domain.)
COPY_SUCCESS = (
    "Your email is confirmed. Open the Caydex app and sign in. If your password doesn't work, "
    "use Forgot password in the app."
)

HEADING_FAILURE = "This link can't be used"
# Written for the builds this link exists for (1.0–1.03: no code screen, and they re-send a
# confirmation email on every unconfirmed sign-in, so an older email's link is usually the dead
# one — COMPAT-1). Version-neutral: no control only the new build has. "Sign in to get a new
# email" holds on both: old builds auto-resend, the new one opens the code screen.
COPY_FAILURE = (
    "This link has expired or was already used. Each new email from Caydex replaces the earlier "
    "ones, so open the newest one and tap its button. If you already confirmed, just sign in to "
    "the Caydex app. Otherwise open the app and sign in to get a new confirmation email."
)

HEADING_UNAVAILABLE = "Please try again"
COPY_UNAVAILABLE = "We couldn't confirm your email right now. Please try again in a few minutes."

HEADING_RATE_LIMITED = "Too many attempts"
COPY_RATE_LIMITED = (
    "Too many attempts from this network. Please wait a few minutes, then try again."
)

# Used only if the template file is missing from the image (a packaging fault): the page must
# still work, because without it nobody on an old app version can confirm.
_FALLBACK_TEMPLATE = (
    "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\" />"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />"
    "<meta name=\"robots\" content=\"noindex,nofollow\" />"
    "<title>{{TITLE}} · Caydex</title></head><body>"
    "<main><h1>{{HEADING}}</h1>{{BODY}}{{FORM}}</main></body></html>"
)
_SLOTS = ("{{TITLE}}", "{{HEADING}}", "{{BODY}}", "{{FORM}}")


def is_valid_link(token_hash: Optional[str], otp_type: Optional[str]) -> bool:
    """Both parameters present, `type` exactly "signup", `token_hash` hex within bounds."""
    return (
        isinstance(token_hash, str)
        and isinstance(otp_type, str)
        and otp_type == CONFIRM_TYPE
        and TOKEN_HASH_RE.fullmatch(token_hash) is not None
    )


def _single(values: Iterable[str]) -> Optional[str]:
    """The value when the parameter appeared EXACTLY once; a repeated parameter is refused
    rather than resolved, so no parser disagreement can pick a value we did not validate."""
    vals = list(values or [])
    return vals[0] if len(vals) == 1 else None


def _load_template() -> str:
    try:
        template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.error(
            "email-confirm page: template unreadable at %s (%s: %s) — serving the fallback",
            _TEMPLATE_PATH, type(e).__name__, e,
        )
        return _FALLBACK_TEMPLATE
    if any(slot not in template for slot in _SLOTS):
        logger.error(
            "email-confirm page: template at %s is missing a slot — serving the fallback",
            _TEMPLATE_PATH,
        )
        return _FALLBACK_TEMPLATE
    return template


def render_page(
    *,
    heading: str,
    paragraphs: Iterable[str],
    status_code: int,
    form_token: Optional[str] = None,
    note: Optional[str] = None,
    extra_headers: Optional[dict] = None,
) -> HTMLResponse:
    """Fill the template with code-owned copy. `form_token` draws the confirm button; it is
    re-validated HERE as well, so no caller can ever draw an unvalidated request value."""
    body = "\n        ".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    form = ""
    if form_token is not None:
        if not is_valid_link(form_token, CONFIRM_TYPE):  # pragma: no cover - callers validate
            raise ValueError("refusing to draw an unvalidated token_hash")
        form = (
            f'<form method="post" action="{CONFIRM_PATH}">'
            f'<input type="hidden" name="token_hash" value="{html.escape(form_token, quote=True)}" />'
            f'<input type="hidden" name="type" value="{CONFIRM_TYPE}" />'
            f'<button type="submit">{html.escape(BUTTON_LABEL)}</button>'
            f"</form>"
        )
        if note:
            form += f'\n        <p class="note">{html.escape(note)}</p>'
    page = _load_template()
    for slot, value in (
        ("{{TITLE}}", html.escape(heading)),
        ("{{HEADING}}", html.escape(heading)),
        ("{{BODY}}", body),
        ("{{FORM}}", form),
    ):
        page = page.replace(slot, value)
    headers = dict(_PAGE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    return HTMLResponse(content=page, status_code=status_code, headers=headers)


def _failure_page() -> HTMLResponse:
    return render_page(heading=HEADING_FAILURE, paragraphs=[COPY_FAILURE], status_code=400)


@router.api_route(CONFIRM_PATH, methods=["GET", "HEAD"], include_in_schema=False)
async def email_confirm_page(request: Request) -> HTMLResponse:
    """The link's landing. STATIC: never calls GoTrue, so a scanner's prefetch spends nothing.

    Takes no client dependency on purpose — there is nothing here that could redeem the token.
    """
    params = request.query_params
    token_hash = _single(params.getlist("token_hash"))
    otp_type = _single(params.getlist("type"))
    if not is_valid_link(token_hash, otp_type):
        logger.info("email-confirm page: link parameters missing or malformed")
        return _failure_page()
    return render_page(
        heading=HEADING_PROMPT,
        paragraphs=[COPY_PROMPT],
        status_code=200,
        form_token=token_hash,
        note=COPY_PROMPT_NOTE,
    )


async def _read_form(request: Request) -> Optional[dict]:
    """The url-encoded form, or None for anything that is not exactly that.

    Hand-parsed on purpose: `request.form()` would also accept multipart bodies (with file
    parts spooled to disk) on an unauthenticated root route. A hard byte cap is applied while
    streaming — `cap_json_body` allows up to 1 MiB, far beyond two hidden fields.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_FORM_BYTES:
            return None
    try:
        text = bytes(body).decode("ascii")
        return parse_qs(text, keep_blank_values=True, max_num_fields=8)
    except (UnicodeDecodeError, ValueError):
        return None


# The POST is the only verb here that reaches GoTrue; it forwards the caller's trusted address
# so GoTrue's per-IP `verify` limit buckets this person, not the shared egress (SEC-7).
@router.post(
    CONFIRM_PATH, include_in_schema=False,
    dependencies=[Depends(forward_client_ip_to_gotrue)],
)
async def email_confirm_submit(
    request: Request,
    auth_client: Client = Depends(get_auth_client),
) -> HTMLResponse:
    """A person tapped "Confirm my email": redeem the token server-side, show the outcome."""
    # Classifier shared with the API's own OTP routes (one table of GoTrue codes, one truth).
    from app.api.v1.endpoints.auth import (
        _classify_gotrue_failure, _gotrue_code, _gotrue_status,
    )

    client_ip = trusted_client_ip(request)
    if not rate_limiter.is_allowed(
        f"emailconfirm:ip:{client_ip}",
        max_requests=POST_LIMIT_MAX, window_seconds=POST_LIMIT_WINDOW_SECONDS, protected=True,
    ):
        logger.warning("email-confirm page: per-IP limit reached")
        return render_page(
            heading=HEADING_RATE_LIMITED, paragraphs=[COPY_RATE_LIMITED], status_code=429,
            extra_headers={"Retry-After": str(POST_LIMIT_WINDOW_SECONDS)},
        )

    form = await _read_form(request)
    token_hash = _single((form or {}).get("token_hash", []))
    otp_type = _single((form or {}).get("type", []))
    if not is_valid_link(token_hash, otp_type):
        logger.info("email-confirm page: submitted form missing or malformed")
        return _failure_page()

    try:
        verified = await run_gotrue(
            auth_client.auth.verify_otp, {"token_hash": token_hash, "type": CONFIRM_TYPE},
        )
    except Exception as e:
        kind = _classify_gotrue_failure(e)
        status_code, code = _gotrue_status(e), _gotrue_code(e)
        if kind == "rejected":
            # Expired, already used (often by our own earlier tap or a scanner), or never real.
            logger.info(
                "email-confirm page: token refused (%s status=%s code=%s)",
                type(e).__name__, status_code, code,
            )
            return _failure_page()
        if kind == "rate_limited":
            logger.error(
                "GOTRUE_RATE_LIMITED route=email-confirm-page verb=verify_otp status=%s code=%s",
                status_code, code,
            )
        else:
            logger.error(
                "email-confirm page: verify_otp failed upstream: %s: %s (status=%s code=%s)",
                type(e).__name__, e, status_code, code,
            )
        # The token is very likely still good: offer the same button again.
        return render_page(
            heading=HEADING_UNAVAILABLE, paragraphs=[COPY_UNAVAILABLE], status_code=503,
            form_token=token_hash,
        )

    # The Supabase session GoTrue returned is DISCARDED: never drawn, never logged, never kept.
    # (`run_gotrue` also clears the isolated client's in-memory session before its next verb.)
    user = getattr(verified, "user", None)
    user_id = str(getattr(user, "id", "") or "")
    if not user_id:
        logger.warning("email-confirm page: verify_otp returned no user — showing the failure page")
        return _failure_page()
    if not getattr(user, "email_confirmed_at", None):
        logger.error(
            "email-confirm page: verify_otp succeeded but user=%s is still unconfirmed", user_id,
        )
        return render_page(
            heading=HEADING_UNAVAILABLE, paragraphs=[COPY_UNAVAILABLE], status_code=503,
        )
    logger.info("email-confirm page: address confirmed for user=%s", user_id)
    return render_page(heading=HEADING_SUCCESS, paragraphs=[COPY_SUCCESS], status_code=200)
