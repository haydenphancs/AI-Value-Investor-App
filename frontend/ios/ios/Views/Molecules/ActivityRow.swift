//
//  ActivityRow.swift
//  ios
//
//  Molecule: the ONE row shape for everything alert-shaped — tinted glyph, title,
//  subtitle, optional trailing detail, optional "new" dot.
//
//  WHY IT EXISTS. Tracking → Alerts stacked three card grammars in one scroll view:
//
//    • "Upcoming & Events"  rounded 14pt card, inset 16pt, 12pt gaps, hairline+shadow,
//                           40pt tinted circle glyph
//    • "Price Rules"        one bordered GROUP, rows separated by 1pt of page colour,
//                           no glyph
//    • "Notifications"      full-bleed SQUARE slabs, no edge, no glyph, an 8pt dot,
//                           11pt subtitle, 16pt of page colour between them
//
//  Three grammars carrying equally important information, and the split was not even
//  semantic — the digest and the notifications both cover whale trades, insider activity
//  and earnings. This is the single shape they all render through.
//
//  ⚠️ It is also the fix for a LIGHT-MODE bug. The notification rows used a bare
//  `.background(AppColors.cardBackground)` — no shape, no `cardEdge`, no shadow — which
//  in light is #FFFFFF on the #F4F5F8 page: 1.09:1 with no edge, i.e. invisible. It
//  survived review because dark separates by fill and looks fine. `.cardSurface()` is
//  what the theme rules require and what this uses.
//
//  Geometry is `AlertCardView`'s, because that was the compliant one of the three.
//

import SwiftUI

struct ActivityRow<Trailing: View>: View {
    let systemName: String
    let iconColor: Color
    let title: String
    let subtitle: String
    /// Small print under the subtitle — a relative time, a delivery note. Optional
    /// because the digest roll-ups have no timestamp to show.
    var footnote: String?
    /// Draws the unread dot. Only the notification rows use it.
    var isNew: Bool = false
    var onTap: (() -> Void)?
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                ZStack(alignment: .topLeading) {
                    AlertCategoryIcon(systemName: systemName, color: iconColor)

                    // Rides ON the glyph rather than taking a column of its own, so an
                    // unread row is exactly as wide as a read one and the titles of
                    // adjacent rows still line up.
                    if isNew {
                        Circle()
                            .fill(AppColors.primaryGraphic)
                            .frame(width: 10, height: 10)
                            .overlay(
                                Circle().strokeBorder(AppColors.cardBackground, lineWidth: 2)
                            )
                            .offset(x: -2, y: -2)
                    }
                }

                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(title)
                        .font(isNew ? AppTypography.bodyEmphasis : AppTypography.body)
                        .foregroundColor(AppColors.textPrimary)
                        .multilineTextAlignment(.leading)

                    Text(subtitle)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .multilineTextAlignment(.leading)
                        // Bounded so one long body cannot push a whole feed off screen,
                        // but `fixedSize` vertically so it is never clipped mid-line at
                        // large Dynamic Type sizes.
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)

                    if let footnote, !footnote.isEmpty {
                        Text(footnote)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                }

                Spacer(minLength: 0)

                trailing()
            }
            .padding(AppSpacing.lg)
            .cardSurface(cornerRadius: AppCornerRadius.large)
        }
        .buttonStyle(.plain)
        // `.contain`, not `.combine`: the trailing view can hold its own controls (the
        // price rules' toggle and delete button), and combining would swallow them.
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(isNew ? "New. " : "")\(title). \(subtitle)")
    }
}

extension ActivityRow where Trailing == EmptyView {
    /// A row with no trailing detail.
    init(
        systemName: String,
        iconColor: Color,
        title: String,
        subtitle: String,
        footnote: String? = nil,
        isNew: Bool = false,
        onTap: (() -> Void)? = nil
    ) {
        self.init(
            systemName: systemName, iconColor: iconColor, title: title,
            subtitle: subtitle, footnote: footnote, isNew: isNew, onTap: onTap,
            trailing: { EmptyView() }
        )
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.md) {
            ActivityRow(
                systemName: "person.badge.key.fill",
                iconColor: AppColors.alertOrange,
                title: "Insider activity in ACHR",
                subtitle: "Eric Lentell sold $631K of ACHR.",
                footnote: "1d ago",
                isNew: true
            )
            ActivityRow(
                systemName: "dollarsign.circle.fill",
                iconColor: AppColors.bullish,
                title: "Whales Bought",
                subtitle: "ORCL this week — totaling $4K – $60K.",
                trailing: {
                    VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                        Text("$4K – $60K")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.bullish)
                        Text("BOUGHT")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            )
            ActivityRow(
                systemName: "sparkles",
                iconColor: AppColors.primaryBlue,
                title: "CRM analysis is ready",
                subtitle: "The Quality Compounder — tap to read the full report.",
                footnote: "1w ago"
            )
        }
        .padding(AppSpacing.lg)
    }
    .background(AppColors.background)
}
