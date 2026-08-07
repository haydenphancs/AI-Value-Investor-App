//
//  ScannerLeaderboardRow.swift
//  ios
//
//  Molecule: one rank row in a scanner card's expanded leaderboard. Coloring of
//  the two right-hand values depends on the scanner kind (see `ScannerEntry`).
//

import SwiftUI

struct ScannerLeaderboardRow: View {
    let entry: ScannerEntry
    let kind: ScannerKind
    var onTap: (() -> Void)? = nil

    private var primaryColor: Color {
        switch kind {
        case .movers: return entry.isPositive ? AppColors.bullish : AppColors.bearish
        case .volume: return AppColors.textPrimary
        case .shorts: return AppColors.neutral
        }
    }

    private var secondaryColor: Color {
        switch kind {
        case .movers, .shorts: return AppColors.textSecondary
        case .volume: return entry.isPositive ? AppColors.bullish : AppColors.bearish
        }
    }

    var body: some View {
        Button { onTap?() } label: {
            HStack(spacing: 10) {
                // `minWidth`, not `width: 16`: at 11pt a two-digit rank already clipped
                // at the DEFAULT content size, before Dynamic Type entered into it.
                Text("\(entry.rank)")
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .frame(minWidth: 20, alignment: .leading)

                VStack(alignment: .leading, spacing: 1) {
                    Text(entry.symbol)
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                    Text(entry.name)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)
                        .truncationMode(.tail)
                }

                Spacer(minLength: 8)

                VStack(alignment: .trailing, spacing: 1) {
                    Text(entry.primaryText)
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(primaryColor)
                    Text(entry.secondaryText)
                        .font(AppTypography.caption)
                        .foregroundColor(secondaryColor)
                }
            }
            .padding(.vertical, 8)
            .padding(.horizontal, 2)
            .contentShape(Rectangle())
            .overlay(alignment: .top) {
                Rectangle()
                    .fill(AppColors.textPrimary.opacity(0.05))
                    .frame(height: 1)
            }
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    VStack(spacing: 0) {
        ScannerLeaderboardRow(entry: MockHomeRepository.movers.gainers[0], kind: .movers)
        ScannerLeaderboardRow(entry: MockHomeRepository.heavyTraffic.entries[1], kind: .volume)
        ScannerLeaderboardRow(entry: MockHomeRepository.skepticalMoney.entries[1], kind: .shorts)
    }
    .padding()
    .background(AppColors.cardBackground)
}
