//
//  TrillionClubCard.swift
//  ios
//
//  Molecule: one company tile in Home › "Trillion-Dollar Club Bets", shaped like an Emerging
//  Frontiers tile (`TrendingThemeTile`): a fixed logo band on top, then the company's name and
//  ONE grey line — "8 holdings · 2 stakes". Everything else (holdings, stakes, sources, dates)
//  is one tap away on the detail screen. The 2026-09-24 redesign replaced a 292pt card that
//  carried all of it and read as a wall of text.
//
//  EVERY TILE IS THE SAME HEIGHT, by construction rather than by measurement: the band is a
//  fixed height, and the text band is always the same number of lines — the name and
//  `cardLine` on one line each (scaled down before they truncate) up to the Large sizes, and a
//  name that RESERVES two lines from xxLarge up, where one line would cut long names off.
//
//  The logo: `CompanyLogoView` for any symbol the logo CDN keys on (`ClubSanitize.logoSymbol`
//  — a U.S. ticker, or a local listing such as "2222.SR"), with the company's MONOGRAM as the
//  loading placeholder — never the symbol's first character, which would flash a "2" for
//  Saudi Aramco. No symbol → the monogram tile.
//
//  THE WHOLE TILE IS THE TAP TARGET: the label fills the tile (`maxHeight: .infinity` +
//  `contentShape`), so no strip of it ignores a tap. Neutral ink only — nothing on a tile is
//  good or bad news (pinned by test_ios_trillion_club_guards.py).
//

import SwiftUI

struct TrillionClubCard: View {
    let company: TrillionClubCompany
    /// The tile → the company's detail screen.
    let onTap: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    /// The logo band's fixed height — half of what keeps every tile the same size.
    static let bandHeight: CGFloat = 96
    private static let logoSize: CGFloat = 56
    /// Frontiers' tile radius, so the two sections read as one family.
    private static let cornerRadius: CGFloat = 15

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 0) {
                logoBand
                textBand
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous))
            .cardBorder(cornerRadius: Self.cornerRadius)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        // A label OVERRIDE on the Button itself — not `.accessibilityElement(children:)`,
        // which would wrap the Button in a new element without its activation action.
        .accessibilityLabel(company.cardAccessibilityText)
        .accessibilityHint("Opens \(company.name)'s stakes")
    }

    // MARK: - Logo band

    private var logoBand: some View {
        Color.clear
            .frame(maxWidth: .infinity)
            .frame(height: Self.bandHeight)
            .background(AppColors.cardBackgroundLight)
            .overlay { logo }
    }

    @ViewBuilder
    private var logo: some View {
        if let symbol = company.logoSymbol {
            CompanyLogoView(ticker: symbol, size: Self.logoSize, fallbackText: company.monogram)
                .accessibilityHidden(true)
        } else {
            Text(company.monogram)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textSecondary)
                .frame(width: Self.logoSize, height: Self.logoSize)
                // `cardBackground`, not `cardBackgroundNested`: the nested fill IS the band's
                // colour in dark (#252B3B on #252B3B, and `cardEdge` is clear there), which
                // left a floating letter with no tile.
                .background(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .cardFill(AppColors.cardBackground)
                )
                .accessibilityHidden(true)
        }
    }

    // MARK: - Text band

    private var textBand: some View {
        VStack(alignment: .leading, spacing: 3) {
            nameText
            Text(company.cardLine)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, AppSpacing.md)
        .padding(.top, 10)
        .padding(.bottom, AppSpacing.md)
    }

    /// One line (scaled before it truncates) up to Large-ish sizes; two RESERVED lines from
    /// `.xxLarge` up, so a one-line name and a two-line name make the same tile height. The
    /// switch is NOT at the accessibility sizes: `bodySmallEmphasis` reaches its 1.4× reading
    /// cap at xxxLarge, so at xxLarge–xxxLarge "Samsung Electronics" at the 0.8 scale floor
    /// already overflows a 375pt phone's tile.
    @ViewBuilder
    private var nameText: some View {
        let name = Text(company.name)
            .font(AppTypography.bodySmallEmphasis)
            .foregroundColor(AppColors.textPrimary)
        if dynamicTypeSize >= .xxLarge {
            name.lineLimit(2, reservesSpace: true)
        } else {
            name.lineLimit(1).minimumScaleFactor(0.8)
        }
    }
}

#Preview {
    HStack(alignment: .top, spacing: AppSpacing.md) {
        ForEach(MockHomeRepository.trillionClub.companies.prefix(2)) { company in
            TrillionClubCard(company: company, onTap: {})
                .frame(width: 174)
        }
    }
    .fixedSize(horizontal: false, vertical: true)
    .padding()
    .background(AppColors.background)
}
