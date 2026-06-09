//
//  ReportListRow.swift
//  ios
//
//  Molecule: the STANDARD report list row — Key Management's style, extracted so
//  the report's name/value lists read consistently. A flat row (no card chrome)
//  with a bold `label` primary on the left, muted `caption` sub-lines, a
//  right-aligned value column, and a hairline divider. Height grows with the
//  number of lines (Key Management = 2, Recent Transactions / Congress = 3).
//  Colors vary per line; fonts + spacing do not.
//

import SwiftUI

/// One styled text line in a `ReportListRow`. `isPrimary` → `label` (bold-ish);
/// otherwise `caption`. Default color is muted; callers override per line.
struct ReportRowText {
    let text: String
    var color: Color = AppColors.textMuted
    var isPrimary: Bool = false
}

struct ReportListRow: View {
    let leftPrimary: String
    var leftPrimaryColor: Color = AppColors.textPrimary
    /// Optional inline badge after the primary (e.g. Key Management's "43% owner").
    var chip: (text: String, color: Color)? = nil
    var leftLines: [ReportRowText] = []     // caption sub-lines (title, date, …)
    var rightLines: [ReportRowText] = []    // value column (first usually primary)

    var body: some View {
        VStack(spacing: AppSpacing.xs) {
            HStack(alignment: .top, spacing: AppSpacing.md) {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    HStack(spacing: AppSpacing.xs) {
                        Text(leftPrimary)
                            .font(AppTypography.label)
                            .foregroundColor(leftPrimaryColor)
                            .lineLimit(1)
                        if let chip {
                            Text(chip.text)
                                .font(AppTypography.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(chip.color)
                                .padding(.horizontal, AppSpacing.sm)
                                .padding(.vertical, 2)
                                .background(Capsule().fill(chip.color.opacity(0.15)))
                        }
                    }
                    ForEach(Array(leftLines.enumerated()), id: \.offset) { _, line in
                        cell(line)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    ForEach(Array(rightLines.enumerated()), id: \.offset) { _, line in
                        cell(line)
                    }
                }
            }

            Divider()
                .background(AppColors.textMuted.opacity(0.15))
        }
    }

    private func cell(_ line: ReportRowText) -> some View {
        Text(line.text)
            .font(line.isPrimary ? AppTypography.label : AppTypography.caption)
            .fontWeight(line.isPrimary ? .medium : .regular)
            .foregroundColor(line.color)
            .lineLimit(1)
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.md) {
        // Key Management style (2 lines)
        ReportListRow(
            leftPrimary: "Lawrence Joseph Ellison",
            chip: ("43% owner", AppColors.bullish),
            leftLines: [ReportRowText(text: "director, 10 percent owner")],
            rightLines: [
                ReportRowText(text: "1.16B", color: AppColors.textPrimary, isPrimary: true),
                ReportRowText(text: "$214.5B"),
            ]
        )
        // Recent Transactions style (3 lines)
        ReportListRow(
            leftPrimary: "Levey Stuart",
            leftLines: [
                ReportRowText(text: "officer: EVP, Chief Legal Officer"),
                ReportRowText(text: "04/20/2026"),
            ],
            rightLines: [
                ReportRowText(text: "-15K shares", color: AppColors.bearish, isPrimary: true),
                ReportRowText(text: "Informative Sell", color: AppColors.bearish),
                ReportRowText(text: "$176.19"),
            ]
        )
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
