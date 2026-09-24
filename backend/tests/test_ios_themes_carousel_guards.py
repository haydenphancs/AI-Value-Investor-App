"""The endless Emerging Frontiers carousel (owner request 2026-09-23: "scroll left or right like
a loop, unstop" → endless MANUAL swipe, nothing moves on its own).

How it works: `TrendingThemesSection` lays out K identical copies of the theme columns, parks
the reader in the middle copy, and — only when scrolling is fully at rest in an outer copy —
jumps unanimated to the same column of the middle copy. Every copy draws the same pixels, so
the jump is invisible and a swipe never meets an end.

Three halves, pinned three ways:
  A. `ThemeCarouselLoop` (the arithmetic) and `ImageDownsampler` are EXECUTED with
     `xcrun swift -` — there is no XCTest target.
  B. The section is pinned by comment-stripped, brace-bounded scans: the traps that froze Home
     before (`.scrollPosition`, lazy stacks, GeometryReader, animated scrolls) stay out, and
     the pieces that make the loop invisible (idle-only re-centre, unanimated jump, per-slot
     ids, one real copy for assistive tech) stay in.
  C. The shared hero loader: each image fetched and decoded ONCE — an AsyncImage per tile
     would decode ~40 full-size 1290×1080 heroes (~220 MB) — and a failure never cached.
Every scan was mutation-tested by hand (`.claude/rules/testing.md` §3).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
IOS = BACKEND.parent / "frontend/ios/ios"
LOOP = IOS / "Core/Utilities/ThemeCarouselLoop.swift"
DOWNSAMPLER = IOS / "Core/Utilities/ImageDownsampler.swift"
LOADER = IOS / "Core/Services/DownsampledImageLoader.swift"
SECTION = IOS / "Views/Organisms/TrendingThemesSection.swift"
TILE = IOS / "Views/Molecules/TrendingThemeTile.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} is missing — every assertion below would be vacuous"
    return _strip_comments(path.read_text())


def _block_after(src: str, anchor: str) -> str:
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found — this scan has drifted"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    pytest.fail(f"unbalanced braces after `{anchor}`")


# ── A. The arithmetic, executed ───────────────────────────────────────────────────────

HARNESS = r"""
import Foundation
import ImageIO
import UniformTypeIdentifiers

var failures = 0
func check<T: Equatable>(_ name: String, _ got: T, _ expect: T) {
    if got == expect { print("ok|\(name)") }
    else { failures += 1; print("FAIL|\(name)|got=\(got)|expect=\(expect)") }
}
typealias L = ThemeCarouselLoop
func shape(_ n: Int, vo: Bool = false) -> [Int] {
    let l = L.layout(themeCount: n, voiceOverEnabled: vo)
    return [l.loops ? 1 : 0, l.periodLength, l.unit, l.copies, l.middle, l.totalColumns]
}

// Layout: [loops, period, unit, copies, middle, totalColumns]
check("layout_0", shape(0), [0, 0, 0, 1, 0, 0])
check("layout_negative", shape(-3), [0, 0, 0, 1, 0, 0])
check("layout_1", shape(1), [0, 1, 1, 1, 0, 1])
check("layout_3_single_last_column", shape(3), [0, 3, 2, 1, 0, 2])
check("layout_4_fits_one_screen", shape(4), [0, 4, 2, 1, 0, 2])
check("layout_5_odd_played_twice", shape(5), [1, 10, 5, 5, 2, 25])
check("layout_6", shape(6), [1, 6, 3, 7, 3, 21])
check("layout_7", shape(7), [1, 14, 7, 5, 2, 35])
check("layout_8_the_live_count", shape(8), [1, 8, 4, 5, 2, 20])
check("layout_9", shape(9), [1, 18, 9, 3, 1, 27])
check("layout_16", shape(16), [1, 16, 8, 3, 1, 24])
check("layout_5_voiceover_is_static", shape(5, vo: true), [0, 5, 3, 1, 0, 3])
check("layout_8_voiceover_is_static", shape(8, vo: true), [0, 8, 4, 1, 0, 4])
check("layout_8_first_middle", L.layout(themeCount: 8, voiceOverEnabled: false).firstMiddleColumn, 8)

// Runway: at least `bufferColumnsPerSide` columns on BOTH sides of the middle copy.
var runway = 0
for n in 5...60 {
    let l = L.layout(themeCount: n, voiceOverEnabled: false)
    check("runway_left_n\(n)", l.middle * l.unit >= L.bufferColumnsPerSide, true)
    check("runway_right_n\(n)", (l.copies - l.middle - 1) * l.unit >= L.bufferColumnsPerSide, true)
    check("copies_odd_n\(n)", l.copies % 2, 1)
    runway += 1
}
check("runway_ran", runway, 56)

// Columns: which themes each column shows.
let l8 = L.layout(themeCount: 8, voiceOverEnabled: false)
check("cols8_0", L.themeIndices(inColumn: 0, layout: l8), [0, 1])
check("cols8_3", L.themeIndices(inColumn: 3, layout: l8), [6, 7])
check("cols8_next_copy", L.themeIndices(inColumn: 4, layout: l8), [0, 1])
check("cols8_last", L.themeIndices(inColumn: 19, layout: l8), [6, 7])
check("cols8_negative_wraps", L.themeIndices(inColumn: -1, layout: l8), [6, 7])
let l5 = L.layout(themeCount: 5, voiceOverEnabled: false)
check("cols5_period", (0..<5).map { L.themeIndices(inColumn: $0, layout: l5) },
      [[0, 1], [2, 3], [4, 0], [1, 2], [3, 4]])
let l5vo = L.layout(themeCount: 5, voiceOverEnabled: true)
check("cols5_static_last_single", (0..<3).map { L.themeIndices(inColumn: $0, layout: l5vo) },
      [[0, 1], [2, 3], [4]])
let l3 = L.layout(themeCount: 3, voiceOverEnabled: false)
check("cols3_static", (0..<2).map { L.themeIndices(inColumn: $0, layout: l3) }, [[0, 1], [2]])
check("cols_empty", L.themeIndices(inColumn: 0, layout: L.layout(themeCount: 0, voiceOverEnabled: false)), [])

// Every theme appears equally often in a period, and the 4 tiles on screen are distinct.
var fairness = 0
for n in 5...16 {
    let l = L.layout(themeCount: n, voiceOverEnabled: false)
    var seen = [Int: Int]()
    for g in 0..<l.unit { for i in L.themeIndices(inColumn: g, layout: l) { seen[i, default: 0] += 1 } }
    check("fair_all_present_n\(n)", seen.count, n)
    check("fair_even_n\(n)", Set(seen.values), [l.periodLength / n])
    for g in 0..<(l.totalColumns - 1) {
        let visible = L.themeIndices(inColumn: g, layout: l) + L.themeIndices(inColumn: g + 1, layout: l)
        if Set(visible).count != 4 { check("distinct_on_screen_n\(n)_g\(g)", Set(visible).count, 4) }
    }
    fairness += 1
}
check("fairness_ran", fairness, 12)

// Resting column: 20 columns of 174.5pt with 12pt gaps, 16pt leading margin → pitch 186.5.
let width: CGFloat = 20 * 174.5 + 19 * 12
func rest(_ offset: CGFloat, inset: CGFloat = 16, w: CGFloat = width, s: CGFloat = 12, n: Int = 20) -> Int? {
    L.restingColumn(offsetX: offset, leadingInset: inset, contentWidth: w, spacing: s, totalColumns: n)
}
check("rest_col0_at_start", rest(-16), 0)
check("rest_col8", rest(8 * 186.5 - 16), 8)
check("rest_col8_within_tolerance", rest(8 * 186.5 - 16 + 0.9), 8)
check("rest_mid_swipe_is_nil", rest(8 * 186.5 - 16 + 2), nil)
check("rest_col18_last_resting", rest(18 * 186.5 - 16), 18)
check("rest_rubber_band_left_nil", rest(-30), nil)
check("rest_past_end_nil", rest(20 * 186.5 - 16), nil)
check("rest_nan_offset", rest(.nan), nil)
check("rest_inf_offset", rest(.infinity), nil)
check("rest_zero_width", rest(0, w: 0), nil)
check("rest_no_columns", rest(0, n: 0), nil)
check("rest_nan_spacing", rest(0, s: .nan), nil)
check("rest_negative_spacing", rest(0, s: -12), nil)

// Re-centre: the same column of the middle copy, and never from inside it.
check("recentre_from_middle_nil", L.recentreTarget(from: 8, layout: l8), nil)
check("recentre_from_middle_end_nil", L.recentreTarget(from: 11, layout: l8), nil)
check("recentre_left_copy", L.recentreTarget(from: 3, layout: l8), 11)
check("recentre_first_column", L.recentreTarget(from: 0, layout: l8), 8)
check("recentre_right_copy", L.recentreTarget(from: 12, layout: l8), 8)
check("recentre_last_column", L.recentreTarget(from: 19, layout: l8), 11)
check("recentre_out_of_range_low", L.recentreTarget(from: -1, layout: l8), nil)
check("recentre_out_of_range_high", L.recentreTarget(from: 20, layout: l8), nil)
check("recentre_not_looping", L.recentreTarget(from: 0, layout: l5vo), nil)
var sameContent = 0
for n in 5...12 {
    let l = L.layout(themeCount: n, voiceOverEnabled: false)
    for g in 0..<l.totalColumns {
        if let t = L.recentreTarget(from: g, layout: l) {
            // The jump is invisible ONLY because both columns draw the same themes.
            if L.themeIndices(inColumn: t, layout: l) != L.themeIndices(inColumn: g, layout: l)
                || t / l.unit != l.middle {
                check("recentre_same_content_n\(n)_g\(g)", false, true)
            }
        }
    }
    sameContent += 1
}
check("same_content_ran", sameContent, 8)

// Parking keeps the reader's column across a layout change.
check("park_phase0", L.parkingColumn(phase: 0, layout: l8), 8)
check("park_phase3", L.parkingColumn(phase: 3, layout: l8), 11)
check("park_phase_wraps", L.parkingColumn(phase: 9, layout: l8), 9)
check("park_phase_negative", L.parkingColumn(phase: -1, layout: l8), 11)
check("park_empty", L.parkingColumn(phase: 2, layout: L.layout(themeCount: 0, voiceOverEnabled: false)), 0)

// Assistive tech: exactly one period is exposed, starting at the resting column, so BOTH
// visible columns are always reachable (the old middle-copy rule hid column 12 for n = 8
// at phase 3) and every theme is exposed exactly as often as a period shows it.
var exposure = 0
for n in 5...16 {
    let l = L.layout(themeCount: n, voiceOverEnabled: false)
    for phase in 0..<l.unit {
        let lead = L.parkingColumn(phase: phase, layout: l)
        let exposed = (0..<l.totalColumns).filter { L.isExposed($0, restingPhase: phase, layout: l) }
        if exposed.count != l.unit { check("expose_count_n\(n)_p\(phase)", exposed.count, l.unit) }
        if !(L.isExposed(lead, restingPhase: phase, layout: l)
             && L.isExposed(lead + 1, restingPhase: phase, layout: l)) {
            check("expose_both_visible_n\(n)_p\(phase)", false, true)
        }
        var seen = [Int: Int]()
        for g in exposed { for i in L.themeIndices(inColumn: g, layout: l) { seen[i, default: 0] += 1 } }
        if seen.count != n || Set(seen.values) != [l.periodLength / n] {
            check("expose_every_theme_n\(n)_p\(phase)", seen.count, n)
        }
    }
    exposure += 1
}
check("exposure_ran", exposure, 12)
check("expose_n8_p3_second_visible", L.isExposed(12, restingPhase: 3, layout: l8), true)
check("expose_n8_p3_behind", L.isExposed(10, restingPhase: 3, layout: l8), false)
check("expose_static_all", (0..<3).allSatisfy { L.isExposed($0, restingPhase: 0, layout: l5vo) }, true)

// ImageDownsampler: display-sized, orientation kept, never upscaled, junk rejected.
func encoded(_ w: Int, _ h: Int, _ type: UTType) -> Data {
    let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpaceCreateDeviceRGB(),
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.setFillColor(CGColor(red: 0.2, green: 0.4, blue: 0.8, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    let out = NSMutableData()
    let dest = CGImageDestinationCreateWithData(out, type.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, ctx.makeImage()!, nil)
    CGImageDestinationFinalize(dest)
    return out as Data
}
let hero = encoded(1290, 1080, .jpeg)
let small = ImageDownsampler.downsample(hero, maxPixelSize: 720)
check("ds_width", small?.width, 720)
check("ds_height_keeps_aspect", (602...604).contains(small?.height ?? 0), true)
let portrait = ImageDownsampler.downsample(encoded(1080, 1290, .png), maxPixelSize: 720)
check("ds_portrait_long_side", portrait?.height, 720)
check("ds_never_upscales", ImageDownsampler.downsample(hero, maxPixelSize: 5000)?.width, 1290)
check("ds_empty", ImageDownsampler.downsample(Data(), maxPixelSize: 720) == nil, true)
check("ds_garbage", ImageDownsampler.downsample(Data("<html>404</html>".utf8), maxPixelSize: 720) == nil, true)
check("ds_nan_size", ImageDownsampler.downsample(hero, maxPixelSize: .nan) == nil, true)
check("ds_subpixel_size", ImageDownsampler.downsample(hero, maxPixelSize: 0.5) == nil, true)
check("ds_infinite_size", ImageDownsampler.downsample(hero, maxPixelSize: .infinity) == nil, true)

print("DONE|\(failures)")
"""


@pytest.fixture(scope="module")
def swift_output() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    source = LOOP.read_text() + "\n" + DOWNSAMPLER.read_text() + "\n" + HARNESS
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=source,
                              text=True, capture_output=True, timeout=240)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail("the Swift harness did not complete — a helper probably stopped compiling "
                    f"standalone (a SwiftUI/UIKit import?)\nstdout:\n{proc.stdout[-3000:]}\n"
                    f"stderr:\n{proc.stderr[-3000:]}")
    return proc.stdout


def test_every_loop_and_downsample_case(swift_output: str):
    failures = [l for l in swift_output.splitlines() if l.startswith("FAIL|")]
    assert not failures, "\n  ".join(failures)


def test_the_harness_actually_asserted_something(swift_output: str):
    oks = {l.split("|", 1)[1] for l in swift_output.splitlines() if l.startswith("ok|")}
    assert len(oks) >= 200, f"only {len(oks)} checks ran"
    for required in ("layout_8_the_live_count", "layout_5_voiceover_is_static", "runway_ran",
                     "cols5_period", "fairness_ran", "rest_mid_swipe_is_nil", "recentre_left_copy",
                     "same_content_ran", "park_phase_negative", "ds_width", "ds_never_upscales",
                     "ds_garbage", "exposure_ran", "expose_n8_p3_second_visible"):
        assert required in oks, required


@pytest.mark.parametrize("path", [LOOP, DOWNSAMPLER], ids=["loop", "downsampler"])
def test_helpers_are_standalone(path):
    raw = path.read_text()
    code = _strip_comments(raw)
    assert "import SwiftUI" not in code and "import UIKit" not in code
    assert re.search(r"\bnonisolated enum \w+", code), "helper must be a nonisolated enum"
    assert "xcrun swift" in raw, "the header no longer explains why the file is UI-free (anti-vacuity)"


# ── B. The section ────────────────────────────────────────────────────────────────────

def _section() -> str:
    return _block_after(_code(SECTION), "struct TrendingThemesSection: View")


def test_the_reader_encloses_the_horizontal_scroll_view():
    """A `ScrollViewReader` NESTED inside the ScrollView is a silent no-op: `scrollTo` compiles,
    runs and does nothing, and the carousel would simply stop looping."""
    section = _section()
    reader = _block_after(section, "ScrollViewReader { proxy in")
    assert "ScrollView(.horizontal" in reader
    assert section.find("ScrollViewReader {") < section.find("ScrollView(.horizontal")


def test_no_banned_scroll_or_layout_machinery():
    section = _section()
    for banned in (".scrollPosition(", "ScrollPosition(", "LazyHStack", "LazyVStack", "LazyHGrid",
                   "LazyVGrid", "GeometryReader", "TimelineView", "repeatForever", "withAnimation",
                   "AsyncImage", "Timer"):
        assert banned not in section, (
            f"`{banned}` is in TrendingThemesSection. .scrollPosition writes state during layout "
            "(froze Home); lazy stacks re-walk on resize (froze Home); GeometryReader stops "
            "updating when culled; an animated scroll would make the silent jump visible; an "
            "AsyncImage per tile decodes one full-size hero per copy; and the loop must never "
            "move on its own (owner decision: endless MANUAL swipe).")


def test_the_recentre_runs_only_at_rest_and_only_via_the_pure_math():
    phase = _block_after(_section(), ".onScrollPhaseChange")
    assert "phase == .idle" in phase, "the jump could fire mid-gesture or mid-fling"
    assert "layout.loops" in phase
    assert "parkedLayout == layout" in phase, (
        "the scroll view reports an .idle during its first layout (width 228, inset 0 — "
        "measured); acting on it records a meaningless resting column")
    for needed in ("ThemeCarouselLoop.restingColumn(", "ThemeCarouselLoop.recentreTarget(",
                   "geometry.contentOffset.x", "geometry.contentInsets.leading",
                   "geometry.contentSize.width", "jump(proxy, to: target)"):
        assert needed in phase, needed


def test_the_jump_is_unanimated_and_retried():
    jump = _block_after(_code(SECTION), "private func jump(_ proxy: ScrollViewProxy")
    for needed in ("transaction.animation = nil", "transaction.disablesAnimations = true",
                   "withTransaction(transaction)", "proxy.scrollTo(ThemeColumnID(index: column), anchor: .leading)",
                   "DispatchQueue.main.async"):
        assert needed in jump, needed
    assert jump.count("proxy.scrollTo(") == 2
    assert "withAnimation" not in jump and "animation: ." not in jump


def test_parking_happens_once_per_layout():
    """Parking on EVERY geometry update would yank the reader back to the middle copy's first
    column after each Home refresh or on returning from a theme — it must re-park only when the
    layout or width actually changed, and on the column the reader last rested on."""
    section = _section()
    transform = _block_after(section, ".onScrollGeometryChange(for: CGFloat.self)")
    assert "geometry.contentSize.width" in transform
    action = _block_after(section, "} action: { _, width in")
    assert "ThemeCarouselLoop.restingColumn(" not in action, "scan drifted into the phase handler"
    assert "parkedLayout != layout" in action and "parkedWidth" in action
    assert "ThemeCarouselLoop.parkingColumn(phase: restingPhase, layout: layout)" in action
    # Not looping → the park is FORGOTTEN, so the loop re-parks when it comes back
    # (VoiceOver off again, or the theme count recovering). Returning early left the strip
    # at copy 0: a hard edge on the first backward swipe.
    assert "guard layout.loops else { parkedLayout = nil; return }" in action


def test_columns_have_unique_ids_and_tiles_are_keyed_by_slot():
    section = _section()
    assert "ForEach(0..<layout.totalColumns, id: \\.self)" in section
    assert ".id(ThemeColumnID(index: g))" in section
    column = _block_after(_code(SECTION), "private func column(_ g: Int")
    assert "id: \\.offset" in column, "tiles must be keyed by slot, not by TrendingTheme.id"
    assert "ThemeCarouselLoop.themeIndices(inColumn: g, layout: layout)" in column
    assert "themes.indices.contains(index)" in column
    assert "ForEach(columns" not in _code(SECTION) and "ForEach(themes)" not in _code(SECTION)


def test_only_one_period_is_visible_to_assistive_tech():
    section = _section()
    assert (".accessibilityHidden(!ThemeCarouselLoop.isExposed(\n"
            "                                    g, restingPhase: restingPhase, layout: layout))"
            in section)
    assert "@Environment(\\.accessibilityVoiceOverEnabled) private var voiceOverEnabled" in _code(SECTION)
    assert ("ThemeCarouselLoop.layout(themeCount: themes.count,\n"
            in section or "ThemeCarouselLoop.layout(themeCount: themes.count," in section)
    assert "voiceOverEnabled: voiceOverEnabled" in section


def test_paging_and_bleed_are_kept():
    section = _section()
    for needed in (".scrollTargetLayout()", ".scrollTargetBehavior(.viewAligned)",
                   ".contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)",
                   ".containerRelativeFrame(.horizontal)"):
        assert needed in section, needed


# ── C. Shared hero images ─────────────────────────────────────────────────────────────

def test_every_copy_shares_one_decoded_hero():
    code = _code(SECTION)
    column = _block_after(code, "private func column(_ g: Int")
    assert "hero: .shared(heroImage(for: theme))" in column
    load = _block_after(code, "private func loadHeroes() async")
    assert "DownsampledImageLoader.shared.image(at: url, maxPixelSize: maxPixelSize)" in load
    assert "heroes[$0.absoluteString] == nil" in load, "already-loaded heroes must not refetch"
    assert ".task(id: heroKey)" in code
    # A failed hero retries on the next Home refresh (the per-refresh theme id joins the key
    # only while an image is missing); a complete set keeps a stable key.
    key = _block_after(code, "private var heroKey: String")
    assert "let complete = urls.allSatisfy { heroes[$0.absoluteString] != nil }" in key
    assert 'return complete ? base : base + "#" + (themes.first.map { $0.id.uuidString } ?? "")' in key


def test_the_tile_draws_a_shared_hero_without_its_own_loader():
    tile = _code(TILE)
    image = _block_after(tile, "@ViewBuilder private var themeImage: some View")
    shared = image[: image.find("} else if let s = theme.imageUrl")]
    assert "if case .shared(let image) = hero" in shared
    assert "Image(uiImage: image)" in shared and "accentFallback" in shared
    assert "AsyncImage" not in shared, "a shared hero must not start its own fetch"
    assert "case remote" in tile and "case shared(UIImage?)" in tile
    assert "var hero: ThemeHeroSource = .remote" in tile


def test_the_loader_dedups_decodes_off_actor_and_never_caches_a_failure():
    loader = _block_after(_code(LOADER), "actor DownsampledImageLoader")
    fetch = _block_after(loader, "func image(at url: URL, maxPixelSize: CGFloat) async -> UIImage?")
    assert "if let running = inflight[key]" in fetch, "concurrent loads must share one request"
    assert "Task.detached" in fetch, "the decode must not serialise behind the actor"
    assert "ImageDownsampler.downsample(data, maxPixelSize: maxPixelSize)" in fetch
    cache_write = fetch[fetch.find("inflight[key] = nil"):]
    assert "if let result {" in cache_write and "cache[key] = result" in cache_write, (
        "a failed load must not be cached — the next load has to retry")
    assert "try?" not in loader, "a swallowed error leaves no trace of why a hero is missing"
    assert fetch.count("Self.log.") >= 3, "every failure path must log"


def test_comment_stripping_is_real():
    raw = SECTION.read_text()
    assert "scrollPosition" in raw, "the header no longer explains the .scrollPosition ban"
    assert "scrollPosition" not in _strip_comments(raw)


# ── E. Theme detail fixes from the 2026-09-23 review ─────────────────────────────────

DETAIL_MODELS = IOS / "Models/ThemeDetailModels.swift"
DETAIL_VIEW = IOS / "Views/Screens/ThemeDetailView.swift"
CHANGES_CARD = IOS / "Views/Molecules/ThemeChangesCard.swift"
COMPANY_ROW = IOS / "Views/Molecules/ThemeCompanyRow.swift"


def _swift_func(src: str, anchor: str) -> str:
    """The whole `static func …` declaration, signature and braces included."""
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found — this scan has drifted"
    body = _block_after(src[at:], anchor)
    return src[at: at + src[at:].find(body)] + body


def test_the_sign_colour_follows_the_printed_number():
    """-0.0004 prints "+0.0%": colouring it by the raw value put a plus sign in red.
    `readsNonNegative` and `fractionPercent` are executed together here."""
    code = _code(DETAIL_MODELS)
    assert "themeIsPositive: ThemeDetailFormat.readsNonNegative(p.theme)" in code
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable")
    funcs = (_swift_func(code, "static func fractionPercent(_ value: Double?)") + "\n"
             + _swift_func(code, "static func readsNonNegative(_ value: Double?)"))
    harness = ("import Foundation\nenum F {\n" + funcs + "\n}\nvar bad = 0\n"
               "for v in [-0.0004, -0.00049, -0.0005, -0.0001, 0.0, 0.0004, -0.0421, 0.0421, "
               "-1e-12, .nan, .infinity] {\n"
               "  let text = F.fractionPercent(v)\n"
               "  let positive = F.readsNonNegative(v)\n"
               "  if text != \"—\" && (text.hasPrefix(\"+\") != positive) { bad += 1; print(\"BAD|\\(v)|\\(text)|\\(positive)\") }\n"
               "}\nprint(\"DONE|\\(bad)|\\(F.fractionPercent(-0.0004))|\\(F.readsNonNegative(-0.0004))\")\n")
    proc = subprocess.run(["xcrun", "swift", "-"], input=harness, text=True,
                          capture_output=True, timeout=240)
    assert "DONE|0|+0.0%|true" in proc.stdout, proc.stdout + proc.stderr[-1500:]


def test_the_performance_chart_uses_the_graphic_accent():
    models, view = _code(DETAIL_MODELS), _code(DETAIL_VIEW)
    assert "chartAccent: Color(themedHex: accentHex, role: .graphic, fallback: AppColors.primaryGraphic)" in models
    assert "ThemePerformanceCard(performance: performance, accent: detail.chartAccent)" in view


def test_change_and_new_badges_keep_text_contrast():
    """11pt text on its own tint: 0.14 measured 4.25:1 in light mode, 0.08 is 4.63:1."""
    card, row = _code(CHANGES_CARD), _code(COMPANY_ROW)
    assert ".background(Capsule().fill(tint(change.kind).opacity(0.08)))" in card
    assert ".background(Capsule().fill(AppColors.primaryBlue.opacity(0.08)))" in row
    assert "opacity(0.14)" not in card and "opacity(0.14)" not in row


def test_no_changes_copy_claims_only_what_the_rules_guarantee():
    card = _code(CHANGES_CARD)
    assert "closely tied" not in card
    assert "Reviewed — no changes this month." in card


def test_the_loader_rejects_an_unusable_size_before_converting_it():
    loader = _code(LOADER)
    fn = _block_after(loader, "func image(at url: URL, maxPixelSize: CGFloat) async -> UIImage?")
    guard_at = fn.find("guard maxPixelSize.isFinite, maxPixelSize >= 1 else")
    assert 0 <= guard_at < fn.find("Int(maxPixelSize"), "Int(CGFloat.nan) traps"
