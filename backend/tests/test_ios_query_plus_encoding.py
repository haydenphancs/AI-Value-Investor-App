"""`APIClient.buildRequest` sends a literal `+` in a query value as `%2B` (TestFlight, 2026-09-11).

`URLComponents.queryItems` percent-encodes with `urlQueryAllowed`, which keeps `+`;
Starlette decodes a raw `+` as a space. The notification inbox's keyset cursor carries a
`+00:00` offset, so page 2 reached the backend as `…21.462 00:00|<id>`, Postgres refused
the timestamp and the Alerts tab toasted "Couldn't load more notifications" on every
build. Source-scan guard (no XCTest target): comment-stripped, brace-bound to the one
function every request goes through, and ordered — the rewrite must come AFTER the
`queryItems` assignment it corrects. Mutation-tested: the rewrite removed → red; the
rewrite moved above the assignment → red; `%2B` → `%20` → red.

The backend half — a cursor with no `+` in it, and a parser that repairs the mangled
form — is pinned in `test_notification_inbox_cursor.py`.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CLIENT = _REPO / "frontend/ios/ios/Core/Services/APIClient.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _decl_block(src: str, prefix: str) -> str:
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {prefix!r}")


@pytest.fixture(scope="module")
def build_request() -> str:
    assert _CLIENT.exists(), f"{_CLIENT} moved — update this guard, do not delete it"
    return _decl_block(_strip_comments(_CLIENT.read_text(encoding="utf-8")),
                       "private func buildRequest(")


def test_a_literal_plus_goes_out_as_percent_2b(build_request):
    assign = build_request.find("components.queryItems =")
    assert assign != -1, "queryItems are no longer set in buildRequest — re-point this guard"
    rewrite = re.search(
        r'components\.percentEncodedQuery\s*=\s*components\.percentEncodedQuery\?\s*'
        r'\.replacingOccurrences\(of:\s*"\+",\s*with:\s*"%2B"\)',
        build_request,
    )
    assert rewrite, "the `+` → `%2B` rewrite is gone: the inbox cursor's offset is a space again"
    assert rewrite.start() > assign, "the rewrite must run AFTER the queryItems assignment it corrects"


def test_the_rewrite_is_the_only_query_encoding_path(build_request):
    """One funnel. A second `percentEncodedQuery` writer, or a per-endpoint hand-encoding,
    is how a fix to one route silently misses the next."""
    assert build_request.count("percentEncodedQuery =") == 1
    assert "addingPercentEncoding" not in build_request


def test_the_notifications_cursor_still_travels_as_a_query_value():
    """The guard above only matters while the cursor is a query value. If it ever moves
    to a header or body, delete this file rather than let it pass vacuously."""
    endpoint = _strip_comments((_REPO / "frontend/ios/ios/Core/Services/APIEndpoint.swift").read_text())
    body = _decl_block(endpoint, "var queryParameters: [String: String]?")
    arm = body[body.index("case .listNotifications(let limit, let before):"):]
    arm = arm[:arm.index("case .listCreditHistory")]
    assert 'q["before"] = before' in arm
