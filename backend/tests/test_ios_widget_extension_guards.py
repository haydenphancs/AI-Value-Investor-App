"""Source-scan guards for the Home Screen widget's extension and its App Group store.

The 2026-09-30 deep check (82 verified findings) traced the "widget doesn't change on sign-in /
sign-out" report to a handful of extension-side defects, each invisible from a single screen:

  * the extension's in-flight market fetch wrote FMP data back AFTER the sign-out wipe, and
    then rendered it on a signed-out phone indefinitely;
  * a Holdings tile with no Holdings snapshot fell back to MARKET movers under "My Holdings";
  * the Market tile rendered a single stock picked out of the whole market;
  * one envelope shared by both processes, read-modify-written, so a market write re-encoded
    the previous account's holdings back over a `clearAll()`;
  * one malformed array element blanked the whole tile;
  * Large lost its session footer whenever the market band was absent;
  * nothing stopped a short label ("S&P 500") being printed beside an ETF's price.

The second review (2026-09-30, the fixes themselves) added:

  * the Lock Screen inline line dropped the AGE before the number, so an old move read as
    today's — every candidate built while aged must now carry it;
  * the market/holdings/short-label scans were per named struct, so a NEW helper view got
    past each of them — they now walk the struct call graph (see `_closure` for its limits);
  * the owner rule in `writePortfolio` was pinned by the bare word `ownerChanged`;
  * the token publish's reload, and the JWT reader's independence (its harness compiles it
    alone), were pinned by nothing;
  * the Small Holdings headline cut its own percentage, the Small Market breadth showed
    "3…", the Holdings states said "today" under "Fri close", a 24/7 headline aged by the
    equity session, and the 45-minute "As of" label waited for a reload.

2026-10-14 adds the cause's context `note` (CRWV −15.46% showed "No clear catalyst" while the
Insights card named the fall): established causes only, drawn whole or dropped, and when room
is short the TAG gives way first, then the note (owner decisions "Headline + context" and
"Drop the tag").

There is no XCTest target, so these are pinned from Python by reading the Swift. Per
`.claude/rules/testing.md` §3 every scan strips comments first and is brace-bound to the
declaration it means. Rule 3 (mutation-test by hand) is EXECUTABLE here: each guard is a pure
`_problems_*` function, and `test_each_guard_bites` applies a plausible regression to an
in-memory COPY of the source and asserts the guard turns red — so a guard that goes vacuous
fails the suite instead of passing quietly. The files on disk are never touched.

Only `swiftc -parse` may run in a subagent; the behaviour itself is proven by the main
session's canonical build and the Simulator (CLAUDE.md, Machine safety).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios"
_PATHS = {
    "widget": _IOS / "CaydexWidgets" / "MoversWidget.swift",
    "intent": _IOS / "CaydexWidgets" / "MoversConfigurationIntent.swift",
    "store": _IOS / "Shared" / "WidgetSnapshotStore.swift",
    "fetcher": _IOS / "Shared" / "WidgetMarketFetcher.swift",
    "schedule": _IOS / "Shared" / "WidgetRefreshSchedule.swift",
    "apiconfig": _IOS / "Shared" / "WidgetAPIConfig.swift",
    "label": _IOS / "Shared" / "WidgetSessionLabel.swift",
    "jwt": _IOS / "Shared" / "WidgetJWT.swift",
}


def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks and `//` comments, blanking lines so positions stay meaningful.

    `\\s//`, not a bare `//`, for trailing comments: a bare one would eat `https://` in a
    literal. The comments beside these fixes quote every token the scans look for.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        out.append("" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    """The brace-balanced body after `header` in comment-stripped source."""
    src = _strip_comments(src)
    start = src.find(header)
    if start == -1:
        raise LookupError(f"{header!r} not found — this scan has drifted")
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    raise LookupError(f"unbalanced braces after {header!r}")


def _struct(src: str, name: str) -> str:
    return _block(src, f"struct {name}")


def _balanced(code: str, open_index: int, opener: str = "{", closer: str = "}") -> str:
    """The balanced span starting at `code[open_index]` (which must be `opener`)."""
    if code[open_index] != opener:
        raise LookupError(f"expected {opener!r} at {open_index}")
    depth = 0
    for i in range(open_index, len(code)):
        if code[i] == opener:
            depth += 1
        elif code[i] == closer:
            depth -= 1
            if depth == 0:
                return code[open_index : i + 1]
    raise LookupError(f"unbalanced {opener}{closer} at {open_index}")


def _all_structs(src: str) -> dict[str, str]:
    """Every `struct Name` → its body, matched on the WHOLE name (`_block`'s `find` would let
    `struct Asset` answer for `struct AssetCell`)."""
    code = _strip_comments(src)
    return {
        m.group(1): _balanced(code, m.end() - 1)
        for m in re.finditer(r"\bstruct\s+(\w+)\b\s*(?::[^{]*)?\{", code)
    }


# A view NAMED in a body — `Foo(…)` or `Foo { … }` — that is a struct in the same file.
_NAMED_TYPE = re.compile(r"\b([A-Z]\w*)\s*[({]")


def _closure(structs: dict[str, str], roots: tuple[str, ...]) -> set[str]:
    """`roots` plus every struct in the file they construct, transitively.

    Makes a scan see what a view DRAWS, not only what its own body says: a new helper view is
    otherwise invisible to every per-struct check (2026-09-30 review). Its limit, stated rather
    than hidden: it follows struct NAMES only — not helper funcs, `@ViewBuilder` properties,
    view modifiers or computed properties on the snapshot — so the gap narrows, not closes.
    """
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen or name not in structs:
            continue
        seen.add(name)
        stack.extend(m.group(1) for m in _NAMED_TYPE.finditer(structs[name]) if m.group(1) in structs)
    return seen


def _sources() -> dict[str, str]:
    return {key: path.read_text(encoding="utf-8") for key, path in _PATHS.items()}


def _safe(fn: Callable[[dict[str, str]], list[str]], src: dict[str, str]) -> list[str]:
    """Run a guard; a declaration that vanished is itself a problem, not a crash."""
    try:
        return fn(src)
    except (LookupError, ValueError) as exc:
        return [f"scan could not find its anchor: {exc}"]


# ── 1. The extension re-checks the widget token after its await ───────────────────────

def _problems_token_recheck(src: dict[str, str]) -> list[str]:
    problems = []
    timeline = _block(src["widget"], "func timeline(for configuration:")
    guard = re.search(
        r"guard\s+let\s+\w+\s*=\s*WidgetAPIConfig\.widgetToken\s+else\s*\{", timeline
    )
    if not guard or guard.start() > timeline.find("var snap = snapshot(for: mode)"):
        problems.append("no signed-out early return before the stored snapshot is read")
    else:
        early = _block(timeline[guard.start():], "else")
        if "signedOutEntry(" not in early or "return Timeline(" not in early:
            problems.append("the no-token branch does not return a signed-out timeline")
    call = timeline.find("WidgetMarketFetcher.fetchMarket()")
    after = timeline[call:] if call != -1 else ""
    recheck = after.find("WidgetAPIConfig.widgetToken")
    use = after.find("snap = fresh")
    store = after.find("writeFromExtension(")
    if recheck == -1 or use == -1 or recheck > use or store == -1 or recheck > store:
        problems.append(
            "the fresh market payload is used or stored without re-reading the widget "
            "token after the await — a sign-out during the fetch puts FMP data back"
        )
    if not re.search(r"WidgetAPIConfig\.widgetToken\s*==\s*nil", after):
        problems.append("the entries are not rebuilt as signed-out when the token went away")

    ext = _block(src["store"], "public static func writeFromExtension(")
    if "fromExtension: true" not in ext or not re.search(r"guard\s+mode\s*==\s*\.market\b", ext):
        problems.append("writeFromExtension no longer fences its writes (market only, token check)")
    market = _block(src["store"], "private static func writeMarket(")
    fence = re.search(r"if\s+fromExtension\s*,\s*WidgetAPIConfig\.widgetToken\s*==\s*nil", market)
    setter = market.find("defaults.set(")
    if not fence or setter == -1 or fence.start() > setter:
        problems.append("writeMarket stores an extension payload without the last-moment token check")
    return problems


# ── 2. Holdings never shows market data ──────────────────────────────────────────────

_HOLDINGS_VIEWS = (
    "HoldingsView", "HoldingsHeader", "HoldingsMedium", "HoldingsSmall", "HoldingsLarge",
    "SidePanel", "MoverColumn", "CountsLine", "RectangularHoldings", "InlineHoldings",
)
_MARKET_TOKENS = ("marketAssets", "marketContext", "marketBrief", "marketRows",
                  "MarketView(", "MarketHeader(", "AssetCell(", "AssetGrid(", "IndexStrip")
_HOLDINGS_ROOTS = ("HoldingsView", "RectangularHoldings", "InlineHoldings")


def _problems_holdings_isolation(src: dict[str, str]) -> list[str]:
    problems = []
    read = _block(src["widget"], "private func snapshot(for mode: MoversMode)")
    arm = read[read.index("case .portfolio:") : read.index("case .market:")]
    if "market" in arm:
        problems.append("the Holdings slot falls back to the market snapshot again")
    structs = _all_structs(src["widget"])
    # The named list AND everything the Holdings roots draw: a new `BandStrip(snapshot:)`
    # reading `marketContext` is caught although no list names it.
    reached = set(_HOLDINGS_VIEWS) | _closure(structs, _HOLDINGS_ROOTS)
    for name in sorted(reached):
        body = structs.get(name)
        if body is None:
            problems.append(f"scan could not find its anchor: struct {name}")
            continue
        leaked = [t for t in _MARKET_TOKENS if t in body]
        if leaked:
            problems.append(f"{name} renders market data {leaked}")
    for name in ("HoldingsView", "RectangularHoldings", "InlineHoldings"):
        if 'mode == "portfolio"' not in _struct(src["widget"], name):
            problems.append(f"{name} no longer checks the payload is portfolio-scoped")
    return problems


# ── 3. Market never renders a mover ──────────────────────────────────────────────────

_MARKET_VIEWS = (
    "MarketView", "MarketHeader", "AssetGrid", "AssetColumn", "AssetCell", "AssetPriceRow",
    "AssetPriceList", "AssetChange", "SectorLeaders", "RectangularMarket", "AssetLine",
    "InlineMarket",
)
_MOVER_TOKENS = re.compile(
    r"headlineMover|runnersUp|topGainers|topLosers|\brisers\b|\bfallers\b|ChangeBadge\(|"
    r"CauseView\(|CompactMoverRow\(|HeadlineRow\(|\bWidgetMover\b"
)


_MARKET_ROOTS = ("MarketView", "RectangularMarket", "InlineMarket")


def _problems_market_has_no_mover(src: dict[str, str]) -> list[str]:
    problems = []
    structs = _all_structs(src["widget"])
    # The named list AND everything the Market roots draw: a new `TopStockLine(snapshot:)`
    # rendering `headlineMover` is caught although no list names it.
    reached = set(_MARKET_VIEWS) | _closure(structs, _MARKET_ROOTS)
    for name in sorted(reached):
        body = structs.get(name)
        if body is None:
            problems.append(f"scan could not find its anchor: struct {name}")
            continue
        hit = _MOVER_TOKENS.search(body)
        if hit:
            problems.append(f"{name} renders a mover ({hit.group(0)}) — Market is not a mover tile")
    rect = _struct(src["widget"], "RectangularView")
    inline = _struct(src["widget"], "InlineView")
    if "RectangularMarket(" not in rect or "InlineMarket(" not in inline:
        problems.append("a Lock Screen family lost its market-specific layout")
    return problems


# ── 4. Per-mode keys, and clearAll takes every one ───────────────────────────────────

def _problems_store_keys(src: dict[str, str]) -> list[str]:
    problems = []
    store = _strip_comments(src["store"])
    config = _block(store, "public enum WidgetSharedConfig")
    values = dict(re.findall(r'static let (\w+) = "([^"]+)"', config))
    if values.get("snapshotKey") != "widget.movers.snapshot.v1":
        problems.append("`snapshotKey` no longer means the legacy v1 envelope")
    keys = ("snapshotKey", "snapshotKeyV2Market", "snapshotKeyV2Portfolio", "modeOverrideKey")
    missing = [k for k in keys if k not in values]
    if missing:
        problems.append(f"WidgetSharedConfig lost {missing}")
    elif len({values[k] for k in keys}) != len(keys):
        problems.append("two widget keys share one value — the modes would overwrite each other")

    clear_all = _block(store, "public static func clearAll()")
    for key in keys:
        if f"removeObject(forKey: WidgetSharedConfig.{key})" not in clear_all:
            problems.append(f"clearAll() no longer removes {key}")

    setters = {
        "writeMarket": "snapshotKeyV2Market",
        "writePortfolio": "snapshotKeyV2Portfolio",
    }
    for fn, key in setters.items():
        body = _block(store, f"private static func {fn}(")
        targets = set(re.findall(r"defaults\.set\([^\n]*forKey:\s*WidgetSharedConfig\.(\w+)\)", body))
        if targets != {key}:
            problems.append(f"{fn} writes {sorted(targets)}, expected only {key}")
    if re.search(r"\.set\([^\n]*forKey:\s*WidgetSharedConfig\.snapshotKey\)", store):
        problems.append("something writes the legacy v1 envelope again")
    if "snapshotKey)" in _block(store, "private static func storedPortfolio("):
        problems.append("the unowned v1 Holdings half is readable again")
    if "removeObject(forKey: WidgetSharedConfig.snapshotKey)" not in _block(
        store, "public static func migrateLegacyIfNeeded()"
    ):
        problems.append("the migration no longer deletes the v1 envelope")
    return problems


# ── 5. Lossy decode ──────────────────────────────────────────────────────────────────

def _problems_lossy_decode(src: dict[str, str]) -> list[str]:
    problems = []
    lossy = _struct(src["store"], "LossyArray")
    loop = lossy.find("while !container.isAtEnd")
    catch = lossy[lossy.find("catch", loop) :] if loop != -1 and "catch" in lossy[loop:] else ""
    if not re.search(r"\bbreak\b", catch):
        problems.append(
            "LossyArray's catch no longer stops — an index that does not advance would spin"
        )
    element = _struct(src["store"], "LossyElement")
    element_catch = element[element.find("catch") :] if "catch" in element else ""
    if "value = nil" not in element_catch or "widgetDecodeLog." not in element_catch:
        problems.append("LossyElement no longer drops AND logs a bad element")
    snapshot = _struct(src["store"], "WidgetMoverSnapshot")
    for case in ("runnersUp", "topGainers", "topLosers", "marketAssets"):
        if not re.search(rf"LossyArray<\w+>\.self,\s*forKey:\s*\.{case}\)", snapshot):
            problems.append(f".{case} is decoded strictly — one bad row blanks the tile")
    return problems


# ── 6. The session footer, on every family ───────────────────────────────────────────

def _split_top_level(items: str) -> list[str]:
    """`a, "b, c", f(d, e)` → three items: commas inside strings or brackets do not split."""
    out, depth, quoted, start = [], 0, False, 0
    for i, ch in enumerate(items):
        if ch == '"' and (i == 0 or items[i - 1] != "\\"):
            quoted = not quoted
        elif not quoted and ch in "([":
            depth += 1
        elif not quoted and ch in ")]":
            depth -= 1
        elif not quoted and depth == 0 and ch == ",":
            out.append(items[start:i])
            start = i + 1
    out.append(items[start:])
    return [s.strip() for s in out if s.strip()]


def _aged_candidates_problems(widget: str, name: str) -> list[str]:
    """While aged, EVERY inline candidate must carry the age.

    The inline Lock Screen line keeps the first candidate that fits, and used to fall back
    from "AAPL +2.10% · Tue 10:05 AM ET" to the bare "AAPL +2.10%" — an old move read as
    today's, on every device once the label grew to "· Sep 23 — open Caydex". So inside the
    `if let aged` branch every candidate (array element or `append` argument) must name
    `aged` or `compact`, and the branch must return rather than fall through to the bare list.
    """
    body = _struct(widget, name)
    m = re.search(r"\bif\s+let\s+aged\s*\{", body)
    if not m:
        return [f"{name} has no `if let aged` branch — its aged line is built with the bare ones"]
    branch = _balanced(body, m.end() - 1)
    problems = []
    if "compactAgedLabel(" not in branch:
        problems.append(f"{name}'s aged branch has no compact label to fall back to")
    if not re.search(r"\breturn\b", branch):
        problems.append(f"{name}'s aged branch falls through to the bare candidates")
    candidates: list[str] = []
    for lit in re.finditer(r"(?:=|\breturn)\s*\[", branch):
        candidates += _split_top_level(_balanced(branch, lit.end() - 1, "[", "]")[1:-1])
    for app in re.finditer(r"\.append\(", branch):
        candidates.append(_balanced(branch, app.end() - 1, "(", ")")[1:-1].strip())
    if not candidates:
        problems.append(f"{name}'s aged branch builds no candidates this scan can read")
    for c in candidates:
        if not re.search(r"\b(?:aged|compact)\b", c):
            problems.append(f"{name} offers {c!r} while aged — a number with no age reads as today's")
    return problems


def _problems_footer_everywhere(src: dict[str, str]) -> list[str]:
    problems = []
    widget = src["widget"]
    root = _block(widget, "struct MoversWidgetView: View")
    body = _block(root, "var body: some View")
    default_arm = body[body.find("default:") :]
    if "homeScreen {" not in default_arm:
        problems.append("the Home Screen families no longer go through homeScreen")
    if "bottomRow(" not in _block(widget, "private func homeScreen<Content: View>"):
        problems.append("homeScreen lost its bottom row")
    if "SessionFooter(" not in _block(widget, "private func bottomRow(compactToggle: Bool)"):
        problems.append("the shared bottom row lost the session footer (Small/Medium/Large)")
    for name in ("RectangularMarket", "RectangularHoldings"):
        if "SessionFooter(" not in _struct(widget, name):
            problems.append(f"{name} lost the session footer")
    for name in ("InlineMarket", "InlineHoldings"):
        if "agedLabel(" not in _struct(widget, name):
            problems.append(f"{name} never says the numbers are from a previous session")
        problems.extend(_aged_candidates_problems(widget, name))
    footer = _struct(widget, "SessionFooter")
    if ".tertiary" in footer or ".foregroundStyle(.secondary)" not in footer:
        problems.append("the footer is not `.secondary` — `.tertiary` was ~1.7:1 in light mode")
    scale = re.search(r"minimumScaleFactor\(([0-9.]+)\)", footer)
    if not scale or float(scale.group(1)) < 0.85:
        problems.append("the footer may scale below 0.85 — illegible is no better than absent")
    return problems


# ── 7. A short label is never printed beside a price ─────────────────────────────────

def _problems_short_label_beside_price(src: dict[str, str]) -> list[str]:
    problems = []
    structs = _all_structs(src["widget"])
    # Per struct that DRAWS `shortLabel`, over everything it draws — a price moved into an
    # `AssetPriceTag(asset:)` sub-view is still beside the label. NOT the closure of
    # `MarketView` or the widget root: those rightly hold AssetCell's short label AND
    # AssetPriceRow's price, in different rows.
    for name, body in structs.items():
        if "shortLabel" not in body:
            continue
        for reached in sorted(_closure(structs, (name,))):
            if re.search(r"\.price\b|priceText", structs[reached]):
                where = name if reached == name else f"{name} (via {reached})"
                problems.append(
                    f"{where} draws `shortLabel` and a price — 'S&P 500 $650' is off by 10x"
                )
    row = _struct(src["widget"], "AssetPriceRow")
    if "asset.price" not in row or "Text(asset.label)" not in row:
        problems.append("AssetPriceRow no longer pairs the price with the full label")
    return problems


# ── 8. Portfolio data is privacy-sensitive ───────────────────────────────────────────

def _problems_privacy(src: dict[str, str]) -> list[str]:
    return [
        f"{name} shows holdings on a locked device (no .privacySensitive())"
        for name in ("HoldingsView", "RectangularHoldings", "InlineHoldings")
        if ".privacySensitive(" not in _struct(src["widget"], name)
    ]


# ── 9. The extension writes only the market slot, and never manages the session ──────

def _problems_extension_writes(src: dict[str, str]) -> list[str]:
    problems = []
    code = _strip_comments(src["widget"]) + _strip_comments(src["intent"])
    for call in ("WidgetSnapshotStore.write(", "clearAll(", "clearPortfolio(",
                 "migrateLegacyIfNeeded(", "publishWidgetToken", "clearWidgetToken"):
        if call in code:
            problems.append(f"the extension calls {call} — only the app may")
    for m in re.finditer(r"writeFromExtension\(mode:\s*\.(\w+)", code):
        if m.group(1) != "market":
            problems.append(f"the extension writes the {m.group(1)} slot")
    return problems


# ── 10. Entry dates roll over at ET midnight ─────────────────────────────────────────

def _problems_render_dates(src: dict[str, str]) -> list[str]:
    problems = []
    timeline = _block(src["widget"], "func timeline(for configuration:")
    if "WidgetRefreshSchedule.renderDates(now: now, reload: reload)" not in timeline:
        problems.append("the timeline builds its own entry dates again")
    if "Calendar.current" in timeline:
        problems.append("the timeline uses the DEVICE calendar — outside ET the rollover is lost")
    dates = _block(src["schedule"], "public static func renderDates(now: Date, reload: Date)")
    if "easternCalendar" not in dates or "Calendar.current" in dates:
        problems.append("renderDates no longer rolls over on the ET calendar")

    # The render at the instant the "As of" label starts speaking (2026-09-30 review): during
    # regular hours `dates` is [now] alone, so without it the label waited for a reload.
    fetch = timeline.find("WidgetMarketFetcher.fetchMarket()")
    boundary = timeline.find("WidgetSessionLabel.ageBoundary(")
    if boundary == -1 or fetch == -1 or boundary < fetch:
        problems.append(
            "the timeline no longer adds the age-boundary entry, or decides it before the "
            "snapshot is final"
        )
    if not re.search(r"\bentryDates\.append\(boundary\)", timeline):
        problems.append("the age-boundary date is computed but never becomes an entry")
    if not re.search(r"let\s+entries\s*:\s*\[MoversEntry\]\s*=\s*entryDates\.map\b", timeline):
        problems.append("the entries are built from the dates WITHOUT the age boundary")
    age = _block(src["label"], "public static func ageBoundary(")
    if "intradayAgeLimit" not in age:
        problems.append("ageBoundary re-derives the 45 minutes instead of using intradayAgeLimit")
    return problems


# ── 11. The write rules ──────────────────────────────────────────────────────────────

def _problems_write_rules(src: dict[str, str]) -> list[str]:
    problems = []
    store = src["store"]
    has_content = _block(store, "public func hasContent(for mode: WidgetSnapshotStore.WidgetMode)")
    market_arm = has_content[has_content.index("case .market:") : has_content.index("case .portfolio:")]
    portfolio_arm = has_content[has_content.index("case .portfolio:") :]
    if "marketAssets" not in market_arm or "marketBrief" not in market_arm:
        problems.append("hasContent(.market) ignores the assets or the brief the tile renders")
    if "holdingsCount != nil" not in portfolio_arm:
        problems.append(
            "hasContent(.portfolio) ignores holdingsCount — an empty or switched group would "
            "be refused and the old group's movers would freeze on the tile again"
        )
    fetch = _block(src["fetcher"], "public static func fetchMarket()")
    if "hasContent(for: .market)" not in fetch:
        problems.append("the fetcher accepts by a different rule than the store keeps")
    if "hasContent(for: .market)" not in _block(store, "private static func writeMarket("):
        problems.append("writeMarket no longer protects a good snapshot from a degraded one")
    portfolio = _block(store, "private static func writePortfolio(")
    scope = re.search(r'if\s+snapshot\.mode\s*!=\s*"portfolio"\s*\{', portfolio)
    if not scope:
        problems.append("a market-scoped payload can land in the Holdings slot")
    else:
        refused = _block(portfolio[scope.start():], "{")
        if "defaults.set(" in refused or "return" not in refused:
            problems.append("the market-scope branch stores the payload instead of refusing it")
    # The OWNER rule, by structure — the bare word `ownerChanged` appears four times, so a
    # neutered derivation or a dropped `!ownerChanged` used to stay green (2026-09-30 review).
    if not re.search(
        r"let\s+ownerChanged\s*=\s*existing\s*!=\s*nil\s*&&\s*"
        r"existing\?\.owner\?\.lowercased\(\)\s*!=\s*owner\?\.lowercased\(\)",
        portfolio,
    ):
        problems.append(
            "ownerChanged is no longer derived from the stored owner vs this write's owner — "
            "a different account's holdings could be kept"
        )
    if not re.search(r"if\s+!ownerChanged\s*,\s*!snapshot\.hasContent\(for:\s*\.portfolio\)", portfolio):
        problems.append(
            "the degraded-payload refusal no longer exempts an owner change — account B's "
            "first (degraded) payload would leave account A's holdings in the slot"
        )
    if scope:
        refused = _block(portfolio[scope.start():], "{")
        owner = re.search(r"if\s+ownerChanged\s*\{", refused)
        if not owner or "removeObject(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio)" not in _balanced(
            refused, owner.end() - 1
        ):
            problems.append(
                "a market-scoped payload for a different account no longer clears that "
                "account's Holdings slot"
            )
    return problems


# ── 12. The token publish reloads, and the JWT reader stands alone ───────────────────

def _problems_token_publish(src: dict[str, str]) -> list[str]:
    """The first refresh after sign-in writes its snapshots BEFORE the token is minted; the
    publish's reload is what turns the signed-out tile into a signed-in one. And the claim
    reader must stay compilable on its own, or `scripts/widget-jwt-check.sh` cannot test it.

    (Not `clearWidgetToken`'s reload: its one caller clears every snapshot next, which
    reloads anyway, so pinning it would be ceremony.)
    """
    problems = []
    publish = _block(src["apiconfig"], "public static func publishWidgetToken(")
    stored = publish.find(".set(token, forKey: widgetTokenKey)")
    reload = publish.find("WidgetSnapshotStore.reloadTimelines()")
    if stored == -1:
        problems.append("publishWidgetToken no longer stores the token")
    if reload == -1 or reload < stored:
        problems.append(
            "publishWidgetToken does not reload the tiles after storing the token — a first "
            "sign-in leaves 'Sign in to Caydex' up until WidgetKit next wakes on its own"
        )
    if "WidgetJWT.subject(of:" not in _block(src["apiconfig"], "public static func jwtSubject(of token: String)"):
        problems.append("jwtSubject decodes on its own again — the harness tests WidgetJWT")
    if "WidgetJWT.expiry(of:" not in _block(src["apiconfig"], "public static var widgetTokenExpiry: Date?"):
        problems.append("widgetTokenExpiry decodes on its own again — the harness tests WidgetJWT")
    jwt = _strip_comments(src["jwt"])
    imports = set(re.findall(r"^\s*import\s+(\w+)", jwt, re.M))
    if not imports <= {"Foundation", "OSLog"}:
        problems.append(f"WidgetJWT imports {sorted(imports - {'Foundation', 'OSLog'})}")
    leaked = re.findall(r"\b(Widget(?:SharedConfig|SharedDefaults|SnapshotStore|APIConfig)|WidgetCenter)\b", jwt)
    if leaked:
        problems.append(
            f"WidgetJWT depends on {sorted(set(leaked))} — the harness compiles it ALONE"
        )
    return problems


# ── 13. The Small headline keeps its number ──────────────────────────────────────────

def _problems_headline_number(src: dict[str, str]) -> list[str]:
    """At ~126pt a "BTCUSD" + "24h" + "+3.42%" row needs ~139pt; the badge, with no
    priority, took the even split and printed "+3.4…"."""
    problems = []
    row = _struct(src["widget"], "HeadlineRow")
    badge = re.search(r"ChangeBadge\([^\n]*\)((?:\s*\.\w+\([^\n]*\))*)", row)
    if not badge:
        problems.append("HeadlineRow no longer draws a ChangeBadge")
    else:
        if ".layoutPriority(" not in badge.group(1):
            problems.append("the headline badge has no layout priority — the number is cut, not the ticker")
        if ".fixedSize(" in badge.group(1):
            problems.append("the headline badge is fixedSize — it overflows the tile at accessibility sizes")
    if not re.search(r"if\s+showsRollingTag\s*,\s*mover\.isRolling24h", row):
        problems.append("HeadlineRow draws the 24h tag unconditionally — Small cannot move it")
    small = _struct(src["widget"], "HoldingsSmall")
    if not re.search(r"HeadlineRow\([^)]*showsRollingTag:\s*false", small):
        problems.append("Small keeps the 24h tag in its headline row")
    if "RollingTag()" not in small:
        problems.append("Small lost the 24h tag altogether — a crypto move reads as the session's")
    # A step down in size before truncation, and the pair's base symbol on screen: rendered,
    # "BTCUSD" beside "+12.34%" lost the whole ticker on a Small SE.
    if not re.search(r"ViewThatFits\(in:\s*\.horizontal\)\s*\{\s*row\(font\)\s*row\(\.headline\)", row):
        problems.append("HeadlineRow has no smaller-font fallback — a long ticker is cut to '…'")
    if "Text(mover.displayTicker)" not in row:
        problems.append("HeadlineRow prints the raw pair ('BTCUSD') instead of the display ticker")
    header = _struct(src["widget"], "HoldingsHeader")
    if not re.search(r'if\s+family\s*==\s*\.systemSmall\s*\{\s*return\s+" · \\\(n\)"\s*\}', header):
        problems.append("Small's header spends its width on 'N holdings' and cuts the group NAME")
    return problems


# ── 14. Small Market breadth drops out instead of "3…" ───────────────────────────────

def _problems_breadth_drops_out(src: dict[str, str]) -> list[str]:
    header = _struct(src["widget"], "MarketHeader")
    options = _block(header, "ViewThatFits(in: .horizontal)")
    lines = [ln.strip() for ln in options[1:-1].splitlines() if ln.strip()]
    if not lines or not re.fullmatch(r"Color\.clear\.frame\(width:\s*0,\s*height:\s*0\)", lines[-1]):
        return [
            "the breadth ViewThatFits has no empty last option — on Small with a sentiment it "
            "squeezes '3/11 up' into '3…'"
        ]
    return []


# ── 15. The Holdings states never say "today" ────────────────────────────────────────

def _problems_no_today_in_states(src: dict[str, str]) -> list[str]:
    """The footer dates the tile; "None today" / "No prices … today" contradicted "Fri close"."""
    widget = src["widget"]
    scopes = {
        "MoverColumn": _struct(widget, "MoverColumn"),
        "CountsLine": _struct(widget, "CountsLine"),
        "emptyMessage": _block(widget, "static func emptyMessage(for snap: WidgetMoverSnapshot)"),
    }
    problems = []
    for where, body in scopes.items():
        for lit in re.findall(r'"(?:[^"\\]|\\.)*"', body):
            if re.search(r"\btoday\b", lit, re.I):
                problems.append(f"{where} says {lit} — beside a 'Fri close' footer that is false")
    return problems


# ── 16. A 24/7 headline ages by the day it was built ─────────────────────────────────

def _problems_rolling_ages_by_build_day(src: dict[str, str]) -> list[str]:
    problems = []
    layout = _block(src["widget"], "private func layout(_ snap: WidgetMoverSnapshot, headline m: WidgetMover)")
    if not re.search(
        r"m\.isRolling24h\s*\?\s*WidgetSessionLabel\.isPriorETDay\(asOf:\s*snap\.asOf", layout
    ):
        problems.append(
            "a round-the-clock headline ages by the EQUITY session again — a Saturday crypto "
            "build (stamped Friday) gets the past-tense wording on Saturday itself"
        )
    cause = _struct(src["widget"], "CauseView")
    if not re.search(r"aged\s*\?\s*\(cause\.detailAged\s*\?\?\s*cause\.detail\)", cause):
        problems.append("CauseView no longer prefers the aged wording when aged")
    return problems


# ── 17. The cause's context note: established only, whole or dropped, tag first ─────

def _modifier_chain(body: str, view: str) -> str | None:
    """The `.modifier(…)` lines chained directly onto the FIRST `view` in `body` — e.g. the
    styling of `Text(note)` — or None when `body` does not draw `view` at all."""
    m = re.search(re.escape(view) + r"((?:\s*\.\w+\([^\n]*\))*)", body)
    return m.group(1) if m else None


def _if_let_else(body: str, name: str) -> tuple[str, str] | None:
    """The `if let <name> { … } else { … }` branches in `body` ("" when there is no else)."""
    m = re.search(rf"\bif\s+let\s+{name}\s*\{{", body)
    if not m:
        return None
    then = _balanced(body, m.end() - 1)
    rest = body[m.end() - 1 + len(then):]
    els = re.match(r"\s*else\s*\{", rest)
    return then, (_balanced(rest, els.end() - 1) if els else "")


_RUNG = re.compile(r"stack\(withTag:\s*(true|false),\s*note:\s*(\w+)\)")
_DETAIL_STYLE = ".foregroundStyle(cause.kind.isEstablished ? .primary : .secondary)"


def _problems_cause_note(src: dict[str, str]) -> list[str]:
    """The backend's `note` beside an established cause (CauseView, 2026-10-14).

    (a) gated on `cause.kind.isEstablished` and on a trimmed, non-empty string; (b) a
    vertical ViewThatFits ladder whose first rung is tag + headline + note, some rung drops
    the TAG but keeps the note, and the LAST rung carries no note (the old layout); the
    headline in a note rung is uncut, or ViewThatFits would accept a rung that cut it;
    (c) the note is secondary caption2 with no line limit and no scale factor — whole or
    dropped, never "fell 4.…"; (d) nothing outside CauseView reads a cause's `.note`, which
    would bypass all of that; (e) the no-note path is the old layout, with no ViewThatFits;
    (f) the gallery sample shows the note on a FICTIONAL company, and the `.none` sample
    stays previewable, with no note.
    """
    problems = []
    widget = src["widget"]
    cause = _struct(widget, "CauseView")

    # (a) The gate.
    prop = re.search(r"private\s+var\s+note\s*:\s*String\?\s*\{", cause)
    if not prop:
        problems.append("CauseView has no `note` property — nothing gates the backend's note")
    else:
        getter = _balanced(cause, prop.end() - 1)
        if not re.search(r"\bguard\s+cause\.kind\.isEstablished\s*,", getter):
            problems.append(
                "the note is not gated on `cause.kind.isEstablished` — under `.none` it would "
                "read as support for a reason that was not found"
            )
        bound = re.search(r"\blet\s+(\w+)\s*=\s*cause\.note\b([^\n]*)", getter)
        if not bound:
            problems.append("the note property no longer reads `cause.note`")
        else:
            name, tail = bound.group(1), bound.group(2)
            if not re.match(r"\?\.trimmingCharacters\(in:\s*\.whitespacesAndNewlines\)", tail):
                problems.append("the note is not trimmed — a blank note would draw an empty grey line")
            if not re.search(rf"!\s*{name}\.isEmpty\b", getter):
                problems.append("an empty note is not refused — it would draw an empty grey line")
            if not re.search(rf"\breturn\s+{name}\b", getter) or not re.search(r"\breturn\s+nil\b", getter):
                problems.append("the note property no longer returns the trimmed note, or nil")

    # (b) + (e) The body: a ladder with a note, the old layout without one.
    body = _block(cause, "var body: some View")
    split = _if_let_else(body, "note")
    if split is None:
        problems.append(
            "CauseView's body no longer branches on the note — without one the old layout "
            "must stay exactly as it was, with no ViewThatFits"
        )
    else:
        with_note, without = split
        vtf = re.search(r"ViewThatFits\(in:\s*\.vertical\)\s*\{", with_note)
        if not vtf:
            problems.append(
                "the note path has no vertical ViewThatFits — a note that does not fit would "
                "be cut, not dropped"
            )
        else:
            ladder = _balanced(with_note, vtf.end() - 1)
            rungs: list[tuple[bool, bool]] = []
            for line in (ln.strip() for ln in ladder[1:-1].splitlines()):
                if not line:
                    continue
                m = _RUNG.fullmatch(line)
                if not m:
                    problems.append(f"a ladder rung this scan cannot read: {line!r}")
                    continue
                rungs.append((m.group(1) == "true", m.group(2) != "nil"))
            if len(rungs) < 2:
                problems.append("the ladder has fewer than two rungs — nothing can give way")
            else:
                if rungs[0] != (True, True):
                    problems.append(
                        "the first rung is not tag + headline + note — the tag is dropped even "
                        "when the tile has room for it"
                    )
                if not any(not tag and note for tag, note in rungs[:-1]):
                    problems.append(
                        "no rung drops the tag and keeps the note — the owner's order (the tag "
                        "gives way first, then the note) is gone"
                    )
                if rungs[-1] != (True, False):
                    problems.append(
                        "the ladder's LAST rung is not tag + headline with no note — when nothing "
                        "fits, ViewThatFits draws the last rung, and a note there is cut mid-number"
                    )
        if not without:
            problems.append("CauseView's no-note path is gone")
        else:
            if "ViewThatFits" in without:
                problems.append("the no-note path is wrapped in ViewThatFits — the old layout changed")
            if not re.fullmatch(r"\{\s*stack\(withTag:\s*true,\s*note:\s*nil\)\s*\}", without):
                problems.append(
                    "the no-note path is not the old layout (`stack(withTag: true, note: nil)`)"
                )
    if body.count("ViewThatFits") > 1:
        problems.append("CauseView's body has more than one ViewThatFits")

    # (b) + (c) + (e) The rung helper.
    helper = re.search(
        r"private\s+func\s+stack\(withTag:\s*Bool,\s*note:\s*String\?\)\s*->\s*some\s+View\s*\{", cause
    )
    if not helper:
        problems.append("CauseView lost its rung helper `stack(withTag:note:)`")
    else:
        rung = _balanced(cause, helper.end() - 1)
        if not re.search(
            r"\bif\s+withTag\s*,\s*showTag\s*,\s*cause\.kind\.isEstablished\s*,\s*"
            r"let\s+tag\s*=\s*cause\.tag\s*,\s*!tag\.isEmpty\s*\{",
            rung,
        ):
            problems.append(
                "the tag is no longer drawn only when the rung asks for it, for an established "
                "cause with a non-empty tag"
            )
        tag_chain = _modifier_chain(rung, "Text(tag)") or ""
        for style in (".font(.caption2.weight(.bold))", ".foregroundStyle(.secondary)", ".textCase(.uppercase)"):
            if style not in tag_chain:
                problems.append(f"the tag lost its style {style}")
        branches = _if_let_else(rung, "note")
        if branches is None:
            problems.append("the rung helper no longer draws the note")
        else:
            note_rung, old_rung = branches
            note_chain = _modifier_chain(note_rung, "Text(note)")
            if note_chain is None:
                problems.append("a note rung does not draw `Text(note)`")
            else:
                if ".foregroundStyle(.secondary)" not in note_chain or ".font(.caption2)" not in note_chain:
                    problems.append("the note is not secondary caption2 — owner: the context in grey")
                if ".lineLimit(" in note_chain or ".minimumScaleFactor(" in note_chain:
                    problems.append(
                        "the note has a line limit or a scale factor — it is shown whole or "
                        "dropped, never cut or shrunk"
                    )
            head = _modifier_chain(note_rung, "Text(detail)")
            if head is None:
                problems.append("a note rung does not draw the headline")
            else:
                if ".lineLimit(" in head or ".minimumScaleFactor(" in head:
                    problems.append(
                        "the headline in a note rung has a line limit — ViewThatFits would "
                        "accept a rung whose headline was cut to fit"
                    )
                if ".fixedSize(horizontal: false, vertical: true)" not in head:
                    problems.append("the headline in a note rung does not report its full height (fixedSize)")
                if _DETAIL_STYLE not in head:
                    problems.append("the headline in a note rung lost its established/none colour")
            old = _modifier_chain(old_rung, "Text(detail)")
            if not old_rung or old is None:
                problems.append("the no-note rung (the old layout) is gone")
            else:
                if ".lineLimit(lineLimit)" not in old or ".minimumScaleFactor(0.8)" not in old:
                    problems.append(
                        "the no-note rung lost `.lineLimit(lineLimit).minimumScaleFactor(0.8)` — "
                        "the old layout changed"
                    )
                if _DETAIL_STYLE not in old:
                    problems.append("the no-note headline lost its established/none colour")
                if re.search(r"\bnote\b", old_rung):
                    problems.append("the no-note rung draws a note")

    # (d) Nobody else reads it.
    structs = _all_structs(widget)
    outside = _strip_comments(widget).replace(structs["CauseView"], "", 1) + _strip_comments(src["intent"])
    if re.search(r"\.note\b", outside):
        problems.append(
            "something outside CauseView reads a cause's `.note` — it would bypass the "
            "established-only, whole-or-dropped gate (a Lock Screen line would cut it)"
        )

    # (f) The samples. Last: a missing sample raises, and that must not mask the rest.
    gallery = _block(widget, "func snapshot(for configuration: MoversConfigurationIntent, in context: Context)")
    if not re.search(r"\(previewMode\s*==\s*\.portfolio\)\s*\?\s*\.previewHoldings\s*:\s*\.preview\b", gallery):
        problems.append("the Holdings gallery card is no longer `.previewHoldings`")
    holdings = _block(widget, "static var previewHoldings: WidgetMoverSnapshot")
    if not re.search(r"kind:\s*\.companyNews\b", holdings) or not re.search(r'\bnote:\s*"', holdings):
        problems.append("the gallery sample no longer shows an insight-backed cause with its note")
    if 'ticker: "ACME"' not in holdings:
        problems.append(
            "the gallery sample's headline is not on the fictional ACME — an invented headline "
            "must never be attributed to a real company"
        )
    none_sample = _block(widget, "static var previewHoldingsNoCause: WidgetMoverSnapshot")
    if not re.search(r"kind:\s*\.none\b", none_sample) or re.search(r"\bnote:", none_sample):
        problems.append("previewHoldingsNoCause is no longer a `.none` cause without a note")
    code = _strip_comments(widget)
    for family in ("Medium", "Large"):
        at = code.find(f'#Preview("{family}"')
        if at == -1:
            problems.append(f"the {family} #Preview is gone")
            continue
        timeline = _block(code[at:], "timeline:")
        for sample in (".previewHoldings,", ".previewHoldingsNoCause,"):
            if sample not in timeline:
                problems.append(f"the {family} preview no longer renders {sample.rstrip(',')}")
    return problems


_GUARDS: dict[str, Callable[[dict[str, str]], list[str]]] = {
    "token_recheck": _problems_token_recheck,
    "holdings_isolation": _problems_holdings_isolation,
    "market_has_no_mover": _problems_market_has_no_mover,
    "store_keys": _problems_store_keys,
    "lossy_decode": _problems_lossy_decode,
    "footer_everywhere": _problems_footer_everywhere,
    "short_label_beside_price": _problems_short_label_beside_price,
    "privacy": _problems_privacy,
    "extension_writes": _problems_extension_writes,
    "render_dates": _problems_render_dates,
    "write_rules": _problems_write_rules,
    "token_publish": _problems_token_publish,
    "headline_number": _problems_headline_number,
    "breadth_drops_out": _problems_breadth_drops_out,
    "no_today_in_states": _problems_no_today_in_states,
    "rolling_ages_by_build_day": _problems_rolling_ages_by_build_day,
    "cause_note": _problems_cause_note,
}


@pytest.mark.parametrize("name", sorted(_GUARDS))
def test_the_current_source_passes(name):
    problems = _safe(_GUARDS[name], _sources())
    assert problems == [], f"{name}: {problems}"


# Each: (guard, file, the exact text to replace, its regression). The text must exist — a
# mutation that no longer applies means the source moved and this table needs updating.
_MUTATIONS: list[tuple[str, str, str, str]] = [
    ("token_recheck", "widget",
     "if WidgetAPIConfig.widgetToken == tokenAtFetch {", "if true {"),
    ("token_recheck", "widget",
     "let signedOut = WidgetAPIConfig.widgetToken == nil", "let signedOut = false"),
    ("token_recheck", "widget",
     "guard let tokenAtFetch = WidgetAPIConfig.widgetToken else {",
     "guard let tokenAtFetch = Optional(\"x\") else {"),
    ("token_recheck", "store",
     "if fromExtension, WidgetAPIConfig.widgetToken == nil {", "if false {"),
    ("token_recheck", "store",
     "reloading: false, fromExtension: true)", "reloading: false, fromExtension: false)"),
    ("holdings_isolation", "widget",
     "            return envelope?.portfolio\n",
     "            return envelope?.portfolio ?? envelope?.market\n"),
    ("holdings_isolation", "widget",
     "                HoldingsHeader(snapshot: snap)\n                if let m = snap.headlineMover {",
     "                HoldingsHeader(snapshot: snap)\n                MarketHeader(sentiment: nil, context: snap.marketContext)\n"
     "                if let m = snap.headlineMover {"),
    ("market_has_no_mover", "widget",
     "            MarketHeader(sentiment: snap.marketBrief?.sentiment, context: snap.marketContext)\n",
     "            MarketHeader(sentiment: snap.marketBrief?.sentiment, context: snap.marketContext)\n"
     "            if let m = snap.headlineMover { ChangeBadge(mover: m) }\n"),
    ("market_has_no_mover", "widget",
     "                AssetCell(asset: assets[i])",
     "                AssetCell(asset: assets[i])\n                CompactMoverRow(mover: x)"),
    ("store_keys", "store",
     "            defaults.removeObject(forKey: WidgetSharedConfig.modeOverrideKey)\n", ""),
    ("store_keys", "store",
     "        defaults.set(data, forKey: WidgetSharedConfig.snapshotKeyV2Market)",
     "        defaults.set(data, forKey: WidgetSharedConfig.snapshotKey)"),
    ("store_keys", "store",
     'public static let snapshotKeyV2Portfolio = "widget.movers.snapshot.v2.portfolio"',
     'public static let snapshotKeyV2Portfolio = "widget.movers.snapshot.v2.market"'),
    ("lossy_decode", "store",
     "                break\n            }\n        }\n        elements = out",
     "                continue\n            }\n        }\n        elements = out"),
    ("lossy_decode", "store",
     "decodeIfPresent(LossyArray<WidgetMover>.self, forKey: .runnersUp)?.elements ?? []",
     "decodeIfPresent([WidgetMover].self, forKey: .runnersUp) ?? []"),
    ("footer_everywhere", "widget",
     "            if let snap = entry.snapshot {\n                SessionFooter(snapshot: snap, now: entry.date)\n            }\n",
     ""),
    ("footer_everywhere", "widget",
     "                .font(.caption2.weight(.semibold))\n                // `.secondary`, not `.tertiary`",
     "                .font(.caption2.weight(.semibold))\n                .foregroundStyle(.tertiary)\n                // `.secondary`, not `.tertiary`"),
    ("short_label_beside_price", "widget",
     "            Text(asset.label)\n                .font(.caption)",
     "            Text(asset.shortLabel ?? asset.label)\n                .font(.caption)"),
    ("privacy", "widget",
     "            // locked device (Lock Screen, StandBy). The market data elsewhere is not.\n"
     "            .privacySensitive()\n",
     "            // locked device (Lock Screen, StandBy). The market data elsewhere is not.\n"),
    ("extension_writes", "widget",
     "WidgetSnapshotStore.writeFromExtension(mode: .market, snapshot: fresh)",
     "WidgetSnapshotStore.write(mode: .market, snapshot: fresh)"),
    ("render_dates", "widget",
     "let dates = WidgetRefreshSchedule.renderDates(now: now, reload: reload)",
     "let dates = [now, Calendar.current.date(byAdding: .minute, value: 20, to: now)!]"),
    ("write_rules", "store",
     "            return headlineMover != nil || holdingsCount != nil",
     "            return headlineMover != nil"),
    ("write_rules", "store",
     '        if snapshot.mode != "portfolio" {',
     '        if snapshot.mode == "never" {'),
    # R23 — the owner rule, three ways to neuter it with the word `ownerChanged` still there.
    ("write_rules", "store",
     "let ownerChanged = existing != nil && existing?.owner?.lowercased() != owner?.lowercased()",
     "let ownerChanged = false"),
    ("write_rules", "store",
     "if !ownerChanged, !snapshot.hasContent(for: .portfolio),",
     "if !snapshot.hasContent(for: .portfolio),"),
    ("write_rules", "store",
     "            if ownerChanged {\n                defaults.removeObject",
     "            if false {\n                defaults.removeObject"),
    # R5 — a bare number back among the aged candidates.
    ("footer_everywhere", "widget",
     "            lines.append(compact)\n            return lines",
     "            lines.append(bare)\n            lines.append(compact)\n            return lines"),
    ("footer_everywhere", "widget",
     '"\\(first) · \\(compact)", compact]',
     '"\\(first) · \\(compact)", first]'),
    ("footer_everywhere", "widget",
     "            ) ?? aged\n            return [",
     "            ) ?? aged\n            _ = compact\n        }\n        if false {\n            return ["),
    # R15 — the age-boundary entry.
    ("render_dates", "widget",
     "let entries: [MoversEntry] = entryDates.map", "let entries: [MoversEntry] = dates.map"),
    ("render_dates", "widget",
     "            entryDates.append(boundary)\n", ""),
    ("render_dates", "label",
     "            boundary = asOf.addingTimeInterval(intradayAgeLimit + 1)",
     "            boundary = asOf.addingTimeInterval(45 * 60 + 1)"),
    # R24 — the publish reload, the delegation, and the reader's independence.
    ("token_publish", "apiconfig",
     "        WidgetSharedDefaults.store?.set(token, forKey: widgetTokenKey)\n"
     "        WidgetSnapshotStore.reloadTimelines()\n",
     "        WidgetSharedDefaults.store?.set(token, forKey: widgetTokenKey)\n"),
    ("token_publish", "apiconfig",
     "        WidgetJWT.subject(of: token)\n", "        nil\n"),
    ("token_publish", "jwt",
     "import OSLog\n", "import OSLog\nimport WidgetKit\n"),
    ("token_publish", "jwt",
     "    private static func isBoolean(",
     "    static var shared: Any? { WidgetSharedDefaults.store }\n\n    private static func isBoolean("),
    # R4 — the headline number.
    ("headline_number", "widget",
     "            ChangeBadge(mover: mover, font: f.weight(.semibold))\n                .layoutPriority(1)\n",
     "            ChangeBadge(mover: mover, font: f.weight(.semibold))\n"),
    ("headline_number", "widget",
     "            ChangeBadge(mover: mover, font: f.weight(.semibold))\n                .layoutPriority(1)\n",
     "            ChangeBadge(mover: mover, font: f.weight(.semibold))\n                .layoutPriority(1)\n"
     "                .fixedSize()\n"),
    # Rendered 2026-09-30: "BTCUSD" + "+12.34%" cut the ticker to "B…" / "…" on Small.
    ("headline_number", "widget", "            row(.headline)\n", ""),
    ("headline_number", "widget", "Text(mover.displayTicker)\n                .font(f.weight(.bold))",
     "Text(mover.ticker)\n                .font(f.weight(.bold))"),
    ("headline_number", "widget", 'if family == .systemSmall { return " · \\(n)" }', ""),
    ("headline_number", "widget",
     "HeadlineRow(mover: headline, font: .title3, showsRollingTag: false)",
     "HeadlineRow(mover: headline, font: .title3, showsRollingTag: true)"),
    # R16 — the empty breadth option.
    ("breadth_drops_out", "widget",
     "                    Color.clear.frame(width: 0, height: 0)\n", ""),
    # R18 — "today" back in a state string.
    ("no_today_in_states", "widget", 'Text("None")', 'Text("None today")'),
    ("no_today_in_states", "widget",
     '"No prices for your \\(n) holdings"', '"No prices for your \\(n) holdings today"'),
    ("no_today_in_states", "widget",
     '"\\(unpriced) no price"', '"\\(unpriced) no price today"'),
    # R6 — the crypto headline aged by the equity session again.
    ("rolling_ages_by_build_day", "widget",
     "WidgetSessionLabel.isPriorETDay(asOf: snap.asOf, now: entry.date)",
     "WidgetSessionLabel.isPriorSession(sessionDate: snap.sessionDate, now: entry.date)"),
    # 2026-10-14 — the cause note. (a) the gate.
    ("cause_note", "widget",
     "        guard cause.kind.isEstablished,\n              let text = cause.note",
     "        guard true,\n              let text = cause.note"),
    ("cause_note", "widget",
     ",\n              !text.isEmpty\n        else { return nil }",
     "\n        else { return nil }"),
    ("cause_note", "widget",
     "let text = cause.note?.trimmingCharacters(in: .whitespacesAndNewlines),",
     "let text = cause.note,"),
    # (b) the ladder: no tag-dropping rung; the tag dropped first; a note on the last rung.
    ("cause_note", "widget", "                stack(withTag: false, note: note)\n", ""),
    ("cause_note", "widget",
     "                stack(withTag: true, note: note)\n                stack(withTag: false, note: note)\n",
     "                stack(withTag: false, note: note)\n                stack(withTag: true, note: note)\n"),
    ("cause_note", "widget",
     "                stack(withTag: true, note: nil)\n            }\n        } else {",
     "                stack(withTag: true, note: note)\n            }\n        } else {"),
    # (b) a note rung that may cut its headline, or no longer reports its full height.
    ("cause_note", "widget",
     "                    .foregroundStyle(cause.kind.isEstablished ? .primary : .secondary)\n"
     "                    .fixedSize(horizontal: false, vertical: true)\n",
     "                    .foregroundStyle(cause.kind.isEstablished ? .primary : .secondary)\n"
     "                    .lineLimit(lineLimit)\n"),
    ("cause_note", "widget",
     "                    .foregroundStyle(cause.kind.isEstablished ? .primary : .secondary)\n"
     "                    .fixedSize(horizontal: false, vertical: true)\n",
     "                    .foregroundStyle(cause.kind.isEstablished ? .primary : .secondary)\n"),
    # (c) the note cut, shrunk, or drawn as primary.
    ("cause_note", "widget",
     "                Text(note)\n                    .font(.caption2)\n",
     "                Text(note)\n                    .font(.caption2)\n                    .lineLimit(2)\n"),
    ("cause_note", "widget",
     "                Text(note)\n                    .font(.caption2)\n",
     "                Text(note)\n                    .font(.caption2)\n                    .minimumScaleFactor(0.8)\n"),
    ("cause_note", "widget",
     "                Text(note)\n                    .font(.caption2)\n                    .foregroundStyle(.secondary)\n",
     "                Text(note)\n                    .font(.caption2)\n                    .foregroundStyle(.primary)\n"),
    # (d) a Lock Screen line reading the note directly.
    ("cause_note", "widget",
     "                        if let c = m.formattedChange { Text(c).font(.caption) }\n",
     "                        if let c = m.formattedChange { Text(c).font(.caption) }\n"
     "                        if let n = m.cause.note { Text(n).font(.caption2) }\n"),
    # (e) the no-note path: wrapped in a ladder, its line limit gone, its tag gone, the
    # branch on the note gone (`if true` compiles: `note` is the Optional property).
    ("cause_note", "widget",
     "        } else {\n            stack(withTag: true, note: nil)\n        }",
     "        } else {\n            ViewThatFits(in: .vertical) {\n                stack(withTag: true, note: nil)\n"
     "            }\n        }"),
    ("cause_note", "widget",
     "                    .lineLimit(lineLimit)\n                    .minimumScaleFactor(0.8)\n", ""),
    ("cause_note", "widget",
     "        } else {\n            stack(withTag: true, note: nil)",
     "        } else {\n            stack(withTag: false, note: nil)"),
    ("cause_note", "widget",
     "        if let note {\n            ViewThatFits", "        if true {\n            ViewThatFits"),
    # (f) the samples.
    ("cause_note", "widget",
     '                kind: .companyNews, tag: "Bearish News",',
     '                kind: .none, tag: "Bearish News",'),
    ("cause_note", "widget",
     'ticker: "ACME", companyName: "Acme Robotics"', 'ticker: "CRWV", companyName: "CoreWeave"'),
    ("cause_note", "widget",
     '                detail: headline, detailAged: headline,\n'
     '                note: "Software - Infrastructure fell 4.0%."\n',
     "                detail: headline, detailAged: headline\n"),
    ("cause_note", "widget",
     "No clear catalyst in that day's news.\"\n",
     "No clear catalyst in that day's news.\",\n                note: \"Aerospace & Defense fell 1.2%.\"\n"),
    ("cause_note", "widget",
     "(previewMode == .portfolio) ? .previewHoldings : .preview",
     "(previewMode == .portfolio) ? .previewHoldingsNoCause : .preview"),
    ("cause_note", "widget",
     "    MoversEntry(date: .now, snapshot: .previewHoldingsNoCause, mode: .portfolio, configuredMode: .portfolio)\n",
     ""),
]


# R22 — regressions that live in a NEW helper struct, invisible to a per-name scan.
# Each: (guard, file, anchor, anchor-with-the-call, the struct appended to the file).
_HELPER_MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("market_has_no_mover", "widget",
     "            MarketHeader(sentiment: snap.marketBrief?.sentiment, context: snap.marketContext)\n",
     "            MarketHeader(sentiment: snap.marketBrief?.sentiment, context: snap.marketContext)\n"
     "            TopStockLine(snapshot: snap)\n",
     "\nprivate struct TopStockLine: View {\n    let snapshot: WidgetMoverSnapshot\n"
     "    var body: some View {\n        if let m = snapshot.headlineMover { Text(m.ticker) }\n    }\n}\n"),
    ("holdings_isolation", "widget",
     "                HoldingsHeader(snapshot: snap)\n",
     "                HoldingsHeader(snapshot: snap)\n                BandStrip(snapshot: snap)\n",
     "\nprivate struct BandStrip: View {\n    let snapshot: WidgetMoverSnapshot\n"
     "    var body: some View {\n        Text(snapshot.marketContext?.text ?? \"\")\n    }\n}\n"),
    ("short_label_beside_price", "widget",
     "            AssetChange(asset: asset, font: .caption2.weight(.semibold))\n",
     "            AssetPriceTag(asset: asset)\n"
     "            AssetChange(asset: asset, font: .caption2.weight(.semibold))\n",
     "\nprivate struct AssetPriceTag: View {\n    let asset: WidgetIndex\n"
     "    var body: some View {\n        if let p = asset.price { Text(String(p)) }\n    }\n}\n"),
    # 2026-10-14 — a new helper drawing the note beside CauseView, outside its gate.
    ("cause_note", "widget",
     "                CauseView(cause: headline.cause, lineLimit: 4, aged: aged)\n",
     "                CauseView(cause: headline.cause, lineLimit: 4, aged: aged)\n"
     "                CauseNoteLine(cause: headline.cause)\n",
     "\nprivate struct CauseNoteLine: View {\n    let cause: WidgetCause\n"
     "    var body: some View {\n        Text(cause.note ?? \"\").lineLimit(1)\n    }\n}\n"),
]


@pytest.mark.parametrize(
    "guard, file, old, new, appended", _HELPER_MUTATIONS,
    ids=[f"{g}-helper" for g, *_rest in _HELPER_MUTATIONS],
)
def test_each_guard_follows_a_new_helper_view(guard, file, old, new, appended):
    """The regression moved into a struct no list names must still turn the guard red."""
    sources = _sources()
    assert sources[file].count(old) == 1, (
        f"helper-mutation anchor for {guard!r} is gone or ambiguous in {file} — update the table"
    )
    mutated = dict(sources)
    mutated[file] = sources[file].replace(old, new, 1) + appended
    problems = _safe(_GUARDS[guard], mutated)
    assert problems, f"{guard} stayed green with the regression in a new helper struct"
    assert not all(p.startswith("scan could not find") for p in problems), problems


@pytest.mark.parametrize(
    "guard, file, old, new", _MUTATIONS,
    ids=[f"{g}-{i}" for i, (g, *_rest) in enumerate(_MUTATIONS)],
)
def test_each_guard_bites(guard, file, old, new):
    """MUTATION_LOG, executable: the regression applied to a COPY must turn its guard red."""
    sources = _sources()
    assert old in sources[file], (
        f"mutation anchor for {guard!r} is gone from {file} — update the table, do not delete it"
    )
    mutated = dict(sources)
    mutated[file] = sources[file].replace(old, new, 1)
    problems = _safe(_GUARDS[guard], mutated)
    assert problems, f"{guard} stayed green on a regression: {new!r}"
    # Killed for the RIGHT reason: a mutation that merely broke an anchor proves nothing
    # about whether the guard would notice the regression it names.
    assert not all(p.startswith("scan could not find") for p in problems), problems


def test_a_comment_cannot_satisfy_a_guard():
    """Control: deleting the real fence but writing it back in a COMMENT must still fail."""
    sources = _sources()
    old = "if fromExtension, WidgetAPIConfig.widgetToken == nil {"
    assert old in sources["store"]
    mutated = dict(sources)
    mutated["store"] = sources["store"].replace(
        old, "// if fromExtension, WidgetAPIConfig.widgetToken == nil {\n        if false {", 1
    )
    assert _safe(_problems_token_recheck, mutated)


def test_the_signed_out_and_empty_states_name_themselves():
    """A sign-out read as a broken widget: the copy was the first-install copy."""
    code = _strip_comments(_sources()["widget"])
    assert "Sign in to Caydex to see the market and your holdings" in code
    assert "Open the app to load today's movers." not in code
    holdings = _struct(_sources()["widget"], "HoldingsView")
    for phrase in ("No holdings in", "No prices for your", "Open Caydex to load your holdings"):
        assert phrase in holdings, f"the Holdings tile lost the {phrase!r} state"
    root = _block(_sources()["widget"], "private var homeContent: some View")
    assert root.index("entry.isSignedOut") < root.index("MarketView(")


def test_the_scanner_helpers_are_not_vacuous():
    assert _strip_comments('let u = "https://x" // trailing') == 'let u = "https://x"'
    assert _strip_comments("/* a { */ b()") == " b()"
    assert _block("struct A {\n  // }\n  x()\n}\ny()", "struct A") == "{\n\n  x()\n}"
    structs = _all_structs("private struct A: View { var b: Int }\nstruct C { }")
    assert set(structs) == {"A", "C"}
    for path in _PATHS.values():
        assert path.exists(), f"{path} moved — every scan above would silently fail to anchor"
