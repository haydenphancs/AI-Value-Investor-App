//
//  SignalDisclosureRow.swift
//  ios
//
//  Molecule: one expandable row inside the "App-Exclusive Signals" card —
//  icon tile, title/subtitle, a top pick, and a disclosure chevron that reveals
//  the top-leaders list.
//

import SwiftUI

struct SignalDisclosureRow: View {
    let signal: ExclusiveSignal
    var onLeaderTap: ((SignalLeader) -> Void)? = nil

    @State private var expanded = false

    var body: some View {
        VStack(spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.25)) { expanded.toggle() }
            } label: {
                HStack(spacing: 12) {
                    IconTile(systemName: signal.iconSystemName, accent: signal.accent,
                             size: 40, iconPointSize: 21)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(signal.title)
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                        Text(signal.subtitle)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                            .lineLimit(1)
                    }

                    Spacer(minLength: 6)

                    VStack(alignment: .trailing, spacing: 2) {
                        Text(signal.topSymbol)
                            .font(AppTypography.dataMedium)
                            .foregroundColor(AppColors.textPrimary)
                        Text(signal.topStat)
                            .font(AppTypography.captionSmall)
                            .foregroundColor(AppColors.textMuted)
                    }

                    Image(systemName: "chevron.down")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                }
                .padding(12)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                VStack(spacing: 7) {
                    ForEach(signal.leaders) { leader in
                        Button { onLeaderTap?(leader) } label: {
                            HStack {
                                Text(leader.symbol)
                                    .font(AppTypography.labelEmphasis)
                                    .foregroundColor(AppColors.textPrimary)
                                Spacer()
                                Text(leader.stat)
                                    .font(AppTypography.labelSmall)
                                    .foregroundColor(AppColors.textSecondary)
                            }
                            .padding(.horizontal, 11)
                            .padding(.vertical, 8)
                            .background(Color.white.opacity(0.03))
                            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.leading, 64)
                .padding(.trailing, 12)
                .padding(.bottom, 12)
                .padding(.top, 2)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .background(Color.white.opacity(0.03))
        .overlay(
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .stroke(Color.white.opacity(0.05), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 13, style: .continuous))
    }
}

#Preview {
    VStack(spacing: 9) {
        ForEach(MockHomeRepository.signals) { SignalDisclosureRow(signal: $0) }
    }
    .padding()
    .background(Color(hex: "1B2233"))
}
