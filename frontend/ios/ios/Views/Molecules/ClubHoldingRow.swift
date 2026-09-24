//
//  ClubHoldingRow.swift
//  ios
//
//  Molecule: one 13F position of a Trillion-Dollar Club company — a HOLDING (name, weight in
//  the filing, shares and reported value) or a CHANGE (what moved vs the quarter before).
//  Takes ONE model, `ClubPosition`, which carries both shapes.
//
//  NEUTRAL INK, ALWAYS. The change pill is text-role grey (or `primaryBlue` for "Newly
//  reported", the one outcome worth drawing the eye to) — never a gain/loss colour. A green
//  "Increased" reads as a buy signal, and most "new" rows in Q2 2026 were IPO conversions, not
//  purchases. Pinned by backend/tests/test_ios_trillion_club_guards.py.
//
//  Not a Button: the detail screen wraps a row in one only when it has a routable symbol, so a
//  row with nowhere to go never looks tappable.
//

import SwiftUI

struct ClubHoldingRow: View {
    enum Style {
        /// Home card: name (up to two lines, the Club member chip under it) + weight.
        case compact
        /// Detail › Holdings: symbol, shares, value, and the change pill.
        case holding
        /// Detail › Changes: what moved, in shares.
        case change
    }

    let position: ClubPosition
    var style: Style = .holding
    /// Draws a trailing chevron — set by a caller that wraps the row in a Button.
    var showsChevron: Bool = false

    var body: some View {
        switch style {
        case .compact: compactRow
        case .holding, .change: fullRow
        }
    }

    // MARK: - Compact (Home card)

    private var compactRow: some View {
        // The NAME gets the room: up to two lines, with the Club member chip on its own line
        // UNDER it. Pinned beside the name, the chip left it ~68pt of a 236pt panel, so the
        // headline row of two 13F cards read "Space E… [Club member] 95%".
        HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(position.name)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                if position.clubMemberSlug != nil {
                    ClubStakeChip(chip: .clubMember)
                }
            }
            Spacer(minLength: AppSpacing.xs)
            if let weight = position.weightText {
                Text(weight)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .monospacedDigit()
                    .fixedSize()
                    .layoutPriority(1)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(position.accessibilityText)
    }

    // MARK: - Full (detail)

    private var fullRow: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(position.name)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                if let line = secondaryLine {
                    Text(line)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if showsPill || position.clubMemberSlug != nil {
                    FlowLayout(spacing: AppSpacing.xs, lineSpacing: AppSpacing.xs) {
                        if showsPill, let change = position.change, let label = change.pillLabel {
                            changePill(label, change: change)
                        }
                        if position.clubMemberSlug != nil {
                            ClubStakeChip(chip: .clubMember)
                        }
                    }
                }

                // A sentence, so OUTSIDE the flow, on its own line at the column's width. (Inside it,
                // before `FlowLayout` capped children at the row width on 2026-09-24, this line was laid
                // out at its one-line width and ran into the weight column at larger text sizes.)
                if let small = position.smallText {
                    Text(small)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Spacer(minLength: AppSpacing.sm)

            if let weight = position.weightText {
                Text(weight)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .monospacedDigit()
            }
            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(AppTypography.iconXS)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityHidden(true)
            }
        }
        .padding(.vertical, AppSpacing.sm)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(position.accessibilityText)
    }

    private var secondaryLine: String? {
        switch style {
        case .change: return position.changeLine ?? position.holdingLine
        case .holding, .compact: return position.holdingLine
        }
    }

    /// A holdings list shows the pill only when something happened — seven "Unchanged" pills
    /// in eight rows are noise. The Changes list always shows it (unchanged rows never arrive).
    private var showsPill: Bool {
        guard let change = position.change else { return false }
        return style == .change || change != .unchanged
    }

    private func changePill(_ label: String, change: ClubChangeKind) -> some View {
        // TEXT-role inks. `primaryBlue` on its own 8% tint measures 4.63:1 in light (14% was
        // 4.25 — ThemeChangesCard's measurement); `textSecondary` at 10% is ≈6.6:1.
        let ink = change == .newlyReported ? AppColors.primaryBlue : AppColors.textSecondary
        return TintedTagBadge(text: label, color: ink,
                              backgroundOpacity: change == .newlyReported ? 0.08 : 0.10)
    }
}

#Preview {
    let group = MockHomeRepository.trillionClub
    let holdings = group.companies.first?.topHoldings ?? []
    return VStack(alignment: .leading, spacing: 0) {
        ForEach(holdings) { ClubHoldingRow(position: $0, style: .compact) }
        Divider().overlay(AppColors.divider)
        ForEach(holdings) { ClubHoldingRow(position: $0, style: .holding, showsChevron: true) }
        Divider().overlay(AppColors.divider)
        ForEach(TrillionClubSamples.nvidiaDetailLocked?.changes ?? []) {
            ClubHoldingRow(position: $0, style: .change)
        }
    }
    .padding()
    .cardSurface()
    .padding()
    .background(AppColors.background)
}
