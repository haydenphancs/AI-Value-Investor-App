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
        // One entry, refreshed on a cadence WidgetKit is free to stretch. Asking for
        // more is pointless: the content only changes when the APP writes a new
        // snapshot, and it calls `reloadTimelines` when it does. This interval is the
        // backstop for "the app has not been opened in a while".
        let next = Calendar.current.date(byAdding: .minute, value: 20, to: Date()) ?? Date()
        return Timeline(entries: [entry(for: configuration)], policy: .after(next))
    }

    private func entry(for configuration: MoversConfigurationIntent) -> MoversEntry {
        let envelope = WidgetSnapshotStore.read()
        let snap: WidgetMoverSnapshot?
        switch configuration.mode {
        case .portfolio: snap = envelope?.portfolio ?? envelope?.market
        case .market:    snap = envelope?.market
        }
        return MoversEntry(date: Date(), snapshot: snap)
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
        case .systemSmall:          SmallView(entry: entry)
        case .systemLarge:          LargeView(entry: entry)
        default:                    MediumView(entry: entry)
        }
    }
}

// MARK: - Shared pieces

/// The change badge. Renders NOTHING when the percentage is unknown — a fabricated
/// "0.00%" on a stock whose quote we could not read is worse than an absent number.
private struct ChangeBadge: View {
    let mover: WidgetMover
    var font: Font = .caption.weight(.semibold)

    var body: some View {
        if let text = mover.formattedChange {
            Text(text)
                .font(font)
                .foregroundStyle(mover.isPositive ? Color.green : Color.red)
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

/// Says when the data is from, so "−4.8%" is never mistaken for live. The sweeper
/// sleeps overnight and all weekend; that is normal, not broken.
private struct SessionFooter: View {
    let snapshot: WidgetMoverSnapshot

    private var label: String {
        switch snapshot.marketSession {
        case "closed":     return "At the close"
        case "premarket":  return "Pre-market"
        case "afterhours": return "After hours"
        default:           return ""
        }
    }

    var body: some View {
        if !label.isEmpty {
            Text(label).font(.caption2).foregroundStyle(.tertiary)
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
                Text(String(format: "%.1f×", z)).font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }
}

private struct EmptyStateView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Caydex").font(.caption.weight(.semibold))
            Text("Open the app to load today's movers.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }
}

// MARK: - Families

private struct SmallView: View {
    let entry: MoversEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                Text(m.ticker).font(.headline.weight(.bold))
                ChangeBadge(mover: m, font: .subheadline.weight(.semibold))
                // No tag at this size: there is not room for both a badge and enough
                // of the sentence for it to mean anything.
                CauseView(cause: m.cause, lineLimit: 3, showTag: false)
                Spacer(minLength: 0)
                ContextLine(context: m.context, showIndustry: false)
                SessionFooter(snapshot: snap)
            } else {
                EmptyStateView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct MediumView: View {
    let entry: MoversEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(m.ticker).font(.headline.weight(.bold))
                    ChangeBadge(mover: m, font: .subheadline.weight(.semibold))
                    Spacer()
                    SessionFooter(snapshot: snap)
                }
                ContextLine(context: m.context)
                CauseView(cause: m.cause, lineLimit: 3)
                if let basket = snap.basket {
                    Divider()
                    Text(basket.text).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer(minLength: 0)
            } else {
                EmptyStateView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct LargeView: View {
    let entry: MoversEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let snap = entry.snapshot, let m = snap.headlineMover {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(m.ticker).font(.title2.weight(.bold))
                    ChangeBadge(mover: m, font: .headline.weight(.semibold))
                    Spacer()
                    SessionFooter(snapshot: snap)
                }
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
                EmptyStateView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct RectangularView: View {
    let entry: MoversEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            if let m = entry.snapshot?.headlineMover {
                HStack(spacing: 4) {
                    Text(m.ticker).font(.caption.weight(.bold))
                    if let c = m.formattedChange { Text(c).font(.caption) }
                }
                // No colour on the Lock Screen — accessory widgets render monochrome,
                // so red/green would vanish and the sign is the only cue left.
                Text(m.cause.detail).font(.caption2).lineLimit(2)
            } else {
                Text("Caydex").font(.caption.weight(.bold))
                Text("Open to load movers").font(.caption2)
            }
        }
    }
}

private struct InlineView: View {
    let entry: MoversEntry

    var body: some View {
        // One short line, no wrapping — the system truncates hard here.
        if let m = entry.snapshot?.headlineMover, let c = m.formattedChange {
            Text("\(m.ticker) \(c)")
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
