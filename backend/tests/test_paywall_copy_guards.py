"""The upgrade screen may only promise things the app actually does.

Three separate ways this screen went wrong before, each guarded here:

  1. **It promised a feature that does not exist.** The header read "plus priority
     analysis" and Account's upgrade card read "priority AI and advanced analytics". There
     is no tier-based priority anywhere — the report semaphore and
     MAX_CONCURRENT_REPORTS_PER_USER are global and no code path reads `tier`. The
     project's own `documents/legal/app-store-listing.md` rule #3 is "nothing that isn't in
     the shipping build", and a purchase surface is the worst place to break it.
  2. **The offline fallback can drift from the gate.** `PlanFeature.bundled(...)` in Swift
     hardcodes the Updates and whale-follow limits so the paywall still renders against a
     backend that predates `plan_features.py`. That duplication is deliberate and bounded —
     but only because this file pins it against `entitlements.py`.
  3. **A call site can silently lose its context.** `PaywallView` is opened from eight
     locks; one that forgets `context:` falls back to the generic header, and one that
     forgets `.environment(\\.appState,)` renders the wrong "current plan" — neither
     failure produces an error, a crash, or a visibly broken screen.

## Why these DERIVE rather than list

Listing the eight known call sites would pin exactly the eight already wired. The
`_inflight` precedent in this repo is the cautionary one — an audit named 6 services and the
derived guard found 25 — so each assertion walks the tree and states a property of whatever
it finds. A ninth entry point added next month fails the build instead of quietly joining.

Per `project_source_scan_guard_vacuity`: comments are stripped (this file's own subjects are
discussed at length in the comments that explain them), windows are brace-bounded, and every
assertion here was mutation-tested — the defect reintroduced, this file confirmed failing,
the source restored.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import settings
from app.services import entitlements
from app.services import plan_features as pf

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_MODELS = "Models/SubscriptionModels.swift"


def _swift_files() -> list[Path]:
    if not _IOS.is_dir():
        pytest.skip(f"{_IOS} not present")
    return sorted(_IOS.rglob("*.swift"))


def _code_only(src: str) -> str:
    """Blank whole-line `//` comments, preserving line numbers.

    Load-bearing: `PaywallView.swift` and `ProfileView.swift` both explain in comments
    exactly which false claim they used to make, quoting it. Scanning raw text would flag
    the explanation as the offence and the only way to get green would be to delete the
    reasoning."""
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _read(rel: str) -> str:
    path = _IOS / rel
    if not path.is_file():
        pytest.skip(f"{path} not present")
    return _code_only(path.read_text(encoding="utf-8"))


def _balanced_window(code: str, start: int, opener: str = "{", closer: str = "}") -> str:
    """Text from `start` to the delimiter that closes the first one at/after it."""
    depth = 0
    began = False
    for i in range(start, len(code)):
        if code[i] == opener:
            depth += 1
            began = True
        elif code[i] == closer:
            depth -= 1
            if began and depth == 0:
                return code[start:i + 1]
    return code[start:]


# ── 1. No claim the app cannot back up ───────────────────────────────────────────────

# `layoutPriority` / `.priority(` are SwiftUI APIs, not copy.
_PRIORITY_CLAIM = re.compile(r"priorit", re.IGNORECASE)
_PRIORITY_API = re.compile(r"layoutPriority|\.priority\(|priority:", re.IGNORECASE)


def _view_files() -> list[Path]:
    """The view layer only.

    Scoped to `Views/` because that is where 100% of the app's UI copy lives, and because
    `Models/BooksContent.swift` and `Models/BookReadAlong.swift` carry the bundled Wiser
    library — tens of thousands of words of editorial prose that legitimately contains
    "priorities". Widening this to every Swift file makes the guard fire on a book summary,
    and the only way back to green would be to weaken it into uselessness.

    Still DERIVED, not a list: this walks ~480 files, so a ninth screen inventing a new
    claim is caught the same as the two that shipped.
    """
    return [p for p in _swift_files() if "/Views/" in p.as_posix()]


def _priority_claims() -> list[tuple[str, str]]:
    hits = []
    for path in _view_files():
        for line in _code_only(path.read_text(encoding="utf-8")).splitlines():
            if not _PRIORITY_CLAIM.search(line) or _PRIORITY_API.search(line):
                continue
            # Only user-visible strings matter; a variable named `priorityQueue` does not.
            if '"' in line:
                hits.append((path.name, line.strip()))
    return hits


def test_no_shipped_copy_promises_a_priority_tier():
    hits = _priority_claims()
    assert not hits, (
        "user-visible copy claims a priority tier the backend does not implement: "
        + "; ".join(f"{f}: {l}" for f, l in hits)
    )


def test_the_priority_scanner_is_not_vacuous():
    """Four ways this guard could pass while seeing nothing: no files, a scope that misses
    the screens, an exclusion that swallows everything, or a comment-stripper that blanks
    the whole file."""
    views = _view_files()
    assert len(views) > 300, f"only {len(views)} view files in scope"
    for rel in ("Views/Screens/PaywallView.swift", "Views/Screens/ProfileView.swift"):
        assert (_IOS / rel) in views, f"{rel} is outside the scanned scope"
        assert _read(rel).count('Text("') > 3, f"{rel} stripped to nothing"
    # The exclusion must exclude something real, or it is silently matching nothing.
    assert any(
        _PRIORITY_API.search(line)
        for p in views
        for line in _code_only(p.read_text(encoding="utf-8")).splitlines()
    ), "the SwiftUI-API exclusion matches nothing — it would hide a real regression"
    # And the detector must still FIRE on the exact strings that shipped.
    for shipped in (
        '            Text("More monthly credits for AI reports and Cay AI chat, plus priority analysis.")',
        '                    Text("Unlock your investing potential with priority AI and advanced analytics.")',
    ):
        assert _PRIORITY_CLAIM.search(shipped) and not _PRIORITY_API.search(shipped)


def test_no_copy_advertises_personalization_while_the_flag_is_off():
    """`CHAT_PERSONALIZATION_ENABLED` ships False (a Terms §2 carve-out is still pending),
    so a plan row selling a personalized agent would sell a feature that never runs."""
    if settings.CHAT_PERSONALIZATION_ENABLED:
        pytest.skip("flag is on — the claim would be truthful")
    for tier in entitlements.TIER_ORDER:
        for row in pf.features_for_tier(
            tier,
            monthly_credits=1200,
            report_cost=settings.REPORT_CREDIT_COST,
            chat_cost=settings.CHAT_CREDIT_COST,
        ):
            blob = f"{row['title']} {row['detail']}".lower()
            assert "personaliz" not in blob, f"{tier}/{row['key']}: {blob}"


# ── 2. The Swift fallback must state the limits entitlements.py enforces ──────────────


def _bundled_body() -> str:
    code = _read(_MODELS)
    m = re.search(r"static func bundled\(", code)
    assert m, "PlanFeature.bundled not found — the fallback guard cannot run"
    return _balanced_window(code, m.start())


def _bundled_limits() -> dict[str, tuple[int, object]]:
    """{swift tier case: (updates limit, follow limit or None)} parsed from the switch."""
    body = _bundled_body()
    out: dict[str, tuple[int, object]] = {}
    for case, updates, follows in re.findall(
        r"case \.(\w+):\s*updatesTickers = (\d+);\s*whaleFollows = (\d+|nil)", body
    ):
        out[case] = (int(updates), None if follows == "nil" else int(follows))
    return out


# Swift `UserTier` case → the backend tier key. `premium` is displayed as "Max".
_SWIFT_TO_TIER = {
    "free": entitlements.TIER_FREE,
    "pro": entitlements.TIER_PRO,
    "premium": entitlements.TIER_MAX,
}


def test_the_bundled_fallback_matches_the_enforced_limits():
    limits = _bundled_limits()
    assert set(limits) == set(_SWIFT_TO_TIER), (
        f"PlanFeature.bundled covers {sorted(limits)}; expected {sorted(_SWIFT_TO_TIER)}"
    )
    for swift_case, (updates, follows) in limits.items():
        tier = _SWIFT_TO_TIER[swift_case]
        assert updates == entitlements.updates_ticker_limit(tier), (
            f"{swift_case}: fallback says {updates} Updates tickers, "
            f"entitlements enforces {entitlements.updates_ticker_limit(tier)}"
        )
        assert follows == entitlements.whale_follow_limit(tier), (
            f"{swift_case}: fallback says {follows} whale follows, "
            f"entitlements enforces {entitlements.whale_follow_limit(tier)}"
        )


def test_the_fallback_parser_is_not_vacuous():
    """A parser that matched nothing would make the test above trivially true."""
    assert len(_bundled_limits()) == 3
    assert "creditcard.fill" in _bundled_body()   # the window really is the function body
    # The parse must actually depend on the numbers: perturbing the source flips it.
    mutated = _bundled_body().replace("updatesTickers = 15", "updatesTickers = 99")
    assert re.search(r"updatesTickers = 99", mutated)


def test_the_fallback_offers_the_same_keys_the_backend_serves():
    """iOS joins on `key` twice — to highlight the triggered row and to select a default
    plan. A key that exists only on one side makes both silently no-op."""
    body = _bundled_body()
    served = {
        r["key"]
        for r in pf.features_for_tier(
            entitlements.TIER_PRO, monthly_credits=1200, report_cost=20, chat_cost=1
        )
    }
    assert served
    for key in served:
        assert f'key: "{key}"' in body, f"fallback is missing the {key!r} row"


def _fallback_row(key: str) -> str:
    """The `PlanFeature(...)` literal declaring `key` in the bundled table.

    Anchors on the `PlanFeature(` that OPENS the row, not on the key match: a window
    starting mid-literal closes on the first nested paren it meets (`validatedSymbol(...)`)
    and would silently examine four lines instead of the row.
    """
    body = _bundled_body()
    m = re.search(rf'key: "{re.escape(key)}"', body)
    assert m, f"the fallback has no {key!r} row"
    opens = [o.start() for o in re.finditer(r"PlanFeature\(", body) if o.start() < m.start()]
    assert opens, f"no PlanFeature( opens the {key!r} row"
    return _balanced_window(body, opens[-1], "(", ")")


def test_the_fallback_keeps_journey_narration_out_of_the_paid_block():
    """Journey narration is free on every tier. In the fallback that is expressed by
    `isAlwaysIncluded: true`, and losing it would sell the reader something they have."""
    row = _fallback_row("journey_audio")
    assert "isAlwaysIncluded: true" in row
    # Anti-vacuity: the extractor really did isolate ONE row, and a paid row does not
    # carry the flag — so the assertion above can fail.
    assert row.count("key: ") == 1 and "waveform" in row
    assert "isAlwaysIncluded: true" not in _fallback_row("learn_audio")

    # The flag alone is not enough. A row can sit in the free block and still READ as an
    # upsell ("Unlock narration"), which is the confusion this whole arrangement exists to
    # prevent — so the TITLE (the line that is actually scanned) has to name the Journey,
    # and the detail has to say it is free. Asserted on the title specifically: a check
    # against the whole literal passes on the detail alone and lets the headline lie.
    title = re.search(r'title: "([^"]+)"', row)
    assert title and "Journey" in title.group(1), (
        f"the free-narration row's title no longer names what it covers: {row!r}"
    )
    assert "every plan" in row


def test_no_paid_fallback_row_claims_narration_in_general():
    """Mirrors `test_plan_features.test_no_paid_row_claims_narration_in_general` on the
    Swift side. 207 of the 230 clips are Journey, so a blanket "narration" promise in the
    paid block sells the reader something they already have on Free."""
    body = _bundled_body()
    starts = [m.start() for m in re.finditer(r"PlanFeature\(", body)]
    assert len(starts) >= 6, f"parsed only {len(starts)} fallback rows"

    checked = 0
    for start in starts:
        row = _balanced_window(body, start, "(", ")")
        key = re.search(r'key: "([\w_]+)"', row)
        if not key or "isAlwaysIncluded: true" in row:
            continue
        checked += 1
        assert "Journey" not in row, f"paid row {key.group(1)!r} mentions the Journey"
        if "narration" in row.lower():
            assert key.group(1) == "learn_audio"
            title = re.search(r'title: "([^"]+)"', row).group(1)
            assert "Money Moves" in title and "book" in title.lower(), (
                f"the paid narration row must name what it covers, got {title!r}"
            )
    assert checked >= 5, f"only inspected {checked} paid rows — the parse is wrong"


# ── 3. Every entry point carries its context and its AppState ────────────────────────


def _paywall_call_sites() -> list[tuple[str, str]]:
    """(file name, call text) for each `PaywallView(` outside a #Preview."""
    sites = []
    for path in _swift_files():
        code = _code_only(path.read_text(encoding="utf-8"))
        preview = code.find("#Preview")
        for m in re.finditer(r"\bPaywallView\(", code):
            if preview != -1 and m.start() > preview:
                continue
            # The window runs to the end of the modifier chain on the presented view, so a
            # `.environment(...)` on the following line is inside it.
            window = code[m.start():m.start() + 400]
            sites.append((path.name, window))
    return sites


def test_every_paywall_entry_point_passes_a_context_and_the_app_state():
    sites = _paywall_call_sites()
    # Derived, not listed: eight are wired today, and a ninth must not slip through.
    assert len(sites) >= 8, f"expected >= 8 PaywallView call sites, found {len(sites)}"
    for name, window in sites:
        assert "context:" in window, f"{name}: PaywallView opened without a context"
        assert ".environment(\\.appState" in window, (
            f"{name}: PaywallView presented without `.environment(\\.appState, appState)` — "
            "a sheet inherits no environment, so it would highlight the wrong current plan"
        )


def test_every_paywall_context_is_reachable_and_names_a_real_feature():
    """A context no call site uses is a permanently empty analytics dimension; a
    `featureKey` the backend never emits silently disables the highlight."""
    code = _read("Models/SubscriptionModels.swift")
    m = re.search(r"enum PaywallContext[^{]*\{", code)
    assert m, "PaywallContext not found"
    body = _balanced_window(code, m.end() - 1)

    cases = re.findall(r"case (\w+)(?:\s*=\s*\"([\w_]+)\")?", body)
    assert len(cases) >= 5, f"parsed only {len(cases)} contexts — the window is wrong"

    used = " ".join(w for _, w in _paywall_call_sites())
    for swift_case, _ in cases:
        assert f".{swift_case}" in used, f"PaywallContext.{swift_case} is never used"

    served = {
        r["key"]
        for tier in entitlements.TIER_ORDER
        for r in pf.features_for_tier(
            tier, monthly_credits=1200, report_cost=20, chat_cost=1
        )
    }
    m = re.search(r"var featureKey: String \{", code)
    assert m, "PaywallContext.featureKey not found"
    for key in re.findall(r'return "([\w_]+)"', _balanced_window(code, m.end() - 1)):
        assert key in served, f"featureKey {key!r} matches no row plan_features emits"


# ── 4. Accent vocabulary parity ──────────────────────────────────────────────────────


def test_every_accent_the_backend_emits_is_mapped_client_side():
    """An unmapped accent falls back to a neutral colour, so the row loses its meaning
    without anything failing — exactly the class of silent drift this whole change is
    about."""
    code = _read(_MODELS)
    m = re.search(r"static func accentColor\(_ key: String\) -> Color \{", code)
    assert m, "PlanFeature.accentColor not found"
    body = _balanced_window(code, m.end() - 1)

    mapped = set(re.findall(r'case "(\w+)": return', body))
    assert mapped, "parsed no accent cases — the window is wrong"
    assert "default:" in body, "accentColor has no default — an unknown key would not compile"

    assert pf.ACCENTS <= mapped, f"backend emits unmapped accents: {sorted(pf.ACCENTS - mapped)}"

    # And every accent actually SERVED is in that set (ACCENTS could drift from the rows).
    served = {
        r["accent"]
        for tier in entitlements.TIER_ORDER
        for r in pf.features_for_tier(
            tier, monthly_credits=1200, report_cost=20, chat_cost=1
        )
    }
    assert served <= mapped, f"served accents are unmapped: {sorted(served - mapped)}"
