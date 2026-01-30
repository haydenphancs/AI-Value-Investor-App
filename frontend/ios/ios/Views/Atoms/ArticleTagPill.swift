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

        var backgroundColor: Color {
            switch self {
            case .standard: return Color.black.opacity(0.4)
            case .featured: return Color(hex: "F59E0B").opacity(0.2)
            case .warning: return Color(hex: "EF4444").opacity(0.2)
            case .success: return Color(hex: "22C55E").opacity(0.2)
            }
        }

        var borderColor: Color {
            switch self {
            case .standard: return Color.white.opacity(0.3)
            case .featured: return Color(hex: "F59E0B").opacity(0.5)
            case .warning: return Color(hex: "EF4444").opacity(0.5)
            case .success: return Color(hex: "22C55E").opacity(0.5)
            }
        }

        var textColor: Color {
            switch self {
            case .standard: return .white
            case .featured: return Color(hex: "F59E0B")
            case .warning: return Color(hex: "EF4444")
            case .success: return Color(hex: "22C55E")
            }
        }
    }

    var body: some View {
        Text(text.uppercased())
            .font(AppTypography.captionBold)
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
    .preferredColorScheme(.dark)
}
