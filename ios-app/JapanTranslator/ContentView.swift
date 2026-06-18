import SwiftUI

struct ContentView: View {
    // Connection managers
    @StateObject private var serverDiscovery = ServerDiscovery()
    @StateObject private var bluetoothManager = BluetoothManager()
    @StateObject private var audioRecorder = AudioRecorder()
    @StateObject private var audioPlayer = AudioPlayer()

    // State
    @State private var translationText: String = ""
    @State private var japaneseText: String = ""  // Japanese text to display for EN->JA
    @State private var isTranslating: Bool = false
    @State private var errorMessage: String?
    @State private var activeMode: TranslationMode?
    @State private var hasPermission: Bool = false
    @State private var connectionMode: ConnectionMode = .bluetooth  // Default to Bluetooth
    @State private var lastTranslationMode: TranslationMode?

    private let translationService = TranslationService()

    enum TranslationMode {
        case japaneseToEnglish
        case englishToJapanese
    }

    enum ConnectionMode: String, CaseIterable {
        case wifi = "WiFi"
        case bluetooth = "Bluetooth"
    }

    // Computed properties for connection status
    private var isConnected: Bool {
        switch connectionMode {
        case .wifi:
            return serverDiscovery.serverURL != nil
        case .bluetooth:
            return bluetoothManager.isConnected
        }
    }

    private var statusMessage: String {
        switch connectionMode {
        case .wifi:
            return serverDiscovery.statusMessage
        case .bluetooth:
            return bluetoothManager.statusMessage
        }
    }

    private var isSearching: Bool {
        switch connectionMode {
        case .wifi:
            return serverDiscovery.isSearching
        case .bluetooth:
            return bluetoothManager.isScanning
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Header
            headerView

            // Connection mode picker
            connectionModePicker

            // Connection status
            connectionStatusView

            // Translation display
            translationDisplayView

            Spacer()

            // Translation buttons
            translationButtonsView

            // Instructions
            instructionsView
        }
        .padding()
        .background(Color(.systemBackground))
        .task {
            hasPermission = await audioRecorder.requestPermission()
        }
        .onChange(of: bluetoothManager.translatedText) { _, newValue in
            if !newValue.isEmpty {
                // Check if this is Japanese text (for EN->JA) or English text (for JA->EN)
                if lastTranslationMode == .englishToJapanese {
                    japaneseText = newValue
                    translationText = ""
                } else {
                    translationText = newValue
                    japaneseText = ""
                }
                isTranslating = false
            }
        }
        .onChange(of: bluetoothManager.receivedAudio) { _, newValue in
            if let audioData = newValue {
                audioPlayer.play(audioData: audioData)
                isTranslating = false
            }
        }
    }

    // MARK: - Header

    private var headerView: some View {
        VStack(spacing: 4) {
            Text("Local Translator")
                .font(.title)
                .fontWeight(.bold)
            Text("ローカル翻訳")
                .font(.headline)
                .foregroundColor(.secondary)
        }
        .padding(.top, 20)
        .padding(.bottom, 10)
    }

    // MARK: - Connection Mode Picker

    private var connectionModePicker: some View {
        Picker("Connection", selection: $connectionMode) {
            ForEach(ConnectionMode.allCases, id: \.self) { mode in
                Text(mode.rawValue).tag(mode)
            }
        }
        .pickerStyle(.segmented)
        .padding(.horizontal)
        .padding(.bottom, 8)
    }

    // MARK: - Connection Status

    private var connectionStatusView: some View {
        HStack {
            Circle()
                .fill(isConnected ? Color.green : Color.orange)
                .frame(width: 10, height: 10)

            Text(statusMessage)
                .font(.subheadline)
                .foregroundColor(.secondary)

            Spacer()

            if isSearching {
                ProgressView()
                    .scaleEffect(0.8)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(Color(.secondarySystemBackground))
        .cornerRadius(8)
        .padding(.bottom, 16)
    }

    // MARK: - Translation Display

    private var translationDisplayView: some View {
        VStack(alignment: .center, spacing: 12) {
            if let error = errorMessage {
                Text(error)
                    .foregroundColor(.red)
                    .font(.body)
            } else if isTranslating {
                VStack(spacing: 12) {
                    HStack {
                        ProgressView()
                        Text("Translating... / 翻訳中...")
                            .foregroundColor(.secondary)
                    }

                    Button(action: {
                        resetTranslation()
                    }) {
                        Text("Cancel / キャンセル")
                            .font(.footnote)
                            .foregroundColor(.white)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 8)
                            .background(Color.red.opacity(0.8))
                            .cornerRadius(8)
                    }
                }
            } else if !translationText.isEmpty || !japaneseText.isEmpty {
                // Show translation text
                if !translationText.isEmpty {
                    Text(translationText)
                        .font(.title3)
                        .fontWeight(.medium)
                        .multilineTextAlignment(.center)
                }

                // Show Japanese text for EN->JA translations
                if !japaneseText.isEmpty {
                    Text(japaneseText)
                        .font(.title2)
                        .fontWeight(.semibold)
                        .foregroundColor(.primary)
                        .multilineTextAlignment(.center)
                        .padding(.top, 4)
                }

                // Replay button for audio translations
                if lastTranslationMode == .englishToJapanese && audioPlayer.canReplay {
                    Button(action: {
                        audioPlayer.replay()
                    }) {
                        HStack {
                            Image(systemName: audioPlayer.isPlaying ? "speaker.wave.3.fill" : "arrow.counterclockwise.circle.fill")
                                .font(.title2)
                            Text(audioPlayer.isPlaying ? "Playing..." : "Replay / 再生")
                                .font(.headline)
                        }
                        .foregroundColor(.white)
                        .padding(.horizontal, 24)
                        .padding(.vertical, 12)
                        .background(audioPlayer.isPlaying ? Color.orange : Color.blue)
                        .cornerRadius(25)
                    }
                    .disabled(audioPlayer.isPlaying)
                    .padding(.top, 8)
                }
            } else {
                Text("Translation will appear here\n翻訳がここに表示されます")
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 180, alignment: .center)
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(12)
    }

    // MARK: - Translation Buttons

    private var translationButtonsView: some View {
        HStack(spacing: 16) {
            // Japanese to English button
            TranslationButton(
                title: "日本語",
                subtitle: "Japanese",
                direction: "→ 英語",
                isActive: activeMode == .japaneseToEnglish,
                isDisabled: !hasPermission || !isConnected,
                audioLevel: activeMode == .japaneseToEnglish ? audioRecorder.audioLevel : 0
            )
            .simultaneousGesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        if activeMode == nil && hasPermission && isConnected {
                            startRecording(mode: .japaneseToEnglish)
                        }
                    }
                    .onEnded { _ in
                        if activeMode == .japaneseToEnglish {
                            stopRecording()
                        }
                    }
            )

            // English to Japanese button
            TranslationButton(
                title: "English",
                subtitle: "英語",
                direction: "→ 日本語",
                isActive: activeMode == .englishToJapanese,
                isDisabled: !hasPermission || !isConnected,
                audioLevel: activeMode == .englishToJapanese ? audioRecorder.audioLevel : 0
            )
            .simultaneousGesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        if activeMode == nil && hasPermission && isConnected {
                            startRecording(mode: .englishToJapanese)
                        }
                    }
                    .onEnded { _ in
                        if activeMode == .englishToJapanese {
                            stopRecording()
                        }
                    }
            )
        }
        .padding(.vertical, 20)
    }

    // MARK: - Instructions

    private var instructionsView: some View {
        VStack(spacing: 4) {
            Text("Hold to speak")
                .font(.footnote)
            Text("押して話す")
                .font(.footnote)
                .foregroundColor(.secondary)

            if !hasPermission {
                Text("Microphone permission required")
                    .font(.caption)
                    .foregroundColor(.red)
                    .padding(.top, 4)
            }
        }
        .padding(.bottom, 20)
    }

    // MARK: - Recording Actions

    private func startRecording(mode: TranslationMode) {
        activeMode = mode
        errorMessage = nil
        audioRecorder.startRecording()
    }

    private func stopRecording() {
        guard let mode = activeMode,
              let audioData = audioRecorder.stopRecording() else {
            activeMode = nil
            return
        }

        activeMode = nil
        isTranslating = true

        switch connectionMode {
        case .wifi:
            translateViaWiFi(mode: mode, audioData: audioData)
        case .bluetooth:
            translateViaBluetooth(mode: mode, audioData: audioData)
        }
    }

    private func translateViaWiFi(mode: TranslationMode, audioData: Data) {
        guard let serverURL = serverDiscovery.serverURL else {
            isTranslating = false
            errorMessage = "Not connected to server"
            return
        }

        // Mirror the Bluetooth path's state handling so WiFi gets the same UX:
        // lastTranslationMode gates the Replay button, and we clear this
        // direction's previous result before the new one arrives.
        lastTranslationMode = mode
        if mode == .englishToJapanese {
            japaneseText = ""
        } else {
            translationText = ""
        }

        Task {
            do {
                switch mode {
                case .japaneseToEnglish:
                    let text = try await translationService.translateJapaneseToEnglish(
                        audioData: audioData,
                        serverURL: serverURL
                    )
                    translationText = text
                    japaneseText = ""

                case .englishToJapanese:
                    let result = try await translationService.translateEnglishToJapanese(
                        audioData: audioData,
                        serverURL: serverURL
                    )
                    // Display the Japanese text the server returned alongside the
                    // audio (via the X-Translation-Text header), like Bluetooth does.
                    // If the server provided no text, fall back to a playback
                    // indicator so the result block — and its Replay button — still
                    // render (translationDisplayView only shows them when some text
                    // is present).
                    if result.text.isEmpty {
                        translationText = "[Playing Japanese audio / 日本語音声を再生中]"
                        japaneseText = ""
                    } else {
                        japaneseText = result.text
                        translationText = ""
                    }
                    audioPlayer.play(audioData: result.audio)
                }
                errorMessage = nil
            } catch {
                errorMessage = error.localizedDescription
                translationText = ""
                japaneseText = ""
            }
            isTranslating = false
        }
    }

    private func translateViaBluetooth(mode: TranslationMode, audioData: Data) {
        lastTranslationMode = mode
        // Clear previous results
        if mode == .englishToJapanese {
            japaneseText = ""
        } else {
            translationText = ""
        }

        switch mode {
        case .japaneseToEnglish:
            bluetoothManager.translateJapaneseToEnglish(audioData: audioData)
        case .englishToJapanese:
            bluetoothManager.translateEnglishToJapanese(audioData: audioData)
        }
        // Results will come via onChange handlers
    }

    private func resetTranslation() {
        isTranslating = false
        activeMode = nil
        errorMessage = nil
        audioRecorder.stopRecording()
    }
}

// MARK: - Translation Button Component

struct TranslationButton: View {
    let title: String
    let subtitle: String
    let direction: String
    let isActive: Bool
    let isDisabled: Bool
    let audioLevel: Float

    var body: some View {
        VStack(spacing: 8) {
            // Microphone icon with level indicator
            ZStack {
                Circle()
                    .fill(isActive ? Color.red.opacity(0.2) : Color.clear)
                    .frame(width: 50 + CGFloat(audioLevel * 30), height: 50 + CGFloat(audioLevel * 30))
                    .animation(.easeOut(duration: 0.1), value: audioLevel)

                Image(systemName: isActive ? "mic.fill" : "mic")
                    .font(.title)
                    .foregroundColor(isActive ? .red : (isDisabled ? .gray : .primary))
            }
            .frame(height: 60)

            Text(title)
                .font(.headline)
                .foregroundColor(isDisabled ? .gray : .primary)

            Text(subtitle)
                .font(.subheadline)
                .foregroundColor(.secondary)

            Text(direction)
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 20)
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(isActive ? Color.red.opacity(0.1) : Color(.secondarySystemBackground))
                .overlay(
                    RoundedRectangle(cornerRadius: 16)
                        .stroke(isActive ? Color.red : Color.clear, lineWidth: 2)
                )
        )
        .opacity(isDisabled ? 0.6 : 1.0)
    }
}

#Preview {
    ContentView()
}
