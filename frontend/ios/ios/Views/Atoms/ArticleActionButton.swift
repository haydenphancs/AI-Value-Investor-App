//
//  ArticleActionButton.swift
//  ios
//
//  Atom: Action button for article interactions (Listen, Share, etc.)
//

import SwiftUI

struct ArticleActionButton: View {
    let icon: String
    let label: String
    var isActive: Bool = false
    var style: ActionButtonStyle = .standard
    var onTap: (() -> Void)?

    enum ActionButtonStyle {
        case standard
        case primary
        case compact

        var backgroundColor: Color {
            switch self {
            case .standard: return AppColors.cardBackgroundLight
            case .primary: return AppColors.primaryBlue
            case .compact: return Color.clear
            }
        }

        var foregroundColor: Color {
            switch self {
            case .standard: return AppColors.textSecondary
            case .primary: return .white
            case .compact: return AppColors.textSecondary
            }
        }
    }

    var body: some View {
        Button(action: { onTap?() }) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(.system(size: style == .compact ? 16 : 14, weight: .medium))

                if style != .compact {
                    Text(label)
                        .font(AppTypography.caption)
                }
            }
            .foregroundColor(isActive ? AppColors.primaryBlue : style.foregroundColor)
            .padding(.horizontal, style == .compact ? AppSpacing.sm : AppSpacing.md)
            .padding(.vertical, style == .compact ? AppSpacing.sm : AppSpacing.sm)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(style.backgroundColor)
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        HStack(spacing: AppSpacing.md) {
            ArticleActionButton(icon: "headphones", label: "Listen")
            ArticleActionButton(icon: "square.and.arrow.up", label: "Share")
            ArticleActionButton(icon: "bookmark", label: "Save", isActive: true)
        }

        HStack(spacing: AppSpacing.md) {
            ArticleActionButton(icon: "iphone", label: "Mobile Post", style: .primary)
            ArticleActionButton(icon: "bolt.fill", label: "Instant", style: .primary)
        }

        HStack(spacing: AppSpacing.md) {
            ArticleActionButton(icon: "heart", label: "", style: .compact)
            ArticleActionButton(icon: "bubble.left", label: "", style: .compact)
            ArticleActionButton(icon: "arrow.2.squarepath", label: "", style: .compact)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
