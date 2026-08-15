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
        // SEVERAL entries over ONE snapshot, not one.
        //
        // The content only changes when the APP writes a new snapshot, and it calls
        // `reloadTimelines` when it does — so re-rendering buys no new DATA. What it buys
        // is an honest LABEL: `WidgetSessionLabel` derives the time wording at render
        // time, so a tile written at 14:14 says "Live 2:14 PM ET" now, "As of 2:14 PM ET"
        // an hour later, and "Fri close" tomorrow — with no fetch, no background task,
        // and nothing for anyone to keep true. Each entry is the same bytes at a later
        // clock, which is exactly what the derivation needs.
        let now = Date()
        let cal = Calendar.current
        var dates: [Date] = [now]
        for minutes in [20, 60, 180] {
            if let d = cal.date(byAdding: .minute, value: minutes, to: now) { dates.append(d) }
        }
        // Cross midnight so "today" becomes "yesterday" without waiting for a refresh.
        if let midnight = cal.nextDate(
            after: now, matching: DateComponents(hour: 0, minute: 1),
            matchingPolicy: .nextTime
        ) {
            dates.append(midnight)
        }

        let snap = snapshot(for: configuration)
        let entries = dates.map {
            MoversEntry(date: $0, snapshot: snap, configuredMode: configuration.mode)
        }
        let next = dates.last ?? now
        return Timeline(entries: entries, policy: .after(next))
    }

    private func entry(for configuration: MoversConfigurationIntent) -> MoversEntry {
        MoversEntry(
            date: Date(),
            snapshot: snapshot(for: configuration),
            configuredMode: configuration.mode
        )
    }

    private func snapshot(for configuration: MoversConfigurationIntent) -> WidgetMoverSnapshot? {
        let envelope = WidgetSnapshotStore.read()
        switch configuration.mode {
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

    var body: some View {
        switch family {
        case .accessoryInline:      InlineView(entry: entry)
        case .accessoryRectangular: RectangularView(entry: entry)
        case .systemSmall:          SmallView(entry: entry, configured: entry.configuredMode)
        case .systemLarge:          LargeView(entry: entry, configured: entry.configuredMode)
        default:                    MediumView(entry: entry, configured: entry.configuredMode)
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

/// "1.1× normal · Aerospace & Defense −1.2%" — pure arithmetic, always true.
///
/// This is the line that makes a bare percentage mean something: it says whether the
/// move was remarkable *for this stock*, and whether its group went the same way.
private struct ContextLine: View {
    let context: WidgetMoveContext
    var showIndustry: Bool = true

    private var pieces: [String] {
        var out: [String] = []
        if let v = context.volatilityLabelText { out.append(v) }
        if showIndustry, let i = context.industryLabel { out.append(i) }
        return out
    }

    var body: some View {
        if !pieces.isEmpty {
            Text(pieces.joined(separator: " · "))
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
    }
}

private extension WidgetMoveContext {
    var volatilityLabelText: String? {
        guard let z else { return nil }
        return String(format: "%.1f× normal", z)
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

    private var label: String {
        WidgetSessionLabel.displayLabel(
            asOf: snapshot.asOf,
            sessionDate: snapshot.sessionDate,
            marketSession: snapshot.marketSession,
            sessionLabel: snapshot.sessionLabel,
            now: now
        )
    }

    var body: some View {
        if !label.isEmpty {
            Text(label).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
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
            } else if let z = mover.z {
                // "0.9× normal", not a bare "0.9×". This sits in the column other rows
                // use for a cause TAG, so an unlabelled multiplier reads as a reason.
                Text(String(format: "%.1f× normal", z))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
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
        VStack(alignment: .leading, spacing: 3) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                ScopeBanner(snapshot: snap, configured: configured)
                Text(m.ticker)
                    .font(.headline.weight(.bold))
                    // The tile is a FIXED ~155pt with no scrolling, so anything without
                    // a limit here wraps at accessibility sizes and pushes the cause,
                    // the context line and the footer straight off the bottom — the
                    // ticker survives and everything that gives it meaning is clipped.
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                ChangeBadge(mover: m, font: .subheadline.weight(.semibold))
                // No tag at this size: there is not room for both a badge and enough
                // of the sentence for it to mean anything.
                CauseView(cause: m.cause, lineLimit: typeSize.isAccessibilitySize ? 2 : 3, showTag: false)
                Spacer(minLength: 0)
                if !typeSize.isAccessibilitySize {
                    ContextLine(context: m.context, showIndustry: false)
                }
                SessionFooter(snapshot: snap, now: entry.date)
            } else {
                EmptyStateView(snapshot: entry.snapshot, now: entry.date)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct MediumView: View {
    let entry: MoversEntry
    var configured: MoversMode = .market

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(m.ticker).font(.headline.weight(.bold)).lineLimit(1)
                    ChangeBadge(mover: m, font: .subheadline.weight(.semibold))
                    Spacer(minLength: 4)
                    SessionFooter(snapshot: snap, now: entry.date)
                }
                ScopeBanner(snapshot: snap, configured: configured)
                ContextLine(context: m.context)
                CauseView(cause: m.cause, lineLimit: 3)
                if let basket = snap.basket {
                    Divider()
                    Text(basket.text).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer(minLength: 0)
            } else {
                EmptyStateView(snapshot: entry.snapshot, now: entry.date)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct LargeView: View {
    let entry: MoversEntry
    var configured: MoversMode = .market

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(m.ticker).font(.title2.weight(.bold)).lineLimit(1)
                    ChangeBadge(mover: m, font: .headline.weight(.semibold))
                    Spacer(minLength: 4)
                    SessionFooter(snapshot: snap, now: entry.date)
                }
                ScopeBanner(snapshot: snap, configured: configured)
                if let name = m.companyName {
                    Text(name).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                ContextLine(context: m.context)
                CauseView(cause: m.cause, lineLimit: 4)

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
                    ForEach(snap.runnersUp.prefix(3), id: \.ticker) { RunnerRow(mover: $0) }
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
                HStack(spacing: 4) {
                    Text(m.ticker).font(.caption.weight(.bold))
                    if let c = m.formattedChange { Text(c).font(.caption) }
                    Spacer(minLength: 2)
                    SessionFooter(snapshot: snap, now: entry.date)
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
            if let c = m.formattedChange {
                Text("\(m.ticker) \(c)")
            } else {
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
                )
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
