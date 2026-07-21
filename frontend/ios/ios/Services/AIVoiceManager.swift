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
            guard !AudioManager.shared.isPlaying else { return }
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
        statusObserver = item.observe(\.status, options: [.new]) { [weak self, weak item] _, _ in
            guard let item else { return }
            Task { @MainActor in self?.handleClipStatus(item) }
        }
        failObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.handleClipLoadFailed() }
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
        guard player?.currentItem === item, item.status == .failed else { return }
        handleClipLoadFailed()
    }

    /// Clip failed to load — never leave the card stuck (isPlaying=true, no audio, no completion).
    /// Fall back to on-device speech so it still narrates, highlights, and fires onComplete.
    private func handleClipLoadFailed() {
        guard player != nil else { return }   // already handled / torn down
        print("[AIVoiceManager] clip failed to load; falling back to on-device speech")
        let text = currentText
        let completion = onComplete
        teardownPlayer()
        speak(text, onComplete: completion)
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
        Task { @MainActor in
            guard utterance === self.currentUtterance else { return }   // stale utterance
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
        Task { @MainActor in
            guard utterance === self.currentUtterance else { return }   // a stale finish must not fire the new card's onComplete
            self.currentUtterance = nil
            self.isPlaying = false
            self.progress = 1.0
            self.onComplete?()
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didPause utterance: AVSpeechUtterance) {
        Task { @MainActor in
            guard utterance === self.currentUtterance else { return }
            self.isPlaying = false
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didContinue utterance: AVSpeechUtterance) {
        Task { @MainActor in
            guard utterance === self.currentUtterance else { return }
            self.isPlaying = true
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in
            // Gate on the owning utterance: a cancel from the PREVIOUS card (triggered by
            // stopSpeaking during navigation) can land after the next speak() already set
            // isPlaying=true, and would otherwise freeze the orb/highlight for the whole new card.
            guard utterance === self.currentUtterance else { return }
            self.isPlaying = false
            self.currentWordIndex = 0
            self.progress = 0.0
        }
    }
}
