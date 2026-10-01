"""
Bluesky adapter for the marketing publisher (design doc §12.10, rules/marketing.md §1-§2).

Text posts to the brand account (`MARKETING_BLUESKY_HANDLE`, an APP password) through
`app/integrations/bluesky.py`. Bluesky is the one outlet with a true exactly-once publish, and this
adapter is built around it (verified against the atproto lexicons and the reference PDS, 2026-09-30):

* The record key is a TID chosen BY US — deterministic from the post's idempotency key and a salt —
  and the full record (text, createdAt, facets) is built ONCE and stored in the claim's write-ahead
  (`metadata.publish.bluesky`). Every later attempt resends those exact bytes.
* It is written with `putRecord` and `"swapRecord": null` ("only if nothing is at this key"): an
  identical retry is a 200 no-op; a different record at that key is a 400 `InvalidSwap`.
* After an ambiguous call, `getRecord` (no auth) answers it: found = published; `RecordNotFound` =
  nothing was written, and the stored record may be sent again with no risk of a second copy.

Links are clickable only through a link FACET with UTF-8 BYTE offsets (Bluesky does not detect
them); the caption's only link is the code-owned `/go/bluesky` smart link, and any other URL is
refused. Bluesky has no AI-content flag or self-label for posts: the caption's disclaimer
("AI-assisted") is the disclosure (rules/marketing.md §1).

Sessions: createSession is limited to 30 per 5 minutes and 300 per day per account, so the session
is kept in memory, refreshed once on `ExpiredToken`, logins are capped locally, and a refused login
opens an hour-long circuit (a wrong app password must not burn the daily allowance).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Deque, Dict, Optional, Tuple, Union

from app.config import settings
from app.integrations import bluesky
from app.services.marketing import post_copy
from app.services.marketing.outlet_base import (
    ABSENT,
    AMBIGUOUS,
    AUTH_BACKOFF_SECONDS,
    FOUND,
    GAVE_UP,
    NOT_SENT,
    PUBLISHED,
    REFUSED,
    RETRACTED,
    RETRY,
    UNKNOWN,
    Adapter,
    MarketingPublishRefused,
    Outcome,
    Prepared,
    ReconcileResult,
    RetractResult,
    scrub,
    text_sha256,
)
from app.services.marketing.run_service import post_run_date

logger = logging.getLogger(__name__)

PLATFORM = "bluesky"
#: Lexicon limits for app.bsky.feed.post text.
MAX_GRAPHEMES = 300
MAX_TEXT_BYTES = 3000
#: Refresh a cached session before the reference PDS's 120-minute access-token lifetime ends.
SESSION_MAX_AGE_SECONDS = 100 * 60
#: Local cap on createSession calls (Bluesky: 30 per 5 min, 300 per day). Normal use is one per
#: ~100 minutes of activity; anything near this cap is a loop, and it stops here.
LOGIN_CAP_PER_HOUR = 6

_S32 = "234567abcdefghijklmnopqrstuvwxyz"
TID_RE = re.compile(r"[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}")
_URL_RE = re.compile(r"https?://\S+")
_URL_TRAILING = ".,;:!?)]}\"'"

# ── module state (one web process; the publisher is sequential) ────────────────────────────────
_session: Optional[Dict[str, Any]] = None
_session_at = 0.0
_login_times: Deque[float] = deque()
_blocked_until = 0.0
_blocked_reason = ""


def _reset_state() -> None:
    """Forget the session and every back-off (tests; a credential change needs a restart anyway)."""
    global _session, _session_at, _blocked_until, _blocked_reason
    _session, _session_at, _blocked_until, _blocked_reason = None, 0.0, 0.0, ""
    _login_times.clear()


# ── pure helpers ────────────────────────────────────────────────────────────────────────────────


def tid_for(key: str, salt: int, run_day: date) -> str:
    """A valid atproto TID (13 base32-sortable chars: a zero bit, 53 bits of microseconds since the
    epoch, 10 bits of clock id), DETERMINISTIC from (idempotency key, salt): the microseconds fall
    inside `run_day` (UTC) and the clock id comes from the same hash. TIDs are not checked against
    the clock anywhere in the network; a fixed key is what lets every retry name the same record."""
    digest = hashlib.sha256(f"{key}|{int(salt)}".encode("utf-8")).digest()
    day_start = int(datetime(run_day.year, run_day.month, run_day.day, tzinfo=timezone.utc).timestamp())
    micros = day_start * 1_000_000 + int.from_bytes(digest[:8], "big") % 86_400_000_000
    clock_id = int.from_bytes(digest[8:10], "big") % 1024
    value = (micros << 10) | clock_id
    chars = []
    for _ in range(13):
        chars.append(_S32[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def link_facets(text: str) -> list:
    """`app.bsky.richtext.facet#link` for every URL in `text`, with UTF-8 BYTE offsets (start
    inclusive, end exclusive). Only the code-owned smart link may appear; anything else is refused —
    a link the validators did not author must not become clickable."""
    facets = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_URL_TRAILING)
        if not url.startswith(post_copy.LINK_BASE_URL + "/"):
            raise MarketingPublishRefused(f"bluesky: a link that is not the code-owned smart link ({url[:60]!r})")
        start = len(text[: match.start()].encode("utf-8"))
        facets.append({
            "index": {"byteStart": start, "byteEnd": start + len(url.encode("utf-8"))},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        })
    return facets


def build_record(text: str, created_at: datetime) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "$type": bluesky.POST_COLLECTION,
        "text": text,
        "createdAt": created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") +
        f"{created_at.astimezone(timezone.utc).microsecond // 1000:03d}Z",
        "langs": ["en"],
    }
    facets = link_facets(text)
    if facets:
        record["facets"] = facets
    return record


def post_url_for(repo: Any, rkey: Any) -> Optional[str]:
    return f"https://bsky.app/profile/{repo}/post/{rkey}" if repo and rkey else None


def _bluesky_meta(post: Dict[str, Any]) -> Dict[str, Any]:
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    pub = meta.get("publish") if isinstance(meta.get("publish"), dict) else {}
    bsky = pub.get("bluesky")
    return bsky if isinstance(bsky, dict) else {}


#: Salts are tiny counters (one per rkey-validation refusal); anything else is corrupt metadata.
_MAX_SALT = 1_000


def _salt(value: Any) -> int:
    """The stored salt as an int, refusing anything that is not a small non-negative integer (a
    hand-edited or corrupted row must be refused for THIS post, never crash the publish step)."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_SALT:
        raise MarketingPublishRefused(f"bluesky: unreadable record-key salt {str(value)[:20]!r}")
    return value


def _rkey_from_uri(uri: Any) -> Optional[str]:
    parts = str(uri or "").rsplit("/", 1)
    return parts[1] if len(parts) == 2 and TID_RE.fullmatch(parts[1]) else None


# ── sessions ────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _NoSession:
    """No usable session this cycle, and why — RETURNED (not raised) by the session helpers, so
    nothing here is an exception class the marketing exception walk would have to classify. A
    session failure is never a publish: no record was written."""

    category: str
    error: str
    retry_at: Optional[datetime] = None
    alert: Optional[str] = None


_Session = Union[Dict[str, Any], _NoSession]


def _block(seconds: float, reason: str) -> None:
    global _blocked_until, _blocked_reason
    _blocked_until = max(_blocked_until, time.monotonic() + max(seconds, 0.0))
    _blocked_reason = reason


async def _login() -> _Session:
    global _session, _session_at
    now = time.monotonic()
    while _login_times and now - _login_times[0] > 3600:
        _login_times.popleft()
    if len(_login_times) >= LOGIN_CAP_PER_HOUR:
        return _NoSession("rate_limited", f"bluesky: {LOGIN_CAP_PER_HOUR} logins in the last hour — waiting",
                          retry_at=datetime.now(timezone.utc) + timedelta(minutes=30))
    _login_times.append(now)
    try:
        session = await bluesky.create_session()
    except bluesky.BlueskyRateLimitError as e:
        retry_at = getattr(e, "retry_at", None) or datetime.now(timezone.utc) + timedelta(minutes=15)
        _block((retry_at - datetime.now(timezone.utc)).total_seconds(), "createSession rate limited")
        return _NoSession("rate_limited", scrub(e), retry_at=retry_at)
    except (bluesky.BlueskyAuthError, bluesky.BlueskyNotConfiguredError, bluesky.BlueskyRefusedError) as e:
        # A wrong / revoked app password: stop trying for an hour (each try counts against 300/day).
        _block(AUTH_BACKOFF_SECONDS, "login refused")
        return _NoSession("auth", scrub(e), retry_at=datetime.now(timezone.utc) + timedelta(seconds=AUTH_BACKOFF_SECONDS),
                          alert="auth")
    except bluesky.BlueskyException as e:
        # No post was attempted, whatever the transport did: a login is never a publish.
        return _NoSession("transport", scrub(e))
    _session, _session_at = session, time.monotonic()
    logger.info("marketing bluesky: session created did=%s pds=%s", session.get("did"), session.get("pds"))
    return session


async def _get_session(*, renew: bool = False) -> _Session:
    """The cached session, refreshed (or re-created) when old or when `renew` (after ExpiredToken),
    or a `_NoSession` saying why there is none this cycle."""
    global _session, _session_at
    if time.monotonic() < _blocked_until:
        return _NoSession("rate_limited" if "rate" in _blocked_reason else "auth",
                          f"bluesky: paused ({_blocked_reason})")
    fresh = _session is not None and time.monotonic() - _session_at < SESSION_MAX_AGE_SECONDS
    if fresh and not renew:
        return _session  # type: ignore[return-value]
    if _session is not None and _session.get("refresh_jwt"):
        try:
            refreshed = await bluesky.refresh_session(str(_session["refresh_jwt"]))
            prev_pds = _session.get("pds")
            if prev_pds and (not refreshed.get("pds") or refreshed.get("pds") == bluesky.service_url()):
                # A refresh answer without a DID document falls back to the ENTRYWAY URL: keep the
                # account's own PDS learned at login — reconcile must ask it, never a mirror.
                refreshed["pds"] = prev_pds
            _session, _session_at = refreshed, time.monotonic()
            return refreshed
        except (bluesky.BlueskyExpiredTokenError, bluesky.BlueskyAuthError, bluesky.BlueskyRefusedError):
            _session = None  # the refresh token is gone too — log in again
        except bluesky.BlueskyException as e:
            return _NoSession("transport", scrub(e))
    return await _login()


# ── the adapter ─────────────────────────────────────────────────────────────────────────────────


class BlueskyAdapter(Adapter):
    platform = PLATFORM
    retractable = True
    resend_safe = True
    #: Seconds after the send's start: getRecord answers definitively, so these are only retries of
    #: a getRecord that itself failed; after six hours of no answer the owner decides.
    reconcile_schedule = (600, 1800, 5400, 21600)

    def configured(self) -> bool:
        return bluesky.configured()

    def available(self) -> bool:
        return time.monotonic() >= _blocked_until

    def prepare(self, post: Dict[str, Any]) -> Prepared:
        text = post.get("caption") if isinstance(post.get("caption"), str) else ""
        if not text.strip():
            raise MarketingPublishRefused("bluesky: the post has no text")
        if post.get("format") != "text":
            raise MarketingPublishRefused(f"bluesky: format {post.get('format')!r} is not published on Bluesky (text only)")
        if post.get("asset_ids"):
            raise MarketingPublishRefused("bluesky: a text post may not carry media")
        if len(text) > MAX_GRAPHEMES or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            # Code points ≥ graphemes, so this is conservative (post_copy counts the same way).
            raise MarketingPublishRefused(f"bluesky: {len(text)} characters > {MAX_GRAPHEMES}")
        sha = text_sha256(text)
        stored = _bluesky_meta(post)
        if (isinstance(stored.get("record"), dict) and stored.get("rkey") and stored.get("text_sha256") == sha
                and TID_RE.fullmatch(str(stored["rkey"]))):
            # A retry: the exact record and key of the first attempt (an identical putRecord is a no-op).
            rkey, record, salt = str(stored["rkey"]), stored["record"], _salt(stored.get("salt"))
        else:
            run_day = post_run_date(post)
            if run_day is None:
                raise MarketingPublishRefused("bluesky: the post's idempotency key carries no run date")
            salt = _salt(stored.get("salt"))
            rkey = tid_for(str(post.get("idempotency_key")), salt, run_day)
            record = build_record(text, datetime.now(timezone.utc))
        facets = record.get("facets") or []
        return Prepared(
            payload={"rkey": rkey, "record": record},
            text_sha256=sha,
            reserve_micros=0,
            publish_meta={"bluesky": {"rkey": rkey, "record": record, "salt": salt, "text_sha256": sha}},
            summary=f"bluesky rkey={rkey} chars={len(text)} link_facets={len(facets)} sha256={sha[:12]}",
        )

    async def _put(self, prepared: Prepared) -> Tuple[_Session, Dict[str, Any]]:
        """putRecord with one renewal on ExpiredToken (nothing is written on that error). Returns
        (_NoSession, {}) when no session could be had."""
        session = await _get_session()
        for attempt in range(2):
            if isinstance(session, _NoSession):
                return session, {}
            try:
                res = await bluesky.put_record(
                    str(session["pds"]), str(session["access_jwt"]), repo=str(session["did"]),
                    collection=bluesky.POST_COLLECTION, rkey=prepared.payload["rkey"],
                    record=prepared.payload["record"])
                return session, res
            except bluesky.BlueskyExpiredTokenError:
                if attempt:
                    raise
                session = await _get_session(renew=True)
        raise AssertionError("unreachable")  # pragma: no cover

    async def send(self, post: Dict[str, Any], prepared: Prepared) -> Outcome:
        rkey = prepared.payload["rkey"]
        # Where the put went (the session's account and PDS), recorded with an AMBIGUOUS outcome so
        # a reconcile after a restart asks THAT server, never a mirror.
        where: Dict[str, Any] = {}
        if isinstance(_session, dict) and _session.get("did") and _session.get("pds"):
            where = {"repo": _session["did"], "pds": _session["pds"]}

        def ambiguous(category: str, error: str) -> Outcome:
            loc = dict(where)
            if isinstance(_session, dict) and _session.get("did") and _session.get("pds"):
                loc = {"repo": _session["did"], "pds": _session["pds"]}
            meta = {"bluesky": {**prepared.publish_meta["bluesky"], **loc}} if loc else {}
            return Outcome(AMBIGUOUS, category, error=error, publish_meta=meta)

        try:
            session, res = await self._put(prepared)
        except bluesky.BlueskyNotSentError as e:
            return Outcome(NOT_SENT, "transport", error=scrub(e))
        except bluesky.BlueskyRateLimitError as e:
            retry_at = getattr(e, "retry_at", None)
            if retry_at is not None:
                _block((retry_at - datetime.now(timezone.utc)).total_seconds(), "rate limited")
            return Outcome(NOT_SENT, "rate_limited", error=scrub(e), retry_at=retry_at)
        except bluesky.BlueskyInvalidSwapError as e:
            # Something is already at OUR key: reconcile reads it (most likely our own earlier write).
            return ambiguous("duplicate", scrub(e))
        except bluesky.BlueskyAmbiguousError as e:
            return ambiguous("server", scrub(e))
        except bluesky.BlueskyExpiredTokenError as e:
            return Outcome(NOT_SENT, "auth", error=scrub(e))
        except bluesky.BlueskyAuthError as e:
            _block(AUTH_BACKOFF_SECONDS, "auth refused")
            return Outcome(NOT_SENT, "auth", error=scrub(e),
                           retry_at=datetime.now(timezone.utc) + timedelta(seconds=AUTH_BACKOFF_SECONDS), alert="auth")
        except bluesky.BlueskyRefusedError as e:
            detail = f"{getattr(e, 'error', '')} {getattr(e, 'detail', '')} {e}".lower()
            if "rkey" in detail or "record key" in detail:
                # Validation runs before any write: re-mint the key (salt + 1) and try again.
                salt = int(prepared.publish_meta["bluesky"].get("salt") or 0)
                return Outcome(NOT_SENT, "invalid", error=scrub(e), publish_meta={"bluesky": {"salt": salt + 1}})
            return Outcome(REFUSED, "invalid", error=scrub(e), alert="failed")
        except bluesky.BlueskyException as e:
            return ambiguous("server", scrub(e))
        if isinstance(session, _NoSession):
            return Outcome(NOT_SENT, session.category, error=session.error, retry_at=session.retry_at,
                           alert=session.alert)
        did = str(session.get("did") or "")
        uri = str(res.get("uri") or f"at://{did}/{bluesky.POST_COLLECTION}/{rkey}")
        return Outcome(
            PUBLISHED, external_id=uri, external_url=post_url_for(did, rkey),
            published_at=datetime.now(timezone.utc).isoformat(),
            publish_meta={"bluesky": {**prepared.publish_meta["bluesky"], "uri": uri, "cid": res.get("cid"),
                                      "repo": did, "pds": session.get("pds")}},
        )

    def _where(self, post: Dict[str, Any]) -> Tuple[Optional[str], str, Optional[str]]:
        """(repo, host, rkey) for a post: what the write-ahead / the publish recorded, else the
        configured handle on the entryway (which forwards getRecord)."""
        stored = _bluesky_meta(post)
        repo = stored.get("repo") or (_session or {}).get("did") or bluesky.handle() or None
        host = str(stored.get("pds") or (_session or {}).get("pds") or bluesky.service_url())
        rkey = stored.get("rkey") or _rkey_from_uri(post.get("external_id"))
        return (str(repo) if repo else None), host, (str(rkey) if rkey else None)

    async def reconcile(self, post: Dict[str, Any]) -> ReconcileResult:
        stored = _bluesky_meta(post)
        repo, host, rkey = self._where(post)
        if not stored.get("pds"):
            # No recorded PDS (a crash right after the claim, an older row): log in to learn the
            # account's OWN PDS. An absent answer from anything else (an entryway or AppView mirror)
            # is not proof — it could close a live post as absent, or license a resend on a lag.
            session = await _get_session()
            if isinstance(session, _NoSession):
                return ReconcileResult(UNKNOWN, error=f"bluesky: no session to find the account's PDS ({session.error})")
            repo, host = str(session.get("did") or repo or ""), str(session.get("pds") or "")
            if not host:
                return ReconcileResult(UNKNOWN, error="bluesky: the session names no PDS")
        if not repo or not rkey:
            return ReconcileResult(UNKNOWN, error="bluesky: no repo or record key recorded for this post")
        try:
            found = await bluesky.get_record(host, repo=repo, collection=bluesky.POST_COLLECTION, rkey=rkey)
        except bluesky.BlueskyException as e:
            return ReconcileResult(UNKNOWN, error=scrub(e))
        if found is None:
            return ReconcileResult(ABSENT, resend_safe=True)
        value = found.get("value") if isinstance(found.get("value"), dict) else {}
        if value.get("text") != post.get("caption"):
            logger.warning("marketing bluesky reconcile: post_id=%s rkey=%s holds DIFFERENT text than the "
                           "caption — recorded as published; check it by hand", post.get("id"), rkey)
        uri = str(found.get("uri") or f"at://{repo}/{bluesky.POST_COLLECTION}/{rkey}")
        did = uri.split("/")[2] if uri.startswith("at://") and len(uri.split("/")) > 2 else repo
        return ReconcileResult(FOUND, external_id=uri, external_url=post_url_for(did, rkey),
                               publish_meta={"bluesky": {**_bluesky_meta(post), "uri": uri,
                                                         "cid": found.get("cid"), "repo": did}})

    async def retract(self, post: Dict[str, Any]) -> RetractResult:
        _repo, _host, rkey = self._where(post)
        if not rkey:
            return RetractResult(GAVE_UP, error="bluesky: the record key is unknown — remove it by hand")
        stored = _bluesky_meta(post)
        uri = str(post.get("external_id") or "")
        recorded_repo = stored.get("repo") or (uri.split("/")[2] if uri.startswith("at://") and uri.count("/") >= 4 else None)
        try:
            session = await _get_session()
            for attempt in range(2):
                if isinstance(session, _NoSession):
                    return RetractResult(RETRY, error=session.error)
                if recorded_repo and str(recorded_repo) != str(session.get("did")):
                    # Posted from another account than the one configured now: deleteRecord answers 200
                    # for an absent record, so deleting "here" would report a takedown that did not happen.
                    return RetractResult(GAVE_UP, error=f"bluesky: posted from another account ({recorded_repo}) "
                                                        "— remove it by hand")
                try:
                    await bluesky.delete_record(str(session["pds"]), str(session["access_jwt"]),
                                                repo=str(session["did"]), collection=bluesky.POST_COLLECTION,
                                                rkey=rkey)
                    break
                except bluesky.BlueskyExpiredTokenError:
                    if attempt:
                        raise
                    session = await _get_session(renew=True)
        except (bluesky.BlueskyNotSentError, bluesky.BlueskyAmbiguousError, bluesky.BlueskyRateLimitError,
                bluesky.BlueskyExpiredTokenError, bluesky.BlueskyAuthError) as e:
            return RetractResult(RETRY, error=scrub(e))
        except bluesky.BlueskyRefusedError as e:
            return RetractResult(GAVE_UP, error=scrub(e))
        except bluesky.BlueskyException as e:
            return RetractResult(RETRY, error=scrub(e))
        return RetractResult(RETRACTED)

    def post_url(self, post: Dict[str, Any]) -> Optional[str]:
        if post.get("external_url"):
            return str(post["external_url"])
        repo, _host, rkey = self._where(post)
        return post_url_for(repo, rkey)


ADAPTER = BlueskyAdapter()
