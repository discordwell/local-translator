import Foundation
import AVFoundation
import Combine

/// Handles audio playback for translated Japanese speech.
@MainActor
class AudioPlayer: NSObject, ObservableObject {
    /// Whether currently playing audio
    @Published var isPlaying: Bool = false

    /// Whether there is audio available to replay. Published (rather than a
    /// computed property over `lastPlayedAudio`) so SwiftUI drives the Replay
    /// button's visibility from real state, instead of relying on an incidental
    /// re-render from some other @Published change happening to coincide.
    @Published private(set) var canReplay: Bool = false

    private var audioPlayer: AVAudioPlayer?
    private var audioQueue: [Data] = []
    private var isProcessingQueue: Bool = false

    /// Last played audio for replay functionality
    private var lastPlayedAudio: Data?

    override init() {
        super.init()
        setupAudioSession()
    }

    private func setupAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            // Use playAndRecord to allow both recording and playback
            // defaultToSpeaker ensures playback uses the loudspeaker
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetooth])
            try session.overrideOutputAudioPort(.speaker)
            try session.setActive(true)
        } catch {
            print("Failed to setup audio session: \(error)")
        }
    }

    /// Ensure audio plays through the loudspeaker at full volume
    private func ensureLoudspeaker() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.overrideOutputAudioPort(.speaker)
        } catch {
            print("Failed to set speaker output: \(error)")
        }
    }

    /// Play audio data (WAV format).
    func play(audioData: Data) {
        print("[PLAYER] play() called with \(audioData.count) bytes")

        // Store for replay
        lastPlayedAudio = audioData
        canReplay = true

        // Add to queue
        audioQueue.append(audioData)

        // Process queue if not already processing
        if !isProcessingQueue {
            processQueue()
        }
    }

    /// Replay the last played audio
    func replay() {
        guard let audioData = lastPlayedAudio else {
            print("No audio to replay")
            return
        }
        play(audioData: audioData)
    }

    private func processQueue() {
        guard !audioQueue.isEmpty else {
            isProcessingQueue = false
            return
        }

        isProcessingQueue = true
        let audioData = audioQueue.removeFirst()

        // Ensure loudspeaker before playing
        ensureLoudspeaker()

        do {
            audioPlayer = try AVAudioPlayer(data: audioData)
            audioPlayer?.delegate = self
            audioPlayer?.volume = 1.0  // Maximum volume
            audioPlayer?.play()
            isPlaying = true
            print("Playing audio (\(audioData.count) bytes) on speaker")
        } catch {
            print("Failed to play audio: \(error)")
            // Try next in queue
            processQueue()
        }
    }

    /// Stop playback and clear the queue.
    func stop() {
        audioPlayer?.stop()
        audioPlayer = nil
        audioQueue.removeAll()
        isPlaying = false
        isProcessingQueue = false
    }

    /// Clear the audio queue without stopping current playback.
    func clearQueue() {
        audioQueue.removeAll()
    }
}

extension AudioPlayer: AVAudioPlayerDelegate {
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.isPlaying = false

            // Play next in queue
            if !self.audioQueue.isEmpty {
                self.processQueue()
            } else {
                self.isProcessingQueue = false
            }
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        if let error = error {
            print("Audio decode error: \(error)")
        }
        Task { @MainActor in
            self.isPlaying = false
            self.processQueue()
        }
    }
}
