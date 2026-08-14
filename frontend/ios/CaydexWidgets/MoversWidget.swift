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
//  Only a `.catalyst` reason may be framed as *why* the stock moved. A `.context`
//  reason is a news headline — it says what is going on, and establishes no cause.
//  Measured against the live backend, `.context` is the common case and `.catalyst` is
//  currently rare, so the distinction is not an edge case: it is most of what renders.
//  Putting a "Why it moved" label above a news roll-up is the one way this widget can
//  actively mislead someone, in one second, with no way to interrogate it.
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

/// The reason line, with the provenance rule enforced in one place.
private struct ReasonView: View {
    let reason: WidgetReason
    var lineLimit: Int
    var showLabel: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if showLabel, reason.isCausal {
                // ONLY a grounded, cited catalyst earns this label.
                Text(reason.catalystTag ?? "Why it moved")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
            }
            Text(reason.text)
                .font(.caption)
                .foregroundStyle(reason.isCausal ? .primary : .secondary)
                .lineLimit(lineLimit)
                .minimumScaleFactor(0.85)
        }
    }
}

/// Shown when the snapshot predates the current session, so "−4.8%" is never mistaken
/// for live. The sweeper sleeps overnight and all weekend; that is normal, not broken.
private struct StaleFooter: View {
    let snapshot: WidgetMoverSnapshot

    private var label: String {
        switch snapshot.marketSession {
        case "closed":      return "At the close"
        case "premarket":   return "Pre-market"
        case "afterhours":  return "After hours"
        default:            return ""
        }
    }

    var body: some View {
        if !label.isEmpty {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.tertiary)
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

/// A flat day, or an empty portfolio — the market story rather than a blank tile.
private struct MarketStoryView: View {
    let snapshot: WidgetMoverSnapshot
    var lineLimit: Int = 3

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Today's market")
                .font(.caption2.weight(.bold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
            Text(snapshot.marketStory ?? "No standout moves right now.")
                .font(.caption)
                .lineLimit(lineLimit)
        }
    }
}

// MARK: - Families

private struct SmallView: View {
    let entry: MoversEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let snap = entry.snapshot {
                if let m = snap.headlineMover {
                    Text(m.ticker).font(.headline.weight(.bold))
                    ChangeBadge(mover: m, font: .subheadline.weight(.semibold))
                    // One line only at this size, and the label is suppressed: there is
                    // not enough room to show both a tag and enough of the sentence for
                    // it to mean anything.
                    ReasonView(reason: m.reason, lineLimit: 3, showLabel: false)
                    Spacer(minLength: 0)
                    StaleFooter(snapshot: snap)
                } else {
                    MarketStoryView(snapshot: snap, lineLimit: 4)
                }
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
        VStack(alignment: .leading, spacing: 6) {
            if let snap = entry.snapshot {
                if let m = snap.headlineMover {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(m.ticker).font(.headline.weight(.bold))
                        ChangeBadge(mover: m, font: .subheadline.weight(.semibold))
                        Spacer()
                        StaleFooter(snapshot: snap)
                    }
                    if let name = m.companyName {
                        Text(name).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                    ReasonView(reason: m.reason, lineLimit: 3)
                    if let basket = snap.basket {
                        Divider()
                        Text(basket.text).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                    }
                } else {
                    MarketStoryView(snapshot: snap)
                }
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
            if let snap = entry.snapshot {
                if let m = snap.headlineMover {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(m.ticker).font(.title2.weight(.bold))
                        ChangeBadge(mover: m, font: .headline.weight(.semibold))
                        Spacer()
                        StaleFooter(snapshot: snap)
                    }
                    if let name = m.companyName {
                        Text(name).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    ReasonView(reason: m.reason, lineLimit: 5)

                    if let basket = snap.basket {
                        Divider()
                        Text(basket.text).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                    }
                    Spacer(minLength: 0)
                    if let story = snap.marketStory {
                        Divider()
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Today's market")
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(.secondary)
                                .textCase(.uppercase)
                            Text(story).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                } else {
                    MarketStoryView(snapshot: snap, lineLimit: 6)
                }
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
                // No colour on the Lock Screen — accessory widgets render monochrome, so
                // red/green would silently vanish and the sign is the only cue left.
                Text(m.reason.text).font(.caption2).lineLimit(2)
            } else if let story = entry.snapshot?.marketStory {
                Text("Market").font(.caption.weight(.bold))
                Text(story).font(.caption2).lineLimit(2)
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
    /// Deliberately a `.context` reason: that is the common case against the live
    /// backend, so the gallery preview should show what users will actually get.
    static var preview: WidgetMoverSnapshot {
        WidgetMoverSnapshot(
            mode: "market",
            asOf: Date(),
            marketSession: "regular",
            isStale: false,
            headlineMover: WidgetMover(
                ticker: "ACHR",
                companyName: "Archer Aviation Inc.",
                changePercent: -5.02,
                price: 6.62,
                tier: "Notable",
                z: 1.1,
                reason: WidgetReason(
                    kind: .context,
                    text: "Archer Aviation explores new markets and strategic growth",
                    catalystTag: nil
                )
            ),
            basket: nil,
            marketStory: "Mixed Signals Cloud US Market Outlook",
            universeLabel: "Tracked by Caydex"
        )
    }
}

#Preview(as: .systemMedium) {
    MoversWidget()
} timeline: {
    MoversEntry(date: .now, snapshot: .preview)
    MoversEntry(date: .now, snapshot: nil)
}
