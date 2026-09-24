//
//  TrillionClubSection.swift
//  ios
//
//  Organism: Home › "Trillion-Dollar Club Bets" — what the companies valued at $1 trillion or
//  more own in other companies, one card per company in a horizontal row. Placed after
//  Emerging Frontiers and above the disclaimer; hidden when there are no cards.
//
//  A PLAIN horizontal `ScrollView` + `HStack`, paged by `.viewAligned`, and deliberately NOT
//  endless: a fixed handful of companies has a real first and last card. Three things this
//  file must not grow, each of which has frozen or hung Home before and is pinned by
//  backend/tests/test_ios_trillion_club_guards.py:
//   • no `Lazy*` stack — a lazy container around cards that size themselves hung the feed
//     (project_home_feed_lazyvstack_hang);
//   • no `.scrollPosition(id:)` — it writes its binding back DURING layout (banned app-wide);
//   • no `GeometryReader` — the cards have a fixed width and a free height instead.
//
//  Equal-height cards come from `.fixedSize(horizontal: false, vertical: true)` on the row:
//  the row takes its tallest card's ideal height and every card fills it, so a short card's
//  "Open profile" button sits on the same baseline as its neighbours.
//
//  THIS SECTION PRESENTS NOTHING ITSELF. The ⓘ button calls `onInfoTap`, and Home owns and
//  presents the info sheet, so Home's `.onPresentationReset` can take it down. A sheet owned
//  here escaped that reset: a push tapped while it was open queued its screen BEHIND the sheet,
//  and nothing appeared until the user closed the sheet by hand.
//

import SwiftUI

struct TrillionClubSection: View {
    let group: TrillionClubGroup
    /// A card → that company's detail screen.
    let onCompanyTap: (TrillionClubCompany) -> Void
    /// "Open profile" on an investor-profile card (Berkshire) → its whale profile.
    let onProfileTap: (TrillionClubCompany) -> Void
    /// The ⓘ button → Home presents the info sheet (see the header: Home owns it).
    let onInfoTap: () -> Void

    var body: some View {
        if !group.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                header
                    .padding(.horizontal, AppSpacing.lg)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        ForEach(group.companies) { company in
                            TrillionClubCard(
                                company: company,
                                onTap: { onCompanyTap(company) },
                                onProfileTap: { onProfileTap(company) }
                            )
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
            onProfileTap: { _ in },
            onInfoTap: {}
        )
        .padding(.vertical)
    }
    .background(AppColors.background)
}
