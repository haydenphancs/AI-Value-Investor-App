//
//  BookCoreDetailView.swift
//  ios
//
//  Book Core Detail View - Detailed content view for a specific core chapter
//  Displays chapter content with sections, audio playback, and AI chat
//

import SwiftUI
import Combine

// MARK: - Book Core Detail View
struct BookCoreDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var audioManager: AudioManager
    @State private var scrollOffset: CGFloat = 0
    @State private var previousScrollOffset: CGFloat = 0
    @State private var inputText: String = ""
    @State private var currentContent: CoreChapterContent

    // Completion tracking — real, persisted progress (local cache + backend sync).
    @ObservedObject private var progress = BookProgressStore.shared
    @State private var audioCompletionCancellable: AnyCancellable?

    // Track if user initiated playback from this view or if it was already playing
    @State private var shouldShowPlayer: Bool = false

    // True while we're programmatically scrolling to follow the narration, so the scroll-driven
    // player hide/show logic ignores it (read-along auto-scroll shouldn't hide the mini player).
    @State private var isAutoScrolling: Bool = false

    /// Stable token keying this screen's compact-mode requests + audio overlay host registration.
    @State private var compactToken = UUID().uuidString

    let book: LibraryBook
    let allCores: [BookCoreChapter]
    
    init(content: CoreChapterContent, book: LibraryBook) {
        self.book = book
        self.allCores = book.coreChapters
        self._currentContent = State(initialValue: content)
    }
    
    private var currentCoreIndex: Int {
        allCores.firstIndex { $0.number == currentContent.chapterNumber } ?? 0
    }
    
    private var hasPreviousCore: Bool {
        currentCoreIndex > 0
    }
    
    private var hasNextCore: Bool {
        currentCoreIndex < allCores.count - 1
    }

    private var content: CoreChapterContent {
        currentContent
    }

    // MARK: - Read-along (sentence highlighting synced to the narration)

    /// Timed narrated blocks (headings + paragraphs, in render order) for the current core. These
    /// align 1:1 with the LEADING narrated sections; the action plan (last) has no entry.
    private var readAlongBlocks: [ReadAlongBlock] {
        ReadAlongBlock.byBook[book.curriculumOrder]?[currentContent.chapterNumber] ?? []
    }

    /// The narration playhead (seconds) when THIS book's audio is the active episode, else nil
    /// (no highlight). Passed to each block so only the sentence under the playhead lights up.
    private var readAlongActiveTime: Double? {
        guard audioManager.currentEpisode?.id == book.audioEpisode.id else { return nil }
        return audioManager.currentTime
    }

    /// Index of the narrated block currently being read (drives auto-scroll), if any.
    private var activeReadAlongBlock: Int? {
        guard let t = readAlongActiveTime else { return nil }
        return readAlongBlocks.firstIndex { blk in
            blk.sentences.contains { t >= $0.start && t < $0.end }
        }
    }

    private var isCurrentCoreCompleted: Bool {
        progress.isCompleted(order: book.curriculumOrder, core: currentContent.chapterNumber)
    }

    // Computed property for header opacity based on scroll
    private var headerOpacity: Double {
        let fadeStart: CGFloat = 60
        let fadeEnd: CGFloat = 120
        if scrollOffset < fadeStart { return 0 }
        if scrollOffset > fadeEnd { return 1 }
        return Double((scrollOffset - fadeStart) / (fadeEnd - fadeStart))
    }

    // The whole book is ONE narration file; a core is just a seek offset within it.
    private var currentAudioEpisode: AudioEpisode { book.audioEpisode }

    // Load the book narration only if it isn't already the active episode — so navigating between
    // cores (or arriving while it's already playing) doesn't tear down and restart playback.
    private func ensureBookEpisodeLoaded() {
        if audioManager.currentEpisode?.id != currentAudioEpisode.id {
            audioManager.load(currentAudioEpisode)
        }
    }

    var body: some View {
        ZStack(alignment: .top) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main scrollable content
            ScrollViewReader { scrollProxy in
            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {
                    // Header spacer for back button area
                    Color.clear
                        .frame(height: 60)

                    // Chapter header
                    CoreDetailHeaderSection(
                        content: content,
                        book: book,
                        onPlayStarted: { shouldShowPlayer = true }
                    )
                    .padding(.horizontal, AppSpacing.lg)

                    // Content sections — the leading narrated blocks render with live sentence
                    // highlighting (ReadAlongBlockView); the action plan (and anything past the
                    // narrated prefix) renders normally. readAlongBlocks aligns 1:1 with the
                    // narrated sections, so index < count == "this section is narrated".
                    LazyVStack(alignment: .leading, spacing: AppSpacing.xxl) {
                        ForEach(Array(content.sections.enumerated()), id: \.element.id) { index, section in
                            if index < readAlongBlocks.count {
                                ReadAlongBlockView(
                                    block: readAlongBlocks[index],
                                    activeTime: readAlongActiveTime
                                )
                                .id("readAlongBlock-\(index)")
                            } else {
                                CoreSectionView(section: section)
                            }
                        }
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.xxl)

                    // Victory Button - Complete & Continue
                    CoreCompletionButton(
                        isCompleted: isCurrentCoreCompleted,
                        hasNextCore: hasNextCore,
                        onComplete: handleCoreCompletion
                    )
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.xxl)

                    // Bottom padding for global audio player + AI bar
                    Color.clear.frame(height: 180)
                }
                .background(
                    GeometryReader { proxy in
                        Color.clear
                            .preference(
                                key: CoreDetailScrollOffsetKey.self,
                                value: -proxy.frame(in: .named("coreDetailScroll")).origin.y
                            )
                    }
                )
            }
            .coordinateSpace(name: "coreDetailScroll")
            .onPreferenceChange(CoreDetailScrollOffsetKey.self) { value in
                handleScrollChange(newOffset: value)
            }
            .onChange(of: activeReadAlongBlock) { _, newValue in
                // Follow the narration: gently center the block being read, without tripping the
                // scroll-driven player hide. Only while actually playing.
                guard audioManager.isPlaying, let idx = newValue else { return }
                isAutoScrolling = true
                withAnimation(.easeInOut(duration: 0.4)) {
                    scrollProxy.scrollTo("readAlongBlock-\(idx)", anchor: .center)
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { isAutoScrolling = false }
            }
            }

            // Sticky mini header (appears on scroll)
            if headerOpacity > 0 {
                CoreDetailMiniHeader(
                    content: content,
                    hasPrevious: hasPreviousCore,
                    hasNext: hasNextCore,
                    onBackTapped: handleBackTapped,
                    onCloseTapped: handleCloseTapped,
                    onNextTapped: handleNextTapped
                )
                .opacity(headerOpacity)
                .zIndex(10)
            }

            // Navigation header (transparent)
            if headerOpacity == 0 {
                CoreDetailNavigationHeader(
                    hasPrevious: hasPreviousCore,
                    hasNext: hasNextCore,
                    onBackTapped: handleBackTapped,
                    onCloseTapped: handleCloseTapped,
                    onNextTapped: handleNextTapped
                )
                .zIndex(10)
            }

            // Bottom AI chat bar
            VStack(spacing: 0) {
                Spacer()

                // Global Mini Player (show if audio was playing or user started playback);
                // hidden when collapsed to the top island (chat-bar focused).
                if shouldShowPlayer && audioManager.hasActiveEpisode && !audioManager.isPlayerHiddenByScroll && !audioManager.showFullScreenPlayer && !audioManager.isCompactMode {
                    GlobalMiniPlayer()
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }

                // Tapping the chat bar collapses the player to the top status island (Wiser-only).
                CaydexAIChatBar(
                    inputText: $inputText,
                    onSend: handleAISend,
                    onFocusChange: { focused in
                        audioManager.setCompactMode(focused, reason: compactToken)
                    }
                )
            }
            .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.hasActiveEpisode)
            .animation(.spring(response: 0.25, dampingFraction: 0.85), value: audioManager.isPlayerHiddenByScroll)
            .animation(.spring(response: 0.3, dampingFraction: 0.85), value: shouldShowPlayer)
            .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.isCompactMode)
        }
        // Top status island + full-screen player + overlay-host registration (this screen is a
        // fullScreenCover above RootContainerView, whose own overlay would be hidden). "Read" jumps
        // the reading view to the core the narration is currently in.
        .globalAudioOverlay(token: compactToken, onNavigateToCore: { coreNumber in
            jumpReadingToCore(coreNumber)
        })
        .navigationBarHidden(true)
        .onAppear {
            // If audio is already playing, show the player
            if audioManager.hasActiveEpisode && audioManager.isPlaying {
                shouldShowPlayer = true
            }

            // Load the book narration when view appears (paused), unless already active
            ensureBookEpisodeLoaded()

            // When the single book file finishes, the final core is done.
            audioCompletionCancellable = audioManager.playbackDidComplete
                .receive(on: DispatchQueue.main)
                .sink { [self] completedEpisode in
                    if completedEpisode.id == book.audioEpisode.id {
                        autoCompleteFinalCore()
                    }
                }
        }
        .onDisappear {
            // Reset scroll hiding when leaving the view
            audioManager.resetScrollHiding()
            // Cancel audio completion subscription
            audioCompletionCancellable?.cancel()
            audioCompletionCancellable = nil
        }
        .onChange(of: currentContent.chapterNumber) {
            // Same single book file across cores — only load if it isn't already active, so
            // swiping between cores never interrupts ongoing playback.
            ensureBookEpisodeLoaded()
        }
        .onChange(of: audioManager.currentTime) { oldTime, newTime in
            // Auto-complete cores as the narration plays through them (no button tap needed).
            autoCompleteListenedCores(from: oldTime, to: newTime)
            // Follow the audio: if it leaves the core we're viewing, open the next one.
            autoAdvanceReading(from: oldTime, to: newTime)
        }
    }

    // MARK: - Scroll Handling
    private func handleScrollChange(newOffset: CGFloat) {
        // Ignore programmatic read-along scrolling so it doesn't hide the mini player.
        if isAutoScrolling {
            previousScrollOffset = newOffset
            scrollOffset = newOffset
            return
        }
        let scrollDelta = newOffset - previousScrollOffset

        // Update global audio player visibility based on scroll direction
        // Require minimum scroll delta to avoid flickering
        if abs(scrollDelta) > 8 {
            if scrollDelta > 0 && newOffset > 50 {
                // Scrolling down - hide global audio player
                audioManager.hidePlayerByScroll()
            } else if scrollDelta < -8 {
                // Scrolling up - show global audio player
                audioManager.showPlayerAfterScroll()
            }
            previousScrollOffset = newOffset
        }

        scrollOffset = newOffset
    }

    // MARK: - Audio-driven Completion

    /// Mark cores complete as the narration plays past them. No-op when this book isn't the active
    /// audio, or on seeks (markListenedThrough guards continuity). Fires a success haptic per core.
    private func autoCompleteListenedCores(from old: Double, to new: Double) {
        guard audioManager.currentEpisode?.id == book.audioEpisode.id,
              let info = book.bookAudioInfo else { return }
        var newly: [Int] = []
        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
            newly = progress.markListenedThrough(
                order: book.curriculumOrder, from: old, to: new,
                coreStarts: info.coreStartSeconds, totalSeconds: info.totalSeconds)
        }
        if !newly.isEmpty {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }

    /// While playing, if the narration crosses out of the core currently on screen, advance the
    /// reading view to the next core so the highlight keeps following the voice.
    ///
    /// It ONLY advances when the playhead leaves the core being viewed — so if the learner has
    /// navigated elsewhere (another core, or any other screen) it never snaps them back. It resumes
    /// naturally: once the playhead reaches whatever core they're on, the highlight shows, and when
    /// it then passes that core, auto-advance picks up again.
    private func autoAdvanceReading(from old: Double, to new: Double) {
        guard audioManager.isPlaying,
              audioManager.currentEpisode?.id == book.audioEpisode.id,
              let info = book.bookAudioInfo,
              new > old, new - old < 2.0 else { return }
        let shown = currentContent.chapterNumber
        // End of the core on screen == start of the next core. nil => it's the last core.
        guard let nextStart = info.coreStartSeconds[shown + 1].map(Double.init) else { return }
        if old < nextStart, nextStart <= new {
            navigateToNextCore()
        }
    }

    /// The book file played to the very end → mark the last core complete.
    private func autoCompleteFinalCore() {
        guard let last = allCores.last,
              !progress.isCompleted(order: book.curriculumOrder, core: last.number) else { return }
        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
            progress.markCompleted(order: book.curriculumOrder, core: last.number)
        }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    // MARK: - Completion Handling
    private func handleCoreCompletion() {
        // Don't re-complete if already done
        guard !isCurrentCoreCompleted else { return }

        // Trigger success haptic feedback
        let generator = UINotificationFeedbackGenerator()
        generator.notificationOccurred(.success)

        // Mark current core as completed (persists locally + syncs to backend).
        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
            progress.markCompleted(order: book.curriculumOrder, core: currentContent.chapterNumber)
        }

        // If there's a next core, navigate to it after a delay
        if hasNextCore {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                navigateToNextCore()
            }
        }
    }

    // MARK: - Actions
    private func handleCloseTapped() {
        print("🔴 DEBUG: Close button tapped")
        dismiss()
    }
    
    private func handleBackTapped() {
        print("🔵 DEBUG: Back button tapped")
        guard hasPreviousCore else {
            dismiss()
            return
        }
        navigateToPreviousCore()
    }
    
    private func handleNextTapped() {
        print("🟢 DEBUG: Next button tapped")
        guard hasNextCore else { return }
        navigateToNextCore()
    }
    
    private func navigateToPreviousCore() {
        let previousIndex = currentCoreIndex - 1
        guard previousIndex >= 0, let newContent = allCores[previousIndex].getDetailContent(for: book) else { return }
        withAnimation(.easeInOut(duration: 0.3)) {
            currentContent = newContent
        }
        // Reset scroll position and show audio player
        scrollOffset = 0
        previousScrollOffset = 0
        audioManager.resetScrollHiding()
    }

    /// Jump the reading view to a specific core — used by the full-screen player's "Read" button to
    /// snap back to the core the narration is currently in (the read-along highlight resumes there).
    private func jumpReadingToCore(_ number: Int) {
        guard number != currentContent.chapterNumber,
              let chapter = allCores.first(where: { $0.number == number }),
              let content = chapter.getDetailContent(for: book) else { return }
        withAnimation(.easeInOut(duration: 0.3)) {
            currentContent = content
        }
        scrollOffset = 0
        previousScrollOffset = 0
        audioManager.resetScrollHiding()
    }

    private func navigateToNextCore() {
        let nextIndex = currentCoreIndex + 1
        guard nextIndex < allCores.count, let newContent = allCores[nextIndex].getDetailContent(for: book) else { return }
        withAnimation(.easeInOut(duration: 0.3)) {
            currentContent = newContent
        }
        // Reset scroll position and show audio player
        scrollOffset = 0
        previousScrollOffset = 0
        audioManager.resetScrollHiding()
    }
    
    private func handleAISend() {
        guard !inputText.isEmpty else { return }
        print("Ask AI about chapter: \(inputText)")
        inputText = ""
    }
}

// MARK: - Scroll Offset Preference Key
private struct CoreDetailScrollOffsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

// MARK: - Header Section
private struct CoreDetailHeaderSection: View {
    @EnvironmentObject private var audioManager: AudioManager
    let content: CoreChapterContent
    let book: LibraryBook
    var onPlayStarted: (() -> Void)?

    // One narration file for the whole book; this core is the segment starting at coreStart.
    private var bookEpisode: AudioEpisode { book.audioEpisode }

    private var coreStart: TimeInterval { TimeInterval(book.coreStartSeconds(content.chapterNumber) ?? 0) }

    private var coreEnd: TimeInterval {
        if let next = book.coreStartSeconds(content.chapterNumber + 1) { return TimeInterval(next) }
        return TimeInterval(book.bookAudioInfo?.totalSeconds ?? Int(coreStart))
    }

    // "This core" is playing when the book narration is the active episode, it's playing, and the
    // playhead sits within this core's [start, nextStart) window.
    private var isThisCoreAudioPlaying: Bool {
        guard let current = audioManager.currentEpisode,
              current.id == bookEpisode.id, audioManager.isPlaying else { return false }
        let t = audioManager.currentTime
        return t >= coreStart - 0.5 && t < coreEnd
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Chapter badge with play button
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "book.fill")
                    .font(AppTypography.iconXS).fontWeight(.semibold)
                    .foregroundColor(book.level.color)

                Text(content.formattedChapterLabel.uppercased())
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(book.level.color)
                    .tracking(0.8)

                Spacer()

                // Direct Play button
                Button(action: handleDirectPlay) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: isThisCoreAudioPlaying ? "pause.circle.fill" : "play.circle.fill")
                            .font(AppTypography.iconXL).fontWeight(.medium)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Chapter title
            Text(content.chapterTitle)
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
                .padding(.top, AppSpacing.sm)
        }
    }

    private func handleDirectPlay() {
        if isThisCoreAudioPlaying {
            // Pause if this core is currently playing
            audioManager.togglePlayPause()
        } else {
            // Play the whole-book narration, seeking to this core's start offset
            audioManager.play(bookEpisode, startAt: coreStart)
            // Force show the global audio player
            audioManager.resetScrollHiding()
            // Notify parent that playback started
            onPlayStarted?()
        }
    }
}

// MARK: - Section View
private struct CoreSectionView: View {
    let section: CoreChapterSection

    var body: some View {
        switch section.content {
        case .text(let text):
            if section.type == .heading {
                CoreHeadingView(text: text)
            } else {
                CoreParagraphView(text: text)
            }

        case .richText(let attributedString):
            Text(attributedString)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(6)

        case .quote(let quote):
            CoreQuoteView(quote: quote)

        case .assetList(let assets):
            CoreAssetListView(assets: assets, title: section.title)

        case .actionPlan(let steps):
            CoreActionPlanView(steps: steps)

        case .bulletPoints(let points):
            CoreBulletPointsView(points: points, title: section.title)

        case .callout(let callout):
            CoreCalloutView(callout: callout)
        }
    }
}

// MARK: - Heading View
private struct CoreHeadingView: View {
    let text: String

    var body: some View {
        Text(text)
            .font(AppTypography.titleCompact)
            .foregroundColor(AppColors.textPrimary)
            .padding(.top, AppSpacing.md)
    }
}

// MARK: - Paragraph View
private struct CoreParagraphView: View {
    let text: String

    var body: some View {
        Text(text)
            .font(AppTypography.body)
            .foregroundColor(AppColors.textSecondary)
            .lineSpacing(6)
            .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - Quote View
private struct CoreQuoteView: View {
    let quote: QuoteContent

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Quote icon
            Image(systemName: "quote.opening")
                .font(AppTypography.iconXL).fontWeight(.bold)
                .foregroundColor(AppColors.accentCyan.opacity(0.6))

            // Quote text
            Text(quote.text)
                .font(.system(size: 16, weight: .medium, design: .serif))
                .foregroundColor(AppColors.textPrimary)
                .lineSpacing(6)
                .italic()

            // Attribution
            HStack(spacing: AppSpacing.xs) {
                Rectangle()
                    .fill(AppColors.accentCyan)
                    .frame(width: 24, height: 2)

                Text(quote.author)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.accentCyan)

                if let source = quote.source {
                    Text(",")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textMuted)

                    Text(source)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .italic()
                }
            }
        }
        .padding(AppSpacing.xl)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Asset List View
private struct CoreAssetListView: View {
    let assets: [AssetCategory]
    let title: String?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            if let title = title {
                Text(title)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(spacing: AppSpacing.md) {
                ForEach(assets) { asset in
                    CoreAssetCard(asset: asset)
                }
            }
        }
    }
}

// MARK: - Asset Card
private struct CoreAssetCard: View {
    let asset: AssetCategory

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.lg) {
            // Icon
            ZStack {
                Circle()
                    .fill(Color(hex: asset.iconColor).opacity(0.15))
                    .frame(width: 44, height: 44)

                Image(systemName: asset.icon)
                    .font(AppTypography.iconMedium).fontWeight(.semibold)
                    .foregroundColor(Color(hex: asset.iconColor))
            }

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(asset.title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(asset.description)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - Action Plan View
private struct CoreActionPlanView: View {
    let steps: [ActionStep]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            ForEach(steps) { step in
                CoreActionStepCard(step: step)
            }
        }
    }
}

// MARK: - Action Step Card
private struct CoreActionStepCard: View {
    let step: ActionStep

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Title with indicator
            HStack(alignment: .top, spacing: AppSpacing.md) {
                // Checkbox indicator
                ZStack {
                    Circle()
                        .strokeBorder(
                            step.isCompleted ? AppColors.bullish : AppColors.primaryBlue,
                            lineWidth: 2
                        )
                        .frame(width: 24, height: 24)

                    if step.isCompleted {
                        Image(systemName: "checkmark")
                            .font(AppTypography.iconXS).fontWeight(.bold)
                            .foregroundColor(AppColors.bullish)
                    }
                }

                Text(step.title)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Description
            Text(step.description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
                .padding(.leading, 24 + AppSpacing.md)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Bullet Points View
private struct CoreBulletPointsView: View {
    let points: [BulletPoint]
    let title: String?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            if let title = title {
                Text(title)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(alignment: .leading, spacing: AppSpacing.md) {
                ForEach(points) { point in
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        Circle()
                            .fill(point.isHighlighted ? AppColors.accentCyan : AppColors.textMuted)
                            .frame(width: 6, height: 6)
                            .padding(.top, 6)

                        Text(point.text)
                            .font(point.isHighlighted ? AppTypography.bodyEmphasis : AppTypography.body)
                            .foregroundColor(point.isHighlighted ? AppColors.textPrimary : AppColors.textSecondary)
                            .lineSpacing(4)
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Callout View
private struct CoreCalloutView: View {
    let callout: CalloutContent

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: callout.style.iconName)
                .font(AppTypography.iconMedium).fontWeight(.semibold)
                .foregroundColor(callout.style.iconColor)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(callout.title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(callout.text)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
            }
        }
        .padding(AppSpacing.lg)
        .background(callout.style.backgroundColor)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - Navigation Header
private struct CoreDetailNavigationHeader: View {
    let hasPrevious: Bool
    let hasNext: Bool
    let onBackTapped: () -> Void
    let onCloseTapped: () -> Void
    let onNextTapped: () -> Void

    var body: some View {
        HStack {
            // Back button (left)
            Button(action: onBackTapped) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)

                    Text(hasPrevious ? "Prev" : "Back")
                        .font(AppTypography.bodyEmphasis)
                }
                .foregroundColor(AppColors.textPrimary)
                .frame(height: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            Spacer()
            
            // Close button (center)
            Button(action: onCloseTapped) {
                Image(systemName: "xmark")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())
            
            Spacer()
            
            // Next button (right)
            if hasNext {
                Button(action: onNextTapped) {
                    HStack(spacing: AppSpacing.sm) {
                        Text("Next")
                            .font(AppTypography.bodyEmphasis)
                        
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconDefault).fontWeight(.semibold)
                    }
                    .foregroundColor(AppColors.textPrimary)
                    .frame(height: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
            } else {
                // Invisible spacer to balance layout
                Color.clear
                    .frame(width: 80, height: 44)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
        .background(
            AppColors.background
                .ignoresSafeArea(edges: .top)
        )
    }
}

// MARK: - Mini Header
private struct CoreDetailMiniHeader: View {
    let content: CoreChapterContent
    let hasPrevious: Bool
    let hasNext: Bool
    let onBackTapped: () -> Void
    let onCloseTapped: () -> Void
    let onNextTapped: () -> Void

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Back button
            Button(action: onBackTapped) {
                Image(systemName: "chevron.left")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            // Chapter info
            VStack(alignment: .leading, spacing: 2) {
                Text(content.formattedChapterLabel)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)

                Text(content.chapterTitle)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
            }

            Spacer()
            
            // Close button
            Button(action: onCloseTapped) {
                Image(systemName: "xmark")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())
            
            // Next button
            if hasNext {
                Button(action: onNextTapped) {
                    HStack(spacing: AppSpacing.xs) {
                        Text("Next")
                            .font(AppTypography.bodyEmphasis)
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconDefault).fontWeight(.semibold)
                    }
                    .foregroundColor(AppColors.textPrimary)
                    .frame(height: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(
            AppColors.background
                .shadow(color: Color.black.opacity(0.2), radius: 4, y: 2)
                .ignoresSafeArea(edges: .top)
        )
    }
}

// MARK: - Completion Button
private struct CoreCompletionButton: View {
    let isCompleted: Bool
    let hasNextCore: Bool
    let onComplete: () -> Void

    var body: some View {
        Button(action: {
            if !isCompleted {
                onComplete()
            }
        }) {
            HStack(spacing: AppSpacing.md) {
                Text(isCompleted ? "Review Again" : "Complete & Continue")
                    .font(AppTypography.bodyEmphasis)

                Image(systemName: isCompleted ? "arrow.counterclockwise" : "arrow.right")
                    .font(AppTypography.iconSmall).fontWeight(.semibold)
            }
            .foregroundColor(isCompleted ? AppColors.textSecondary : .white)
            .frame(maxWidth: .infinity)
            .frame(height: 56)
            .background(
                Group {
                    if isCompleted {
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .strokeBorder(AppColors.textMuted, lineWidth: 1.5)
                    } else {
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .fill(AppColors.primaryBlue)
                    }
                }
            )
        }
        .buttonStyle(PlainButtonStyle())
        .animation(.easeInOut(duration: 0.25), value: isCompleted)
    }
}

// MARK: - Preview
#Preview {
    @Previewable @StateObject var audioManager = AudioManager.shared

    BookCoreDetailView(
        content: .sampleFinancialScorecard,
        book: LibraryBook.sampleData[0]
    )
    .environmentObject(audioManager)
    .preferredColorScheme(.dark)
}
