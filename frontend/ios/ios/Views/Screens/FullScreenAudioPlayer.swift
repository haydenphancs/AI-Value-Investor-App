//
//  FullScreenAudioPlayer.swift
//  ios
//
//  Full screen audio player with complete playback controls
//  Expanded from mini player, dismissible via swipe down gesture
//

import SwiftUI

struct FullScreenAudioPlayer: View {
    @EnvironmentObject private var audioManager: AudioManager
    /// "Read" / "Go to Text": open the reader at the core the narration is in (book audio only).
    /// Every host passes one — `RootContainerView` and `GlobalAudioOverlay`. It used to come only
    /// from the two Book screens, so Read vanished whenever the player was expanded anywhere else
    /// (TestFlight 1.0(8)). nil only in previews.
    var onNavigateToCore: ((NarratedCoreRoute) -> Void)? = nil

    @State private var dragOffset: CGFloat = 0
    @State private var showSpeedPicker: Bool = false
    @State private var showSleepTimer: Bool = false
    @State private var showShareSheet: Bool = false

    private let dismissThreshold: CGFloat = 150
    /// The artwork's largest size. It shrinks below this when the column is short on height
    /// (small phones, large Dynamic Type) — see `artworkSection`.
    private let maxArtworkSize: CGFloat = 280

    /// The core the narration is currently inside (number + title), for book audio only.
    private var currentBookCore: (number: Int, title: String)? {
        guard let episode = audioManager.currentEpisode,
              let order = episode.bookCurriculumOrder,
              let info = BookAudioInfo.byOrder[order] else { return nil }
        let t = audioManager.currentTime
        let num = info.coreStartSeconds.filter { Double($0.value) <= t }
            .max(by: { $0.value < $1.value })?.key
            ?? info.coreStartSeconds.min(by: { $0.value < $1.value })?.key
        guard let n = num else { return nil }
        let title = BookCoreChapter.listsByOrder[order]?.first { $0.number == n }?.title ?? ""
        return (n, title)
    }

    /// Where Read goes — only when a host can open it AND the catalog knows the book and core,
    /// so the button never opens a blank cover.
    private var readerRoute: NarratedCoreRoute? {
        guard onNavigateToCore != nil,
              let order = audioManager.currentEpisode?.bookCurriculumOrder,
              let core = currentBookCore else { return nil }
        let route = NarratedCoreRoute(curriculumOrder: order, coreNumber: core.number)
        return LibraryBook.narratedCore(for: route) == nil ? nil : route
    }

    var body: some View {
        // The WINDOW's insets, not `geometry.safeAreaInsets`. The GeometryReader below ignores the
        // safe area so the player spans the whole window in every host (root overlay, and the
        // `.overlay` inside a cover) and slides fully off-screen on collapse — and a GeometryReader
        // that ignores the safe area reports ZERO insets. Reading those put the header inside the
        // Dynamic Island band and the bottom row over the home indicator (TestFlight 1.0(8)).
        let insets = WindowMetrics.safeAreaInsets
        GeometryReader { geometry in
            ZStack {
                // Background gradient
                backgroundGradient
                    .ignoresSafeArea()

                // Main content
                VStack(spacing: 0) {
                    // Drag indicator and header
                    headerSection

                    // Current core being narrated (book audio only)
                    if let core = currentBookCore {
                        currentCoreLabel(number: core.number, title: core.title)
                    }

                    // The two spacers yield first (priority -1) so the artwork keeps its size
                    // until the column is genuinely short; then the artwork shrinks, never the text.
                    Spacer(minLength: AppSpacing.sm)
                        .layoutPriority(-1)

                    // Artwork
                    artworkSection

                    Spacer(minLength: AppSpacing.sm)
                        .layoutPriority(-1)

                    // Title and info
                    titleSection

                    // Progress bar
                    progressSection
                        .padding(.top, AppSpacing.xxl)

                    // Main controls
                    controlsSection
                        .padding(.top, AppSpacing.xl)

                    // Secondary controls
                    secondaryControlsSection
                        .padding(.top, AppSpacing.xxl)

                    Spacer()
                        .frame(height: insets.bottom + AppSpacing.xl)
                }
                // Symmetric again. The old `.leading(sm)` / `.trailing(xxxl)` + the ellipsis's
                // `offset(x: -7)` compensated for the artwork glow's 392pt layout frame, which made
                // this column 432pt wide on a 402pt screen; the glow no longer takes layout space.
                .padding(.horizontal, AppSpacing.sm)
                .padding(.top, insets.top)
            }
            .offset(y: dragOffset)
            .gesture(
                DragGesture()
                    .onChanged { value in
                        // Only allow downward drag
                        if value.translation.height > 0 {
                            dragOffset = value.translation.height
                        }
                    }
                    .onEnded { value in
                        if value.translation.height > dismissThreshold ||
                            value.predictedEndTranslation.height > dismissThreshold * 2 {
                            audioManager.collapsePlayer()
                        }
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                            dragOffset = 0
                        }
                    }
            )
        }
        // Span the full window in every host so the gradient reaches all edges. Content clears the
        // status bar / home indicator through the window insets read above.
        .ignoresSafeArea()
        .sheet(isPresented: $showSpeedPicker) {
            PlaybackSpeedSheet()
                .environmentObject(audioManager)
                .presentationDetents([.height(320)])
        }
        .sheet(isPresented: $showSleepTimer) {
            SleepTimerSheet()
                .environmentObject(audioManager)
                .presentationDetents([.height(400)])
        }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: shareItems)
        }
    }

    // MARK: - Share

    /// What Share sends: the episode's title and subtitle (for a book that is "by <author>" — the
    /// Book screen's exact wording), the core being narrated, and the app link that
    /// `ShareContent` appends. Was an empty action closure: a visible, dead button.
    private var shareItems: [Any] {
        ShareContent.items(shareBody)
    }

    private var shareBody: String {
        guard let episode = audioManager.currentEpisode else { return "" }
        var lines = [episode.title, episode.subtitle]
        if let core = currentBookCore {
            let coreTitle = core.title.trimmingCharacters(in: .whitespacesAndNewlines)
            lines.append(coreTitle.isEmpty ? "Core \(core.number)" : "Core \(core.number): \(coreTitle)")
        }
        return lines
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: "\n")
    }

    /// Read / Go to Text: hand the route to the host, then get out of the way.
    private func openReader(_ route: NarratedCoreRoute) {
        onNavigateToCore?(route)
        audioManager.collapsePlayer()
    }

    // MARK: - Background Gradient
    private var backgroundGradient: some View {
        ZStack {
            // Base dark background
            AppColors.background

            // Dynamic gradient from artwork colors
            if let episode = audioManager.currentEpisode {
                LinearGradient(
                    colors: [
                        episode.artworkColors.first?.opacity(0.6) ?? .clear,
                        episode.artworkColors.first?.opacity(0.3) ?? .clear,
                        AppColors.background
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
        }
    }

    // MARK: - Header Section
    private var headerSection: some View {
        // Tight on purpose (TestFlight 1.0(8): "the header is too close to my iPhone… we may
        // reduce the height"): the block now starts below the status bar, so its own top gap and
        // spacing were trimmed to keep the row just under it.
        VStack(spacing: AppSpacing.sm) {
            // Drag indicator
            Capsule()
                .fill(AppColors.textPrimary.opacity(0.3))
                .frame(width: 36, height: 5)
                .padding(.top, AppSpacing.xs)

            // Header row
            HStack {
                // Collapse button
                Button(action: {
                    audioManager.collapsePlayer()
                }) {
                    Image(systemName: "chevron.down")
                        .font(AppTypography.iconLarge).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Minimize player")

                Spacer()

                // Category label
                if let episode = audioManager.currentEpisode {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: episode.category.icon)
                            .font(AppTypography.iconXS).fontWeight(.medium)
                        Text(episode.category.rawValue.uppercased())
                            .font(AppTypography.captionEmphasis)
                            .tracking(0.8)
                    }
                    .foregroundColor(episode.category.accentColor)
                    .frame(maxWidth: .infinity)
                }

                Spacer()

                // More options. Was an empty action closure.
                moreOptionsMenu
            }
        }
    }

    /// Go to Text (book audio) · Share · Stop Playback. Go to Text and Share repeat the bottom row
    /// on purpose; Stop Playback is the only one-step way to end audio from here (otherwise:
    /// collapse, then the mini player's ✕ — which makes the same `stop()` call).
    private var moreOptionsMenu: some View {
        Menu {
            if let route = readerRoute {
                Button {
                    openReader(route)
                } label: {
                    Label("Go to Text", systemImage: "book")
                }
            }

            Button {
                showShareSheet = true
            } label: {
                Label("Share", systemImage: "square.and.arrow.up")
            }

            Divider()

            Button(role: .destructive) {
                audioManager.stop()
            } label: {
                Label("Stop Playback", systemImage: "stop.fill")
            }
        } label: {
            Image(systemName: "ellipsis")
                .font(AppTypography.iconLarge).fontWeight(.semibold)
                .foregroundColor(AppColors.textPrimary)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .accessibilityLabel("More options")
    }

    // MARK: - Current Core Label (book narration)
    private func currentCoreLabel(number: Int, title: String) -> some View {
        VStack(spacing: AppSpacing.xxs) {
            Text("CORE \(number)")
                .font(AppTypography.captionTiny).fontWeight(.bold)
                .foregroundColor(AppColors.textMuted)
                .tracking(1.0)

            Text(title)
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)
                .lineLimit(2)
        }
        .padding(.horizontal, AppSpacing.xl)
        .padding(.top, AppSpacing.lg)
    }

    // MARK: - Artwork Section
    /// A flexible square, `maxArtworkSize` at most. With the safe area honoured the column has
    /// ~96pt less height than before, so on short phones and at large Dynamic Type the artwork
    /// shrinks instead of pushing the controls off-screen. `zIndex(-1)`: the glow is drawn outside
    /// the artwork's frame and must pass UNDER the core title and book title, not over them.
    private var artworkSection: some View {
        Group {
            if let episode = audioManager.currentEpisode {
                GeometryReader { box in
                    AudioArtworkLarge(episode: episode, size: max(1, min(box.size.width, box.size.height)))
                        .scaleEffect(audioManager.isPlaying ? 1.0 : 0.95)
                        .animation(.spring(response: 0.4), value: audioManager.isPlaying)
                        .frame(width: box.size.width, height: box.size.height)
                }
                .aspectRatio(1, contentMode: .fit)
                .frame(maxWidth: maxArtworkSize, maxHeight: maxArtworkSize)
            }
        }
        .zIndex(-1)
    }

    // MARK: - Title Section
    private var titleSection: some View {
        VStack(spacing: AppSpacing.sm) {
            if let episode = audioManager.currentEpisode {
                Text(episode.title)
                    .font(AppTypography.titleCompact)
                    .foregroundColor(AppColors.textPrimary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)

                Text(episode.authorName)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Progress Section
    private var progressSection: some View {
        VStack(spacing: AppSpacing.sm) {
            // Progress slider
            AudioProgressSlider(
                progress: audioManager.progress,
                onSeek: { progress in
                    audioManager.seekToProgress(progress)
                }
            )
            .padding(.horizontal, AppSpacing.xl)

            // Time labels
            HStack {
                Text(audioManager.formattedCurrentTime)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .monospacedDigit()

                Spacer()

                Text(audioManager.formattedRemainingTime)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .monospacedDigit()
            }
            .padding(.horizontal, AppSpacing.xl)
        }
    }

    // MARK: - Main Controls Section
    private var controlsSection: some View {
        HStack(spacing: AppSpacing.xxxl) {
            // Skip backward 15s
            Button(action: {
                audioManager.skipBackward()
            }) {
                ZStack {
                    Image(systemName: "gobackward.15")
                        .font(AppTypography.iconXL).fontWeight(.medium)
                        .foregroundColor(AppColors.textPrimary)
                }
                .frame(width: 56, height: 56)
            }
            .buttonStyle(PlainButtonStyle())

            // Play/Pause
            Button(action: {
                audioManager.togglePlayPause()
            }) {
                ZStack {
                    Circle()
                        .fill(AppColors.textPrimary)
                        .frame(width: 72, height: 72)

                    Image(systemName: audioManager.isPlaying ? "pause.fill" : "play.fill")
                        .font(AppTypography.iconXL).fontWeight(.bold)
                        .foregroundColor(AppColors.background)
                        .offset(x: audioManager.isPlaying ? 0 : 2)
                }
            }
            .buttonStyle(PlainButtonStyle())

            // Skip forward 15s
            Button(action: {
                audioManager.skipForward()
            }) {
                ZStack {
                    Image(systemName: "goforward.15")
                        .font(AppTypography.iconXL).fontWeight(.medium)
                        .foregroundColor(AppColors.textPrimary)
                }
                .frame(width: 56, height: 56)
            }
            .buttonStyle(PlainButtonStyle())
        }
    }

    // MARK: - Secondary Controls
    /// Speed · Read · Sleep · Share. Read sits between Speed and Sleep (TestFlight 1.0(8): "add a
    /// back icon… between the speed icon and the sleep icon"); it is hidden for non-book audio.
    private var secondaryControlsSection: some View {
        HStack {
            // Playback speed
            Button(action: { showSpeedPicker = true }) {
                VStack(spacing: AppSpacing.xxs) {
                    Text(audioManager.playbackSpeed.label)
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Speed")
                        .font(AppTypography.captionTiny).fontWeight(.medium)
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(width: 56)
            }
            .buttonStyle(PlainButtonStyle())

            Spacer()

            // Go to the current core's reading view (book narration only). Snaps the reader to the
            // core the audio is in; the read-along highlight resumes there.
            if let route = readerRoute {
                Button(action: {
                    openReader(route)
                }) {
                    VStack(spacing: AppSpacing.xxs) {
                        Image(systemName: "book.fill")
                            .font(AppTypography.iconMedium).fontWeight(.medium)
                            .foregroundColor(AppColors.textPrimary)
                        Text("Read")
                            .font(AppTypography.captionTiny).fontWeight(.medium)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .frame(width: 56)
                }
                .buttonStyle(PlainButtonStyle())

                Spacer()
            }

            // Sleep timer
            Button(action: { showSleepTimer = true }) {
                VStack(spacing: AppSpacing.xxs) {
                    Image(systemName: audioManager.sleepTimer == .off ? "moon" : "moon.fill")
                        .font(AppTypography.iconMedium).fontWeight(.medium)
                        .foregroundColor(audioManager.sleepTimer == .off ? AppColors.textPrimary : AppColors.primaryBlue)
                    Text("Sleep")
                        .font(AppTypography.captionTiny).fontWeight(.medium)
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(width: 56)
            }
            .buttonStyle(PlainButtonStyle())

            Spacer()

            // Share. Was an empty action closure — a visible, dead button.
            Button(action: {
                showShareSheet = true
            }) {
                VStack(spacing: AppSpacing.xxs) {
                    Image(systemName: "square.and.arrow.up")
                        .font(AppTypography.iconMedium).fontWeight(.medium)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Share")
                        .font(AppTypography.captionTiny).fontWeight(.medium)
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(width: 56)
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - Audio Progress Slider
struct AudioProgressSlider: View {
    let progress: Double
    var onSeek: ((Double) -> Void)?

    @State private var isDragging: Bool = false
    @State private var dragProgress: Double = 0

    private var displayProgress: Double {
        isDragging ? dragProgress : progress
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                // Track background
                Capsule()
                    .fill(AppColors.textPrimary.opacity(0.2))
                    .frame(height: 3)

                // Progress fill
                Capsule()
                    .fill(AppColors.textPrimary)
                    .frame(width: geometry.size.width * displayProgress, height: 3)

                // Thumb (visible on drag)
                if isDragging {
                    Circle()
                        .fill(AppColors.textPrimary)
                        .frame(width: 14, height: 14)
                        .offset(x: (geometry.size.width * displayProgress) - 7)
                }
            }
            .frame(height: 20)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        isDragging = true
                        let newProgress = max(0, min(1, value.location.x / geometry.size.width))
                        dragProgress = newProgress
                    }
                    .onEnded { value in
                        let finalProgress = max(0, min(1, value.location.x / geometry.size.width))
                        onSeek?(finalProgress)
                        isDragging = false
                    }
            )
        }
        .frame(height: 20)
    }
}

// MARK: - Playback Speed Sheet
struct PlaybackSpeedSheet: View {
    @EnvironmentObject private var audioManager: AudioManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                ForEach(PlaybackSpeed.allCases) { speed in
                    Button(action: {
                        audioManager.playbackSpeed = speed
                        dismiss()
                    }) {
                        HStack {
                            Text(speed.label)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)

                            Spacer()

                            if audioManager.playbackSpeed == speed {
                                Image(systemName: "checkmark")
                                    .font(AppTypography.iconSmall).fontWeight(.semibold)
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                        .padding(.vertical, AppSpacing.xs)
                        .contentShape(Rectangle())
                    }
                }
            }
            .listStyle(.plain)
            // A .plain List draws on systemBackground (#000000 in dark), which
            // left a 1.22:1 seam under the AppColors.background nav bar. Same
            // pairing as AssetsListSection.
            .scrollContentBackground(.hidden)
            .background(AppColors.background)
            .navigationTitle("Playback Speed")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDragIndicator(.visible)
    }
}

// MARK: - Sleep Timer Sheet
struct SleepTimerSheet: View {
    @EnvironmentObject private var audioManager: AudioManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                ForEach(SleepTimerOption.allCases) { option in
                    Button(action: {
                        audioManager.sleepTimer = option
                        dismiss()
                    }) {
                        HStack {
                            Text(option.label)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)

                            Spacer()

                            if audioManager.sleepTimer == option {
                                Image(systemName: "checkmark")
                                    .font(AppTypography.iconSmall).fontWeight(.semibold)
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                        .padding(.vertical, AppSpacing.xs)
                        .contentShape(Rectangle())
                    }
                }
            }
            .listStyle(.plain)
            // A .plain List draws on systemBackground (#000000 in dark), which
            // left a 1.22:1 seam under the AppColors.background nav bar. Same
            // pairing as AssetsListSection.
            .scrollContentBackground(.hidden)
            .background(AppColors.background)
            .navigationTitle("Sleep Timer")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDragIndicator(.visible)
    }
}

// MARK: - Audio Queue Sheet

// MARK: - Preview
#Preview {
    FullScreenAudioPlayer()
        .environmentObject(AudioManager.shared)
        .onAppear {
            AudioManager.shared.play(.sampleMoneyMoves)
        }
}
