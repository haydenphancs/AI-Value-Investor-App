//
//  ScannerCard.swift
//  ios
//
//  Molecule: one card in the Home "Daily Scanners" carousel. Renders a header
//  (with an optional tappable "i" popover for cards that carry an explainer), a
//  kind-specific hero metric + sparkline, and an expandable leaderboard. Owns its
//  own ephemeral UI state (gainers/losers toggle, expand, info popover) — the
//  data comes from a single `DailyScanner` model.
//

import SwiftUI

struct ScannerCard: View {
    let scanner: DailyScanner
    var onEntryTap: ((ScannerEntry) -> Void)? = nil

    @State private var moversMode: MoversMode = .gainers
    @State private var expanded = false
    @State private var showInfo = false

    private var list: [ScannerEntry] {
        switch scanner.kind {
        case .movers: return moversMode == .gainers ? scanner.gainers : scanner.losers
        case .volume, .shorts: return scanner.entries
        }
    }

    private var head: ScannerEntry? { list.first }

    private var heroSparkColor: Color {
        switch scanner.kind {
        case .movers, .volume: return (head?.isPositive ?? true) ? AppColors.bullish : AppColors.bearish
        case .shorts: return scanner.accent
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.bottom, 13)

            heroRow

            expandButton
                .padding(.top, 13)

            if expanded {
                leaderboard
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(15)
        .background(AppColors.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .animation(.easeInOut(duration: 0.25), value: expanded)
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 9) {
            IconTile(systemName: scanner.iconSystemName, accent: scanner.accent,
                     size: 30, cornerRadius: 9, iconPointSize: 17)

            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    Text(scanner.title)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    // Tappable info affordance — shown only when this card carries
                    // an explainer. Tapping pops over the note instead of always
                    // occupying a box in the card.
                    if let note = scanner.infoNote {
                        Button { showInfo = true } label: {
                            Image(systemName: "info.circle.fill")
                                .font(.system(size: 14))
                                .foregroundColor(scanner.accent)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("More about \(scanner.title)")
                        .popover(isPresented: $showInfo) {
                            Text(note)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                                .multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(14)
                                .frame(maxWidth: 260)
                                .presentationCompactAdaptation(.popover)
                        }
                    }
                }
                // Subtitle is optional — an empty string (e.g. the Top Movers card)
                // hides the row without affecting the other cards.
                if !scanner.subtitle.isEmpty {
                    Text(scanner.subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer(minLength: 6)

            if scanner.kind == .movers {
                MoversToggle(mode: $moversMode)
            } else if let badge = scanner.badgeText {
                TintedTagBadge(text: badge, color: scanner.accent)
            }
        }
    }

    // MARK: - Hero row (metric + sparkline)

    private var heroRow: some View {
        HStack(alignment: .center, spacing: 12) {
            heroMetric
                .frame(maxWidth: .infinity, alignment: .leading)

            TintedSparkline(
                points: head?.spark ?? [],
                color: heroSparkColor,
                showBaseline: scanner.kind == .movers,
                showEndDot: true,
                lineWidth: 2.2
            )
            .frame(width: 104, height: 48)
        }
    }

    @ViewBuilder
    private var heroMetric: some View {
        if let head {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Text(head.symbol)
                        .font(AppTypography.dataLarge)
                        .foregroundColor(AppColors.textPrimary)
                    Text(head.name)
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(1)
                }

                switch scanner.kind {
                case .movers:
                    Text(head.primaryText)
                        .font(AppTypography.dataDisplay)
                        .foregroundColor(head.isPositive ? AppColors.bullish : AppColors.bearish)
                    Text("\(head.secondaryText) · #1 today")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)

                case .volume:
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(head.primaryText)
                            .font(AppTypography.dataDisplay)
                            .foregroundColor(AppColors.textPrimary)
                        Text(head.secondaryText)
                            .font(AppTypography.dataMedium)
                            .foregroundColor(head.isPositive ? AppColors.bullish : AppColors.bearish)
                    }
                    Text("avg daily volume · spiking")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)

                case .shorts:
                    Text(head.primaryText)
                        .font(AppTypography.dataDisplay)
                        .foregroundColor(scanner.accent)
                    Text("of float sold short")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
    }

    // MARK: - Expand button

    private var expandButton: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.25)) { expanded.toggle() }
        } label: {
            HStack(spacing: 6) {
                Text(scanner.expandCTA)
                    .font(AppTypography.labelSmallEmphasis)
                Image(systemName: "chevron.down")
                    .font(.system(size: 12, weight: .semibold))
                    .rotationEffect(.degrees(expanded ? 180 : 0))
            }
            .foregroundColor(AppColors.primaryBlue)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 9)
            .background(Color(hex: "14171F"))
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    // MARK: - Leaderboard

    private var leaderboard: some View {
        VStack(spacing: 0) {
            ForEach(list) { entry in
                ScannerLeaderboardRow(entry: entry, kind: scanner.kind) {
                    onEntryTap?(entry)
                }
            }
        }
        .padding(.top, 6)
    }
}

#Preview {
    ScrollView {
        VStack(spacing: 14) {
            ScannerCard(scanner: MockHomeRepository.movers)
            ScannerCard(scanner: MockHomeRepository.heavyTraffic)
            ScannerCard(scanner: MockHomeRepository.skepticalMoney)
        }
        .padding()
    }
    .background(AppColors.background)
}
