//
//  TrillionClubCard.swift
//  ios
//
//  Molecule: one company in Home › "Trillion-Dollar Club Bets". Takes ONE model,
//  `TrillionClubCompany`, and renders one of four card shapes from its `kind`:
//
//   • 13F filer (NVIDIA)   — "8 U.S.-listed holdings · $63.4B reported", the top 3 holdings with
//                             their weights, the share-count change line, the filing dates.
//   • No 13F (Microsoft)   — why there is no holdings list, then the disclosed stakes.
//   • Non-U.S. (TSMC)      — "Each stake names its source and date.", then the stakes.
//   • Investor profile (Berkshire) — a pointer to its existing whale profile, never its 13F.
//
//  Every stake shows its figure with the verb its source used ("carried at", "invested",
//  "committed up to") and "per <source>, <date>". Change wording is share counts only, in
//  NEUTRAL ink — never a gain/loss token (pinned by test_ios_trillion_club_guards.py).
//
//  The logo is `CompanyLogoView` only for a real U.S. ticker (`ClubSanitize.usTicker`);
//  anything else gets a letter tile, because a guessed pseudo-ticker can fetch a DIFFERENT
//  listed company's logo, and "2222.SR" would show a "2" while it loads.
//
//  THE WHOLE CARD IS THE TAP TARGET. The row stretches every card to its tallest one, and that
//  extra height lives INSIDE the Button's label (`maxHeight: .infinity` + `contentShape`). A
//  `Spacer` outside the Button left about half of a short card dead under a chevron.
//
//  Text WRAPS, it does not truncate: the height is free, and a one-line cap cut the "as of"
//  date off seven of the twelve cards' source lines at the default text size.
//

import SwiftUI

struct TrillionClubCard: View {
    let company: TrillionClubCompany
    /// The card body → the company's detail screen.
    let onTap: () -> Void
    /// "Open profile" on an investor-profile card → its whale profile.
    let onProfileTap: () -> Void

    /// Fixed so the row pages cleanly and the next card peeks in on every phone width; the
    /// height is free, so Dynamic Type grows the card downward instead of clipping it.
    static let width: CGFloat = 292
    /// The Home card shows the first few stakes; the detail lists every one.
    private static let maxStakes = 2

    private var isThirteenF: Bool { company.kind == .thirteenF }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Button(action: onTap) {
                // Fills every point the row gives this card above the optional profile button,
                // so no part of the card surface ignores a tap.
                mainContent
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // A label OVERRIDE on the Button itself — not `.accessibilityElement(children:)`,
            // which would wrap the Button in a new element without its activation action.
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint("Opens \(company.name)'s stakes")

            if company.kind == .whaleLink, company.whaleId != nil {
                Button(action: onProfileTap) {
                    Label(TrillionClubCopy.openProfile, systemImage: "person.crop.circle")
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                        .frame(maxWidth: .infinity, minHeight: 44)
                        // Text on its own tint: 8% keeps primaryBlue at 4.63:1 in light.
                        .background(Capsule().fill(AppColors.primaryBlue.opacity(0.08)))
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityHint("Opens \(company.name)'s investor profile")
            }
        }
        .padding(AppSpacing.lg)
        .frame(width: Self.width, alignment: .topLeading)
        .frame(maxHeight: .infinity, alignment: .top)
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }

    // MARK: - Body

    private var mainContent: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            header

            if let marketValue = company.marketValueLine {
                Text(marketValue)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(2)
            }

            // A 13F filer before its first filing is processed shows the explainer instead:
            // nothing about a filing that is not on file.
            if isThirteenF, company.hasFilingOnFile {
                thirteenFBody
            }
            if let explainer = company.explainer {
                Text(explainer)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let notice = company.noticeText {
                HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                    Image(systemName: "clock")
                        .font(AppTypography.iconXS)
                        .foregroundColor(AppColors.textMuted)
                        .accessibilityHidden(true)
                    Text(notice)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            stakesBlock
        }
    }

    private var header: some View {
        HStack(spacing: AppSpacing.md) {
            logo
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(company.name)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                // TEXT-role ink on its own 8% tint (4.63:1 light).
                TintedTagBadge(text: company.badgeText, color: AppColors.primaryBlue,
                               backgroundOpacity: 0.08)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(AppTypography.iconXS)
                .foregroundColor(AppColors.textMuted)
                .accessibilityHidden(true)
        }
    }

    @ViewBuilder
    private var logo: some View {
        if let symbol = company.logoSymbol {
            CompanyLogoView(ticker: symbol, size: 40)
                .accessibilityHidden(true)
        } else {
            // No remote fetch: the name's first LETTER on a nested surface, in text-role ink.
            Text(company.monogram)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textSecondary)
                .frame(width: 40, height: 40)
                .background(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .cardFill(AppColors.cardBackgroundNested)
                )
                .accessibilityHidden(true)
        }
    }

    private var thirteenFBody: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            if let stat = company.holdingsStatLine {
                Text(stat)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if !company.topHoldings.isEmpty {
                // A card inside the card: `cardBackgroundNested`, or it measures 1.00:1 against
                // its parent in dark and the panel disappears.
                VStack(spacing: AppSpacing.xs) {
                    ForEach(company.topHoldings.prefix(3)) { position in
                        ClubHoldingRow(position: position, style: .compact)
                    }
                }
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium, style: .continuous)
                        .cardFill(AppColors.cardBackgroundNested)
                )
            }

            // Neutral ink: share counts, not a verdict.
            if let change = company.changeLine {
                Text(change)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let dates = company.filingDatesLine {
                Text(dates)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var stakesBlock: some View {
        if let heading = company.stakesHeading {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text(heading)
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textMuted)
                ForEach(company.stakes.prefix(Self.maxStakes)) { stake in
                    stakeLine(stake)
                }
                // Counted from EVERY published stake — the card lists only material ones, and
                // the detail lists them all.
                if let more = company.moreStakesText(shown: shownStakes) {
                    Text(more)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.top, AppSpacing.xs)
        }
    }

    private func stakeLine(_ stake: ClubStake) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            Text(stake.investeeName)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            if let figure = stake.figureText {
                Text(figure)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // "per <source>, <date>" — the date is the part a one-line cap cut off.
            Text(stake.sourceText)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            let chips = stake.chips(onThirteenFCard: isThirteenF)
            if !chips.isEmpty {
                ClubChipGroup(chips: chips, source: stake.sourceTitle)
                    .padding(.top, AppSpacing.xxs)
            }
        }
    }

    // MARK: - Accessibility

    private var shownStakes: Int { min(company.stakes.count, Self.maxStakes) }

    private var accessibilityLabel: String {
        var parts = [company.accessibilityText]
        if isThirteenF {
            parts += company.topHoldings.prefix(3).map(\.accessibilityText)
        }
        parts += company.stakes.prefix(Self.maxStakes).map { $0.accessibilityText(onThirteenFCard: isThirteenF) }
        if company.stakesHeading != nil, let more = company.moreStakesText(shown: shownStakes) {
            parts.append(more)
        }
        return parts.joined(separator: ". ")
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(alignment: .top, spacing: 12) {
            ForEach(MockHomeRepository.trillionClub.companies) { company in
                TrillionClubCard(company: company, onTap: {}, onProfileTap: {})
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding()
    }
    .background(AppColors.background)
}
