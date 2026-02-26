// APIServiceProtocol.swift - Protocol for dependency injection and testing.

import Foundation

protocol APIServiceProtocol: AnyObject, Sendable {
    // MARK: - JSON Endpoints (GivaViewModel)

    func health() async throws -> HealthResponse
    func getStatus() async throws -> StatusResponse
    func getProfile() async throws -> ProfileResponse
    func getTasks(status: String?, limit: Int) async throws -> TaskListResponse
    func updateTaskStatus(taskId: Int, status: String) async throws -> UpdateTaskStatusResponse
    func triggerSync() async throws -> SyncResponse
    func triggerExtract() async throws -> ExtractResponse
    func triggerReset() async throws -> ResetResponse
    func getConfig() async throws -> ConfigResponse
    func updateConfig(updates: [String: Any]) async throws -> [String: Any]
    func getSession() async throws -> SessionStateResponse
    func getAvailableModels() async throws -> AvailableModelsResponse
    func selectModels(assistant: String, filter: String) async throws -> ModelSelectResponse
    func getReviewStatus() async throws -> ReviewStatusResponse
    func getAgentQueue(status: String?, limit: Int) async throws -> AgentQueueResponse
    func confirmAgent(jobId: String) async throws
    func cancelAgent(jobId: String) async throws
    func taskAI(taskId: Int) async throws -> [String: Any]
    func streamTaskChat(taskId: Int, query: String) -> AsyncThrowingStream<SSEEvent, Error>
    func getTaskMessages(taskId: Int, limit: Int) async throws -> GoalMessagesResponse
    func transcribe(audioData: Data, filename: String) async throws -> String
    func streamTranscribe(audioData: Data, filename: String, chunkId: String) -> AsyncThrowingStream<SSEEvent, Error>

    // MARK: - SSE Streaming (GivaViewModel)

    func streamChat(query: String, voice: Bool) -> AsyncThrowingStream<SSEEvent, Error>
    func streamSession() -> AsyncThrowingStream<SSEEvent, Error>
    func streamSessionRespond(response: String) -> AsyncThrowingStream<SSEEvent, Error>
    func streamSuggest() -> AsyncThrowingStream<SSEEvent, Error>

    // MARK: - Goals (GoalsViewModel)

    func getGoals(tier: String?, status: String?) async throws -> GoalListResponse
    func createGoal(request: GoalRequest) async throws -> GoalItem
    func getGoal(id: Int) async throws -> GoalItem
    func updateGoal(id: Int, request: GoalUpdateRequest) async throws -> GoalItem
    func updateGoalStatus(id: Int, status: String) async throws -> GoalItem
    func addGoalProgress(id: Int, note: String, source: String) async throws -> GoalProgressItem
    func getGoalMessages(goalId: Int, limit: Int) async throws -> GoalMessagesResponse
    func streamInferGoals() -> AsyncThrowingStream<SSEEvent, Error>
    func streamStrategy(goalId: Int) -> AsyncThrowingStream<SSEEvent, Error>
    func acceptStrategy(goalId: Int, strategyId: Int) async throws -> [String: Any]
    func streamPlan(goalId: Int) -> AsyncThrowingStream<SSEEvent, Error>
    func acceptPlan(goalId: Int, planJson: String) async throws -> [String: Any]
    func streamPlanReview() -> AsyncThrowingStream<SSEEvent, Error>
    func streamGoalChat(goalId: Int, query: String) -> AsyncThrowingStream<SSEEvent, Error>
    func streamReviewStart() -> AsyncThrowingStream<SSEEvent, Error>
    func respondReview(reviewId: Int, response: String) async throws -> ReviewSummaryResponse
    func goalBrainstorm(goalId: Int) async throws -> [String: Any]

    // MARK: - Conversation History

    func getConversationDates(limit: Int) async throws -> ConversationDatesResponse
    func getConversationMessages(date: String, limit: Int) async throws -> ConversationMessagesResponse

    // MARK: - Bootstrap (BootstrapManager)

    func getBootstrapStatus() async throws -> BootstrapStatusResponse
    func startBootstrap() async throws -> BootstrapStatusResponse
    func retryBootstrap() async throws -> BootstrapStatusResponse
    func streamBootstrapStatus() -> AsyncThrowingStream<SSEEvent, Error>
    func triggerUpgrade(projectRoot: String) async throws -> UpgradeResponse
}

// Default parameter values for callers using the protocol type.
extension APIServiceProtocol {
    func getTasks(status: String? = "pending", limit: Int = 50) async throws -> TaskListResponse {
        try await getTasks(status: status, limit: limit)
    }
    func streamChat(query: String, voice: Bool = false) -> AsyncThrowingStream<SSEEvent, Error> {
        streamChat(query: query, voice: voice)
    }
    func transcribe(audioData: Data, filename: String = "recording.wav") async throws -> String {
        try await transcribe(audioData: audioData, filename: filename)
    }
    func streamTranscribe(
        audioData: Data, filename: String = "recording.wav", chunkId: String = "0"
    ) -> AsyncThrowingStream<SSEEvent, Error> {
        streamTranscribe(audioData: audioData, filename: filename, chunkId: chunkId)
    }
    func getGoals(tier: String? = nil, status: String? = nil) async throws -> GoalListResponse {
        try await getGoals(tier: tier, status: status)
    }
    func addGoalProgress(
        id: Int, note: String, source: String = "user"
    ) async throws -> GoalProgressItem {
        try await addGoalProgress(id: id, note: note, source: source)
    }
    func getGoalMessages(goalId: Int, limit: Int = 50) async throws -> GoalMessagesResponse {
        try await getGoalMessages(goalId: goalId, limit: limit)
    }
    func getTaskMessages(taskId: Int, limit: Int = 50) async throws -> GoalMessagesResponse {
        try await getTaskMessages(taskId: taskId, limit: limit)
    }
    func getAgentQueue(
        status: String? = nil, limit: Int = 20
    ) async throws -> AgentQueueResponse {
        try await getAgentQueue(status: status, limit: limit)
    }
    func getConversationDates(limit: Int = 30) async throws -> ConversationDatesResponse {
        try await getConversationDates(limit: limit)
    }
    func getConversationMessages(date: String, limit: Int = 200) async throws -> ConversationMessagesResponse {
        try await getConversationMessages(date: date, limit: limit)
    }
}
