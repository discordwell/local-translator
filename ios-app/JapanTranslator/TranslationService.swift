import Foundation

/// HTTP client for communicating with the translation server.
class TranslationService {
    private let session: URLSession

    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60  // Translation can take a while
        config.timeoutIntervalForResource = 120
        self.session = URLSession(configuration: config)
    }

    /// Translate Japanese audio to English text.
    /// - Parameters:
    ///   - audioData: WAV audio data containing Japanese speech
    ///   - serverURL: Base URL of the translation server
    /// - Returns: English text translation
    func translateJapaneseToEnglish(audioData: Data, serverURL: URL) async throws -> String {
        let endpoint = serverURL.appendingPathComponent("translate/ja-to-en")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"

        // Create multipart form data
        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"audio\"; filename=\"audio.wav\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/wav\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        request.httpBody = body

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw TranslationError.invalidResponse
        }

        guard httpResponse.statusCode == 200 else {
            throw TranslationError.serverError(statusCode: httpResponse.statusCode)
        }

        // Parse JSON response
        let decoder = JSONDecoder()
        let result = try decoder.decode(TextTranslationResponse.self, from: data)

        return result.text
    }

    /// Translate English audio to Japanese audio.
    /// - Parameters:
    ///   - audioData: WAV audio data containing English speech
    ///   - serverURL: Base URL of the translation server
    /// - Returns: WAV audio data containing Japanese speech
    func translateEnglishToJapanese(audioData: Data, serverURL: URL) async throws -> Data {
        let endpoint = serverURL.appendingPathComponent("translate/en-to-ja")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"

        // Create multipart form data
        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"audio\"; filename=\"audio.wav\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/wav\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        request.httpBody = body

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw TranslationError.invalidResponse
        }

        guard httpResponse.statusCode == 200 else {
            throw TranslationError.serverError(statusCode: httpResponse.statusCode)
        }

        return data
    }

    /// Check if the server is healthy and the model is loaded.
    func checkHealth(serverURL: URL) async throws -> Bool {
        let endpoint = serverURL.appendingPathComponent("health")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "GET"

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            return false
        }

        let decoder = JSONDecoder()
        let health = try decoder.decode(HealthResponse.self, from: data)

        return health.status == "ok" && health.modelLoaded
    }
}

// MARK: - Response Types

struct TextTranslationResponse: Codable {
    let text: String
}

struct HealthResponse: Codable {
    let status: String
    let modelLoaded: Bool

    enum CodingKeys: String, CodingKey {
        case status
        case modelLoaded = "model_loaded"
    }
}

// MARK: - Errors

enum TranslationError: LocalizedError {
    case invalidResponse
    case serverError(statusCode: Int)
    case noServerConnection

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "Invalid response from server"
        case .serverError(let statusCode):
            return "Server error (status \(statusCode))"
        case .noServerConnection:
            return "Not connected to server"
        }
    }
}
