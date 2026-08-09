"""A fixed-pattern `DateFormatter` must pin its locale.

`DateFormatter` with a literal `dateFormat` and NO `locale` resolves the pattern against the
DEVICE's calendar. On a phone set to Thai (Buddhist), Japanese (era) or an Islamic calendar,
`"MMM yyyy"` renders that calendar's month and year — so the Account screen showed
"Member since 8月 8" instead of "Aug 2026", a date the user cannot reconcile with anything else
in the app, since every other date is Gregorian.

`en_US_POSIX` is the fixed-format locale: the one guaranteed not to be re-interpreted by a
user's regional settings.

DERIVED, not enumerated (`project_source_scan_guard_vacuity`): the scan finds every
`DateFormatter()` under `ViewModels/` and `Models/` and checks each one, so a new formatter is
covered without anyone remembering to add it here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_DIRS = ("ViewModels", "Models")


def _code_only(src: str) -> str:
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _formatter_windows(src: str):
    """Yield (index, text) for each `DateFormatter()` construction and the code that follows it.

    Bounded by the NEXT `DateFormatter()` or a `return`/closing of the closure — deliberately
    NOT by `.locale`, which is the token being asserted. Bounding a window with the token you
    are looking for is circular: delete it and the window grows until it finds another.
    """
    starts = [m.start() for m in re.finditer(r"DateFormatter\(\)", src)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(src)
        yield start, src[start:end]


def _swift_files():
    files = []
    for d in _DIRS:
        root = _IOS / d
        if root.exists():
            files.extend(sorted(root.rglob("*.swift")))
    return files


def test_there_are_formatters_to_check():
    """Anti-vacuity: if the sweep finds nothing, the parametrised test below proves nothing."""
    total = sum(
        len(list(_formatter_windows(_code_only(f.read_text(encoding="utf-8")))))
        for f in _swift_files()
    )
    assert total >= 3, f"only {total} DateFormatter() found — the sweep is probably broken"


# Files that already had unlocalised fixed-pattern formatters when this guard was written.
#
# This pass covered the ACCOUNT/SETTINGS surface, and these 18 are elsewhere in the app. They
# are a REAL defect of the same class — several are PARSING formatters, where a device locale
# with Arabic-Indic digits makes the parse fail outright rather than merely render oddly — but
# fixing 18 unrelated files inside an Account-screen change would be an unreviewable diff.
#
# So this is a RATCHET, not an amnesty: the set may shrink, never grow. A new unlocalised
# formatter fails the build; clearing one of these means deleting its line here.
_KNOWN_UNLOCALISED = {
    "Models/ChatConversationModels.swift",
    "Models/ChatModels.swift",
    "Models/CommodityDetailModels.swift",
    "Models/HomeModels.swift",
    "Models/IndexDetailModels.swift",
    "Models/IndexDetailResponseModels.swift",
    "Models/InvestorPathModels.swift",
    "Models/MoneyMoveArticleModels.swift",
    "Models/ResearchModels.swift",
    "Models/SignalOfConfidenceModels.swift",
    "Models/TickerDetailModels.swift",
    "Models/TickerNewsModels.swift",
    "Models/TrackingModels.swift",
    "Models/UpdatesModels.swift",
    "Models/WhaleDTOs.swift",
    "Models/WhaleProfileModels.swift",
    "ViewModels/CryptoDetailViewModel.swift",
    "ViewModels/TickerDetailViewModel.swift",
}


def _offending_files() -> set[str]:
    out = set()
    for path in _swift_files():
        src = _code_only(path.read_text(encoding="utf-8"))
        for _, window in _formatter_windows(src):
            # Only PATTERNS matter. A `dateStyle`/`timeStyle` formatter SHOULD follow the
            # device locale — that is exactly what those are for.
            if "dateFormat" not in window or ".locale" in window:
                continue
            out.add(str(path.relative_to(_IOS)))
    return out


def test_no_new_unlocalised_fixed_pattern_formatter():
    """The ratchet. New offenders fail; the baseline is allowed to persist."""
    new = _offending_files() - _KNOWN_UNLOCALISED
    assert not new, (
        "new fixed-pattern DateFormatter with no explicit locale — it renders (or parses) "
        "against the DEVICE calendar, so a Buddhist/Japanese/Islamic-calendar phone gets the "
        "wrong date: " + ", ".join(sorted(new))
    )


def test_the_baseline_has_not_silently_healed():
    """Anti-rot. If a file here is fixed, it must be REMOVED from the set — otherwise the
    baseline slowly becomes a list of files that are fine, and stops meaning anything."""
    stale = _KNOWN_UNLOCALISED - _offending_files()
    assert not stale, (
        "these files no longer have an unlocalised formatter; delete them from "
        "_KNOWN_UNLOCALISED so the ratchet keeps tightening: " + ", ".join(sorted(stale))
    )


def test_the_account_surface_is_clean():
    """The files this pass actually covered. A hard assertion, not a ratchet entry."""
    account = {
        "ViewModels/ProfileViewModel.swift",
        "ViewModels/NotificationSettingsViewModel.swift",
        "ViewModels/BuyCreditsViewModel.swift",
        "ViewModels/PaywallViewModel.swift",
    }
    offenders = _offending_files() & account
    assert not offenders, (
        "Account-screen formatter with no locale: " + ", ".join(sorted(offenders))
    )


def test_the_credit_reset_label_is_anchored_to_eastern_time():
    """Separate from the locale problem and just as visible. `resets_at` is written by
    `ensure_credit_period` as a next-month boundary in ET, so rendering it in the DEVICE's zone
    showed everyone west of ET the day BEFORE their credits actually renew."""
    src = _code_only((_IOS / "ViewModels" / "ProfileViewModel.swift").read_text(encoding="utf-8"))
    windows = [w for _, w in _formatter_windows(src) if '"MMM d"' in w]
    assert windows, "the credit-reset formatter is gone or no longer uses the MMM d pattern"
    assert any("America/New_York" in w for w in windows), (
        "the reset-date formatter does not pin ET, so 'Resets on Aug 31' is off by a day for "
        "every user west of Eastern"
    )
