"""The calling app's marketing version (`X-App-Version`), for behaviour an OLD build must not get.

iOS sends `X-App-Version: <CFBundleShortVersionString>` on every request
(`APIClient.buildRequest`). Builds that share a marketing version send the same value — 1.0 (10)
and 1.0 (11) both say "1.0" — so this tells VERSIONS apart, never builds. That is exactly the
unit an App Store release changes.

The value is request-scoped: `capture_client_app_version`, a router-level dependency, stores it
in a ContextVar. A dependency runs in the request's own task, so the endpoint, a streaming body
generator and every task or thread they start all read the same value, and the next request
starts again from the default.

`client_is_older_than` fails OPEN on absence. No header, or one that does not parse, is not an old
iOS build (a test, a script, a future web client), so only a caller that SENT a parseable version
below the minimum counts as older. Use it to WITHHOLD something from old builds.

`client_is_at_least` fails CLOSED: no header, an unparseable one, or an unreadable minimum is
False. Use it to GRANT something only a caller that proves its version can handle — a payload
whose correct use needs code an old build lacks (the widget's web-only cause, 2026-11-12: an
old build cannot link the card that holds the cause's sources). The widget extension's own
market fetch sent no `X-App-Version` before that release, so it is held by construction.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
import logging
from typing import Optional, Tuple, Union

from fastapi import Header

logger = logging.getLogger(__name__)

AppVersion = Tuple[int, int, int]

_MAX_LEN = 32
_VERSION_RE = re.compile(r"(\d{1,4})(?:\.(\d{1,4}))?(?:\.(\d{1,4}))?")
_client_app_version: ContextVar[Optional[str]] = ContextVar("client_app_version", default=None)


def parse_app_version(raw: object) -> Optional[AppVersion]:
    """`"1.1"` → (1, 1, 0); `"1.0.2"` → (1, 0, 2). Anything else → None: not a string, empty,
    over 32 characters, a build suffix (`"1.0 (10)"`), a prefix (`"v1"`) or a fourth part."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > _MAX_LEN:
        return None
    match = _VERSION_RE.fullmatch(text)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor or 0), int(patch or 0))


def set_client_app_version(raw: Optional[str]) -> None:
    """Record this request's raw header (an over-long value is dropped, i.e. treated as absent)."""
    _client_app_version.set(raw if isinstance(raw, str) and len(raw) <= _MAX_LEN else None)


def client_app_version() -> Optional[AppVersion]:
    """The caller's parsed version for this request, or None when it sent none we can read."""
    return parse_app_version(_client_app_version.get())


def client_is_older_than(minimum: AppVersion) -> bool:
    """True only for a caller that sent a parseable version below `minimum`."""
    version = client_app_version()
    return version is not None and version < minimum


# The first marketing version whose Home Screen widget can LINK a card-backed cause to the Updates
# card that holds its sources (`caydex://updates/<SYMBOL>`, 2026-11-12, "Hold for old builds").
# A web-only Insights card (its sources are web pages alone) may back a widget cause only for a
# caller at or above this. The header carries the MARKETING version only (never the build
# number), so builds that share one cannot be told apart. 1.02 (build 13) is the live version and
# has no link. No 1.03 build had been archived when the link landed (owner, 2026-11-12: "keep all
# 1.03"), so every 1.03 build must be archived from a `main` that carries it — archiving a 1.03
# without the link would put web-only causes on a widget that cannot open their sources.
WEB_ONLY_CAUSE_MIN_APP_VERSION = "1.03"


def _as_minimum(minimum: object) -> Optional[AppVersion]:
    """A minimum given as `"1.04"` or `(1, 4, 0)`, or None when it is neither."""
    if isinstance(minimum, tuple):
        if len(minimum) == 3 and all(
            isinstance(part, int) and not isinstance(part, bool) and part >= 0 for part in minimum
        ):
            return minimum  # type: ignore[return-value]
        return None
    return parse_app_version(minimum)


def client_is_at_least(minimum: Union[str, AppVersion]) -> bool:
    """FAIL-CLOSED: True only for a caller that SENT a parseable version at or above `minimum`.

    No header, an unparseable or over-long one, and an unreadable `minimum` are all False. The
    opposite of `client_is_older_than`'s default on purpose — see the module docstring."""
    floor = _as_minimum(minimum)
    if floor is None:
        logger.warning(
            "client_app_version: unreadable minimum %r — answering 'not at least' (fail closed)",
            minimum,
        )
        return False
    version = client_app_version()
    return version is not None and version >= floor


async def capture_client_app_version(
    x_app_version: Optional[str] = Header(None, alias="X-App-Version"),
) -> None:
    """Router-level dependency: records the caller's `X-App-Version` for this request."""
    set_client_app_version(x_app_version)
