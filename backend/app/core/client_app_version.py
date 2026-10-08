"""The calling app's marketing version (`X-App-Version`), for behaviour an OLD build must not get.

iOS sends `X-App-Version: <CFBundleShortVersionString>` on every request
(`APIClient.buildRequest`). Builds that share a marketing version send the same value — 1.0 (10)
and 1.0 (11) both say "1.0" — so this tells VERSIONS apart, never builds. That is exactly the
unit an App Store release changes.

The value is request-scoped: `capture_client_app_version`, a router-level dependency, stores it
in a ContextVar. A dependency runs in the request's own task, so the endpoint, a streaming body
generator and every task or thread they start all read the same value, and the next request
starts again from the default.

It fails OPEN on absence. No header, or one that does not parse, is not an old iOS build (a test,
a script, a future web client), so only a caller that SENT a parseable version below the minimum
counts as older.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Optional, Tuple

from fastapi import Header

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


async def capture_client_app_version(
    x_app_version: Optional[str] = Header(None, alias="X-App-Version"),
) -> None:
    """Router-level dependency: records the caller's `X-App-Version` for this request."""
    set_client_app_version(x_app_version)
