"""
APNs push service — token-based auth (.p8), HTTP/2 via httpx.

Sends alert pushes to a user's registered `device_tokens` (migration 102).

This is the DELIVERY layer and nothing else. It does not decide who gets a
notification, whether they opted out, or whether one was already sent — that is
`push_dispatch_service`, which claims a `notification_events` row first and then
calls `send_to_user` here. Ten kinds ship through it today; see
`notification_kinds.NOTIFICATION_KINDS` for the list.

Design goals:

  * Gracefully DISABLED when APNs config is absent (`enabled` is False) — the app
    runs fine without push; registration still records tokens for later.
  * httpx only (no requests/urllib), HTTP/2 required by APNs — needs the `h2`
    package (see requirements.txt).
  * ES256 provider JWT (iss=team, kid=key id) signed with the .p8, cached ~50 min
    (APNs allows a token age up to 60 min).
  * Prune tokens APNs rejects as 410 Unregistered / BadDeviceToken.

Apple prerequisites (out-of-band): APNs Auth Key (.p8) + Key ID, Team ID, the
Push Notifications capability on the App ID, and the `aps-environment` entitlement
in the app. Provide APNS_* via env/secrets — never commit the .p8.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import httpx

from app.config import settings
from app.database import get_supabase

logger = logging.getLogger(__name__)


class PushOutcome(NamedTuple):
    """What APNs said about EACH of a user's devices, not just whether one worked.

    WHY THIS IS NOT AN INT. `send_to_user` used to return a bare accepted-count and
    `_deliver` stamped `sent` on any non-zero — so a user with several registered devices
    got a row saying "sent" when only one of them took it, with nothing recorded about the
    others. Per-device rejections were `logger.warning` only, and Railway's log buffer is
    hours deep, so by the time anyone asked "why didn't my phone buzz?" the evidence was
    gone. `last_error` was NULL on every row in the table.

    That is exactly how a TestFlight report of "the price-move notification doesn't work"
    became undiagnosable: the alert's row read `sent`, and the account had three simulator
    tokens alongside one real iPhone. Any one of them accepting was enough to make the row
    look perfect.

    `failures` is what survives into `notification_events.last_error`, so a partial
    delivery is answerable with a SQL query instead of a live log tail.
    """

    attempted: int
    accepted: int
    failures: Tuple[str, ...] = ()

    @property
    def partial(self) -> bool:
        """Some devices took it and some did not — the case that used to be invisible."""
        return 0 < self.accepted < self.attempted

    def summary(self) -> Optional[str]:
        """One line for the ledger, or None when every device accepted."""
        return "; ".join(self.failures)[:500] or None

# APNs allows a provider token up to 60 min old; refresh a little early.
_TOKEN_TTL_SECONDS = 50 * 60

_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}


# The lock-screen banner is a few lines; anything past this is unread on the device and
# unhelpful in a list row. It is a DISPLAY bound, which is why it lives here at the APNs
# boundary and NOT in the senders — see `truncate_for_banner`.
BANNER_BODY_LIMIT = 180

# A defensive ceiling on what the ledger stores. The body of a `ticker_move` is written by a
# grounded LLM search, so it is not length-bounded by construction; this keeps one pathological
# generation from putting a novel in an inbox row. Generous on purpose — the whole point is
# that the detail screen can show the reason in full.
LEDGER_BODY_LIMIT = 1000


def truncate_for_banner(text: str, limit: int = BANNER_BODY_LIMIT) -> str:
    """Shorten a notification body without cutting mid-word, and mark that it was cut.

    ⚠️ THE SENDERS MUST NOT DO THIS THEMSELVES. `updates_insight_sweeper` used to pass
    `headline[:180]`, and that one slice was the whole body the system ever knew: the same
    string went to APNs *and* into `notification_events.body`, so the inbox row was born
    truncated. Every stored `ticker_move` body was exactly 180 characters, ending mid-word —
    "…analyst estimates of $3.27, and sub". The Activity detail screen exists to show the
    catalyst in full and could only ever show a fragment of it.

    Note which text lost: a short fallback headline ("Hydrogen Stocks Face Selloff", ~40-60
    chars) passed through untouched, while the grounded, cited catalyst — the sentence worth
    reading — was the one guillotined.

    Truncation belongs HERE, at the boundary that actually has the constraint, so the ledger
    keeps the full text and only the banner is shortened.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    # Only honour a word boundary that is not absurdly early — a 170-character single "word"
    # (a URL, say) should still be cut near the limit rather than thrown away.
    if space > limit // 2:
        cut = cut[:space]
    # A trailing comma or dash before an ellipsis reads as a typo: "guidance,…".
    return cut.rstrip(" ,;:—-") + "…"


def host_for_environment(environment: Optional[str], default: str) -> str:
    """Pure: pick the APNs host for a token's stored environment.

    A device token is bound to the environment it was minted in (sandbox in DEBUG,
    production in Release). Routing a sandbox token to the prod host (or vice-versa)
    yields BadDeviceToken, so each token must go to ITS OWN host — never a single
    global one. Falls back to `default` (the server's APNS_ENV) then sandbox.
    """
    env = (environment or default or "sandbox").lower()
    return _HOSTS.get(env, _HOSTS["sandbox"])


class PushService:
    # Class-level JWT cache shared across instances (the signing inputs are static).
    _jwt: Optional[str] = None
    _jwt_minted_at: float = 0.0

    def __init__(self):
        self.supabase = get_supabase()

    @property
    def enabled(self) -> bool:
        """True only when all signing inputs are configured."""
        return bool(
            settings.APNS_KEY_ID
            and settings.APNS_TEAM_ID
            and settings.APNS_AUTH_KEY
            and settings.APNS_BUNDLE_ID
        )

    def _provider_jwt(self) -> Optional[str]:
        """Mint (and cache) the ES256 provider token used as the APNs bearer.

        Uses python-jose (already a dependency) to sign with the .p8 EC key.
        Returns None if signing fails so callers degrade instead of raising.
        """
        now = time.time()
        if PushService._jwt and (now - PushService._jwt_minted_at) < _TOKEN_TTL_SECONDS:
            return PushService._jwt
        try:
            from jose import jwt  # local import: keeps module import cheap

            token = jwt.encode(
                {"iss": settings.APNS_TEAM_ID, "iat": int(now)},
                settings.APNS_AUTH_KEY,
                algorithm="ES256",
                headers={"kid": settings.APNS_KEY_ID},
            )
            PushService._jwt = token
            PushService._jwt_minted_at = now
            return token
        except Exception as e:
            logger.error("APNs provider JWT mint failed (%s: %s)", type(e).__name__, e)
            return None

    def _device_tokens_for(self, user_id: str) -> List[dict]:
        """Return the user's device tokens WITH their per-token environment so each
        can be routed to the correct APNs host.

        SYNCHRONOUS by design — the Supabase Python SDK is sync (CLAUDE.md invariant #5, no
        ORM). Callers inside an `async def` MUST reach it via `asyncio.to_thread`, which is
        what `send_to_user` does. See the note there.
        """
        try:
            result = (
                self.supabase.table("device_tokens")
                .select("token, environment")
                .eq("user_id", user_id)
                .execute()
            )
            return [
                {"token": r["token"], "environment": r.get("environment")}
                for r in (result.data or [])
                if r.get("token")
            ]
        except Exception as e:
            logger.error(
                "device_tokens read failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )
            return []

    def _prune_token(self, token: str) -> None:
        """Best-effort delete of a token APNs reports as gone."""
        try:
            self.supabase.table("device_tokens").delete().eq("token", token).execute()
            logger.info("Pruned dead APNs token …%s", token[-8:])
        except Exception as e:
            logger.warning("Failed to prune dead token (%s: %s)", type(e).__name__, e)

    async def send_to_user(
        self,
        user_id: str,
        *,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        devices: Optional[List[dict]] = None,
        interruption_level: Optional[str] = None,
        thread_id: Optional[str] = None,
        collapse_id: Optional[str] = None,
        category: Optional[str] = None,
        badge: Optional[int] = None,
        expiration_hours: Optional[int] = None,
    ) -> PushOutcome:
        """Send an alert push to every device the user has registered.

        Returns the number of pushes accepted by APNs. No-op (returns 0) when push
        is disabled or the user has no tokens. Never raises — a push failure must
        not break whatever action triggered it.

        `devices` lets a caller that has ALREADY batched the token lookup pass it in
        (`PushDispatchService` reads every recipient's tokens in one `.in_()` query).
        Omitted, the per-user read below still runs, so the single-recipient path and
        the existing tests are unchanged.

        The APNs-shaping arguments all come from the `NotificationKind` registry and are
        optional so a caller that supplies none gets byte-identical behaviour to before:
          * `interruption_level` — `passive` lets iOS batch for battery (right for a
            Form 4); `time-sensitive` pierces Focus (right for a threshold the user
            typed in themselves) but is SILENTLY DOWNGRADED without the
            com.apple.developer.usernotifications.time-sensitive entitlement.
          * `thread_id` — iOS groups the notification stack by this, so a busy day is
            one collapsible stack per topic instead of N stacks of one.
          * `collapse_id` — APNs REPLACES an undelivered notification with the same id
            rather than queueing both. Hard-truncated to 64 bytes below: APNs rejects a
            longer one with a 400, which would drop the whole send.
          * `badge` — server-computed unread count. Never incremented client-side, or it
            drifts the moment a notification is delivered and never opened.
          * `expiration_hours` — how long APNs may keep RETRYING an undelivered push.
            `None` OMITS the header, which is APNs' own store-and-retry default. It is
            NOT the same as sending 0: `apns-expiration: 0` means "attempt once, never
            store", so confusing the two would drop a notification to any device that
            was briefly offline. See `NotificationKind.expiration_hours`.
        """
        # ⚠️ EVERY return below is a `PushOutcome`, never a bare int.
        #
        # These three early exits kept returning `0` after the signature changed, and
        # `_deliver` calls `.summary()` on the result — so the reachable one (`_provider_jwt`
        # failing) raised `AttributeError` AFTER the claim row was already inserted. The
        # per-recipient guard swallowed it, `mark_state` never ran, and the
        # `(user_id, dedup_key)` pair was permanently burned: that notification could never be
        # retried for that user. A malformed `APNS_AUTH_KEY` PEM is all it takes.
        if not self.enabled:
            logger.info("Push disabled (APNs not configured) — skipping send to %s", user_id)
            return PushOutcome(
                attempted=0, accepted=0, failures=("APNs is not configured on this server",)
            )

        if devices is None:
            # OFF-THREAD. The Supabase SDK is synchronous, and this runs inside the Updates
            # sweeper's async loop — a blocking DB round-trip here stalls the ENTIRE event loop,
            # i.e. every in-flight request in the process, once per recipient of every alert.
            # `push_dispatch_service.py` already wraps all four of its Supabase calls this way;
            # this module was the one that did not. CLAUDE.md invariant #6.
            devices = await asyncio.to_thread(self._device_tokens_for, user_id)
        if not devices:
            # Nothing attempted and nothing wrong — the caller stamps `no_device`, which is a
            # legitimate state, not a failure. No failure string, or every tokenless user
            # would write noise into `last_error`.
            return PushOutcome(attempted=0, accepted=0)

        jwt_token = self._provider_jwt()
        if not jwt_token:
            # THE REACHABLE ONE. All four APNS_* settings are present (or `enabled` would have
            # caught it above) and signing still failed — overwhelmingly a mangled
            # `APNS_AUTH_KEY` PEM, which is what happens when a `.p8` is pasted into an env var
            # instead of piped in. Named explicitly so `last_error` points at the cause rather
            # than at the devices, which are fine.
            return PushOutcome(
                attempted=len(devices),
                accepted=0,
                failures=(
                    "APNs provider token could not be signed — check APNS_AUTH_KEY (PEM "
                    "newlines), APNS_KEY_ID and APNS_TEAM_ID",
                ),
            )

        aps: Dict[str, Any] = {
            # Shortened HERE and nowhere else. The full text is what the ledger stored and
            # what the in-app detail screen shows; only the lock-screen banner is bounded.
            "alert": {"title": title, "body": truncate_for_banner(body)},
            "sound": "default",
        }
        if interruption_level:
            aps["interruption-level"] = interruption_level
        if thread_id:
            aps["thread-id"] = thread_id
        if category:
            aps["category"] = category
        # `badge: 0` is meaningful (it CLEARS the badge), so test for None, not falsiness.
        if badge is not None and badge >= 0:
            aps["badge"] = badge

        payload: Dict[str, Any] = {"aps": aps}
        if data:
            payload.update(data)

        headers = {
            "authorization": f"bearer {jwt_token}",
            "apns-topic": settings.APNS_BUNDLE_ID,
            "apns-push-type": "alert",
        }
        if collapse_id:
            # 64 BYTES, not 64 characters — a multi-byte ticker or company name would
            # otherwise slip past a len() check and 400 the entire send. Truncate on the
            # encoded form and decode back, dropping any partial trailing sequence.
            headers["apns-collapse-id"] = (
                collapse_id.encode("utf-8")[:64].decode("utf-8", errors="ignore")
            )
        # 5 = "power considerations": iOS may batch delivery. Correct for a passive
        # background signal, and materially kinder to battery than waking the device for
        # a 13F filing. 10 = deliver immediately, the default for everything else.
        headers["apns-priority"] = "5" if interruption_level == "passive" else "10"
        if expiration_hours is not None and expiration_hours > 0:
            # An ABSOLUTE UNIX epoch, not a duration — APNs stops retrying at that
            # instant and discards the notification. Computed here rather than in the
            # registry so the deadline is measured from the send, which is what matters
            # for a row that sat in the quiet-hours queue for six hours first.
            #
            # The `> 0` guard is belt-and-braces: `__post_init__` already rejects a
            # non-positive value, and this makes the "0 means omit, never send 0"
            # invariant hold even for a caller that bypasses the registry.
            headers["apns-expiration"] = str(int(time.time() + expiration_hours * 3600))

        accepted = 0
        attempted = 0
        # Per-device outcomes, kept so a PARTIAL delivery reaches the ledger. See PushOutcome.
        failures: List[str] = []

        def _label(device: dict) -> str:
            """Identify a device in an error string WITHOUT logging its token.

            Last six characters plus the environment is enough to tell one registration
            from another when reading `last_error`, and a token is a credential.
            """
            env = (device.get("environment") or settings.APNS_ENV or "?").lower()
            return f"{env} …{str(device.get('token') or '')[-6:]}"

        try:
            async with httpx.AsyncClient(http2=True, timeout=httpx.Timeout(10.0)) as client:
                for device in devices:
                    token = device["token"]
                    attempted += 1
                    host = host_for_environment(device.get("environment"), settings.APNS_ENV)
                    try:
                        resp = await client.post(
                            f"{host}/3/device/{token}",
                            headers=headers,
                            json=payload,
                        )
                    except Exception as e:
                        logger.warning(
                            "APNs POST failed for token …%s (%s: %s)",
                            token[-8:], type(e).__name__, e,
                        )
                        failures.append(f"{_label(device)}: transport {type(e).__name__}")
                        continue
                    if resp.status_code == 200:
                        accepted += 1
                        continue

                    # APNs states the machine-readable cause in `reason`; the raw body is
                    # noise in a 500-char ledger column.
                    try:
                        reason = (resp.json() or {}).get("reason") or ""
                    except Exception:
                        reason = (resp.text or "")[:60]
                    failures.append(f"{_label(device)}: {resp.status_code} {reason}".strip())

                    if resp.status_code == 410:
                        # 410 Unregistered = token is genuinely dead → prune.
                        # NOT on 400/BadDeviceToken: that can be an env/routing issue,
                        # and pruning a valid token would silently stop notifications.
                        # Off-thread for the same reason as the read above.
                        await asyncio.to_thread(self._prune_token, token)
                    elif resp.status_code == 403:
                        # NOT a device problem — a PROVIDER problem, so it is an error, not a
                        # per-token warning. A 403 means the .p8 cannot sign for this host at
                        # all, so it takes out EVERY device in that environment at once while
                        # the other environment keeps succeeding.
                        #
                        # That asymmetry is exactly how this hid: an APNs auth key scoped to
                        # Sandbox only returned 200 for three simulator tokens and
                        # `403 BadEnvironmentKeyInToken` for the one real iPhone, so every
                        # ledger row read `sent` and no TestFlight user ever got a push. It is
                        # a configuration fault with a one-line fix, and it was invisible for
                        # weeks.
                        logger.error(
                            "APNs REFUSED THE PROVIDER KEY for the %s environment "
                            "(HTTP 403 %s). This is not a bad device token — no push can "
                            "reach ANY %s device until the APNS_AUTH_KEY/APNS_KEY_ID pair is "
                            "valid for it. A Sandbox-only key cannot sign for production.",
                            "sandbox" if "sandbox" in host else "production",
                            reason or resp.text[:120],
                            "sandbox" if "sandbox" in host else "production",
                        )
                    else:
                        logger.warning(
                            "APNs rejected token …%s: %s %s",
                            token[-8:], resp.status_code, resp.text[:200],
                        )
        except Exception as e:
            logger.error(
                "Push send to user=%s failed (%s: %s)", user_id, type(e).__name__, e
            )
            failures.append(f"send aborted: {type(e).__name__}")
        return PushOutcome(attempted=attempted, accepted=accepted, failures=tuple(failures))
