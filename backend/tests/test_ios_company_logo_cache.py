"""Company logos are drawn from an in-memory cache, on a rebuilt view's FIRST frame.

TestFlight 1.0 (9): "the whole screen here is blink" on the Reports screen. One cause was the
logo tile. `CompanyLogoView` drew the FMP CDN logo through `AsyncImage`, which keeps its phase
in per-view state: every NEW view starts at `.empty` and draws the initials tile, even with the
PNG already in `URLCache`. SwiftUI makes a new view on LazyVStack recycling, on the
Research ⇄ Reports segment (an if/else that destroys the list) and on re-entering a report.
The CDN sends only a weak ETag (no Cache-Control), so `URLCache` revalidates and on mobile data
the initials showed for a visible beat — every logo flipped to a letter and back.

Fix (pinned here):

* `Core/Services/CompanyLogoCache.swift` — a `@MainActor final class` with a SYNCHRONOUS
  `image(for:)` over a byte-capped `[String: UIImage]` (NOT `NSCache`: the design-doc parity
  test pins "NO NSCache" across the iOS tree, comments included). `load(_:)` short-circuits on a
  hit or a remembered no-logo, shares one in-flight fetch per symbol, runs the fetch detached,
  stores only `.image` verdicts and remembers `.noLogo` (404/410, or an undecodable 2xx body)
  for a few minutes. Offline / 429 / 5xx are `.failed` and never remembered.
* `Views/Atoms/CompanyLogoView.swift` — `body` reads `CompanyLogoCache.shared.image(for:)`
  synchronously (through `remoteImage(for:)`), then the logo this view loaded (`fetched`, tagged
  with its symbol); `.task(id: remoteSymbol)` fills the cache. The init, the white chip and the
  initials placeholder are byte-for-byte what they were (the trillion-club guards pin them too).
  `store(_:for:)` writes through `images.updateValue`, moves a REPLACED symbol to the back of
  `order` (so the newest entry is never evicted as the "oldest"), counts decoded bytes through
  `cost(of:)` and evicts oldest-first `while bytes > Self.maxBytes, order.count > 1`.
* The Reports screen itself: the row (`ReportCard`) and the open report's header
  (`ReportHeaderBar`) draw through `CompanyLogoView`, never an `AsyncImage`.
* A tree-wide scan: the CDN URL (any `financialmodelingprep.com/` path, the legacy
  `/image-stock/` logo included) is built in exactly one place — the cache. A second scan finds
  no `AsyncImage` beside a logo URL anywhere; the one declared exemption is `WhaleAvatarView`,
  the whale's own avatar (`avatar_url`), not a company logo.
* The two former holdouts (2026-10-02) read the cache the way the atom does, each keeping its
  own tile: `TradeGroupDetailView.TradeTickerLogo` (48 pt, keyed on the normalised ticker) and
  `WhaleProfileView.WhaleTickerIcon` (40 pt). The whale icon draws the server's `logo_url` —
  FMP's profile `image` — so it is keyed on the symbol in that FMP file name
  (`CompanyLogoCache.symbol(forLogoURL:)`: the CDN's `/symbol/<SYM>.png` or the legacy
  `/image-stock/<SYM>.png`), sharing the entry with every `CompanyLogoView` for that ticker. No
  URL keeps the letter tile with no fetch (as before); a non-FMP URL keeps it too, with a
  warning — swapping in the CDN file for a symbol could draw a different security's logo.

There is no XCTest target (testing.md §3), so this pins the Swift source: comments are stripped
before every assertion (the fix's own comments name `AsyncImage`, `NSCache` and `Task`), every
check is brace-bounded to the declaration it means, a presence check with its own message runs
before every `_block` that depends on it, and each test asserts it is reading the real
declaration (anti-vacuity).

Mutation-tested IN MEMORY (``pathlib.Path.read_text`` monkeypatched for the one target file —
the real Swift files are never written, because other sessions read the tree concurrently).
The table runs on every pass as ``test_each_mutation_is_killed``; each anchor must occur exactly
once, and each mutation must fail with the assertion message that names it
(``pytest.raises(match=…)``), so a mutation cannot "pass" by tripping an unrelated earlier check.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_ATOM = _IOS / "Views" / "Atoms" / "CompanyLogoView.swift"
_CACHE = _IOS / "Core" / "Services" / "CompanyLogoCache.swift"
_TRADE = _IOS / "Views" / "Screens" / "TradeGroupDetailView.swift"
_WIDGET = _IOS.parent / "CaydexWidgets" / "MoversWidget.swift"
_WHALE = _IOS / "Views" / "Screens" / "WhaleProfileView.swift"
_REPORT_CARD = _IOS / "Views" / "Molecules" / "ReportCard.swift"
_REPORT_HEADER = _IOS / "Views" / "Molecules" / "ReportHeaderBar.swift"
_ASSET_ROW = _IOS / "Views" / "Molecules" / "AssetRow.swift"

_CDN = "images.financialmodelingprep.com/symbol/"
# Any path on FMP's hosts — the CDN above AND the legacy `financialmodelingprep.com/image-stock/`
# logo path. Wider than `_CDN` on purpose: the app never calls FMP directly (the backend does),
# so an FMP path in Swift is a logo URL. `DataSourcesView`'s attribution link has no trailing
# slash (`"https://financialmodelingprep.com"`), so it does not match.
_FMP_PATH = "financialmodelingprep.com/"
# A company-logo URL in a file: one the app builds (the CDN), or one the server sends
# (`logoURL` / `logoUrl` / `logo_url`, which the backend fills from the FMP profile `image`).
_LOGO_URL = re.compile(r"(?i)logo_?url|" + re.escape(_CDN))
_INIT = ("ticker: String, imageName: String? = nil, size: CGFloat = 40, "
         "gradientColors: [String]? = nil, fallbackText: String? = nil")
# The old `AsyncImage` `.success` chip, modifier for modifier.
_CHIP = [
    "Image(uiImage: logo)",
    ".resizable()",
    ".aspectRatio(contentMode: .fit)",
    ".padding(size * 0.16)",
    ".frame(width: size, height: size)",
    ".background(AppColors.mediaSurface)",
    ".accessibilityIgnoresInvertColors()",
    ".clipShape(RoundedRectangle(cornerRadius: size * 0.25))",
]
_ATOM_COLOURS = {"cardBackgroundLight", "cardBackground", "textOnAccent", "textPrimary", "mediaSurface"}


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the fix's own comments name `AsyncImage`, `NSCache` and `Task` while
    explaining why they are gone, so an un-stripped scan for their ABSENCE fails on prose and
    a scan for their PRESENCE passes on a revert whose comment survived. A tail needs leading
    whitespace, so a `https://` inside a string literal is not cut.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", raw))
    return "\n".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _block(src: str, header: str, open_: str = "{", close: str = "}") -> str:
    """The balanced `open_`…`close` body that follows the ONLY `header` (a literal prefix).

    `[`/`]` bound an array literal, `{`/`}` a declaration.
    """
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    return _balanced(src, src.index(open_, src.index(header) + len(header)), open_, close)


def _balanced(src: str, start: int, open_: str = "{", close: str = "}") -> str:
    """The balanced `open_`…`close` body that opens at `src[start]`."""
    assert src[start] == open_, f"expected `{open_}` at offset {start}, found {src[start]!r}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced `{open_}{close}` from offset {start}")


def _norm(src: str) -> str:
    """Whitespace-collapsed, so a re-wrapped line still matches its literal."""
    return re.sub(r"\s+", " ", src).strip()


def _has(src: str, literal: str) -> bool:
    return _norm(literal) in _norm(src)


def _decl(src: str, header: str, what: str) -> str:
    """`_block`, after a presence check whose message names the declaration it needs."""
    n = src.count(header)
    assert n == 1, f"{what} is gone or duplicated: expected one `{header}`, found {n}"
    return _block(src, header)


def _atom() -> str:
    return _decl(_code(_ATOM), "struct CompanyLogoView: View", "the CompanyLogoView atom")


def _cache() -> str:
    return _decl(_code(_CACHE), "final class CompanyLogoCache", "the CompanyLogoCache class")


def _returns(block: str) -> list[str]:
    """The `.case` names a block returns, in source order (`return .noLogo` → `noLogo`)."""
    return re.findall(r"\breturn\s+\.(\w+)", block)


def _arms(switch_block: str) -> list[tuple[str, str]]:
    """`switch` arms as (case name, whitespace-normalised body), in order — a list, so a
    duplicated case is visible rather than collapsed."""
    inner = switch_block.strip()[1:-1]
    parts = re.split(r"\bcase\s+\.(\w+)(?:\([^)]*\))?\s*:", inner)
    assert not parts[0].strip(), f"code before the first `case` of the switch: {parts[0]!r}"
    return [(parts[i], _norm(parts[i + 1])) for i in range(1, len(parts), 2)]


# ── 1. The atom draws a cached logo synchronously ────────────────────────────


def test_atom_resolves_the_logo_synchronously():
    """`body` must reach the cache WITHOUT awaiting anything, so a rebuilt view's first frame
    is the logo, not the initials."""
    atom = _atom()
    assert all(t in atom for t in ("initialsView", "fallbackGradient", "bundledImage")) and len(atom) > 2000, (
        "not the real CompanyLogoView (initialsView / fallbackGradient / bundledImage missing)")

    # Whole file, comments stripped: a helper view in the same file must not bring it back.
    assert "AsyncImage" not in _code(_ATOM), (
        "CompanyLogoView draws through AsyncImage again — every rebuilt view (LazyVStack recycling, "
        "the Research ⇄ Reports segment, re-entering a report) restarts at `.empty` and flashes "
        "the initials tile")

    body = _decl(atom, "var body: some View", "CompanyLogoView.body")
    assert re.search(
        r"else\s+if\s+let\s+symbol\s*=\s*remoteSymbol\s*\{\s*"
        r"if\s+let\s+logo\s*=\s*remoteImage\(for:\s*symbol\)\s*\{\s*remoteLogo\(logo\)\s*\}\s*"
        r"else\s*\{\s*initialsView\s*\}\s*\}",
        body), (
        "the remote branch must draw remoteImage(for: symbol) through remoteLogo(_:), else "
        "initialsView — a logo read anywhere else (e.g. only from the task's result) is not on "
        "the rebuilt view's first frame")
    assert re.search(r"ZStack\s*\{\s*if\s+let\s+bundledImage\s*\{", body), (
        "a bundled asset no longer wins: `if let bundledImage` must be the first branch of the "
        "ZStack, ahead of the remote logo")

    assert re.search(r"\bfunc\s+remoteImage\(\s*for\s+symbol\s*:\s*String\s*\)", atom), (
        "remoteImage(for:) is gone — body has no synchronous resolver to draw from")
    assert re.search(r"\bfunc\s+remoteImage\(\s*for\s+symbol\s*:\s*String\s*\)\s*->\s*UIImage\?\s*\{", atom), (
        "remoteImage(for:) must stay synchronous — its signature must be "
        "`func remoteImage(for symbol: String) -> UIImage?` (no async / throws)")
    resolver = _block(atom, "func remoteImage(")
    assert not re.search(r"\b(await|async|Task)\b", resolver), (
        "remoteImage(for:) must stay synchronous — its body awaits or starts a Task, so body "
        "cannot draw the logo on the first frame")
    assert re.match(
        r"\{\s*if\s+let\s+cached\s*=\s*CompanyLogoCache\.shared\.image\(for:\s*symbol\)\s*\{\s*return\s+cached\s*\}",
        resolver), (
        "remoteImage(for:) must read CompanyLogoCache.shared.image(for: symbol) FIRST — that "
        "process-wide read is what puts a logo shown this session on a NEW view's first frame")
    assert re.search(
        r"guard\s+let\s+fetched\s*,\s*fetched\.symbol\s*==\s*symbol\s+else\s*\{\s*return\s+nil\s*\}\s*"
        r"return\s+fetched\.image\s*\}\s*$",
        resolver), (
        "the held logo must be matched to the symbol (`guard let fetched, fetched.symbol == symbol "
        "else { return nil }`) — a view reused for another ticker would draw the old company's logo")


def test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol():
    atom = _atom()
    assert re.search(
        r"@State\s+private\s+var\s+fetched\s*:\s*\(\s*symbol\s*:\s*String\s*,\s*image\s*:\s*UIImage\s*\)\?",
        atom), (
        "the view must hold the logo it loaded, tagged with its symbol: "
        "`@State private var fetched: (symbol: String, image: UIImage)?` — it keeps the logo on "
        "screen after the cache evicts it, and the tag stops a reused view drawing the old one")

    remote_symbol = _decl(atom, "private var remoteSymbol: String?", "CompanyLogoView.remoteSymbol")
    assert re.fullmatch(
        r"\{\s*bundledImage\s*==\s*nil\s*\?\s*CompanyLogoCache\.symbol\(for:\s*ticker\)\s*:\s*nil\s*\}",
        remote_symbol), (
        "remoteSymbol must be nil while a bundled asset is drawn "
        "(`bundledImage == nil ? CompanyLogoCache.symbol(for: ticker) : nil`) — else that path "
        "fetches a logo it never shows")

    body = _decl(atom, "var body: some View", "CompanyLogoView.body")
    assert len(re.findall(r"\.task\b", atom)) == 1 and ".task(id: remoteSymbol) {" in body, (
        "the logo load must be `.task(id: remoteSymbol)` — the ONE task, restarted when the ticker "
        "changes and cancelled with the view")
    assert not re.search(r"\bTask(?:\.detached)?\s*(?:\([^)]*\))?\s*\{", atom), (
        "CompanyLogoView starts its own unstructured Task — the only load is the cancellable "
        "`.task(id: remoteSymbol)`")
    zstack = _decl(body, "ZStack", "the body's ZStack")
    assert "remoteImage(for: symbol)" in zstack and body.index(zstack) < body.index(".task(id: remoteSymbol)"), (
        "the synchronous remoteImage(for:) draw must be in the ZStack, ahead of the .task load")

    task = _block(body, ".task(id: remoteSymbol)")
    assert re.search(r"let\s+image\s*=\s*await\s+CompanyLogoCache\.shared\.load\(symbol\)", task), (
        "the task must fill through CompanyLogoCache.load — the shared cache is what a rebuilt "
        "view reads on its first frame")
    assert re.search(r"\bfetched\s*=\s*\(\s*symbol:\s*symbol\s*,\s*image:\s*image\s*\)", task), (
        "the task must keep the loaded logo in `fetched`, tagged with the symbol it loaded")
    assert re.search(r"!\s*Task\.isCancelled", task), (
        "the task must not write `fetched` after it was cancelled (`!Task.isCancelled`) — the "
        "ticker changed or the view went away")
    assert re.search(
        r"if\s+fetched\?\.symbol\s*!=\s*symbol\s*\{\s*fetched\s*=\s*\(\s*symbol:\s*symbol\s*,\s*image:\s*image\s*\)\s*\}",
        task), (
        "assign `fetched` only when the symbol changes — a tuple @State cannot be compared, so a "
        "cache hit re-assigning it costs an extra body pass on every row appearance")
    # Presence of `!Task.isCancelled` (above) says nothing about WHERE it sits: a `fetched` write
    # ahead of the guard is the write-after-cancel it exists to stop.
    assert re.match(
        r"\{\s*guard\s+let\s+symbol\s*=\s*remoteSymbol\s*,\s*"
        r"let\s+image\s*=\s*await\s+CompanyLogoCache\.shared\.load\(symbol\)\s*,\s*"
        r"!\s*Task\.isCancelled\s+else\s*\{\s*return\s*\}",
        task), (
        "the cancellation guard must be the task's FIRST statement (`guard let symbol = remoteSymbol, "
        "let image = await CompanyLogoCache.shared.load(symbol), !Task.isCancelled else { return }`) — "
        "a `fetched` write ahead of it lands after the ticker changed or the view went away")
    n_writes = len(re.findall(r"\bfetched\s*=(?!=)", atom))
    assert n_writes == 1, (
        f"`fetched` is assigned in {n_writes} places — the only write must be the task's guarded "
        "`if fetched?.symbol != symbol { … }` (any other one re-renders on every cache hit or writes "
        "after a cancel)")

    code = _code(_ATOM)
    for token in ("URLSession", "URL(string:", "UIImage(data:", "DownsampledImageLoader"):
        assert token not in code, (
            f"CompanyLogoView reaches `{token}` itself — the logo must come from CompanyLogoCache "
            "(one fetch per symbol, a synchronous read for rebuilt views)")


def test_atom_init_and_visuals_are_unchanged():
    """The cache changes how the logo arrives, not what the tile looks like or how it is
    called (five call sites; the trillion-club guards pin the call shape too)."""
    atom = _atom()
    inits = [_norm(p) for p in re.findall(r"\binit\(([^)]*)\)", atom)]
    assert inits == [_INIT], (
        f"CompanyLogoView's init labels changed: {inits} — the five call sites and the "
        "trillion-club guards pin `" + _INIT + "`")

    chip = _decl(atom, "private func remoteLogo(_ logo: UIImage) -> some View", "the remoteLogo(_:) chip")
    lines = [ln.strip() for ln in chip.strip()[1:-1].splitlines() if ln.strip()]
    assert lines == _CHIP, (
        f"the logo chip drifted from the old AsyncImage `.success` chip: {lines}")
    assert atom.count("Image(uiImage:") == 1, (
        "a second remote-logo draw bypasses the remoteLogo(_:) chip (white surface, padding, "
        "invert-colours opt-out)")

    initials = _decl(atom, "private var initialsView: some View", "CompanyLogoView.initialsView")
    assert "Text(fallbackText ?? String(ticker.prefix(1)))" in initials, (
        "the initials placeholder drifted: it must stay `Text(fallbackText ?? String(ticker.prefix(1)))`")

    colours = set(re.findall(r"AppColors\.(\w+)", atom))
    n_color = len(re.findall(r"(?<![\w.])Color\(", atom))
    assert colours == _ATOM_COLOURS and n_color == 1, (
        f"CompanyLogoView gained a colour: AppColors {sorted(colours)} (expected "
        f"{sorted(_ATOM_COLOURS)}), {n_color} bare Color( call(s) (expected the one gradient hex)")


# ── 2. The cache: main-actor, synchronous read, bounded ──────────────────────


def test_cache_is_main_actor_readable_and_bounded():
    code = _code(_CACHE)
    # Checked on the whole file BEFORE `_cache()`: that helper's own count would otherwise
    # report an `actor CompanyLogoCache` as "the class is gone" instead of naming the change.
    assert re.search(r"@MainActor\s+final\s+class\s+CompanyLogoCache\s*\{", code), (
        "CompanyLogoCache must be a @MainActor final class — body reads it synchronously, and "
        "its dictionaries must never leave the main actor")
    cache = _cache()
    assert "static let shared = CompanyLogoCache()" in cache and _has(
        cache, "func load(_ symbol: String) async -> UIImage?"), (
        "not the real CompanyLogoCache (`shared` / `load(_:)` missing)")

    assert re.search(r"private\s+var\s+images\s*:\s*\[\s*String\s*:\s*UIImage\s*\]\s*=\s*\[:\]", cache), (
        "the logo store must be a byte-capped dictionary: `private var images: [String: UIImage] = [:]`")
    # RAW text on purpose: test_system_design_doc_parity strips only whole-line comments, so a
    # trailing `// … NSCache …` would break its pinned "NO NSCache" claim.
    assert "NSCache" not in _CACHE.read_text(encoding="utf-8"), (
        "NSCache must not appear in CompanyLogoCache.swift, comments included — "
        "SYSTEM_DESIGN_GUIDELINES §7.1 says NO NSCache and test_system_design_doc_parity pins it")
    assert re.search(r"\bfunc\s+image\(for\s+symbol:\s*String\)\s*->\s*UIImage\?\s*\{\s*images\[symbol\]\s*\}", cache), (
        "image(for:) must be a SYNCHRONOUS read: `func image(for symbol: String) -> UIImage? { images[symbol] }`")

    store = _decl(cache, "private func store(_ image: UIImage, for symbol: String)", "the bounded store(_:for:)")
    write = "if let old = images.updateValue(image, forKey: symbol)"
    assert _has(store, write + " {"), (
        "store(_:for:) must write the logo under the key image(for:) reads "
        "(`if let old = images.updateValue(image, forKey: symbol) {`) — else the cache never hits "
        "and every rebuilt view flashes its initials")
    # Presence says nothing about WHERE the write sits: a leading guard (reject-when-full, skip a
    # large logo) returns before it, and from then on a new ticker is never cached.
    assert re.match(
        r"\{\s*if\s+let\s+old\s*=\s*images\.updateValue\(image,\s*forKey:\s*symbol\)\s*\{", store), (
        "nothing may run ahead of store(_:for:)'s write: its FIRST statement must be "
        "`if let old = images.updateValue(image, forKey: symbol) {` — a leading guard (e.g. reject "
        "when the budget is full) stops caching new logos, and every rebuilt view for a new ticker "
        "flashes its initials again")
    for step in ("order.append(symbol)", "bytes += Self.cost(of: image)", "while bytes > Self.maxBytes",
                 "let oldest = order.removeFirst()", "images.removeValue(forKey: oldest)"):
        assert _has(store, step), (
            f"the logo cache must stay bounded: store(_:for:) lost `{step}` (count the bytes, "
            "evict oldest-first while over maxBytes)")
    for step in ("bytes -= Self.cost(of: old)", "bytes -= Self.cost(of: evicted)"):
        assert _has(store, step), (
            f"the byte count must give back a replaced or evicted logo: store(_:for:) lost `{step}` — "
            "a count that only grows evicts every logo but the newest")
    # The replace branch, brace-bounded, then whatever follows its closing brace.
    replaced = _block(store, write)
    after = store[store.index("{", store.index(write) + len(write)) + len(replaced):]
    assert _has(replaced, "order.removeAll { $0 == symbol }") and re.match(r"\s*order\.append\(symbol\)", after), (
        "a REPLACED logo must move to the back of `order`: the `updateValue` branch must "
        "`order.removeAll { $0 == symbol }`, and `order.append(symbol)` must follow it unconditionally "
        "— else the logo just stored can be evicted as the 'oldest' (or `order` lists it twice)")
    assert re.search(r"while\s+bytes\s*>\s*Self\.maxBytes\s*,\s*order\.count\s*>\s*1\s*\{", store), (
        "the eviction loop must be exactly `while bytes > Self.maxBytes, order.count > 1 {` — a scaled "
        "budget slips past the maxBytes bound below, and `order.count > 0` evicts the logo just stored")
    outside = cache.replace(store, "")
    assert not re.search(
        r"\bimages\s*\[[^\]]*\]\s*=(?!=)|\bimages\s*=(?!=)|\bimages\.(?:updateValue|removeValue|removeAll|merge)\b",
        outside), (
        "the logo store is written outside store(_:for:) — that write bypasses the byte cap and "
        "the eviction order")
    budget = re.search(r"^\s*static\s+let\s+maxBytes\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024\s*$", cache, re.M)
    assert budget and 1 <= int(budget.group(1)) <= 32, (
        "the logo byte budget must stay small: `static let maxBytes = N * 1024 * 1024` with N <= 32 "
        "(it is never purged; the URLCache memory tier is 32 MB in total)")
    assert int(budget.group(1)) >= 8, (
        "the logo byte budget must stay between 8 and 32 MB: below 8 MB (~55 decoded 192 px logos at "
        "~147 KB each) a long Reports list evicts on scroll and every rebuilt row flashes its "
        f"initials again; got maxBytes = {budget.group(1)} MB")
    cost = _decl(cache, "private static func cost(of image: UIImage) -> Int", "CompanyLogoCache.cost(of:)")
    assert _norm(cost) == _norm(
            "{ guard let bitmap = image.cgImage else { return 1 } return bitmap.bytesPerRow * bitmap.height }"), (
        f"cost(of:) must count a logo's decoded bytes (`bitmap.bytesPerRow * bitmap.height`) — every "
        f"byte count depends on it, and a constant lets the cache grow without limit; got `{_norm(cost)}`")

    symbol = _decl(cache, "static func symbol(for ticker: String) -> String?", "CompanyLogoCache.symbol(for:)")
    assert _has(symbol, "ticker.uppercased().trimmingCharacters(in: .whitespacesAndNewlines)") and _has(
        symbol, "isEmpty ? nil"), (
        "the cache key must be the trimmed, uppercased symbol (nil when blank) — it is also the "
        "CDN file name")
    url = _decl(cache, "static func url(for symbol: String) -> URL?", "CompanyLogoCache.url(for:)")
    assert _norm(url) == _norm('{ URL(string: "https://' + _CDN + '\\(symbol).png") }'), (
        "the logo URL must be the CDN file for the normalised symbol: "
        "`https://images.financialmodelingprep.com/symbol/\\(symbol).png`")

    assert "DownsampledImageLoader" not in code, (
        "logos must not share the hero loader — DownsampledImageLoader is an actor (no synchronous "
        "read), removeAll()s at 48 entries and cannot tell 'no logo' from 'offline'")
    assert "try?" not in code, (
        "a `try?` in CompanyLogoCache swallows a logo failure silently — classify and log it")


def test_load_dedups_and_remembers_only_logos_and_no_logo():
    cache = _cache()
    load = _decl(cache, "func load(_ symbol: String) async -> UIImage?", "CompanyLogoCache.load(_:)")
    assert _has(load, "let result = await task.value") and "switch result" in load, (
        "not the real load(_:) body (`let result = await task.value` / `switch result` missing)")

    steps = [
        ("if let hit = image(for: symbol) { return hit }",
         "a cached logo must short-circuit load (`if let hit = image(for: symbol) { return hit }`)"),
        ("if isKnownMissing(symbol) { return nil }",
         "a remembered no-logo must short-circuit load (`if isKnownMissing(symbol) { return nil }`) — "
         "else every rebuild refetches the 404"),
        ("if let running = inflight[symbol] { return await running.value.image }",
         "concurrent loads of one symbol must share one request: join the running fetch "
         "(`if let running = inflight[symbol] { return await running.value.image }`)"),
        ("Task.detached(",
         "the fetch must run off the main actor (`Task.detached`) — the decode is CPU work"),
        ("inflight[symbol] = task",
         "concurrent loads of one symbol must share one request: register the fetch "
         "(`inflight[symbol] = task`)"),
        ("await task.value", None),
        ("inflight[symbol] = nil",
         "the inflight entry must be cleared once the fetch ends (`inflight[symbol] = nil`) — else "
         "a failed symbol is never retried"),
        ("switch result", None),
    ]
    for literal, message in steps:
        if message:
            assert _has(load, literal), message
    flat = _norm(load)
    at = [flat.find(_norm(literal)) for literal, _ in steps]
    assert -1 not in at and at == sorted(at), (
        "load's steps are out of order: expected hit → known-missing → join → detach → register → "
        f"await → clear → switch, found positions {dict(zip([s for s, _ in steps], at))}")
    # In order is not enough: an early return between the shared fetch ending and the store (a
    # "don't work after cancellation" edit) drops a logo already fetched — and one before
    # `inflight[symbol] = nil` leaves every later load joining a finished task that never stores.
    assert re.search(
        r"inflight\[symbol\]\s*=\s*task\s*let\s+result\s*=\s*await\s+task\.value\s*"
        r"inflight\[symbol\]\s*=\s*nil\s*switch\s+result\s*\{",
        load), (
        "nothing may return between the shared fetch ending and the store: `inflight[symbol] = task` / "
        "`let result = await task.value` / `inflight[symbol] = nil` / `switch result` must be adjacent — "
        "a cancelled asking view must still warm the cache (the fetch is detached and shared)")

    url_guard = re.search(r"guard\s+let\s+url\s*=\s*Self\.url\(for:\s*symbol\)\s*else\s*\{(.*?)\}", load, re.S)
    assert url_guard and "Self.log." in url_guard.group(1) and re.search(r"\breturn\s+nil\b", url_guard.group(1)), (
        "load's missing-URL path must log before returning nil — never swallow silently (CLAUDE.md)")

    arms = _arms(_block(load, "switch result"))
    assert [name for name, _ in arms] == ["image", "noLogo", "failed"], (
        f"load must switch over exactly .image / .noLogo / .failed (no default), got {arms}")
    arm = dict(arms)
    assert arm["image"] == "store(image, for: symbol)", (
        f"a decoded logo must be stored through the bounded store(_:for:), got `{arm['image']}`")
    assert arm["noLogo"] == "noLogoUntil[symbol] = Date().addingTimeInterval(Self.noLogoTTL)", (
        f"a CDN 'no logo' must be remembered for noLogoTTL, got `{arm['noLogo']}`")
    assert arm["failed"] == "break", (
        f"a failed fetch must not be remembered (offline / 429 / 5xx retry on the next "
        f"appearance), got `{arm['failed']}`")
    assert re.search(r"\}\s*return\s+result\.image\s*\}\s*$", load), (
        "load must end by returning the fetched logo (`return result.image`) after the switch — the "
        "cache is not observable, so the asking view gets nil and keeps its initials")

    missing = _decl(cache, "private func isKnownMissing(_ symbol: String) -> Bool", "CompanyLogoCache.isKnownMissing(_:)")
    assert _norm(missing) == _norm(
        "{ guard let until = noLogoUntil[symbol] else { return false } "
        "if until > Date() { return true } noLogoUntil[symbol] = nil return false }"), (
        "an expired no-logo entry must be dropped and retried — isKnownMissing must answer true "
        "only while `until > Date()`")

    ttl = re.search(r"^\s*static\s+let\s+noLogoTTL\s*:\s*TimeInterval\s*=\s*(\d+)\s*\*\s*60\s*$", cache, re.M)
    assert ttl and 1 <= int(ttl.group(1)) <= 60, (
        "the no-logo memory must be short: `static let noLogoTTL: TimeInterval = N * 60` with "
        "1 <= N <= 60 — a logo newly added to the CDN must appear within the hour")


def test_fetch_remembers_only_the_cdns_own_no_logo():
    """Only the CDN's own "no such file" may be remembered; anything transient retries."""
    cache = _cache()
    is_no_logo = _decl(cache, "static func isNoLogo(status: Int) -> Bool", "CompanyLogoCache.isNoLogo(status:)")
    assert re.fullmatch(r"\{\s*status\s*==\s*404\s*\|\|\s*status\s*==\s*410\s*\}", is_no_logo), (
        "only 404/410 mean 'no logo' — 403 / 429 / 5xx are transient and must not be remembered")

    fetch = _decl(cache, "private static func fetch(_ url: URL, session: URLSession) async -> CompanyLogoFetchResult",
                  "CompanyLogoCache.fetch(_:session:)")
    assert "try await session.data(from: url)" in fetch, "not the real fetch (`session.data(from: url)` missing)"

    px = re.search(r"static\s+let\s+maxPixelSize\s*:\s*CGFloat\s*=\s*(\d+)\b", cache)
    assert px and 168 <= int(px.group(1)) <= 256, (
        "maxPixelSize must cover the largest tile (TrillionClubCard 56 pt @3x = 168 px) and keep "
        "no more than the CDN's 250 px PNGs")
    assert _has(fetch, "ImageDownsampler.downsample(data, maxPixelSize: Self.maxPixelSize)"), (
        "logos must decode at display size through ImageDownsampler (eager, off-main, never upscales)")

    status_head = "!(200..<300).contains(http.statusCode)"
    assert _has(fetch, "if let http = response as? HTTPURLResponse, " + status_head), (
        "fetch must classify every non-2xx HTTP answer before decoding the body")
    status = _block(fetch, status_head)
    assert "if Self.isNoLogo(status: http.statusCode)" in status and _returns(status) == ["noLogo", "failed"], (
        f"a non-2xx answer must be .noLogo only when isNoLogo(status:), else .failed; returns {_returns(status)}")

    undecodable_head = "maxPixelSize: Self.maxPixelSize) else"
    assert fetch.count(undecodable_head) == 1, "the undecodable-body guard after the downsample is gone"
    assert _returns(_block(fetch, undecodable_head)) == ["noLogo"], (
        "an undecodable 2xx body is the CDN's own answer (no captive portal can answer over https "
        "for this host) and must be .noLogo")
    assert re.search(r"\breturn\s+\.image\(\s*UIImage\(\s*cgImage:\s*bitmap\s*\)\s*\)", fetch), (
        "a decoded logo must be returned as `.image(UIImage(cgImage: bitmap))` — any other verdict "
        "never stores it, and `.noLogo` remembers every logo as missing")

    assert fetch.count("} catch") == 1, "fetch lost its `catch` — a transport error must be classified"
    assert _returns(_block(fetch, "} catch")) == ["failed"], (
        "a transport error (offline, timeout) must be .failed — never remembered as 'no logo'")

    assert fetch.count("Self.log.") >= 4, (
        f"every non-success path must log (no-logo, HTTP failure, undecodable, transport error); "
        f"found {fetch.count('Self.log.')} log calls")


def test_a_server_logo_url_maps_to_its_fmp_symbol():
    """`WhaleTickerIcon` draws the server's `logo_url` (FMP's profile `image`) through the cache,
    keyed on the symbol in the FMP file name. Only FMP's two logo paths may map: a non-FMP image
    swapped for the CDN file of a symbol can be a different security's logo."""
    cache = _cache()
    header = "static func symbol(forLogoURL logoURL: String) -> String?"
    assert _has(cache, header), (
        "CompanyLogoCache.symbol(forLogoURL:) is gone — WhaleTickerIcon has no cache key for the "
        "server's logo_url")
    mapper = _decl(cache, header, "CompanyLogoCache.symbol(forLogoURL:)")
    assert "url.pathComponents" in mapper and "url.host?.lowercased()" in mapper, (
        "not the real symbol(forLogoURL:) (`url.host?.lowercased()` / `url.pathComponents` missing)")
    assert _has(mapper, 'guard parts.count == 3, parts[0] == "/" else { return nil }'), (
        "symbol(forLogoURL:) must accept only a one-directory path (`/symbol/<SYM>.png`, "
        "`/image-stock/<SYM>.png`): `guard parts.count == 3, parts[0] == \"/\" else { return nil }`")
    cdn = 'let isCDN = host == "images.financialmodelingprep.com" && parts[1] == "symbol"'
    legacy = 'let isLegacy = host == "financialmodelingprep.com" && parts[1] == "image-stock"'
    assert _has(mapper, cdn) and _has(mapper, legacy), (
        "symbol(forLogoURL:) must accept only FMP's two logo files — the CDN's "
        "`images.financialmodelingprep.com/symbol/` and the legacy `financialmodelingprep.com/image-stock/`, "
        "each host paired with its own directory")
    assert _has(mapper, 'guard isCDN || isLegacy, parts[2].lowercased().hasSuffix(".png") else { return nil }'), (
        "symbol(forLogoURL:) must refuse anything but an FMP `.png` logo file "
        "(`guard isCDN || isLegacy, parts[2].lowercased().hasSuffix(\".png\") else { return nil }`)")
    assert re.search(r"return\s+symbol\(for:\s*String\(parts\[2\]\.dropLast\(4\)\)\)\s*\}\s*$", mapper), (
        "symbol(forLogoURL:) must normalise through symbol(for:) "
        "(`return symbol(for: String(parts[2].dropLast(4)))`) — the key must match the one "
        "CompanyLogoView stores for the same ticker")
    assert len(_returns(mapper)) == 0 and len(re.findall(r"\breturn\b", mapper)) == 4, (
        "symbol(forLogoURL:) must have exactly three `return nil` refusals and one normalised return")


# ── 3. One place builds the CDN logo URL ─────────────────────────────────────


# Declarations that draw an `AsyncImage` beside a logo URL but are NOT a company logo — exempt by
# declaration, never by file, so a logo AsyncImage added elsewhere in the same file still counts.
_NOT_A_COMPANY_LOGO = {
    # The whale's own avatar (`avatar_url`). Its file also holds `WhaleTickerIcon`'s `logoURL`.
    _WHALE: "struct WhaleAvatarView: View",
}


def test_the_cdn_logo_url_is_built_in_one_place():
    """The cache fixes the flash only for views that use it. Scans the app, `Shared/` and the
    widget for anything else that builds the CDN logo URL — or any other `financialmodelingprep.com/`
    path, such as the legacy `/image-stock/` logo — (or resolves it through the cache to draw it
    itself). Only the cache may; `TradeTickerLogo`, the last holdout, adopted it on 2026-10-02.

    A server-sent logo URL never contains the CDN string in Swift, so a second scan finds every
    file where an `AsyncImage` sits beside ANY logo URL (`logo_url` / `logoURL` / the CDN). There
    are none: `WhaleTickerIcon` (the backend's FMP `logo_url`) adopted the cache too. The one
    `AsyncImage` left beside a logo URL is the whale's own avatar, exempted by DECLARATION in
    `_NOT_A_COMPANY_LOGO` — each exemption must still hold exactly one `AsyncImage(`, so a stale
    entry, or a logo draw slipped into the avatar, fails here."""
    root = _IOS.parent  # frontend/ios: ios/, Shared/, CaydexWidgets/
    files = sorted(root.rglob("*.swift"))
    assert len(files) > 500 and {_ATOM, _CACHE, _TRADE, _WIDGET, _WHALE, _REPORT_CARD} <= set(files), (
        f"scanned {len(files)} Swift files under {root} — the tree moved; this guard is vacuous")

    builds, holdouts, resolvers = [], [], []
    logo_async: dict[str, int] = {}
    for f in files:
        src = _strip_swift_comments(f.read_text(encoding="utf-8"))
        rel = str(f.relative_to(root))
        if f in _NOT_A_COMPANY_LOGO:
            exempt = _decl(src, _NOT_A_COMPANY_LOGO[f], f"the exempt `{_NOT_A_COMPANY_LOGO[f]}` in {f.name}")
            n_exempt = exempt.count("AsyncImage(")
            assert n_exempt == 1, (
                f"the exempt `{_NOT_A_COMPANY_LOGO[f]}` holds {n_exempt} AsyncImage( draws (expected its one "
                "avatar) — drop a stale exemption, or draw a company logo through the cache")
            src = src.replace(exempt, "{}")
        # Any FMP path, not just `_CDN`: the legacy `financialmodelingprep.com/image-stock/`
        # logo path drawn through a new AsyncImage is the same blink.
        if _FMP_PATH in src:
            builds.append(rel)
            if "AsyncImage(" in src:
                holdouts.append(rel)
        if f != _CACHE and "CompanyLogoCache.url(for:" in src:
            resolvers.append(rel)
        if "AsyncImage(" in src and _LOGO_URL.search(src):
            logo_async[rel] = src.count("AsyncImage(")

    cache_rel = str(_CACHE.relative_to(root))
    assert builds == [cache_rel], (
        f"a second place builds the FMP logo URL: {builds} (any `{_FMP_PATH}` path, the legacy "
        "`/image-stock/` one included) — draw it through CompanyLogoView / CompanyLogoCache, or "
        "every rebuilt view flashes its initials again")
    assert not holdouts, (
        f"an AsyncImage draws beside a built FMP logo URL in {holdouts} — the CDN-logo holdouts are "
        "gone (TradeTickerLogo adopted the cache on 2026-10-02); read CompanyLogoCache.shared instead")
    assert not resolvers, (
        f"only CompanyLogoCache may resolve the CDN logo URL; {resolvers} call "
        "CompanyLogoCache.url(for:) to draw it themselves — read CompanyLogoCache.shared instead")
    assert not logo_async, (
        f"the AsyncImage draws beside a company-logo URL changed: {logo_async} (recorded: none) — "
        "a new one restarts at `.empty` and flashes its initials on every rebuild; draw it through "
        "CompanyLogoView or CompanyLogoCache, or exempt a non-logo image by declaration in "
        "_NOT_A_COMPANY_LOGO")


# ── 3b. The Reports screen's own logo draws ──────────────────────────────────

_REPORT_LOGOS = [
    # (file, view header, the ticker the logo must be for, a token proving it is the real view)
    (_REPORT_CARD, "struct ReportCard: View", "report.ticker", "report.companyName"),
    (_REPORT_HEADER, "struct ReportHeaderBar: View", "ticker", "Text(companyName)"),
]


def test_the_reports_screen_draws_logos_through_the_atom():
    """TestFlight reported the blink on the Reports screen. The cache only helps a view that
    draws through `CompanyLogoView`, and the tree-wide scan cannot see a row that swaps it for an
    `AsyncImage` over a server-sent URL — so pin the screen's own two logo draws: the list row
    (`ReportsListSection` → `SelectableReportRow` → `ReportCard`, the first hops pinned by
    test_ios_reports_list_no_blink) and the open report's header."""
    for path, header, ticker, real in _REPORT_LOGOS:
        code = _code(path)
        # Whole file, comments stripped: a helper view in the same file must not bring it back.
        assert "AsyncImage" not in code, (
            f"{path.name} draws through AsyncImage — a Reports-screen logo must come from "
            "CompanyLogoView, or every rebuilt row starts at `.empty` and flashes its initials")
        view = _decl(code, header, f"the {path.stem} view")
        assert real in view, f"not the real {path.stem} (`{real}` missing)"
        assert re.search(r"\bCompanyLogoView\(\s*ticker:\s*" + re.escape(ticker) + r"\s*[,)]", view), (
            f"{path.name} must draw its company logo through `CompanyLogoView(ticker: {ticker}, …)` "
            "inside the view itself — the Reports screen is where every logo flipped to its initials "
            "and back")


# ── 3c. The whale-holding and trade tiles (the two former AsyncImage holdouts) ─

_CACHED_TILES = [
    # (file, view header, its `logoSymbol` body, tile side in pt, letter-tile fill opacity)
    (_WHALE, "struct WhaleTickerIcon: View", "logoURL.flatMap(CompanyLogoCache.symbol(forLogoURL:))", 40, "0.2"),
    (_TRADE, "struct TradeTickerLogo: View", "CompanyLogoCache.symbol(for: ticker)", 48, "0.15"),
]


def test_whale_and_trade_tiles_draw_the_cached_logo_synchronously():
    """`WhaleTickerIcon` (whale profile holdings, a LazyVStack) and `TradeTickerLogo` (a trade
    group's cards) drew the FMP CDN logo through their own `AsyncImage`, so every rebuilt row
    started on its letter tile. They now read `CompanyLogoCache` exactly as the atom does —
    synchronous read, symbol-tagged `fetched`, one `.task(id: logoSymbol)` — and keep their own
    look: an un-chipped logo at the tile's side, the same corner radius, the tinted letter tile."""
    for path, header, symbol_body, side, opacity in _CACHED_TILES:
        name = header.split()[1].rstrip(":")
        view = _decl(_code(path), header, f"the {name} view")
        assert "letterFallback" in view and "backgroundColor" in view, (
            f"not the real {name} (letterFallback / backgroundColor missing)")

        assert "AsyncImage" not in view, (
            f"{name} draws through AsyncImage again — every rebuilt row restarts at `.empty` and "
            "flashes its letter tile")
        for token in ("URLSession", "URL(string:", "UIImage(data:", "DownsampledImageLoader"):
            assert token not in view, (
                f"{name} reaches `{token}` itself — the logo must come from CompanyLogoCache")

        assert re.search(
            r"@State\s+private\s+var\s+fetched\s*:\s*\(\s*symbol\s*:\s*String\s*,\s*image\s*:\s*UIImage\s*\)\?",
            view), (
            f"{name} must hold the logo it loaded, tagged with its symbol: "
            "`@State private var fetched: (symbol: String, image: UIImage)?`")
        key = _decl(view, "private var logoSymbol: String?", f"{name}.logoSymbol")
        assert _norm(key) == _norm("{ " + symbol_body + " }"), (
            f"{name} must key the cache on `{symbol_body}`, got `{_norm(key)}`")

        assert re.search(r"\bfunc\s+remoteImage\(\s*for\s+symbol\s*:\s*String\s*\)\s*->\s*UIImage\?\s*\{", view), (
            f"{name}.remoteImage(for:) must stay synchronous: `func remoteImage(for symbol: String) -> UIImage?`")
        resolver = _block(view, "func remoteImage(")
        assert not re.search(r"\b(await|async|Task)\b", resolver), (
            f"{name}.remoteImage(for:) must stay synchronous — its body awaits or starts a Task")
        assert re.match(
            r"\{\s*if\s+let\s+cached\s*=\s*CompanyLogoCache\.shared\.image\(for:\s*symbol\)\s*\{\s*return\s+cached\s*\}",
            resolver), (
            f"{name}.remoteImage(for:) must read CompanyLogoCache.shared.image(for: symbol) FIRST — "
            "that process-wide read puts a logo shown this session on a rebuilt row's first frame")
        assert re.search(
            r"guard\s+let\s+fetched\s*,\s*fetched\.symbol\s*==\s*symbol\s+else\s*\{\s*return\s+nil\s*\}\s*"
            r"return\s+fetched\.image\s*\}\s*$",
            resolver), (
            f"{name}: the held logo must be matched to the symbol (`guard let fetched, fetched.symbol == "
            "symbol else { return nil }`) — a recycled row would draw the previous holding's logo")

        body = _decl(view, "var body: some View", f"{name}.body")
        draw = "if let symbol = logoSymbol, let logo = remoteImage(for: symbol)"
        assert re.match(r"\{\s*ZStack\s*\{\s*" + re.escape(draw) + r"\s*\{", body), (
            f"{name}.body must draw `{draw}` first inside its ZStack — a logo read anywhere else (e.g. "
            "only from the task's result) is not on the rebuilt row's first frame")
        chip = _block(body, draw)
        lines = [ln.strip() for ln in chip.strip()[1:-1].splitlines() if ln.strip()]
        expected = ["Image(uiImage: logo)", ".resizable()", ".aspectRatio(contentMode: .fit)",
                    f".frame(width: {side}, height: {side})",
                    ".clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))"]
        assert lines == expected, f"{name}'s logo tile drifted from its old AsyncImage `.success` draw: {lines}"
        after = body[body.index(chip) + len(chip):]
        assert re.match(r"\s*else\s*\{\s*letterFallback\s*\}\s*\}", after), (
            f"{name}.body must fall back to `else {{ letterFallback }}` while loading or with no logo")
        assert view.count("Image(uiImage:") == 1, f"{name} draws a second remote logo outside its tile"

        assert len(re.findall(r"\.task\b", view)) == 1 and ".task(id: logoSymbol) {" in body, (
            f"{name}: the logo load must be `.task(id: logoSymbol)` — the ONE task, restarted when the "
            "symbol changes and cancelled with the row")
        assert not re.search(r"\bTask(?:\.detached)?\s*(?:\([^)]*\))?\s*\{", view), (
            f"{name} starts its own unstructured Task — the only load is `.task(id: logoSymbol)`")
        task = _block(body, ".task(id: logoSymbol)")
        assert re.match(r"\{\s*guard\s+let\s+symbol\s*=\s*logoSymbol\b", task), (
            f"{name}: the task must start `guard let symbol = logoSymbol` — the symbol body drew")
        loaded = re.search(
            r"let\s+image\s*=\s*await\s+CompanyLogoCache\.shared\.load\(symbol\)\s*,\s*"
            r"!\s*Task\.isCancelled\s+else\s*\{\s*return\s*\}", task)
        assert loaded, (
            f"{name}: the task must not write `fetched` after it was cancelled — "
            "`let image = await CompanyLogoCache.shared.load(symbol), !Task.isCancelled else { return }`")
        write = re.search(
            r"if\s+fetched\?\.symbol\s*!=\s*symbol\s*\{\s*fetched\s*=\s*\(\s*symbol:\s*symbol\s*,\s*image:\s*image\s*\)\s*\}",
            task)
        assert write, (
            f"{name}: assign `fetched` only when the symbol changes — a cache hit re-assigning the tuple "
            "costs an extra body pass on every row appearance")
        n_writes = len(re.findall(r"\bfetched\s*=(?!=)", view))
        assert n_writes == 1 and loaded.end() <= write.start(), (
            f"{name}: `fetched` is written in {n_writes} place(s); the only write must follow the "
            "load-and-cancellation guard")

        letter = _decl(view, "private var letterFallback: some View", f"{name}.letterFallback")
        for literal in (f".fill(backgroundColor.opacity({opacity}))", f".frame(width: {side}, height: {side})",
                        "Text(String(ticker.prefix(1)))", "RoundedRectangle(cornerRadius: AppCornerRadius.medium)"):
            assert _has(letter, literal), f"{name}'s letter tile drifted: `{literal}` missing"


def test_whale_icon_logs_a_logo_url_it_cannot_key():
    """A non-FMP `logo_url` is contract drift (the server sends FMP's profile `image`). It keeps
    the letter tile — never swallowed silently (CLAUDE.md)."""
    view = _decl(_code(_WHALE), "struct WhaleTickerIcon: View", "the WhaleTickerIcon view")
    task = _block(view, ".task(id: logoSymbol)")
    refusal = re.match(r"\{\s*guard\s+let\s+symbol\s*=\s*logoSymbol\s+else\s*(\{)", task)
    assert refusal, "WhaleTickerIcon's task must open `guard let symbol = logoSymbol else { … }`"
    branch = _balanced(task, refusal.start(1))
    assert "Self.log.warning(" in branch and re.search(r"\breturn\s*\}\s*$", branch), (
        "WhaleTickerIcon must log a logo_url that is not an FMP logo file (`Self.log.warning(…)`) "
        "before keeping the letter tile")
    assert re.search(r"if\s+let\s+logoURL\s*,\s*!\s*logoURL\.trimmingCharacters\(", branch), (
        "WhaleTickerIcon must warn only when a logo_url was SENT — a holding with none is the "
        "normal no-logo case")


# ── 4. The mutations above, re-run in memory on every pass ──────────────────

_CDN_LITERAL = 'URL(string: "https://' + _CDN + 'AAPL.png")'

# (file, anchor, replacement, guard, the assertion message the guard must fail WITH). The
# message is matched so a mutation cannot pass by tripping an unrelated, earlier assertion.
_MUTATIONS = [
    # ── atom: synchronous draw ──
    (_ATOM, "                    remoteLogo(logo)\n",
     "                    AsyncImage(url: CompanyLogoCache.url(for: symbol))\n",
     test_atom_resolves_the_logo_synchronously, "CompanyLogoView draws through AsyncImage again"),
    (_ATOM, "        if let cached = CompanyLogoCache.shared.image(for: symbol) { return cached }\n", "",
     test_atom_resolves_the_logo_synchronously,
     "remoteImage(for:) must read CompanyLogoCache.shared.image(for: symbol) FIRST"),
    (_ATOM, "if let cached = CompanyLogoCache.shared.image(for: symbol) { return cached }",
     "if let cached = await CompanyLogoCache.shared.load(symbol) { return cached }",
     test_atom_resolves_the_logo_synchronously, "remoteImage(for:) must stay synchronous — its body"),
    (_ATOM, "private func remoteImage(for symbol: String) -> UIImage? {",
     "private func remoteImage(for symbol: String) async -> UIImage? {",
     test_atom_resolves_the_logo_synchronously, "remoteImage(for:) must stay synchronous — its signature"),
    (_ATOM, "guard let fetched, fetched.symbol == symbol else { return nil }",
     "guard let fetched else { return nil }",
     test_atom_resolves_the_logo_synchronously, "the held logo must be matched to the symbol"),
    (_ATOM, "if let logo = remoteImage(for: symbol) {", "if let logo = fetched?.image {",
     test_atom_resolves_the_logo_synchronously, "the remote branch must draw remoteImage(for: symbol)"),
    (_ATOM, "            if let bundledImage {\n", "            if false, let bundledImage {\n",
     test_atom_resolves_the_logo_synchronously, "a bundled asset no longer wins"),
    # ── atom: the task that fills the cache ──
    (_ATOM, "bundledImage == nil ? CompanyLogoCache.symbol(for: ticker) : nil",
     "CompanyLogoCache.symbol(for: ticker)",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "remoteSymbol must be nil while a bundled asset is drawn"),
    (_ATOM, ".task(id: remoteSymbol) {", ".task {",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "the logo load must be `.task(id: remoteSymbol)`"),
    (_ATOM, "        .task(id: remoteSymbol) {\n",
     '        .task(id: remoteSymbol) {\n            Task { _ = await CompanyLogoCache.shared.load(remoteSymbol ?? "") }\n',
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "CompanyLogoView starts its own unstructured Task"),
    (_ATOM, "await CompanyLogoCache.shared.load(symbol)",
     "await DownsampledImageLoader.shared.image(at: CompanyLogoCache.url(for: symbol)!, maxPixelSize: 192)",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "the task must fill through CompanyLogoCache.load"),
    (_ATOM, "fetched = (symbol: symbol, image: image)", "_ = image",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "the task must keep the loaded logo in `fetched`"),
    (_ATOM, "                  !Task.isCancelled else { return }", "                  true else { return }",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "the task must not write `fetched` after it was cancelled"),
    (_ATOM, "            if fetched?.symbol != symbol {\n                fetched = (symbol: symbol, image: image)\n            }\n",
     "            fetched = (symbol: symbol, image: image)\n",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "assign `fetched` only when the symbol changes"),
    (_ATOM, "@State private var fetched: (symbol: String, image: UIImage)? = nil",
     "@State private var fetched: UIImage? = nil",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "the view must hold the logo it loaded, tagged with its symbol"),
    (_ATOM, "    private var remoteSymbol: String? {\n",
     "    private let session = URLSession.shared\n\n    private var remoteSymbol: String? {\n",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "CompanyLogoView reaches `URLSession` itself"),
    # ── atom: init and visuals ──
    (_ATOM, "fallbackText: String? = nil) {", "placeholder: String? = nil) {",
     test_atom_init_and_visuals_are_unchanged, "CompanyLogoView's init labels changed"),
    (_ATOM, ".padding(size * 0.16)", ".padding(size * 0.1)",
     test_atom_init_and_visuals_are_unchanged, "the logo chip drifted"),
    (_ATOM, ".background(AppColors.mediaSurface)", ".background(AppColors.cardBackground)",
     test_atom_init_and_visuals_are_unchanged, "the logo chip drifted"),
    (_ATOM, "            .accessibilityIgnoresInvertColors()\n", "",
     test_atom_init_and_visuals_are_unchanged, "the logo chip drifted"),
    (_ATOM, "                    remoteLogo(logo)\n", "                    Image(uiImage: logo)\n",
     test_atom_init_and_visuals_are_unchanged, "a second remote-logo draw bypasses the remoteLogo(_:) chip"),
    (_ATOM, "Text(fallbackText ?? String(ticker.prefix(1)))", "Text(String(ticker.prefix(1)))",
     test_atom_init_and_visuals_are_unchanged, "the initials placeholder drifted"),
    (_ATOM, ".foregroundColor(initialsInk)", ".foregroundColor(AppColors.primaryBlue)",
     test_atom_init_and_visuals_are_unchanged, "CompanyLogoView gained a colour"),
    # ── cache: isolation, read, bound ──
    (_CACHE, "@MainActor\nfinal class CompanyLogoCache {", "actor CompanyLogoCache {",
     test_cache_is_main_actor_readable_and_bounded, "CompanyLogoCache must be a @MainActor final class"),
    (_CACHE, "@MainActor\nfinal class CompanyLogoCache {", "final class CompanyLogoCache {",
     test_cache_is_main_actor_readable_and_bounded, "CompanyLogoCache must be a @MainActor final class"),
    (_CACHE, "func image(for symbol: String) -> UIImage? {", "func image(for symbol: String) async -> UIImage? {",
     test_cache_is_main_actor_readable_and_bounded, "image(for:) must be a SYNCHRONOUS read"),
    (_CACHE, "    private var images: [String: UIImage] = [:]\n",
     "    private let images = NSCache<NSString, UIImage>()\n",
     test_cache_is_main_actor_readable_and_bounded, "the logo store must be a byte-capped dictionary"),
    (_CACHE, "    private var bytes = 0\n", "    private var bytes = 0  // not NSCache: it purges in the background\n",
     test_cache_is_main_actor_readable_and_bounded, "NSCache must not appear in CompanyLogoCache.swift"),
    (_CACHE, "while bytes > Self.maxBytes, order.count > 1 {", "while false, order.count > 1 {",
     test_cache_is_main_actor_readable_and_bounded, "the logo cache must stay bounded"),
    (_CACHE, "        bytes += Self.cost(of: image)\n", "",
     test_cache_is_main_actor_readable_and_bounded, "the logo cache must stay bounded"),
    (_CACHE, "        order.append(symbol)\n", "        _ = symbol\n",
     test_cache_is_main_actor_readable_and_bounded, "the logo cache must stay bounded"),
    (_CACHE, "{ bytes -= Self.cost(of: evicted) }", "{ _ = evicted }",
     test_cache_is_main_actor_readable_and_bounded, "the byte count must give back a replaced or evicted logo"),
    (_CACHE, "            bytes -= Self.cost(of: old)\n", "            _ = old\n",
     test_cache_is_main_actor_readable_and_bounded, "the byte count must give back a replaced or evicted logo"),
    (_CACHE, "            store(image, for: symbol)\n", "            images[symbol] = image\n",
     test_cache_is_main_actor_readable_and_bounded, "the logo store is written outside store(_:for:)"),
    (_CACHE, "static let maxBytes = 16 * 1024 * 1024", "static let maxBytes = 512 * 1024 * 1024",
     test_cache_is_main_actor_readable_and_bounded, "the logo byte budget must stay small"),
    (_CACHE, "ticker.uppercased().trimmingCharacters(in: .whitespacesAndNewlines)", "ticker",
     test_cache_is_main_actor_readable_and_bounded, "the cache key must be the trimmed, uppercased symbol"),
    (_CACHE, "/symbol/\\(symbol).png", "/symbol/\\(symbol.lowercased()).png",
     test_cache_is_main_actor_readable_and_bounded, "the logo URL must be the CDN file for the normalised symbol"),
    (_CACHE, "        let session = self.session\n",
     "        let session = self.session\n        _ = DownsampledImageLoader.shared\n",
     test_cache_is_main_actor_readable_and_bounded, "logos must not share the hero loader"),
    (_CACHE, "guard let bitmap = ImageDownsampler.downsample(", "guard let bitmap = try? ImageDownsampler.downsample(",
     test_cache_is_main_actor_readable_and_bounded, "a `try?` in CompanyLogoCache swallows a logo failure"),
    # ── cache: load ──
    (_CACHE, "        if let hit = image(for: symbol) { return hit }\n", "",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "a cached logo must short-circuit load"),
    (_CACHE, "        if isKnownMissing(symbol) { return nil }\n", "",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "a remembered no-logo must short-circuit load"),
    (_CACHE, "        if let running = inflight[symbol] { return await running.value.image }\n", "",
     test_load_dedups_and_remembers_only_logos_and_no_logo,
     "concurrent loads of one symbol must share one request: join the running fetch"),
    (_CACHE, "inflight[symbol] = task", "_ = task",
     test_load_dedups_and_remembers_only_logos_and_no_logo,
     "concurrent loads of one symbol must share one request: register the fetch"),
    (_CACHE, "Task.detached(priority: .userInitiated) {", "Task {",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "the fetch must run off the main actor"),
    (_CACHE, "        inflight[symbol] = nil\n", "",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "the inflight entry must be cleared"),
    (_CACHE, "        inflight[symbol] = task\n        let result = await task.value\n        inflight[symbol] = nil\n",
     "        inflight[symbol] = task\n        inflight[symbol] = nil\n        let result = await task.value\n",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "load's steps are out of order"),
    (_CACHE, '            Self.log.error("no logo URL for \\(symbol, privacy: .public)")\n', "",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "load's missing-URL path must log"),
    (_CACHE, "            store(image, for: symbol)\n", "            _ = image\n",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "a decoded logo must be stored"),
    (_CACHE, "        case .failed:\n            break\n",
     "        case .failed:\n            noLogoUntil[symbol] = Date().addingTimeInterval(Self.noLogoTTL)\n",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "a failed fetch must not be remembered"),
    (_CACHE, "            noLogoUntil[symbol] = Date().addingTimeInterval(Self.noLogoTTL)\n", "            break\n",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "a CDN 'no logo' must be remembered"),
    (_CACHE, "static let noLogoTTL: TimeInterval = 10 * 60", "static let noLogoTTL: TimeInterval = 24 * 60 * 60",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "the no-logo memory must be short"),
    (_CACHE, "if until > Date() { return true }", "return true",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "an expired no-logo entry must be dropped"),
    # ── cache: fetch verdicts ──
    (_CACHE, "status == 404 || status == 410", "!(200..<300).contains(status)",
     test_fetch_remembers_only_the_cdns_own_no_logo, "only 404/410 mean 'no logo'"),
    (_CACHE, "maxPixelSize: CGFloat = 192", "maxPixelSize: CGFloat = 96",
     test_fetch_remembers_only_the_cdns_own_no_logo, "maxPixelSize must cover the largest tile"),
    (_CACHE, "ImageDownsampler.downsample(data, maxPixelSize: Self.maxPixelSize)", "UIImage(data: data)?.cgImage",
     test_fetch_remembers_only_the_cdns_own_no_logo, "logos must decode at display size"),
    (_CACHE, "if Self.isNoLogo(status: http.statusCode) {", "if true {",
     test_fetch_remembers_only_the_cdns_own_no_logo, "a non-2xx answer must be .noLogo only when isNoLogo"),
    (_CACHE, "                return .noLogo\n            }\n            return .image",
     "                return .failed\n            }\n            return .image",
     test_fetch_remembers_only_the_cdns_own_no_logo, "an undecodable 2xx body is the CDN's own answer"),
    (_CACHE, "} catch {\n", "} catch {\n            return .noLogo\n",
     test_fetch_remembers_only_the_cdns_own_no_logo, "a transport error (offline, timeout) must be .failed"),
    (_CACHE, '                    Self.log.info("no CDN logo for \\(name, privacy: .public)")\n', "",
     test_fetch_remembers_only_the_cdns_own_no_logo, "every non-success path must log"),
    # ── tree-wide ──
    (_ATOM, "struct CompanyLogoView: View {",
     "private let legacy = " + _CDN_LITERAL + "\n\nstruct CompanyLogoView: View {",
     test_the_cdn_logo_url_is_built_in_one_place, "a second place builds the FMP logo URL"),
    (_WIDGET, "import SwiftUI\n", "import SwiftUI\n\nprivate let moverLogo = " + _CDN_LITERAL + "\n",
     test_the_cdn_logo_url_is_built_in_one_place, "a second place builds the FMP logo URL"),
    # The former holdout building the CDN URL again (re-derived 2026-10-02: it adopted the cache).
    (_TRADE, "struct TradeTickerLogo: View {",
     "private let legacyLogo = " + _CDN_LITERAL + "\n\nstruct TradeTickerLogo: View {",
     test_the_cdn_logo_url_is_built_in_one_place, "a second place builds the FMP logo URL"),
    (_CACHE, "import UIKit\n",
     'import UIKit\nimport SwiftUI\n\nprivate let logoPreview = AsyncImage(url: CompanyLogoCache.url(for: "AAPL"))\n',
     test_the_cdn_logo_url_is_built_in_one_place, "an AsyncImage draws beside a built FMP logo URL"),
    (_ATOM, "struct CompanyLogoView: View {",
     'private let legacy = CompanyLogoCache.url(for: "AAPL")\n\nstruct CompanyLogoView: View {',
     test_the_cdn_logo_url_is_built_in_one_place, "only CompanyLogoCache may resolve the CDN logo URL"),
    # ── review round 2: gaps the first table left open ──
    # store(_:for:) must actually write, under the key image(for:) reads.
    (_CACHE, "if let old = images.updateValue(image, forKey: symbol) {", "if let old = images[symbol] {",
     test_cache_is_main_actor_readable_and_bounded,
     "store(_:for:) must write the logo under the key image(for:) reads"),
    (_CACHE, "images.updateValue(image, forKey: symbol)", "images.updateValue(image, forKey: symbol.lowercased())",
     test_cache_is_main_actor_readable_and_bounded,
     "store(_:for:) must write the logo under the key image(for:) reads"),
    # A replaced symbol moves to the back of `order`.
    (_CACHE, "            order.removeAll { $0 == symbol }\n", "",
     test_cache_is_main_actor_readable_and_bounded, "a REPLACED logo must move to the back of `order`"),
    (_CACHE, "order.removeAll { $0 == symbol }", "order.removeAll()",
     test_cache_is_main_actor_readable_and_bounded, "a REPLACED logo must move to the back of `order`"),
    (_CACHE, "            order.removeAll { $0 == symbol }\n        }\n        order.append(symbol)\n",
     "        } else {\n            order.append(symbol)\n        }\n",
     test_cache_is_main_actor_readable_and_bounded, "a REPLACED logo must move to the back of `order`"),
    # The eviction loop's whole header.
    (_CACHE, "while bytes > Self.maxBytes, order.count > 1 {", "while bytes > Self.maxBytes * 64, order.count > 1 {",
     test_cache_is_main_actor_readable_and_bounded, "the eviction loop must be exactly"),
    (_CACHE, "order.count > 1 {", "order.count > 0 {",
     test_cache_is_main_actor_readable_and_bounded, "the eviction loop must be exactly"),
    # cost(of:), which every byte count depends on.
    (_CACHE, "return bitmap.bytesPerRow * bitmap.height", "return 0",
     test_cache_is_main_actor_readable_and_bounded, "cost(of:) must count a logo's decoded bytes"),
    # load hands the fetched logo back to the asking view.
    (_CACHE, "        return result.image\n", "        return nil\n",
     test_load_dedups_and_remembers_only_logos_and_no_logo, "load must end by returning the fetched logo"),
    # fetch's success verdict.
    (_CACHE, "return .image(UIImage(cgImage: bitmap))", "return .noLogo",
     test_fetch_remembers_only_the_cdns_own_no_logo, "a decoded logo must be returned as"),
    # The atom's task: cancellation guard first, one `fetched` write.
    (_ATOM, "                  let image = await CompanyLogoCache.shared.load(symbol),\n"
            "                  !Task.isCancelled else { return }\n",
     "                  let image = await CompanyLogoCache.shared.load(symbol) else { return }\n"
     "            if fetched?.symbol != symbol { fetched = (symbol: symbol, image: image) }\n"
     "            guard !Task.isCancelled else { return }\n",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol,
     "the cancellation guard must be the task's FIRST statement"),
    (_ATOM, "            if fetched?.symbol != symbol {\n                fetched = (symbol: symbol, image: image)\n            }\n",
     "            if fetched?.symbol != symbol {\n                fetched = (symbol: symbol, image: image)\n            }\n"
     "            fetched = (symbol: symbol, image: image)\n",
     test_atom_fills_the_cache_from_a_task_keyed_on_the_symbol, "`fetched` is assigned in 2 places"),
    # The Reports screen's own logo draws.
    (_REPORT_CARD, "CompanyLogoView(ticker: report.ticker, size: 36)",
     'AsyncImage(url: URL(string: report.logoUrl ?? "")) { $0.resizable() } placeholder: { Color.clear }'
     ".frame(width: 36, height: 36)",
     test_the_reports_screen_draws_logos_through_the_atom, "ReportCard.swift draws through AsyncImage"),
    (_REPORT_CARD, "CompanyLogoView(ticker: report.ticker, size: 36)", 'Image(systemName: "building.2")',
     test_the_reports_screen_draws_logos_through_the_atom,
     "ReportCard.swift must draw its company logo through `CompanyLogoView(ticker: report.ticker"),
    (_REPORT_CARD, "CompanyLogoView(ticker: report.ticker, size: 36)", "CompanyLogoView(ticker: report.companyName, size: 36)",
     test_the_reports_screen_draws_logos_through_the_atom,
     "ReportCard.swift must draw its company logo through `CompanyLogoView(ticker: report.ticker"),
    (_REPORT_HEADER, "CompanyLogoView(ticker: ticker, size: 36)",
     'AsyncImage(url: URL(string: logoUrl ?? "")) { $0.resizable() } placeholder: { Color.clear }'
     ".frame(width: 36, height: 36)",
     test_the_reports_screen_draws_logos_through_the_atom, "ReportHeaderBar.swift draws through AsyncImage"),
    (_REPORT_HEADER, "CompanyLogoView(ticker: ticker, size: 36)", 'Image(systemName: "building.2")',
     test_the_reports_screen_draws_logos_through_the_atom,
     "ReportHeaderBar.swift must draw its company logo through `CompanyLogoView(ticker: ticker"),
    # A server-sent logo URL drawn through AsyncImage (invisible to the CDN-string scan).
    (_REPORT_CARD, "import SwiftUI\n",
     "import SwiftUI\n\nprivate struct RowLogo: View {\n    let logoUrl: String?\n"
     '    var body: some View { AsyncImage(url: URL(string: logoUrl ?? "")) }\n}\n',
     test_the_cdn_logo_url_is_built_in_one_place,
     "the AsyncImage draws beside a company-logo URL changed"),
    # …and one added beside WhaleProfileView's avatar AsyncImage (a file-level record misses it).
    (_WHALE, "struct WhaleTickerIcon: View {",
     "private struct HoldingLogo: View {\n    let logoURL: String?\n"
     '    var body: some View { AsyncImage(url: URL(string: logoURL ?? "")) }\n}\n\n'
     "struct WhaleTickerIcon: View {",
     test_the_cdn_logo_url_is_built_in_one_place,
     "the AsyncImage draws beside a company-logo URL changed"),
    # The avatar exemption (re-derived 2026-10-02: WhaleTickerIcon adopted the cache, so the
    # avatar is the file's one AsyncImage). Stale when the avatar stops using one…
    (_WHALE, "AsyncImage(url: imageURL) { phase in", "CachedAvatarImage(url: imageURL) { phase in",
     test_the_cdn_logo_url_is_built_in_one_place,
     "the exempt `struct WhaleAvatarView: View` holds 0 AsyncImage("),
    # …and a logo AsyncImage slipped INSIDE the exempt declaration still counts.
    (_WHALE, "    private var initialsAvatar: some View {\n",
     "    private func holdingLogo(_ url: URL) -> some View { AsyncImage(url: url) }\n\n"
     "    private var initialsAvatar: some View {\n",
     test_the_cdn_logo_url_is_built_in_one_place,
     "the exempt `struct WhaleAvatarView: View` holds 2 AsyncImage("),
    # ── review round 3: gaps the second table left open ──
    # load: an early return after the shared fetch ends skips the store (M1), or skips clearing
    # `inflight` too, so every later load joins a finished task that never stores (M1b).
    (_CACHE, "        inflight[symbol] = nil\n        switch result {\n",
     "        inflight[symbol] = nil\n        guard !Task.isCancelled else { return nil }\n        switch result {\n",
     test_load_dedups_and_remembers_only_logos_and_no_logo,
     "nothing may return between the shared fetch ending and the store"),
    (_CACHE, "        let result = await task.value\n        inflight[symbol] = nil\n",
     "        let result = await task.value\n        if Task.isCancelled { return nil }\n        inflight[symbol] = nil\n",
     test_load_dedups_and_remembers_only_logos_and_no_logo,
     "nothing may return between the shared fetch ending and the store"),
    # store(_:for:): a leading budget guard returns before the write (M2).
    (_CACHE, "    private func store(_ image: UIImage, for symbol: String) {\n",
     "    private func store(_ image: UIImage, for symbol: String) {\n"
     "        guard bytes + Self.cost(of: image) <= Self.maxBytes else { return }\n",
     test_cache_is_main_actor_readable_and_bounded,
     "nothing may run ahead of store(_:for:)'s write"),
    # A "memory diet" budget: ~7 logos, so a Reports list evicts on scroll (M3).
    (_CACHE, "static let maxBytes = 16 * 1024 * 1024", "static let maxBytes = 1 * 1024 * 1024",
     test_cache_is_main_actor_readable_and_bounded, "the logo byte budget must stay between 8 and 32 MB"),
    # FMP's legacy logo path, drawn through a new per-view AsyncImage (M7).
    (_ASSET_ROW, "import SwiftUI\n",
     "import SwiftUI\n\nprivate struct RowLogo: View {\n    let ticker: String\n"
     '    var body: some View { AsyncImage(url: URL(string: "https://financialmodelingprep.com/image-stock/\\(ticker).png")) }\n'
     "}\n",
     test_the_cdn_logo_url_is_built_in_one_place, "a second place builds the FMP logo URL"),
    # ── 2026-10-02: the whale-holding and trade tiles adopt the cache ──
    # A server logo URL drawn through a new AsyncImage beside the adopted trade tile.
    (_TRADE, "struct TradeTickerLogo: View {",
     "private struct ServerLogo: View {\n    let logoUrl: String?\n"
     '    var body: some View { AsyncImage(url: URL(string: logoUrl ?? "")) }\n}\n\n'
     "struct TradeTickerLogo: View {",
     test_the_cdn_logo_url_is_built_in_one_place, "the AsyncImage draws beside a company-logo URL changed"),
    # Each tile: back to AsyncImage, no synchronous read, unmatched or task-only logo.
    (_WHALE, "            } else {\n                letterFallback\n            }\n",
     "            } else {\n                AsyncImage(url: nil) { $0.resizable() } placeholder: { letterFallback }\n            }\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "WhaleTickerIcon draws through AsyncImage again"),
    (_TRADE, "            } else {\n                letterFallback\n            }\n",
     "            } else {\n                AsyncImage(url: nil) { $0.resizable() } placeholder: { letterFallback }\n            }\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "TradeTickerLogo draws through AsyncImage again"),
    (_WHALE, "        if let cached = CompanyLogoCache.shared.image(for: symbol) { return cached }\n", "",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon.remoteImage(for:) must read CompanyLogoCache.shared.image(for: symbol) FIRST"),
    (_TRADE, "        if let cached = CompanyLogoCache.shared.image(for: symbol) { return cached }\n", "",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo.remoteImage(for:) must read CompanyLogoCache.shared.image(for: symbol) FIRST"),
    (_WHALE, "private func remoteImage(for symbol: String) -> UIImage? {",
     "private func remoteImage(for symbol: String) async -> UIImage? {",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon.remoteImage(for:) must stay synchronous: "),
    (_TRADE, "if let cached = CompanyLogoCache.shared.image(for: symbol) { return cached }",
     "if let cached = await CompanyLogoCache.shared.load(symbol) { return cached }",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo.remoteImage(for:) must stay synchronous — its body"),
    (_WHALE, "guard let fetched, fetched.symbol == symbol else { return nil }", "guard let fetched else { return nil }",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon: the held logo must be matched to the symbol"),
    (_TRADE, "guard let fetched, fetched.symbol == symbol else { return nil }", "guard let fetched else { return nil }",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo: the held logo must be matched to the symbol"),
    (_WHALE, "if let symbol = logoSymbol, let logo = remoteImage(for: symbol) {",
     "if let symbol = logoSymbol, let logo = fetched?.image {",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon.body must draw `if let symbol = logoSymbol, let logo = remoteImage(for: symbol)` first"),
    (_TRADE, "if let symbol = logoSymbol, let logo = remoteImage(for: symbol) {",
     "if let symbol = logoSymbol, let logo = fetched?.image {",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo.body must draw `if let symbol = logoSymbol, let logo = remoteImage(for: symbol)` first"),
    (_WHALE, "@State private var fetched: (symbol: String, image: UIImage)? = nil",
     "@State private var fetched: UIImage? = nil",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon must hold the logo it loaded, tagged with its symbol"),
    (_TRADE, "    private var logoSymbol: String? {\n",
     '    private var logoURL: URL? { URL(string: "https://example.com/x.png") }\n\n    private var logoSymbol: String? {\n',
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "TradeTickerLogo reaches `URL(string:` itself"),
    # Each tile's cache key: the whale tile's server URL, the trade tile's normalised ticker.
    (_WHALE, "logoURL.flatMap(CompanyLogoCache.symbol(forLogoURL:))", "CompanyLogoCache.symbol(for: ticker)",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon must key the cache on `logoURL.flatMap(CompanyLogoCache.symbol(forLogoURL:))`"),
    (_TRADE, "        CompanyLogoCache.symbol(for: ticker)\n", "        ticker\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo must key the cache on `CompanyLogoCache.symbol(for: ticker)`"),
    # Each tile's task.
    (_WHALE, ".task(id: logoSymbol) {", ".task {",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon: the logo load must be `.task(id: logoSymbol)`"),
    (_TRADE, ".task(id: logoSymbol) {", ".task(id: ticker) {",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo: the logo load must be `.task(id: logoSymbol)`"),
    (_TRADE, ".task(id: logoSymbol) {\n",
     '.task(id: logoSymbol) {\n            Task { _ = await CompanyLogoCache.shared.load("X") }\n',
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "TradeTickerLogo starts its own unstructured Task"),
    (_TRADE, ".task(id: logoSymbol) {\n", ".task(id: logoSymbol) {\n            fetched = nil\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo: the task must start `guard let symbol = logoSymbol`"),
    (_WHALE, "                  !Task.isCancelled else { return }", "                  true else { return }",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon: the task must not write `fetched` after it was cancelled"),
    (_TRADE, "                  !Task.isCancelled else { return }", "                  true else { return }",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo: the task must not write `fetched` after it was cancelled"),
    (_WHALE, "            if fetched?.symbol != symbol {\n                fetched = (symbol: symbol, image: image)\n            }\n",
     "            fetched = (symbol: symbol, image: image)\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon: assign `fetched` only when the symbol changes"),
    (_TRADE, "            if fetched?.symbol != symbol {\n                fetched = (symbol: symbol, image: image)\n            }\n",
     "            fetched = (symbol: symbol, image: image)\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "TradeTickerLogo: assign `fetched` only when the symbol changes"),
    (_WHALE, "    private var letterFallback: some View {\n",
     "    private func reset() { fetched = nil }\n\n    private var letterFallback: some View {\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously,
     "WhaleTickerIcon: `fetched` is written in 2 place(s)"),
    # Each tile's look: side, corner radius, no atom chip; the tinted letter tile.
    (_WHALE, "                    .frame(width: 40, height: 40)\n", "                    .frame(width: 44, height: 44)\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "WhaleTickerIcon's logo tile drifted"),
    (_TRADE, "                    .frame(width: 48, height: 48)\n", "                    .frame(width: 52, height: 52)\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "TradeTickerLogo's logo tile drifted"),
    (_WHALE, ".clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))", ".clipShape(Circle())",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "WhaleTickerIcon's logo tile drifted"),
    (_TRADE, "                    .aspectRatio(contentMode: .fit)\n",
     "                    .aspectRatio(contentMode: .fit)\n                    .padding(8)\n",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "TradeTickerLogo's logo tile drifted"),
    (_WHALE, ".fill(backgroundColor.opacity(0.2))", ".fill(backgroundColor.opacity(0.35))",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "WhaleTickerIcon's letter tile drifted"),
    (_TRADE, ".fill(backgroundColor.opacity(0.15))", ".fill(backgroundColor.opacity(0.3))",
     test_whale_and_trade_tiles_draw_the_cached_logo_synchronously, "TradeTickerLogo's letter tile drifted"),
    # The whale tile's warning for a logo_url it cannot key.
    (_WHALE, "                    Self.log.warning(", "                    _ = (",
     test_whale_icon_logs_a_logo_url_it_cannot_key,
     "WhaleTickerIcon must log a logo_url that is not an FMP logo file"),
    (_WHALE, "if let logoURL, !logoURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {", "if true {",
     test_whale_icon_logs_a_logo_url_it_cannot_key, "WhaleTickerIcon must warn only when a logo_url was SENT"),
    (_WHALE, "            guard let symbol = logoSymbol else {\n", "            guard let symbol = logoSymbol, true else {\n",
     test_whale_icon_logs_a_logo_url_it_cannot_key,
     "WhaleTickerIcon's task must open `guard let symbol = logoSymbol else { … }`"),
    # The cache's server-URL → symbol mapping.
    (_CACHE, "static func symbol(forLogoURL logoURL: String) -> String? {",
     "static func symbol(fromLogo logoURL: String) -> String? {",
     test_a_server_logo_url_maps_to_its_fmp_symbol, "CompanyLogoCache.symbol(forLogoURL:) is gone"),
    (_CACHE, 'host == "images.financialmodelingprep.com" && parts[1] == "symbol"', 'parts[1] == "symbol"',
     test_a_server_logo_url_maps_to_its_fmp_symbol, "symbol(forLogoURL:) must accept only FMP's two logo files"),
    (_CACHE, 'host == "financialmodelingprep.com" && parts[1] == "image-stock"',
     'host.hasSuffix("financialmodelingprep.com")',
     test_a_server_logo_url_maps_to_its_fmp_symbol, "symbol(forLogoURL:) must accept only FMP's two logo files"),
    (_CACHE, '        guard parts.count == 3, parts[0] == "/" else { return nil }\n',
     "        guard parts.count >= 3 else { return nil }\n",
     test_a_server_logo_url_maps_to_its_fmp_symbol, "symbol(forLogoURL:) must accept only a one-directory path"),
    (_CACHE, 'parts[2].lowercased().hasSuffix(".png")', "true",
     test_a_server_logo_url_maps_to_its_fmp_symbol,
     "symbol(forLogoURL:) must refuse anything but an FMP `.png` logo file"),
    (_CACHE, "return symbol(for: String(parts[2].dropLast(4)))", "return String(parts[2].dropLast(4))",
     test_a_server_logo_url_maps_to_its_fmp_symbol, "symbol(forLogoURL:) must normalise through symbol(for:)"),
    (_CACHE, "        let parts = url.pathComponents\n",
     '        if host.hasSuffix(".cloudfront.net") { return symbol(for: url.lastPathComponent) }\n'
     "        let parts = url.pathComponents\n",
     test_a_server_logo_url_maps_to_its_fmp_symbol,
     "symbol(forLogoURL:) must have exactly three `return nil` refusals"),
]


@pytest.mark.parametrize(
    "path,old,new,test,message",
    _MUTATIONS,
    ids=[f"{p.name}:{i}" for i, (p, *_rest) in enumerate(_MUTATIONS)],
)
def test_each_mutation_is_killed(monkeypatch, path, old, new, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — other sessions' tests read these Swift files concurrently,
    so they are never rewritten."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    assert original.count(old) == 1, (
        f"mutation anchor `{old[:60]}` occurs {original.count(old)} times in {path.name} (expected "
        "exactly once) — re-derive this mutation against the new source rather than deleting it")
    mutated = original.replace(old, new, 1)
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    # The unmutated source passes (the plain tests above prove it); mutated, it must fail.
    with pytest.raises(AssertionError, match=re.escape(message)):
        test()
