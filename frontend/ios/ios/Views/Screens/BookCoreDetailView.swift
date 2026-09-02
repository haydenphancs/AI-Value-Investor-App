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

    /// True while the whole-book finale is up. It also FREEZES the reader's content — see the
    /// guards in navigateToNextCore / navigateToPreviousCore / jumpReadingToCore.
    @State private var showBookFinale = false
    @State private var bookFinishedCancellable: AnyCancellable?

    // Track if user initiated playback from this view or if it was already playing
    @State private var shouldShowPlayer: Bool = false

    // True while we're programmatically scrolling — following the narration, or jumping to the top
    // on a core switch — so the scroll-driven player hide/show logic ignores it (our own scrolling
    // shouldn't hide the mini player).
    @State private var isAutoScrolling: Bool = false

    /// Monotonic token for programmatic scrolls. Every `beginProgrammaticScroll` bumps it, and the
    /// clear it schedules only fires if it still owns the newest token. Without this, two scrolls
    /// closer together than the settle window (Next-Next taps; a read-along hop landing on a core
    /// switch) let the EARLIER one's clear unset the flag mid-flight of the LATER one — re-arming
    /// `hidePlayerByScroll()` against a scroll the user never made.
    @State private var autoScrollGeneration: Int = 0

    /// Set while a core switch settles. The scroll view is already back at the top, but the offset
    /// PreferenceKey keeps reporting the OLD mid-page value for a frame or two while the new core
    /// lays out. Believing it drives `headerOpacity` 0 → 1 → 0 and flashes the sticky mini header in
    /// and straight back out, so we pin the reported offset to 0 across that window.
    @State private var isPinnedToTop: Bool = false

    /// Scroll anchor for "the very top of the reader" — see the header spacer in `body`.
    private static let coreTopAnchorID = "coreTop"

    /// Stable token keying this screen's compact-mode requests + audio overlay host registration.
    @State private var compactToken = UUID().uuidString
    /// Owns the chat conversation for this core so it resumes while the screen is open.
    @StateObject private var chatViewModel = ChatViewModel()
    @State private var showAIChat = false

    let book: LibraryBook
    let allCores: [BookCoreChapter]

    /// Called when the learner taps "Thank you" on the whole-book finale. The presenter is expected
    /// to close THIS reader and then, once that dismissal has actually finished, close the book
    /// detail cover underneath it — landing the learner back on whatever opened the book.
    /// `nil` (previews, or a future entry point that doesn't wire it) degrades to closing just this.
    let onFinishedBook: (() -> Void)?

    init(content: CoreChapterContent, book: LibraryBook, onFinishedBook: (() -> Void)? = nil) {
        self.book = book
        self.allCores = book.coreChapters
        self.onFinishedBook = onFinishedBook
        self._currentContent = State(initialValue: content)
    }

    private var currentCoreIndex: Int {
        allCores.firstIndex { $0.number == currentContent.chapterNumber } ?? 0
    }

    /// Every core number in this book — the roster `BookProgressStore` judges "the book is
    /// finished" against. Passed to every completion write this screen makes.
    private var coreNumbers: [Int] { allCores.map(\.number) }
    
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

    /// Read-along block for each content SECTION index, matched by text in render order — NOT by raw
    /// position. The read-along table drops non-narrated sections (e.g. the "The Action Plan"
    /// heading), so `content.sections[i]` is NOT always `readAlongBlocks[i]`: when two headings sit
    /// consecutively before the action plan, the dropped heading shifts every later block by one, so
    /// a positional pairing renders the WRONG heading, drops one, and duplicates the last paragraph
    /// (seen in book4/core6, book6/core12, book9/core3). Walking both lists and consuming a block
    /// only when its text matches the section keeps every section on its OWN text; a narrated
    /// section that fails to match simply renders without the live highlight (never someone else's
    /// text).
    private var readAlongBySection: [Int: ReadAlongBlock] {
        let blocks = readAlongBlocks
        guard !blocks.isEmpty else { return [:] }
        var map: [Int: ReadAlongBlock] = [:]
        var b = 0
        for (i, section) in content.sections.enumerated() {
            guard b < blocks.count, let text = Self.narratedText(of: section) else { continue }
            if Self.readAlongKey(text) == Self.readAlongKey(blocks[b].joinedText) {
                map[i] = blocks[b]
                b += 1
            }
        }
        return map
    }

    /// The text a section contributes to the narration (heading/paragraph); nil for non-text blocks
    /// (quote/action-plan/etc.) which never carry read-along timings.
    private static func narratedText(of section: CoreChapterSection) -> String? {
        if case let .text(t) = section.content { return t }
        return nil
    }

    /// Match key: alphanumerics only, lowercased — robust to whitespace / punctuation / smart-quote
    /// differences between the authored section text and the force-aligned sentence splits.
    private static func readAlongKey(_ s: String) -> String {
        String(String.UnicodeScalarView(s.lowercased().unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) }))
    }

    /// SECTION index of the block currently being read (drives auto-scroll), if any.
    ///
    /// Takes the LOWEST matching section rather than `.first`: `readAlongBySection` is a
    /// Dictionary, whose iteration order is unspecified, and spans are allowed to overlap
    /// (`ReadAlongSentence` deliberately tolerates out-of-order/degenerate timings instead of
    /// rejecting them). With `.first`, two blocks matching the same instant could each win on
    /// different ticks and the view would oscillate between them mid-sentence.
    private var activeReadAlongSection: Int? {
        guard let t = readAlongActiveTime else { return nil }
        return readAlongBySection
            .filter { _, block in block.sentences.contains { t >= $0.start && t < $0.end } }
            .keys.min()
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
    //
    // Entitlement-gated before anything else: `load()` starts BUFFERING, so a locked account
    // must not reach it, and must not spend a request resolving a URL it can't use either.
    private func ensureBookEpisodeLoaded() {
        guard LearnAudioEntitlement.shared.isUnlocked else { return }
        guard audioManager.currentEpisode?.id != currentAudioEpisode.id else { return }
        Task {
            // Prepare-only, so this one stays SILENT on failure by design: the user didn't ask
            // for anything, and the play sites resolve on demand. Nothing is lost but a warm
            // start. `try?` is deliberate here and banned at the two tap sites above.
            guard let episode = try? await book.playableAudioEpisode() else { return }
            guard audioManager.currentEpisode?.id != episode.id else { return }
            // ⚠️ ONLY warm-start from a local mirror. `load()` → `preparePlayer()` starts
            // buffering, so on a cache miss this spent up to 45 MB of Storage egress for someone
            // who opened a core purely to READ — and it re-fired on every `.onAppear` and every
            // chapter change, with the per-book guard defeated by switching books, `stop()`, or
            // any relaunch. Off a cached file the prepare is free, so the warm start survives
            // exactly where it costs nothing.
            guard let urlString = episode.audioUrl, let url = URL(string: urlString),
                  LearnAudioCache.shared.cachedFile(for: url) != nil else { return }
            audioManager.load(episode)
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
                    // Header spacer for back button area. Carries the scroll anchor for
                    // `scrollToTop`: it's a child of this plain VStack, NOT of the LazyVStack below,
                    // so it is always realized and `scrollTo` can reach it even when the reader is
                    // parked at the bottom of a long core with every section row recycled away.
                    // (`Color.clear` holds no state, so giving it an explicit identity is free.)
                    Color.clear
                        .frame(height: 60)
                        .id(Self.coreTopAnchorID)

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
                        let blockBySection = readAlongBySection
                        ForEach(Array(content.sections.enumerated()), id: \.element.id) { index, section in
                            if let block = blockBySection[index] {
                                ReadAlongBlockView(
                                    block: block,
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
            .onChange(of: activeReadAlongSection) { _, newValue in
                // Follow the narration: gently center the block being read, without tripping the
                // scroll-driven player hide. Only while actually playing.
                guard audioManager.isPlaying, let idx = newValue else { return }
                beginProgrammaticScroll(settleAfter: 0.6)
                withAnimation(.easeInOut(duration: 0.4)) {
                    // The FIRST narrated block sits directly under the chapter header, so centering
                    // it shoves the header off screen the moment a core starts being read. Anchor to
                    // the top instead. This also makes the auto-advance case order-independent: when
                    // the narration crosses into the next core, THIS handler and the core-switch
                    // handler below both fire in the same update pass with no defined ordering — and
                    // now they agree on the destination instead of fighting over it.
                    if idx == readAlongBySection.keys.min() {
                        scrollProxy.scrollTo(Self.coreTopAnchorID, anchor: .top)
                    } else {
                        scrollProxy.scrollTo("readAlongBlock-\(idx)", anchor: .center)
                    }
                }
            }
            .onChange(of: currentContent.chapterNumber) { _, _ in
                // A new core must open at the TOP. `currentContent` is swapped IN PLACE, so the
                // ScrollView keeps its identity and therefore its content offset — without this, the
                // next core opens scrolled to wherever the previous one was left (which, since
                // "Complete & Continue" sits at the very bottom, is every single time).
                //
                // This lives INSIDE the ScrollViewReader closure because it needs `scrollProxy`; the
                // sibling `.onChange(of: currentContent.chapterNumber)` on the outer ZStack (audio
                // loading) has no access to it. Keying on the core number rather than calling from
                // each navigate function covers EVERY path in one place: Next / Prev,
                // "Complete & Continue", the player's "Read" jump, and the narration auto-advance.
                beginProgrammaticScroll(pinTop: true, settleAfter: 0.35)
                scrollToTop(scrollProxy)

                // The core swapped underneath VoiceOver's focused element; without an explicit
                // screen-change notification the focus lands non-deterministically.
                UIAccessibility.post(notification: .screenChanged, argument: currentContent.chapterTitle)

                // Re-baseline the scroll-derived UI here rather than in the three navigate functions:
                // there, the next preference tick (still reporting the OLD offset, because the scroll
                // view hasn't physically moved yet) overwrote these immediately and flashed the
                // sticky mini header in and back out. `isPinnedToTop` is what makes the reset stick.
                scrollOffset = 0
                previousScrollOffset = 0
                audioManager.resetScrollHiding()
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
                    GlobalMiniPlayer(bottomSpacing: AppSpacing.md / 2)
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
        // Attached to the outer ZStack, so everything INSIDE it — including the bottom mini player
        // and the Ask Cay AI bar — sits under the scrim, while the audio chrome added below (the
        // top status island and the full-screen player) stays above it. That's the right split:
        // the island only appears while the chat bar is focused, which is impossible behind a
        // modal scrim, and the full-screen player is only up if the learner opened it deliberately.
        .overlay { bookFinaleOverlay }
        // Top status island + full-screen player + overlay-host registration (this screen is a
        // fullScreenCover above RootContainerView, whose own overlay would be hidden). "Read" jumps
        // the reading view to the core the narration is currently in.
        .globalAudioOverlay(token: compactToken, onNavigateToCore: { coreNumber in
            jumpReadingToCore(coreNumber)
        })
        .navigationBarHidden(true)
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
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

            // The book's LAST remaining core was just completed — by the button, by the narration
            // reaching the end of the file, or by the narration playing through it. The store
            // raises this from the one place all three write, so it doesn't matter which view
            // performed the write (BookDetailView drives markListenedThrough on the same ticks we
            // do, in an order SwiftUI doesn't define). Assigning to a single cancellable rather
            // than a Set is deliberate: .onAppear can fire more than once, and reassignment
            // cancels the previous subscription, so a double-appear can't double-subscribe.
            bookFinishedCancellable = progress.didFinishBook
                .receive(on: DispatchQueue.main)
                .sink { [self] order in
                    guard order == book.curriculumOrder else { return }
                    presentBookFinale()
                }
        }
        .onDisappear {
            // Reset scroll hiding when leaving the view
            audioManager.resetScrollHiding()
            // Cancel audio completion subscription
            audioCompletionCancellable?.cancel()
            audioCompletionCancellable = nil
            bookFinishedCancellable?.cancel()
            bookFinishedCancellable = nil
        }
        .onChange(of: currentContent.chapterNumber) {
            // Same single book file across cores — only load if it isn't already active, so
            // swiping between cores never interrupts ongoing playback.
            //
            // NOTE: the scroll-to-top on a core switch is a SEPARATE handler on the same value,
            // inside the ScrollViewReader closure above (it needs `scrollProxy`, which doesn't
            // reach out here). Don't consolidate the two — this one can't move inside the reader
            // without dragging `.globalAudioOverlay` / `.aiChatCover` scope with it.
            ensureBookEpisodeLoaded()
        }
        .onChange(of: audioManager.currentTime) { oldTime, newTime in
            // Auto-complete cores as the narration plays through them (no button tap needed).
            autoCompleteListenedCores(from: oldTime, to: newTime)
            // Follow the audio: if it leaves the core we're viewing, open the next one.
            autoAdvanceReading(from: oldTime, to: newTime)
        }
        // Narration is Pro/Max: the audio ENGINE refuses a locked episode and asks for
        // an upgrade, so this presenter is what turns that into the plan sheet. Needed on
        // each screen because these are fullScreenCovers — a modifier on the presenter
        // does not reach them.
        .learnAudioPaywall()
    }

    // MARK: - Whole-book finale

    @ViewBuilder
    private var bookFinaleOverlay: some View {
        if showBookFinale {
            ZStack {
                // Deliberately NOT tap-to-dismiss: the CTA is the only exit, and the reader behind
                // this is about to be torn down anyway. contentShape + an empty gesture stops stray
                // taps from landing on the page underneath.
                AppColors.scrim
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture {}
                    .accessibilityHidden(true)

                BookFinishedCard(
                    bookTitle: book.title,
                    message: "All \(allCores.count) cores, start to finish. It's marked mastered in your library.",
                    ctaTitle: "Thank you",
                    onCTATapped: handleFinaleThankYou
                )
            }
            .transition(.opacity)
            // Hides the whole reader from VoiceOver, so swiping can't wander behind the card.
            .accessibilityAddTraits(.isModal)
            .zIndex(50)
        }
    }

    private func presentBookFinale() {
        guard !showBookFinale else { return }

        // Quiet the narration. A voice reading on under a "you finished the book" card is jarring,
        // and this is reachable MID-FILE when the book is finished out of order (the
        // markListenedThrough path), not only at the end. `pause()` — never `stop()` — keeps the
        // episode and its position loaded, so the mini player survives the dismiss and can resume.
        if audioManager.isPlaying, audioManager.currentEpisode?.id == book.audioEpisode.id {
            audioManager.pause()
        }

        withAnimation(.easeOut(duration: 0.25)) {
            showBookFinale = true
        }

        // The per-core success haptic already fired in whichever completion path got here; this
        // second, heavier bump lands with the ring closing, so the two read as "core done ✓ …
        // book done". Both are gated by the user's haptics setting inside Haptics.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { Haptics.impact(.heavy) }

        // The card is modal — move VoiceOver onto it rather than leaving focus on a paragraph
        // behind the scrim.
        UIAccessibility.post(notification: .screenChanged, argument: "You finished \(book.title)")
    }

    private func handleFinaleThankYou() {
        guard let onFinishedBook else {
            // Nobody wired the two-cover dismissal (previews, or a future entry point):
            // at least close the reader rather than trapping the learner behind the scrim.
            dismiss()
            return
        }
        onFinishedBook()
    }

    // MARK: - Scroll Handling

    /// Jump the reader to the very top, unanimated.
    ///
    /// The callers mutate `currentContent` inside `withAnimation(.easeInOut(duration: 0.3))`, and the
    /// `.onChange` action runs inside that same transaction — so a bare `scrollTo` INHERITS the 0.3s
    /// curve and the page visibly races upward past every paragraph instead of cutting.
    /// `withTransaction` REPLACES the ambient transaction (it doesn't merge), so an empty one clears
    /// the inherited animation; `disablesAnimations` is the flag the UIScrollView behind SwiftUI
    /// reads, matching the idiom in TickerChartView / GrowthChartSheet / ChartSettingsSheet.
    ///
    /// Called twice on purpose (same shape as EarningsTimelineChart): the synchronous call lands
    /// before the frame is committed when layout is already valid, and the `main.async` retry covers
    /// the case where the new core's content is still being rebuilt. Because the anchor is the FIRST
    /// child, both calls resolve to the same destination (offset 0) no matter how tall the new core
    /// is — so the retry can never fight the first call, and a shorter core's contentOffset clamp
    /// (which clamps toward 0) can't fight it either.
    private func scrollToTop(_ proxy: ScrollViewProxy) {
        func jump() {
            var transaction = Transaction()
            transaction.animation = nil
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                proxy.scrollTo(Self.coreTopAnchorID, anchor: .top)
            }
        }
        jump()
        DispatchQueue.main.async { jump() }
    }

    /// Open a window in which reported scroll offsets are treated as OURS, not the user's: the mini
    /// player never hides, and when `pinTop` the reported offset is ignored in favour of 0.
    ///
    /// The generation token is what makes overlapping scrolls safe. The previous shape — a bare
    /// `isAutoScrolling = true` paired with an unconditional `asyncAfter { isAutoScrolling = false }`
    /// — let two scrolls 0.3s apart have the FIRST one's clear land mid-flight of the SECOND and
    /// re-arm the hide/show logic against a scroll the user never made.
    private func beginProgrammaticScroll(pinTop: Bool = false, settleAfter: Double) {
        autoScrollGeneration &+= 1
        let generation = autoScrollGeneration
        isAutoScrolling = true
        // Assign rather than only-set-on-true: the pin asserts "we are heading to offset 0", so a
        // scroll that is NOT heading there (the read-along centring a mid-core block) must clear an
        // in-flight pin. Otherwise a narration hop landing inside a core switch's window would keep
        // reporting 0 while the content sat mid-page, freezing the sticky header for the remainder.
        isPinnedToTop = pinTop
        DispatchQueue.main.asyncAfter(deadline: .now() + settleAfter) {
            guard generation == autoScrollGeneration else { return }
            isAutoScrolling = false
            isPinnedToTop = false
        }
    }

    private func handleScrollChange(newOffset: CGFloat) {
        // Ignore programmatic scrolling (read-along follow, core-switch jump-to-top) so it doesn't
        // hide the mini player. During a core switch we also DISBELIEVE the reported offset: the
        // scroll view is already at the top, but the PreferenceKey still reports the old mid-page
        // value for a frame or two, which would drive headerOpacity 0 → 1 → 0 and flash the sticky
        // mini header. The read-along path deliberately does NOT pin — its offset reports are real,
        // and freezing headerOpacity for 0.6s there would strand the mini header.
        if isAutoScrolling {
            let effective = isPinnedToTop ? 0 : newOffset
            previousScrollOffset = effective
            scrollOffset = effective
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
                coreStarts: info.coreStartSeconds, totalSeconds: info.totalSeconds,
                seekEpoch: audioManager.seekEpoch, bookCores: coreNumbers)
        }
        if !newly.isEmpty {
            Haptics.success()
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
            progress.markCompleted(order: book.curriculumOrder, core: last.number, bookCores: coreNumbers)
        }
        Haptics.success()
    }

    // MARK: - Completion Handling
    private func handleCoreCompletion() {
        // Don't re-complete if already done
        guard !isCurrentCoreCompleted else { return }

        // Trigger success haptic feedback (gated by the Haptics setting)
        Haptics.success()

        // Mark current core as completed (persists locally + syncs to backend).
        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
            progress.markCompleted(order: book.curriculumOrder, core: currentContent.chapterNumber, bookCores: coreNumbers)
        }

        // If there's a next core, navigate to it after a delay.
        if hasNextCore {
            // Capture the core this tap completed: if the learner taps "Next" (or the narration
            // auto-advances) inside the 1.5s window, this delayed hop would fire ON TOP of that and
            // skip a core entirely. Only advance if we're still where the tap left us.
            let completedCore = currentContent.chapterNumber
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                guard currentContent.chapterNumber == completedCore else { return }
                navigateToNextCore()
            }
        }
    }

    // MARK: - Actions
    private func handleCloseTapped() {
        dismiss()
    }

    private func handleBackTapped() {
        guard hasPreviousCore else {
            dismiss()
            return
        }
        navigateToPreviousCore()
    }

    private func handleNextTapped() {
        guard hasNextCore else { return }
        navigateToNextCore()
    }

    private func navigateToPreviousCore() {
        guard !showBookFinale else { return }   // frozen behind the finale — see navigateToNextCore
        let previousIndex = currentCoreIndex - 1
        guard previousIndex >= 0, let newContent = allCores[previousIndex].getDetailContent(for: book) else { return }
        withAnimation(.easeInOut(duration: 0.3)) {
            currentContent = newContent
        }
        // Scroll reset + player un-hide are centralised in the .onChange(of:
        // currentContent.chapterNumber) inside the ScrollViewReader — it's the only place with a
        // scroll proxy, and doing it here only zeroed the tracking vars, never the real offset.
    }

    /// Jump the reading view to a specific core — used by the full-screen player's "Read" button to
    /// snap back to the core the narration is currently in (the read-along highlight resumes there).
    private func jumpReadingToCore(_ number: Int) {
        guard !showBookFinale else { return }   // frozen behind the finale — see navigateToNextCore
        guard number != currentContent.chapterNumber,
              let chapter = allCores.first(where: { $0.number == number }),
              let content = chapter.getDetailContent(for: book) else { return }
        withAnimation(.easeInOut(duration: 0.3)) {
            currentContent = content
        }
        // Scroll reset + player un-hide: see navigateToPreviousCore.
    }

    private func navigateToNextCore() {
        // While the finale is up the reader's content is FROZEN behind it. "The last REMAINING
        // core" is not "the last core" — finish out of order and the final hole can be core 5 of
        // 12, where hasNextCore is true and handleCoreCompletion's 1.5s delayed hop IS scheduled.
        // Without this the reader would swap to core 6 behind the card, which also trips the
        // scroll-to-top .onChange and its UIAccessibility screenChanged post, stealing VoiceOver
        // focus off the card — and would strand the learner on a core they never opened.
        // Guarding the three navigation funnels covers the delayed hop, the narration's own
        // auto-advance, and the player's "Read" jump in one concept.
        guard !showBookFinale else { return }
        let nextIndex = currentCoreIndex + 1
        guard nextIndex < allCores.count, let newContent = allCores[nextIndex].getDetailContent(for: book) else { return }
        withAnimation(.easeInOut(duration: 0.3)) {
            currentContent = newContent
        }
        // Scroll reset + player un-hide: see navigateToPreviousCore.
    }
    
    private func handleAISend() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        inputText = ""
        // Ground on the PASSAGE the reader is looking at, plus the guide outline as a map so
        // "how does this connect to core 9?" still works. One renderer shared with the card
        // entry points, so both stay inside the same budget — an oversized `context` is a
        // Pydantic 422 the client cannot decode, not a truncation.
        chatViewModel.startNewConversation(
            firstMessage: text,
            context: book.studyGuideContext(.passage(currentContent)),
            contextType: .book,
            referenceId: String(book.curriculumOrder)
        )
        // Release the focus-driven compact reason deterministically (the covered TextField's
        // focus-off event is unreliable) so the mini player returns when the chat closes.
        audioManager.setCompactMode(false, reason: compactToken)
        showAIChat = true
    }
}

// MARK: - Read-along block text
private extension ReadAlongBlock {
    /// The block's full narrated text — its sentence texts rejoined (used to match a block to the
    /// content section that produced it).
    var joinedText: String { sentences.map(\.text).joined(separator: " ") }
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
    @ObservedObject private var entitlement = LearnAudioEntitlement.shared
    @EnvironmentObject private var audioManager: AudioManager
    let content: CoreChapterContent
    let book: LibraryBook
    var onPlayStarted: (() -> Void)?

    /// True while the signed narration URL is being fetched.
    @State private var isPreparingAudio = false

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
                        Image(systemName: entitlement.isLocked(bookEpisode)
                              ? "lock.circle.fill"
                              : (isPreparingAudio ? "circle.dotted"
                                 : (isThisCoreAudioPlaying ? "pause.circle.fill" : "play.circle.fill")))
                            .font(AppTypography.iconXL).fontWeight(.medium)
                            // Text-role token — must clear 4.5:1 in both appearances.
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
            return
        }
        // Entitlement FIRST — see the same guard in `BookDetailListenRow.handlePlayTapped`.
        // Resolving a URL for a locked account returns nil, which would make the tap silently
        // do nothing instead of raising the paywall.
        guard !entitlement.refuseIfLocked(bookEpisode) else { return }
        guard !isPreparingAudio else { return }
        isPreparingAudio = true
        Task {
            defer { isPreparingAudio = false }
            do {
                let episode = try await book.playableAudioEpisode()
                // Play the whole-book narration, seeking to this core's start offset
                audioManager.play(episode, startAt: coreStart)
                // Force show the global audio player
                audioManager.resetScrollHiding()
                // Notify parent that playback started
                onPlayStarted?()
            } catch LibraryBook.PlayableAudioFailure.notNarrated {
                // No narration for this book — nothing to report.
            } catch {
                // Same reasoning as BookDetailView.handlePlayTapped: a user-initiated tap that
                // fails must not revert in silence.
                AppActions.shared.reportMutationFailure(error, action: "play book narration")
            }
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
        .cardSurface(cornerRadius: AppCornerRadius.large)
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
                    .fill(Color(themedHex: asset.iconColor, role: .graphic, fallback: AppColors.primaryBlue).opacity(0.15))
                    .frame(width: 44, height: 44)

                Image(systemName: asset.icon)
                    .font(AppTypography.iconMedium).fontWeight(.semibold)
                    // A meaningful glyph on a card is TEXT (4.5), not a chart mark (3.0).
                    // The 0.15 tint behind it stays `.graphic` — it carries no ink.
                    .foregroundColor(Color(themedHex: asset.iconColor, role: .text, fallback: AppColors.primaryBlue))
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
        .cardSurface(cornerRadius: AppCornerRadius.medium)
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
        .cardSurface(cornerRadius: AppCornerRadius.large)
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
        .cardSurface(cornerRadius: AppCornerRadius.large)
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
                .shadow(color: AppColors.shadowAmbient, radius: 4, y: 2)
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
            .foregroundColor(isCompleted ? AppColors.textSecondary : AppColors.textOnAccent)
            .frame(maxWidth: .infinity)
            .frame(height: 56)
            .background(
                Group {
                    if isCompleted {
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .strokeBorder(AppColors.textMuted, lineWidth: 1.5)
                    } else {
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .fill(AppColors.primaryFill)
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
}
