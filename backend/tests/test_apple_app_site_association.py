"""The associated-domains manifest Apple fetches from `caydexinvest.com`.

This file is load-bearing for two features and gives NO feedback when it is wrong. Apple fetches
it out-of-band; if the shape is off, `ASAuthorizationPlatformPublicKeyCredentialProvider` returns
an error and Password AutoFill simply never offers a credential. There is no log, no crash, and
nothing on screen to explain it — so the contract is pinned here instead.

What Apple requires, and what each test below protects:

  * `webcredentials.apps` containing exactly `<TeamID>.<BundleID>`. A typo means the app asking
    for the passkey is not the app the domain vouches for, and the request is refused.
  * `Content-Type: application/json` — not text/html, not text/plain.
  * Reachable unauthenticated. It contains no secrets: the Team ID and bundle id ship inside
    every copy of the app.

Not covered here because it cannot be: the file must be served with **no redirects** from the
exact RP ID host. That is a DNS/hosting property, verified against production rather than in
pytest — see the route docstring in `app/main.py`.
"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.main import apple_app_site_association


@pytest.mark.asyncio
async def test_names_exactly_the_team_and_bundle_pair():
    response = await apple_app_site_association()
    body = json.loads(response.body)

    apps = body["webcredentials"]["apps"]
    assert apps == [f"{settings.APPLE_TEAM_ID}.{settings.APPLE_BUNDLE_ID}"]


@pytest.mark.asyncio
async def test_the_app_identifier_is_well_formed():
    """`TEAMID.bundle.id` — a 10-char Team ID then a reverse-DNS bundle id.

    Pinned literally because a plausible-looking wrong value (bundle id alone, the two joined by
    a slash, a trailing dot) fails silently at Apple's end with no signal on ours.
    """
    body = json.loads((await apple_app_site_association()).body)
    identifier = body["webcredentials"]["apps"][0]

    team, _, bundle = identifier.partition(".")
    assert len(team) == 10 and team.isalnum() and team.isupper(), team
    assert bundle.count(".") >= 2, bundle
    assert not identifier.endswith(".")
    assert " " not in identifier


@pytest.mark.asyncio
async def test_content_type_is_application_json():
    """Apple rejects the file if it is served as HTML or plain text."""
    response = await apple_app_site_association()
    assert response.media_type == "application/json"


@pytest.mark.asyncio
async def test_carries_no_credentials_or_secrets():
    """It is public by necessity — Apple fetches it unauthenticated. Guard against anyone
    later 'enriching' it with something that must not be world-readable."""
    raw = json.loads((await apple_app_site_association()).body)
    flattened = json.dumps(raw).lower()
    for forbidden in ("key", "secret", "token", "password", "dsn"):
        assert forbidden not in flattened, f"{forbidden!r} leaked into a public file"


@pytest.mark.asyncio
async def test_route_is_registered_at_the_exact_path_apple_fetches():
    """Apple fetches `/.well-known/apple-app-site-association` — no `.json`, no prefix.

    Asserted against the real app router rather than the function, because the path is the part
    that is easy to get wrong: mounting this under the `/api/v1` prefix everything else uses
    would make it unreachable, and nothing else in the suite would notice.
    """
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/.well-known/apple-app-site-association" in paths
    assert "/.well-known/apple-app-site-association.json" not in paths
