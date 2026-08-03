"""The iOS side of `POST /users/me/claim-guest-data` is actually wired up.

WHY THIS FILE EXISTS. The endpoint shipped complete — handler, guards, and a full test file
(`test_guest_data_claim.py`) — with **zero callers in the iOS app**. No `APIEndpoint` case, no
repository method, nothing in the auth transition. Every backend test passed, and the feature
did not exist: a user added tickers during onboarding, signed up, and landed on an empty
watchlist, because migration 108 partitions guests per install and a real account keys off its
user id. The most expensive kind of bug — invisible to the entire backend suite.

A backend test cannot call Swift, so this reads the Swift source as text. That is the
established idiom here (see `test_journey_schema_parity.py`, which parses
`JourneyContentStore.swift` the same way).

What this guards, concretely:
  * the endpoint case exists at all (the original bug),
  * its path matches the FastAPI route **derived from the Python source**, so renaming either
    side fails this test rather than 404ing at runtime,
  * it is registered as POST — the `method` switch ends in `default: return .GET`, so an
    omission there silently ships a GET and the route answers 405,
  * the auth transition actually invokes it,
  * the `X-Guest-Id` header is still sent unconditionally — the claim identifies the install
    ONLY through that header, so gating it behind a condition would make every claim a no-op
    that reports success.

No network, no Supabase — pure source inspection.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"

_API_ENDPOINT = _IOS / "Core" / "Services" / "APIEndpoint.swift"
_API_CLIENT = _IOS / "Core" / "Services" / "APIClient.swift"
_ACCOUNT_REPO = _IOS / "Core" / "Repositories" / "AccountRepository.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_USERS_EP = _REPO / "backend" / "app" / "api" / "v1" / "endpoints" / "users.py"
_API_ROUTER = _REPO / "backend" / "app" / "api" / "v1" / "api.py"


def _read(path: Path) -> str:
    assert path.exists(), f"expected to exist: {path}"
    return path.read_text(encoding="utf-8")


def _backend_claim_route() -> str:
    """Full path of the claim endpoint, derived from the Python source.

    Composed from the router prefix in api.py and the @router.post decorator in users.py, so a
    rename on the backend breaks this test instead of silently 404ing the app.
    """
    prefix = re.search(
        r"include_router\(\s*users\.router\s*,\s*prefix=\"([^\"]+)\"", _read(_API_ROUTER)
    )
    assert prefix, "could not find the users router registration in api.py"

    route = re.search(r"@router\.post\(\"(/me/claim-guest-data)\"\)", _read(_USERS_EP))
    assert route, "the claim-guest-data route disappeared from users.py"

    return f"/api/v1{prefix.group(1)}{route.group(1)}"


def test_backend_route_is_where_we_think_it_is():
    assert _backend_claim_route() == "/api/v1/users/me/claim-guest-data"


def test_api_endpoint_case_exists():
    """THE original bug: no case, so nothing could ever call the endpoint."""
    assert re.search(r"^\s*case claimGuestData\b", _read(_API_ENDPOINT), re.MULTILINE), (
        "APIEndpoint has no `claimGuestData` case — the guest→account claim cannot be invoked"
    )


def test_api_endpoint_path_matches_the_backend_route():
    src = _read(_API_ENDPOINT)
    match = re.search(
        r"case \.claimGuestData:\s*\n\s*return \"([^\"]+)\"", src
    )
    assert match, "no path mapping for .claimGuestData in APIEndpoint.path"
    assert match.group(1) == _backend_claim_route(), (
        f"iOS calls {match.group(1)!r} but the backend serves {_backend_claim_route()!r}"
    )


def test_api_endpoint_uses_post():
    """The `method` switch ends in `default: return .GET`; an omission here ships a silent 405."""
    src = _read(_API_ENDPOINT)
    method_block = re.search(
        r"var method: HTTPMethod \{\s*switch self \{(.*?)return \.POST", src, re.DOTALL
    )
    assert method_block, "could not locate the .POST arm of APIEndpoint.method"
    assert ".claimGuestData" in method_block.group(1), (
        ".claimGuestData is not in the .POST case list — it would be sent as a GET"
    )


def test_repository_exposes_the_call():
    src = _read(_ACCOUNT_REPO)
    assert "func claimGuestData()" in src, "AccountRepository does not expose claimGuestData()"
    assert "endpoint: .claimGuestData" in src, (
        "AccountRepository.claimGuestData does not actually hit the .claimGuestData endpoint"
    )


def test_auth_transition_invokes_the_claim():
    """A wired endpoint nobody calls is the same bug one layer up."""
    src = _read(_APP_STATE)
    assert "claimGuestData" in src, (
        "AppState never calls claimGuestData — signing in still strands the guest watchlist"
    )

    on_auth = re.search(r"private func onAuthenticated\([^)]*\) async \{(.*?)\n    \}", src, re.DOTALL)
    assert on_auth, "onAuthenticated() not found in AppState — did it get renamed?"
    assert "claimGuestData" in on_auth.group(1), (
        "the claim is not invoked from onAuthenticated(), the single funnel for every "
        "transition to .authenticated"
    )


def test_claim_runs_before_the_credit_refresh():
    """Ordering matters: the claim moves the rows a later read would report on."""
    src = _read(_APP_STATE)
    body = re.search(r"private func onAuthenticated\([^)]*\) async \{(.*?)\n    \}", src, re.DOTALL).group(1)
    assert body.index("claimGuestData") < body.index("refreshCredits"), (
        "claimGuestDataIfNeeded() must run before refreshCredits()"
    )


def test_claim_cannot_throw_out_of_the_auth_transition():
    """The user is ALREADY signed in when this runs; an escaping error turns a successful
    sign-in into a visible failure."""
    src = _read(_APP_STATE)
    fn = re.search(
        r"private func claimGuestDataIfNeeded\(\) async \{(.*?)\n    \}", src, re.DOTALL
    )
    assert fn, "claimGuestDataIfNeeded() not found in AppState"
    assert "catch" in fn.group(1), "claimGuestDataIfNeeded must swallow errors, not propagate them"
    assert " throws" not in fn.group(0).split("{")[0], "claimGuestDataIfNeeded must not be throwing"


def test_reentrancy_guard_present():
    """`restoreAuthState()` on launch can race an explicit `signIn()`; two concurrent claims
    both read the guest rows before either writes."""
    src = _read(_APP_STATE)
    assert "isClaimingGuestData" in src, (
        "no re-entrancy guard on the claim — concurrent auth transitions double-run it"
    )


def test_guest_id_header_is_sent_unconditionally():
    """The claim identifies the install ONLY by this header. If it were ever sent conditionally,
    `guest_user_id_for(None)` would return the shared sentinel, the endpoint's hard guard would
    refuse, and every claim would report success while moving nothing."""
    src = _read(_API_CLIENT)
    match = re.search(
        r"^([ \t]*)request\.setValue\(GuestIdentity\.current, forHTTPHeaderField: \"X-Guest-Id\"\)",
        src,
        re.MULTILINE,
    )
    assert match, "the X-Guest-Id header is no longer set in APIClient"
    assert len(match.group(1)) == 8, (
        "the X-Guest-Id header line changed indentation, which suggests it moved inside a "
        "conditional — the guest-data claim depends on it being sent on every request"
    )


@pytest.mark.parametrize(
    "swift_type,field",
    [("ClaimGuestDataResult", "claimed"), ("Counts", "watchlistItems")],
)
def test_response_dto_exists_with_snake_case_mapping(swift_type, field):
    """APIClient does NOT set .convertFromSnakeCase, so `watchlist_items` must be mapped by hand
    or the response throws on decode — on the sign-in path."""
    src = _read(_IOS / "Models" / "AccountSettingsModels.swift")
    assert swift_type in src, f"{swift_type} missing from AccountSettingsModels.swift"
    assert field in src, f"{swift_type}.{field} missing"
    assert 'case watchlistItems = "watchlist_items"' in src, (
        "watchlist_items is not mapped in CodingKeys; APIClient has no snake_case conversion"
    )
