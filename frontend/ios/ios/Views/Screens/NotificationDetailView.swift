//
//  NotificationDetailView.swift
//  ios
//
//  Detail screen for a delivered notification — the reason first, then where to go.
//

import SwiftUI

/// The detail behind one Activity notification row.
///
/// WHY THIS EXISTS. A tester: *"It should open a screen to show the reason or detail of information
/// first, then users want to go to that ticker or else."* Until this screen, tapping a notification
/// row went straight to the ticker's detail screen — so the only place the alert's own text existed
/// was the row, where it is clamped to three lines behind a `…`. For a `ticker_move` that clipped
/// text IS the grounded catalyst (`price_move["reason"]`), i.e. the answer to the question the alert
/// raises, and the only way to finish reading it was to leave.
///
/// The digest half of Activity has had `AlertDetailView` all along. This is its counterpart, built
/// to look like it: same 72pt header, same card grammar, same destination rows.
///
/// ⚠️ Takes the whole `CollapsedGroup`, not one row. Adjacent repeats about one ticker are merged
/// into a single row with a `×N` badge, so without this the other N−1 are unreachable — six PLUG
/// "analysis is ready" notifications currently render as one row that hides five of themselves.
struct NotificationDetailView: View {
    let group: NotificationInboxSection.CollapsedGroup

    @Environment(\.dismiss) private var dismiss

    /// The pushed destination. `AlertDestination` is `Hashable` so it can drive
    /// `.navigationDestination(item:)` — the same shape `AlertDetailView` uses for the whale push.
    @State private var pushed: AlertDestination?

    private var item: NotificationEventDTO { group.newest }
    private var destinations: [AlertDestination] { AlertDestination.destinations(for: item) }

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(spacing: AppSpacing.xl) {
                    headerIcon
                        .padding(.top, AppSpacing.xxl)

                    VStack(spacing: AppSpacing.sm) {
                        Text(item.title)
                            .font(AppTypography.titleCompact)
                            .foregroundColor(AppColors.textPrimary)
                            .multilineTextAlignment(.center)

                        // NO `lineLimit`. This is the whole point of the screen — the row
                        // truncates at three lines and this is where the rest of the sentence
                        // lives.
                        Text(item.body)
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, AppSpacing.lg)
                    }

                    VStack(spacing: AppSpacing.md) {
                        receivedCard
                        if group.count > 1 { alsoInGroupCard }
                        if !destinations.isEmpty { destinationsCard }
                    }
                    .padding(.horizontal, AppSpacing.lg)

                    Spacer()
                        .frame(height: 40)
                }
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text(item.title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Done") { dismiss() }
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }
        }
        .navigationDestination(item: $pushed) { destination in
            destinationView(destination)
        }
        // Matches the sheet convention the rest of the app uses (~19 sites) and that
        // `AlertDetailView` now adopts too, so the two halves of Activity are indistinguishable.
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    // MARK: - Pushed destination

    /// ⚠️ `NotificationRouteContent`, never `NotificationRouteDestination` — the latter wraps
    /// itself in a `NavigationStack` and nesting one inside this screen's stack double-stacks.
    @ViewBuilder
    private func destinationView(_ destination: AlertDestination) -> some View {
        switch destination.target {
        case .whale(let whaleId):
            WhaleProfileView(whaleId: whaleId)
        default:
            if let route = destination.route {
                NotificationRouteContent(route: route)
            }
        }
    }

    // MARK: - Header

    private var headerIcon: some View {
        ZStack {
            Circle()
                .fill(item.iconColor.opacity(0.15))
                .frame(width: 72, height: 72)

            Image(systemName: item.iconName)
                .font(AppTypography.iconDisplay).fontWeight(.semibold)
                .foregroundColor(item.iconColor)
        }
    }

    // MARK: - Cards

    private var receivedCard: some View {
        card {
            detailRow(label: "Received", value: absoluteTime(item))
            // Only when delivery did NOT happen. Saying "delivered" on every other row would be
            // permanent chrome carrying no information — the same rule the row's footnote follows.
            if let note = deliveryNote(item) {
                detailRow(label: "Delivery", value: note)
            }
        }
    }

    /// The rest of a collapsed group.
    private var alsoInGroupCard: some View {
        card {
            detailRow(label: "Also in this alert", value: "\(group.count - 1) more")
            ForEach(group.items.dropFirst()) { member in
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(member.title)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    Text(member.body)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(absoluteTime(member))
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private var destinationsCard: some View {
        card {
            ForEach(destinations) { destination in
                AlertDestinationRow(destination: destination) {
                    pushed = destination
                }
            }
        }
    }

    private func card<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(spacing: AppSpacing.md) {
            content()
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }

    private func detailRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(value)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.trailing)
        }
    }

    // MARK: - Formatting

    /// An ABSOLUTE timestamp, deliberately — the row already shows "2h ago". A detail screen that
    /// repeated the relative time would add a line and no information.
    private func absoluteTime(_ event: NotificationEventDTO) -> String {
        guard let date = NotificationDetailView.parse(event.createdAt) else {
            return event.relativeTime
        }
        return NotificationDetailView.display.string(from: date)
    }

    /// Same wording as `NotificationInboxSection.footnote`, and nil for a normal delivery.
    private func deliveryNote(_ event: NotificationEventDTO) -> String? {
        switch event.deliveryState {
        case "deferred":  return "Held during quiet hours"
        case "no_device": return "Not sent to this device"
        case "failed":    return "Couldn't be delivered"
        default:          return nil
        }
    }

    /// Two parsers, for the same reason `NotificationEventDTO.relativeTime` needs two: Postgres
    /// emits fractional seconds for `timestamptz` only sometimes, and `ISO8601DateFormatter`
    /// silently returns nil for the shape it was not configured for.
    private static func parse(_ raw: String) -> Date? {
        withFraction.date(from: raw) ?? withoutFraction.date(from: raw)
    }

    private static let withFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let withoutFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let display: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        return f
    }()
}
