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
# ⚠️ RE-CLASSIFIED 2026-09-07 (account-only). The five `*_identity` wrappers used to resolve a
# signed-out caller to a per-install guest; they now delegate to `get_current_user` and raise.
# They are STRICT, and listing them as guest-capable would make every assertion below assert
# the opposite of the truth.
#
# Two genuinely guest-capable dependencies remain:
#   • `get_identity_only_user` — analytics. The one dependency that must never raise
#     (`.claude/rules/auth.md` §4).
#   • `get_current_user_or_guest` — admin only, and those routes are gated a second time by
#     `_authorize_admin`, which is the real check.
# `get_optional_user_id` has zero call sites now; it stays listed so that its RETURN would be
# caught rather than silently re-opening a route.
_GUEST_DEPS = {
    "get_current_user_or_guest",
    "get_identity_only_user",
    "get_optional_user_id",
}
_STRICT_DEPS = {
    "get_current_user",
    "get_current_user_id",
    "get_learn_identity",
    "get_profile_identity",
    "get_research_identity",
    # `get_chat_identity` was MISSING from the old _GUEST_DEPS, which made all 7 chat routes
    # invisible to both scanners in this file. Fixed while re-classifying.
    "get_chat_identity",
    "get_watchlist_identity",
}


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
    assertion here would pass vacuously.

    All three values still occur after the account-only change, but only just: `.guestAllowed`
    now has exactly ONE member (`trackEvents`) and `.public` has ten. That makes the set alone
    a weak canary — a parser that found three arms would satisfy it — so the count floor below
    is the half that actually holds.
    """
    policies = _policy_by_case(_swift_source())
    assert len(policies) >= 140, (
        f"only {len(policies)} endpoint cases parsed — the switch has ~147, so the regex has "
        "drifted and every assertion in this file is now vacuous"
    )
    values = set(policies.values())
    assert values == {"public", "guestAllowed", "signInRequired"}, values


# ── the contract with the backend ────────────────────────────────────────────

def _router_level_deps(source: str) -> set[str]:
    """Dependencies declared on the ROUTER, which apply to every route in that module.

    ⚠️ Without this, both scanners below are blind to the account-only gating. The ~56
    market-data routes are closed by `APIRouter(dependencies=[Depends(get_current_user_id)])`
    rather than per route — deliberately, so a route added tomorrow is authenticated by
    default — and a per-route `Depends(...)` scan sees none of it. When that gating landed the
    whole 8,818-test suite stayed green, which is exactly how a guard goes vacuous.
    """
    marker = "APIRouter("
    i = source.find("router = " + marker)
    if i == -1:
        return set()
    # Paren-BALANCED, not `[^)]*`. The argument list is `dependencies=[Depends(get_current_user_id)]`,
    # which contains nested parens — a non-greedy or negated-class match stops at the FIRST `)`,
    # captures `dependencies=[Depends(get_current_user_id`, and finds no `Depends(...)` in it.
    # That is not hypothetical: it is what this helper did on its first draft, and the strict
    # floor in `test_the_guest_surface_is_only_analytics_and_admin` is what caught it (88 routes
    # scanned as strict instead of ~140). Without that floor it would have silently under-reported
    # for good.
    start = source.index("(", i + len("router = "))
    depth = 0
    for j in range(start, len(source)):
        if source[j] == "(":
            depth += 1
        elif source[j] == ")":
            depth -= 1
            if depth == 0:
                return set(re.findall(r"Depends\((\w+)\)", source[start : j + 1]))
    return set()


def _backend_strict_routes() -> set[tuple[str, str]]:
    """(method, path-with-{params}) for every route taking a STRICT auth dependency."""
    strict: set[tuple[str, str]] = set()
    for py in sorted(_ENDPOINTS_DIR.glob("*.py")):
        text = py.read_text()
        router_deps = _router_level_deps(text)
        # Split on the decorator so each chunk is one handler.
        for chunk in re.split(r"(?=@router\.)", text):
            m = re.match(r'@router\.(get|post|put|patch|delete)\(\s*[\'"]([^\'"]*)[\'"]', chunk)
            if not m:
                continue
            method, path = m.group(1).upper(), m.group(2)
            signature = chunk[: chunk.find("):") + 2] if "):" in chunk else chunk[:2000]
            deps_used = set(re.findall(r"Depends\((\w+)\)", signature)) | router_deps
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
        # ⚠️ THIS LIST WAS INVERTED 2026-09-07. Every name below used to be pinned as
        # `.guestAllowed`, with a comment explaining that gating it "removes a working feature
        # from every signed-out user". That was true, and it is now the intended cost: FMP's
        # signed Order Form grants End-User Display Rights only — their data may be shown
        # solely "through the Licensee's authenticated platform" — and Public External Display
        # was declined on 2026-09-04.
        #
        # The list is KEPT rather than deleted because its job simply reversed. It used to stop
        # these drifting closed; it now stops them drifting back OPEN, one plausible-looking
        # commit at a time. Every name here is a route that worked signed-out within living
        # memory, so each is a candidate for a well-meaning "restore guest access" change.
        "getWatchlist", "addToWatchlist", "removeFromWatchlist",
        "getTrackingAssets", "bulkUpdateHoldings", "getPortfolioInsights",
        "getPortfolios", "createPortfolio", "renamePortfolio", "deletePortfolio",
        "setPortfolioTickers", "setPortfolioHoldings", "reorderPortfolios",
        "getPortfolioInsightsForPortfolio", "activatePortfolio",
        "getUserCredits",
        # Whales / 13F — FMP's Institutional Ownership package plus congress disclosures.
        "getWhaleList", "getWhaleProfile",
        "getWhaleTradeGroups", "getWhaleTradeGroupDetail", "getSignalDetail",
        # Learn. Note this content is OURS — `learn.py` imports no FMP — so the licence does
        # not compel these three; the hard wall does. Recorded so a future reader knows Learn
        # can be re-opened without touching the FMP contract, if the wall is ever softened.
        "getJourney", "getMoneyMoves", "getBooksAudio",
        # Investor profile. Captured during onboarding, which NOW runs after sign-in — the
        # gate order in `iosApp.swift` is disclaimer → sign-in → onboarding. Reverse that and
        # `APIClient` refuses the PUT before it leaves the device and every onboarding answer
        # is dropped silently. That ordering is the precondition for this pair being here.
        "getMyInvestorProfile", "updateMyInvestorProfile",
        # Market data that took NO backend dependency at all before the redesign, and so was
        # correctly `.public` here. These two are the plainest statement of the licence
        # problem: `/stocks/{t}/holders` and `/stocks/{t}/news/enrich` served FMP data to
        # anyone on the internet.
        "getHoldersData", "enrichStockNews",
    ],
)
def test_account_only_endpoints_are_gated(case_name):
    """Was `test_guest_capable_endpoints_are_not_gated` — same list, opposite assertion."""
    policies = _policy_by_case(_swift_source())
    assert policies.get(case_name) == "signInRequired", (
        f"{case_name} is {policies.get(case_name)!r}, but it serves FMP data or the caller's "
        "own data to a signed-out caller. End-User Display Rights permit neither."
    )


def _routes_by_dep(deps: set[str]) -> set[tuple[str, str, str]]:
    """(module, method, path) for every route whose signature uses one of `deps`.

    The MODULE is part of the key deliberately: decorator paths are router-relative, and
    several routers declare `@router.get("")` for their collection endpoint. Keying on
    (method, path) alone collapses watchlist's `GET ""` onto every other `GET ""`, which
    made an earlier version of the overlap check below report a phantom conflict.
    """
    found: set[tuple[str, str, str]] = set()
    for py in sorted(_ENDPOINTS_DIR.glob("*.py")):
        text = py.read_text()
        router_deps = _router_level_deps(text)
        for chunk in re.split(r"(?=@router\.)", text):
            m = re.match(r'@router\.(get|post|put|patch|delete)\(\s*[\'"]([^\'"]*)[\'"]', chunk)
            if not m:
                continue
            signature = chunk[: chunk.find("):") + 2] if "):" in chunk else chunk[:2000]
            if (set(re.findall(r"Depends\((\w+)\)", signature)) | router_deps) & deps:
                found.add((py.stem, m.group(1).upper(), m.group(2)))
    return found


def _backend_guest_routes() -> set[tuple[str, str, str]]:
    """Every route taking a GUEST-capable identity dependency."""
    return _routes_by_dep(_GUEST_DEPS)


# The only routes allowed to resolve an identity without requiring one. Both are argued for
# at their definition; anything else appearing here is a re-opened route.
_ALLOWED_GUEST_MODULES = {"analytics", "admin"}


def test_the_guest_surface_is_only_analytics_and_admin():
    """Inverted 2026-09-07. Was `>= 15 guest routes`; now the guest surface must be tiny.

    ⚠️ The floor on the STRICT side is not decoration — it is the half that stops this test
    going vacuous. "Zero guest routes" is trivially satisfied by a scanner that finds nothing
    at all, which is precisely the failure the original docstring warned about, and precisely
    what happened before `_router_level_deps` existed: the ~56 market-data routes were gated
    and every scanner in this file kept reporting the pre-change numbers.
    """
    guest = _backend_guest_routes()
    offenders = sorted(r for r in guest if r[0] not in _ALLOWED_GUEST_MODULES)
    assert not offenders, (
        "these routes still resolve a guest identity, which End-User Display Rights do not "
        f"permit for FMP data: {offenders}"
    )

    strict = _routes_by_dep(_STRICT_DEPS)
    assert len(strict) >= 120, (
        f"only {len(strict)} routes scan as strict — the app has ~165 and nearly all of them "
        "are account-only now, so the scanner has drifted and the assertion above is vacuous"
    )


def test_no_route_takes_both_a_guest_and_a_strict_dependency():
    """A route holding both is ambiguous: iOS must pick ONE policy for it, and whichever
    it picks is wrong half the time — either a guest is locked out or a tokenless tap
    spends a round trip to be refused."""
    overlap = _backend_guest_routes() & _routes_by_dep(_STRICT_DEPS)
    assert not overlap, f"routes with conflicting auth dependencies: {sorted(overlap)}"


def test_the_public_surface_is_exactly_ten_cases():
    """An allow-list, so re-opening a route requires editing this test and saying why.

    `getHoldersData` and `enrichStockNews` used to be pinned here — they took no backend auth
    dependency at all. They are FMP data, so they are now the plainest example of what the
    licence forbids, and they are pinned in `test_account_only_endpoints_are_gated` instead.
    """
    policies = _policy_by_case(_swift_source())
    public = sorted(name for name, policy in policies.items() if policy == "public")
    assert public == sorted([
        # The eight pre-session auth flows — gating any of them is an unbreakable loop.
        "signIn", "signUp", "refreshToken", "forgotPassword", "resetPassword",
        "resendConfirmation", "oauthSignIn", "sessionExchange",
        # The two price catalogues: no FMP data, no user data, and the paywall renders
        # from them before an account exists.
        "getPlanCatalog", "getCreditPackCatalog",
    ]), public


def test_the_guest_allowed_surface_is_only_analytics():
    """`trackEvents` is the single remaining `.guestAllowed` case, and deliberately so.

    `get_identity_only_user` is the one backend dependency that must never raise
    (`.claude/rules/auth.md` §4). Gating it would also delete the pre-sign-up funnel — the only
    instrument that can measure what the sign-in wall costs in installs, which is a number
    someone will want within a week of launch.
    """
    policies = _policy_by_case(_swift_source())
    guest = sorted(name for name, policy in policies.items() if policy == "guestAllowed")
    assert guest == ["trackEvents"], guest


def test_auth_flow_endpoints_are_public():
    """Password recovery in particular exists FOR people who cannot sign in — gating it would
    make the recovery path unreachable exactly when it is needed."""
    policies = _policy_by_case(_swift_source())
    for name in (
        "signIn", "signUp", "refreshToken", "forgotPassword", "resetPassword",
        "resendConfirmation", "oauthSignIn", "sessionExchange",
        "getPlanCatalog", "getCreditPackCatalog",
    ):
        assert policies.get(name) == "public", f"{name} -> {policies.get(name)}"


# ── MUTATION_LOG — the account-only inversion ────────────────────────────────────────
#
# Hand-run 2026-09-07, when this file was inverted from "these 27 cases must be guest-allowed"
# to "these 29 must be gated". Each mutation applied, the file run, then reverted.
#
#  1. Three Learn cases moved back to `.guestAllowed` in the Swift switch — the shape of a
#     plausible "restore guest access to the education content" commit.
#       -> 4 FAILED (test_account_only_endpoints_are_gated ×3 +
#          test_the_guest_allowed_surface_is_only_analytics)  ✅
#  2. `getThemeDetail` moved back to `.public`.
#       -> test_the_public_surface_is_exactly_ten_cases FAILED  ✅
#  3. `_router_level_deps` stubbed to return an empty set — i.e. the scanner blind to
#     router-level gating again, which is the state this file was in before today.
#       -> test_the_guest_surface_is_only_analytics_and_admin FAILED on the strict floor
#          (142 -> 88)  ✅
#     This mutation is the reason the floor exists, and it is not hypothetical: the FIRST
#     draft of `_router_level_deps` used `[^)]*`, which stops at the paren inside
#     `Depends(...)` and silently found nothing. The floor caught it. Without the floor the
#     helper would have shipped inert and every guest/strict number here would have been wrong.
#
# ⚠️ What these mutations do NOT cover, deliberately: un-gating one backend router. Removing
# `dependencies=[Depends(get_current_user_id)]` from, say, `indices.py` drops the strict count
# by 5 — not enough to breach the floor — and this file would stay green. That case is covered
# behaviourally by `tests/test_account_only_licence_gate.py`, which actually issues the request
# and asserts 401. Neither file is sufficient alone; the licence gate is the one that proves
# the app is closed, and this one proves the two SIDES agree about it.
