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

    // MARK: - Singleton
    static let shared = AIVoiceManager()

    override init() {
        super.init()
        setupSynthesizer()
        setupAudioSession()
    }

    // MARK: - Setup

    private func setupSynthesizer() {
        synthesizer = AVSpeechSynthesizer()
        synthesizer?.delegate = self
    }

    private func setupAudioSession() {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
            try AVAudioSession.sharedInstance().setActive(true)
        } catch {
            print("Failed to setup audio session: \(error)")
        }
    }

    // MARK: - Public Methods

    /// Speak the given text with word-by-word progress tracking
    func speak(_ text: String, onComplete: (() -> Void)? = nil) {
        guard let synthesizer = synthesizer else { return }

        // Stop any current speech
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }

        currentText = text
        self.onComplete = onComplete
        wordRanges = calculateWordRanges(for: text)
        currentWordIndex = 0
        progress = 0.0

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

    /// Resume paused speech (synth or clip)
    func resume() {
        if player != nil {
            player?.play()
        } else {
            synthesizer?.continueSpeaking()
        }
        isPlaying = true
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

        // Fallback: estimate the word index from the elapsed fraction of the clip.
        let totalChars = Double(currentText.count)
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
        return wordRanges.count - 1
    }
}

// MARK: - AVSpeechSynthesizerDelegate

extension AIVoiceManager: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, willSpeakRangeOfSpeechString characterRange: NSRange, utterance: AVSpeechUtterance) {
        Task { @MainActor in
            guard utterance === self.currentUtterance else { return }   // stale utterance
            self.currentWordRange = characterRange
            self.currentWordIndex = self.wordIndex(forCharacterAt: characterRange.location)

            // Calculate progress
            let totalLength = self.currentText.count
            if totalLength > 0 {
                self.progress = Double(characterRange.location + characterRange.length) / Double(totalLength)
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
