//
//  MoversWidget.swift
//  CaydexWidgets
//
//  "How is the market doing, and what moved most of mine" — at a glance, on the Home Screen.
//
//  TWO MODES, TWO DIFFERENT QUESTIONS
//  • Market answers "what is the tape doing": the day's one-line brief, sector breadth and
//    the Home "Market Pulse" assets (S&P 500, Nasdaq, Dow, Russell 2000, Gold, Bitcoin). It
//    NEVER renders a single stock — a mover picked out of the whole market is not something
//    anyone glancing at a Market tile asked for.
//  • Holdings answers "what moved most of mine": the active portfolio's name and size, its
//    biggest |%| mover and why, how many rose and fell, and the top riser and faller.
//
//  WHO FETCHES WHAT. Market mode fetches for itself with the widget token (see
//  `WidgetMarketFetcher`). Holdings mode renders what the APP last wrote — the extension
//  holds no session and must never hold one (the long note in `WidgetSnapshotStore.swift`).
//  With no widget token at all, BOTH modes render the sign-in state and no stored data.
//
//  THE ONE RULE FOR CAUSES
//  A tag badge appears ONLY when `cause.kind.isEstablished` — i.e. the backend found a
//  dated, structured reason (earnings today, an analyst action today, a classified
//  headline, an industry move). `.none` renders its sentence with no badge, because a
//  badge implies a known cause. `.none` is the COMMON case, not an error.
//
//  Everything here is DAILY. There is no path by which a multi-day window can reach
//  this file; see `daily_move_attribution` for why that is structural.
//

import AppIntents
import OSLog
import SwiftUI
import WidgetKit

// MARK: - Timeline

struct MoversEntry: TimelineEntry {
    let date: Date
    let snapshot: WidgetMoverSnapshot?
    /// What this tile SHOWS: its configured mode, or the in-tile toggle's choice for tiles
    /// configured that way (Home Screen families only).
    var mode: MoversMode = .market
    /// What the user CONFIGURED in Edit Widget — the key the toggle's choice is stored under,
    /// so a tap on this tile cannot flip a tile configured the other way.
    var configuredMode: MoversMode = .market
    /// No widget token: signed out (or not yet minted). Renders "Sign in to Caydex…" in BOTH
    /// modes and never stored data — FMP data may be shown only to an authenticated user.
    var isSignedOut: Bool = false
    /// True when no snapshot exists at all — first install, or the app has never run.
    var isPlaceholder: Bool { snapshot == nil }
}

struct MoversProvider: AppIntentTimelineProvider {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    func placeholder(in context: Context) -> MoversEntry {
        MoversEntry(date: Date(), snapshot: .preview)
    }

    func snapshot(for configuration: MoversConfigurationIntent, in context: Context) async -> MoversEntry {
        // The gallery preview must never render an empty card, or the widget looks
        // broken before it has been added even once.
        //
        // It shows the CONFIGURED mode, never the in-tile toggle's: a tap on a tile already
        // on the Home Screen is not a reason for the "add widget" gallery to advertise the
        // other layout under the default configuration.
        if context.isPreview {
            let previewMode: MoversMode = configuration.mode
            let sample: WidgetMoverSnapshot = (previewMode == .portfolio) ? .previewHoldings : .preview
            return MoversEntry(
                date: Date(), snapshot: sample, mode: previewMode, configuredMode: configuration.mode
            )
        }
        let mode = effectiveMode(for: configuration, family: context.family)
        return entry(mode: mode, configuration: configuration)
    }

    func timeline(for configuration: MoversConfigurationIntent, in context: Context) async -> Timeline<MoversEntry> {
        let mode = effectiveMode(for: configuration, family: context.family)
        let now = Date()
        let reload = WidgetRefreshSchedule.nextRefresh(after: now)
        // SEVERAL entries over ONE snapshot — they buy an honest, self-ageing LABEL, not new
        // data. The day rollover is ET 00:01; see `WidgetRefreshSchedule.renderDates`.
        let dates = WidgetRefreshSchedule.renderDates(now: now, reload: reload)

        // Signed out: nothing stored may render and nothing may be fetched.
        guard let tokenAtFetch = WidgetAPIConfig.widgetToken else {
            let entries = dates.map { signedOutEntry(at: $0, mode: mode, configuration: configuration) }
            return Timeline(entries: entries, policy: .after(reload))
        }

        // THE EXTENSION FETCHES — Market mode only (see `WidgetMarketFetcher`). Holdings needs
        // an identity the extension must never hold, so it renders what the app last wrote.
        var snap = snapshot(for: mode)

        if mode == .market, let fresh = await WidgetMarketFetcher.fetchMarket() {
            // ⚠️ THE AWAIT IS A WINDOW A SIGN-OUT CAN LAND IN. The request carried a valid
            // token and widget tokens are not revocable, so the 200 arrives after the app has
            // cleared the token and every snapshot. Using it — let alone storing it — put FMP
            // prices back on a signed-out Home Screen for good.
            if WidgetAPIConfig.widgetToken == tokenAtFetch {
                snap = fresh
                // Store it WITHOUT a reload — see `writeFromExtension`. Not how the entries
                // below get the data (they already have it); it is so the next FAILED fetch
                // falls back to something current. The store re-checks the token itself.
                WidgetSnapshotStore.writeFromExtension(mode: .market, snapshot: fresh)
            } else {
                Self.log.warning("widget: market refresh discarded — the widget token changed while it was in flight")
            }
        }

        let signedOut = WidgetAPIConfig.widgetToken == nil

        // ONE MORE RENDER, AT THE INSTANT THE LABEL STARTS SPEAKING. During regular hours
        // `dates` is just [now] (the 20-minute reload replaces the rest), so a snapshot crossed
        // its 45-minute "As of 10:05 AM ET" line only at a reload built after it — up to a
        // reload interval late, and indefinitely late when WidgetKit deferred one. Decided
        // here, after the fetch, because only now is `snap` final. NOT filtered against the
        // reload: an entry past it costs nothing (a reload that runs replaces the timeline)
        // and is exactly what a deferred one needs.
        var entryDates: [Date] = dates
        if !signedOut, let snap,
           let boundary = WidgetSessionLabel.ageBoundary(
               asOf: snap.asOf, sessionDate: snap.sessionDate,
               marketSession: snap.marketSession, now: now
           ),
           !entryDates.contains(boundary) {
            entryDates.append(boundary)
            entryDates.sort()
        }

        let entries: [MoversEntry] = entryDates.map { date in
            if signedOut {
                return signedOutEntry(at: date, mode: mode, configuration: configuration)
            }
            return MoversEntry(date: date, snapshot: snap, mode: mode, configuredMode: configuration.mode)
        }
        // ⚠️ `.after(reload)`, NOT the last entry's date. Those used to be the same thing,
        // because the last entry WAS the next 00:01 — WidgetKit was told not to ask again
        // until tomorrow, so nothing could wake the extension during the day.
        return Timeline(entries: entries, policy: .after(reload))
    }

    /// The tile's mode: the in-tile toggle's choice for tiles CONFIGURED like this one, else
    /// the configuration.
    ///
    /// Home Screen families only. The Lock Screen families cannot host the toggle, so a tap
    /// on a Home Screen tile must never reach them — they had no way to tap back.
    private func effectiveMode(
        for configuration: MoversConfigurationIntent, family: WidgetFamily
    ) -> MoversMode {
        switch family {
        case .systemSmall, .systemMedium, .systemLarge:
            return WidgetModeOverride.current(for: configuration.mode) ?? configuration.mode
        default:
            return configuration.mode
        }
    }

    private func entry(mode: MoversMode, configuration: MoversConfigurationIntent) -> MoversEntry {
        guard WidgetAPIConfig.widgetToken != nil else {
            return signedOutEntry(at: Date(), mode: mode, configuration: configuration)
        }
        return MoversEntry(
            date: Date(), snapshot: snapshot(for: mode), mode: mode, configuredMode: configuration.mode
        )
    }

    private func signedOutEntry(
        at date: Date, mode: MoversMode, configuration: MoversConfigurationIntent
    ) -> MoversEntry {
        MoversEntry(
            date: date, snapshot: nil, mode: mode,
            configuredMode: configuration.mode, isSignedOut: true
        )
    }

    /// Each mode reads ONLY its own slot.
    ///
    /// Holdings used to fall back to the market snapshot when it had none — so an emptied
    /// group, a new account or a signed-out device showed market movers under "My
    /// Holdings". A Holdings tile with no Holdings snapshot now says so instead.
    private func snapshot(for mode: MoversMode) -> WidgetMoverSnapshot? {
        let envelope = WidgetSnapshotStore.read()
        switch mode {
        case .portfolio:
            return envelope?.portfolio
        case .market:
            return envelope?.market
        }
    }
}

// MARK: - Widget

struct MoversWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(
            kind: WidgetSharedConfig.moversKind,
            intent: MoversConfigurationIntent.self,
            provider: MoversProvider()
        ) { entry in
            MoversWidgetView(entry: entry)
                // Mandatory on iOS 17+. A widget without it does not render at all —
                // and the failure is a blank tile, not a build error.
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Market & Holdings")
        .description("How the market is doing, and your portfolio's biggest movers.")
        .supportedFamilies([
            .systemSmall, .systemMedium, .systemLarge,
            .accessoryRectangular, .accessoryInline,
        ])
    }
}

// MARK: - Root view

struct MoversWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: MoversEntry

    var body: some View {
        Group {
            switch family {
            // Lock Screen families are one to three lines of glanceable text, and they cannot
            // host a Button — so they have their own compact layouts and no toggle.
            case .accessoryInline:      InlineView(entry: entry)
            case .accessoryRectangular: RectangularView(entry: entry)
            default:                    homeScreen { homeContent }
            }
        }
        .widgetURL(tileURL)
    }

    /// The tile-wide tap target: the Holdings headline mover's detail screen.
    ///
    /// ONE per widget, set here and nowhere else (a second `.widgetURL` deeper in the tree
    /// is undefined behaviour). It is the only target a Small or Lock Screen tile has, and
    /// the fallback outside the row `Link`s on Medium/Large (see `TapThrough`).
    ///
    /// nil, so the app opens at its root, when signed out (the app shows the sign-in wall
    /// either way), with no snapshot, on a market payload standing in for Holdings, and in
    /// Market mode. A Market tile shows several assets and no single one is "the" tile, so
    /// its assets link one by one on Medium/Large instead.
    private var tileURL: URL? {
        guard !entry.isSignedOut, entry.mode == .portfolio,
              let snap = entry.snapshot, snap.mode == "portfolio",
              let headline = snap.headlineMover
        else { return nil }
        return headline.deepLink
    }

    @ViewBuilder
    private var homeContent: some View {
        if entry.isSignedOut {
            SignedOutView()
        } else if entry.mode == .market {
            MarketView(entry: entry)
        } else {
            HoldingsView(entry: entry)
        }
    }

    /// Home Screen content with ONE bottom row: the session footer, then the mode toggle.
    ///
    /// ⚠️ A ROW, NOT AN OVERLAY. The first toggle was pinned `.bottomTrailing` and on Small
    /// drew straight through the footer — "As of 2:14 PM E⇆ Holdings". That footer is the
    /// widget's honesty mechanism (the only thing saying whether a number is from today),
    /// so nothing may cover it. It used to sit on its own row ABOVE the toggle row, which
    /// cost Small its last line and left Large with no footer at all when the band was
    /// absent; one shared ~13pt row fixes both, and every Home Screen family has it.
    ///
    /// The content takes exactly the height left above the row, and is CLIPPED to it, so an
    /// overfull layout (the SE's medium tile is only ~116pt tall) loses its last content line,
    /// never the footer — the one line saying "Tue close" must be the last to go.
    private func homeScreen<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            content()
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .clipped()
            // The toggle drops its word before the footer is ever squeezed.
            ViewThatFits(in: .horizontal) {
                bottomRow(compactToggle: false)
                bottomRow(compactToggle: true)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func bottomRow(compactToggle: Bool) -> some View {
        HStack(alignment: .center, spacing: 4) {
            if let snap = entry.snapshot {
                SessionFooter(snapshot: snap, now: entry.date)
            }
            Spacer(minLength: 4)
            // Signed out, both modes show the same sign-in message — a switch would do nothing.
            if !entry.isSignedOut {
                ModeToggle(current: entry.mode, base: entry.configuredMode, compact: compactToggle)
            }
        }
    }
}

// MARK: - Shared states

/// The signed-out tile. Says what is wrong AND what fixes it — the old "Open the app to load
/// today's movers" looked identical to a first install, so a sign-out read as a broken widget.
private struct SignedOutView: View {
    var body: some View {
        MessageView(title: "Caydex", message: "Sign in to Caydex to see the market and your holdings")
    }
}

/// A titled one-sentence state: no data yet, nothing to show, signed out.
private struct MessageView: View {
    let title: String
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionCaption(title: title)
            Text(message)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(4)
                .minimumScaleFactor(0.85)
        }
    }
}

/// The small caps label above a section. `.secondary`, not `.tertiary`: tertiary was ~1.7:1
/// in light mode, which is decoration, not a label.
private struct SectionCaption: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(.secondary)
            .textCase(.uppercase)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
    }
}

// MARK: - Market mode

/// The Market tile, in every Home Screen family: what the tape is doing, never one stock.
///
/// ⚠️ MARKET MODE IS NOT A MOVER TILE. It used to be — the same biggest-mover layout as
/// Holdings over a different universe — and that answered the wrong question. The payload
/// still carries a mover for already-installed builds; this view never reads it.
///
/// Budgets (content area): Medium ~306×126pt (~289×116 on SE), Small ~126×126, Large
/// ~306×322. Header ~12 + brief 2×16 + three asset rows ~39 + the bottom row ~13 fits Small
/// and Medium; Large adds prices and the sector leaders.
private struct MarketView: View {
    @Environment(\.widgetFamily) private var family
    @Environment(\.dynamicTypeSize) private var typeSize
    let entry: MoversEntry

    var body: some View {
        if let snap = entry.snapshot {
            content(snap)
        } else {
            MessageView(title: "Market", message: "Open Caydex to load the market")
        }
    }

    /// One line at the larger text sizes, so the numbers below keep their room.
    private var briefLineLimit: Int {
        if family == .systemLarge { return typeSize >= .xLarge ? 2 : 3 }
        return typeSize >= .xLarge ? 1 : 2
    }

    @ViewBuilder
    private func content(_ snap: WidgetMoverSnapshot) -> some View {
        let rows: [WidgetIndex] = snap.marketRows
        VStack(alignment: .leading, spacing: 4) {
            MarketHeader(sentiment: snap.marketBrief?.sentiment, context: snap.marketContext)

            // The sentence is the reason this tile exists, so it outranks the numbers for
            // space. Without the priority SwiftUI recovers a cramped tile from the tallest
            // flexible element, which is exactly this one. No `fixedSize`: on the smallest
            // tiles it must be able to give up its second line rather than overflow.
            if let brief = snap.marketBrief {
                Text(brief.headline)
                    .font(.caption.weight(.semibold))
                    .lineLimit(briefLineLimit)
                    .minimumScaleFactor(0.85)
                    .layoutPriority(1)
            }

            if rows.isEmpty {
                if snap.marketBrief == nil {
                    Text("Market numbers are unavailable right now.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            } else if family == .systemLarge {
                Divider()
                AssetPriceList(assets: rows, limit: typeSize.isAccessibilitySize ? 3 : 6)
                if let mc = snap.marketContext {
                    Divider()
                    SectorLeaders(context: mc)
                }
            } else if family == .systemSmall || typeSize.isAccessibilitySize {
                // ~126pt cannot hold two "label  ±0.00%" cells side by side without scaling
                // the label past legibility, so Small lists three, full width.
                AssetColumn(assets: Array(rows.prefix(typeSize.isAccessibilitySize ? 2 : 3)))
            } else {
                AssetGrid(assets: Array(rows.prefix(6)))
            }
        }
    }
}

/// "MARKET · BULLISH" with sector breadth on the same row — the row costs nothing extra.
private struct MarketHeader: View {
    let sentiment: String?
    let context: WidgetMarketContext?

    private var title: String {
        if let s = sentiment?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty {
            return "Market · \(s)"
        }
        return "Market"
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            SectionCaption(title: title)
                .layoutPriority(1)
            Spacer(minLength: 4)
            if let up = context?.breadthUp, let total = context?.breadthTotal, total > 0 {
                // Breadth over the 11 SECTORS — a real population, so "3 of 11" is a defined
                // statistic. Shortens, then DROPS OUT — never truncates. On Small with a
                // sentiment the priority caption ("MARKET · NEUTRAL", ~100 of ~126pt) leaves
                // ~16pt, none of the strings fits, and `ViewThatFits` squeezed the last one
                // into "3…". The empty last option is what it falls back to instead.
                ViewThatFits(in: .horizontal) {
                    Text(verbatim: "\(up) of \(total) sectors up")
                    Text(verbatim: "\(up)/\(total) sectors up")
                    Text(verbatim: "\(up)/\(total) up")
                    Color.clear.frame(width: 0, height: 0)
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
        }
    }
}

/// Small/Medium: two columns of three, read row-wise (S&P 500 | Nasdaq, Dow | Russell 2000,
/// Gold | Bitcoin) — the Home pulse order. Two `VStack`s rather than a `Grid`: every cell
/// is one caption2 line, so the rows align by construction.
private struct AssetGrid: View {
    let assets: [WidgetIndex]

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            column(Array(stride(from: 0, to: assets.count, by: 2)))
            column(Array(stride(from: 1, to: assets.count, by: 2)))
        }
    }

    private func column(_ positions: [Int]) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(positions, id: \.self) { i in
                AssetCell(asset: assets[i])
                    .tapThrough(assets[i].deepLink)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct AssetColumn: View {
    let assets: [WidgetIndex]

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            // Small lists here too, where `tapThrough` draws no Link (one tap target per tile).
            ForEach(assets, id: \.symbol) { AssetCell(asset: $0).tapThrough($0.deepLink) }
        }
    }
}

/// One grid cell: the SHORT label and the change. No price, ever — see `AssetPriceRow`.
private struct AssetCell: View {
    let asset: WidgetIndex

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(asset.shortLabel ?? asset.label)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
            if asset.isRolling24h { RollingTag() }
            Spacer(minLength: 2)
            AssetChange(asset: asset, font: .caption2.weight(.semibold))
        }
    }
}

/// Large: the FULL label ("S&P 500 ETF") beside its price.
///
/// ⚠️ NEVER the short label here. An ETF's ~$650 printed beside "S&P 500" reads as the index
/// being off by 10× — which is why the server sends both and why only this row shows a price.
private struct AssetPriceRow: View {
    let asset: WidgetIndex

    private var priceText: String? {
        guard let p = asset.price, p.isFinite else { return nil }
        return p.formatted(.number.precision(.fractionLength(2)))
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(asset.label)
                .font(.caption)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
            if asset.isRolling24h { RollingTag() }
            Spacer(minLength: 4)
            if let priceText {
                Text(priceText)
                    .font(.caption)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            AssetChange(asset: asset, font: .caption.weight(.semibold))
                .frame(minWidth: 54, alignment: .trailing)
        }
    }
}

private struct AssetPriceList: View {
    let assets: [WidgetIndex]
    var limit: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(assets.prefix(limit), id: \.symbol) { AssetPriceRow(asset: $0).tapThrough($0.deepLink) }
        }
    }
}

/// An asset's change. "—" when there is no reading for this session (the server nulls a
/// row whose own session stamp is not the payload's) — never a fabricated 0.00%.
private struct AssetChange: View {
    @Environment(\.widgetRenderingMode) private var renderingMode
    let asset: WidgetIndex
    var font: Font

    /// Colour REINFORCES the signed number, never carries the direction alone — accessory
    /// and tinted modes flatten it away.
    private var tint: Color {
        guard asset.formattedChange != nil else { return .secondary }
        guard renderingMode == .fullColor else { return .primary }
        if asset.isFlat { return .secondary }
        return asset.isPositive ? .green : .red
    }

    var body: some View {
        Text(asset.formattedChange ?? "—")
            .font(font)
            .foregroundStyle(tint)
            .lineLimit(1)
            .fixedSize()
            .accessibilityLabel(asset.formattedChange ?? "no reading")
    }
}

/// Which parts of the market are pulling, and which are dragging.
///
/// Large only. Both halves are already on the payload — nothing extra is fetched — and
/// each renders independently, so a missing leader does not cost the laggard.
private struct SectorLeaders: View {
    @Environment(\.widgetRenderingMode) private var renderingMode
    let context: WidgetMarketContext

    private func tint(_ pct: Double?) -> Color {
        // Same rule as ChangeBadge: colour REINFORCES the signed number, never carries
        // the direction alone — accessory and tinted modes flatten it away.
        guard renderingMode == .fullColor, let pct else { return .secondary }
        return pct >= 0 ? .green : .red
    }

    @ViewBuilder
    private func row(_ caption: String, _ name: String?, _ pct: Double?) -> some View {
        if let name, !name.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(caption)
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
                Text(name)
                    .font(.caption2.weight(.medium))
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                if let pct {
                    Text(String(format: "%+.1f%%", pct))
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(tint(pct))
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            row("Leading", context.leadingSector, context.leadingSectorChangePercent)
            row("Lagging", context.laggingSector, context.laggingSectorChangePercent)
        }
    }
}

// MARK: - The in-tile toggle

/// The in-tile Market ⇄ Holdings switch.
///
/// Home Screen families only — Apple does not allow buttons on Lock Screen widgets, so
/// `.accessoryInline` / `.accessoryRectangular` keep the long-press configuration (and the
/// provider ignores the toggle for them).
///
/// Records the choice for tiles CONFIGURED as `base` — see `ToggleMoversModeIntent`.
private struct ModeToggle: View {
    let current: MoversMode
    let base: MoversMode
    /// Icon only, when the footer needs the width.
    var compact: Bool = false

    private var other: MoversMode { current == .market ? .portfolio : .market }
    private var title: String { other == .market ? "Market" : "Holdings" }

    var body: some View {
        Button(intent: ToggleMoversModeIntent(mode: other, base: base)) {
            HStack(spacing: 3) {
                Image(systemName: "arrow.left.arrow.right")
                    .font(.system(size: 8, weight: .bold))
                if !compact {
                    Text(title)
                        .font(.system(size: 9, weight: .semibold))
                        .lineLimit(1)
                }
            }
            .foregroundStyle(.secondary)
            // Horizontal room is free (the row has a Spacer); vertical room is not.
            .padding(.horizontal, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Show \(title)")
    }
}

// MARK: - Holdings mode

/// The Holdings tile: the active portfolio, its biggest mover and why, and how the rest did.
///
/// Every state names itself — the old tile said "No unusual moves to report" whether the
/// group was empty, unpriced, or the data had failed to load, and "Open the app" whether the
/// user was signed out or the app had simply never run.
private struct HoldingsView: View {
    @Environment(\.widgetFamily) private var family
    let entry: MoversEntry

    var body: some View {
        if let snap = entry.snapshot, snap.mode == "portfolio" {
            VStack(alignment: .leading, spacing: 4) {
                HoldingsHeader(snapshot: snap)
                if let m = snap.headlineMover {
                    layout(snap, headline: m)
                } else {
                    Text(Self.emptyMessage(for: snap))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                        .minimumScaleFactor(0.85)
                }
            }
            // Holdings, moves and the portfolio's name are the user's own: redacted on a
            // locked device (Lock Screen, StandBy). The market data elsewhere is not.
            .privacySensitive()
        } else {
            MessageView(title: "My Holdings", message: "Open Caydex to load your holdings")
        }
    }

    /// What a Holdings payload with no headline mover means — three different things.
    ///
    /// No "today" in any of them: the footer already says when the numbers are from, and a
    /// Friday snapshot read on Saturday said "No prices for your 12 holdings today" beside
    /// "Fri close". The Lock Screen's inline line shows this with no footer at all.
    static func emptyMessage(for snap: WidgetMoverSnapshot) -> String {
        guard let n = snap.holdingsCount else {
            // Degraded: the holdings or quotes were unreadable. Never stored over a good
            // snapshot, so reaching here means there was nothing better.
            return "Open Caydex to load your holdings"
        }
        if n <= 0 {
            if let name = snap.namedGroup { return "No holdings in \(name) yet" }
            return "No holdings in this portfolio yet"
        }
        return n == 1 ? "No price for your 1 holding" : "No prices for your \(n) holdings"
    }

    @ViewBuilder
    private func layout(_ snap: WidgetMoverSnapshot, headline m: WidgetMover) -> some View {
        // A previous session's cause must not say "today" beside a "Tue close" footer.
        //
        // A ROUND-THE-CLOCK headline ages by the ET day it was BUILT, not by the equity
        // session the payload is stamped with: its 24 h move belongs to that day. Judged by
        // the session date, a Saturday (or Monday pre-market) build — stamped Friday — was
        // "aged" on the very day it was built, and a live move got the past-tense wording.
        let aged: Bool = m.isRolling24h
            ? WidgetSessionLabel.isPriorETDay(asOf: snap.asOf, now: entry.date)
            : WidgetSessionLabel.isPriorSession(sessionDate: snap.sessionDate, now: entry.date)
        switch family {
        case .systemSmall:
            HoldingsSmall(snapshot: snap, headline: m)
        case .systemLarge:
            HoldingsLarge(snapshot: snap, headline: m, aged: aged)
        default:
            HoldingsMedium(snapshot: snap, headline: m, aged: aged)
        }
    }
}

/// "GROWTH · 12 HOLDINGS". The name truncates; the count never does — it is the one figure
/// that says how much of the portfolio the tile is (and is not) showing.
private struct HoldingsHeader: View {
    @Environment(\.widgetFamily) private var family
    let snapshot: WidgetMoverSnapshot

    /// "· 12" on Small, "· 12 holdings" elsewhere. Rendered at ~126pt, the full count kept
    /// its fixed width and cut the NAME to "GRO…" — and the name is what the user asked to
    /// see; the bare number still reads as a count beside it.
    private var countText: String? {
        guard let n = snapshot.holdingsCount, n > 0 else { return nil }
        if family == .systemSmall { return " · \(n)" }
        return n == 1 ? " · 1 holding" : " · \(n) holdings"
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            Text(snapshot.holdingsTitle)
                .lineLimit(1)
                .truncationMode(.tail)
            if let countText {
                Text(countText)
                    .lineLimit(1)
                    .fixedSize()
                    .layoutPriority(1)
            }
            Spacer(minLength: 0)
        }
        .font(.system(size: 10, weight: .bold))
        .foregroundStyle(.secondary)
        .textCase(.uppercase)
    }
}

/// Medium: the biggest mover and why on the left (~180pt), the rest of the day on the right.
///
/// Budget ~306×126pt (~289×116 on SE): header ~12, left ~22 + three caption lines ~48,
/// bottom row ~13. The right column drops its RISING / FALLING captions when it is short of
/// height (SE) and disappears at accessibility sizes, where the cause sentence needs the room.
private struct HoldingsMedium: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let snapshot: WidgetMoverSnapshot
    let headline: WidgetMover
    let aged: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HeadlineRow(mover: headline, font: .headline)
                CauseView(cause: headline.cause, lineLimit: 3, aged: aged)
                    .layoutPriority(1)
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)

            if !typeSize.isAccessibilitySize {
                Divider()
                SidePanel(snapshot: snapshot)
                    .frame(width: 104, alignment: .topLeading)
            }
        }
    }
}

/// "▲8 ▼5", then the top riser and the top faller (both excluding the headline).
private struct SidePanel: View {
    let snapshot: WidgetMoverSnapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            CountsLine(snapshot: snapshot)
            ViewThatFits(in: .vertical) {
                labelled
                glyphs
            }
        }
    }

    private var labelled: some View {
        VStack(alignment: .leading, spacing: 1) {
            if let riser = snapshot.risers.first {
                Text("Rising")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
                CompactMoverRow(mover: riser)
                    .tapThrough(riser.deepLink)
            }
            if let faller = snapshot.fallers.first {
                Text("Falling")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
                CompactMoverRow(mover: faller)
                    .tapThrough(faller.deepLink)
            }
        }
    }

    private var glyphs: some View {
        VStack(alignment: .leading, spacing: 1) {
            if let riser = snapshot.risers.first {
                CompactMoverRow(mover: riser, glyph: "▲").tapThrough(riser.deepLink)
            }
            if let faller = snapshot.fallers.first {
                CompactMoverRow(mover: faller, glyph: "▼").tapThrough(faller.deepLink)
            }
        }
    }
}

/// Small: the mover on one row, the counts, then one riser and one faller. No cause — at
/// ~126pt a cause sentence truncates into something that is not an explanation.
private struct HoldingsSmall: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let snapshot: WidgetMoverSnapshot
    let headline: WidgetMover

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            // "24h" is NOT in the headline row here: at ~126pt a title3 "BTCUSD", the ~16pt
            // fixed tag and "+3.42%" need ~139pt even fully scaled, and what gave way was the
            // number ("+3.4…"). It moves to the right of the counts row, under the change.
            HeadlineRow(mover: headline, font: .title3, showsRollingTag: false)
            if headline.isRolling24h {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    CountsLine(snapshot: snapshot)
                    Spacer(minLength: 4)
                    RollingTag()
                }
            } else {
                CountsLine(snapshot: snapshot)
            }
            if !typeSize.isAccessibilitySize {
                if let riser = snapshot.risers.first { CompactMoverRow(mover: riser, glyph: "▲") }
                if let faller = snapshot.fallers.first { CompactMoverRow(mover: faller, glyph: "▼") }
            }
        }
    }
}

/// Large: the mover and why, the counts, then up to five risers and five fallers side by
/// side, and the basket sentence when several moved together.
private struct HoldingsLarge: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let snapshot: WidgetMoverSnapshot
    let headline: WidgetMover
    let aged: Bool

    private var rowLimit: Int { typeSize.isAccessibilitySize ? 2 : 5 }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HeadlineRow(mover: headline, font: .title2)
            // Priority over the lists below: when the tile is tight SwiftUI shrinks whatever
            // it likes, and what it picked was this — leaving tickers with no explanation.
            CauseView(cause: headline.cause, lineLimit: 4, aged: aged)
                .layoutPriority(1)
            CountsLine(snapshot: snapshot, showsDetail: true)
            Divider()
            HStack(alignment: .top, spacing: 12) {
                MoverColumn(title: "Rising", movers: snapshot.risers, limit: rowLimit)
                MoverColumn(title: "Falling", movers: snapshot.fallers, limit: rowLimit)
            }
            if let basket = snapshot.basket {
                Divider()
                Text(basket.text)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
    }
}

private struct MoverColumn: View {
    let title: String
    let movers: [WidgetMover]
    var limit: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
            if movers.isEmpty {
                // Not "None today": under a "Fri close" footer that contradicted it.
                Text("None")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(movers.prefix(limit), id: \.ticker) {
                    CompactMoverRow(mover: $0, font: .caption)
                        .tapThrough($0.deepLink)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }
}

/// How many holdings rose and fell this session — over every priced holding, headline
/// included. Large adds the flat and unpriced remainder, so the counts add up to N.
private struct CountsLine: View {
    @Environment(\.widgetRenderingMode) private var renderingMode
    let snapshot: WidgetMoverSnapshot
    var font: Font = .caption.weight(.semibold)
    var showsDetail: Bool = false

    /// Not named `tint(_:)`: inside a View that would compete with SwiftUI's `.tint(_:)`.
    private func arrowTint(_ color: Color) -> Color {
        renderingMode == .fullColor ? color : .primary
    }

    var body: some View {
        if let up = snapshot.upCount, let down = snapshot.downCount {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(verbatim: "▲\(up)").foregroundStyle(arrowTint(.green))
                Text(verbatim: "▼\(down)").foregroundStyle(arrowTint(.red))
                if showsDetail {
                    if let flat = snapshot.flatCount, flat > 0 {
                        Text(verbatim: "\(flat) flat").foregroundStyle(.secondary)
                    }
                    if let unpriced = snapshot.unpricedCount, unpriced > 0 {
                        Text(verbatim: "\(unpriced) no price").foregroundStyle(.secondary)
                    }
                }
            }
            .font(font)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(up) up, \(down) down")
        }
    }
}

/// Ticker and change on ONE row — stacked, they spent two of a small tile's few lines on a
/// single fact.
private struct HeadlineRow: View {
    let mover: WidgetMover
    let font: Font
    /// Off on Small, which shows the "24h" tag on its counts row instead (see `HoldingsSmall`).
    var showsRollingTag: Bool = true

    var body: some View {
        // A step down in size before anything truncates: rendered on a Small SE, even the
        // base symbol beside "+12.34%" at title3 cut the TICKER to "…". The smaller row keeps
        // both whole; the priority below still decides who gives way if neither fits.
        ViewThatFits(in: .horizontal) {
            row(font)
            row(.headline)
        }
    }

    private func row(_ f: Font) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(mover.displayTicker)
                .font(f.weight(.bold))
                // The tile is a FIXED size with no scrolling, so anything without a limit
                // wraps at accessibility sizes and pushes everything below it off the bottom.
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if showsRollingTag, mover.isRolling24h { RollingTag() }
            Spacer(minLength: 4)
            // ⚠️ THE NUMBER WINS THE ROW. Without a priority the stack split a short row
            // evenly and cut the badge ("+3.4…" — which could be +3.4% or +3.47%); with it the
            // TICKER absorbs the cut, and the badge keeps its own 0.7 scale floor.
            // Priority, NOT `.fixedSize()`: a badge that cannot shrink overflows the tile at
            // accessibility sizes, where the root's `.clipped()` would cut it anyway.
            ChangeBadge(mover: mover, font: f.weight(.semibold))
                .layoutPriority(1)
        }
    }
}

/// A mover in a NARROW column — ticker and change only. A truncated cause tag ("Analyst
/// Downg…") is worse than none; these rows answer "what else moved", not "why".
private struct CompactMoverRow: View {
    let mover: WidgetMover
    /// "▲" / "▼" when there is no caption above the row to say which list it is.
    var glyph: String? = nil
    var font: Font = .caption2

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            if let glyph {
                Text(verbatim: glyph)
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(.secondary)
            }
            Text(mover.displayTicker)
                .font(font.weight(.semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            if mover.isRolling24h { RollingTag() }
            Spacer(minLength: 2)
            ChangeBadge(mover: mover, font: font)
        }
    }
}

// MARK: - Shared pieces

/// The change badge. Renders NOTHING when the percentage is unknown — a fabricated
/// "0.00%" on a stock whose quote we could not read is worse than an absent number.
private struct ChangeBadge: View {
    @Environment(\.widgetRenderingMode) private var renderingMode
    let mover: WidgetMover
    var font: Font = .caption.weight(.semibold)

    /// Colour is a REINFORCEMENT of the sign, never the only carrier of direction.
    ///
    /// Accessory families render monochrome, and on a tinted Home Screen (iOS 18) or in
    /// StandBy every colour is flattened into the accent tint — so green and red become
    /// the same pixel. `formattedChange` always carries an explicit `+`/`-`, which is
    /// what actually survives.
    private var tint: Color {
        guard renderingMode == .fullColor else { return .primary }
        if mover.isFlat { return .secondary }
        return mover.isPositive ? .green : .red
    }

    var body: some View {
        if let text = mover.formattedChange {
            Text(text)
                .font(font)
                .foregroundStyle(tint)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
    }
}

/// "24h" beside a round-the-clock asset: its change is a rolling 24 hours, not the session
/// the footer names.
private struct RollingTag: View {
    var body: some View {
        Text(verbatim: "24h")
            .font(.system(size: 8, weight: .semibold))
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .fixedSize()
            .accessibilityLabel("rolling 24 hours")
    }
}

/// The cause line. The tag badge appears ONLY for an established cause — a `none`
/// result gets the sentence with no badge, so nothing implies a known reason.
private struct CauseView: View {
    let cause: WidgetCause
    var lineLimit: Int
    /// The snapshot is from a previous session: prefer the wording that does not say "today".
    var aged: Bool = false
    var showTag: Bool = true

    private var detail: String {
        aged ? (cause.detailAged ?? cause.detail) : cause.detail
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if showTag, cause.kind.isEstablished, let tag = cause.tag, !tag.isEmpty {
                Text(tag)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
            }
            Text(detail)
                .font(.caption)
                .foregroundStyle(cause.kind.isEstablished ? .primary : .secondary)
                .lineLimit(lineLimit)
                .minimumScaleFactor(0.8)
        }
    }
}

/// Says when the data is from, so "−4.8%" is never mistaken for live.
///
/// Derived at RENDER time from `session_date` (see `WidgetSessionLabel`) rather than read
/// from the frozen `market_session` string. Holdings cannot fetch for itself, so a stored
/// wording decays with nothing to update it: this footer used to render nothing during
/// regular hours and "After hours" all weekend.
private struct SessionFooter: View {
    let snapshot: WidgetMoverSnapshot
    /// Supplied by the timeline entry, so each entry re-derives against ITS date.
    let now: Date

    private var label: String? {
        // AGED only: a previous session, or an intraday reading that has gone stale. During
        // the session the numbers are what the reader already assumes.
        WidgetSessionLabel.agedLabel(
            asOf: snapshot.asOf,
            sessionDate: snapshot.sessionDate,
            marketSession: snapshot.marketSession,
            sessionLabel: snapshot.sessionLabel,
            now: now
        )
    }

    var body: some View {
        if let label, !label.isEmpty {
            Text(label)
                .font(.caption2.weight(.semibold))
                // `.secondary`, not `.tertiary` (~1.7:1 in light mode): this label IS the
                // honesty mechanism, so it must be readable.
                .foregroundStyle(.secondary)
                .lineLimit(1)
                // Scale rather than truncate — a clipped "Fri clo…" beside a stale number is
                // the one failure this footer exists to prevent — but not below legibility.
                .minimumScaleFactor(0.85)
                .layoutPriority(1)
        }
    }
}

// MARK: - Lock Screen

private struct RectangularView: View {
    let entry: MoversEntry

    var body: some View {
        if entry.isSignedOut {
            VStack(alignment: .leading, spacing: 1) {
                Text("Caydex").font(.caption.weight(.bold))
                Text("Sign in to see the market and your holdings")
                    .font(.caption2)
                    .lineLimit(2)
            }
        } else if entry.mode == .market {
            RectangularMarket(snapshot: entry.snapshot, now: entry.date)
        } else {
            RectangularHoldings(snapshot: entry.snapshot, now: entry.date)
        }
    }
}

/// Three assets, the footer on the first row. No colour on the Lock Screen — accessory
/// widgets render monochrome, so the sign is the only cue.
private struct RectangularMarket: View {
    let snapshot: WidgetMoverSnapshot?
    let now: Date

    var body: some View {
        if let snap = snapshot, let first = snap.marketRows.first {
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 4) {
                    AssetLine(asset: first)
                    Spacer(minLength: 2)
                    SessionFooter(snapshot: snap, now: now)
                }
                ForEach(snap.marketRows.dropFirst().prefix(2), id: \.symbol) {
                    AssetLine(asset: $0)
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 1) {
                Text("Market").font(.caption.weight(.bold))
                Text("Open Caydex to load the market")
                    .font(.caption2)
                    .lineLimit(2)
            }
        }
    }
}

/// One Lock Screen asset line: short label and change, no price.
private struct AssetLine: View {
    let asset: WidgetIndex

    var body: some View {
        HStack(spacing: 3) {
            Text(asset.shortLabel ?? asset.label)
                .font(.caption2)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
            if asset.isRolling24h { RollingTag() }
            Text(asset.formattedChange ?? "—")
                .font(.caption2.weight(.semibold))
                .lineLimit(1)
        }
    }
}

/// The portfolio's name, its biggest mover, and the counts.
private struct RectangularHoldings: View {
    let snapshot: WidgetMoverSnapshot?
    let now: Date

    var body: some View {
        if let snap = snapshot, snap.mode == "portfolio" {
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 4) {
                    Text(snap.holdingsTitle)
                        .font(.caption.weight(.bold))
                        .lineLimit(1)
                    Spacer(minLength: 2)
                    SessionFooter(snapshot: snap, now: now)
                }
                if let m = snap.headlineMover {
                    HStack(spacing: 4) {
                        Text(m.displayTicker).font(.caption.weight(.semibold)).lineLimit(1)
                        if let c = m.formattedChange { Text(c).font(.caption) }
                    }
                    CountsLine(snapshot: snap, font: .caption2)
                } else {
                    Text(HoldingsView.emptyMessage(for: snap))
                        .font(.caption2)
                        .lineLimit(2)
                }
            }
            .privacySensitive()
        } else {
            VStack(alignment: .leading, spacing: 1) {
                Text("My Holdings").font(.caption.weight(.bold))
                Text("Open Caydex to load your holdings")
                    .font(.caption2)
                    .lineLimit(2)
            }
        }
    }
}

private struct InlineView: View {
    let entry: MoversEntry

    var body: some View {
        // One short line, no wrapping — the system truncates hard here, so each mode offers
        // a few lengths and `ViewThatFits` keeps the longest that fits whole.
        if entry.isSignedOut {
            Text("Sign in to Caydex")
        } else if let snap = entry.snapshot {
            if entry.mode == .market {
                InlineMarket(snapshot: snap, now: entry.date)
            } else {
                InlineHoldings(snapshot: snap, now: entry.date)
            }
        } else {
            Text("Caydex")
        }
    }
}

/// One Lock Screen line from several candidates, longest first: `ViewThatFits` keeps the
/// first that fits whole, so the line shortens by dropping a clause rather than truncating
/// mid-number.
private struct InlineCandidates: View {
    let candidates: [String]

    var body: some View {
        ViewThatFits {
            ForEach(Array(candidates.enumerated()), id: \.offset) { item in
                Text(verbatim: item.element)
            }
        }
    }
}

/// "S&P 500 −0.18% · Nasdaq +0.12%", or "S&P 500 −0.18% · Tue close" when aged.
///
/// ⚠️ WHILE AGED, EVERY CANDIDATE CARRIES THE AGE. The fallback used to be the bare number,
/// so whenever "· Tue 10:05 AM ET" did not fit the slot the line showed an old number as
/// today's. Now the number goes before the age does.
private struct InlineMarket: View {
    let snapshot: WidgetMoverSnapshot
    let now: Date

    private var candidates: [String] {
        let parts: [String] = snapshot.marketRows.compactMap { asset in
            asset.formattedChange.map { "\(asset.shortLabel ?? asset.label) \($0)" }
        }
        guard let first = parts.first else { return [] }
        let aged = WidgetSessionLabel.agedLabel(
            asOf: snapshot.asOf, sessionDate: snapshot.sessionDate,
            marketSession: snapshot.marketSession, sessionLabel: snapshot.sessionLabel, now: now
        )
        if let aged {
            let compact: String = WidgetSessionLabel.compactAgedLabel(
                asOf: snapshot.asOf, sessionDate: snapshot.sessionDate,
                marketSession: snapshot.marketSession, now: now
            ) ?? aged
            return ["\(first) · \(aged)", "\(first) · \(compact)", compact]
        }
        if parts.count > 1 { return ["\(first) · \(parts[1])", first] }
        return [first]
    }

    var body: some View {
        let lines = candidates
        if lines.isEmpty {
            Text("Caydex")
        } else {
            InlineCandidates(candidates: lines)
        }
    }
}

/// "AAPL +2.10% · Tue close" — the mover first, its age when it has one. A known ticker is
/// worth showing without its percentage: `change_percent` is legitimately null at times.
///
/// ⚠️ WHILE AGED, EVERY CANDIDATE CARRIES THE AGE: "AAPL +2.10% · Tue 10:05 AM ET", then
/// "… · Tue 10:05", then "AAPL · Tue 10:05", then the age alone. The old fallback was the
/// bare "AAPL +2.10%" — a week-old number shown as today's whenever the full line was too
/// wide, which "· Sep 23 — open Caydex" always is.
private struct InlineHoldings: View {
    let snapshot: WidgetMoverSnapshot
    let now: Date

    private var candidates: [String] {
        guard snapshot.mode == "portfolio" else { return [] }
        guard let m = snapshot.headlineMover else {
            return [HoldingsView.emptyMessage(for: snapshot), snapshot.holdingsTitle]
        }
        let bare: String = [m.displayTicker, m.formattedChange].compactMap { $0 }.joined(separator: " ")
        let aged = WidgetSessionLabel.agedLabel(
            asOf: snapshot.asOf, sessionDate: snapshot.sessionDate,
            marketSession: snapshot.marketSession, sessionLabel: snapshot.sessionLabel, now: now
        )
        if let aged {
            let compact: String = WidgetSessionLabel.compactAgedLabel(
                asOf: snapshot.asOf, sessionDate: snapshot.sessionDate,
                marketSession: snapshot.marketSession, now: now
            ) ?? aged
            var lines: [String] = ["\(bare) · \(aged)", "\(bare) · \(compact)"]
            if bare != m.displayTicker { lines.append("\(m.displayTicker) · \(compact)") }
            lines.append(compact)
            return lines
        }
        return bare == m.displayTicker ? [bare] : [bare, m.displayTicker]
    }

    var body: some View {
        let lines = candidates
        if lines.isEmpty {
            Text("Caydex")
        } else {
            InlineCandidates(candidates: lines)
                .privacySensitive()
        }
    }
}

// MARK: - Tap-through links

/// Every widget tap used to open the app at its root. These send it to the asset instead,
/// through `caydex://ticker/<SYMBOL>` (`CaydexDeepLink`, shared with the app, which parses
/// it, gates it behind sign-in and opens the detail screen).
///
/// TWO MECHANISMS, because WidgetKit gives each family different tap rules:
/// - `.widgetURL` is the tile-wide target, and the ONLY one a Small or Lock Screen tile
///   has. It is set once, on `MoversWidgetView`, to the Holdings headline mover. On
///   Medium/Large it is also the fallback for any tap outside a `Link`.
/// - `Link` per row on Medium/Large (`tapThrough`). It is inert on Small and Lock Screen
///   families, so it is not drawn there at all.
///
/// The URL carries the ticker and its class, nothing else. That keeps it as safe to hold
/// behind `.privacySensitive()` Holdings rows as the ticker printed on them.
private extension WidgetMover {
    /// The class when the quote supplied one. Otherwise the app resolves from the symbol,
    /// which is right for stocks and for crypto pairs.
    var deepLink: URL? {
        CaydexDeepLink.tickerURL(symbol: ticker, assetClass: CaydexDeepLink.AssetClass(wire: assetType))
    }
}

private extension WidgetIndex {
    /// ONLY when the server named the class. The market assets are ETF proxies (SPY, ONEQ,
    /// DIA, …), and an unclassified SPY resolves to the STOCK screen. A tile with no link is
    /// better than a tap that opens the wrong screen.
    var deepLink: URL? {
        guard let assetClass = CaydexDeepLink.AssetClass(wire: assetType) else { return nil }
        return CaydexDeepLink.tickerURL(symbol: symbol, assetClass: assetClass)
    }
}

/// Wraps a row in a `Link` on the families that honour one. Everywhere else, or with no
/// destination, the row renders exactly as it did before.
private struct TapThrough: ViewModifier {
    @Environment(\.widgetFamily) private var family
    let url: URL?

    private var familyHonoursLinks: Bool {
        family == .systemMedium || family == .systemLarge
    }

    func body(content: Content) -> some View {
        if let url, familyHonoursLinks {
            Link(destination: url) {
                content
                    // A concrete colour, not the hierarchical `.primary`, so a Link's own tint
                    // cannot reach an unstyled ticker. Children with their own style keep it.
                    .foregroundStyle(Color.primary)
                    // The whole row is the target, the Spacer included.
                    .contentShape(Rectangle())
            }
        } else {
            content
        }
    }
}

private extension View {
    func tapThrough(_ url: URL?) -> some View {
        modifier(TapThrough(url: url))
    }
}

// MARK: - Snapshot helpers (view-side)

private extension WidgetMoverSnapshot {
    /// The Market tile's rows: the pulse assets, else (an old backend) the index band.
    var marketRows: [WidgetIndex] {
        marketAssets.isEmpty ? (marketContext?.indices ?? []) : marketAssets
    }

    /// The group's own name, or nil when it has none worth printing in a sentence.
    var namedGroup: String? {
        guard let name = groupName?.trimmingCharacters(in: .whitespacesAndNewlines),
              !name.isEmpty else { return nil }
        return name
    }

    /// Risers, headline excluded. An OLD backend sends no `top_gainers` and no counts; its
    /// runners-up are then split by sign, keeping their order.
    var risers: [WidgetMover] {
        if !topGainers.isEmpty || upCount != nil { return topGainers }
        return runnersUp.filter { $0.isPositive && !$0.isFlat }
    }

    var fallers: [WidgetMover] {
        if !topLosers.isEmpty || downCount != nil { return topLosers }
        return runnersUp.filter { ($0.changePercent ?? 0) < 0 && !$0.isFlat }
    }
}

// MARK: - Previews

extension WidgetMoverSnapshot {
    /// Today's ET date, so the samples render as current rather than aged.
    static var previewSessionDate: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "America/New_York")
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date())
    }

    /// The Market sample — also the gallery card and the placeholder, so it is clearly
    /// sample-like rather than a flattering best case. Labels match the backend's pulse.
    static var preview: WidgetMoverSnapshot {
        var assets: [WidgetIndex] = []
        assets.append(WidgetIndex(symbol: "SPY", label: "S&P 500 ETF", changePercent: -0.18, price: 642.10, shortLabel: "S&P 500"))
        assets.append(WidgetIndex(symbol: "ONEQ", label: "Nasdaq ETF", changePercent: 0.12, price: 84.30, shortLabel: "Nasdaq"))
        assets.append(WidgetIndex(symbol: "DIA", label: "Dow Jones ETF", changePercent: -0.31, price: 451.20, shortLabel: "Dow"))
        assets.append(WidgetIndex(symbol: "IWM", label: "Russell 2000 ETF", changePercent: 0.45, price: 231.70, shortLabel: "Russell 2000"))
        assets.append(WidgetIndex(symbol: "GLD", label: "Gold ETF", changePercent: 0.62, price: 301.40, shortLabel: "Gold"))
        assets.append(WidgetIndex(symbol: "BTCUSD", label: "Bitcoin", changePercent: 1.84, price: 112_340.00, shortLabel: "Bitcoin", rolling24h: true))

        let context = WidgetMarketContext(
            breadthUp: 3, breadthTotal: 11,
            leadingSector: "Energy", leadingSectorChangePercent: 0.81,
            laggingSector: "Technology", laggingSectorChangePercent: -1.44
        )
        let brief = WidgetMarketBrief(
            headline: "Stocks drift lower as tech lags while energy and gold catch a bid.",
            sentiment: "Neutral"
        )
        return WidgetMoverSnapshot(
            mode: "market", asOf: Date(), marketSession: "regular",
            sessionDate: previewSessionDate, sessionLabel: "Live 2:14 PM ET",
            scopeLabel: "The stocks Caydex tracks",
            marketBrief: brief, marketContext: context,
            headlineMover: nil, basket: nil,
            marketAssets: assets
        )
    }

    /// The Holdings sample: a named group, a `.none` cause with a real industry comparison
    /// (the common case against the live backend), and both sides of the day.
    static var previewHoldings: WidgetMoverSnapshot {
        let ctx = WidgetMoveContext(
            changePercent: -5.02, z: 1.1, gapPercent: nil, intradayPercent: nil,
            gapDominant: false, industryName: "Aerospace & Defense",
            industryChangePercent: -1.23, marketChangePercent: -0.15
        )
        let head = WidgetMover(
            ticker: "ACHR", companyName: "Archer Aviation Inc.", changePercent: -5.02,
            cause: WidgetCause(
                kind: .none,
                detail: "Aerospace & Defense fell 1.2%; ACHR moved far more. No clear catalyst in today's news.",
                detailAged: "Aerospace & Defense fell 1.2%; ACHR moved far more. No clear catalyst in that day's news."
            ),
            context: ctx
        )
        var gainers: [WidgetMover] = []
        gainers.append(previewMover("SOFI", 3.42, .earnings, "Earnings Beat", "SOFI beat EPS estimates by 12.0%.", ctx))
        gainers.append(previewMover("PLTR", 3.11, .none, nil, "No clear catalyst in today's news.", ctx))
        gainers.append(previewMover("NVDA", 2.80, .analyst, "Analyst Upgrade", "Bernstein upgraded NVDA to Outperform.", ctx))
        var losers: [WidgetMover] = []
        losers.append(previewMover("JOBY", -4.06, .sector, "Sector Move", "Aerospace & Defense fell 1.2% today.", ctx))
        losers.append(previewMover("RKLB", -3.91, .analyst, "Analyst Downgrade", "Morgan Stanley downgraded RKLB.", ctx))

        return WidgetMoverSnapshot(
            mode: "portfolio", asOf: Date(), marketSession: "regular",
            sessionDate: previewSessionDate, sessionLabel: "Live 2:14 PM ET",
            scopeLabel: "Your holdings",
            headlineMover: head, basket: nil,
            groupName: "Growth", holdingsCount: 12,
            upCount: 5, downCount: 6, flatCount: 1,
            topGainers: gainers, topLosers: losers
        )
    }

    /// A crypto pair leading Holdings — the widest headline a Small tile gets ("BTCUSD",
    /// "+12.34%" and the "24h" tag), now common under the |%| ranking. The Small layout must
    /// keep the whole number; the ticker is what may shorten.
    static var previewHoldingsCrypto: WidgetMoverSnapshot {
        let ctx = WidgetMoveContext(
            changePercent: 12.34, z: nil, gapPercent: nil, intradayPercent: nil,
            gapDominant: false, industryName: nil,
            industryChangePercent: nil, marketChangePercent: nil
        )
        let head = WidgetMover(
            ticker: "BTCUSD", companyName: "Bitcoin", changePercent: 12.34,
            cause: WidgetCause(kind: .none, detail: "No clear catalyst in the news."),
            context: ctx, rolling24h: true, assetType: "crypto"
        )
        var gainers: [WidgetMover] = []
        gainers.append(previewMover("SOFI", 3.42, .earnings, "Earnings Beat", "SOFI beat EPS estimates by 12.0%.", ctx))
        var losers: [WidgetMover] = []
        losers.append(previewMover("RKLB", -3.91, .analyst, "Analyst Downgrade", "Morgan Stanley downgraded RKLB.", ctx))
        return WidgetMoverSnapshot(
            mode: "portfolio", asOf: Date(), marketSession: "regular",
            sessionDate: previewSessionDate, sessionLabel: "Live 2:14 PM ET",
            scopeLabel: "Your holdings",
            headlineMover: head, basket: nil,
            groupName: "Growth", holdingsCount: 8,
            upCount: 5, downCount: 3, flatCount: 0,
            topGainers: gainers, topLosers: losers
        )
    }

    /// An authoritative empty group: "No holdings in Retirement yet".
    static var previewEmptyGroup: WidgetMoverSnapshot {
        WidgetMoverSnapshot(
            mode: "portfolio", asOf: Date(), marketSession: "regular",
            sessionDate: previewSessionDate, scopeLabel: "Your holdings",
            headlineMover: nil, basket: nil,
            groupName: "Retirement", holdingsCount: 0
        )
    }

    private static func previewMover(
        _ ticker: String, _ change: Double, _ kind: WidgetCauseKind, _ tag: String?,
        _ detail: String, _ ctx: WidgetMoveContext
    ) -> WidgetMover {
        WidgetMover(
            ticker: ticker, changePercent: change,
            cause: WidgetCause(kind: kind, tag: tag, detail: detail),
            context: ctx
        )
    }
}

#Preview("Medium", as: .systemMedium) {
    MoversWidget()
} timeline: {
    MoversEntry(date: .now, snapshot: .previewHoldings, mode: .portfolio, configuredMode: .portfolio)
    MoversEntry(date: .now, snapshot: .preview, mode: .market, configuredMode: .market)
    MoversEntry(date: .now, snapshot: .previewEmptyGroup, mode: .portfolio, configuredMode: .portfolio)
    MoversEntry(date: .now, snapshot: nil, mode: .market, configuredMode: .market, isSignedOut: true)
}

#Preview("Small", as: .systemSmall) {
    MoversWidget()
} timeline: {
    MoversEntry(date: .now, snapshot: .previewHoldings, mode: .portfolio, configuredMode: .portfolio)
    MoversEntry(date: .now, snapshot: .previewHoldingsCrypto, mode: .portfolio, configuredMode: .portfolio)
    MoversEntry(date: .now, snapshot: .preview, mode: .market, configuredMode: .market)
}

#Preview("Large", as: .systemLarge) {
    MoversWidget()
} timeline: {
    MoversEntry(date: .now, snapshot: .previewHoldings, mode: .portfolio, configuredMode: .portfolio)
    MoversEntry(date: .now, snapshot: .preview, mode: .market, configuredMode: .market)
}
