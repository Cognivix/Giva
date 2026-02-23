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

    // MARK: - Session (server-driven state machine)

    func getSession() async throws -> SessionStateResponse {
        return try await get("/api/session")
    }

    func streamSession() -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/session/stream")
        // Session stream is long-lived — must NOT close on "error" or "done" events
        return sseStream(url: url, method: "GET", persistent: true)
    }

    func streamSessionRespond(response: String) -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/session/respond")
        return sseStream(url: url, method: "POST", body: OnboardingRequest(response: response))
    }

    // MARK: - Onboarding & Reset (legacy — use session endpoints instead)

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

    // MARK: - Goals

    func getGoals(tier: String? = nil, status: String? = nil) async throws -> GoalListResponse {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("api/goals"),
            resolvingAgainstBaseURL: false
        )!
        var queryItems: [URLQueryItem] = []
        if let tier { queryItems.append(URLQueryItem(name: "tier", value: tier)) }
        if let status { queryItems.append(URLQueryItem(name: "status", value: status)) }
        if !queryItems.isEmpty { components.queryItems = queryItems }
        guard let url = components.url else { throw APIError.networkError(URLError(.badURL)) }
        return try await getURL(url)
    }

    func createGoal(request: GoalRequest) async throws -> GoalItem {
        return try await post("api/goals", body: request)
    }

    func getGoal(id: Int) async throws -> GoalItem {
        return try await get("/api/goals/\(id)")
    }

    func updateGoal(id: Int, request: GoalUpdateRequest) async throws -> GoalItem {
        return try await put("api/goals/\(id)", body: request)
    }

    func updateGoalStatus(id: Int, status: String) async throws -> GoalItem {
        return try await post("api/goals/\(id)/status", body: GoalStatusUpdateRequest(status: status))
    }

    func addGoalProgress(id: Int, note: String, source: String = "user") async throws -> GoalProgressItem {
        return try await post("api/goals/\(id)/progress", body: GoalProgressRequest(note: note, source: source))
    }

    func getGoalProgress(id: Int, limit: Int = 20) async throws -> [GoalProgressItem] {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("api/goals/\(id)/progress"),
            resolvingAgainstBaseURL: false
        )!
        components.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        guard let url = components.url else { throw APIError.networkError(URLError(.badURL)) }
        return try await getURL(url)
    }

    func streamInferGoals() -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/goals/infer")
        return sseStream(url: url, method: "POST")
    }

    func streamStrategy(goalId: Int) -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/goals/\(goalId)/strategy")
        return sseStream(url: url, method: "POST")
    }

    func acceptStrategy(goalId: Int, strategyId: Int) async throws -> [String: Any] {
        let url = baseURL.appendingPathComponent("api/goals/\(goalId)/strategy/\(strategyId)/accept")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    func streamPlan(goalId: Int) -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/goals/\(goalId)/plan")
        return sseStream(url: url, method: "POST")
    }

    func acceptPlan(goalId: Int, planJson: String) async throws -> [String: Any] {
        let url = baseURL.appendingPathComponent("api/goals/\(goalId)/plan/accept")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(PlanAcceptRequest(planJson: planJson))
        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    func streamPlanReview() -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/goals/plan/review")
        return sseStream(url: url, method: "POST")
    }

    func streamGoalChat(goalId: Int, query: String) -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/goals/\(goalId)/chat")
        return sseStream(url: url, method: "POST", body: GoalChatRequest(query: query))
    }

    func getGoalMessages(goalId: Int, limit: Int = 50) async throws -> GoalMessagesResponse {
        return try await get("/api/goals/\(goalId)/messages?limit=\(limit)")
    }

    // MARK: - Daily Review

    func getReviewStatus() async throws -> ReviewStatusResponse {
        return try await get("/api/review/status")
    }

    func streamReviewStart() -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/review/start")
        return sseStream(url: url, method: "POST")
    }

    func respondReview(reviewId: Int, response: String) async throws -> ReviewSummaryResponse {
        return try await post(
            "api/review/respond",
            body: ReviewRespondRequest(reviewId: reviewId, response: response)
        )
    }

    func getReviewHistory(limit: Int = 7) async throws -> [ReviewHistoryItem] {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("api/review/history"),
            resolvingAgainstBaseURL: false
        )!
        components.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        guard let url = components.url else { throw APIError.networkError(URLError(.badURL)) }
        return try await getURL(url)
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

    // MARK: - Agent Queue

    func getAgentQueue(status: String? = nil, limit: Int = 20) async throws -> AgentQueueResponse {
        var path = "api/agents/queue?limit=\(limit)"
        if let status { path += "&status=\(status)" }
        return try await get(path)
    }

    func confirmAgent(jobId: String) async throws {
        let url = baseURL.appendingPathComponent("api/agents/confirm")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(AgentConfirmRequest(jobId: jobId))
        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
    }

    func cancelAgent(jobId: String) async throws {
        let url = baseURL.appendingPathComponent("api/agents/queue/\(jobId)/cancel")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
    }

    func getAgentJob(jobId: String) async throws -> AgentJobItem {
        return try await get("api/agents/queue/\(jobId)")
    }

    func taskAI(taskId: Int) async throws -> [String: Any] {
        let url = baseURL.appendingPathComponent("api/tasks/\(taskId)/ai")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw URLError(.cannotParseResponse)
        }
        return json
    }

    func goalBrainstorm(goalId: Int) async throws -> [String: Any] {
        let url = baseURL.appendingPathComponent("api/goals/\(goalId)/brainstorm")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        try checkResponse(response, data: data)
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw URLError(.cannotParseResponse)
        }
        return json
    }

    // MARK: - SSE Parser

    /// Parse an SSE stream from the server.
    ///
    /// - Parameters:
    ///   - persistent: When `true`, the stream stays open even after "done" or
    ///     "error" events (used by session stream which is long-lived).
    ///     When `false` (default), "done"/"error" closes the stream.
    /// Parse an SSE stream from the server using byte-level line splitting.
    ///
    /// **Why not `bytes.lines`?**  Swift's `AsyncLineSequence` silently drops
    /// empty lines, but SSE uses empty lines as event delimiters.  We read raw
    /// bytes and split on `\n` (stripping `\r`) to preserve empty lines.
    ///
    /// - Parameters:
    ///   - persistent: When `true`, the stream stays open even after "done" or
    ///     "error" events (used by session stream which is long-lived).
    ///     When `false` (default), "done"/"error" closes the stream.
    private func sseStream(
        url: URL,
        method: String,
        body: (some Encodable)? = Optional<String>.none,
        persistent: Bool = false
    ) -> AsyncThrowingStream<SSEEvent, Error> {
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
                    var lineBuffer = Data()

                    // Read raw bytes — preserves empty lines that bytes.lines drops.
                    for try await byte in bytes {
                        if byte == UInt8(ascii: "\n") {
                            let line = String(decoding: lineBuffer, as: UTF8.self)
                            lineBuffer.removeAll(keepingCapacity: true)

                            if line.hasPrefix("event: ") {
                                currentEvent = String(line.dropFirst(7))
                            } else if line.hasPrefix("data: ") {
                                currentData = String(line.dropFirst(6))
                            } else if line == "data:" {
                                currentData = ""
                            } else if line.hasPrefix(":") {
                                // SSE comment / keepalive ping — ignore
                            } else if line.isEmpty {
                                // Empty line = end of event block
                                if !currentEvent.isEmpty {
                                    continuation.yield(SSEEvent(event: currentEvent, data: currentData))

                                    // Finite streams close on terminal events;
                                    // persistent (session) streams stay open.
                                    if !persistent && (currentEvent == "done" || currentEvent == "error") {
                                        continuation.finish()
                                        return
                                    }
                                }
                                currentEvent = ""
                                currentData = ""
                            }
                        } else if byte != UInt8(ascii: "\r") {
                            lineBuffer.append(byte)
                        }
                        // \r bytes are silently stripped
                    }

                    // Stream ended (server closed connection)
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish(throwing: CancellationError())
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

    private func put<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
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
