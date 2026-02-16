//
//  ETFDetailSnapshotsSection.swift
//  ios
//
//  Organism: Snapshots section for ETF Detail with expandable cards
//  Categories: Identity & Rating, Net Yield, Holdings & Risk
//  No ranking - content-driven snapshots to be populated later
//

import SwiftUI

struct ETFDetailSnapshotsSection: View {
    let snapshots: [ETFSnapshotItem]
    var onDeepResearchTap: (() -> Void)?
    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title with info button
            HStack {
                Text("Snapshots")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    showInfoSheet = true
                }) {
                    Text("What's Snapshots?")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Snapshot cards
            VStack(spacing: 0) {
                ForEach(snapshots) { snapshot in
                    ETFSnapshotCard(snapshot: snapshot)
                }
            }

            // AI Deep Research button
            AIDeepResearchButton {
                onDeepResearchTap?()
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            ETFSnapshotsInfoSheet()
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - ETF Snapshot Card (no ranking, expandable)
struct ETFSnapshotCard: View {
    let snapshot: ETFSnapshotItem
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header row
            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    // Category icon
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(snapshot.category.iconColor.opacity(0.15))
                            .frame(width: 36, height: 36)

                        Image(systemName: snapshot.category.iconName)
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(snapshot.category.iconColor)
                    }

                    // Category name
                    Text(snapshot.category.rawValue)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    // Expand/collapse chevron
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.vertical, AppSpacing.md)
            }
            .buttonStyle(PlainButtonStyle())

            // Content area (when expanded)
            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    if snapshot.paragraphs.isEmpty {
                        // Placeholder for future content
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "text.alignleft")
                                .font(.system(size: 14))
                                .foregroundColor(AppColors.textMuted)

                            Text("Content coming soon")
                                .font(AppTypography.footnote)
                                .foregroundColor(AppColors.textMuted)
                                .italic()
                        }
                        .padding(.vertical, AppSpacing.sm)
                    } else {
                        ForEach(Array(snapshot.paragraphs.enumerated()), id: \.offset) { _, paragraph in
                            Text(paragraph)
                                .font(AppTypography.footnote)
                                .foregroundColor(AppColors.textSecondary)
                                .lineSpacing(4)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .padding(.bottom, AppSpacing.md)
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

// MARK: - ETF Snapshots Info Sheet
struct ETFSnapshotsInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // What are Snapshots?
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "camera.viewfinder")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("What are Snapshots?")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Snapshots provide a quick, comprehensive view of an ETF's key dimensions. Each snapshot covers a different aspect of the fund, giving you an instant understanding of its structure, yield, and risk profile.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    // Snapshot Categories
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "square.grid.2x2.fill")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("Snapshot Categories")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            SnapshotBulletPoint(
                                icon: "shield.checkered",
                                title: "Identity & Rating",
                                description: "The ETF's issuer, tracking index, fund structure, and analyst or agency ratings."
                            )

                            SnapshotBulletPoint(
                                icon: "percent",
                                title: "Net Yield",
                                description: "Dividend yield, distribution frequency, expense ratio impact, and net income returned to investors."
                            )

                            SnapshotBulletPoint(
                                icon: "chart.bar.doc.horizontal.fill",
                                title: "Holdings & Risk",
                                description: "Top holdings concentration, sector allocation, portfolio diversification, and key risk metrics."
                            )
                        }
                    }

                    // Pro Tips
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("Pro Tips")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            ProTipCard(
                                icon: "arrow.triangle.2.circlepath",
                                tip: "Compare expense ratios across similar ETFs. Even small differences compound significantly over long holding periods."
                            )

                            ProTipCard(
                                icon: "chart.pie.fill",
                                tip: "Check top holdings concentration. An ETF with 40% in its top 10 holdings behaves differently than one evenly spread across 500."
                            )

                            ProTipCard(
                                icon: "shield.fill",
                                tip: "Always review Holdings & Risk before investing. Understand what you actually own inside the fund."
                            )
                        }
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("About Snapshots")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

#Preview {
    ScrollView {
        ETFDetailSnapshotsSection(snapshots: ETFSnapshotItem.sampleSPY)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}

#Preview("Info Sheet") {
    ETFSnapshotsInfoSheet()
}
