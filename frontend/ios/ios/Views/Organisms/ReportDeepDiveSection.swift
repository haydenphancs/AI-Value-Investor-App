//
//  ReportDeepDiveSection.swift
//  ios
//
//  Organism: Collapsible deep dive module container
//

import SwiftUI

struct ReportDeepDiveSection<Content: View>: View {
    let module: DeepDiveModule
    /// Suppresses the bottom hairline divider for the LAST module so the parent's
    /// rounded card ends cleanly at its curved bottom corners.
    var isLast: Bool = false
    /// Fired after this module has closed VIA THE BOTTOM "^" ONLY, so the parent can put
    /// the module's own header back at the top of the viewport.
    ///
    /// ⚠️ Deliberately NOT fired from the header chevron, and that is the whole design.
    /// Collapsing removes height BELOW the header, so the header itself never moves — a
    /// user who taps it is looking straight at it and nothing has gone anywhere. Scrolling
    /// on that path would CREATE the jump this fixes, in mirror image: tap a header sitting
    /// at y≈600 while reading the executive summary and the page yanks down 600pt.
    ///
    /// The bottom affordance is the opposite case by construction — reaching it means the
    /// header is far above the fold, so the collapse pulls the content the reader was
    /// looking at up and out of view. That is the reported bug.
    var onCollapse: (() -> Void)? = nil
    @ViewBuilder let content: () -> Content

    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(spacing: 0) {
            // Header row (tappable)
            Button(action: {
                isExpanded.toggle()
            }) {
                HStack(spacing: AppSpacing.md) {
                    Image(systemName: module.iconName)
                        .font(.system(size: module.iconName == "dollarsign.circle" ? 22 : 16))
                        .foregroundColor(AppColors.primaryBlue)
                        .frame(width: module.iconName == "dollarsign.circle" ? 36 : 28, alignment: module.iconName == "dollarsign.circle" ? .leading : .center)

                    Text(module.title)
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                        .offset(x: module.iconName == "dollarsign.circle" ? -8 : 0)

                    Spacer()

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppTypography.iconXS).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.lg)
                // AFTER the padding, and not optional. A Button is hit-tested on
                // the shape its label DRAWS: `Spacer()` draws nothing and padding
                // is empty, so without this only the icon, the title glyphs and
                // the chevron respond — reported from TestFlight as "you can only
                // hit the title or the down icon". Applied before the padding it
                // would measure the unpadded frame and leave the margins dead.
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            if isExpanded {
                content()
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.md)

                // Second collapse affordance: a "^" at the bottom-right that
                // closes the card, mirroring the header chevron. These modules
                // can be long, so this lets the user dismiss one without
                // scrolling back up to the header.
                Button(action: {
                    isExpanded = false
                    onCollapse?()
                }) {
                    HStack(spacing: 0) {
                        Spacer()
                        Image(systemName: "chevron.up")
                            .font(AppTypography.iconXS)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.textMuted)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.lg)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
            }

            if !isLast {
                Divider()
                    .overlay(AppColors.textMuted.opacity(0.15))
            }
        }
        .background(AppColors.cardBackground)
    }
}

#Preview {
    VStack(spacing: 0) {
        ReportDeepDiveSection(
            module: DeepDiveModule(
                title: "Fundamentals & Growth",
                iconName: "chart.bar.fill",
                type: .fundamentalsGrowth
            )
        ) {
            Text("Content goes here")
                .foregroundColor(AppColors.textSecondary)
        }
        ReportDeepDiveSection(
            module: DeepDiveModule(
                title: "Recent Price Movement",
                iconName: "chart.xyaxis.line",
                type: .recentPriceMovement
            )
        ) {
            EmptyView()
        }
    }
    .background(AppColors.background)
}
