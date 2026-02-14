//
//  CryptoDetailSnapshotsSection.swift
//  ios
//
//  Organism: Snapshots section for Crypto Detail with expandable cards
//  Categories: Origin and Technology, Tokenomics, Next Big Moves, Risks
//  No ranking/stars - content-driven snapshots to be populated later
//

import SwiftUI

struct CryptoDetailSnapshotsSection: View {
    let snapshots: [CryptoSnapshotItem]
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
                    CryptoSnapshotCard(snapshot: snapshot)
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
            CryptoSnapshotsInfoSheet()
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Crypto Snapshot Card (no ranking, expandable)
struct CryptoSnapshotCard: View {
    let snapshot: CryptoSnapshotItem
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

// MARK: - Crypto Snapshots Info Sheet
struct CryptoSnapshotsInfoSheet: View {
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

                        Text("Snapshots provide a quick, comprehensive view of a cryptocurrency's key dimensions. Each snapshot covers a different aspect of the asset, giving you an instant understanding of its fundamentals and risks.")
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
                                icon: "cpu.fill",
                                title: "Origin and Technology",
                                description: "The history, founding team, consensus mechanism, and underlying technology of the cryptocurrency."
                            )

                            SnapshotBulletPoint(
                                icon: "chart.pie.fill",
                                title: "Tokenomics",
                                description: "Supply mechanics, distribution, inflation/deflation model, staking rewards, and token utility."
                            )

                            SnapshotBulletPoint(
                                icon: "arrow.up.forward.circle.fill",
                                title: "Next Big Moves",
                                description: "Upcoming catalysts, protocol upgrades, partnerships, and ecosystem developments."
                            )

                            SnapshotBulletPoint(
                                icon: "exclamationmark.triangle.fill",
                                title: "Risks",
                                description: "Regulatory concerns, technical vulnerabilities, competition, and market risks to consider."
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
                                tip: "Don't focus on a single snapshot. A crypto with strong technology but poor tokenomics may still be risky."
                            )

                            ProTipCard(
                                icon: "calendar",
                                tip: "Check 'Next Big Moves' regularly - upcoming events can significantly impact price and adoption."
                            )

                            ProTipCard(
                                icon: "shield.fill",
                                tip: "Always review the 'Risks' section before investing. Understanding downside scenarios is essential for crypto investing."
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
        CryptoDetailSnapshotsSection(snapshots: CryptoSnapshotItem.sampleETH)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}

#Preview("Info Sheet") {
    CryptoSnapshotsInfoSheet()
}
