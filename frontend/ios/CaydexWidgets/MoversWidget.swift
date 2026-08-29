//
//  MoversWidget.swift
//  CaydexWidgets
//
//  "Which of my stocks moved, and why" — at a glance, on the Home Screen.
//
//  READS ONLY. This extension makes no network call and no Keychain read. The app
//  fetches (it owns the auth token) and writes a snapshot into the shared App Group;
//  this renders it. See the long note in `WidgetSnapshotStore.swift` for why that split
//  is mandatory rather than merely tidy.
//
//  THE ONE RULE FOR THIS FILE
//  A tag badge appears ONLY when `cause.kind.isEstablished` — i.e. the backend found a
//  dated, structured reason (earnings today, an analyst action today, a classified
//  headline, an industry move). `.none` renders its sentence with no badge, because a
//  badge implies a known cause.
//
//  `.none` is the COMMON case, not an error. For ACHR it reads "Aerospace & Defense
//  fell 1.2%; ACHR moved far more. No clear catalyst in today's news." — which is more
//  useful than any headline available, and true. The earlier build put a generic PR
//  headline there instead, and it read as a cause purely by sitting under the number.
//
//  Everything here is DAILY. There is no path by which a multi-day window can reach
//  this file; see `daily_move_attribution` for why that is structural.
//

import AppIntents
import SwiftUI
import WidgetKit

// MARK: - Timeline

struct MoversEntry: TimelineEntry {
    let date: Date
    let snapshot: WidgetMoverSnapshot?
    /// What the USER configured this instance to show. Kept alongside the snapshot so the
    /// views can tell "showing your holdings" from "showing the market because you have
    /// none" — the payload's own `mode` says what the data IS, this says what was ASKED
    /// for, and only the pair reveals a mismatch.
    var configuredMode: MoversMode = .market
    /// True when no snapshot exists at all — first install, or the app has never run.
    var isPlaceholder: Bool { snapshot == nil }
}

struct MoversProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> MoversEntry {
        MoversEntry(date: Date(), snapshot: .preview)
    }

    func snapshot(for configuration: MoversConfigurationIntent, in context: Context) async -> MoversEntry {
        // The gallery preview must never render an empty card, or the widget looks
        // broken before it has been added even once.
        if context.isPreview {
            return MoversEntry(date: Date(), snapshot: .preview)
        }
        return entry(for: configuration)
    }

    func timeline(for configuration: MoversConfigurationIntent, in context: Context) async -> Timeline<MoversEntry> {
        // THE EXTENSION FETCHES NOW. It did not used to, and that was the whole bug:
        // the tile could only change when the APP was foregrounded, so a user who did
        // not open Caydex for a day saw yesterday's numbers for a day. Reported from
        // TestFlight as "it doesn't automatically update new information", and correct.
        //
        // Market mode only — see `WidgetMarketFetcher`. Holdings needs an identity the
        // extension must never hold, so it still renders what the app last wrote.
        let mode = effectiveMode(for: configuration)
        var snap = snapshot(for: mode)

        if mode == .market, let fresh = await WidgetMarketFetcher.fetchMarket() {
            snap = fresh
            // Store it WITHOUT a reload — see `writeFromExtension`. This is not how the
            // entries below get the data (they already have it); it is so the next
            // FAILED fetch falls back to something current instead of to whenever the
            // app was last opened.
            WidgetSnapshotStore.writeFromExtension(mode: .market, snapshot: fresh)
        }

        // SEVERAL entries over ONE snapshot.
        //
        // Re-rendering buys no new DATA between fetches; what it buys is an honest
        // LABEL. `WidgetSessionLabel` derives the wording at render time, so a tile
        // written at 14:14 says "Live 2:14 PM ET" now and "As of 2:14 PM ET" an hour
        // later with no network at all. Each entry is the same bytes at a later clock,
        // which is exactly what that derivation needs.
        let now = Date()
        let reload = WidgetRefreshSchedule.nextRefresh(after: now)
        let cal = Calendar.current
        var dates: [Date] = [now]
        for minutes in [20, 60, 180] {
            guard let d = cal.date(byAdding: .minute, value: minutes, to: now) else { continue }
            // Never schedule a render past the point we have asked to be reloaded: those
            // entries are redundant when the reload lands, and on a quiet weekend the
            // ones before it are the only thing keeping the label moving.
            if d < reload { dates.append(d) }
        }
        // Cross midnight so "today" becomes "yesterday" even when the reload is far off.
        if let midnight = cal.nextDate(
            after: now, matching: DateComponents(hour: 0, minute: 1),
            matchingPolicy: .nextTime
        ), midnight < reload {
            dates.append(midnight)
        }

        let entries = dates
            .sorted()
            .map { MoversEntry(date: $0, snapshot: snap, configuredMode: mode) }
        // ⚠️ `.after(reload)`, NOT the last entry's date. Those used to be the same
        // thing, because the last entry WAS the next 00:01 — and that equality was the
        // second half of the bug: WidgetKit was told not to ask again until tomorrow, so
        // nothing could wake the extension during the day even in principle.
        return Timeline(entries: entries, policy: .after(reload))
    }

    /// The tile's mode: what the in-tile toggle last set, else what was configured.
    ///
    /// The override is global (WidgetKit gives a provider no per-instance identity), so
    /// an untouched install behaves exactly as before — it stays nil until somebody
    /// taps the button.
    private func effectiveMode(for configuration: MoversConfigurationIntent) -> MoversMode {
        WidgetModeOverride.current() ?? configuration.mode
    }

    private func entry(for configuration: MoversConfigurationIntent) -> MoversEntry {
        let mode = effectiveMode(for: configuration)
        return MoversEntry(date: Date(), snapshot: snapshot(for: mode), configuredMode: mode)
    }

    private func snapshot(for mode: MoversMode) -> WidgetMoverSnapshot? {
        let envelope = WidgetSnapshotStore.read()
        switch mode {
        case .portfolio:
            // Fall back to market data rather than showing nothing — but the tile MUST
            // say so. Two independent fallbacks land here (this one, and the backend
            // serving a market payload on the portfolio route when the caller has no
            // holdings), and `scopeLabel` travels with the payload precisely so a widget
            // the user configured as "My Holdings" cannot silently present a stock they
            // do not own as one of theirs.
            return envelope?.portfolio ?? envelope?.market
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
        .configurationDisplayName("Biggest Mover")
        .description("The stock that moved most today, and why — for the market or your holdings.")
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

    /// The Market brief, when this tile is in Market mode and the backend supplied one.
    ///
    /// Absent is ordinary — the roll-up is session-gated server-side — and the tile then
    /// falls through to the mover layout it has always had. A market tile that renders
    /// nothing because its headline expired would be a worse regression than the staleness
    /// this whole change is fixing.
    private var marketBrief: WidgetMarketBrief? {
        guard entry.configuredMode == .market else { return nil }
        return entry.snapshot?.marketBrief
    }

    var body: some View {
        switch family {
        // Lock Screen families stay exactly as they were: they are one or two lines of
        // glanceable text, they cannot host a Button, and a headline sentence does not
        // fit where a ticker and a percentage barely do.
        case .accessoryInline:      InlineView(entry: entry)
        case .accessoryRectangular: RectangularView(entry: entry)
        case .systemSmall:
            if let snap = entry.snapshot, let brief = marketBrief {
                homeScreen {
                    MarketBriefView(
                        snapshot: snap, brief: brief,
                        indexLimit: 1, headlineLimit: 3, now: entry.date,
                        showBreadth: false
                    )
                }
            } else {
                homeScreen { SmallView(entry: entry, configured: entry.configuredMode) }
            }
        case .systemLarge:
            if let snap = entry.snapshot, let brief = marketBrief {
                homeScreen {
                    MarketBriefView(
                        snapshot: snap, brief: brief,
                        indexLimit: 3, headlineLimit: 4, now: entry.date,
                        showSectorDetail: true
                    )
                }
            } else {
                homeScreen { LargeView(entry: entry, configured: entry.configuredMode) }
            }
        default:
            if let snap = entry.snapshot, let brief = marketBrief {
                homeScreen {
                    MarketBriefView(
                        snapshot: snap, brief: brief,
                        indexLimit: 3, headlineLimit: 3, now: entry.date
                    )
                }
            } else {
                homeScreen { MediumView(entry: entry, configured: entry.configuredMode) }
            }
        }
    }

    /// Home Screen content with the mode toggle on its own row underneath.
    ///
    /// ⚠️ A ROW, NOT AN OVERLAY. The first version pinned it to `.bottomTrailing`, which
    /// on the Small family drew it straight through the session footer — the rendered
    /// tile read "As of 2:14 PM E⇆ Holdings". That footer is the widget's honesty
    /// mechanism (it is the only thing saying whether a number is from today), so
    /// anything that can cover it is disqualified no matter how little space it saves.
    /// A row costs ~11pt and cannot overlap by construction.
    @ViewBuilder
    private func homeScreen<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            content()
            HStack(spacing: 0) {
                Spacer(minLength: 0)
                ModeToggle(current: entry.configuredMode)
            }
        }
    }
}

// MARK: - Market mode

/// The Market tile: the day's one-line read, then the numbers behind it.
///
/// ⚠️ MARKET MODE IS NOT A MOVER TILE. It used to be — the same biggest-mover layout as
/// Holdings, differing only in which universe it ranked — and that answered the wrong
/// question. Someone glancing at a Market tile wants to know what the tape is doing, not
/// to pick a name out of it. Holdings keeps the mover layout, because there the
/// individual name IS the point.
///
/// The headline is the `__MARKET__` roll-up the Updates screen already shows, and the
/// backend drops it unless it was generated in the session the rest of the payload
/// describes — so this view never has to caveat it.
private struct MarketBriefView: View {
    let snapshot: WidgetMoverSnapshot
    let brief: WidgetMarketBrief
    var indexLimit: Int
    var headlineLimit: Int
    var now: Date = Date()
    var showBreadth: Bool = true
    /// Large only. Without it that family rendered four lines in a 354pt tile and left
    /// the bottom two-thirds empty — which reads as a broken widget, not a calm one.
    var showSectorDetail: Bool = false

    private var breadth: String? {
        guard showBreadth, let mc = snapshot.marketContext,
              let up = mc.breadthUp, let total = mc.breadthTotal, total > 0
        else { return nil }
        return "\(up) of \(total) sectors up"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("Market")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.tertiary)
                    .textCase(.uppercase)
                    .lineLimit(1)
                if let s = brief.sentiment, !s.isEmpty {
                    Text(s)
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
                Spacer(minLength: 2)
            }

            // The sentence is why this tile exists, so it outranks everything below it
            // for space. Without the priority SwiftUI recovers a cramped tile from the
            // tallest flexible element, which is exactly this one.
            Text(brief.headline)
                .font(.caption.weight(.semibold))
                .lineLimit(headlineLimit)
                .minimumScaleFactor(0.85)
                .fixedSize(horizontal: false, vertical: true)
                .layoutPriority(1)

            if let mc = snapshot.marketContext, !mc.isEmpty {
                IndexStrip(indices: mc.indices, limit: indexLimit)
            }
            if let breadth {
                Text(breadth)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }

            if showSectorDetail, let mc = snapshot.marketContext {
                SectorLeaders(context: mc)
            }

            Spacer(minLength: 0)
            SessionFooter(snapshot: snapshot, now: now)
        }
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
                    .foregroundStyle(.tertiary)
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

/// The in-tile Market ⇄ Holdings switch.
///
/// Home Screen families only — Apple does not allow buttons on Lock Screen widgets, so
/// `.accessoryInline` / `.accessoryRectangular` keep the long-press configuration.
///
/// ⚠️ Flips EVERY Caydex tile, not just this one: WidgetKit gives a provider no
/// per-instance identity, so the choice has to live in the App Group. See
/// `ToggleMoversModeIntent`.
private struct ModeToggle: View {
    let current: MoversMode

    private var other: MoversMode { current == .market ? .portfolio : .market }
    private var title: String { other == .market ? "Market" : "Holdings" }

    var body: some View {
        Button(intent: ToggleMoversModeIntent(mode: other)) {
            HStack(spacing: 3) {
                Image(systemName: "arrow.left.arrow.right")
                    .font(.system(size: 8, weight: .bold))
                Text(title)
                    .font(.system(size: 9, weight: .semibold))
                    .lineLimit(1)
            }
            .foregroundStyle(.secondary)
        }
        .buttonStyle(.plain)
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
    /// what actually survives; asking for a colour that will be discarded just produces
    /// a worse contrast ratio in the modes where it is honoured.
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

/// The cause line. The tag badge appears ONLY for an established cause — a `none`
/// result gets the sentence with no badge, so nothing implies a known reason.
private struct CauseView: View {
    let cause: WidgetCause
    var lineLimit: Int
    var showTag: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if showTag, cause.kind.isEstablished, let tag = cause.tag, !tag.isEmpty {
                Text(tag)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
            }
            Text(cause.detail)
                .font(.caption)
                .foregroundStyle(cause.kind.isEstablished ? .primary : .secondary)
                .lineLimit(lineLimit)
                .minimumScaleFactor(0.85)
        }
    }
}

/// Says when the data is from, so "−4.8%" is never mistaken for live.
///
/// Derived at RENDER time from `session_date` (see `WidgetSessionLabel`) rather than read
/// from the frozen `market_session` string. The extension cannot fetch, so a stored
/// wording decays with nothing to update it: this footer used to render an EMPTY string
/// during regular hours and "After hours" all weekend, which is how a Friday −5% could
/// sit on a Monday Home Screen with no time cue at all.
private struct SessionFooter: View {
    let snapshot: WidgetMoverSnapshot
    /// Supplied by the timeline entry, so each entry re-derives against ITS date.
    let now: Date

    private var label: String? {
        // AGED only. During the session the numbers are what the reader already assumes,
        // and a "Live 2:14 PM ET" line spends a row of a small tile saying so. It appears
        // when the data is from a PREVIOUS session — the case where staying silent would
        // present Friday's move as today's.
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
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                // Scale rather than truncate. This label IS the honesty mechanism — it is
                // the only thing telling the reader whether "−5.02%" is from today — so it
                // is the last element on the tile that should be cut. "Live 2:14 P…" is a
                // small ugliness; a clipped "Fri clo…" beside a stale number is not.
                .minimumScaleFactor(0.7)
                .layoutPriority(1)
        }
    }
}

/// Names the universe when it is NOT what the widget was configured to show.
///
/// A "My Holdings" tile can end up rendering market data two different ways — the backend
/// serves a market payload when the caller has no holdings, and the provider falls back to
/// the market slot when no portfolio snapshot exists. Both are reasonable; neither may be
/// silent. Presenting a stock the user does not own where their own holding belongs is a
/// misattribution they have no way to detect.
private struct ScopeBanner: View {
    let snapshot: WidgetMoverSnapshot
    let configured: MoversMode

    private var mismatched: Bool {
        configured == .portfolio && snapshot.mode != "portfolio"
    }

    var body: some View {
        if mismatched {
            Text(snapshot.scopeLabel ?? "The stocks Caydex tracks")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
    }
}

/// One runner-up row. Fills the large family with real content instead of a void.
private struct RunnerRow: View {
    let mover: WidgetMover

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(mover.ticker).font(.caption.weight(.semibold))
            ChangeBadge(mover: mover, font: .caption)
            Spacer(minLength: 4)
            if let tag = mover.cause.tag, mover.cause.kind.isEstablished {
                Text(tag).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            // Nothing when there is no established cause. The σ multiple used to fill this
            // slot, but it sits in the column every other row uses for a REASON — so
            // "1.1× normal" read as one, when it is only a restatement of the percentage
            // already printed to its left. An empty cell says "no known cause", which is
            // both true and what `kind == .none` means.
        }
    }
}

/// The market band: how the tape itself did, above the single name.
///
/// This is what the widget was missing. A reader seeing "ACHR −5.02%" alone has no idea
/// whether the whole market was red — the S&P's move existed in the payload but only
/// buried inside the mover's own `context`, where nothing rendered it.
private struct IndexStrip: View {
    @Environment(\.widgetRenderingMode) private var renderingMode
    let indices: [WidgetIndex]
    /// Small has room for one; medium/large show the row.
    var limit: Int

    private func tint(_ i: WidgetIndex) -> Color {
        guard renderingMode == .fullColor else { return .primary }
        if i.isFlat { return .secondary }
        return i.isPositive ? .green : .red
    }

    var body: some View {
        if !indices.isEmpty {
            HStack(spacing: 10) {
                ForEach(indices.prefix(limit), id: \.symbol) { idx in
                    HStack(spacing: 4) {
                        Text(idx.label)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.75)
                        if let c = idx.formattedChange {
                            Text(c)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(tint(idx))
                                .lineLimit(1)
                                .minimumScaleFactor(0.75)
                        }
                    }
                }
                Spacer(minLength: 0)
            }
        }
    }
}

/// A runner-up in a NARROW column — ticker and change only.
///
/// The medium family's right-hand column is ~120pt. A cause tag does not fit there, and a
/// truncated one ("Analyst Downg…") is worse than none: this column sits where the reader
/// is scanning for names, not explanations. The headline mover keeps the full sentence;
/// these answer "what else moved".
private struct CompactMoverRow: View {
    let mover: WidgetMover

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(mover.ticker)
                .font(.caption2.weight(.semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Spacer(minLength: 2)
            ChangeBadge(mover: mover, font: .caption2)
        }
    }
}

/// The "what else moved" column. Renders nothing when there is nothing to add, so the
/// headline block reflows into the full width rather than sitting beside an empty rail.
private struct RunnerColumn: View {
    let movers: [WidgetMover]
    var limit: Int

    var body: some View {
        if !movers.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Text("Also moving")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.tertiary)
                    .textCase(.uppercase)
                    .lineLimit(1)
                ForEach(movers.prefix(limit), id: \.ticker) { CompactMoverRow(mover: $0) }
                Spacer(minLength: 0)
            }
        }
    }
}

/// Two DIFFERENT empty states, because they call for different actions.
///
/// "No snapshot has ever been written" is fixed by opening the app. "A snapshot exists and
/// its headline_mover is null" is not — the app has already run, and telling the user to
/// open it is a dead end that reads as the widget being broken. Collapsing the two into
/// one message meant the only instruction on the tile was, half the time, useless.
private struct EmptyStateView: View {
    /// nil ⇒ nothing has ever been written.
    let snapshot: WidgetMoverSnapshot?
    var now: Date = Date()

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Caydex").font(.caption.weight(.semibold))
            if let snapshot {
                Text("No unusual moves to report.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                SessionFooter(snapshot: snapshot, now: now)
            } else {
                Text("Open the app to load today's movers.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
    }
}

// MARK: - Families

private struct SmallView: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let entry: MoversEntry
    var configured: MoversMode = .market

    var body: some View {
        // Six cramped lines became four legible ones. The ~155pt tile could not hold a
        // cause sentence at a width that kept it meaningful — "Aerospace & Defense fe…"
        // is not an explanation — so this size answers the two questions it CAN answer
        // completely: how is the market, and what moved most.
        VStack(alignment: .leading, spacing: 5) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                // ONE index at this size. Three rows would cost 3 of ~7 available lines
                // for symbols that are ~90% correlated on a typical session; one answers
                // "is the market up or down today", which is the whole job of this band.
                if let mc = snap.marketContext, !mc.isEmpty, !typeSize.isAccessibilitySize {
                    IndexStrip(indices: mc.indices, limit: 1)
                }
                ScopeBanner(snapshot: snap, configured: configured)

                // Ticker and change on ONE row. Stacked, they spent two of the tile's
                // few lines on a single fact.
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(m.ticker)
                        .font(.title3.weight(.bold))
                        // The tile is a FIXED ~155pt with no scrolling, so anything
                        // without a limit here wraps at accessibility sizes and pushes
                        // everything below it straight off the bottom.
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                    Spacer(minLength: 4)
                    ChangeBadge(mover: m, font: .title3.weight(.semibold))
                }

                // The freed rows go to OTHER NAMES, which is the one kind of content that
                // stays complete at this width — a ticker and a percentage never truncate
                // into something misleading, and a truncated sentence does.
                if !snap.runnersUp.isEmpty, !typeSize.isAccessibilitySize {
                    Divider()
                    ForEach(snap.runnersUp.prefix(3), id: \.ticker) {
                        CompactMoverRow(mover: $0)
                    }
                }
                Spacer(minLength: 0)
                SessionFooter(snapshot: snap, now: entry.date)
            } else {
                EmptyStateView(snapshot: entry.snapshot, now: entry.date)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct MediumView: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let entry: MoversEntry
    var configured: MoversMode = .market

    /// The ranked column is the FIRST thing to go at accessibility sizes. It is the least
    /// information per pixel on the tile — names and numbers with no explanation — and
    /// keeping it would squeeze the headline's cause sentence, which is the whole point
    /// of the widget.
    private var showsColumn: Bool { !typeSize.isAccessibilitySize }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                VStack(alignment: .leading, spacing: 4) {
                    if let mc = snap.marketContext, !mc.isEmpty {
                        // The strip gets the WHOLE row. Sharing it with anything else
                        // squeezed both to "S&... -0... Na... -0..." — and a truncated
                        // "-0..." could be -0.6% or -0.06%, which is worse than no number.
                        IndexStrip(indices: mc.indices, limit: 2)
                        Divider()
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(m.ticker).font(.title3.weight(.bold)).lineLimit(1)
                        Spacer(minLength: 4)
                        ChangeBadge(mover: m, font: .title3.weight(.semibold))
                    }
                    ScopeBanner(snapshot: snap, configured: configured)
                    // The breadth and volatility lines were two rows of secondary
                    // qualifiers above the one sentence that says WHY. Their space goes
                    // to the sentence, which now renders whole instead of "…catalyst in…".
                    CauseView(cause: m.cause, lineLimit: 4)
                        .layoutPriority(1)
                    SessionFooter(snapshot: snap, now: entry.date)
                    if let basket = snap.basket {
                        Text(basket.text)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    Spacer(minLength: 0)
                }
                .frame(maxWidth: .infinity, alignment: .topLeading)

                if showsColumn, !snap.runnersUp.isEmpty {
                    Divider()
                    RunnerColumn(movers: snap.runnersUp, limit: 5)
                        .frame(width: 104, alignment: .topLeading)
                }
            } else {
                EmptyStateView(snapshot: entry.snapshot, now: entry.date)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct LargeView: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let entry: MoversEntry
    var configured: MoversMode = .market

    var body: some View {
        // spacing 5, not 8. Large now stacks ~11 elements (market band, headline block,
        // five ranked rows), and at 8 the SPACING alone consumed ~80pt of a ~322pt tile —
        // which SwiftUI recovered by collapsing the cause sentence to a single truncated
        // line. "Aerospace & Defense fell 1.2%; ACHR moved far more. No cle…" is the one
        // thing on this tile the user cannot get from the number beside it.
        VStack(alignment: .leading, spacing: 5) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                // BAND 1 — the tape. Large has the room for all three indices, and this
                // is the size a reader places when they want the whole picture.
                if let mc = snap.marketContext, !mc.isEmpty {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Market")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(.secondary)
                            .textCase(.uppercase)
                        Spacer(minLength: 4)
                        SessionFooter(snapshot: snap, now: entry.date)
                    }
                    IndexStrip(indices: mc.indices, limit: typeSize.isAccessibilitySize ? 1 : 3)
                    Divider()
                }

                // BAND 2 — the one thing.
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(m.ticker).font(.title2.weight(.bold)).lineLimit(1)
                    Spacer(minLength: 4)
                    ChangeBadge(mover: m, font: .title2.weight(.semibold))
                }
                ScopeBanner(snapshot: snap, configured: configured)
                // The company name, the σ multiple and the breadth line were three rows
                // of qualifiers around the two things a reader came for: what moved, and
                // why. The cause sentence and the ranked list get their space.
                // Priority over the ranked list below. When the tile is tight SwiftUI
                // shrinks whatever it likes, and what it picked was this — leaving five
                // tickers with percentages and no explanation, which is the layout the
                // whole feature exists to beat.
                CauseView(cause: m.cause, lineLimit: 4)
                    .layoutPriority(1)

                if let basket = snap.basket {
                    Divider()
                    Text(basket.text).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                }

                // The runners-up are what stop this size rendering a void. Without
                // them a 4x4 tile held one ticker and a lot of empty space.
                if !snap.runnersUp.isEmpty {
                    Divider()
                    Text("Also moving")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.secondary)
                        .textCase(.uppercase)
                    ForEach(snap.runnersUp.prefix(typeSize.isAccessibilitySize ? 2 : 5),
                            id: \.ticker) { RunnerRow(mover: $0) }
                }
                Spacer(minLength: 0)
            } else {
                EmptyStateView(snapshot: entry.snapshot, now: entry.date)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct RectangularView: View {
    let entry: MoversEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                // Three lines total. The index line earns the first only when there is a
                // number to show; the mover is what this family exists for.
                if let idx = snap.marketContext?.indices.first,
                   let c = idx.formattedChange {
                    HStack(spacing: 4) {
                        Text("\(idx.label) \(c)").font(.caption2)
                        Spacer(minLength: 2)
                        SessionFooter(snapshot: snap, now: entry.date)
                    }
                }
                HStack(spacing: 4) {
                    Text(m.ticker).font(.caption.weight(.bold))
                    if let c = m.formattedChange { Text(c).font(.caption) }
                    Spacer(minLength: 2)
                    if snap.marketContext?.indices.first?.formattedChange == nil {
                        SessionFooter(snapshot: snap, now: entry.date)
                    }
                }
                // No colour on the Lock Screen — accessory widgets render monochrome,
                // so red/green would vanish and the sign is the only cue left.
                Text(m.cause.detail).font(.caption2).lineLimit(2)
            } else {
                Text("Caydex").font(.caption.weight(.bold))
                Text(entry.snapshot == nil ? "Open to load movers" : "No unusual moves")
                    .font(.caption2)
            }
        }
    }
}

private struct InlineView: View {
    let entry: MoversEntry

    var body: some View {
        // One short line, no wrapping — the system truncates hard here.
        //
        // A KNOWN TICKER IS WORTH SHOWING WITHOUT ITS PERCENTAGE. `change_percent` is
        // legitimately null while the ticker and cause are valid, and requiring both
        // collapsed the whole Lock Screen line to the bare word "Caydex" — throwing away
        // the one fact it had.
        if let m = entry.snapshot?.headlineMover {
            // "S&P −0.62% · ACHR −5.0%" when both fit — the tape then the name, which is
            // the same reading order as every other family.
            let idx = entry.snapshot?.marketContext?.indices.first
            switch (idx?.formattedChange, m.formattedChange) {
            case let (.some(ic), .some(mc)):
                Text("\(idx!.label) \(ic) · \(m.ticker) \(mc)")
            case let (.none, .some(mc)):
                Text("\(m.ticker) \(mc)")
            default:
                Text(m.ticker)
            }
        } else {
            Text("Caydex")
        }
    }
}

// MARK: - Previews

extension WidgetMoverSnapshot {
    /// Deliberately a `.none` cause with a real industry comparison — that is the
    /// common case against the live backend, so the gallery preview shows what users
    /// will actually get rather than a flattering best case.
    static var preview: WidgetMoverSnapshot {
        let ctx = WidgetMoveContext(
            changePercent: -5.02, z: 1.1, gapPercent: -1.0, intradayPercent: -4.02,
            gapDominant: false, industryName: "Aerospace & Defense",
            industryChangePercent: -1.23, marketChangePercent: -0.15
        )
        return WidgetMoverSnapshot(
            mode: "market",
            asOf: Date(),
            marketSession: "regular",
            sessionDate: {
                let f = DateFormatter()
                f.locale = Locale(identifier: "en_US_POSIX")
                f.timeZone = TimeZone(identifier: "America/New_York")
                f.dateFormat = "yyyy-MM-dd"
                return f.string(from: Date())
            }(),
            sessionLabel: "Live 2:14 PM ET",
            scopeLabel: "The stocks Caydex tracks",
            marketContext: WidgetMarketContext(
                indices: [
                    WidgetIndex(symbol: "^GSPC", label: "S&P 500", changePercent: -0.62, price: 6412.1),
                    WidgetIndex(symbol: "^IXIC", label: "Nasdaq", changePercent: -0.91, price: 21_340.5),
                    WidgetIndex(symbol: "^DJI", label: "Dow", changePercent: -0.30, price: 44_812.0),
                ],
                breadthUp: 3, breadthTotal: 11,
                leadingSector: "Energy", leadingSectorChangePercent: 0.81,
                laggingSector: "Technology", laggingSectorChangePercent: -1.44,
                text: "S&P 500 fell 0.6% · 3 of 11 sectors up · Energy leads 0.8%"
            ),
            headlineMover: WidgetMover(
                ticker: "ACHR", companyName: "Archer Aviation Inc.",
                changePercent: -5.02, price: 6.62, tier: "Notable", z: 1.1,
                cause: WidgetCause(
                    kind: .none, tag: nil,
                    detail: "Aerospace & Defense fell 1.2%; ACHR moved far more. No clear catalyst in today's news."
                ),
                context: ctx
            ),
            basket: nil,
            runnersUp: [
                WidgetMover(
                    ticker: "JOBY", companyName: "Joby Aviation", changePercent: -4.06,
                    price: 9.1, tier: "Typical", z: 0.9,
                    cause: WidgetCause(kind: .sector, tag: "Sector Move",
                                       detail: "Aerospace & Defense fell 1.2% today."),
                    context: ctx
                ),
                WidgetMover(
                    ticker: "RKLB", companyName: "Rocket Lab", changePercent: -3.91,
                    price: 22.4, tier: "Unusual", z: 2.2,
                    cause: WidgetCause(kind: .analyst, tag: "Analyst Downgrade",
                                       detail: "Morgan Stanley downgraded RKLB to Equal Weight."),
                    context: ctx
                ),
                WidgetMover(
                    ticker: "SOFI", companyName: "SoFi Technologies", changePercent: 3.42,
                    price: 14.8, tier: "Notable", z: 1.4,
                    cause: WidgetCause(kind: .earnings, tag: "Earnings Beat",
                                       detail: "SOFI beat EPS estimates by 12.0%, reported this morning."),
                    context: ctx
                ),
                WidgetMover(
                    ticker: "PLTR", companyName: "Palantir", changePercent: 3.11,
                    price: 61.2, tier: "Notable", z: 1.1,
                    cause: WidgetCause(kind: .none, tag: nil,
                                       detail: "No clear catalyst in today's news."),
                    context: ctx
                ),
                WidgetMover(
                    ticker: "NVDA", companyName: "NVIDIA", changePercent: 2.80,
                    price: 184.5, tier: "Typical", z: 0.9,
                    cause: WidgetCause(kind: .analyst, tag: "Analyst Upgrade",
                                       detail: "Bernstein upgraded NVDA to Outperform."),
                    context: ctx
                ),
            ]
        )
    }
}

#Preview(as: .systemMedium) {
    MoversWidget()
} timeline: {
    MoversEntry(date: .now, snapshot: .preview)
    MoversEntry(date: .now, snapshot: nil)
}
