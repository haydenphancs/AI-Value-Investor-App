"""Theme detail plan gate (2026-10-04) — the iOS invariants that must not regress.

The GATE is server-side (tests/test_theme_detail_entitlement.py): a Free caller is never sent
the withheld companies. What can still regress on the client, and is pinned here:

  1. A purchase unlocks the list IN PLACE: the screen reads `AppState` and reloads on
     `entitlementGeneration` (never on `user.tier`, which hydrates from `.free` on every cold
     launch of a paid account).
  2. A stale answer cannot re-lock it: the view model drops a response from a superseded
     load (a slow Free answer landing after the Pro one).
  3. Every cover on the way in and out injects BOTH `AppState` spellings — the screen reads
     `AppState.self`, PaywallView reads `\\.appState`.
  4. A blurred stand-in row never loads a logo: its ticker is made up, and a fetched mark
     could be a real company's under the blur.

Source scans per testing.md: comments stripped and string literals lifted out (the same
scanner the Trillion Club guards use), every check brace-bound to its declaration, and each
guard has an in-memory MUTATION test beside it — the defect re-introduced into the real
source text must make the guard fire.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest

from test_trillion_club_schema_parity import match_brace, scan_swift, type_body

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend" / "ios" / "ios"
DETAIL = IOS / "Views/Screens/ThemeDetailView.swift"
DETAIL_VM = IOS / "ViewModels/ThemeDetailViewModel.swift"
ROW = IOS / "Views/Molecules/ThemeCompanyRow.swift"
HOME = IOS / "Views/Screens/HomeDashboardView.swift"


def _src(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path} not present")
    return path.read_text(encoding="utf-8")


def _code(src: str) -> str:
    return scan_swift(src)[0]


def _closure_after(code: str, anchor: str) -> str:
    at = code.find(anchor)
    assert at != -1, f"anchor {anchor!r} not found — the scan drifted"
    brace = code.index("{", at + len(anchor) - 1)
    return code[brace:match_brace(code, brace + 1) + 1]


# ── 1. Reload on a purchase ─────────────────────────────────────────────────────────────


def reload_violations(src: str) -> List[str]:
    body = type_body(_code(src), "ThemeDetailView")
    out = []
    if not re.search(r"@Environment\(AppState\.self\)\s+private\s+var\s+appState", body):
        out.append("does not read AppState")
    anchor = ".onChange(of: appState.entitlementGeneration)"
    if anchor not in body:
        return out + ["does not observe entitlementGeneration"]
    closure = _closure_after(body, anchor)
    if "viewModel.load()" not in closure:
        out.append("observes entitlementGeneration but does not reload")
    if not re.search(r"guard\s+appState\.auth\.status\s*==\s*\.authenticated\s+else", closure):
        out.append("reloads without the signed-in guard")
    if re.search(r"\.onChange\(of:\s*appState\.user\.tier", body):
        out.append("observes user.tier (fires on every cold launch of a paid account)")
    return out


def test_the_screen_reloads_when_a_purchase_lands():
    assert reload_violations(_src(DETAIL)) == []


@pytest.mark.parametrize("mutation", [
    (".onChange(of: appState.entitlementGeneration)", ".onChange(of: appState.presentationResetToken)"),
    ("            Task { await viewModel.load() }\n        }\n        .inAppBrowser",
     "            Task { }\n        }\n        .inAppBrowser"),
    ("guard appState.auth.status == .authenticated else { return }\n            Task { await viewModel.load() }",
     "Task { await viewModel.load() }"),
    ("@Environment(AppState.self) private var appState", "@Environment(\\.appState) private var appState"),
])
def test_reload_guard_fires(mutation):
    src = _src(DETAIL)
    old, new = mutation
    assert old in src, old
    assert reload_violations(src.replace(old, new, 1)) != []


# ── 2. A superseded load never lands ───────────────────────────────────────────────────


def stale_answer_violations(src: str) -> List[str]:
    body = type_body(_code(src), "ThemeDetailViewModel", kind="class")
    load = _closure_after(body, "func load() async")
    out = []
    if not re.search(r"loadGeneration\s*&\+=\s*1", load):
        out.append("a load does not start a new generation")
    assign = load.find("detail = dto.toDisplay()")
    guard = load.find("guard generation == loadGeneration else { return }")
    if assign == -1:
        out.append("the load no longer assigns the detail (scan drifted)")
    elif guard == -1 or guard > assign:
        out.append("a superseded response can still overwrite the detail")
    return out


def test_a_stale_answer_cannot_re_lock_the_list():
    assert stale_answer_violations(_src(DETAIL_VM)) == []


@pytest.mark.parametrize("mutation", [
    ("            guard generation == loadGeneration else { return }\n            detail = dto.toDisplay()",
     "            detail = dto.toDisplay()"),
    ("loadGeneration &+= 1", "loadGeneration = loadGeneration"),
])
def test_stale_answer_guard_fires(mutation):
    src = _src(DETAIL_VM)
    old, new = mutation
    assert old in src, old
    assert stale_answer_violations(src.replace(old, new, 1)) != []


# ── 3. Both AppState spellings on every cover ──────────────────────────────────────────

_BOTH_ENVS = (".environment(appState)", ".environment(\\.appState, appState)")


def cover_violations(src: str, struct: str, binding: str, view: str) -> List[str]:
    body = type_body(_code(src), struct)
    anchor = f".fullScreenCover(item: ${binding})"
    if anchor not in body:
        return [f"no cover for {binding}"]
    closure = _closure_after(body, anchor)
    out = [] if view in closure else [f"{binding} does not present {view}"]
    return out + [f"{binding} cover lacks {env}" for env in _BOTH_ENVS if env not in closure]


_COVERS = [
    (HOME, "HomeDashboardView", "themeDetailTarget", "ThemeDetailView(slug:"),
    (DETAIL, "ThemeDetailView", "selectedTicker", "TickerDetailView(tickerSymbol:"),
]


@pytest.mark.parametrize("path, struct, binding, view", _COVERS, ids=lambda v: getattr(v, "name", str(v)))
def test_covers_inject_both_app_states(path, struct, binding, view):
    assert cover_violations(_src(path), struct, binding, view) == []


def test_cover_guard_fires_when_an_injection_is_dropped():
    src = _src(HOME)
    anchor = ("ThemeDetailView(slug: target.slug)\n            }\n            .environment(appState)\n"
              "            .environment(\\.appState, appState)")
    assert anchor in src
    dropped = src.replace(anchor, "ThemeDetailView(slug: target.slug)\n            }\n"
                                  "            .environment(appState)", 1)
    assert cover_violations(dropped, "HomeDashboardView", "themeDetailTarget", "ThemeDetailView(slug:")


# ── 4. A stand-in row never loads a logo ───────────────────────────────────────────────


def logo_violations(src: str) -> List[str]:
    body = type_body(_code(src), "ThemeCompanyRow")
    logo = _closure_after(body, "private var logo: some View")
    m = re.search(r"if\s+isLocked\s*\{", logo)
    if not m:
        return ["the logo no longer branches on isLocked"]
    locked_arm = logo[m.end():match_brace(logo, m.end())]
    out = []
    if "CompanyLogoView(" in locked_arm:
        out.append("a locked stand-in builds CompanyLogoView (fetches a logo for a made-up ticker)")
    if "CompanyLogoView(ticker: company.ticker" not in logo[m.end():]:
        out.append("a real row no longer shows its logo (scan drifted)")
    return out


def test_a_stand_in_row_never_loads_a_logo():
    assert logo_violations(_src(ROW)) == []


def test_logo_guard_fires():
    src = _src(ROW)
    old = ("            RoundedRectangle(cornerRadius: 10)\n                .fill(AppColors.mediaSurface)\n"
           "                .frame(width: 40, height: 40)\n        } else {")
    assert old in src
    mutated = src.replace(old, "            CompanyLogoView(ticker: company.ticker, size: 40)\n        } else {", 1)
    assert logo_violations(mutated) != []


def test_the_locked_rows_are_drawn_from_the_count_with_placeholders():
    """The screen draws one stand-in per withheld company from the COUNT the server sent,
    over placeholder words — there is no withheld company on the device to draw from."""
    body = type_body(_code(_src(DETAIL)), "ThemeDetailView")
    rows = _closure_after(body, "private func lockedRows(_ count: Int) -> some View")
    assert "ForEach(0..<count, id: \\.self)" in rows
    assert re.search(r"ThemeCompanyRow\(company:\s*\.lockedPlaceholder\(index\),\s*onTap:\s*openPaywall,\s*isLocked:\s*true\)", rows)
    listing = _closure_after(body, "private func companyList(_ detail: ThemeDetail) -> some View")
    assert "detail.lockedCompanyCount" in listing and "lockedRows(lockedCount)" in listing
