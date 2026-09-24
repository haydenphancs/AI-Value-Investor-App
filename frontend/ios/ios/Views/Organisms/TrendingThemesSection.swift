//
//  TrendingThemesSection.swift
//  ios
//
//  Organism: the "Emerging Frontiers" carousel. Tiles are grouped into columns
//  of two (a vertical pair); the columns scroll horizontally, so one swipe moves
//  a whole column — both stacked cards — at once.
//
//  ENDLESS (owner request, 2026-09-23: "scroll left or right like a loop"). The strip is K
//  identical copies of the columns (`ThemeCarouselLoop`); the reader starts in the middle
//  copy, and when scrolling comes fully to REST in an outer copy the strip jumps, unanimated,
//  to the same column of the middle copy. Every copy draws the same pixels, so the jump is
//  invisible and a swipe never meets an end. Momentum, view-aligned paging and the edge bleed
//  are the real ScrollView's.
//
//  ⚠️ Why not `.scrollPosition(id:)`, the documented API for this: it writes its binding back
//  DURING layout, and on Home that froze the main thread (see DailyScannersSection). It is
//  banned app-wide by test_ios_collapse_scroll_guards.py. The sanctioned tools are used here
//  instead: a `ScrollViewReader` that ENCLOSES the scroll view, stable `.id`s, an imperative
//  unanimated `scrollTo`, and scroll geometry read from the scroll view itself
//  (`onScrollGeometryChange` / `onScrollPhaseChange`), never a `GeometryReader`.
//

import SwiftUI

struct TrendingThemesSection: View {
    let themes: [TrendingTheme]
    var onThemeTap: ((TrendingTheme) -> Void)? = nil

    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOverEnabled
    /// Decoded hero bitmaps keyed by URL, shared by every copy of a tile.
    @State private var heroes: [String: UIImage] = [:]
    /// The layout the strip was last parked for. A new layout (theme count, VoiceOver,
    /// width) re-parks it in the middle copy; the same layout never does, so a data refresh
    /// or returning from a theme's detail leaves the reader exactly where they were.
    @State private var parkedLayout: ThemeCarouselLoop.Layout?
    @State private var parkedWidth: CGFloat = 0
    /// The column within one period (0..<unit) the reader last rested on.
    @State private var restingPhase = 0

    private static let columnSpacing: CGFloat = 12
    /// ~2× a tile's widest band at 3× scale, so the cover-crop never upsamples.
    private static let heroMaxPixelSize: CGFloat = 720

    var body: some View {
        let layout = ThemeCarouselLoop.layout(themeCount: themes.count,
                                              voiceOverEnabled: voiceOverEnabled)
        VStack(alignment: .leading, spacing: 0) {
            // Header (padded; the carousel below bleeds to the screen edges).
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {
                    Text("Emerging Frontiers")
                        .font(AppTypography.heading)
                        .foregroundColor(AppColors.textPrimary)
                    Spacer(minLength: 0)
                    // One date for the whole section: every theme is reviewed in the same
                    // monthly run, so repeating it on eight cards would be noise.
                    if let reviewed = themes.compactMap(\.reviewedOn).max() {
                        Text("Updated \(ThemeReviewDate.short(reviewed))")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(1)
                    }
                }

                Text("The industries shaping the next decade")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.top, 4)
                    .padding(.bottom, 13)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, AppSpacing.lg)

            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: Self.columnSpacing) {
                        ForEach(0..<layout.totalColumns, id: \.self) { g in
                            column(g, layout: layout)
                                // Each column is one of two that fill the row, so its width
                                // matches the previous two-up card and a swipe advances a
                                // full column (both stacked cards) at a time.
                                .containerRelativeFrame(.horizontal) { length, _ in
                                    (length - 2 * AppSpacing.lg - Self.columnSpacing) / 2
                                }
                                .id(ThemeColumnID(index: g))
                                // One period — every theme once — is real to assistive tech,
                                // starting at the resting column so both visible columns are
                                // reachable; the other copies exist to be scrolled through.
                                .accessibilityHidden(!ThemeCarouselLoop.isExposed(
                                    g, restingPhase: restingPhase, layout: layout))
                        }
                    }
                    .scrollTargetLayout()
                }
                .scrollTargetBehavior(.viewAligned)
                .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)
                // Park in the middle copy once the strip has a width — and again only when
                // the layout or width actually changes.
                .onScrollGeometryChange(for: CGFloat.self) { geometry in
                    geometry.contentSize.width
                } action: { _, width in
                    // Not looping (VoiceOver on, or too few themes): forget the park, so the
                    // loop re-parks in the middle copy when it comes back. Keeping it left the
                    // strip at copy 0 after VoiceOver went off — a hard edge on the first
                    // backward swipe, and tiles hidden from Voice Control until then.
                    guard layout.loops else { parkedLayout = nil; return }
                    guard width > 0,
                          parkedLayout != layout || abs(parkedWidth - width) > 0.5 else { return }
                    parkedLayout = layout
                    parkedWidth = width
                    jump(proxy, to: ThemeCarouselLoop.parkingColumn(phase: restingPhase, layout: layout))
                }
                // The silent re-centre. `.idle` arrives only once the strip is fully at rest:
                // a finger on it is `.tracking`/`.interacting`, a fling is `.decelerating`, so
                // this can never fire mid-gesture. Ignored until the strip is parked: the
                // scroll view reports an `.idle` during its FIRST layout, before its width and
                // insets are real (measured: width 228, inset 0), and acting on that would
                // record a meaningless resting column.
                .onScrollPhaseChange { _, phase, context in
                    guard phase == .idle, layout.loops, parkedLayout == layout else { return }
                    let geometry = context.geometry
                    guard let g = ThemeCarouselLoop.restingColumn(
                        offsetX: geometry.contentOffset.x,
                        leadingInset: geometry.contentInsets.leading,
                        contentWidth: geometry.contentSize.width,
                        spacing: Self.columnSpacing,
                        totalColumns: layout.totalColumns
                    ) else { return }
                    restingPhase = g % layout.unit
                    if let target = ThemeCarouselLoop.recentreTarget(from: g, layout: layout) {
                        jump(proxy, to: target)
                    }
                }
            }
        }
        .task(id: heroKey) { await loadHeroes() }
    }

    // MARK: - Columns

    private func column(_ g: Int, layout: ThemeCarouselLoop.Layout) -> some View {
        VStack(spacing: 12) {
            // Keyed by SLOT, never by `TrendingTheme.id`: every copy of a theme shares that
            // id (two equal ids in one ForEach collapse), and it is a fresh UUID on every
            // Home refresh, which would rebuild every tile once a minute.
            ForEach(Array(ThemeCarouselLoop.themeIndices(inColumn: g, layout: layout).enumerated()),
                    id: \.offset) { _, index in
                if themes.indices.contains(index) {
                    let theme = themes[index]
                    TrendingThemeTile(theme: theme, hero: .shared(heroImage(for: theme))) {
                        onThemeTap?(theme)
                    }
                }
            }
        }
    }

    /// Unanimated on purpose: the destination shows the same pixels as the origin, so any
    /// animation would be the only visible thing about the jump. Retried once after the
    /// current update in case the first call lands before layout — harmless, because the
    /// destination is the same column either way.
    private func jump(_ proxy: ScrollViewProxy, to column: Int) {
        var transaction = Transaction()
        transaction.animation = nil
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            proxy.scrollTo(ThemeColumnID(index: column), anchor: .leading)
        }
        DispatchQueue.main.async {
            withTransaction(transaction) {
                proxy.scrollTo(ThemeColumnID(index: column), anchor: .leading)
            }
        }
    }

    // MARK: - Hero images (one fetch + one decode per theme, shared by every copy)

    private var heroURLs: [URL] {
        let unique = Set(themes.compactMap { theme -> String? in
            guard let s = theme.imageUrl, s.hasPrefix("http"), URL(string: s) != nil else { return nil }
            return s
        })
        return unique.sorted().compactMap(URL.init(string:))
    }

    /// The `.task` identity: a Home refresh with the same images does not reload them. While
    /// an image is still missing, the key also carries this refresh's theme id — a fresh UUID
    /// every Home refresh — so a failed hero retries once a minute, as the per-tile AsyncImage
    /// it replaced did; before that, one failed fetch left a gradient until the next launch.
    /// Once every hero has loaded the key stops changing.
    private var heroKey: String {
        let urls = heroURLs
        let base = urls.map(\.absoluteString).joined(separator: "|")
        let complete = urls.allSatisfy { heroes[$0.absoluteString] != nil }
        return complete ? base : base + "#" + (themes.first.map { $0.id.uuidString } ?? "")
    }

    private func heroImage(for theme: TrendingTheme) -> UIImage? {
        guard let s = theme.imageUrl else { return nil }
        return heroes[s]
    }

    private func loadHeroes() async {
        let missing = heroURLs.filter { heroes[$0.absoluteString] == nil }
        guard !missing.isEmpty else { return }
        let maxPixelSize = Self.heroMaxPixelSize
        await withTaskGroup(of: (String, UIImage?).self) { group in
            for url in missing {
                group.addTask {
                    (url.absoluteString,
                     await DownsampledImageLoader.shared.image(at: url, maxPixelSize: maxPixelSize))
                }
            }
            for await (key, image) in group {
                // A failure leaves the accent gradient; the loader already logged it.
                if let image { heroes[key] = image }
            }
        }
    }
}

/// A column's scroll identity: its position in the whole strip, unique across copies.
private struct ThemeColumnID: Hashable {
    let index: Int
}

#Preview {
    TrendingThemesSection(themes: MockHomeRepository.themes)
        .padding(.vertical)
        .background(AppColors.background)
}
