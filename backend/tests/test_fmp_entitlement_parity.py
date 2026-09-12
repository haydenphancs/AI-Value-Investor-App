"""Guard: `fmp.py` must not call an FMP endpoint outside the signed licence.

WHY
---
FMP enforces the Order Form's 9 Data Packages as of 2026-09-03: an unpurchased endpoint
answers ``402 Restricted Endpoint``. Before enforcement everything returned 200, so a
blocked call was invisible in dev and in tests. It is not invisible any more — it is a
user-facing outage — and nothing else in the suite would catch a NEW one being added.

HOW THIS AVOIDS GOING VACUOUS
-----------------------------
`.claude/rules/testing.md` §3 and the `project_source_scan_guard_vacuity` memory record
that source-scan guards pass on prose after the code is reverted. Three defences:

1. **AST, not regex.** `ast.parse` discards comments and docstrings by construction, so
   this scan physically cannot match an explanatory comment that happens to name a path.
   It also cannot be fooled by a path mentioned in a different function's string.
2. **The extractor is itself tested.** `test_the_extractor_actually_finds_paths` asserts
   a floor on the number of call sites found and that known-present paths are among them,
   so a broken extractor fails loudly instead of silently finding nothing and passing.
3. **Mutation-tested by hand** (2026-09-03): inserting
   `await self._make_request("biggest-gainers")` into fmp.py turned
   `test_no_new_blocked_endpoint_is_introduced` red; removing it restored green.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Set

import pytest

from app.integrations.fmp_entitlements import (
    INDEX_CONSTITUENT_PATHS,
    BLOCKED_PATHS,
    ENTITLED_PATHS,
    RETIRED_PATHS,
    normalize_path,
)

FMP_SOURCE = Path(__file__).resolve().parents[1] / "app" / "integrations" / "fmp.py"


# Blocked/retired paths `fmp.py` still *defines a wrapper for*. This is DEBT and it may
# only ever SHRINK.
#
# ⚠️ These wrappers are deliberately NOT deleted. A dataset we have not bought is
# HIDDEN, not removed: the method stays, its callers stay, and `_make_request` refuses
# the call early with FMPNotEntitledException. Buying the package later is one line in
# `fmp_entitlements.PURCHASED_PACKAGES` and the feature returns with no code change.
#
# An entry leaves this set when the *product* stops depending on it — i.e. the calling
# service has been moved onto an entitled substitute and the surface degrades honestly.
# It does NOT leave because someone deleted the wrapper.
KNOWN_BLOCKED_IN_USE: Set[str] = {
    # -- Real-time Market Data (Phase 1: profile / company-screener) ---------------
    "quote",
    "batch-quote",
    "stock-price-change",
    # -- Market Performance (Phase 2: rebuild from company-screener) --------------
    "biggest-gainers",
    "biggest-losers",
    "most-actives",
    "sector-performance-snapshot",
    "industry-performance-snapshot",
    # -- Analyst + Calendar (Phase 3) ---------------------------------------------
    "grades",
    "price-target-consensus",
    "dividends",
    "splits",
    # -- Indexes (Phase 4: ETF proxies) -------------------------------------------
    "sp500-constituent",
    "dowjones-constituent",
    "nasdaq-constituent",
    # -- Dropped in negotiation; no substitute, the section is omitted -------------
    "earning-call-transcript",
    "earning-call-transcript-dates",
    # -- Retired by FMP (404, not a licence issue) ---------------------------------
    # senate-disclosure / house-disclosure are GONE from this list on purpose: their
    # callers now use the entitled senate-trades / house-trades instead. Kept as a
    # worked example of the intended direction — an entry leaves by being replaced.
    # Zero callers. Kept per "hide, don't delete" so the shape survives if FMP ever
    # restores them; the guard makes sure nothing starts using one by accident.
    "company-outlook",
    "sec_filings",
    "social-sentiments/change",
    "social-sentiments/historical",
    "stock-news-sentiments-rss-feed",
}


# `fmp.py` has three ways an endpoint reaches `_make_request`, and a scan that saw only
# the first would silently miss 5 paths. All three are resolved below.
#
#   1. a literal:            self._make_request("profile", ...)
#   2. an f-string template: self._make_request(f"historical-chart/{interval}")
#   3. a forwarding helper that takes the endpoint as its first argument, then passes a
#      *variable* to `_make_request`. Two exist, and their call sites carry the literal:
#          self._latest_perf_snapshot("sector-performance-snapshot")
#          self._fetch_congress_pages("senate-latest", limit)
#      A third, `get_index_constituents`, picks the path out of a dict literal in its own
#      body, so its constants are harvested from the enclosing function instead.
FORWARDING_HELPERS = frozenset({"_latest_perf_snapshot", "_fetch_congress_pages"})
REQUEST_FNS = frozenset({"_make_request", "_make_request_impl"})

# `_make_request` itself forwards to `_make_request_impl`; that indirection is the
# delegation seam, not a call site, so it is expected to be "dynamic".
DYNAMIC_ALLOWED_IN = frozenset(
    {"_make_request", "get_index_constituents"} | set(FORWARDING_HELPERS)
)


def _callee(node: ast.Call):
    fn = node.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)


def _literal_arg(node: ast.Call):
    """First argument as a static string, rendering f-string holes as `{}`."""
    if not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
            for v in arg.values
        )
    return None


def _enclosing_functions(tree: ast.AST):
    """Map every node to the name of the function that lexically contains it."""
    owner, stack = {}, []

    def walk(node, current):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current = node.name
        owner[node] = current
        for child in ast.iter_child_nodes(node):
            walk(child, current)

    walk(tree, None)
    return owner


def _extract_request_paths(source: str) -> Set[str]:
    """Every FMP endpoint `fmp.py` can reach, resolved across all three patterns."""
    tree = ast.parse(source)
    owner = _enclosing_functions(tree)
    found: Set[str] = set()
    dynamic_fns: Set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if name in REQUEST_FNS or name in FORWARDING_HELPERS:
            lit = _literal_arg(node)
            if lit is not None:
                found.add(lit)
            elif name in REQUEST_FNS:
                dynamic_fns.add(owner.get(node) or "?")

    # Pattern 3b: a function that builds its path dynamically may hold the candidates as
    # bare constants (a dict literal). Harvest strings there, keeping only those the
    # manifest already recognises so unrelated literals cannot pollute the set.
    known = ENTITLED_PATHS | set(BLOCKED_PATHS) | set(RETIRED_PATHS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if owner.get(node) in dynamic_fns and node.value in known:
                found.add(node.value)

    # Pattern 3c: `get_index_constituents` reads its path from the manifest's
    # INDEX_CONSTITUENT_PATHS (moved there on 2026-09-11 so index_service can consult
    # `is_entitled` BEFORE calling). The product still depends on those paths — they are
    # debt in KNOWN_BLOCKED_IN_USE until the package is bought — so they count as called.
    if "get_index_constituents" in dynamic_fns:
        found |= set(INDEX_CONSTITUENT_PATHS.values())

    return found


def _dynamic_request_functions(source: str) -> Set[str]:
    """Functions passing a variable to `_make_request` — unresolvable by a literal scan."""
    tree = ast.parse(source)
    owner = _enclosing_functions(tree)
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) in REQUEST_FNS:
            if _literal_arg(node) is None:
                out.add(owner.get(node) or "?")
    return out


@pytest.fixture(scope="module")
def called_paths() -> Set[str]:
    return {normalize_path(p) for p in _extract_request_paths(FMP_SOURCE.read_text())}


def test_the_extractor_actually_finds_paths(called_paths: Set[str]) -> None:
    """Anti-vacuity floor: a broken extractor must fail, not silently pass everything."""
    assert len(called_paths) >= 40, (
        f"Only {len(called_paths)} endpoint literals found in fmp.py — the extractor is "
        "probably broken. Every other assertion in this file is vacuous if this is wrong."
    )
    # Paths that are unambiguously present today, one per major package.
    for anchor in ("profile", "income-statement", "etf/holdings", "news/stock"):
        assert anchor in called_paths, f"expected {anchor!r} among the scanned call sites"


def test_dynamic_paths_only_in_known_forwarding_helpers() -> None:
    """A variable path is invisible to a literal scan, so the set that may use one is closed.

    This is the anti-vacuity condition for pattern 3: if someone adds a new helper that
    forwards a computed endpoint, this fails and forces the extractor to be taught about
    it, rather than the new helper's endpoints silently escaping every assertion here.
    """
    offenders = _dynamic_request_functions(FMP_SOURCE.read_text()) - DYNAMIC_ALLOWED_IN
    assert not offenders, (
        f"{sorted(offenders)} pass a computed endpoint to _make_request, so this guard "
        "cannot see which FMP paths they reach. Either pass a literal, or add the "
        "function to FORWARDING_HELPERS/DYNAMIC_ALLOWED_IN and make the extractor "
        "resolve its call sites."
    )


def test_no_new_blocked_endpoint_is_introduced(called_paths: Set[str]) -> None:
    """The core assertion: nothing outside the licence beyond the recorded debt."""
    offenders = {
        p for p in called_paths
        if (p in BLOCKED_PATHS or p in RETIRED_PATHS) and p not in KNOWN_BLOCKED_IN_USE
    }
    assert not offenders, (
        "fmp.py calls FMP endpoint(s) outside the signed licence:\n"
        + "\n".join(
            f"  - {p}: {BLOCKED_PATHS.get(p) or RETIRED_PATHS.get(p)}"
            for p in sorted(offenders)
        )
        + "\n\nThese answer 402 (or 404) in production, so the feature is dead. Do NOT "
          "add them to KNOWN_BLOCKED_IN_USE — that set may only shrink. Point the caller "
          "at an entitled endpoint; app/integrations/fmp_entitlements.SUBSTITUTION names "
          "the replacement for each one."
    )


def test_debt_list_only_shrinks(called_paths: Set[str]) -> None:
    """A path fixed in code must be deleted from the debt list in the same change.

    Otherwise the set rots into a permanent allowlist and stops meaning anything.
    """
    stale = KNOWN_BLOCKED_IN_USE - called_paths
    assert not stale, (
        "KNOWN_BLOCKED_IN_USE lists path(s) fmp.py no longer references: "
        f"{sorted(stale)}. Delete them from the set — a debt list that outlives the debt "
        "rots into a permanent allowlist and stops meaning anything."
    )


def test_debt_list_holds_only_genuinely_blocked_paths(called_paths: Set[str]) -> None:
    """Buying a package must force its entries out of the debt list.

    Found by mutation-testing: adding "Real-time Market Data" to PURCHASED_PACKAGES left
    `quote`, `batch-quote` and `stock-price-change` sitting in KNOWN_BLOCKED_IN_USE while
    they were no longer blocked at all, and every assertion still passed. The debt list
    would then quietly describe a state that had stopped being true — which is the exact
    failure mode this file exists to prevent.
    """
    no_longer_blocked = {
        p for p in KNOWN_BLOCKED_IN_USE
        if p not in BLOCKED_PATHS and p not in RETIRED_PATHS
    }
    assert not no_longer_blocked, (
        f"KNOWN_BLOCKED_IN_USE still lists {sorted(no_longer_blocked)}, but "
        "fmp_entitlements no longer considers them blocked — most likely a package was "
        "added to PURCHASED_PACKAGES. Delete these entries; the feature is live again."
    )


def test_every_called_path_is_classified(called_paths: Set[str]) -> None:
    """No path may be unknown to the manifest — an unclassified path is unreviewed."""
    unknown = called_paths - ENTITLED_PATHS - set(BLOCKED_PATHS) - set(RETIRED_PATHS)
    assert not unknown, (
        f"fmp.py calls path(s) absent from the entitlement manifest: {sorted(unknown)}. "
        "Probe them against the live key and add them to fmp_entitlements.py — an "
        "unclassified path has never been checked against the licence."
    )
