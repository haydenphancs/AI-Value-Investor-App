//
//  SegmentedTabControl.swift
//  ios
//
//  Atom: Custom segmented control for tab switching
//

import SwiftUI

struct SegmentedTabControl<T: Hashable & RawRepresentable>: View where T.RawValue == String {
    let tabs: [T]
    @Binding var selectedTab: T

    /// Unread counts to badge, keyed by `tab.rawValue`. Absent or `0` = no badge.
    ///
    /// Keyed by the raw value rather than by `T` so the dictionary needs no extra constraint —
    /// `T.RawValue` is already pinned to `String`. Defaulted so `ResearchHeader`, the only other
    /// call site, keeps its exact call.
    var badges: [String: Int] = [:]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(tabs, id: \.rawValue) { tab in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedTab = tab
                    }
                } label: {
                    Text(tab.rawValue)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(selectedTab == tab ? AppColors.textPrimary : AppColors.textMuted)
                        // An OVERLAY, never a layout sibling. The three modifiers directly below
                        // exist because three segments already split 393pt; a badge that took
                        // layout width would shrink every label to buy space for one of them.
                        // An overlay contributes no layout, so the segments do not move.
                        .overlay(alignment: .topTrailing) {
                            UnreadCountBadge(count: badges[tab.rawValue] ?? 0)
                                .offset(x: 14, y: -8)
                        }
                        // Segments split the width evenly, so every tab added narrows all of
                        // them — Tracking went from two to three ("Assets/Whales/Alerts") and
                        // each column lost a third. Without these, SwiftUI's only remedy is to
                        // wrap, and single-word labels have no inter-word break so they split
                        // mid-word. Same trade, and the same three modifiers, as `TabBarItem`.
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .allowsTightening(true)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.md)
                        .background(
                            selectedTab == tab
                                ? AppColors.cardBackgroundLight
                                : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(.plain)
                // `UnreadCountBadge` hides itself from VoiceOver, so the count is announced
                // here or not at all.
                .accessibilityLabel(
                    (badges[tab.rawValue] ?? 0) > 0
                        ? "\(tab.rawValue), \(badges[tab.rawValue] ?? 0) new"
                        : tab.rawValue
                )
            }
        }
        .padding(AppSpacing.xs)
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selected = TrackingTab.assets

        var body: some View {
            SegmentedTabControl(
                tabs: TrackingTab.allCases,
                selectedTab: $selected
            )
            .padding()
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
}
