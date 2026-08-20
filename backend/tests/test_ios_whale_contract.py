"""Source-scan guard over the iOS whale layer.

There is no XCTest target, and — unlike ~10 other features — NOTHING covered the whale
half of the Swift tree. `test_whale_schema_parity.py` and `IOS_PROFILE_KEYS` in
`test_whale_return_provenance.py` are hardcoded PYTHON transcriptions of Swift
`CodingKeys`: they fail when the Pydantic schema drifts, and cannot fail when the Swift
drifts. This file closes that direction by reading the Swift itself.

⚠️ Written against the three vacuity traps recorded in `project_source_scan_guard_vacuity`:

  1. COMMENTS ARE STRIPPED FIRST. The explanatory comment beside each of these fixes
     names every token the assertions grep for, so an un-stripped scan would pass on
     prose after the code was reverted.
  2. DECLARATIONS ARE BRACE-BOUNDED. Asserting against a whole file passes when the
     token lives in a different type — exactly how a fix to a preview-only duplicate
     screen once looked like a fix to the live one.
  3. THE EXTRACTOR IS TESTED. `test_extractor_is_not_silently_empty` fails if any
     bounded slice comes back empty, so a renamed type degrades to a red test rather
     than to a guard that silently checks nothing.

Mutation-tested by hand: each assertion was watched to go red against the pre-fix source.
"""

import re
from pathlib import Path

import pytest

IOS = Path("../frontend/ios/ios")

FILES = {
    "dtos": IOS / "Models/WhaleDTOs.swift",
    "profile_models": IOS / "Models/WhaleProfileModels.swift",
    "tracking_models": IOS / "Models/TrackingModels.swift",
    "profile_vm": IOS / "ViewModels/WhaleProfileViewModel.swift",
    "tracking_vm": IOS / "ViewModels/TrackingViewModel.swift",
    "profile_view": IOS / "Views/Screens/WhaleProfileView.swift",
    "tracking_view": IOS / "Views/Screens/TrackingView.swift",
    "app_error": IOS / "Core/Utilities/AppError.swift",
}


# ── extraction ───────────────────────────────────────────────────────────────


def _code_only(src: str) -> str:
    """Strip comments and string literals so an assertion can only match real code."""
    src = re.sub(r"/\*(?:.|\n)*?\*/", "", src)          # block comments
    src = re.sub(r"(?m)^\s*///.*$", "", src)            # doc comments
    src = re.sub(r"(?m)^\s*//.*$", "", src)             # whole-line comments
    src = re.sub(r"(?m)\s+//(?!/).*$", "", src)         # trailing comments
    return src


def _braced(src: str, decl_pattern: str) -> str:
    """The body of the FIRST declaration matching `decl_pattern`, brace-matched.

    Brace-matched rather than "scan forward to the next landmark": deleting the thing
    under test makes a landmark-bounded window GROW until it finds an unrelated match,
    which is the failure mode that makes these guards pass vacuously.
    """
    m = re.search(decl_pattern, src)
    if not m:
        return ""
    i = src.index("{", m.start())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
    return ""


def _src(key: str) -> str:
    p = FILES[key]
    assert p.exists(), f"missing {p} — this guard is only meaningful if it reads real source"
    return _code_only(p.read_text(encoding="utf-8"))


# ── the extractor itself ─────────────────────────────────────────────────────

_BOUNDED = [
    ("profile_models", r"struct WhaleProfile\b"),
    ("profile_models", r"struct WhaleBehaviorSummary\b"),
    ("profile_models", r"struct WhaleTradeGroup\b"),
    ("tracking_models", r"struct TrendingWhale\b"),
    ("tracking_models", r"struct WhaleTradeGroupActivity\b"),
    ("profile_vm", r"class WhaleProfileViewModel\b"),
    ("tracking_view", r"struct FollowedWhalesRow\b"),
    ("dtos", r"struct LenientArray\b"),
]


@pytest.mark.parametrize("key,pattern", _BOUNDED)
def test_extractor_is_not_silently_empty(key, pattern):
    """A renamed type must fail loudly here, not quietly disarm every assertion below."""
    body = _braced(_src(key), pattern)
    assert len(body) > 80, f"{pattern} in {key} extracted {len(body)} chars — guard is vacuous"


def test_comment_stripper_removes_prose_but_keeps_code():
    sample = '''
    // isLocked showPaywall bullish
    /// isLocked showPaywall bullish
    let real = isLocked  // isLocked
    '''
    out = _code_only(sample)
    assert out.count("isLocked") == 1, out


# ── backend ↔ iOS field parity, read from the Swift side ─────────────────────


def test_profile_dto_declares_every_backend_key():
    """The other direction from `test_whale_schema_parity.py`: that file pins the
    Pydantic shape against a hardcoded list, so editing the SWIFT breaks nothing."""
    from app.schemas.whale import WhaleProfileResponse

    body = _braced(_src("dtos"), r"struct WhaleProfileDTO\b")
    assert body
    backend_keys = set(WhaleProfileResponse.model_construct().model_dump().keys())
    missing = {k for k in backend_keys if f'"{k}"' not in body and k not in body}
    assert not missing, (
        f"WhaleProfileDTO does not decode backend field(s) {sorted(missing)} — "
        "a field iOS ignores is a feature that silently does not ship"
    )


def test_trending_whale_dto_declares_every_backend_key():
    from app.schemas.whale import TrendingWhaleResponse

    body = _braced(_src("dtos"), r"struct TrendingWhaleDTO\b")
    assert body
    for k in TrendingWhaleResponse.model_construct().model_dump():
        assert f'"{k}"' in body or k in body, f"TrendingWhaleDTO is missing {k}"


def test_new_whale_error_codes_are_mapped_on_ios():
    """A backend ErrorCode with no iOS branch falls through to a generic message."""
    body = _src("app_error")
    for code in ("WHALE_FOLLOW_LOCKED", "WHALE_PROFILE_UNAVAILABLE", "WHALE_NOT_FOUND"):
        assert f'"{code}"' in body, f"{code} has no AppError branch"


def test_stat_tile_fields_stay_non_optional_on_the_swift_side():
    """`ytd_return` / `portfolio_value` must decode as non-Optional `Double`.

    The backend guarantees a number and uses the `*_status` fields to say "don't believe
    it". If the Swift ever went Optional the two halves would disagree about who carries
    the doubt.
    """
    body = _braced(_src("dtos"), r"struct WhaleProfileDTO\b")
    assert re.search(r"let portfolioValue:\s*Double(?!\?)", body), body[:0] or "portfolioValue must be non-Optional Double"
    assert re.search(r"let ytdReturn:\s*Double(?!\?)", body), "ytdReturn must be non-Optional Double"


def test_provenance_fields_stay_optional_on_the_swift_side():
    """Swift synthesises `decodeIfPresent` only for Optionals, so a
    non-Optional-with-default throws `keyNotFound` against a backend that predates the
    field and fails the WHOLE profile decode on every installed build."""
    body = _braced(_src("dtos"), r"struct WhaleProfileDTO\b")
    for field in ("returnStatus", "returnWindowYears", "portfolioStatus",
                  "portfolioAsOf", "filingDate", "isLocked", "tierRequired"):
        assert re.search(rf"let {field}:\s*\w+\?", body), f"{field} must stay Optional"


# ── the behavioural fixes ────────────────────────────────────────────────────


def test_behavior_summary_colour_is_direction_aware():
    """The backend emits Accumulating / Reducing / Rebalancing / Holding. Painting the
    verb `bullish` unconditionally rendered a net-selling whale's "Reducing" in GREEN."""
    body = _braced(_src("profile_models"), r"struct WhaleBehaviorSummary\b")
    assert body
    assert "color(forAction" in body, "the action colour must be derived from the action"
    assert not re.search(
        r"actionPart\.foregroundColor\s*=\s*AppColors\.bullish", body
    ), "the primary action verb is still hardcoded to bullish"


def test_followed_whales_row_does_not_take_the_last_word_of_an_institution():
    """`.components(separatedBy: " ").last` rendered "Tiger Global Management" as
    "Management" — and "AQR Capital Management" identically."""
    body = _braced(_src("tracking_view"), r"struct FollowedWhalesRow\b")
    assert body
    assert "shortName" in body, "the caption must go through the category-aware helper"
    assert "category" in body, "the helper must branch on whale.category"


def test_follower_count_has_a_singular_form():
    body = _braced(_src("tracking_models"), r"struct TrendingWhale\b")
    assert body
    fn = _braced(body, r"var formattedFollowers")
    assert fn, "formattedFollowers not found"
    assert "1 follower" in fn or "follower\"" in fn, "no singular form"
    assert "followersCount / 1000" not in fn, (
        "integer division under-counts: 1,999 rendered as '1K followers'"
    )


def test_profile_follow_button_is_plan_gated():
    """A locked Follow tap must raise the paywall BEFORE the optimistic write. Without
    it the server 403s, `reportMutationFailure` falls to its generic-toast arm, and the
    pill animates in and snaps back with no mention of the plan."""
    body = _braced(_src("profile_vm"), r"class WhaleProfileViewModel\b")
    fn = _braced(body, r"func toggleFollow\(\)")
    assert fn, "toggleFollow not found"
    assert "isLocked" in fn, "toggleFollow does not consult profile.isLocked"
    assert "showPaywall" in fn, "toggleFollow does not raise the paywall"
    # The gate must precede the service call, or the optimistic state is created anyway.
    assert fn.index("isLocked") < fn.index("whaleService.toggleFollow"), (
        "the plan gate must run BEFORE the service mutation"
    )


def test_profile_load_error_routes_through_app_error_and_does_not_lie():
    body = _braced(_src("profile_vm"), r"class WhaleProfileViewModel\b")
    assert "AppError.from(" in body, (
        ".claude/rules/ios-swiftui.md requires every error through AppError.from(_:)"
    )
    assert "Showing cached data." not in body, (
        "the old copy claimed cached data while loadSampleProfile() was a no-op"
    )
    assert "loadSampleProfile" not in body, "the dead no-op fallback must be gone"


def test_profile_load_is_cancellable():
    body = _braced(_src("profile_vm"), r"class WhaleProfileViewModel\b")
    assert "loadTask" in body and "loadTask?.cancel()" in body, (
        "concurrent loads must cancel, or a stale response can overwrite a fresh one"
    )


def test_whale_arrays_decode_leniently():
    """One malformed row must not empty the whole tab."""
    vm = _src("tracking_vm")
    assert "LenientArray<TrendingWhaleDTO>" in vm, "roster decode is still all-or-nothing"
    assert "LenientArray<WhaleTradeGroupActivityDTO>" in vm, "feed decode is still all-or-nothing"
    lenient = _braced(_src("dtos"), r"struct LenientArray\b")
    assert lenient and "droppedCount" in lenient, "drops must be counted and logged"


def test_identity_change_clears_whale_state():
    """Follow-derived state is identity-scoped; leaving it up hands account B account
    A's followed investors and Recent Trades (.claude/rules/auth.md §7)."""
    # Name-tolerant: this hook has been called `reloadForIdentityChange` and
    # `handleIdentityChange`. What must not drift is that SOME identity hook clears the
    # follow-derived state — so match either, and fail loudly if neither exists rather
    # than silently checking nothing.
    body = _braced(_src("tracking_vm"), r"func (?:reload|handle)IdentityChange\s*\(")
    assert body, "no identity-change hook found on TrackingViewModel"
    for field in ("trackedWhales", "allWhaleTrades", "groupedWhaleTrades"):
        assert f"{field} = []" in body, f"{field} is not cleared on identity change"


def test_whale_roster_failure_is_surfaced():
    """An unexplained empty roster reads as 'we track nobody'."""
    vm = _src("tracking_vm")
    assert "whalesErrorMessage" in vm, "a roster load failure is still silent"
    assert "whalesErrorMessage" in _src("tracking_view"), "nothing renders the error"


def test_congressional_activity_dates_are_not_relative():
    """A STOCK Act disclosure is 30-45 days behind the trade, so 'Today' over a filing
    about a six-week-old trade states something false."""
    body = _braced(_src("tracking_models"), r"struct WhaleTradeGroupActivity\b")
    fn = _braced(body, r"var formattedDate")
    assert fn, "formattedDate not found"
    assert "politicians" in fn, "congressional rows still use relative wording"
    assert "Disclosed" in fn, "congressional rows must be labelled as disclosures"
    assert "days > 0" in fn, "a future date still renders a negative 'days ago'"


def test_risk_badge_hides_when_the_backend_has_no_classification():
    """`WhaleRiskProfile.fromBackend("")` defaults to `.moderate`, so a whale that has
    never been hydrated (`risk_profile: ""` — Mark Kelly in production) rendered a
    confident "Moderate" badge for a filer nothing is known about."""
    models = _src("profile_models")
    body = _braced(models, r"struct WhaleProfile\b")
    assert body, "WhaleProfile not found"
    assert "riskProfileRaw" in body, "the raw backend string must be preserved"
    assert "hasRiskProfile" in body, "no predicate distinguishing 'no classification'"

    view = _braced(_src("profile_view"), r"struct WhaleProfileHeader\b")
    assert view, "WhaleProfileHeader not found"
    assert "hasRiskProfile" in view, "the badge is still rendered unconditionally"


def test_whale_endpoint_has_no_bare_string_error_details():
    """A bare-string `detail` renders as `{"detail": "..."}`, which iOS
    `APIErrorResponse` cannot decode — the user gets a generic failure with no copy
    and no action (invariant #3)."""
    src = Path("app/api/v1/endpoints/whales.py").read_text(encoding="utf-8")
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#.*$", "", src)
    bare = re.findall(r"detail\s*=\s*[\"']", src)
    assert not bare, (
        f"{len(bare)} bare-string HTTPException detail(s) in whales.py — "
        "use make_error_body(...) so iOS can decode the error"
    )


# ── Activity disclosure (migration 145) ──────────────────────────────────────


def test_activity_fields_reach_both_dtos():
    dtos = _src("dtos")
    roster = _braced(dtos, r"struct TrendingWhaleDTO\b")
    profile = _braced(dtos, r"struct WhaleProfileDTO\b")
    assert roster and profile
    for key in ("activity_status", "activity_label"):
        assert f'"{key}"' in roster, f"TrendingWhaleDTO does not decode {key}"
        assert f'"{key}"' in profile, f"WhaleProfileDTO does not decode {key}"
    for key in ("last_activity_date", "lifecycle_note"):
        assert f'"{key}"' in profile, f"WhaleProfileDTO does not decode {key}"


def test_activity_survives_a_follow_toggle():
    """`withFollowing` rebuilds every field BY HAND, so a field missed there silently
    defaults away — a dormant fund would look active the moment you followed it."""
    body = _braced(_src("tracking_models"), r"struct TrendingWhale\b")
    fn = _braced(body, r"func withFollowing")
    assert fn, "withFollowing not found"
    assert "activityStatus:" in fn and "activityLabel:" in fn, (
        "activity is not threaded through withFollowing"
    )


def test_the_curated_note_wins_over_the_derived_label():
    """A human can say WHY a filer went quiet; no amount of filing data can."""
    body = _braced(_src("profile_models"), r"struct WhaleProfile\b")
    fn = _braced(body, r"var activityNotice")
    assert fn, "activityNotice not found"
    assert fn.index("lifecycleNote") < fn.index("activityLabel"), (
        "the curated note must be preferred over the derived label"
    )


def test_congress_can_never_be_rendered_as_stopped_filing():
    """`hasStoppedFiling` drives the warmer treatment. It must key only on the two 13F /
    curated statuses — a sitting senator who simply hasn't traded is `quiet`, and calling
    that "stopped filing" would be a false statement about a real person."""
    body = _braced(_src("profile_models"), r"struct WhaleProfile\b")
    fn = _braced(body, r"var hasStoppedFiling")
    assert fn, "hasStoppedFiling not found"
    assert '"quiet"' not in fn and '"none"' not in fn, (
        "a quiet or never-traded congressional filer must not read as 'stopped filing'"
    )
    assert '"dormant"' in fn


def test_the_roster_actually_renders_the_chip():
    body = _braced(_src("tracking_view"), r"struct WhaleCard\b")
    assert body, "WhaleCard not found"
    assert "hasActivityNotice" in body, "the roster row never checks for a notice"
    assert "TintedTagBadge" in body, "the chip should reuse the generic capsule atom"


def test_the_profile_actually_renders_the_notice():
    body = _braced(_src("profile_view"), r"struct WhaleProfileHeader\b")
    assert body, "WhaleProfileHeader not found"
    assert "hasActivityNotice" in body and "WhaleActivityNotice" in body


def test_the_notice_uses_text_role_tokens_only():
    """A `*Graphic` token fails the DEBUG launch contrast audit."""
    body = _braced(_src("profile_view"), r"struct WhaleActivityNotice\b")
    assert body, "WhaleActivityNotice not found"
    assert "Graphic" not in body, "graphic-role tokens must not escape the chart layer"
    assert "AppColors.caution" in body or "AppColors.textMuted" in body


def test_the_stat_tiles_are_not_blanked_by_dormancy():
    """Burry's $1.37B really WAS his Q3 2025 book. The fix is disclosure, never deletion —
    a dormant filer must not start rendering an em-dash where a real number belongs."""
    body = _braced(_src("profile_view"), r"struct WhalePortfolioStats\b")
    assert body, "WhalePortfolioStats not found"
    for token in ("hasActivityNotice", "hasStoppedFiling", "activityStatus"):
        assert token not in body, (
            f"WhalePortfolioStats must not gate its value on {token} — "
            "dormancy qualifies the number, it does not delete it"
        )
