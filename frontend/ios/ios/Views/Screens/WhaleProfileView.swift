//
//  WhaleProfileView.swift
//  ios
//
//  Whale Profile screen showing detailed investor information,
//  holdings, trades, and sentiment analysis.
//

import SwiftUI

// MARK: - Whale Profile View
struct WhaleProfileView: View {
    @StateObject private var viewModel: WhaleProfileViewModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.appState) private var appState
    /// A tapped locked section → the plan sheet.
    @State private var showPaywall = false

    init(whaleId: String) {
        _viewModel = StateObject(wrappedValue: WhaleProfileViewModel(whaleId: whaleId))
    }

    /// A withheld section: the REAL header, then one card explaining what is behind the
    /// plan. Keeping the header is deliberate — a section that vanished would read as
    /// "this investor has no trades" rather than "this is paid", which is the difference
    /// between missing data and an offer.
    @ViewBuilder
    private func lockedSection(title: String, message: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text(title)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Button {
                showPaywall = true
            } label: {
                VStack(spacing: AppSpacing.sm) {
                    // A TEXT-role token — this glyph must clear 4.5:1 in both appearances.
                    // A *Graphic token would fail the launch contrast audit.
                    Image(systemName: "lock.fill")
                        .font(AppTypography.iconMedium)
                        .foregroundColor(AppColors.primaryBlue)

                    Text(message)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)

                    Text("Upgrade to unlock")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.xl)
                .padding(.horizontal, AppSpacing.lg)
                .cardSurface(cornerRadius: AppCornerRadius.large)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(title), locked")
            .accessibilityHint("Shows upgrade options")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            if viewModel.isLoading {
                ProgressView()
                    .progressViewStyle(CircularProgressViewStyle(tint: AppColors.primaryBlue))
                    .scaleEffect(1.2)
            } else if let profile = viewModel.profile {
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xl) {
                        // Profile Header
                        WhaleProfileHeader(
                            profile: profile,
                            onFollowToggle: { viewModel.toggleFollow() }
                        )

                        if !profile.isCongressional {
                            // Portfolio Stats (13F only) — FREE on every tier. The headline
                            // book size and return are what make the profile worth opening.
                            WhalePortfolioStats(profile: profile)

                            // Sector Exposure (13F only) — FREE on every tier. Shows the
                            // SHAPE of the book without naming a single position, so it
                            // previews the paid detail instead of substituting for it.
                            WhaleSectorExposureSection(sectors: profile.sectorExposure)

                            // Current Picks (13F only) — PAID.
                            if profile.isLocked {
                                lockedSection(
                                    title: "Current Picks",
                                    message: "See every position this investor holds, and how much of the book each one is."
                                )
                            } else {
                                WhaleCurrentPicksSection(
                                    holdings: viewModel.displayedHoldings,
                                    behaviorSummary: profile.behaviorSummary,
                                    onHoldingTapped: { viewModel.viewHolding($0) },
                                    onTopTenTapped: { viewModel.viewMoreHoldings() }
                                )
                            }
                        }

                        // Recent Trades — PAID. Shown for congressional whales too, which is
                        // why the lock lives here rather than inside the 13F branch above.
                        if profile.isLocked {
                            lockedSection(
                                title: profile.isCongressional ? "Recently Traded" : "Recent Trades",
                                message: "See what they bought and sold, when they filed it, and for how much."
                            )
                        } else {
                            WhaleRecentTradesSection(
                                tradeGroups: viewModel.displayedTradeGroups,
                                isCongressional: profile.isCongressional,
                                onTradeGroupTapped: { viewModel.viewTradeGroup($0) },
                                onInfoTapped: { viewModel.showRecentTradesInfo = true }
                            )
                        }

                        // Sentiment Summary — PAID.
                        if profile.isLocked {
                            lockedSection(
                                title: "Sentiment Summary",
                                message: "Read how this investor is positioned right now."
                            )
                        } else {
                            WhaleSentimentSummary(summary: profile.sentimentSummary)
                        }

                        // NOTE: a "See All Holdings - Upgrade to Pro" footer used to sit
                        // here with an empty action, and was removed on the grounds that
                        // holdings were not Pro-gated. That is no longer true — Current
                        // Picks, Recent Trades and Sentiment Summary are Pro/Max as of the
                        // whale tier gate, and each locks in place above with the real
                        // section header intact. The footer stays gone: an upsell per
                        // withheld section is honest about WHAT is behind the plan, where a
                        // single trailing button was not attached to anything.

                        // Bottom spacing
                        Spacer().frame(height: 40)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                }
                .refreshable {
                    await viewModel.refresh()
                }
            } else {
                // Load failed (offline / 500 / whale not found): loadProfile
                // leaves profile == nil and only sets errorMessage. Without this
                // branch the screen was a blank dark dead-end with no error text
                // and no retry (the .refreshable above is unreachable when
                // profile == nil). Show an actionable error + Retry instead.
                VStack(spacing: AppSpacing.md) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(AppTypography.iconJumbo)
                        .foregroundColor(AppColors.alertOrange)

                    Text(viewModel.errorMessage ?? "Failed to load profile.")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .multilineTextAlignment(.center)

                    Button {
                        viewModel.loadProfile()
                    } label: {
                        Text("Retry")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textOnAccent)
                            .padding(.horizontal, AppSpacing.lg)
                            .padding(.vertical, AppSpacing.sm)
                            .background(AppColors.primaryFill)
                            .cornerRadius(AppCornerRadius.pill)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, AppSpacing.xl)
            }
        }
        .navigationBarBackButtonHidden(true)
        .toolbar {
            ToolbarItem(placement: .navigationBarLeading) {
                NavBackButton(weight: .medium) { dismiss() }
            }

            ToolbarItem(placement: .navigationBarTrailing) {
                if let profile = viewModel.profile {
                    Button {
                        viewModel.toggleFollow()
                    } label: {
                        // A locked whale shows the lock, so the control looks like what
                        // it is before it is tapped. Following is never locked (the
                        // server never gates an unfollow), so the glyph only appears on
                        // the "Follow" state.
                        HStack(spacing: AppSpacing.xs) {
                            if profile.isLocked && !profile.isFollowing {
                                // Text-role token — must clear 4.5:1 in both appearances.
                                Image(systemName: "lock.fill")
                                    .font(AppTypography.iconXS)
                                    .fontWeight(.semibold)
                            }
                            Text(profile.isFollowing ? "Following" : "Follow")
                                .font(AppTypography.bodyEmphasis)
                        }
                        .foregroundColor(AppColors.primaryBlue)
                    }
                    .accessibilityLabel(
                        profile.isFollowing
                            ? "Following \(profile.name)"
                            : (profile.isLocked
                                ? "Follow \(profile.name), locked"
                                : "Follow \(profile.name)")
                    )
                    .accessibilityHint(
                        profile.isLocked && !profile.isFollowing ? "Shows upgrade options" : ""
                    )
                }
            }
        }
        .navigationDestination(item: $viewModel.selectedAssetNavigation) { selection in
            AssetDetailRouter(selection: selection)
        }
        .navigationDestination(item: $viewModel.selectedTradeGroupId) { groupId in
            if let group = viewModel.tradeGroup(for: groupId) {
                TradeGroupDetailView(
                    tradeGroup: group,
                    whaleName: viewModel.profile?.name ?? ""
                )
            } else {
                // A miss used to fall through to an implicit EmptyView, which SwiftUI
                // still pushes — a blank screen with a back button and no explanation.
                WhaleTradeGroupMissingView()
            }
        }
        // A PLAN gate, so the plan sheet — not the BuyCredits route a 402 takes. No amount
        // of credits unlocks a section. `.environment(\.appState, appState)` is REQUIRED:
        // PaywallView reads the custom `\.appState` key and a sheet inherits neither
        // environment automatically, so without it the sheet resolves that key's default
        // `AppState()` and highlights the wrong "current plan".
        .sheet(isPresented: $showPaywall) {
            PaywallView(context: .whaleDetail)
                .environment(\.appState, appState)
        }
        // A locked FOLLOW tap. Separate context from `.whaleDetail` above: that one is
        // "this section is paid", this one is "your plan has no tracking slot" — the
        // same distinction the Tracking tab draws with `.whaleFollowLimit`.
        .sheet(isPresented: $viewModel.showPaywall) {
            PaywallView(context: .whaleFollowLimit)
                .environment(\.appState, appState)
        }
        // Refill the moment a purchase lands rather than at next visit. Keyed on the TIER
        // rather than on the sheet dismissing, so restore-purchases and a background
        // profile refresh cover it too, and dismissing without buying costs nothing.
        // `entitlementGeneration`, not `user.tier` — the same trap HomeDashboardView had.
        // `user.tier` hydrates from its `.free` default during session restore, so observing
        // it re-loaded this profile on every launch of a paid account for no change at all.
        .onChange(of: appState.entitlementGeneration) {
            viewModel.loadProfile()
        }
    }
}

// MARK: - Profile Header
struct WhaleProfileHeader: View {
    let profile: WhaleProfile
    var onFollowToggle: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Avatar
            WhaleAvatarView(
                name: profile.name,
                avatarURL: profile.avatarURL,
                size: 80
            )

            // Name and Title. Person-fronted whales show their firm directly
            // under the name (GuruFocus-style "Warren Buffett / Berkshire
            // Hathaway") — but only when the title doesn't already contain it
            // ("Berkshire Hathaway CEO" would make a separate firm line redundant).
            VStack(spacing: AppSpacing.xs) {
                Text(profile.name)
                    .font(AppTypography.title)
                    .foregroundColor(AppColors.textPrimary)

                if let firm = profile.firmName, !firm.isEmpty,
                   !profile.title.localizedCaseInsensitiveContains(firm) {
                    Text(firm)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textPrimary.opacity(0.85))
                }

                Text(profile.title)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Risk Profile Badge — omitted entirely when the backend has no
            // classification for this whale. An absent badge says nothing; a defaulted
            // one asserts "Moderate" about a filer that has never been hydrated.
            if profile.hasRiskProfile {
                WhaleRiskBadge(riskProfile: profile.riskProfile)
            }

            // Activity notice. Sits directly under the identity block and ABOVE the stat
            // tiles on purpose: it qualifies every number below it. Without it, Michael
            // Burry's profile showed a confident $1.37B and +11.06% next to three empty
            // sections, and the reason (Scion stopped filing after Q3 2025) appeared
            // nowhere at all.
            if profile.hasActivityNotice {
                WhaleActivityNotice(
                    message: profile.activityNotice,
                    hasStoppedFiling: profile.hasStoppedFiling
                )
            }

            // Description
            WhaleDescriptionSection(description: profile.description)
        }
        .padding(.top, AppSpacing.md)
    }
}

// MARK: - Activity Notice

/// "This filer has gone quiet, and here is what we actually know."
///
/// Muted by default — most of these are ordinary (a politician who has not traded), and
/// an alarm-coloured banner would overstate them. Only a filer that has genuinely STOPPED
/// filing gets the warmer `caution` treatment.
///
/// Both tokens are TEXT-role per `.claude/rules/ios-swiftui.md`; a `*Graphic` token would
/// fail the DEBUG launch contrast audit.
struct WhaleActivityNotice: View {
    let message: String
    var hasStoppedFiling: Bool = false

    private var tint: Color { hasStoppedFiling ? AppColors.caution : AppColors.textMuted }

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: hasStoppedFiling
                  ? "exclamationmark.circle" : "clock")
                .font(AppTypography.iconSmall)
                .foregroundColor(tint)

            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(tint)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 0)
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(tint.opacity(0.10))
        .cornerRadius(AppCornerRadius.medium)
        .padding(.horizontal, AppSpacing.lg)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(message)
    }
}

// MARK: - Whale Avatar View
struct WhaleAvatarView: View {
    let name: String
    let avatarURL: String?
    let size: CGFloat
    var category: WhaleCategory = .investors

    private var initials: String {
        let parts = name.components(separatedBy: " ")
        let first = parts.first?.first.map(String.init) ?? ""
        let last = parts.count > 1 ? parts.last?.first.map(String.init) ?? "" : ""
        return "\(first)\(last)"
    }

    private var backgroundColor: Color {
        // FILL tokens: this is a saturated avatar circle carrying `textOnAccent` initials,
        // and the text-safe siblings lighten in dark — white on `gain` #22C55E is 2.28:1,
        // on `accentCyan` #06B6D4 2.43:1, on `alertPurple` #C084FC 2.64:1.
        let colors: [Color] = [
            AppColors.primaryFill, AppColors.gainFill,
            AppColors.alertOrangeFill, AppColors.alertPurpleFill,
            AppColors.accentCyanFill, AppColors.lossFill,
        ]
        return colors[abs(name.hashValue) % colors.count]
    }

    /// Ink for `backgroundColor`, index-for-index with the palette above. Slots 1 and 5
    /// are the ADAPTIVE `gainFill`/`lossFill` and need near-black in dark; the other four
    /// are frozen and need white. One ink cannot serve both — see `AppColors.textOnFill`.
    private var backgroundInk: Color {
        let inks: [Color] = [
            AppColors.textOnAccent, AppColors.textOnFill,
            AppColors.textOnAccent, AppColors.textOnAccent,
            AppColors.textOnAccent, AppColors.textOnFill,
        ]
        return inks[abs(name.hashValue) % inks.count]
    }

    var body: some View {
        if category == .institutions, let url = avatarURL, let imageURL = URL(string: url) {
            AsyncImage(url: imageURL) { phase in
                switch phase {
                case .success(let image):
                    Circle()
                        .fill(AppColors.textOnAccent)
                        .frame(width: size, height: size)
                        .overlay(
                            image
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .padding(size * 0.15)
                        )
                        .clipShape(Circle())
                case .failure, .empty:
                    initialsAvatar
                @unknown default:
                    initialsAvatar
                }
            }
        } else {
            initialsAvatar
        }
    }

    private var initialsAvatar: some View {
        Circle()
            .fill(backgroundColor)
            .frame(width: size, height: size)
            .overlay(
                Text(initials)
                    .font(.system(size: size * 0.38, weight: .bold))
                    .foregroundColor(backgroundInk)
            )
    }
}

// MARK: - Risk Badge
struct WhaleRiskBadge: View {
    let riskProfile: WhaleRiskProfile

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: riskProfile.iconName)
                .font(AppTypography.iconXS).fontWeight(.medium)

            Text(riskProfile.rawValue)
                .font(AppTypography.captionEmphasis)
        }
        .foregroundColor(riskProfile.color)
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(riskProfile.color.opacity(0.15))
        .cornerRadius(AppCornerRadius.pill)
    }
}

// MARK: - Description Section
struct WhaleDescriptionSection: View {
    let description: String
    @State private var isExpanded: Bool = false
    
    private let lineLimit: Int = 3
    
    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
                .lineLimit(isExpanded ? nil : lineLimit)
                .animation(.easeInOut(duration: 0.2), value: isExpanded)
            
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            } label: {
                Text(isExpanded ? "Show Less" : "Show More")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
                    // A bare Text label is a ~17pt target. Padding + a content shape is
                    // what actually grows it: hitSlop's trailing .padding(-inset)
                    // returns the frame and a Button clips its hit region to it, so
                    // slop alone moves nothing here (measured: 21 vs 117 hit points).
                    // The sites that use slop successfully set a .frame first.
                    // 8pt takes a ~17pt text run to ~33pt. Not the full 44:
                    // the remaining 11 would push "more" visibly away from the
                    // paragraph it belongs to, for a control that is already
                    // twice the size it was.
                    .padding(.vertical, AppSpacing.sm)
                    .padding(.trailing, AppSpacing.md)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.md)
    }
}

// MARK: - Portfolio Stats
struct WhalePortfolioStats: View {
    let profile: WhaleProfile

    @State private var showInfoSheet = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack {
                // ── Portfolio value ──────────────────────────────────
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    if profile.hasDisplayablePortfolioValue {
                        Text(profile.formattedPortfolioValue)
                            .font(AppTypography.titleLarge)
                            .foregroundColor(AppColors.textPrimary)
                    } else {
                        Text("—")
                            .font(AppTypography.titleLarge)
                            .foregroundColor(AppColors.textMuted)
                    }

                    Text(portfolioCaption)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                // ── Annual return ────────────────────────────────────
                VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                    if profile.hasDisplayableReturn {
                        Text(profile.formattedYTDReturn)
                            .font(AppTypography.titleLarge)
                            .foregroundColor(
                                profile.isPositiveReturn
                                    ? AppColors.bullish : AppColors.bearish
                            )
                    } else {
                        // The em-dash replaces a green "+0.0%" that a NULL return
                        // used to produce — missing data reading as a flat year.
                        // Deliberately the SAME muted treatment the congressional
                        // branch above uses, so this introduces no new vocabulary.
                        Text("—")
                            .font(AppTypography.titleLarge)
                            .foregroundColor(AppColors.textMuted)
                    }

                    Text(returnCaption)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .multilineTextAlignment(.trailing)
                }
            }

            // One qualifier spanning BOTH tiles, not an icon per tile. Most
            // readers never open a sheet, so the scope has to be legible without
            // tapping: a 13F is US-listed stock positions, never total wealth.
            Button {
                showInfoSheet = true
            } label: {
                HStack(alignment: .top, spacing: AppSpacing.xs) {
                    Image(systemName: "info.circle")
                        .font(AppTypography.iconXS)
                    Text(qualifierText)
                        .font(AppTypography.caption)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .foregroundColor(AppColors.textMuted)
            }
            .buttonStyle(PlainButtonStyle())
            .accessibilityLabel("What these numbers mean")
        }
        .padding(AppSpacing.lg)
        .sheet(isPresented: $showInfoSheet) {
            WhalePortfolioStatsInfoSheet(profile: profile)
        }
    }

    private var portfolioCaption: String {
        return profile.hasDisplayablePortfolioValue
            ? profile.portfolioCaption : "Not available"
    }

    private var returnCaption: String {
        return profile.hasDisplayableReturn
            ? profile.returnCaption : profile.returnUnavailableCaption
    }

    private var qualifierText: String {
        if profile.isStockProxyReturn {
            // Naming the vehicle is the whole point: the left tile is a 13F
            // sleeve and the right one is a share price. Two different sources,
            // side by side, previously with nothing saying so.
            return "U.S.-listed stock positions from this filer's latest 13F — "
                + "not total wealth. The return is a share price, not the 13F book."
        }
        return "U.S.-listed stock positions from this filer's latest 13F — not total wealth."
    }
}

// MARK: - Sector Exposure Section
struct WhaleSectorExposureSection: View {
    let sectors: [WhaleSectorAllocation]
    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack {
                Text("Sector Exposure")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    showInfoSheet = true
                } label: {
                    Image(systemName: "info.circle")
                        .font(AppTypography.iconDefault)
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(.plain)
            }

            if sectors.isEmpty {
                Text("No sector data available")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, AppSpacing.xl)
            } else {
                DonutChartView(
                    segments: sectors.map { sector in
                        DonutChartSegment(
                            id: sector.id,
                            value: sector.percentage,
                            color: sector.color,
                            label: sector.name
                        )
                    },
                    lineWidth: 20
                )
                .padding(.vertical, AppSpacing.sm)
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            SectorExposureInfoSheet()
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Sector Exposure Info Sheet
struct SectorExposureInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // What is Sector Exposure
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "chart.pie.fill")
                                .font(AppTypography.iconLarge)
                                .foregroundColor(AppColors.primaryBlue)

                            Text("What is Sector Exposure?")
                                .font(AppTypography.heading)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Sector exposure shows how an investor's portfolio is distributed across different market sectors. It reveals their investment strategy and risk preferences.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .lineSpacing(4)
                    }

                    Divider()
                        .overlay(AppColors.cardBackgroundLight)

                    // Why it Matters
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "lightbulb.fill")
                                .font(AppTypography.iconLarge)
                                .foregroundColor(AppColors.alertOrange)

                            Text("Why It Matters")
                                .font(AppTypography.heading)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            WhaleBulletPoint(
                                icon: "target",
                                text: "Understand the investor's focus areas and conviction sectors"
                            )
                            WhaleBulletPoint(
                                icon: "arrow.triangle.branch",
                                text: "See how diversified or concentrated their portfolio is"
                            )
                            WhaleBulletPoint(
                                icon: "chart.line.uptrend.xyaxis",
                                text: "Track shifts in sector allocation over time"
                            )
                            WhaleBulletPoint(
                                icon: "exclamationmark.triangle",
                                text: "Identify potential risks from sector concentration"
                            )
                        }
                    }

                    Divider()
                        .overlay(AppColors.cardBackgroundLight)

                    // How to Use
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "hand.tap.fill")
                                .font(AppTypography.iconLarge)
                                .foregroundColor(AppColors.bullish)

                            Text("How to Use This")
                                .font(AppTypography.heading)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Compare the whale's sector exposure to your own portfolio. If you want to follow their strategy, consider similar sector weightings. Use this to identify sectors they're bullish on.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .lineSpacing(4)
                    }

                    Spacer().frame(height: AppSpacing.xl)
                }
                .padding(AppSpacing.xl)
            }
            .background(AppColors.background)
            .navigationTitle("Sector Exposure")
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
    }
}

// MARK: - Recent Trades Info Sheet
struct RecentTradesInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // What are Recent Trades
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "arrow.left.arrow.right")
                                .font(AppTypography.iconLarge)
                                .foregroundColor(AppColors.primaryBlue)

                            Text("What are Recent Trades?")
                                .font(AppTypography.heading)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                                Text("The Definition:")
                                    .font(AppTypography.bodyEmphasis)
                                    .foregroundColor(AppColors.textPrimary)
                                
                                Text("\"Recent Trades\" captures the latest buy and sell transactions disclosed by institutional investors (Whales) or politicians.")
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textSecondary)
                                    .lineSpacing(4)
                            }
                            
                            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                                Text("The Data Source:")
                                    .font(AppTypography.bodyEmphasis)
                                    .foregroundColor(AppColors.textPrimary)
                                
                                Text("This information is sourced from mandatory legal filings, such as SEC 13F Filings (for hedge funds) and Congressional Disclosures (for politicians).")
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textSecondary)
                                    .lineSpacing(4)
                            }
                            
                            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                                Text("The \"Lag\" Factor:")
                                    .font(AppTypography.bodyEmphasis)
                                    .foregroundColor(AppColors.textPrimary)
                                
                                Text("Note that these trades are often reported with a delay (e.g., 45 days for 13F filings), meaning they represent a snapshot of past activity rather than real-time moves.")
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textSecondary)
                                    .lineSpacing(4)
                            }
                        }
                    }

                    Divider()
                        .overlay(AppColors.cardBackgroundLight)

                    // Why it Matters
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "lightbulb.fill")
                                .font(AppTypography.iconLarge)
                                .foregroundColor(AppColors.alertOrange)

                            Text("Why It Matters")
                                .font(AppTypography.heading)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            WhaleBulletPoint(
                                icon: "chart.line.uptrend.xyaxis",
                                text: "Identify emerging trends and conviction changes"
                            )
                            WhaleBulletPoint(
                                icon: AppSymbols.ai,
                                text: "Discover new opportunities they're exploring"
                            )
                            WhaleBulletPoint(
                                icon: "exclamationmark.triangle",
                                text: "Spot positions they're reducing or exiting"
                            )
                        }
                    }

                    Divider()
                        .overlay(AppColors.cardBackgroundLight)

                    // How to Use
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "hand.tap.fill")
                                .font(AppTypography.iconLarge)
                                .foregroundColor(AppColors.bullish)

                            Text("How to Use This")
                                .font(AppTypography.heading)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Pay attention to the size and direction of trades. Large buys signal high conviction, while sells may indicate risk concerns or profit-taking. Tap on any trade group to see detailed transaction information.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .lineSpacing(4)
                    }

                    Spacer().frame(height: AppSpacing.xl)
                }
                .padding(AppSpacing.xl)
            }
            .background(AppColors.background)
            .navigationTitle("Recent Trades")
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
    }
}

// MARK: - Missing Trade Group

/// Shown when a trade-group id no longer resolves in the loaded profile (the profile
/// was refreshed underneath the navigation, or the group aged out of the snapshot).
/// Replaces an implicit `EmptyView`, which SwiftUI still pushes as a blank screen.
struct WhaleTradeGroupMissingView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()
            VStack(spacing: AppSpacing.md) {
                Image(systemName: "tray")
                    .font(AppTypography.iconJumbo)
                    .foregroundColor(AppColors.textMuted)

                Text("These trades are no longer available")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)

                Button {
                    dismiss()
                } label: {
                    Text("Go Back")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textOnAccent)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(AppColors.primaryFill)
                        .cornerRadius(AppCornerRadius.pill)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, AppSpacing.xl)
        }
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Whale Bullet Point
struct WhaleBulletPoint: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: 20)

            Text(text)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(2)
        }
    }
}

// MARK: - Current Picks Section
struct WhaleCurrentPicksSection: View {
    let holdings: [WhaleHolding]
    let behaviorSummary: WhaleBehaviorSummary
    var isCongressional: Bool = false
    var onHoldingTapped: ((WhaleHolding) -> Void)?
    var onTopTenTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                Text(isCongressional ? "Recently Traded" : "Current Picks")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                if !isCongressional {
                    Button {
                        onTopTenTapped?()
                    } label: {
                        Text("Top 10")
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(.plain)
                }
            }

            // Behavior Summary Card
            WhaleBehaviorSummaryCard(behaviorSummary: behaviorSummary)

            // Holdings List
            if holdings.isEmpty {
                Text("No holdings data available")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, AppSpacing.xl)
            } else {
                VStack(spacing: 0) {
                    ForEach(holdings) { holding in
                        WhaleHoldingRow(
                            holding: holding,
                            onTap: { onHoldingTapped?(holding) }
                        )

                        if holding.id != holdings.last?.id {
                            Divider()
                                .overlay(AppColors.cardBackgroundLight)
                        }
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }
}

// MARK: - Behavior Summary Card
struct WhaleBehaviorSummaryCard: View {
    let behaviorSummary: WhaleBehaviorSummary

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Behavior Summary:")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            Text(behaviorSummary.formattedSummary)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.lg)
        .background(Color.clear)
        .cornerRadius(AppCornerRadius.medium)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .stroke(AppColors.primaryBlue.opacity(0.3), lineWidth: 1)
        )
    }
}

// MARK: - Whale Holding Row
struct WhaleHoldingRow: View {
    let holding: WhaleHolding
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                // Logo/Ticker Icon
                WhaleTickerIcon(ticker: holding.ticker, logoURL: holding.logoURL)

                // Company Info
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(holding.companyName)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)

                    Text(holding.ticker)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                // Allocation and Change
                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text(holding.formattedAllocation)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text(holding.formattedChange)
                        .font(AppTypography.caption)
                        .foregroundColor(
                            holding.displayedChangePercent > 0 ? AppColors.bullish :
                            holding.displayedChangePercent < 0 ? AppColors.bearish :
                            AppColors.textMuted
                        )
                }
            }
            .padding(.vertical, AppSpacing.md)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Whale Ticker Icon
struct WhaleTickerIcon: View {
    let ticker: String
    var logoURL: String? = nil

    private var backgroundColor: Color {
        let colors: [Color] = [
            AppColors.primaryBlue,
            AppColors.bullish,
            AppColors.alertOrange,
            AppColors.alertPurple,
            AppColors.accentCyan
        ]
        let index = abs(ticker.hashValue) % colors.count
        return colors[index]
    }

    var body: some View {
        if let urlString = logoURL, let url = URL(string: urlString) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(width: 40, height: 40)
                        .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))
                default:
                    letterFallback
                }
            }
        } else {
            letterFallback
        }
    }

    private var letterFallback: some View {
        ZStack {
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(backgroundColor.opacity(0.2))
                .frame(width: 40, height: 40)

            Text(String(ticker.prefix(1)))
                .font(AppTypography.bodyEmphasis).fontWeight(.bold)
                .foregroundColor(backgroundColor)
        }
    }
}

// MARK: - Recent Trades Section
struct WhaleRecentTradesSection: View {
    let tradeGroups: [WhaleTradeGroup]
    /// Congressional whales title this section "Recently Traded" — the same wording the
    /// LOCKED variant of this section already used, so an upgrade no longer renames the
    /// section under the user.
    var isCongressional: Bool = false
    var onTradeGroupTapped: ((WhaleTradeGroup) -> Void)?
    var onInfoTapped: (() -> Void)?
    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                Text(isCongressional ? "Recently Traded" : "Recent Trades")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    showInfoSheet = true
                } label: {
                    Image(systemName: "info.circle")
                        .font(AppTypography.iconDefault)
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(.plain)
            }

            // Trade Groups List
            if tradeGroups.isEmpty {
                Text("No recent trades disclosed")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, AppSpacing.xl)
            }
            VStack(spacing: 0) {
                ForEach(tradeGroups) { group in
                    WhaleTradeGroupCard(
                        group: group,
                        onTap: { onTradeGroupTapped?(group) }
                    )

                    if group.id != tradeGroups.last?.id {
                        Divider()
                            .overlay(AppColors.cardBackgroundLight)
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            RecentTradesInfoSheet()
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Whale Trade Group Card
struct WhaleTradeGroupCard: View {
    let group: WhaleTradeGroup
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: 0) {
                // Left content
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    // Row 1: Date and trade count (left), Net amount (right)
                    HStack {
                        // Left side: Date and trade count stacked
                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            Text(group.formattedDate)
                                .font(AppTypography.bodyEmphasis)
                                .foregroundColor(AppColors.textSecondary)
                            
                            Text(group.tradeCount > group.trades.count
                                 ? "Top \(group.trades.count) of \(group.formattedTradeCount)"
                                 : group.formattedTradeCount)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }

                        Spacer()

                        // Right side: Net amount and action
                        VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                            Text(group.formattedNetAmount.replacingOccurrences(of: " BOUGHT", with: "").replacingOccurrences(of: " SOLD", with: ""))
                                .font(AppTypography.bodySmallEmphasis)
                                .foregroundColor(group.netAction == .bought ? AppColors.bullish : AppColors.bearish)
                            
                            Text(group.netAction.rawValue)
                                .font(AppTypography.captionSmall).fontWeight(.bold)
                                .foregroundColor(group.netAction == .bought ? AppColors.bullish : AppColors.bearish)
                        }
                    }

                    // Row 2: Optional summary
                    if let summary = group.summary {
                        Text(summary)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(2)
                    }
                }

                // Chevron
                Image(systemName: "chevron.right")
                    .font(AppTypography.iconSmall).fontWeight(.medium)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.leading, AppSpacing.md)
            }
            .padding(.vertical, AppSpacing.md)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Sentiment Summary
struct WhaleSentimentSummary: View {
    let summary: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "brain.head.profile")
                    .font(AppTypography.iconDefault).fontWeight(.medium)
                    .foregroundColor(AppColors.primaryBlue)

                Text("Sentiment Summary")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            // An empty `sentiment_summary` is its BACKEND DEFAULT (`str = ""`), which a
            // congressional whale with no retained disclosures hits routinely. The card
            // used to render its title and gradient over nothing at all — a styled
            // empty box that reads as a rendering fault rather than as missing data.
            if summary.isEmpty {
                Text("No sentiment summary available yet")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
            } else {
                Text(summary)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            LinearGradient(
                colors: [
                    AppColors.primaryBlue.opacity(0.15),
                    AppColors.primaryBlue.opacity(0.05)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .cornerRadius(AppCornerRadius.large)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .stroke(AppColors.primaryBlue.opacity(0.3), lineWidth: 1)
        )
    }
}

// MARK: - Preview
#Preview {
    NavigationStack {
        WhaleProfileView(whaleId: "warren-buffett")
    }
}
