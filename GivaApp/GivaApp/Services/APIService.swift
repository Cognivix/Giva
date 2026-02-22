// APIService.swift - URLSession wrapper with SSE streaming support.

import Foundation

enum APIError: LocalizedError {
    case serverNotRunning
    case httpError(Int, String)
    case decodingError(Error)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .serverNotRunning:
            return "Server is not running"
        case .httpError(let code, let msg):
            return "HTTP \(code): \(msg)"
        case .decodingError(let err):
            return "Decoding error: \(err.localizedDescription)"
        case .networkError(let err):
            return err.localizedDescription
        }
    }
}

class APIService {
    let baseURL: URL
    private let session: URLSession
    /// Dedicated session for SSE streams — long/no timeout so downloads can report for hours.
    private let sseSession: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    init(baseURL: URL) {
        self.baseURL = baseURL
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 300   // 5 min for sync/extract
        config.timeoutIntervalForResource = 600
        self.session = URLSession(configuration: config)

        let sseConfig = URLSessionConfiguration.default
        sseConfig.timeoutIntervalForRequest = 86400   // 24h — SSE events arrive every ~2s
        sseConfig.timeoutIntervalForResource = 86400  // 24h — downloads can take hours
        self.sseSession = URLSession(configuration: sseConfig)
    }

    // MARK: - JSON Endpoints

    func health() async throws -> HealthResponse {
        return try await get("/api/health")
    }

    func getStatus() async throws -> StatusResponse {
        return try await get("/api/status")
    }

    func getProfile() async throws -> ProfileResponse {
        return try await get("/api/profile")
    }

    func getTasks(status: String? = "pending", limit: Int = 50) async throws -> TaskListResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/tasks"), resolvingAgainstBaseURL: false)!
        var queryItems: [URLQueryItem] = []
        if let status = status {
            queryItems.append(URLQueryItem(name: "status", value: status))
        }
        queryItems.append(URLQueryItem(name: "limit", value: String(limit)))
        components.queryItems = queryItems

        guard let url = components.url else {
            throw APIError.networkError(URLError(.badURL))
        }
        return try await getURL(url)
    }

    func updateTaskStatus(taskId: Int, status: String) async throws -> UpdateTaskStatusResponse {
        return try await post("api/tasks/\(taskId)/status", body: UpdateTaskStatusRequest(status: status))
    }

    func triggerSync() async throws -> SyncResponse {
        return try await postNoBody("api/sync")
    }

    func triggerExtract() async throws -> ExtractResponse {
        return try await postNoBody("api/extract")
    }

    // MARK: - SSE Streaming

    func streamChat(query: String, voice: Bool = false) -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/chat")
        return sseStream(url: url, method: "POST", body: ChatRequest(query: query, voice: voice))
    }

    func transcribe(audioData: Data, filename: String = "recording.wav") async throws -> String {
        let url = baseURL.appendingPathComponent("api/transcribe")
        let boundary = UUID().uuidString
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/wav\r\n\r\n".data(using: .utf8)!)
        body.append(audioData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body

        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
        let result = try decoder.decode(TranscribeResponse.self, from: data)
        return result.text
    }

    // MARK: - Onboarding & Reset

    func getOnboardingStatus() async throws -> OnboardingStatusResponse {
        return try await get("/api/onboarding/status")
    }

    func streamOnboardingStart() -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/onboarding/start")
        return sseStream(url: url, method: "POST")
    }

    func streamOnboardingRespond(response: String) -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/onboarding/respond")
        return sseStream(url: url, method: "POST", body: OnboardingRequest(response: response))
    }

    func triggerReset() async throws -> ResetResponse {
        return try await postNoBody("api/reset")
    }

    func streamSuggest() -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/suggest")
        return sseStream(url: url, method: "GET")
    }

    // MARK: - Model Management

    func getModelStatus() async throws -> ModelStatusResponse {
        return try await get("/api/models/status")
    }

    func getAvailableModels() async throws -> AvailableModelsResponse {
        return try await get("/api/models/available")
    }

    func selectModels(assistant: String, filter: String) async throws -> ModelSelectResponse {
        return try await post("api/models/select", body: ModelSelectRequest(
            assistantModel: assistant,
            filterModel: filter
        ))
    }

    func streamModelDownload(modelId: String) -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/models/download")
        return sseStream(url: url, method: "POST", body: ModelDownloadRequest(modelId: modelId))
    }

    // MARK: - Bootstrap

    func getBootstrapStatus() async throws -> BootstrapStatusResponse {
        return try await get("/api/bootstrap/status")
    }

    func startBootstrap() async throws -> BootstrapStatusResponse {
        return try await postNoBody("api/bootstrap/start")
    }

    func retryBootstrap() async throws -> BootstrapStatusResponse {
        return try await postNoBody("api/bootstrap/retry")
    }

    func streamBootstrapStatus() -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/bootstrap/stream")
        return sseStream(url: url, method: "GET")
    }

    func triggerUpgrade(projectRoot: String) async throws -> UpgradeResponse {
        return try await post("api/upgrade", body: UpgradeRequest(projectRoot: projectRoot))
    }

    // MARK: - SSE Parser

    private func sseStream(url: URL, method: String, body: (some Encodable)? = Optional<String>.none) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            Task {
                var request = URLRequest(url: url)
                request.httpMethod = method
                request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

                if let body = body {
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.httpBody = try? encoder.encode(body)
                }

                do {
                    let (bytes, response) = try await sseSession.bytes(for: request)

                    guard let httpResponse = response as? HTTPURLResponse,
                          httpResponse.statusCode == 200 else {
                        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                        continuation.finish(throwing: APIError.httpError(code, "SSE connection failed"))
                        return
                    }

                    var currentEvent = ""
                    var currentData = ""

                    for try await line in bytes.lines {
                        if line.hasPrefix("event: ") {
                            currentEvent = String(line.dropFirst(7))
                        } else if line.hasPrefix("data: ") {
                            currentData = String(line.dropFirst(6))
                        } else if line == "data:" {
                            currentData = ""
                        } else if line.isEmpty {
                            // End of event block
                            if !currentEvent.isEmpty {
                                let event = SSEEvent(event: currentEvent, data: currentData)
                                continuation.yield(event)

                                if currentEvent == "done" || currentEvent == "error" {
                                    continuation.finish()
                                    return
                                }
                            }
                            currentEvent = ""
                            currentData = ""
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }

    // MARK: - Generic HTTP Helpers

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        return try await getURL(url)
    }

    private func getURL<T: Decodable>(_ url: URL) async throws -> T {
        let (data, response) = try await session.data(from: url)
        try checkResponse(response, data: data)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    private func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)

        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    private func postNoBody<T: Decodable>(_ path: String) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"

        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    private func checkResponse(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200...299).contains(http.statusCode) else {
            let detail = (try? decoder.decode(ErrorResponse.self, from: data))?.detail ?? "Unknown error"
            throw APIError.httpError(http.statusCode, detail)
        }
    }
}
