//
//  RatingBadge.swift
//  ios
//
//  Atom: Displays rating score with color coding
//

import SwiftUI

struct RatingBadge: View {
    let rating: Double
    let maxRating: Double

    init(rating: Double, maxRating: Double = 5.0) {
        self.rating = rating
        self.maxRating = maxRating
    }

    private var backgroundColor: Color {
        // FILL tokens, not the text-safe ones. This is a saturated chip carrying
        // `textOnAccent` ink, and the text-safe tokens lighten in dark: white on
        // `bullish` #22C55E is 2.28:1, on `bearish` #EF4444 3.76:1, on `primaryBlue`
        // #60A5FA 2.24:1 — i.e. this badge was below AA in dark at every rating.
        //
        // For the 0–100 report score, defer to QualityBand (the SINGLE source of
        // truth for score→band color, cutoffs 80/65/48/33) so this carousel chip
        // can never disagree with the report gauge. The ratio cutoffs below are
        // only for the 5-star rating path (maxRating <= 5).
        if maxRating >= 100 {
            return QualityBand.forScore(Int(rating.rounded())).fillColor
        }
        let ratio = rating / maxRating
        if ratio >= 0.8 {
            return AppColors.gainFill
        } else if ratio >= 0.6 {
            return AppColors.primaryFill
        } else if ratio >= 0.4 {
            return AppColors.cautionFill
        } else {
            return AppColors.lossFill
        }
    }

    /// Ink for `backgroundColor`, mirroring its branches EXACTLY. `gainFill`/`lossFill`
    /// are ADAPTIVE (bright in dark) and need near-black `textOnFill`; `primaryFill` and
    /// `cautionFill` are frozen and need white. One ink cannot serve both — near-black on
    /// frozen `primaryFill` is 3.35:1.
    private var foregroundInk: Color {
        if maxRating >= 100 {
            return QualityBand.forScore(Int(rating.rounded())).fillInk
        }
        let ratio = rating / maxRating
        if ratio >= 0.8 {
            return AppColors.textOnFill      // gainFill
        } else if ratio >= 0.6 {
            return AppColors.textOnAccent    // primaryFill (frozen)
        } else if ratio >= 0.4 {
            return AppColors.textOnAccent    // cautionFill (frozen)
        } else {
            return AppColors.textOnFill      // lossFill
        }
    }

    private var formattedText: String {
        if maxRating >= 100 {
            return String(format: "%.0f", rating)
        }
        return String(format: "%.1f/%.0f", rating, maxRating)
    }

    var body: some View {
        Text(formattedText)
            .font(AppTypography.captionEmphasis)
            .foregroundColor(foregroundInk)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(backgroundColor)
            .cornerRadius(AppCornerRadius.small)
    }
}

#Preview {
    HStack(spacing: 10) {
        RatingBadge(rating: 4.6)
        RatingBadge(rating: 4.2)
        RatingBadge(rating: 3.3)
        RatingBadge(rating: 2.0)
    }
    .padding()
    .background(AppColors.background)
}
