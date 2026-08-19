//
//  ReportsListSection.swift
//  ios
//
//  Organism: Grouped, searchable, multi-selectable list of analysis reports.
//  Header row hosts Sort + Search + Edit/Done; the list is grouped into time
//  bands (Recent / Last Month / Older) with the Sort option ordering cards
//  within each band.
//

import SwiftUI

struct ReportsListSection: View {
    let sections: [ReportSectionGroup]
    @Binding var sortOption: ReportSortOption
    @Binding var searchText: String
    @Binding var isSearchActive: Bool
    @Binding var isSelecting: Bool
    let selectedIds: Set<String>
    let personaTags: [AnalysisPersona]
    let selectedPersonaKeys: Set<String>
    var onReportTapped: ((AnalysisReport) -> Void)?
    var onRetryTapped: ((AnalysisReport) -> Void)?
    var onToggleSelect: ((AnalysisReport) -> Void)?
    /// Enter selection mode (when idle) or exit + clear (when selecting).
    var onToggleSelectingMode: (() -> Void)?
    var onTogglePersonaTag: ((AnalysisPersona) -> Void)?
    /// Tapped from the first-run zero-state. When nil the CTA is hidden.
    var onGenerateFirst: (() -> Void)?
    /// True when the list is empty because the user is signed out, not because they have no
    /// reports. Different cause, different copy, different action — "Generate your first
    /// analysis" is a dead end for someone who needs an account first.
    var requiresSignIn: Bool = false
    /// Tapped from the signed-out state. When nil the CTA is hidden.
    var onSignIn: (() -> Void)?
    /// A credential is stored but not armed yet — the session is being restored. Takes
    /// precedence over `requiresSignIn`: this user is NOT signed out, so offering them a Sign In
    /// button is both wrong and inert (`AppState.requestSignIn` deliberately declines to prompt
    /// while a restore is pending, and shows "Reconnecting your account…" instead).
    var isReconnecting: Bool = false

    @State private var showSortMenu = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            headerRow

            if isSearchActive {
                searchReveal
            }

            if sections.isEmpty && isReconnecting {
                // Checked BEFORE `requiresSignIn`: during a restore we cannot prove the session
                // yet, but we hold a credential, so "sign in" would be a false statement.
                reconnectingState
            } else if sections.isEmpty && requiresSignIn {
                signedOutState
            } else if sections.isEmpty && isFiltered {
                // A FILTER matched nothing — the user has reports, just not these.
                // `isFiltered` covers the persona chips as well as the search box:
                // previously only `searchText` was checked, so tapping "Disruption" with
                // no Cathie Wood analyses fell through to the FIRST-RUN state below and
                // told someone with forty paid reports "No analyses yet", under a button
                // offering to generate their first one.
                emptyFilterState
            } else if sections.isEmpty {
                // First run: no reports AND no active search. Without this branch a
                // brand-new user's first visit to the paid feature was a completely
                // blank screen under a sort/filter bar.
                emptyFirstRunState
            } else {
                list
            }
        }
        // Custom sort dropdown floats above the list, anchored under the Sort
        // button. Overlay sits BEFORE the horizontal padding so its leading
        // edge lines up with the Sort capsule's leading edge.
        .overlay(alignment: .topLeading) {
            if showSortMenu {
                sortDropdown
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Header

    private var headerRow: some View {
        HStack(spacing: AppSpacing.sm) {
            // Sort — opens a custom dropdown (see sortDropdown). Not a system
            // Menu (can't shrink its ~280pt min width) and not a .popover
            // (has a beak that isn't the iOS-standard look here).
            Button {
                showSortMenu = true
            } label: {
                sortCapsule
            }
            .buttonStyle(PlainButtonStyle())

            // Persona filter tags — horizontally scrollable, fills the middle
            // between Sort (left) and Search/Edit (right).
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.xs) {
                    ForEach(personaTags) { persona in
                        personaTagChip(persona)
                    }
                }
                .padding(.horizontal, 2)
            }

            // Search toggle
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isSearchActive.toggle()
                    if !isSearchActive { searchText = "" }
                }
            } label: {
                iconCapsule(systemName: "magnifyingglass", active: isSearchActive)
            }
            .buttonStyle(PlainButtonStyle())

            // Edit / Done toggle
            Button {
                onToggleSelectingMode?()
            } label: {
                if isSelecting {
                    Text("Done")
                        .font(AppTypography.caption).fontWeight(.semibold)
                        .foregroundColor(AppColors.primaryBlue)
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, AppSpacing.xs)
                        .background(Capsule().fill(AppColors.cardBackgroundLight))
                } else {
                    iconCapsule(systemName: "pencil", active: false)
                }
            }
            .buttonStyle(PlainButtonStyle())
        }
    }

    private var sortCapsule: some View {
        HStack(spacing: AppSpacing.xxs) {
            Text("Sort")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)

            Image(systemName: "arrow.up.arrow.down")
                .font(AppTypography.iconTiny).fontWeight(.medium)
                .foregroundColor(AppColors.textSecondary)
        }
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.xs)
        .background(Capsule().fill(AppColors.cardBackgroundLight))
    }

    // Custom sort dropdown (no popover beak), anchored under the Sort button.
    // iOS-style: a "Sort By" section header on top, then the options with a
    // right-aligned checkmark on the active one (kept right, not the system
    // menu's left). Backdrop catches outside taps to dismiss. Width is fixed;
    // height grows by the header row.
    private var sortDropdown: some View {
        ZStack(alignment: .topLeading) {
            Color.clear
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .contentShape(Rectangle())
                .onTapGesture { showSortMenu = false }

            VStack(alignment: .leading, spacing: 0) {
                // Section header — mirrors the iOS "Sort By" menu caption.
                Text("Sort By")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .padding(.leading, AppSpacing.lg)
                    .padding(.trailing, AppSpacing.md)
                    .padding(.top, AppSpacing.sm + 2)
                    .padding(.bottom, AppSpacing.xs)

                ForEach(ReportSortOption.allCases, id: \.rawValue) { option in
                    Button {
                        sortOption = option
                        showSortMenu = false
                    } label: {
                        HStack(spacing: AppSpacing.md) {
                            Text(option.rawValue)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)
                            Spacer(minLength: AppSpacing.md)
                            Image(systemName: "checkmark")
                                .font(AppTypography.iconSmall).fontWeight(.semibold)
                                .foregroundColor(AppColors.primaryBlue)
                                .opacity(sortOption == option ? 1 : 0)   // reserve space → rows stay aligned
                        }
                        .padding(.leading, AppSpacing.lg)    // more space at left
                        .padding(.trailing, AppSpacing.md)   // less space at right
                        .padding(.vertical, AppSpacing.sm + 2)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }
            .frame(width: 178)
            // Native iOS 26 Liquid Glass — the material supplies the translucent
            // frost, the rounded shape, the adaptive edge highlight, AND the
            // floating-layer shadow, so there's no manual fill / stroke / shadow
            // (an opaque fill would defeat the translucency; a manual shadow
            // would double up). Honors Reduce Transparency automatically.
            .glassPanel(cornerRadius: AppCornerRadius.medium)
            .offset(y: 34)   // drop just below the Sort capsule
        }
    }

    private func iconCapsule(systemName: String, active: Bool) -> some View {
        Image(systemName: systemName)
            .font(AppTypography.iconSmall).fontWeight(.medium)
            .foregroundColor(active ? AppColors.primaryBlue : AppColors.textSecondary)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(Capsule().fill(AppColors.cardBackgroundLight))
    }

    // Persona filter chip — tinted with the persona's accent color. Selected =
    // solid accent + white text; unselected = faint accent tint + accent text.
    private func personaTagChip(_ persona: AnalysisPersona) -> some View {
        let isOn = selectedPersonaKeys.contains(persona.key)
        return Button {
            onTogglePersonaTag?(persona)
        } label: {
            Text(persona.shortName)
                .font(AppTypography.caption).fontWeight(.semibold)
                .foregroundColor(isOn ? AppColors.textOnAccent : persona.accentColor)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    // Selected = `accentFill` (clamped so `textOnAccent` clears 4.5 on it);
                    // unselected = the text-safe accent at 0.15, which carries no ink.
                    // Both halves of the selected pair must move together.
                    Capsule().fill(isOn ? persona.accentFill
                                        : persona.accentColor.opacity(0.15))
                )
                .fixedSize(horizontal: true, vertical: false)   // keep natural width in the scroll
        }
        .buttonStyle(PlainButtonStyle())
    }

    private var searchReveal: some View {
        HStack(spacing: AppSpacing.sm) {
            SearchBar(text: $searchText,
                      placeholder: "Search ticker, company, or persona",
                      autoFocus: true)
            Button("Cancel") {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isSearchActive = false
                    searchText = ""
                }
            }
            .font(AppTypography.caption)
            .foregroundColor(AppColors.primaryBlue)
        }
        .transition(.move(edge: .top).combined(with: .opacity))
    }

    // MARK: - List

    private var list: some View {
        LazyVStack(alignment: .leading, spacing: AppSpacing.md) {
            ForEach(sections) { group in
                ReportTimeSectionHeader(section: group.section)
                    .padding(.top, AppSpacing.xs)

                ForEach(group.reports) { report in
                    SelectableReportRow(
                        report: report,
                        isSelecting: isSelecting,
                        isSelected: report.backendId.map { selectedIds.contains($0) } ?? false,
                        onTap: { onReportTapped?(report) },
                        onRetry: { onRetryTapped?(report) },
                        onToggleSelect: { onToggleSelect?(report) }
                    )
                }
            }
        }
    }

    private var emptyFirstRunState: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 40))
                .foregroundColor(AppColors.textMuted)

            Text("No analyses yet")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text("Pick a ticker and an analyst on the Research tab, and your report will show up here.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)

            if let onGenerateFirst {
                Button(action: onGenerateFirst) {
                    Text("Generate your first analysis")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textOnAccent)
                        .padding(.horizontal, AppSpacing.xl)
                        .padding(.vertical, AppSpacing.md)
                        .background(AppColors.primaryFill)
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(PlainButtonStyle())
                .padding(.top, AppSpacing.xs)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
    }

    /// A stored credential that has not been validated yet. This user IS signed in as far as
    /// they are concerned, so the one thing this must not do is ask them to sign in — that copy
    /// shipped next to their own loaded avatar. It also offers no button on purpose: the session
    /// heals itself (launch / foreground / network-restored / backoff), and `requestSignIn`
    /// declines to prompt in this window, so any CTA here would be inert.
    private var reconnectingState: some View {
        VStack(spacing: AppSpacing.md) {
            ProgressView()
                .controlSize(.large)

            Text("Reconnecting…")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text("Getting your analyses. This usually takes a moment.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Reconnecting. Getting your analyses.")
    }

    /// Reports live on an account, so a signed-out user genuinely has none to show. Saying
    /// "No analyses yet" here would be a lie by omission — their reports may exist, just not
    /// for this device.
    private var signedOutState: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "person.crop.circle.badge.checkmark")
                .font(.system(size: 40))
                .foregroundColor(AppColors.textMuted)

            Text("Sign in to see your analyses")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text("Your reports are saved to your account so they follow you across devices.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)

            if let onSignIn {
                Button(action: onSignIn) {
                    Text("Sign In")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textOnAccent)
                        .padding(.horizontal, AppSpacing.xl)
                        .padding(.vertical, AppSpacing.md)
                        .background(AppColors.primaryFill)
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(PlainButtonStyle())
                .padding(.top, AppSpacing.xs)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
    }

    /// True when the list is empty because of something the USER applied, not because
    /// they have no reports. Either half of the filter counts.
    private var isFiltered: Bool {
        !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || !selectedPersonaKeys.isEmpty
    }

    private var emptyFilterState: some View {
        let personaNames = personaTags
            .filter { selectedPersonaKeys.contains($0.key) }
            .map(\.shortName)
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)

        // Name what is actually filtering, so the way out is obvious.
        let message: String = {
            if !query.isEmpty && !personaNames.isEmpty {
                return "No \(personaNames.joined(separator: " / ")) reports match \"\(query)\""
            }
            if !query.isEmpty {
                return "No reports match \"\(query)\""
            }
            if personaNames.count == 1 {
                return "No \(personaNames[0]) analyses yet"
            }
            return "No analyses yet for the selected analysts"
        }()

        return VStack(spacing: AppSpacing.sm) {
            Image(systemName: query.isEmpty ? "line.3.horizontal.decrease.circle" : "magnifyingglass")
                .font(.system(size: 32))
                .foregroundColor(AppColors.textMuted)
            Text(message)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
            Text(query.isEmpty
                 ? "Clear the analyst filter to see your other reports."
                 : "Try a different ticker, company, or analyst.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
        .accessibilityElement(children: .combine)
    }
}

#Preview {
    ScrollView {
        ReportsListSection(
            sections: [ReportSectionGroup(section: .recent, reports: AnalysisReport.mockReports)],
            sortOption: .constant(.dateNewest),
            searchText: .constant(""),
            isSearchActive: .constant(false),
            isSelecting: .constant(false),
            selectedIds: [],
            personaTags: AnalysisPersona.allCases,
            selectedPersonaKeys: [],
            onReportTapped: { _ in },
            onRetryTapped: { _ in },
            onToggleSelect: { _ in },
            onToggleSelectingMode: { },
            onTogglePersonaTag: { _ in }
        )
    }
    .background(AppColors.background)
}
