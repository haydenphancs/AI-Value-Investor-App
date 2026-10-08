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
//  LOCKED (2026-10-04): a Free caller is not sent a row that names a company its plan
//  withholds from the list (backend `theme_detail_redaction.py`), only how many there were
//  (`lockedCount`). Those become one locked line that opens the plan sheet — and the
//  "no changes" sentence is never shown while rows were withheld, because it would be false.
//

import SwiftUI

struct ThemeChangesCard: View {
    let reviewedOn: Date?
    let changes: [ThemeChange]
    var onTickerTap: ((String) -> Void)? = nil
    /// Change rows the caller's plan withholds (they name a company the list hides).
    var lockedCount: Int = 0
    /// Opens the plan sheet from the locked line.
    var onLockedTap: (() -> Void)? = nil

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

            if changes.isEmpty && lockedCount <= 0 {
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
                    if lockedCount > 0 {
                        lockedRow
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

    private var lockedTitle: String {
        lockedCount == 1 ? "1 more change" : "\(lockedCount) more changes"
    }

    /// The withheld rows, as one line: a count, never a name (none was sent).
    private var lockedRow: some View {
        Button { onLockedTap?() } label: {
            HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {
                // TEXT-role tokens: the glyph and the hint are read, so 4.5:1 in both modes.
                Image(systemName: "lock.fill")
                    .font(AppTypography.iconXS)
                    .foregroundColor(AppColors.primaryBlue)
                VStack(alignment: .leading, spacing: 2) {
                    Text(lockedTitle)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Upgrade to see every change")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Shows upgrade options")
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

#Preview("Free — rows withheld") {
    ThemeChangesCard(
        reviewedOn: ThemeReviewDate.parse("2026-10-01"),
        changes: [
            ThemeChange(dto: ThemeChangeDTO(ticker: "PLL", companyName: "Piedmont Lithium", action: "removed",
                                            reason: "Removed: it no longer trades on a US exchange."))!,
        ],
        lockedCount: 2)
        .padding()
        .background(AppColors.background)
}
