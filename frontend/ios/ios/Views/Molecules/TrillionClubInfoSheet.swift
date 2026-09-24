//
//  TrillionClubInfoSheet.swift
//  ios
//
//  Molecule: "About Trillion-Dollar Club Bets" — what the section shows, what a 13F leaves out,
//  why the data is weeks old, how membership is decided, where every figure comes from, and
//  a list of club members.
//
//  WHICH MEMBERS, AND WHAT THE LIST MEANS, TRAVEL TOGETHER (`Members`). Home passes the members
//  WITHOUT a card, captioned "no disclosed stake large enough for a card"; a company's detail
//  passes every OTHER member — carded or not — under "Other members of the club". The detail
//  once fed its full list to the no-card caption and named Microsoft, Alphabet and nine more
//  carded companies as having no disclosed stake.
//
//  Hand-written and static on purpose: nothing in this section is generated text. It follows
//  the app's existing 13F explanations (`WhalePortfolioStatsInfoSheet`, `RecentTradesInfoSheet`)
//  minus their advice-like lines, and states two facts precisely: a 13F is due WITHIN 45 days
//  (filers often file sooner), and it lists some securities besides stocks. The whale sheet
//  now uses the same wording — keep the two aligned.
//  The membership sentence describes the backend rule in `services/trillion_club/rules.py`
//  (JOIN_CLOSES = 10, LEAVE_CLOSES = 20) — change both together.
//
//  Every section title is a VoiceOver HEADING (the rotor's Headings jump between them).
//

import SwiftUI

struct TrillionClubInfoSheet: View {
    /// Which members the sheet lists — and, bound to it, what the list is called.
    enum Members {
        /// Home: members with no material disclosed stake, so no card.
        case withoutCard([ClubMemberBrief])
        /// A company's detail: every OTHER member of the club, with or without a card.
        case others([ClubMemberBrief])

        var list: [ClubMemberBrief] {
            switch self {
            case .withoutCard(let members), .others(let members): return members
            }
        }

        var heading: String {
            switch self {
            case .withoutCard: return "Also in the club"
            case .others: return "Other members of the club"
            }
        }

        /// Only the no-card list may say "no disclosed stake": it is false of any carded member.
        var caption: String? {
            switch self {
            case .withoutCard: return "Members with no disclosed stake large enough for a card."
            case .others: return nil
            }
        }
    }

    /// May be empty — the block is then hidden.
    let members: Members

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    section(
                        title: "What this shows",
                        body: "Stakes that companies valued at $1 trillion or more hold in other "
                            + "companies. If a company files an SEC Form 13F, its U.S.-listed "
                            + "holdings come from that filing. Other stakes are ones disclosed in "
                            + "its own reports or in official announcements, each with its source."
                    )

                    section(
                        title: "What a 13F leaves out",
                        body: "Form 13F lists U.S.-listed stocks and some other securities, such "
                            + "as convertible notes and options — we leave those out. Private "
                            + "companies, shares listed only outside the U.S., cash and short "
                            + "positions never appear on one, so a company's stakes are usually "
                            + "wider than its 13F."
                    )

                    section(
                        title: "Why it's weeks old",
                        body: "A 13F is filed up to 45 days after the quarter ends, so its "
                            + "positions are usually several weeks old when they appear and may "
                            + "already have changed. That delay is set by law, not by us."
                    )

                    section(
                        title: "\u{201C}Newly reported\u{201D} is not the same as new money",
                        body: "A holding can appear for the first time because a private company "
                            + "went public or a deal turned into shares — not because of a market "
                            + "purchase. A holding that leaves the filing may have been sold, "
                            + "merged, fallen below the reporting threshold, or been kept "
                            + "confidential. Changes are counted in shares, so a holding whose "
                            + "value moved with its price is unchanged."
                    )

                    section(
                        title: "Many stakes come with a deal",
                        body: "Some of these companies are also customers, suppliers or partners of "
                            + "the company they hold. A stake can say more about that relationship "
                            + "than about the stock. Stakes tied to a business agreement are marked "
                            + "\u{201C}Tied to a deal\u{201D}."
                    )

                    section(
                        title: "Private stakes",
                        body: "Private companies have no market price. Where we show a figure, it's "
                            + "the one the owner disclosed — carried at, invested, or committed up "
                            + "to — with its source and date. Stakes may have changed since."
                    )

                    section(
                        title: "Who's in the club",
                        body: TrillionClubCopy.membershipRule + " The buffer keeps a company near "
                            + "the line from flickering in and out. Companies listed only outside "
                            + "the U.S. are reviewed by hand from a cited market value."
                    )

                    if !members.list.isEmpty {
                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            Text(members.heading)
                                .font(AppTypography.headingSmall)
                                .foregroundColor(AppColors.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                                .accessibilityAddTraits(.isHeader)
                            if let caption = members.caption {
                                Text(caption)
                                    .font(AppTypography.bodySmall)
                                    .foregroundColor(AppColors.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            FlowLayout(spacing: AppSpacing.xs, lineSpacing: AppSpacing.xs) {
                                ForEach(members.list) { member in
                                    TintedTagBadge(text: member.name, color: AppColors.textSecondary,
                                                   backgroundOpacity: 0.10)
                                }
                            }
                        }
                    }

                    section(
                        title: "Sources",
                        body: "SEC Form 13F filings for U.S.-listed holdings. Everything else comes "
                            + "from each company's own filings — 10-K, 10-Q, 20-F, 8-K and Schedule "
                            + "13D/13G — or its official releases, and every stake names its source "
                            + "and the date it describes. News reports alone are never used."
                    )

                    section(title: "Not a recommendation", body: TrillionClubCopy.detailFooter)

                    InlineDisclaimerNotice()
                        .padding(.top, AppSpacing.xs)
                }
                .padding(AppSpacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(AppColors.background)
            .navigationTitle("About this section")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    /// A titled paragraph. The title is its own element with the HEADER trait — not combined
    /// with the body, which would make the whole paragraph one unlabelled blob to the rotor.
    private func section(title: String, body: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text(title)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text(body)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#Preview("Home") {
    TrillionClubInfoSheet(members: .withoutCard(MockHomeRepository.trillionClub.alsoInClub))
}

#Preview("Detail") {
    TrillionClubInfoSheet(members: .others(TrillionClubSamples.nvidiaDetailLocked?.otherMembers ?? []))
}
