//
//  ArticleTagPill.swift
//  ios
//
//  Atom: Tag pill for article labels (MUST READ, FEATURED, etc.)
//

import SwiftUI

struct ArticleTagPill: View {
    let text: String
    var style: TagPillStyle = .standard

    enum TagPillStyle {
        case standard
        case featured
        case warning
        case success

        /// The tinted styles are OPAQUE: caution/loss/gain on their own 20% tint measure
        /// 3.92/3.98/4.08:1 in light even on a white card (test_ios_theme_parity §6c). Their
        /// hue stays in the border and the ink.
        var backgroundColor: Color {
            switch self {
            case .standard: return Color.black.opacity(0.4)
            case .featured, .warning, .success: return AppColors.cardBackgroundLight
            }
        }

        var borderColor: Color {
            switch self {
            case .standard: return Color.white.opacity(0.3)
            case .featured: return AppColors.caution.opacity(0.5)
            case .warning: return AppColors.loss.opacity(0.5)
            case .success: return AppColors.gain.opacity(0.5)
            }
        }

        var textColor: Color {
            switch self {
            case .standard: return .white
            case .featured: return AppColors.caution
            case .warning: return AppColors.loss
            case .success: return AppColors.gain
            }
        }
    }

    var body: some View {
        Text(text.uppercased())
            .font(AppTypography.captionEmphasis)
            .foregroundColor(style.textColor)
            .tracking(0.8)
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.xs)
            .background(
                Capsule()
                    .fill(style.backgroundColor)
                    .overlay(
                        Capsule()
                            .strokeBorder(style.borderColor, lineWidth: 1)
                    )
            )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ArticleTagPill(text: "Must Read")
        ArticleTagPill(text: "Featured", style: .featured)
        ArticleTagPill(text: "Warning", style: .warning)
        ArticleTagPill(text: "Success", style: .success)
    }
    .padding()
    .background(AppColors.cardBackground)
}
