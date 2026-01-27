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

        isPlaying = true
        synthesizer.speak(utterance)
    }

    /// Pause the current speech
    func pause() {
        synthesizer?.pauseSpeaking(at: .word)
        isPlaying = false
    }

    /// Resume paused speech
    func resume() {
        synthesizer?.continueSpeaking()
        isPlaying = true
    }

    /// Stop speaking completely
    func stop() {
        synthesizer?.stopSpeaking(at: .immediate)
        isPlaying = false
        currentWordIndex = 0
        currentWordRange = NSRange(location: 0, length: 0)
        progress = 0.0
    }

    /// Toggle between play and pause
    func togglePlayPause() {
        if isPlaying {
            pause()
        } else {
            if synthesizer?.isPaused == true {
                resume()
            }
        }
    }

    // MARK: - Word Range Calculation

    private func calculateWordRanges(for text: String) -> [NSRange] {
        var ranges: [NSRange] = []

        // Use natural language processing to find word boundaries
        var currentIndex = 0
        let words = text.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }

        for word in words {
            if let range = text.range(of: word, range: text.index(text.startIndex, offsetBy: currentIndex)..<text.endIndex) {
                let nsRange = NSRange(range, in: text)
                ranges.append(nsRange)
                currentIndex = nsRange.location + nsRange.length
            }
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
            self.isPlaying = false
            self.progress = 1.0
            self.onComplete?()
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didPause utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.isPlaying = false
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didContinue utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.isPlaying = true
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.isPlaying = false
            self.currentWordIndex = 0
            self.progress = 0.0
        }
    }
}
