import Foundation
import AVFoundation
import Combine

/// Handles audio recording with silence detection for sentence boundaries.
@MainActor
class AudioRecorder: NSObject, ObservableObject {
    /// Whether currently recording
    @Published var isRecording: Bool = false

    /// Current audio level (0.0 to 1.0) for visualization
    @Published var audioLevel: Float = 0.0

    /// Called when a sentence is detected (silence after speech)
    var onSentenceComplete: ((Data) -> Void)?

    private var audioRecorder: AVAudioRecorder?
    private var levelTimer: Timer?
    private var silenceTimer: Timer?

    // Audio settings for WAV output compatible with SeamlessM4T
    private let audioSettings: [String: Any] = [
        AVFormatIDKey: Int(kAudioFormatLinearPCM),
        AVSampleRateKey: 16000,
        AVNumberOfChannelsKey: 1,
        AVLinearPCMBitDepthKey: 16,
        AVLinearPCMIsFloatKey: false,
        AVLinearPCMIsBigEndianKey: false,
    ]

    // Silence detection thresholds
    private let silenceThreshold: Float = -40.0  // dB threshold for silence
    private let silenceDuration: TimeInterval = 0.8  // seconds of silence to end sentence
    private var speechDetected: Bool = false

    private var recordingURL: URL {
        let tempDir = FileManager.default.temporaryDirectory
        return tempDir.appendingPathComponent("recording.wav")
    }

    override init() {
        super.init()
        setupAudioSession()
    }

    private func setupAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            try session.setActive(true)
        } catch {
            print("Failed to setup audio session: \(error)")
        }
    }

    /// Request microphone permission.
    func requestPermission() async -> Bool {
        return await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    /// Start recording audio.
    func startRecording() {
        guard !isRecording else { return }

        // Delete any existing recording
        try? FileManager.default.removeItem(at: recordingURL)

        do {
            audioRecorder = try AVAudioRecorder(url: recordingURL, settings: audioSettings)
            audioRecorder?.isMeteringEnabled = true
            audioRecorder?.delegate = self
            audioRecorder?.record()

            isRecording = true
            speechDetected = false

            // Start monitoring audio levels
            startLevelMonitoring()

            print("Recording started")
        } catch {
            print("Failed to start recording: \(error)")
        }
    }

    /// Stop recording and return the audio data.
    func stopRecording() -> Data? {
        guard isRecording else { return nil }

        stopLevelMonitoring()
        audioRecorder?.stop()
        isRecording = false
        speechDetected = false

        print("Recording stopped")

        // Read the recorded audio file
        do {
            let audioData = try Data(contentsOf: recordingURL)
            return audioData
        } catch {
            print("Failed to read recording: \(error)")
            return nil
        }
    }

    /// Stop recording without returning data (cancel).
    func cancelRecording() {
        stopLevelMonitoring()
        audioRecorder?.stop()
        isRecording = false
        speechDetected = false
        try? FileManager.default.removeItem(at: recordingURL)
    }

    private func startLevelMonitoring() {
        levelTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.updateAudioLevel()
            }
        }
    }

    private func stopLevelMonitoring() {
        levelTimer?.invalidate()
        levelTimer = nil
        silenceTimer?.invalidate()
        silenceTimer = nil
        audioLevel = 0.0
    }

    private func updateAudioLevel() {
        guard let recorder = audioRecorder, recorder.isRecording else { return }

        recorder.updateMeters()
        let averagePower = recorder.averagePower(forChannel: 0)

        // Convert dB to linear scale (0.0 to 1.0)
        // -160 dB is silence, 0 dB is max
        let normalizedLevel = max(0, (averagePower + 60) / 60)
        audioLevel = normalizedLevel

        // Silence detection for sentence boundaries
        if averagePower > silenceThreshold {
            // Speech detected
            speechDetected = true
            silenceTimer?.invalidate()
            silenceTimer = nil
        } else if speechDetected {
            // Silence after speech - start timer if not already running
            if silenceTimer == nil {
                silenceTimer = Timer.scheduledTimer(withTimeInterval: silenceDuration, repeats: false) { [weak self] _ in
                    Task { @MainActor in
                        self?.handleSentenceComplete()
                    }
                }
            }
        }
    }

    private func handleSentenceComplete() {
        guard let audioData = stopRecording() else { return }
        onSentenceComplete?(audioData)
    }
}

extension AudioRecorder: AVAudioRecorderDelegate {
    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        if !flag {
            print("Recording finished unsuccessfully")
        }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        if let error = error {
            print("Recording encode error: \(error)")
        }
    }
}
