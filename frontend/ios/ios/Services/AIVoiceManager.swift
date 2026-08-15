//
//  AIVoiceManager.swift
//  ios
//
//  Service: Manages AI text-to-speech with word-by-word progress tracking
//  Uses AVSpeechSynthesizer for natural voice reading with callbacks for UI updates
//

import AVFoundation
import Combine

@MainActor
class AIVoiceManager: NSObject, ObservableObject {
    // MARK: - Published Properties
    @Published var isPlaying: Bool = false
    @Published var currentWordRange: NSRange = NSRange(location: 0, length: 0)
    @Published var currentWordIndex: Int = 0
    @Published var progress: Double = 0.0

    // MARK: - Private Properties
    private var synthesizer: AVSpeechSynthesizer?
    private var currentText: String = ""
    private var wordRanges: [NSRange] = []
    private var onComplete: (() -> Void)?
    // The utterance currently owning the state. Delegate callbacks arrive on a background queue and
    // hop to the main actor via `Task {}`, so a `didCancel`/`didFinish` from a stopped utterance can
    // land AFTER the next `speak()` has already begun. Every delegate callback is gated on
    // `utterance === currentUtterance`, so a stale callback can't stomp the new card's isPlaying /
    // progress / word-highlight / onComplete. Cleared by stop() and playClip() (synth not in use).
    private var currentUtterance: AVSpeechUtterance?

    // Pre-recorded clip playback (used when a card has a bundled narration file)
    private var player: AVPlayer?
    private var timeObserver: Any?
    private var endObserver: NSObjectProtocol?
    private var clipDuration: Double = 0
    // Forced-aligned per-word timings for the current clip. When present (and index-aligned with
    // `wordRanges`), the active word is chosen by playhead time instead of a character estimate.
    private var readAlongWords: [ReadAlongWord]?
    // Detect a failed clip load (404 / expired / bad remote URL) so a card never hangs with
    // isPlaying=true and no audio — we fall back to on-device speech instead.
    private var statusObserver: NSKeyValueObservation?
    private var failObserver: NSObjectProtocol?
    /// One-shot budget for the signed-URL refresh in `handleClipLoadFailed`.
    ///
    /// Cleared on `.readyToPlay` (something played, so a later expiry earns a fresh attempt).
    /// The retry itself cannot loop even though it re-enters `playClip`: the replacement URL
    /// always differs from the failed one, and `JourneyContentStore.refreshedClipURL` refuses
    /// to return a string equal to the one passed in — so a second failure finds no newer URL
    /// and degrades to on-device speech.
    private var didRetryClipRefresh = false

    // What was last started. The play button after a finished clip calls `resume()`, but
    // `handleClipFinished` has already nil'd the player — so resume had nothing to resume and yet
    // reported isPlaying = true (a permanently animating orb over silence). Kept so resume can
    // honestly replay instead of lying.
    private enum LastRequest {
        case clip(name: String, text: String, readAlong: [ReadAlongWord]?)
        case speech(text: String)
    }
    private var lastRequest: LastRequest?

    // Whether narration was actually running when a system interruption (call / Siri) began, so
    // `.ended` only resumes what the user was really listening to.
    private var wasPlayingBeforeInterruption = false
    private var isSessionActive = false

    // MARK: - Singleton
    static let shared = AIVoiceManager()

    override init() {
        super.init()
        setupSynthesizer()
        configureAudioSession()
        setupAudioSessionObservers()
    }

    // MARK: - Setup

    private func setupSynthesizer() {
        synthesizer = AVSpeechSynthesizer()
        synthesizer?.delegate = self
    }

    /// Declare the category WITHOUT taking the session — activating at init would stop the user's
    /// other audio just for existing. Matches AudioManager's category+mode exactly: both engines
    /// share this one session, and the previous `mode: .default` here permanently downgraded
    /// AudioManager's `.spokenAudio` from the first Journey lesson onward.
    private func configureAudioSession() {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio,
                                                            options: [.allowBluetoothA2DP, .allowAirPlay])
        } catch {
            print("[AIVoiceManager] audio session category setup failed: \(error)")
        }
    }

    /// Take the session at the moment narration actually starts (see configureAudioSession).
    private func activateAudioSession() {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio,
                                                            options: [.allowBluetoothA2DP, .allowAirPlay])
            try AVAudioSession.sharedInstance().setActive(true)
            isSessionActive = true
        } catch {
            print("[AIVoiceManager] audio session activation failed: \(error)")
        }
    }

    /// Hand the session back so other apps' audio can resume once narration is done.
    ///
    /// DEFERRED on purpose: the Journey card flow calls `stop()` and immediately starts the next
    /// card's clip, so releasing synchronously would let the user's other audio (Spotify) barge in
    /// for a fraction of a second between every card. Dropping ownership immediately and releasing a
    /// beat later means a resumed narration simply re-claims it and the release is skipped.
    private func deactivateAudioSession() {
        guard isSessionActive else { return }
        isSessionActive = false   // we no longer claim the session, whatever happens below
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 1_200_000_000)   // longer than a card advance animation
            guard let self, !self.isPlaying, !self.isSessionActive else { return }
            // AudioManager drives book / Money Moves playback on this SAME session (it calls our
            // stop() to take over). Releasing it out from under that engine would cut its audio off.
            //
            // Test OWNERSHIP, not `isPlaying`. `isPlaying` is `playbackState == .playing`, which
            // is false while a freshly started remote clip is still BUFFERING (`.loading`) and
            // also false while paused — a state AudioManager holds the session through on
            // purpose. Closing a Journey lesson 1.2s before a Money Moves clip finished
            // buffering therefore deactivated the session under it and killed audio the user had
            // just started. Strictly more conservative: this can only ever SKIP a release.
            guard !AudioManager.shared.isPlaying, !AudioManager.shared.ownsAudioSession else { return }
            do {
                try AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
            } catch {
                print("[AIVoiceManager] audio session deactivation failed: \(error)")
            }
        }
    }

    /// Track system interruptions (calls / Siri) and route changes, which this class previously
    /// ignored entirely: an incoming call during a Journey lesson silenced the audio while
    /// `isPlaying` stayed true — the orb kept animating, `handleClipFinished` never fired, and the
    /// lesson's auto-advance died for good. Mirrors AudioManager's handling. Observers live for the
    /// app lifetime (singleton), so they're never removed.
    private func setupAudioSessionObservers() {
        let nc = NotificationCenter.default
        let session = AVAudioSession.sharedInstance()
        nc.addObserver(forName: AVAudioSession.interruptionNotification, object: session, queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
            let optionRaw = (note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt) ?? 0
            let shouldResume = AVAudioSession.InterruptionOptions(rawValue: optionRaw).contains(.shouldResume)
            Task { @MainActor [weak self] in self?.handleInterruption(type: type, shouldResume: shouldResume) }
        }
        nc.addObserver(forName: AVAudioSession.routeChangeNotification, object: session, queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  let reason = AVAudioSession.RouteChangeReason(rawValue: raw) else { return }
            Task { @MainActor [weak self] in self?.handleRouteChange(reason: reason) }
        }
    }

    private func handleInterruption(type: AVAudioSession.InterruptionType, shouldResume: Bool) {
        switch type {
        case .began:
            // Only narration WE own counts — AudioManager's playback interruption is its own concern.
            wasPlayingBeforeInterruption = isPlaying && (player != nil || synthesizer?.isSpeaking == true)
            if isPlaying { pause() }
        case .ended:
            // `.shouldResume` means "you MAY resume", not "you were playing" — require both, or a
            // lesson the user had paused would start talking on its own after a call.
            if shouldResume, wasPlayingBeforeInterruption {
                isSessionActive = false   // the system deactivated it; resume() re-takes it
                resume()
            }
            wasPlayingBeforeInterruption = false
        @unknown default:
            break
        }
    }

    private func handleRouteChange(reason: AVAudioSession.RouteChangeReason) {
        // Headphones / Bluetooth pulled → pause rather than blast the lesson out of the speaker.
        if reason == .oldDeviceUnavailable, isPlaying {
            pause()
        }
    }

    // MARK: - Public Methods

    /// Speak the given text with word-by-word progress tracking
    func speak(_ text: String, onComplete: (() -> Void)? = nil) {
        // No entitlement gate. Journey narration is free on every tier including signed-out
        // guests (backend: entitlements.JOURNEY_AUDIO_UNLOCKED_TIERS). This class is
        // Journey-only — Money Moves and book narration run through AudioManager, which
        // still consults LearnAudioEntitlement and is unaffected.
        AudioManager.shared.pauseForExternalAudio()   // see playClip
        guard let synthesizer = synthesizer else { return }

        // Stop any current speech
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }

        currentText = text
        self.onComplete = onComplete
        lastRequest = .speech(text: text)   // so resume() after a finish can replay honestly
        wordRanges = calculateWordRanges(for: text)
        currentWordIndex = 0
        progress = 0.0
        activateAudioSession()

        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.9  // Slightly slower for clarity
        utterance.pitchMultiplier = 1.0
        utterance.volume = 1.0

        currentUtterance = utterance   // gate stale delegate callbacks from the previous utterance
        isPlaying = true
        synthesizer.speak(utterance)
    }

    /// Play a pre-recorded narration clip bundled with the app (e.g. an Achird voice .m4a),
    /// driving the same word-highlight + progress as the synthesizer path via estimated timing.
    /// Falls back to on-device speech if the clip is missing.
    func playClip(named name: String, text: String, readAlong: [ReadAlongWord]? = nil, onComplete: (() -> Void)? = nil) {
        // No entitlement gate — see `speak(_:onComplete:)`.
        //
        // Yield the shared audio session. AudioManager (Money Moves / book
        // narration) and this class each drive their own AVPlayer on the same
        // non-mixable `.playback` session and previously had no knowledge of one
        // another — so starting a Journey lesson while a book was playing left
        // BOTH voices audible, with both read-along highlights tracking the
        // wrong audio and no visible control to stop the other stream.
        AudioManager.shared.pauseForExternalAudio()

        // Stop anything currently playing
        synthesizer?.stopSpeaking(at: .immediate)
        currentUtterance = nil   // synth not in use for this clip → ignore any late synth callbacks
        teardownPlayer()

        // `name` is either a remote Storage URL (http...) or a bundled resource basename.
        let resolvedURL: URL?
        if name.hasPrefix("http"), let remote = URL(string: name) {
            resolvedURL = remote
        } else {
            resolvedURL = Bundle.main.url(forResource: name, withExtension: "m4a")
        }
        guard let url = resolvedURL else {
            // Graceful fallback so a missing clip never leaves the lesson silent
            speak(text, onComplete: onComplete)
            return
        }

        currentText = text
        self.onComplete = onComplete
        lastRequest = .clip(name: name, text: text, readAlong: readAlong)   // see resume()
        wordRanges = calculateWordRanges(for: text)
        // Use the aligned timings only if they line up 1:1 with the tokenized words (they're built
        // from strip_markup(text).split(), the same tokenization as wordRanges); otherwise ignore.
        // Mismatched timings are WORSE than none — every word after the divergence highlights on
        // the wrong syllable — and salvaging a prefix would be guessing where the aligner drifted.
        //
        // But the discard used to be SILENT, which made this the hardest possible bug to see: the
        // lesson still plays, still highlights, just fractionally off, and nothing anywhere says
        // the aligned data was thrown away. The two tokenizations agree only by hand, in two
        // languages (backend `_forced_align.strip_markup` vs `JourneyContentStore.spoken(from:)`),
        // with nothing enforcing it — so ANY new markup token on either side lands here.
        if let aligned = readAlong, aligned.count != wordRanges.count {
            if aligned.isEmpty {
                // Distinct cause: the aligner produced nothing at all. That is an upstream
                // pipeline failure (missing/failed alignment run), not tokenization drift.
                print("⚠️ [AIVoiceManager] read-along EMPTY for clip '\(name)' — alignment never ran or failed upstream; falling back to estimated timing")
            } else {
                print("⚠️ [AIVoiceManager] read-along DISCARDED for clip '\(name)': \(aligned.count) timings vs \(wordRanges.count) tokenized words — backend/iOS tokenization drift; falling back to estimated timing. text=\"\(text.prefix(80))\"")
            }
        }
        readAlongWords = (readAlong?.count == wordRanges.count) ? readAlong : nil
        currentWordIndex = 0
        currentWordRange = NSRange(location: 0, length: 0)
        progress = 0.0
        clipDuration = 0

        let item = AVPlayerItem(url: url)
        let newPlayer = AVPlayer(playerItem: item)
        player = newPlayer

        let interval = CMTime(seconds: 0.05, preferredTimescale: 600)
        timeObserver = newPlayer.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.tickClip() }
        }
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.handleClipFinished() }
        }
        // Fall back to on-device speech if the clip load FAILS (not just if the URL is missing),
        // so a failed remote clip never leaves the lesson stuck "playing" with no audio and the
        // auto-advance (onComplete) never firing.
        // `guard let self` OUTSIDE the Task, matching the two observers above.
        //
        // These two used `self?.` INSIDE the Task, which reads the captured `weak var self`
        // from concurrently-executing code — a data race on the optional itself, and an error
        // in the Swift 6 language mode. Unwrapping first turns it into a plain strong capture
        // whose lifetime is the Task's.
        statusObserver = item.observe(\.status, options: [.new]) { [weak self, weak item] _, _ in
            guard let item, let self else { return }
            Task { @MainActor in self.handleClipStatus(item) }
        }
        failObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.handleClipLoadFailed() }
        }

        activateAudioSession()
        isPlaying = true
        newPlayer.play()
    }

    /// Pause the current speech (synth or clip)
    func pause() {
        if player != nil {
            player?.pause()
        } else {
            synthesizer?.pauseSpeaking(at: .word)
        }
        isPlaying = false
    }

    /// Resume paused speech (synth or clip).
    ///
    /// Only claims `isPlaying` when something will ACTUALLY produce sound. Previously this set
    /// `isPlaying = true` unconditionally, so tapping play after a clip ended (handleClipFinished
    /// nils the player) fell through to a no-op `continueSpeaking()` on an idle synthesizer and left
    /// the card permanently "playing" in silence, with no way back.
    func resume() {
        if let player {
            activateAudioSession()
            player.play()
            isPlaying = true
            return
        }
        if let synthesizer, synthesizer.isPaused {
            activateAudioSession()
            synthesizer.continueSpeaking()
            isPlaying = true
            return
        }
        // Nothing is loaded: the clip finished, or stop() cleared it. Replay what was last requested
        // so the play button does something real; if there's nothing to replay, stay honestly idle.
        guard let lastRequest else {
            isPlaying = false
            return
        }
        switch lastRequest {
        case .clip(let name, let text, let readAlong):
            playClip(named: name, text: text, readAlong: readAlong, onComplete: onComplete)
        case .speech(let text):
            speak(text, onComplete: onComplete)
        }
    }

    /// Stop speaking completely (synth or clip)
    func stop() {
        synthesizer?.stopSpeaking(at: .immediate)
        currentUtterance = nil   // state is reset synchronously below → drop the resulting didCancel
        teardownPlayer()
        isPlaying = false
        currentWordIndex = 0
        currentWordRange = NSRange(location: 0, length: 0)
        progress = 0.0
        readAlongWords = nil
        // Nothing of ours is audible any more. NOTE: AudioManager calls stop() to take the session
        // for a book / Money Moves clip, so only release it if we still hold it — deactivating after
        // the other engine has activated would be a no-op on our flag but is guarded there too.
        deactivateAudioSession()
    }

    /// Toggle between play and pause
    func togglePlayPause() {
        if isPlaying {
            pause()
        } else {
            if player != nil || synthesizer?.isPaused == true {
                resume()
            }
        }
    }

    // MARK: - Clip Playback Helpers

    private func teardownPlayer() {
        if let timeObserver = timeObserver {
            player?.removeTimeObserver(timeObserver)
        }
        timeObserver = nil
        statusObserver?.invalidate()
        statusObserver = nil
        if let endObserver = endObserver {
            NotificationCenter.default.removeObserver(endObserver)
        }
        endObserver = nil
        if let failObserver = failObserver {
            NotificationCenter.default.removeObserver(failObserver)
        }
        failObserver = nil
        player?.pause()
        player = nil
        clipDuration = 0
    }

    /// React to clip readiness: a `.failed` item means the remote/bundled clip can't play.
    private func handleClipStatus(_ item: AVPlayerItem) {
        guard player?.currentItem === item else { return }
        if item.status == .readyToPlay {
            // Something actually played, so a LATER expiry has earned its own one-shot refresh.
            didRetryClipRefresh = false
            return
        }
        guard item.status == .failed else { return }
        handleClipLoadFailed()
    }

    /// Clip failed to load — never leave the card stuck (isPlaying=true, no audio, no completion).
    ///
    /// Falls back to on-device speech, but only AFTER one attempt to re-resolve the clip. Journey
    /// card URLs are signed and finite-lived, and `JourneyContentStore` latches its fetch for the
    /// whole session, so a long-foregrounded app holds URLs past their life. Without the retry a
    /// single expired token silently downgrades a PAYING learner to the robotic system voice for
    /// the rest of the process — the same failure `AudioManager` was given recovery for, which
    /// this engine never got.
    private func handleClipLoadFailed() {
        guard player != nil else { return }   // already handled / torn down
        let text = currentText
        let completion = onComplete
        let readAlong = readAlongWords

        // Only a REMOTE clip can be re-signed, and only once per clip — `didRetryClipRefresh`
        // is cleared in `playClip`, so a genuinely dead object degrades to speech instead of
        // looping, while a later expiry on a different clip still gets its own attempt.
        guard case .clip(let name, _, _) = lastRequest,
              name.hasPrefix("http"),
              !didRetryClipRefresh else {
            print("[AIVoiceManager] clip failed to load; falling back to on-device speech")
            teardownPlayer()
            speak(text, onComplete: completion)
            return
        }
        didRetryClipRefresh = true
        print("[AIVoiceManager] clip failed to load; refreshing signed URL and retrying once")
        teardownPlayer()
        Task { @MainActor in
            await JourneyContentStore.shared.forceRefresh()
            guard let fresh = JourneyContentStore.shared.refreshedClipURL(matching: name) else {
                // Nothing newer to play — degrade to speech, as before.
                print("[AIVoiceManager] no re-signed clip URL available; falling back to speech")
                self.speak(text, onComplete: completion)
                return
            }
            self.playClip(named: fresh, text: text, readAlong: readAlong, onComplete: completion)
        }
    }

    /// Periodic tick: map the playhead to the active word. Prefers forced-aligned per-word timings
    /// (accurate); falls back to a character-position estimate when timings aren't available.
    private func tickClip() {
        guard let player = player, let item = player.currentItem else { return }
        if item.duration.isNumeric {
            let seconds = CMTimeGetSeconds(item.duration)
            if seconds.isFinite && seconds > 0 { clipDuration = seconds }
        }
        let elapsed = CMTimeGetSeconds(player.currentTime())
        if clipDuration > 0 { progress = min(1.0, max(0.0, elapsed / clipDuration)) }

        // Accurate path: the word whose [start, end) contains the playhead.
        if let words = readAlongWords {
            if let index = words.firstIndex(where: { elapsed >= $0.start && elapsed < $0.end }),
               index < wordRanges.count {
                currentWordIndex = index
                currentWordRange = wordRanges[index]
            }
            return
        }

        // Fallback: estimate the word index from the elapsed fraction of the clip. Measure in UTF-16
        // units — `targetChar` is compared against `NSRange.location`, which is UTF-16 — because
        // `String.count` counts GRAPHEMES and under-counts any emoji / accented / non-BMP text, which
        // would make the estimated cursor run ahead of the real word ranges.
        let totalChars = Double(currentText.utf16.count)
        guard clipDuration > 0, totalChars > 0 else { return }
        let targetChar = Int(min(1.0, max(0.0, elapsed / clipDuration)) * totalChars)
        var index = 0
        for (i, range) in wordRanges.enumerated() {
            if range.location <= targetChar { index = i } else { break }
        }
        currentWordIndex = index
        if index >= 0 && index < wordRanges.count {
            currentWordRange = wordRanges[index]
        }
    }

    private func handleClipFinished() {
        let completion = onComplete
        if let last = wordRanges.last { currentWordRange = last }
        teardownPlayer()
        isPlaying = false
        progress = 1.0
        deactivateAudioSession()   // lesson over; let other apps' audio back in
        completion?()
    }

    // MARK: - Word Range Calculation

    private func calculateWordRanges(for text: String) -> [NSRange] {
        var ranges: [NSRange] = []

        // Advance the search cursor with a `String.Index` (grapheme space), NOT an Int derived from
        // NSRange lengths (UTF-16 space). Mixing the two — `text.index(startIndex, offsetBy:)` fed a
        // UTF-16 offset — over-counts by one per non-BMP char (emoji, flags) or combining sequence,
        // and a clustered run (e.g. "🇺🇸🇬🇧 a b") pushes the offset past endIndex → a fatal
        // "String index is out of bounds" the instant narration starts. Staying in String.Index
        // space is correct for any Unicode; NSRange conversion happens only for the returned range.
        var searchStart = text.startIndex
        let words = text.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }

        for word in words {
            guard let range = text.range(of: word, range: searchStart..<text.endIndex) else { continue }
            ranges.append(NSRange(range, in: text))
            searchStart = range.upperBound
        }

        // This `continue` is itself a source of the count mismatch that discards aligned
        // read-along upstream: a token the forward scan can't relocate is dropped, so `ranges`
        // comes back SHORTER than `words` and the 1:1 check in `playClip` fails for a reason that
        // has nothing to do with the backend. Without this line the two causes are
        // indistinguishable in a bug report.
        if ranges.count != words.count {
            print("⚠️ [AIVoiceManager] tokenization lost \(words.count - ranges.count) of \(words.count) word(s) while locating ranges — read-along will fall back to estimated timing. text=\"\(text.prefix(80))\"")
        }

        return ranges
    }

    /// Get the index of the word at a given character position
    func wordIndex(forCharacterAt position: Int) -> Int {
        for (index, range) in wordRanges.enumerated() {
            if position >= range.location && position < range.location + range.length {
                return index
            }
        }
        // Clamp to 0 for an empty word list — `wordRanges.count - 1` would be -1, a negative index
        // published on `currentWordIndex` (e.g. after speak("")), a landmine for any future subscript.
        return max(0, wordRanges.count - 1)
    }
}

// MARK: - AVSpeechSynthesizerDelegate

extension AIVoiceManager: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, willSpeakRangeOfSpeechString characterRange: NSRange, utterance: AVSpeechUtterance) {
        let token = ObjectIdentifier(utterance)   // Sendable; crosses the hop safely
        Task { @MainActor in
            guard let current = self.currentUtterance,
                  ObjectIdentifier(current) == token else { return }   // stale utterance
            self.currentWordRange = characterRange
            self.currentWordIndex = self.wordIndex(forCharacterAt: characterRange.location)

            // Calculate progress. `characterRange` is UTF-16 (NSRange), so the denominator must be
            // too: `String.count` counts graphemes, so an emoji/accented lesson made the ratio
            // exceed 1 (observed ~1.25) and overflowed the progress bar. Clamp as a backstop.
            let totalLength = self.currentText.utf16.count
            if totalLength > 0 {
                let consumed = Double(characterRange.location + characterRange.length)
                self.progress = min(1.0, max(0.0, consumed / Double(totalLength)))
            }
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        let token = ObjectIdentifier(utterance)   // Sendable; crosses the hop safely
        Task { @MainActor in
            guard let current = self.currentUtterance,
                  ObjectIdentifier(current) == token else { return }   // a stale finish must not fire the new card's onComplete
            self.currentUtterance = nil
            self.isPlaying = false
            self.progress = 1.0
            self.onComplete?()
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didPause utterance: AVSpeechUtterance) {
        let token = ObjectIdentifier(utterance)   // Sendable; crosses the hop safely
        Task { @MainActor in
            guard let current = self.currentUtterance,
                  ObjectIdentifier(current) == token else { return }
            self.isPlaying = false
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didContinue utterance: AVSpeechUtterance) {
        let token = ObjectIdentifier(utterance)   // Sendable; crosses the hop safely
        Task { @MainActor in
            guard let current = self.currentUtterance,
                  ObjectIdentifier(current) == token else { return }
            self.isPlaying = true
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        let token = ObjectIdentifier(utterance)   // Sendable; crosses the hop safely
        Task { @MainActor in
            // Gate on the owning utterance: a cancel from the PREVIOUS card (triggered by
            // stopSpeaking during navigation) can land after the next speak() already set
            // isPlaying=true, and would otherwise freeze the orb/highlight for the whole new card.
            guard let current = self.currentUtterance,
                  ObjectIdentifier(current) == token else { return }
            self.isPlaying = false
            self.currentWordIndex = 0
            self.progress = 0.0
        }
    }
}
