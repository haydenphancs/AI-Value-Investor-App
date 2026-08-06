//
//  CardSurface.swift
//  ios
//
//  The one way to draw a card.
//
//  WHY THIS EXISTS
//  ---------------
//  A #FFFFFF card on a #F4F5F8 page is 1.09:1. That is not a bug in the surface
//  values — no design system separates page from card by luminance (Apple 1.116,
//  Shopify Polaris 1.129, IBM Carbon 1.100, Material 1.05–1.09 all sit in the
//  same band). They separate them with a BORDER, and nine of ten investing
//  products do the same in light mode, reserving shadows for overlays.
//
//  This app had no border token at all, so in light mode cards had no edge and
//  read as text floating on a near-white field. That is the single most visible
//  defect in the light theme.
//
//  ELEVATION IS NOT SYMMETRIC. In light a card is lifted by a hairline plus a
//  faint shadow. In dark it gets NEITHER — `cardEdge` and `shadowCard` are both
//  transparent there — and is lifted purely by being a LIGHTER SURFACE than what
//  is behind it. A shadow does almost nothing in dark anyway (black at 4–15% over
//  #171B26 measures ~1.01:1), and a hairline on every card read as noise. All the
//  tokens are adaptive, so one call site gets the right mechanism in each mode
//  with no `colorScheme` branching.
//
//  ⚠️ THE CONSEQUENCE, AND THE ONE WAY TO GET THIS WRONG:
//  surface is the ONLY separator in dark, so a card nested inside another card
//  MUST step up — `.cardFill(AppColors.cardBackgroundNested)` — or it shares its
//  parent's fill, measures 1.00:1 against it, and is simply not there. That is not
//  hypothetical: Recent Activities is a `cardFill()` card whose rows were also
//  `cardFill()`, and in dark up to 73 of them merged into one unbroken slab.
//  An OUTER card on the page needs nothing; fill alone is the Home screen's look.
//

import SwiftUI

enum CardElevation {
    /// Resting card in a scrolling list. Border does the work; the shadow only
    /// softens the edge.
    case flat
    /// Lifted above its siblings: floating bars, selected states, CTAs.
    case raised
    /// Over a scrim: sheets, popovers, menus.
    case overlay

    var shadow: Shadow? {
        switch self {
        case .flat: return AppShadows.card
        case .raised: return AppShadows.raised
        case .overlay: return AppShadows.overlay
        }
    }

    /// Overlays sit on a scrim, where a hairline reads as noise.
    ///
    /// `cardEdge`, not `border`: present in light, absent in dark. `.raised` moves
    /// with `.flat` even though it has no production call sites today — leaving it on
    /// `border` would just mean the next person to reach for it silently reintroduces
    /// a dark border. Anything that genuinely wants one in dark should name
    /// `AppColors.border` at the site, which documents the exception (see
    /// AudioStatusIsland).
    var borderColor: Color? {
        switch self {
        case .flat, .raised: return AppColors.cardEdge
        case .overlay: return nil
        }
    }
}

extension View {
    /// Fill + hairline + shadow for a card surface.
    ///
    /// Prefer this over an inline `.background(RoundedRectangle().fill(...))` —
    /// that form has no edge, which is exactly how light mode broke.
    ///
    /// Uses `strokeBorder` rather than `stroke`: `stroke` straddles the path and
    /// spills half the line outside the shape, which double-darkens where cards
    /// abut and clips under `.clipShape`.
    func cardSurface(
        _ fill: Color = AppColors.cardBackground,
        cornerRadius: CGFloat = AppCornerRadius.large,
        elevation: CardElevation = .flat
    ) -> some View {
        self.background(
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .fill(fill)
        )
        .overlay(
            Group {
                if let border = elevation.borderColor {
                    RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                        .strokeBorder(border, lineWidth: 1)
                }
            }
        )
        .modifier(OptionalShadow(shadow: elevation.shadow))
    }

    /// Edge only — for a view that ALREADY paints its own fill and clips itself, and
    /// just needs the card hairline on top.
    ///
    /// Defaults to `cardEdge`, so it obeys the same rule as `cardSurface`/`cardFill`:
    /// an edge in light, nothing in dark. (This briefly took `AppColors.border` and had
    /// no callers, which made the one API named "card border" a trap that would hand
    /// the next caller a dark hairline. The default is what fixes that, not deleting
    /// the function — Home's cards are exactly the case it exists for: they build
    /// `.background(fill)` + `.clipShape(...)` themselves because the clip is
    /// load-bearing for their sparklines, so they cannot switch to `cardSurface`.)
    func cardBorder(cornerRadius: CGFloat = AppCornerRadius.large,
                    color: Color = AppColors.cardEdge) -> some View {
        self.overlay(
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .strokeBorder(color, lineWidth: 1)
        )
    }
}

extension InsettableShape {
    /// Card fill + hairline, on the SHAPE rather than the view.
    ///
    /// The app's dominant idiom is a shape built inside `.background(...)`:
    ///
    ///     .background(
    ///         RoundedRectangle(cornerRadius: AppCornerRadius.large)
    ///             .fill(AppColors.cardBackground)   // <- no edge
    ///     )
    ///
    /// Swapping `.fill(x)` for `.cardFill()` adds the border in place, reusing
    /// the shape's own geometry — so the hairline can never drift from the
    /// corner radius it is tracing. That is the whole reason this is on
    /// `InsettableShape` and not a view modifier taking a radius parameter: a
    /// radius passed by hand is a radius that eventually disagrees.
    ///
    /// `strokeBorder` (not `stroke`) insets the line so it stays fully inside
    /// the shape and doesn't double-darken where cards abut.
    /// ⚠️ Nested in another card? Pass `AppColors.cardBackgroundNested` as the fill —
    /// in dark the border is transparent, so an unchanged fill means no card at all.
    func cardFill(_ fill: Color = AppColors.cardBackground,
                  border: Color = AppColors.cardEdge,
                  lineWidth: CGFloat = 1) -> some View {
        self.fill(fill).overlay(self.strokeBorder(border, lineWidth: lineWidth))
    }
}

private struct OptionalShadow: ViewModifier {
    let shadow: Shadow?

    func body(content: Content) -> some View {
        if let shadow {
            content.shadow(color: shadow.color, radius: shadow.radius, x: shadow.x, y: shadow.y)
        } else {
            content
        }
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.lg) {
            ForEach([("flat", CardElevation.flat),
                     ("raised", .raised),
                     ("overlay", .overlay)], id: \.0) { name, elevation in
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(name.capitalized)
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                    Text("A card must have a visible edge against the page.")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(AppSpacing.lg)
                .cardSurface(elevation: elevation)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
