//
//  ClubStakeChip.swift
//  ios
//
//  Molecule: one grey chip on a Trillion-Dollar Club stake — Private, Non-U.S. listed,
//  Tied to a deal, Commitment, Club member, or "Listed since … — not on a 13F yet".
//
//  A PLAIN LABEL, never a Button: the chip describes the stake, it does not do anything, and a
//  tappable-looking chip that ignores taps is the dead-control pattern
//  `test_ios_unsupplied_callback_buttons.py` exists to catch. The label is a noun, never a
//  verdict — stakes listed abroad trade, so "Non-U.S. listed" rather than "not tradable".
//
//  Ink is the TEXT-role `textSecondary` on a 10% tint of itself (≈6.6:1 light, ≈6.7:1 dark on a
//  card; ≈6:1 on a nested card) — deliberately grey, and deliberately not a gain/loss token:
//  none of these facts is good or bad news. VoiceOver reads a full sentence
//  (`ClubChip.accessibilityText(source:)`), because "Private" alone says nothing about why it
//  matters — and a Commitment names the source that disclosed it.
//
//  `ClubChipGroup` lays a stake's chips out: word chips flow, and a SENTENCE chip ("Listed since
//  … — not on a 13F yet") gets its own line outside the flow. Until 2026-09-24 `FlowLayout` laid
//  every child out at its one-line width, so a sentence inside it ran past a 260pt card (279pt at
//  the default text size) instead of wrapping to the two lines it asks for. The atom now caps a
//  child at the row width; the sentence still gets its own line rather than sharing a row.
//

import SwiftUI

struct ClubStakeChip: View {
    let chip: ClubChip
    /// The stake's source title, named in the VoiceOver sentence where the chip describes a
    /// disclosure (Commitment). nil → the sentence says "its source".
    var source: String? = nil

    var body: some View {
        TintedTagBadge(
            text: chip.label,
            color: AppColors.textSecondary,
            systemImage: chip.systemImage,
            backgroundOpacity: 0.10,
            font: AppTypography.captionEmphasis,
            // "Listed since Jun 12, 2026 — not on a 13F yet" is a short sentence; two lines
            // keep it from truncating to "Listed since Jun 12…" on a narrow card. They only
            // take effect because `ClubChipGroup` offers it the column's width.
            textLineLimit: 2
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(chip.accessibilityText(source: source))
    }
}

/// A stake's chips: word chips in a `FlowLayout`, sentence chips each on their own line below
/// it, where the parent offers the column's width and the chip can wrap.
struct ClubChipGroup: View {
    let chips: [ClubChip]
    var source: String? = nil

    var body: some View {
        let words = chips.filter { !$0.isSentence }
        let sentences = chips.filter(\.isSentence)
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            if !words.isEmpty {
                FlowLayout(spacing: AppSpacing.xs, lineSpacing: AppSpacing.xs) {
                    ForEach(words) { ClubStakeChip(chip: $0, source: source) }
                }
            }
            ForEach(sentences) { chip in
                ClubStakeChip(chip: chip, source: source)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.md) {
        FlowLayout {
            ClubStakeChip(chip: .privateCompany)
            ClubStakeChip(chip: .nonUSListed)
            ClubStakeChip(chip: .tiedToDeal)
            ClubStakeChip(chip: .commitment)
            ClubStakeChip(chip: .clubMember)
        }
        if let date = ClubDate(iso: "2026-06-12") {
            ClubStakeChip(chip: .listedSince(date))
            ClubChipGroup(chips: [.privateCompany, .commitment, .listedSince(date)],
                          source: "AMD 10-Q")
                .frame(width: 200, alignment: .leading)
        }
    }
    .padding()
    .cardSurface()
    .padding()
    .background(AppColors.background)
}
