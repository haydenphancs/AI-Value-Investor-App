//
//  CompetitorsInfoSheet.swift
//  ios
//
//  Molecule: how the report's Competitors list is built — where the rivals come from,
//  what the row order means, what the threat badge and score mean, and that 5 is a
//  neutral midpoint. Mirrors `ValuationInfoSheet`.
//
//  TestFlight #57 (AVGO): "Is NVIDIA the main competitor? How did we get the
//  competitors?" Nothing on screen said the rows were sorted by threat, not by how
//  directly each rival competes.
//
//  The order paragraph follows the report's own marker: a threat-ordered report —
//  every report stored before 2026-10-01 — is never described as "most direct first",
//  and the customer/partner exclusion is claimed only for the list that applied it.
//  The scoring paragraph follows it too: only a most-direct list scores on directness.
//
//  Web research for rivals was retired on 2026-10-02: every new report is threat-ordered,
//  built from the industry peer list, and scores each rival with a neutral moat factor.
//  Stored reports keep the research list and the moat scaling they were built with, so
//  the copy covers both and never says the moat scaling always applies.
//

import SwiftUI

struct CompetitorsInfoSheet: View {
    /// The order the report's rows are in (`moat_competition.competitor_order`).
    var order: CompetitorListOrder = .highestThreat

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    headerSection
                    sourceSection
                    orderSection
                    threatSection
                    midpointSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Understanding Competitors")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "person.2.fill")
                    .font(AppTypography.iconXL)
                    .foregroundColor(AppColors.primaryBlue)
                Text("Competitors")
                    .font(AppTypography.titleCompact)
                    .foregroundColor(AppColors.textPrimary)
            }
            Text("Up to five public companies that compete with this one. Each row names the rival, the area where it competes when that is known, and a threat badge with a score from 0 to 10.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.large).cardFill())
    }

    private var sourceSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("How they are found")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            Text(sourceText)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var sourceText: String {
        switch order {
        case .mostDirect:
            return "Cay researches the company's latest annual report, its recent earnings calls and the past year of public coverage to find the companies that sell competing products. Companies that are mainly customers, suppliers or partners are left out."
        case .highestThreat:
            return "The list comes from public companies in the same industry. Some earlier reports used Cay's research into company filings and public coverage instead."
        }
    }

    private var orderSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("The order")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            Text(orderText)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.large).cardFill())
    }

    private var orderText: String {
        switch order {
        case .mostDirect:
            return "Most direct first: the first row is the rival that competes for the largest share of this company's revenue. The order shows how directly each rival competes, not how big or how threatening it is — the badge and score show that, so a rival further down can carry a higher score."
        case .highestThreat:
            return "Highest threat first: rows are sorted by their threat score, so the first row is the strongest competitive threat. It is not necessarily the company's closest or most direct rival."
        }
    }

    private var threatSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("The threat badge and score")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            VStack(spacing: AppSpacing.md) {
                row(level: .high,
                    description: "A score of 7 or above: a strong competitive threat.")
                row(level: .moderate,
                    description: "A score above 3 and below 7.")
                row(level: .low,
                    description: "A score of 3 or below: a weak competitive threat.")
            }
            Text(scoringText)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// What the score blends, for the order the rows are in. Only the research list
    /// ranks rivals by directness; on a threat-ordered report the "directness" input is
    /// just the rival's position in its source list (the industry peer list's own order
    /// on the fallback path), so that path must never claim "how directly". The
    /// highest-threat wording is true of both sources and matches what Cay says in chat.
    private var scoringText: String {
        switch order {
        case .mostDirect:
            return "When return-on-capital figures exist for both companies, the score blends how directly the rival competes with how its return on invested capital compares with this company's and, on reports where the rival's own moat score was available, scales the result by it. Otherwise it compares the rival's operating margin, return on equity and revenue growth with its sector's median."
        case .highestThreat:
            return "When return-on-capital figures exist for both companies, the score blends the rival's position in the list it came from (Cay's research or the industry peer list) with how its return on invested capital compares with this company's and, on reports where the rival's own moat score was available, scales the result by it. Otherwise it compares the rival's operating margin, return on equity and revenue growth with its sector's median."
        }
    }

    private var midpointSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("5 is a neutral midpoint")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            Text("A score of 5 is an even match, not a pass mark. Above 5 the rival is a bigger threat than an even match; below 5, a smaller one. When the score compares the rival with its sector, 5 is the sector median.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("The score describes competitive position only. It is not a rating of either company's shares and not a recommendation.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.large).cardFill())
    }

    private func row(level: CompetitorThreatLevel, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Circle().fill(level.color).frame(width: 10, height: 10)
                Text(level.rawValue)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }
            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.medium).cardFill())
    }
}

#Preview("Most direct first") {
    CompetitorsInfoSheet(order: .mostDirect)
}

#Preview("Highest threat first") {
    CompetitorsInfoSheet(order: .highestThreat)
}
