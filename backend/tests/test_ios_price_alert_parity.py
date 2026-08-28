"""The detail-header bell and the Tracking "Price Alerts" list are ONE feature.

TestFlight, build 1.0(6): *"Price Rules must be match with the notification icon in the ticker
when they set it up. Also, how should we add or to let users know they both the same? Add the
bell icon?"* — with a screenshot of ORCL holding an ACTIVE price rule in Tracking → Alerts while
the bell in the ORCL detail header was an unchanged grey outline.

The tester was right, and it could not have been otherwise: **nothing in the app knew a ticker
had alerts until the bell was tapped.** Two view models sat over one endpoint and never spoke,
`AppState` held no alert state, and the view model that DID know was constructed inside the
sheet. Three defects came out of that single shape, and this module pins all three shut:

1. **The bell had no state.** It was the only bare `bell` in the app; every other price-alert
   surface uses `bell.badge`. Now it renders the row's exact glyph and colour.
2. **A rule created in the bell sheet was invisible in Tracking for up to five minutes**
   (`loadIfStale`'s window). Fixed structurally: there is only one array now, so there is no
   second copy left to go stale.
3. **The caption counted ALL rules while the server quota counts only ACTIVE ones**, so
   "20 of 20" showed while a 21st was still creatable.

⚠️ Comments are stripped before every assertion (`_code_only`). That is not a formality here —
the fix is documented in comments that name `bell.badge`, `PriceAlertStore` and `activeCount`
verbatim, so an un-stripped scan would keep passing on prose after a revert. Every scan is also
brace-bounded, and `test_the_parity_scans_are_not_vacuous` proves both helpers still bite.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"

_ROW = _IOS / "Views/Molecules/PriceAlertRuleRow.swift"
_HEADER = _IOS / "Views/Molecules/TickerDetailHeader.swift"
_STORE = _IOS / "Core/Services/PriceAlertStore.swift"
_TAB = _IOS / "Views/Organisms/AlertsTabContent.swift"
_APPSTATE = _IOS / "Core/State/AppState.swift"
_OLD_VM = _IOS / "ViewModels/PriceAlertRulesViewModel.swift"
_SERVICE = Path(__file__).resolve().parents[1] / "app/services/price_alert_service.py"

_DETAIL_SCREENS = [
    "TickerDetailView.swift",
    "IndexDetailView.swift",
    "ETFDetailView.swift",
    "CryptoDetailView.swift",
    "CommodityDetailView.swift",
]
_IDS = [s.replace("DetailView.swift", "") for s in _DETAIL_SCREENS]


def _code_only(src: str) -> str:
    """Strip whole-line comments. See the module docstring — load-bearing."""
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _read(path: Path) -> str:
    # Deliberately NOT pytest.skip: a guard whose subject vanished must fail, not go quiet.
    assert path.exists(), f"{path} is missing — this guard would otherwise pass vacuously"
    return _code_only(path.read_text(encoding="utf-8"))


def _balanced(src: str, opener: str, o: str = "{", c: str = "}") -> str:
    start = src.index(opener) + len(opener) - 1
    assert src[start] == o, f"{opener!r} must end at its opening {o!r}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == o:
            depth += 1
        elif src[i] == c:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced {o!r} after {opener!r}")


def _row_identity() -> tuple[str, str]:
    """The glyph and the active colour the Price Alerts LIST renders, read from the row itself.

    Derived, never hardcoded twice. If the test spelled `"bell.badge"` on both sides, changing
    the row would leave the header behind and the guard would still pass — which is exactly the
    drift this module exists to catch.
    """
    row = _read(_ROW)
    call = _balanced(row, "ActivityRow(", o="(", c=")")
    glyph = re.search(r'systemName:\s*"([^"]+)"', call)
    colour = re.search(r"iconColor:.*?(AppColors\.\w+)", call, re.S)
    assert glyph, "PriceAlertRuleRow no longer names a systemName — scan drifted"
    assert colour, "PriceAlertRuleRow no longer names an AppColors token — scan drifted"
    return glyph.group(1), colour.group(1)


# ── 1. The bell renders the list's identity ──────────────────────────


def test_the_bell_uses_the_same_glyph_and_colour_as_the_list_row():
    """The tester's literal complaint. The row is the source of truth; the header follows it."""
    glyph, colour = _row_identity()
    header = _balanced(_read(_HEADER), "struct TickerDetailHeader: View {")

    assert "hasActiveAlerts" in header, (
        "TickerDetailHeader lost its alert state — the bell is back to being a control that "
        "cannot reflect whether the ticker it sits on has any alerts.")
    assert glyph in header, (
        f"the Price Alerts row renders {glyph!r} but the detail-header bell does not. That "
        f"mismatch is the reported bug: a tester with an active rule read the two surfaces as "
        f"unrelated features.")
    assert colour in header, (
        f"the row tints an active alert {colour} and the header bell does not. Glyph AND "
        f"colour together are the signal — the star beside it changes both for the same reason.")


def test_the_bell_stays_one_image_so_the_tap_target_guard_still_holds():
    """`test_ios_tap_target_guards.py` counts `Image(systemName:` in this file and asserts it
    equals the `.frame`/`.hitSlop()` counts. An `if`/`else` bell with two Images makes those
    counts disagree — a red build for a reason that reads as unrelated. Ternaries, not branches.
    """
    header = _balanced(_read(_HEADER), "struct TickerDetailHeader: View {")
    bells = re.findall(r'Image\(systemName:\s*hasActiveAlerts\s*\?', header)
    assert len(bells) == 1, (
        "the bell must be ONE `Image(systemName: hasActiveAlerts ? … : …)`. Two Images in an "
        "if/else break test_ios_tap_target_guards.py's icon/frame/hit-slop counts.")


@pytest.mark.parametrize("screen", _DETAIL_SCREENS, ids=_IDS)
def test_every_detail_screen_feeds_the_bell(screen: str):
    """Per-screen, because a bell that is right on four screens and wrong on the fifth is the
    same bug the tester reported, just harder to find."""
    src = _read(_IOS / "Views/Screens" / screen)
    call = _balanced(src, "TickerDetailHeader(", o="(", c=")")
    assert "hasActiveAlerts:" in call, (
        f"{screen} does not pass `hasActiveAlerts` to the header, so its bell can never badge.")
    assert "PriceAlertStore.shared" in src, (
        f"{screen} does not observe PriceAlertStore, so the bell would not update when a rule "
        f"is created or deleted elsewhere.")
    # Anti-vacuity: this really is a screen that opens the sheet.
    assert "PriceAlertsSheet(" in src, f"{screen} no longer opens the bell sheet — scan drifted"


# ── 2. One source of truth ───────────────────────────────────────────


def test_there_is_exactly_one_copy_of_the_alert_list():
    """The 5-minute invisibility bug was two arrays over one endpoint. Reintroducing a second
    one brings it straight back — a shorter staleness window would not."""
    assert not _OLD_VM.exists(), (
        "PriceAlertRulesViewModel is back. Its whole body was promoted into PriceAlertStore so "
        "the header bell could read the same array; a second copy re-creates the bug where a "
        "rule created behind the bell stays invisible in Tracking for up to five minutes.")
    tab = _read(_TAB)
    assert "PriceAlertStore.shared" in tab, "the Alerts tab no longer reads the shared store"
    assert "@StateObject private var priceAlerts" not in tab, (
        "the Alerts tab owns its own alert view model again — that is the second copy.")


def test_the_store_is_cleared_when_a_session_ends():
    """auth.md §7 — these are the caller's own rules, and the bell renders straight off them.
    Leaving them would badge the next account's bell with the previous user's alerts."""
    body = _balanced(_read(_APPSTATE), "func discardDataForEndedSession() {")
    assert "PriceAlertStore.shared.reset()" in body, (
        "PriceAlertStore is not reset on session end. Same bug class as the four Learn stores "
        "and WhaleService.")


# ── 3. The count matches the server's own quota ──────────────────────


def test_the_caption_and_the_server_quota_count_the_same_rows():
    """Deliberately ONE test spanning both sides. They disagreed before — the caption counted
    every rule, the quota counted only active ones — so "20 of 20" showed while a 21st was
    still creatable. Split across two tests, they could drift apart again."""
    tab = _read(_TAB)
    section = _balanced(tab, "private func priceAlertsSection() -> some View {")
    assert "activeCount()" in section, (
        "the Price Alerts caption is not counting ACTIVE rules. The server quota "
        "(price_alert_service._count_for_user) counts only `is_active = True`, so any other "
        "basis lets the caption claim the cap is reached while another rule is still creatable.")

    service = _SERVICE.read_text(encoding="utf-8")
    counter = _balanced(service, "def _count_for_user(", o="(", c=")")
    assert counter, "the server-side counter moved — scan drifted"
    body_start = service.index("def _count_for_user(")
    body = service[body_start:body_start + 900]
    assert 'eq("is_active", True)' in body, (
        "the server quota no longer filters on is_active. If that is deliberate, the iOS "
        "caption in AlertsTabContent must change in the same commit — this test spans both "
        "sides precisely so they cannot drift apart again.")


# ── 4. Mutations are never silent ────────────────────────────────────


@pytest.mark.parametrize(
    "fn",
    ["func create(\n", "func toggleActive(_ alert: PriceAlertDTO) async {",
     "func delete(_ alert: PriceAlertDTO) async {"],
    ids=["create", "toggleActive", "delete"],
)
def test_store_mutations_report_their_failures(fn: str):
    """auth.md §6 — a user-initiated mutation that fails silently reads as a UI glitch and
    leaves no trace anywhere. Doubly true now: a failed toggle that silently reverts would also
    flip the bell back with no explanation."""
    store = _read(_STORE)
    # `create` is multi-line, so bound from its signature's opening brace either way.
    start = store.index(fn)
    full = _balanced(store[start:], "{")
    assert "reportMutationFailure" in full, (
        f"{fn} does not report its failure. Banned on these paths: a bare `try?`, a catch that "
        f"only prints, and #if DEBUG-only reporting.")
    assert "try?" not in full, f"{fn} swallows its error with `try?`"
    assert "print(" not in full, f"{fn} reports through print() instead of the failure path"


# ── 5. The dead control stays dead ───────────────────────────────────


def test_the_crypto_screen_has_no_do_nothing_alert_button():
    """CryptoDetailView had a "Set Price Alert" button whose entire handler was a print() — a
    visible control that does nothing, which is the App Review 2.1 risk the header's own comment
    cites. It was also a third name for this one feature."""
    src = _read(_IOS / "Views/Screens/CryptoDetailView.swift")
    assert "handleSetPriceAlert" not in src, "the print()-only alert handler is back"
    assert "Set Price Alert" not in src, (
        "the dead 'Set Price Alert' control is back. The bell in the header is the one entry "
        "point, and it is now the one that shows state.")
    assert "PriceAlertsSheet(" in src, "crypto lost its real alert entry point — scan drifted"


# ── 6. Anti-vacuity ──────────────────────────────────────────────────


def test_the_parity_scans_are_not_vacuous():
    """Both helpers must still bite, and the comment stripping in particular: the files scanned
    here document the fix in comments that name the very tokens asserted on."""
    raw_header = _HEADER.read_text(encoding="utf-8")
    assert "bell.badge" in raw_header, (
        "TickerDetailHeader lost the comment explaining the parity requirement — this control "
        "no longer proves stripping works. Restore it or re-anchor this test.")
    assert "bell.badge" not in _code_only(raw_header).split("Image(systemName:")[0], (
        "comment stripping has stopped working — the glyph assertion could now pass on prose.")

    # The derivation really is a derivation: the row is where the token comes from.
    glyph, colour = _row_identity()
    assert glyph == "bell.badge", (
        f"the row's glyph is now {glyph!r}. That is allowed — but confirm the header followed "
        f"it, which test_the_bell_uses_the_same_glyph_and_colour_as_the_list_row does.")
    assert colour.startswith("AppColors."), "the row colour scan returned something implausible"

    # Brace bounding is bounded on BOTH ends.
    header = _balanced(_read(_HEADER), "struct TickerDetailHeader: View {")
    assert len(header) < len(_read(_HEADER)), "the header block is the whole file"
    assert "#Preview" not in header, "the header block ran past its closing brace"

    # And the store is a real implementation, not a stub.
    store = _read(_STORE)
    assert len(store) > 2000, "PriceAlertStore is too small to be the real thing"
    for required in ("hasActiveAlerts", "activeCount", "loadIfStale", "func reset()"):
        assert required in store, f"PriceAlertStore lost {required} — scan drifted"


# MUTATION_LOG — hand-verified, 2026-08-28 (.claude/rules/testing.md §3):
#   row glyph bell.badge -> bell.fill            => RED (proves parity is derived, not twinned)
#   header bell colour -> AppColors.primaryBlue  => RED
#   drop hasActiveAlerts: from ETFDetailView only=> RED (per-screen, not file-wide)
#   caption activeCount() -> alerts.count        => RED
#   re-add the print()-only Set Price Alert       => RED
#   restore PriceAlertRulesViewModel.swift        => RED
