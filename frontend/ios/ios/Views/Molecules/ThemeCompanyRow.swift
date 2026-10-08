//
//  ThemeCompanyRow.swift
//  ios
//
//  Molecule: one company row in the theme detail's "Companies" list — a logo +
//  company name + ticker on the left, current price + green/red daily change on
//  the right. Tappable → the stock's detail. Flat row (the parent list wraps it
//  in a card with hairline dividers), matching the constituents-list design.
//
//  LOCKED (`isLocked`, 2026-10-04): a stand-in for a company the caller's plan withholds —
//  Free sees a theme's first companies (backend `entitlements.THEME_FREE_COMPANY_LIMIT`).
//  It is the SAME layout as a real row, so a blurred row keeps a real row's height and the
//  list keeps its real length, drawn over `ThemeConstituent.lockedPlaceholder` words and
//  blurred, with a lock. The server never sends a withheld company to a locked caller, so
//  the blur hides placeholder text, not data — the blur is presentation, the gate is
//  server-side (`theme_detail_redaction.py`). A locked row never loads a logo: its ticker is
//  made up, and a fetched mark could be a real company's under the blur.
//

import SwiftUI

struct ThemeCompanyRow: View {
    let company: ThemeConstituent
    var onTap: (() -> Void)? = nil
    /// Draw this row as a blurred, locked stand-in (see the file header).
    var isLocked: Bool = false

    /// Enough to make 15–17pt text unreadable without smearing the row past its padding.
    static let lockBlurRadius: CGFloat = 6

    var body: some View {
        if isLocked {
            Button { onTap?() } label: {
                lockedContent
                    .padding(.vertical, AppSpacing.md)
                    .padding(.horizontal, AppSpacing.md)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Hidden company, locked")
            .accessibilityHint("Shows upgrade options")
        } else {
            Button { onTap?() } label: {
                content
                    .padding(.vertical, AppSpacing.md)
                    .padding(.horizontal, AppSpacing.md)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    /// The real row's content, blurred, with a lock where the price sits.
    private var lockedContent: some View {
        content
            .blur(radius: Self.lockBlurRadius)
            .overlay(alignment: .trailing) {
                // A TEXT-role token (the one LockedTickersChip uses): this glyph has to clear
                // 4.5:1 in both appearances. A *Graphic token would fail the launch audit.
                Image(systemName: "lock.fill")
                    .font(AppTypography.iconXS)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.primaryBlue)
            }
            // Placeholder words must not be read out; the Button carries the one label.
            .accessibilityHidden(true)
    }

    private var content: some View {
        HStack(spacing: AppSpacing.md) {
            logo

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(company.name)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                    if company.isNew {
                        // Joined (or came back) in this month's review.
                        Text("New")
                            .font(AppTypography.captionEmphasis)
                            .foregroundColor(AppColors.primaryBlue)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            // 0.08: text on its own tint — 0.14 measured 4.25:1 (light).
                            .background(Capsule().fill(AppColors.primaryBlue.opacity(0.08)))
                            .fixedSize()
                    }
                }
                HStack(spacing: 4) {
                    Text(company.ticker)
                    if let role = company.roleLabel {
                        Text("·")
                        Text(role)
                    }
                }
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineLimit(1)
            }

            Spacer(minLength: AppSpacing.sm)

            VStack(alignment: .trailing, spacing: 2) {
                if !company.priceText.isEmpty {
                    Text(company.priceText)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                }
                if !company.changeText.isEmpty {
                    Text(company.changeText)
                        .font(AppTypography.caption)
                        .foregroundColor(changeInk)
                }
            }
        }
    }

    /// Green/red for a real move. A locked stand-in has no move, so its smear stays neutral
    /// rather than hinting that the hidden companies are up.
    private var changeInk: Color {
        if isLocked { return AppColors.textSecondary }
        return company.isPositive ? AppColors.bullish : AppColors.bearish
    }

    @ViewBuilder
    private var logo: some View {
        if isLocked {
            // Never `CompanyLogoView` here — see the file header.
            RoundedRectangle(cornerRadius: 10)
                .fill(AppColors.mediaSurface)
                .frame(width: 40, height: 40)
        } else {
            CompanyLogoView(ticker: company.ticker, size: 40)
        }
    }
}

#Preview {
    VStack(spacing: 0) {
        ThemeCompanyRow(company: ThemeConstituent(
            ticker: "NVDA", name: "NVIDIA Corp.", priceText: "$1,204.20",
            changeText: "+2.10%", isPositive: true, marketCapText: "3.0T Cap"))
        ThemeCompanyRow(company: ThemeConstituent(
            ticker: "AMD", name: "Advanced Micro Devices", priceText: "$168.40",
            changeText: "-1.80%", isPositive: false, marketCapText: "270.0B Cap"))
        // Free: the same height as the rows above, blurred, with a lock.
        ThemeCompanyRow(company: .lockedPlaceholder(0), isLocked: true)
        ThemeCompanyRow(company: .lockedPlaceholder(1), isLocked: true)
    }
    .background(AppColors.cardBackground)
    .padding()
    .background(AppColors.background)
}
