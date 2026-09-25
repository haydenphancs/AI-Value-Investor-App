//
//  TrillionClubInfoSheet.swift
//  ios
//
//  Molecule: "About this section" for Trillion-Dollar Club Bets — short, in the shape of the
//  Ticker Detail "i" sheets (`ValuationInfoSheet`): an intro card, who's in the club, a
//  "What you'll see" legend (one sentence per thing on screen), the member list, and the
//  not-a-recommendation line. The 2026-09-24 redesign cut eight paragraphs to this; every
//  fact the old sheet guarded is still here, in one sentence.
//
//  Presented ONLY from a company's detail screen (its toolbar ⓘ) since 2026-09-24 — the Home
//  section header has no ⓘ of its own.
//
//  WHICH MEMBERS, AND WHAT THE LIST MEANS, TRAVEL TOGETHER (`Members`). The detail passes every
//  OTHER member — carded or not — under "Other members of the club". `.withoutCard` (the
//  members WITHOUT a card, captioned "no disclosed stake large enough for a card") was Home's
//  list and stays for a caller that has exactly that list: the detail once fed its full list to
//  the no-card caption and named Microsoft, Alphabet and nine more carded companies as having
//  no disclosed stake.
//
//  Hand-written and static on purpose: nothing in this section is generated text. It states
//  two 13F facts precisely: a 13F is due WITHIN 45 days (filers often file sooner), and it
//  lists some securities besides stocks. The whale sheet uses the same wording — keep the two
//  aligned. The membership sentence describes the backend rule in
//  `services/trillion_club/rules.py` (JOIN_CLOSES = 10, LEAVE_CLOSES = 20) — change both
//  together. Neutral ink only: the Valuation sheet's red/green dots would make a legend of
//  facts read as good or bad news.
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
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    introCard

                    section(
                        title: "Who's in the club",
                        body: TrillionClubCopy.membershipRule
                            + " Companies listed only outside the U.S. are reviewed by hand."
                    )

                    legend

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

                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text(TrillionClubCopy.detailFooter)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                        InlineDisclaimerNotice()
                    }
                }
                .padding(AppSpacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(AppColors.background)
            .navigationTitle("About this section")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    // MARK: - Intro

    private var introCard: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.md) {
                Image(systemName: "building.columns.fill")
                    .font(AppTypography.iconXL)
                    .foregroundColor(AppColors.primaryBlue)
                    .accessibilityHidden(true)
                Text("Trillion-Dollar Club")
                    .font(AppTypography.titleCompact)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
            }
            Text("What companies valued at $1 trillion or more own in other companies, from SEC "
                 + "filings and other official disclosures. Tap a company for its holdings and stakes.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.large).cardFill())
    }

    // MARK: - Legend

    /// One row per thing the section shows, like the Valuation sheet's meter rows — but with a
    /// neutral dot: these are facts, not a rating.
    private var legend: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What you\u{2019}ll see")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)

            legendRow(
                label: "Holdings",
                detail: "U.S.-listed stocks from the company\u{2019}s SEC Form 13F. "
                    + "A 13F is filed up to 45 days after the quarter ends, so its "
                    + "positions can be weeks old. Other securities it lists, such "
                    + "as convertible notes and options, are left out."
            )
            if let newly = ClubChangeKind.newlyReported.helpText {
                legendRow(label: "Newly reported", detail: newly + ".")
            }
            if let gone = ClubChangeKind.noLongerReported.helpText {
                legendRow(label: "No longer reported", detail: gone + ".")
            }
            legendRow(
                label: "Increased / Decreased shares",
                detail: "More or fewer shares than the quarter before \u{2014} counted in shares, so a "
                    + "holding whose value moved only with its price has no tag."
            )
            if let action = ClubChangeKind.corporateAction.helpText {
                legendRow(label: "Corporate action", detail: action + ".")
            }
            legendRow(
                label: "Other stakes",
                detail: "Private stakes, shares listed outside the U.S., commitments and other "
                    + "stakes not on a 13F \u{2014} each with the source that disclosed it and its date."
            )
            legendRow(
                label: "Stakes that came with a deal",
                detail: "Many of these stakes came with a business agreement between the two "
                    + "companies, such as a supply or partnership deal. Such a stake can say more "
                    + "about that relationship than about the stock; its source describes it."
            )
            legendRow(label: "Club member", detail: "The holding is itself valued at $1 trillion or more.")
        }
    }

    private func legendRow(label: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            HStack(spacing: AppSpacing.sm) {
                Circle()
                    .fill(AppColors.primaryBlue)
                    .frame(width: 8, height: 8)
                    .accessibilityHidden(true)
                Text(label)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(detail)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.medium).cardFill())
        // One element per row: "Holdings. U.S.-listed stocks from…".
        .accessibilityElement(children: .combine)
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
