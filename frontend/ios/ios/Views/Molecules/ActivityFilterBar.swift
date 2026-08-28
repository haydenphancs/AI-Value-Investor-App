//
//  ActivityFilterBar.swift
//  ios
//
//  Molecule: the "Activity" section title with its filter chips on the SAME row.
//

import SwiftUI

/// Section title + a horizontally scrolling strip of `AccentFilterChip`s.
///
/// WHY IT IS NOT `SectionHeader`. A TestFlight tester asked for the tags "on the top (same row as
/// Activity), just like in the Report tab". `SectionHeader` is an atom with ~100 call sites whose
/// body is `HStack { Text; Spacer(); … }` — that internal `Spacer()` would shove a scrolling strip
/// hard right with no width to scroll in, and adding a generic trailing slot to it would ripple
/// through every one of those call sites to serve one caller. The title styling here is copied
/// from `SectionHeader` deliberately (`AppTypography.heading` + `AppColors.textPrimary`); keep the
/// two in step.
///
/// The chip strip is the same construction as the Reports tab's
/// (`ReportsListSection.headerRow`) — same `ScrollView(.horizontal, showsIndicators: false)`, same
/// `AppSpacing.xs` gap, same 2pt inset so the capsules do not clip — and the same
/// `AccentFilterChip` atom, which is what "just like in the Report tab" means concretely.
///
/// ⚠️ This is a LEAF in the Alerts tab's `LazyVStack`, the same shape as the `SectionHeader` it
/// replaces. It is not the "child `View` struct is an opaque boundary" hazard documented at the
/// top of `AlertsTabContent` — that one is about a struct that WRAPS many rows and hides them
/// from the lazy stack. This one wraps nothing.
struct ActivityFilterBar: View {
    let title: String

    /// Buckets that have at least one row on screen right now. The caller derives this from the
    /// data, so a chip can never filter to an empty list on its first tap.
    let available: [ActivityFilter]

    @Binding var selection: Set<ActivityFilter>

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text(title)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
                // Hold the title's natural width — without this the scroll view beside it
                // competes for space and the word gets squeezed.
                .fixedSize(horizontal: true, vertical: false)

            // Below two buckets there is nothing to choose BETWEEN: a lone chip either shows
            // everything or shows everything, so it is pure noise on a row the user reads as a
            // heading. Falling back to a plain `Spacer()` leaves the title identical to every
            // other section header on the screen.
            if available.count >= 2 {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: AppSpacing.xs) {
                        ForEach(available) { filter in
                            AccentFilterChip(
                                label: filter.label,
                                accent: filter.accent,
                                accentFill: filter.accentFill,
                                isSelected: selection.contains(filter),
                                action: { toggle(filter) }
                            )
                        }
                    }
                    .padding(.horizontal, 2)
                }
            } else {
                Spacer()
            }
        }
    }

    /// Multi-select, OR within the selection — the same grammar as the Reports tab's persona
    /// tags (`ResearchViewModel.togglePersonaTag`). Empty selection means "show everything",
    /// so tapping the last active chip off restores the full list.
    private func toggle(_ filter: ActivityFilter) {
        withAnimation(.easeInOut(duration: 0.2)) {
            if selection.contains(filter) {
                selection.remove(filter)
            } else {
                selection.insert(filter)
            }
        }
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selection: Set<ActivityFilter> = [.smartMoney]

        var body: some View {
            VStack(alignment: .leading, spacing: AppSpacing.lg) {
                ActivityFilterBar(
                    title: "Activity",
                    available: ActivityFilter.allCases,
                    selection: $selection
                )
                ActivityFilterBar(
                    title: "Activity",
                    available: [.prices],
                    selection: $selection
                )
            }
            .padding(.horizontal, AppSpacing.lg)
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
}
