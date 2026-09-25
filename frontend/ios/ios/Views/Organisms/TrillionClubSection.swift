//
//  TrillionClubSection.swift
//  ios
//
//  Organism: Home › "Trillion-Dollar Club Bets" — what the companies valued at $1 trillion or
//  more own in other companies. Laid out like Emerging Frontiers: columns of TWO small tiles
//  (logo, name, one line), two columns to a screen, swiped sideways. Placed after Emerging
//  Frontiers and above the disclaimer; hidden when there are no cards.
//
//  A PLAIN horizontal `ScrollView` + `HStack` of columns, paged by `.viewAligned`, and
//  deliberately NOT endless (unlike Frontiers): a fixed handful of companies has a real first
//  and last tile. Three things this file must not grow, each of which has frozen or hung Home
//  before and is pinned by backend/tests/test_ios_trillion_club_guards.py:
//   • no `Lazy*` stack — a lazy container around cards that size themselves hung the feed
//     (project_home_feed_lazyvstack_hang);
//   • no `.scrollPosition(id:)` — it writes its binding back DURING layout (banned app-wide);
//   • no `GeometryReader` — a column's width comes from `.containerRelativeFrame`, exactly as
//     in Frontiers, where a horizontal ScrollView's width is its parent's, not its content's.
//
//  Equal-height tiles: every tile is the same height by construction (`TrillionClubCard`),
//  and `.fixedSize(horizontal: false, vertical: true)` on the row makes every column the
//  tallest one's height. An ODD last column fills its second slot with a blank, so its single
//  tile keeps a tile's height instead of stretching to fill two.
//
//  THIS SECTION PRESENTS NOTHING ITSELF. The ⓘ button calls `onInfoTap`, and Home owns and
//  presents the info sheet, so Home's `.onPresentationReset` can take it down. A sheet owned
//  here escaped that reset: a push tapped while it was open queued its screen BEHIND the sheet,
//  and nothing appeared until the user closed the sheet by hand.
//

import SwiftUI

struct TrillionClubSection: View {
    let group: TrillionClubGroup
    /// A tile → that company's detail screen.
    let onCompanyTap: (TrillionClubCompany) -> Void
    /// The ⓘ button → Home presents the info sheet (see the header: Home owns it).
    let onInfoTap: () -> Void

    var body: some View {
        if !group.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                header
                    .padding(.horizontal, AppSpacing.lg)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        ForEach(columnStarts, id: \.self) { start in
                            column(startingAt: start)
                                // Two columns fill the row, as in Frontiers, so a swipe
                                // advances one column (both stacked tiles) at a time.
                                .containerRelativeFrame(.horizontal) { length, _ in
                                    (length - 2 * AppSpacing.lg - AppSpacing.md) / 2
                                }
                        }
                    }
                    .scrollTargetLayout()
                    .fixedSize(horizontal: false, vertical: true)
                }
                .scrollTargetBehavior(.viewAligned)
                .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)

                Text(TrillionClubCopy.footer)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.sm)
            }
        }
    }

    // MARK: - Columns

    /// The index of each column's first company: column c holds companies 2c and 2c+1, so
    /// the largest company sits top-left and the order reads down each column.
    private var columnStarts: [Int] { Array(stride(from: 0, to: group.companies.count, by: 2)) }

    private func column(startingAt start: Int) -> some View {
        VStack(spacing: AppSpacing.md) {
            tile(group.companies[start])
            if start + 1 < group.companies.count {
                tile(group.companies[start + 1])
            } else if group.companies.count > 2 {
                // An odd last column beside a full one: that column sets the row to two tiles
                // plus spacing, so a flexible blank takes the second slot and the single tile
                // gets a tile's share rather than all of it. With ONE company there is no full
                // column — a blank would then set the row to a tile plus ~10pt and halve it.
                Color.clear
                    .accessibilityHidden(true)
            }
        }
    }

    private func tile(_ company: TrillionClubCompany) -> some View {
        TrillionClubCard(company: company, onTap: { onCompanyTap(company) })
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {
                Text(TrillionClubCopy.title)
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 0)
                Button {
                    onInfoTap()
                } label: {
                    Image(systemName: "info.circle")
                        .font(AppTypography.iconSmall)
                        .foregroundColor(AppColors.textMuted)
                        .frame(width: 22, height: 22)
                        .hitSlop(reaching: 22)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("About \(TrillionClubCopy.title)")
                .accessibilityHint("Explains where these stakes come from")
            }

            Text(TrillionClubCopy.subtitle)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 4)
                .padding(.bottom, 13)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    ScrollView {
        TrillionClubSection(
            group: MockHomeRepository.trillionClub,
            onCompanyTap: { _ in },
            onInfoTap: {}
        )
        .padding(.vertical)
    }
    .background(AppColors.background)
}
