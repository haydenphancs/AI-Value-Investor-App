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
    (APNs allows a token age up to 60 min). ONE cache for both senders below:
    APNs refuses a provider token refreshed more often than every 20 minutes
    (TooManyProviderTokenUpdates), so the widget path never mints its own.
  * Prune ONLY tokens APNs reports as 410 (Unregistered). A 400 BadDeviceToken is
    recorded and kept: it can be an environment/routing mistake on our side, and
    pruning a valid token would silently stop notifications (see `send_to_user`).

Two senders live here, and they never share a token store:

  * `send_to_user` — ALERT pushes to `device_tokens` (the app's own APNs token).
  * `send_widget_pushes` — content-free WidgetKit wakes (`apns-push-type: widgets`,
    iOS 26+) to the WidgetKit push tokens stored on `widget_holdings_grants` (the
    holdings-grant migration). It NEVER calls `_prune_token` and never touches
    `device_tokens`: a widget token is a different token for a different topic.
    Who gets a wake, how often, and what is written back is
    `widget_push_service`'s job; this module only delivers and classifies.

Apple prerequisites (out-of-band): APNs Auth Key (.p8) + Key ID, Team ID, the
Push Notifications capability on the App ID (and, for widget wakes, on the WIDGET
extension's App ID), and the `aps-environment` entitlement in the app and the
widget extension. Provide APNS_* via env/secrets — never commit the .p8.
"""

import asyncio
import logging
import re
import time
import uuid
from collections import Counter, deque
from typing import Any, Callable, Deque, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

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


# ── WidgetKit push wakes (iOS 26+) ────────────────────────────────────────────────────
#
# A widget wake carries NO data: WidgetKit reruns the extension's timeline, and the extension
# fetches exactly as it does on its own schedule. Nothing depends on delivery — Apple calls
# these pushes "budgeted" and "an additional update mechanism… that doesn't replace timeline
# updates". So every rule below errs towards NOT sending: a lost wake costs one stale tile until
# the next scheduled reload; a mis-sent burst costs APNs standing and the device's budget.

#: The exact body. Sent as raw bytes (`content=`), never `json=` of a dict someone could grow
#: into a data push: a widget wake must never carry holdings, prices or text.
WIDGET_PUSH_BODY = b'{"aps":{"content-changed":true}}'

#: One request may wait this long. The whole pass is bounded again by the sweeper's budget.
WIDGET_PUSH_TIMEOUT_SECONDS = 5.0
#: Requests in flight at once (HTTP/2 multiplexes them over one connection per host).
WIDGET_PUSH_CONCURRENCY = 10
#: Consecutive retryable answers (429 / 5xx / transport) that stop an environment for the call.
WIDGET_PUSH_CIRCUIT_AFTER = 5
#: When this many of an environment's FIRST devices ALL answer DeviceTokenNotForTopic, the topic
#: is wrong (not the devices), and the environment stops with one ERROR.
WIDGET_PUSH_TOPIC_PROBE = 3

# Outcomes. The first five are exactly the `widget_push_settle` vocabulary; `provider_fault` is
# the sender's own and is settled as `not_sent` (the device did nothing wrong).
WIDGET_ACCEPTED = "accepted"
WIDGET_GONE = "gone"
WIDGET_BAD = "bad"
WIDGET_RETRY = "retry"
WIDGET_NOT_SENT = "not_sent"
WIDGET_PROVIDER_FAULT = "provider_fault"

#: A per-DEVICE 400: that one token is wrong for this topic/host. Never an environment stop on
#: its own — any signed-in account can upload its ordinary alert token (valid, wrong topic), and
#: treating that as a provider fault would let one account stop every wake in an environment.
_WIDGET_DEVICE_REASONS = frozenset({"BadDeviceToken", "DeviceTokenNotForTopic"})
#: A per-REQUEST 400: the same for every device, so the environment stops for the call.
_WIDGET_PROVIDER_REASONS = frozenset(
    {"BadTopic", "TopicDisallowed", "MissingTopic", "InvalidPushType"}
)
#: The `reason` a WidgetPushResult carries for that answer (status code + APNs reason).
_NOT_FOR_TOPIC_REASON = "400 DeviceTokenNotForTopic"
#: A token reaches APNs inside the URL PATH, so it is checked before any formatting: a value with
#: `/`, `?` or `#` would address a different resource. Lower-case hex, even length — the same
#: rule as the holdings-grant migration's CHECK.
_WIDGET_TOKEN_RE = re.compile(r"^[0-9a-f]{32,400}$")
_WIDGET_ENVIRONMENTS = frozenset({"sandbox", "production"})


def widget_push_topic() -> str:
    """`<app bundle id>.push-type.widgets` — always derived from the APP's bundle id
    (`APNS_BUNDLE_ID`), never the extension's (`com.phan.caydex.CaydexWidgets`)."""
    return f"{settings.APNS_BUNDLE_ID}.push-type.widgets"


def widget_token_label(environment: Optional[str], push_token: Any) -> str:
    """A device in a log line: environment + the token's last six characters, never the token."""
    tail = str(push_token or "")[-6:]
    return f"{(environment or '?')} …{tail}"


class WidgetPushResult(NamedTuple):
    """What happened to ONE device. `push_token` is the exact stored value that was sent, so the
    settle can be keyed on (grant_id, push_token) and never clears a token re-registered since."""

    grant_id: str
    push_token: str
    environment: str
    status: str
    reason: str = ""
    #: APNs' 410 `timestamp` (ms since the epoch): the moment APNs learned the token was invalid.
    apns_timestamp_ms: Optional[int] = None


def classify_widget_push_response(status_code: int, reason: str) -> str:
    """Pure: map one APNs answer to an outcome. Anything not named is RETRY — never a token clear
    and never an environment stop on a guess (the per-environment circuit bounds the cost)."""
    if status_code == 200:
        return WIDGET_ACCEPTED
    if status_code == 410:
        return WIDGET_GONE
    if status_code == 403:
        # The provider key cannot sign for this host (a Sandbox-only .p8, a revoked key): every
        # device of the environment would answer the same.
        return WIDGET_PROVIDER_FAULT
    if status_code == 400 and reason in _WIDGET_DEVICE_REASONS:
        return WIDGET_BAD
    if reason in _WIDGET_PROVIDER_REASONS:
        return WIDGET_PROVIDER_FAULT
    return WIDGET_RETRY


def _apns_timestamp_ms(body: Any) -> Optional[int]:
    """APNs' 410 `timestamp` (ms since the epoch), or None. A bool, a fraction, a non-digit
    string or a non-positive value is None — never a made-up time, because the settle compares it
    with the token's registration time to decide whether the token may be cleared."""
    value = body.get("timestamp") if isinstance(body, dict) else None
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


class WidgetPushPacer:
    """Spaces request STARTS at most `per_second` apart, across every worker of one pass.

    A slot is reserved synchronously (no await between the read and the write), so concurrent
    workers can never take the same slot; each then sleeps until its own slot. `clock` and
    `sleep` are injectable so a test can read the reserved slots without waiting for them.
    """

    def __init__(
        self,
        per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.interval = 1.0 / max(float(per_second), 1.0)
        self._clock = clock
        self._sleep = sleep
        self._next: Optional[float] = None

    async def wait(self) -> None:
        now = self._clock()
        if self._next is None or self._next < now:
            self._next = now
        delay = self._next - now
        self._next += self.interval
        if delay > 0:
            await self._sleep(delay)


class _QueuedWidgetPush(NamedTuple):
    """One validated device waiting for a worker. A NamedTuple (never a bare tuple) so a repr in a
    Sentry frame reads `push_token='…'`, which the frame scrub redacts."""

    index: int
    grant_id: str
    push_token: str
    environment: str


class _WidgetEnvState:
    """Per-environment bookkeeping for ONE `send_widget_pushes` call."""

    __slots__ = ("stopped", "consecutive_retries", "retry_reasons", "probe", "probe_hits",
                 "probe_done", "topic_fault", "held")

    def __init__(self) -> None:
        self.stopped: Optional[str] = None
        self.consecutive_retries = 0
        self.retry_reasons: List[str] = []
        self.probe: List[int] = []            # indices of the first WIDGET_PUSH_TOPIC_PROBE dispatched
        self.probe_hits: Dict[int, bool] = {}  # index → answered DeviceTokenNotForTopic
        self.probe_done = False
        self.topic_fault = False
        # DeviceTokenNotForTopic answers held back until the probe decides whether they are the
        # device's fault (bad) or the topic's (provider_fault). Never settled twice.
        self.held: List[Tuple[int, "WidgetPushResult"]] = []


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

    # ── WidgetKit push wakes ──────────────────────────────────────────────────────────

    async def send_widget_pushes(
        self,
        devices: Sequence[Mapping[str, Any]],
        *,
        client: Optional[httpx.AsyncClient] = None,
        pacer: Optional[WidgetPushPacer] = None,
        on_result: Optional[Callable[[WidgetPushResult], None]] = None,
    ) -> List[WidgetPushResult]:
        """Send one content-free WidgetKit wake to each device; never raises (except on
        cancellation, which propagates).

        `devices` are `{grant_id, push_token, push_environment}` mappings (the
        `widget_push_targets` row shape). The answer is one `WidgetPushResult` per device, in
        input order. `on_result` is called once per device as its outcome becomes FINAL, so the
        caller can settle progressively — a pass cut short by its time budget still records
        what it sent.

        Headers: `apns-push-type: widgets`, `apns-topic: <APNS_BUNDLE_ID>.push-type.widgets`,
        `apns-expiration: 0` (attempt once, never stored — a stale wake is worthless and the
        timeline covers a lost one). NO `apns-priority` (APNs' default, as in Apple's sample)
        and no collapse id. Each token goes to ITS OWN environment's host.

        Outcomes (`classify_widget_push_response`):
          * 200 → accepted; 410 → gone (with APNs' timestamp, for the re-registration fence);
          * 400 BadDeviceToken / DeviceTokenNotForTopic → bad, for THAT device only;
          * 403, BadTopic, TopicDisallowed, MissingTopic, InvalidPushType → provider_fault: one
            ERROR, and the rest of that environment is not_sent for this call. The same stop
            applies when the environment's first `WIDGET_PUSH_TOPIC_PROBE` devices ALL answer
            DeviceTokenNotForTopic (the topic is wrong, not the devices) — those answers are then
            provider_fault, never bad, so no valid token is ever cleared for our mistake;
          * 429, 5xx, transport errors, anything unnamed → retry; after
            `WIDGET_PUSH_CIRCUIT_AFTER` in a row the environment's rest is not_sent.

        Never calls `_prune_token`: widget tokens live on the grant row, not in `device_tokens`.
        A token is never logged in full (`widget_token_label`).
        """
        count = len(devices)
        results: List[Optional[WidgetPushResult]] = [None] * count

        def _emit(index: int, result: WidgetPushResult) -> None:
            results[index] = result
            if on_result is None:
                return
            try:
                on_result(result)
            except Exception as e:  # the caller's bookkeeping must never stop the sends
                logger.warning(
                    "widget push: result callback raised for grant=%s (%s: %s)",
                    result.grant_id, type(e).__name__, e,
                )

        # 1. Validate every device BEFORE anything is formatted into a URL.
        queue: Deque[_QueuedWidgetPush] = deque()
        seen_tokens: set = set()
        for index, device in enumerate(devices):
            raw = device if isinstance(device, Mapping) else {}
            grant_id = str(raw.get("grant_id") or "")
            push_token = raw.get("push_token")
            environment = raw.get("push_environment")
            token_str = push_token if isinstance(push_token, str) else ""
            env_str = environment if isinstance(environment, str) else ""
            problem = None
            if not grant_id:
                problem = "invalid_device: no grant_id"
            elif not token_str or not _WIDGET_TOKEN_RE.match(token_str) or len(token_str) % 2:
                problem = "invalid_device: token is not lower-case even-length hex"
            elif env_str not in _WIDGET_ENVIRONMENTS:
                problem = "invalid_device: unknown environment"
            elif token_str in seen_tokens:
                problem = "invalid_device: duplicate token in one call"
            if problem:
                logger.warning(
                    "widget push: skipping grant=%s %s (%s)",
                    grant_id or "?", widget_token_label(env_str or None, token_str), problem,
                )
                _emit(index, WidgetPushResult(grant_id, token_str, env_str, WIDGET_NOT_SENT,
                                              problem))
                continue
            seen_tokens.add(token_str)
            queue.append(_QueuedWidgetPush(index, grant_id, token_str, env_str))

        if not queue:
            return [r for r in results if r is not None]

        # 2. Nothing leaves without a signed provider token. `_provider_jwt` already logs the
        #    ERROR naming the cause; every device is not_sent, with zero HTTP.
        jwt_token = self._provider_jwt() if self.enabled else None
        if not jwt_token:
            reason = "provider_jwt_failed" if self.enabled else "apns_unconfigured"
            for index, grant_id, token_str, env_str in queue:
                _emit(index, WidgetPushResult(grant_id, token_str, env_str, WIDGET_NOT_SENT,
                                              reason))
            return [r for r in results if r is not None]

        topic = widget_push_topic()
        if pacer is None:
            pacer = WidgetPushPacer(max(1, int(settings.WIDGET_PUSH_SENDS_PER_SECOND)))
        states: Dict[str, _WidgetEnvState] = {env: _WidgetEnvState() for env in _WIDGET_ENVIRONMENTS}

        def _release_held(state: _WidgetEnvState, env: str) -> None:
            for held_index, held in state.held:
                if state.topic_fault:
                    held = held._replace(status=WIDGET_PROVIDER_FAULT,
                                         reason=f"topic_fault: {held.reason}")
                _emit(held_index, held)
            state.held = []

        def _settle_probe(state: _WidgetEnvState, env: str) -> None:
            """Decide the topic probe once all of its devices have answered (or one did not
            answer DeviceTokenNotForTopic, which settles it as 'the topic is fine')."""
            if state.probe_done:
                return
            if any(state.probe_hits.get(i) is False for i in state.probe):
                state.probe_done = True
            elif (len(state.probe) >= WIDGET_PUSH_TOPIC_PROBE
                  and all(state.probe_hits.get(i) is True for i in state.probe)):
                state.probe_done = True
                state.topic_fault = True
                if state.stopped is None:
                    state.stopped = "topic"
                logger.error(
                    "APNs: the first %d %s devices ALL answered DeviceTokenNotForTopic for the "
                    "WIDGET topic %s — the topic or the widget extension's push capability is "
                    "wrong, not the devices. No widget wake reaches ANY %s device until it is "
                    "fixed (Apple portal: Push Notifications on the widget App ID; topic = the "
                    "APP bundle id + '.push-type.widgets').",
                    WIDGET_PUSH_TOPIC_PROBE, env, topic, env,
                )
            if state.probe_done:
                _release_held(state, env)

        def _record(index: int, env: str, result: WidgetPushResult) -> None:
            state = states[env]
            is_topic_answer = (result.status == WIDGET_BAD
                               and result.reason == _NOT_FOR_TOPIC_REASON)
            if index in state.probe:
                state.probe_hits[index] = is_topic_answer
            if result.status == WIDGET_RETRY:
                state.consecutive_retries += 1
                state.retry_reasons = (state.retry_reasons + [result.reason])[
                    -WIDGET_PUSH_CIRCUIT_AFTER:]
                if (state.consecutive_retries >= WIDGET_PUSH_CIRCUIT_AFTER
                        and state.stopped is None):
                    state.stopped = "circuit"
                    recent = state.retry_reasons[-WIDGET_PUSH_CIRCUIT_AFTER:]
                    # A run of plain 4xx answers is OUR request's fault — louder than an outage.
                    ours = any(r.startswith("4") and not r.startswith("429") for r in recent)
                    (logger.error if ours else logger.warning)(
                        "widget push: %d retryable answers in a row from the %s host (%s) — "
                        "the rest of %s is skipped this pass and stays pending",
                        len(recent), env, ", ".join(recent), env,
                    )
            elif result.status in (WIDGET_ACCEPTED, WIDGET_GONE, WIDGET_BAD):
                state.consecutive_retries = 0
            if is_topic_answer and (state.topic_fault or not state.probe_done):
                if state.topic_fault:
                    result = result._replace(status=WIDGET_PROVIDER_FAULT,
                                             reason=f"topic_fault: {result.reason}")
                    _emit(index, result)
                else:
                    state.held.append((index, result))
                _settle_probe(state, env)
                return
            _emit(index, result)
            _settle_probe(state, env)

        async def _send_one(http: httpx.AsyncClient, index: int, grant_id: str,
                            push_token: str, env: str) -> None:
            label = widget_token_label(env, push_token)
            apns_id = str(uuid.uuid4())
            headers = {
                "authorization": f"bearer {jwt_token}",
                "apns-topic": topic,
                "apns-push-type": "widgets",
                "apns-expiration": "0",
                "apns-id": apns_id,
            }
            host = host_for_environment(env, settings.APNS_ENV)
            try:
                resp = await http.post(
                    f"{host}/3/device/{push_token}",
                    headers=headers,
                    content=WIDGET_PUSH_BODY,
                    timeout=httpx.Timeout(WIDGET_PUSH_TIMEOUT_SECONDS),
                )
            except Exception as e:  # transport / timeout: retryable, never the device's fault
                logger.info("widget push: transport %s for grant=%s %s apns-id=%s",
                            type(e).__name__, grant_id, label, apns_id)
                _record(index, env, WidgetPushResult(grant_id, push_token, env, WIDGET_RETRY,
                                                     f"transport {type(e).__name__}"))
                return
            body: Any = None
            reason = ""
            if resp.status_code != 200:
                try:
                    body = resp.json()
                except Exception:
                    body = None
                if isinstance(body, dict) and isinstance(body.get("reason"), str):
                    reason = body["reason"][:60]
            status = classify_widget_push_response(resp.status_code, reason)
            stamp = _apns_timestamp_ms(body) if status == WIDGET_GONE else None
            if status == WIDGET_PROVIDER_FAULT:
                state = states[env]
                if state.stopped is None:
                    state.stopped = "provider"
                    logger.error(
                        "APNs REFUSED a WIDGET push for the %s environment (HTTP %d %s, topic "
                        "%s, apns-id=%s). This is not a device problem: no widget wake reaches "
                        "ANY %s device until the provider key / topic / push type is fixed. A "
                        "Sandbox-only key cannot sign for production; the widget App ID needs "
                        "the Push Notifications capability.",
                        env, resp.status_code, reason or "-", topic, apns_id, env,
                    )
            elif status in (WIDGET_GONE, WIDGET_BAD):
                logger.info("widget push: %s for grant=%s %s (HTTP %d %s)",
                            status, grant_id, label, resp.status_code, reason or "-")
            elif status == WIDGET_RETRY:
                logger.info("widget push: retryable HTTP %d %s for grant=%s %s apns-id=%s",
                            resp.status_code, reason or "-", grant_id, label, apns_id)
            _record(index, env, WidgetPushResult(
                grant_id, push_token, env, status,
                f"{resp.status_code} {reason}".strip() if status != WIDGET_ACCEPTED else "",
                stamp,
            ))

        async def _worker(http: httpx.AsyncClient) -> None:
            while queue:
                index, grant_id, push_token, env = queue.popleft()
                state = states[env]
                if state.stopped is None:
                    await pacer.wait()
                if state.stopped is not None:  # re-read: it may have stopped during the wait
                    _emit(index, WidgetPushResult(grant_id, push_token, env, WIDGET_NOT_SENT,
                                                  f"environment_stopped: {state.stopped}"))
                    continue
                if len(state.probe) < WIDGET_PUSH_TOPIC_PROBE:
                    state.probe.append(index)
                await _send_one(http, index, grant_id, push_token, env)

        async def _run(http: httpx.AsyncClient) -> None:
            workers = min(WIDGET_PUSH_CONCURRENCY, len(queue))
            await asyncio.gather(*[_worker(http) for _ in range(workers)])

        if client is not None:
            await _run(client)
        else:
            async with httpx.AsyncClient(
                http2=True, timeout=httpx.Timeout(WIDGET_PUSH_TIMEOUT_SECONDS)
            ) as http:
                await _run(http)

        # A probe that never filled (fewer devices than the probe in an environment) decides
        # nothing: its held answers are what they said — per-device bad.
        for env, state in states.items():
            state.probe_done = True
            _release_held(state, env)

        final = [r for r in results if r is not None]
        tally: Counter = Counter((r.environment or "?", r.status) for r in final)
        logger.info(
            "widget push sent: devices=%d %s",
            count, " ".join(f"{env}:{status}={n}" for (env, status), n in sorted(tally.items())),
        )
        return final
