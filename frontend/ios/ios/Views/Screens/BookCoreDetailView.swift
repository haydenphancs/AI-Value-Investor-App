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

    // Completion tracking
    @State private var completedCoreNumbers: Set<Int> = []
    @State private var audioCompletionCancellable: AnyCancellable?

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

    private var isCurrentCoreCompleted: Bool {
        // Check if already completed by user during this session, or previously completed (chapter <= currentChapter)
        completedCoreNumbers.contains(currentContent.chapterNumber) ||
        currentContent.chapterNumber < book.currentChapter
    }

    // Computed property for header opacity based on scroll
    private var headerOpacity: Double {
        let fadeStart: CGFloat = 60
        let fadeEnd: CGFloat = 120
        if scrollOffset < fadeStart { return 0 }
        if scrollOffset > fadeEnd { return 1 }
        return Double((scrollOffset - fadeStart) / (fadeEnd - fadeStart))
    }

    // Audio episode for the current chapter
    private var currentAudioEpisode: AudioEpisode {
        AudioEpisode(
            id: "book-\(book.id.uuidString)-core-\(content.chapterNumber)",
            title: content.chapterTitle,
            subtitle: "\(content.bookTitle) - Core \(content.chapterNumber)",
            artworkGradientColors: [book.coverGradientStart, book.coverGradientEnd],
            artworkIcon: "book.fill",
            duration: TimeInterval(content.audioDurationSeconds),
            category: .books,
            authorName: content.bookAuthor,
            sourceId: book.id.uuidString
        )
    }

    var body: some View {
        ZStack(alignment: .top) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main scrollable content
            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {
                    // Header spacer for back button area
                    Color.clear
                        .frame(height: 60)

                    // Chapter header
                    CoreDetailHeaderSection(content: content, book: book)
                        .padding(.horizontal, AppSpacing.lg)

                    // Content sections
                    LazyVStack(alignment: .leading, spacing: AppSpacing.xxl) {
                        ForEach(content.sections) { section in
                            CoreSectionView(section: section)
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
                CoreDetailAskAIBar(inputText: $inputText, onSend: handleAISend)
            }
        }
        .navigationBarHidden(true)
        .onAppear {
            // Load the audio episode when view appears (paused)
            audioManager.load(currentAudioEpisode)

            // Subscribe to audio completion events
            audioCompletionCancellable = audioManager.playbackDidComplete
                .receive(on: DispatchQueue.main)
                .sink { [self] completedEpisode in
                    // Check if the completed episode matches the current core's audio
                    if completedEpisode.id == currentAudioEpisode.id {
                        handleCoreCompletion()
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
        .onChange(of: currentContent.chapterNumber) { _ in
            // Load new episode when navigating between chapters (paused)
            audioManager.load(currentAudioEpisode)
        }
    }

    // MARK: - Scroll Handling
    private func handleScrollChange(newOffset: CGFloat) {
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

    // MARK: - Completion Handling
    private func handleCoreCompletion() {
        // Don't re-complete if already done
        guard !isCurrentCoreCompleted else { return }

        // Trigger success haptic feedback
        let generator = UINotificationFeedbackGenerator()
        generator.notificationOccurred(.success)

        // Mark current core as completed
        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
            completedCoreNumbers.insert(currentContent.chapterNumber)
        }

        // If there's a next core, navigate to it after a delay
        if hasNextCore {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
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

    // Audio episode for this chapter
    private var audioEpisode: AudioEpisode {
        AudioEpisode(
            id: "book-\(book.id.uuidString)-core-\(content.chapterNumber)",
            title: content.chapterTitle,
            subtitle: "\(content.bookTitle) - Core \(content.chapterNumber)",
            artworkGradientColors: [book.coverGradientStart, book.coverGradientEnd],
            artworkIcon: "book.fill",
            duration: TimeInterval(content.audioDurationSeconds),
            category: .books,
            authorName: content.bookAuthor,
            sourceId: book.id.uuidString
        )
    }

    private var isThisCoreAudioPlaying: Bool {
        guard let currentEpisode = audioManager.currentEpisode else { return false }
        return currentEpisode.id == audioEpisode.id && audioManager.isPlaying
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Chapter badge with play button
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "book.fill")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(book.level.color)

                Text(content.formattedChapterLabel.uppercased())
                    .font(AppTypography.captionBold)
                    .foregroundColor(book.level.color)
                    .tracking(0.8)

                Spacer()

                // Direct Play button
                Button(action: handleDirectPlay) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: isThisCoreAudioPlaying ? "pause.circle.fill" : "play.circle.fill")
                            .font(.system(size: 28, weight: .medium))
                            .foregroundColor(AppColors.primaryBlue)
                    }
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Chapter title
            Text(content.chapterTitle)
                .font(AppTypography.largeTitle)
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
            // Pause if already playing this core
            audioManager.togglePlayPause()
        } else {
            // Start playing this core's audio
            audioManager.play(audioEpisode)
            // Force show the global audio player
            audioManager.resetScrollHiding()
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
            .font(AppTypography.title2)
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
                .font(.system(size: 24, weight: .bold))
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
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.accentCyan)

                if let source = quote.source {
                    Text(",")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textMuted)

                    Text(source)
                        .font(AppTypography.callout)
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
                    .font(AppTypography.headline)
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
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(Color(hex: asset.iconColor))
            }

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(asset.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(asset.description)
                    .font(AppTypography.callout)
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
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(AppColors.bullish)
                    }
                }

                Text(step.title)
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Description
            Text(step.description)
                .font(AppTypography.callout)
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
                    .font(AppTypography.headline)
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
                            .font(point.isHighlighted ? AppTypography.bodyBold : AppTypography.body)
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
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(callout.style.iconColor)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(callout.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(callout.text)
                    .font(AppTypography.callout)
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
                        .font(.system(size: 16, weight: .semibold))

                    Text(hasPrevious ? "Prev" : "Back")
                        .font(AppTypography.bodyBold)
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
                    .font(.system(size: 16, weight: .semibold))
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
                            .font(AppTypography.bodyBold)
                        
                        Image(systemName: "chevron.right")
                            .font(.system(size: 16, weight: .semibold))
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
                    .font(.system(size: 16, weight: .semibold))
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
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
            }

            Spacer()
            
            // Close button
            Button(action: onCloseTapped) {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .semibold))
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
                            .font(AppTypography.bodyBold)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 16, weight: .semibold))
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

// MARK: - Ask AI Bar
private struct CoreDetailAskAIBar: View {
    @Binding var inputText: String
    let onSend: () -> Void

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // AI icon
            Image(systemName: "sparkles")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(AppColors.accentCyan)

            // Text field
            TextField("Ask Caudex AI...", text: $inputText)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .submitLabel(.send)
                .onSubmit(onSend)

            Spacer()

            // Send button
            Button(action: onSend) {
                Image(systemName: "paperplane.fill")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(inputText.isEmpty ? AppColors.textMuted : AppColors.primaryBlue)
            }
            .buttonStyle(PlainButtonStyle())
            .disabled(inputText.isEmpty)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.extraLarge)
        .padding(.horizontal, AppSpacing.lg)
        .padding(.bottom, AppSpacing.lg)
        .background(
            LinearGradient(
                colors: [
                    AppColors.background.opacity(0),
                    AppColors.background.opacity(0.95),
                    AppColors.background
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
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
                    .font(AppTypography.bodyBold)

                Image(systemName: isCompleted ? "arrow.counterclockwise" : "arrow.right")
                    .font(.system(size: 14, weight: .semibold))
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
