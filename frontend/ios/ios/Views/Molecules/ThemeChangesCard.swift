//
//  ThemeChangesCard.swift
//  ios
//
//  Molecule: "What changed this month" on the theme detail — the latest monthly review's
//  added / returned / removed stocks, each with its one-line reason.
//
//  The reasons are FIXED server templates about relevance or eligibility, never a price
//  move; the card carries "Not a recommendation" because a curated list that explains its
//  changes can otherwise read as buy/sell advice. A review with no changes is still shown
//  as one honest line — "reviewed, nothing better found" is information.
//

import SwiftUI

struct ThemeChangesCard: View {
    let reviewedOn: Date?
    let changes: [ThemeChange]
    var onTickerTap: ((String) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(alignment: .firstTextBaseline) {
                Text("What changed this month")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                Spacer(minLength: AppSpacing.sm)
                if let reviewedOn {
                    Text("Reviewed \(ThemeReviewDate.short(reviewedOn))")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            if changes.isEmpty {
                // Claims only what the rules guarantee: a list can stay unchanged while an
                // outsider ranks above a member (the buffer, a first strike, tenure), and the
                // rank includes the market and size tie-breakers, so "most closely tied" was
                // not a claim the review supports.
                Text("Reviewed — no changes this month. A stock is replaced only when a stronger on-theme company clearly outranks it.")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    ForEach(changes) { change in
                        row(change)
                    }
                }
            }

            Text("Reviewed monthly · Not a recommendation")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardSurface()
    }

    @ViewBuilder
    private func row(_ change: ThemeChange) -> some View {
        let content = HStack(alignment: .top, spacing: AppSpacing.sm) {
            Text(change.label)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(tint(change.kind))
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                // 0.08, not 0.14: the 11pt label is TEXT (4.5:1). primaryBlue on its own
                // 14% tint measured 4.25:1 in light mode; on 8% it is 4.63:1.
                .background(Capsule().fill(tint(change.kind).opacity(0.08)))
                .fixedSize()
            VStack(alignment: .leading, spacing: 2) {
                Text("\(change.name) · \(change.ticker)")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                if !change.reason.isEmpty {
                    Text(change.reason)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .contentShape(Rectangle())

        if change.kind == .removed {
            content.accessibilityElement(children: .combine)
        } else {
            // A stock that is IN the list opens its detail; a removed one is just history.
            Button { onTickerTap?(change.ticker) } label: { content }
                .buttonStyle(.plain)
                .accessibilityElement(children: .combine)
                .accessibilityHint("Opens \(change.ticker)")
        }
    }

    /// TEXT-role tokens — this ink is read, so it must clear 4.5:1 in both appearances.
    private func tint(_ kind: ThemeChange.Kind) -> Color {
        switch kind {
        case .added: return AppColors.primaryBlue
        case .returned: return AppColors.primaryBlue
        case .removed: return AppColors.textSecondary
        }
    }
}

#Preview {
    ThemeChangesCard(
        reviewedOn: ThemeReviewDate.parse("2026-10-01"),
        changes: [
            ThemeChange(dto: ThemeChangeDTO(ticker: "GEN", companyName: "Gen Digital Inc.", action: "added",
                                            reason: "Added: now held by most of the leading funds that track this theme."))!,
            ThemeChange(dto: ThemeChangeDTO(ticker: "PLL", companyName: "Piedmont Lithium", action: "removed",
                                            reason: "Removed: it no longer trades on a US exchange."))!,
        ])
        .padding()
        .background(AppColors.background)
}
