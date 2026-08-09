"""iOS `APIEndpoint.authPolicy` must agree with the backend dependency on the same route.

Why this exists. `authPolicy`'s predecessor was `requiresAuth: Bool`, ending in
`default: return true`, and that one line was WRONG for 27 of the 42 endpoints it swept up:
watchlist, tracking, every portfolio route, the nine research routes, `/users/me/credits` and
analytics were guest-capable on the backend, and two more were fully public.

(The nine research routes plus the two report routes have since moved to `.signInRequired` by
product decision — AI generation costs real money per call and its guest metering was
bypassable. They are listed under the strict set below.)

Nothing enforced the flag, so the drift was invisible. The moment `APIClient` started honouring
it — which is the whole point of the fix — a wrong `.signInRequired` would have deleted a
working feature for every signed-out user, and a wrong `.guestAllowed` would put the sign-in
prompt back to being a silently-reverted button.

This is the same guard-rail role as the schema-parity tests: a failure here is a real bug that
ships, not a style nit. Source-level on both sides, so it needs no app build and no network.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ENDPOINT_SWIFT = _REPO / "frontend/ios/ios/Core/Services/APIEndpoint.swift"
_ENDPOINTS_DIR = _REPO / "backend/app/api/v1/endpoints"

# Backend dependency → the policy iOS must declare.
#
# `get_identity_only_user` counts as guest-allowed: it resolves an identity (for telemetry
# bucketing) but never requires one.
_GUEST_DEPS = {
    "get_current_user_or_guest",
    "get_learn_identity",
    "get_research_identity",
    "get_watchlist_identity",
    "get_identity_only_user",
    "get_optional_user_id",
}
_STRICT_DEPS = {"get_current_user", "get_current_user_id"}


def _swift_source() -> str:
    assert _ENDPOINT_SWIFT.exists(), f"missing {_ENDPOINT_SWIFT}"
    return _ENDPOINT_SWIFT.read_text()


def _auth_policy_block(src: str) -> str:
    """The body of `var authPolicy`, up to the `requiresAuth` alias that follows it.

    Comment lines are stripped: the prose here deliberately quotes the old `default: true`
    it replaced, and a naive scan would read that as a live default arm.
    """
    start = src.index("var authPolicy: AuthPolicy {")
    end = src.index("var requiresAuth: Bool", start)
    return "\n".join(
        line for line in src[start:end].splitlines()
        if not line.strip().startswith("//")
    )


def _policy_by_case(src: str) -> dict[str, str]:
    """case name → "public" | "guestAllowed" | "signInRequired".

    Handles multi-line case lists: `case .a, .b,` / `     .c:` — the dominant style in this
    switch, so a line-must-start-with-`case` parser silently sees almost nothing.
    """
    block = _auth_policy_block(src)
    policies: dict[str, str] = {}
    pending: list[str] = []
    collecting = False
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("case "):
            collecting = True
        if collecting:
            pending += re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)", stripped)
            if stripped.endswith(":"):
                collecting = False
            continue
        m = re.search(r"return \.(public|guestAllowed|signInRequired)\b", stripped)
        if m:
            for name in pending:
                policies[name] = m.group(1)
            pending = []
    return policies


def _declared_cases(src: str) -> set[str]:
    """Every `case x` declared on the enum itself (before the `path` computed property)."""
    head = src[src.index("enum APIEndpoint: Sendable {"):src.index("nonisolated var path: String")]
    return {
        m.group(1)
        for m in re.finditer(r"^\s{4}case ([a-z][A-Za-z0-9_]*)", head, flags=re.MULTILINE)
    }


# ── the property itself ──────────────────────────────────────────────────────

def test_auth_policy_has_no_default_arm():
    """Exhaustiveness is the guard-rail: a new endpoint must not COMPILE until someone decides
    its policy. A `default:` would silently reinstate the exact bug this replaced."""
    block = _auth_policy_block(_swift_source())
    assert "default:" not in block, (
        "authPolicy must stay exhaustive — a default arm lets a new endpoint inherit a policy "
        "nobody chose, which is how 27 endpoints ended up mislabelled"
    )


def test_every_endpoint_case_has_a_policy():
    src = _swift_source()
    declared = _declared_cases(src)
    policies = _policy_by_case(src)
    missing = declared - policies.keys()
    assert not missing, f"endpoint cases with no authPolicy arm: {sorted(missing)}"


def test_policies_are_not_all_one_value():
    """Sanity check on the parser itself — if the regex silently stopped matching, every other
    assertion here would pass vacuously."""
    values = set(_policy_by_case(_swift_source()).values())
    assert values == {"public", "guestAllowed", "signInRequired"}, values


# ── the contract with the backend ────────────────────────────────────────────

def _backend_strict_routes() -> set[tuple[str, str]]:
    """(method, path-with-{params}) for every route taking a STRICT auth dependency."""
    strict: set[tuple[str, str]] = set()
    for py in sorted(_ENDPOINTS_DIR.glob("*.py")):
        text = py.read_text()
        # Split on the decorator so each chunk is one handler.
        for chunk in re.split(r"(?=@router\.)", text):
            m = re.match(r'@router\.(get|post|put|patch|delete)\(\s*[\'"]([^\'"]*)[\'"]', chunk)
            if not m:
                continue
            method, path = m.group(1).upper(), m.group(2)
            signature = chunk[: chunk.find("):") + 2] if "):" in chunk else chunk[:2000]
            deps_used = set(re.findall(r"Depends\((\w+)\)", signature))
            if deps_used & _STRICT_DEPS:
                strict.add((method, path))
    return strict


def test_backend_strict_routes_are_discoverable():
    """Guards the scanner: if the decorator regex drifts, the parity assertion below would
    pass by finding nothing."""
    strict = _backend_strict_routes()
    assert len(strict) >= 10, f"expected the known strict-auth routes, found {sorted(strict)}"
    # The reported bug's route must be in there.
    assert ("POST", "/{whale_id}/follow") in strict


@pytest.mark.parametrize(
    "case_name",
    [
        # The 15 genuinely strict-auth endpoints. Listed explicitly rather than derived, so a
        # backend route that LOSES its auth dependency is caught too.
        "followWhale", "unfollowWhale", "getWhaleActivity",
        "getCurrentUser", "updateProfile", "deleteAccount", "claimGuestData",
        "getMySubscription", "getMySettings", "updateMySettings",
        "registerDevice", "unregisterDevice", "verifyPurchase",
        "signOut", "changePassword",
        # AI generation moved here from guest-capable: metering keyed on a client-supplied
        # X-Guest-Id, so rotating the header bought unlimited ~17-Gemini-call reports. BOTH
        # generation paths must be listed (`/research/generate` and `GET /stocks/{t}/report`)
        # or the gate leaks through the one that isn't.
        "generateResearch", "getResearchStatus", "getResearchReport",
        "getResearchReportPDF", "regenerateResearchReportPDF", "getResearchTickerReport",
        "getMyReports", "rateReport", "deleteReport",
        "getTickerReport", "chatWithTickerReport", "prewarmReportCollection",
    ],
)
def test_strict_endpoints_are_signInRequired(case_name):
    policies = _policy_by_case(_swift_source())
    assert policies.get(case_name) == "signInRequired", (
        f"{case_name} hits a strict `get_current_user(_id)` route; marking it anything else "
        f"means a tokenless tap spends a round trip to be refused and then fails silently"
    )


@pytest.mark.parametrize(
    "case_name",
    [
        # The regression set: every one of these was `requiresAuth == true` under the old
        # `default:` while the backend served it to guests. Gating any of them removes a
        # working feature from every signed-out user.
        "getWatchlist", "addToWatchlist", "removeFromWatchlist",
        "getTrackingAssets", "bulkUpdateHoldings", "getPortfolioInsights",
        "getPortfolios", "createPortfolio", "renamePortfolio", "deletePortfolio",
        "setPortfolioTickers", "setPortfolioHoldings", "reorderPortfolios",
        "getPortfolioInsightsForPortfolio",
        # Switching the active group (migration 126). Guest-capable like every sibling —
        # `get_watchlist_identity` resolves a signed-out caller to their per-install
        # partition. Gating it would freeze guests on whichever group they were seeded
        # with, on the tab that exists to organise their tickers.
        "activatePortfolio",
        "getUserCredits", "trackEvents",
    ],
)
def test_guest_capable_endpoints_are_not_gated(case_name):
    policies = _policy_by_case(_swift_source())
    assert policies.get(case_name) == "guestAllowed", (
        f"{case_name} is guest-capable on the backend (per-install identity); marking it "
        f"signInRequired locks signed-out users out of a feature that works today"
    )


@pytest.mark.parametrize("case_name", ["getHoldersData", "enrichStockNews"])
def test_public_endpoints_are_public(case_name):
    """Both took no auth dependency at all yet fell into the old `default: true`."""
    policies = _policy_by_case(_swift_source())
    assert policies.get(case_name) == "public", policies.get(case_name)


def test_auth_flow_endpoints_are_public():
    """Password recovery in particular exists FOR people who cannot sign in — gating it would
    make the recovery path unreachable exactly when it is needed."""
    policies = _policy_by_case(_swift_source())
    for name in (
        "signIn", "signUp", "refreshToken", "forgotPassword", "resetPassword",
        "resendConfirmation", "oauthSignIn", "sessionExchange", "getPlanCatalog",
    ):
        assert policies.get(name) == "public", f"{name} -> {policies.get(name)}"
