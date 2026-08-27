"""Source-scan guards for the fast-core first paint on the four asset-detail screens.

TestFlight, build 1.0 (6): *"It's very slow at first time open it."* — a screenshot of
`^GSPC` with the entire screen shimmering. Every one of these screens gated its whole
first paint on ONE aggregated response, and `^GSPC` measured **5.63s cold against 0.14s
warm** in production. The stock screen never had that problem despite the SLOWEST full
build of the lot (DECK, 7.94s cold), because `/overview/core` paints in 0.32s first.

There is no XCTest target, so the invariants that must not regress are pinned from here by
reading the Swift source. Four of them, and each is a specific way this quietly rots:

1. Every load path fires `loadCore()` **in parallel** with the full fetch. Dropping the
   call restores the original bug with no other symptom.
2. `loadCore` keeps its race guard. Without `guard <data> == nil`, a slow core response
   landing after the full one makes the screen step BACKWARDS to a header-only model.
3. `loadCore` stays invisible on failure — `try?`, and no write to `errorMessage`. Core is
   an accelerator; the full response owns the error path, and a core failure surfacing an
   error banner over a screen that is about to load fine is worse than the slowness.
4. The header/chart gate reads `headerData`, and the TAB gates do NOT. Core carries none
   of the tab sections, so pointing a tab gate at it would render an empty tab as though
   it were loaded.

⚠️ Comments are stripped before every assertion. The explanatory comments beside each of
these fixes spell out `loadCore`, `headerData`, `errorMessage` and `coreData` verbatim, so
an un-stripped scan would pass on prose after the code was reverted — the exact vacuity
this repo has been bitten by before. Every scan is also brace-bounded to the declaration
it means to check, and `test_the_scanners_are_not_vacuous` proves the helpers still bite.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_VM = _IOS / "ViewModels"
_SCREENS = _IOS / "Views/Screens"


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails. See the module docstring."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace : i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


# (viewmodel, load function, published full-model property, screen, price header view)
_SCREENS_UNDER_GUARD = [
    ("IndexDetailViewModel.swift", "func loadIndexData()", "indexData",
     "IndexDetailView.swift"),
    ("ETFDetailViewModel.swift", "func loadETFData()", "etfData",
     "ETFDetailView.swift"),
    ("CommodityDetailViewModel.swift", "func loadCommodityData()", "commodityData",
     "CommodityDetailView.swift"),
    ("CryptoDetailViewModel.swift", "func loadCryptoData()", "cryptoData",
     "CryptoDetailView.swift"),
]

_IDS = [s[0].replace("DetailViewModel.swift", "") for s in _SCREENS_UNDER_GUARD]


# ── 1. The load path fires core in parallel ──────────────────────────


@pytest.mark.parametrize("vm,loadfn,data,screen", _SCREENS_UNDER_GUARD, ids=_IDS)
def test_the_load_path_fires_core(vm, loadfn, data, screen):
    body = _decl_block((_VM / vm).read_text(), loadfn)
    assert "loadCore()" in body, (
        f"{vm}: {loadfn} no longer starts the fast-core fetch — the screen is back to "
        f"gating its entire first paint on the aggregated response"
    )
    # `async let` (or an explicit child Task), never a bare `await self.loadCore()` before
    # the full fetch — that would serialise them and make core a DELAY, not an accelerator.
    assert re.search(r"async let \w*[Cc]oreTask", body), (
        f"{vm}: core must be started with `async let` so it runs alongside the full "
        f"fetch, not before it"
    )


# ── 2. The race guard ────────────────────────────────────────────────


@pytest.mark.parametrize("vm,loadfn,data,screen", _SCREENS_UNDER_GUARD, ids=_IDS)
def test_core_never_overwrites_the_full_model(vm, loadfn, data, screen):
    body = _decl_block((_VM / vm).read_text(), "private func loadCore()")
    assert re.search(rf"guard\s+{data}\s*==\s*nil\s+else\s*{{\s*return", body), (
        f"{vm}: loadCore lost its `guard {data} == nil` race guard. A core response "
        f"landing after the full one would make the screen step backwards from the "
        f"complete model to the header-only one."
    )
    # The guard must come BEFORE the write it protects, or it protects nothing.
    guard_at = body.index(f"guard {data} == nil")
    write_at = body.index("coreData = ")
    assert guard_at < write_at, (
        f"{vm}: the race guard is AFTER the `coreData` write, so it guards nothing"
    )


# ── 3. Core failures stay invisible ──────────────────────────────────


@pytest.mark.parametrize("vm,loadfn,data,screen", _SCREENS_UNDER_GUARD, ids=_IDS)
def test_core_failure_is_silent_to_the_user(vm, loadfn, data, screen):
    body = _decl_block((_VM / vm).read_text(), "private func loadCore()")
    assert "try? await" in body, (
        f"{vm}: loadCore must swallow its own failure — the full response is already in "
        f"flight and owns the error path"
    )
    assert "errorMessage" not in body, (
        f"{vm}: loadCore writes errorMessage. A core failure must not put an error over "
        f"a screen that is about to load perfectly well."
    )
    # It must not disturb the full fetch's ordering token either.
    assert "RequestToken" not in body and "RequestGen" not in body, (
        f"{vm}: loadCore touches the full fetch's request token/generation counter"
    )


# ── 4. Only the header gate reads core ───────────────────────────────


@pytest.mark.parametrize("vm,loadfn,data,screen", _SCREENS_UNDER_GUARD, ids=_IDS)
def test_the_header_gate_reads_headerdata(vm, loadfn, data, screen):
    src = _strip_comments((_SCREENS / screen).read_text())
    assert f"viewModel.headerData" in src, (
        f"{screen}: the header/chart gate no longer reads `headerData`, so the fast-core "
        f"slice is fetched and then never rendered"
    )


@pytest.mark.parametrize("vm,loadfn,data,screen", _SCREENS_UNDER_GUARD, ids=_IDS)
def test_the_tab_gates_do_not_read_core(vm, loadfn, data, screen):
    """Core carries the header and chart and NOTHING else. A tab gate reading it would
    render an empty Overview as though it had loaded — the same class of bug as Tracking
    telling a user holding four tickers that they had none."""
    src = _strip_comments((_SCREENS / screen).read_text())
    # Exactly one gate may read headerData: the header/chart one.
    assert src.count("viewModel.headerData") == 1, (
        f"{screen}: {src.count('viewModel.headerData')} gates read `headerData`. Only "
        f"the header/chart gate may — core has none of the tab sections."
    )
    assert "viewModel.coreData" not in src, (
        f"{screen}: the view reads `coreData` directly. Go through `headerData`, which "
        f"is the one place the full-vs-core preference is expressed."
    )


@pytest.mark.parametrize("vm,loadfn,data,screen", _SCREENS_UNDER_GUARD, ids=_IDS)
def test_headerdata_prefers_the_full_model(vm, loadfn, data, screen):
    body = _decl_block((_VM / vm).read_text(), "var headerData:")
    assert re.search(rf"if let {data}\b", body) and "return coreData" in body, (
        f"{vm}: headerData must return the full model when it exists and fall back to "
        f"core — not the other way round"
    )
    # The full model must be returned FIRST.
    assert body.index(f"return {data}") < body.index("return coreData"), (
        f"{vm}: headerData returns coreData before the full model"
    )


# ── 5. Anti-vacuity ──────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    """Every scan above passes trivially if a helper stops matching, so pin the helpers.

    `_strip_comments` in particular: the real files carry comments containing `loadCore`,
    `headerData` and `errorMessage`, so an absence assertion over an un-stripped file
    would be green with the code deleted.
    """
    assert _strip_comments("// errorMessage = x\nlet a = 1\n") == "let a = 1"
    assert _strip_comments("let a = 1 // coreData\n") == "let a = 1"
    assert "loadCore" not in _strip_comments("// calls loadCore() here\n")

    src = "func f() {\n  let a = { 1 }\n}\nfunc g() { BAD }\n"
    assert "BAD" not in _decl_block(src, "func f()")
    assert "BAD" in _decl_block(src, "func g()")

    with pytest.raises(AssertionError):
        _decl_block("func f() {}\n", "func nope()")


def test_every_guarded_screen_actually_exists():
    """A renamed file would make every parametrised case above vanish, and a vanished
    case is not a failure."""
    for vm, loadfn, data, screen in _SCREENS_UNDER_GUARD:
        assert (_VM / vm).exists(), f"{vm} is gone — this file no longer guards it"
        assert (_SCREENS / screen).exists(), f"{screen} is gone"
        assert loadfn in (_VM / vm).read_text(), f"{vm}: {loadfn} was renamed"
