// MockAPIService.swift - Configurable mock for unit testing ViewModels.

import Foundation
@testable import GivaApp

final class MockAPIService: APIServiceProtocol, @unchecked Sendable {
    // MARK: - Call tracking

    var healthCallCount = 0
    var getStatusCallCount = 0
    var getProfileCallCount = 0
    var getTasksCallCount = 0
    var updateTaskStatusCallCount = 0
    var getDismissedTasksCallCount = 0
    var restoreTaskCallCount = 0
    var triggerSyncCallCount = 0
    var triggerExtractCallCount = 0
    var triggerResetCallCount = 0
    var getSessionCallCount = 0
    var getAvailableModelsCallCount = 0
    var selectModelsCallCount = 0
    var getReviewStatusCallCount = 0
    var getAgentQueueCallCount = 0
    var confirmAgentCallCount = 0
    var cancelAgentCallCount = 0
    var taskAICallCount = 0
    var getTaskMessagesCallCount = 0
    var transcribeCallCount = 0
    var streamTranscribeCallCount = 0
    var getGoalsCallCount = 0
    var createGoalCallCount = 0
    var getGoalCallCount = 0
    var updateGoalCallCount = 0
    var updateGoalStatusCallCount = 0
    var addGoalProgressCallCount = 0
    var getGoalMessagesCallCount = 0
    var getBootstrapStatusCallCount = 0
    var startBootstrapCallCount = 0
    var retryBootstrapCallCount = 0
    var triggerUpgradeCallCount = 0
    var acceptStrategyCallCount = 0
    var acceptPlanCallCount = 0
    var respondReviewCallCount = 0
    var goalBrainstormCallCount = 0
    var getConfigCallCount = 0
    var updateConfigCallCount = 0

    // MARK: - Stub returns

    var healthResult: Result<HealthResponse, Error> = .success(
        HealthResponse(status: "ok", version: "1.0.0", commit: "abc123")
    )

    var getStatusResult: Result<StatusResponse, Error> = .success(
        StatusResponse(emails: 100, events: 50, pendingTasks: 5,
                       syncs: [], model: "test-model", modelLoaded: true)
    )

    var getProfileResult: Result<ProfileResponse, Error> = .success(
        ProfileResponse(displayName: "Test User", emailAddress: "test@example.com",
                        topTopics: ["Work"], avgResponseTimeMin: 30.0,
                        emailVolumeDaily: 10.0, summary: "Test profile", updatedAt: nil)
    )

    var getTasksResult: Result<TaskListResponse, Error> = .success(
        TaskListResponse(tasks: [], count: 0)
    )

    var updateTaskStatusResult: Result<UpdateTaskStatusResponse, Error> = .success(
        UpdateTaskStatusResponse(success: true, taskId: 1, status: "done")
    )

    var getDismissedTasksResult: Result<DismissedTaskListResponse, Error> = .success(
        DismissedTaskListResponse(tasks: [], count: 0)
    )

    var restoreTaskResult: Result<RestoreTaskResponse, Error> = .success(
        RestoreTaskResponse(success: true, taskId: 1)
    )

    var triggerSyncResult: Result<SyncResponse, Error> = .success(
        SyncResponse(mailSynced: 10, mailFiltered: 5, eventsSynced: 3,
                     profileUpdated: false, needsOnboarding: false)
    )

    var triggerExtractResult: Result<ExtractResponse, Error> = .success(
        ExtractResponse(tasksExtracted: 3)
    )

    var triggerResetResult: Result<ResetResponse, Error> = .success(
        ResetResponse(success: true, message: "Reset complete")
    )

    var getSessionResult: Result<SessionStateResponse, Error>?

    var getAvailableModelsResult: Result<AvailableModelsResponse, Error>?

    var selectModelsResult: Result<ModelSelectResponse, Error> = .success(
        ModelSelectResponse(success: true, message: "Models selected")
    )

    var getReviewStatusResult: Result<ReviewStatusResponse, Error> = .success(
        ReviewStatusResponse(due: false, lastReviewDate: nil)
    )

    var getAgentQueueResult: Result<AgentQueueResponse, Error> = .success(
        AgentQueueResponse(jobs: [], count: 0, activeCount: 0)
    )

    var taskAIResult: Result<[String: Any], Error> = .success(["status": "ok"])
    var getTaskMessagesResult: Result<GoalMessagesResponse, Error> = .success(
        GoalMessagesResponse(messages: [], count: 0)
    )

    var transcribeResult: Result<String, Error> = .success("Transcribed text")
    var streamTranscribeEvents: [SSEEvent] = []

    var getGoalsResult: Result<GoalListResponse, Error> = .success(
        GoalListResponse(goals: [], count: 0)
    )

    var createGoalResult: Result<GoalItem, Error>?
    var getGoalResult: Result<GoalItem, Error>?
    var updateGoalResult: Result<GoalItem, Error>?
    var updateGoalStatusResult: Result<GoalItem, Error>?
    var addGoalProgressResult: Result<GoalProgressItem, Error>?

    var getGoalMessagesResult: Result<GoalMessagesResponse, Error> = .success(
        GoalMessagesResponse(messages: [], count: 0)
    )

    var getBootstrapStatusResult: Result<BootstrapStatusResponse, Error>?
    var startBootstrapResult: Result<BootstrapStatusResponse, Error>?
    var retryBootstrapResult: Result<BootstrapStatusResponse, Error>?
    var triggerUpgradeResult: Result<UpgradeResponse, Error>?
    var acceptStrategyResult: Result<[String: Any], Error> = .success(["status": "ok"])
    var acceptPlanResult: Result<[String: Any], Error> = .success(["status": "ok"])
    var respondReviewResult: Result<ReviewSummaryResponse, Error> = .success(
        ReviewSummaryResponse(summary: "Review summary")
    )
    var goalBrainstormResult: Result<[String: Any], Error> = .success(["status": "ok"])

    var getConfigResult: Result<ConfigResponse, Error> = .success(
        ConfigResponse(
            llm: LLMConfigResponse(
                model: "test-model", filterModel: "test-filter",
                maxTokens: 2048, temperature: 0.7, topP: 0.9,
                contextBudgetTokens: 8000
            ),
            voice: VoiceConfigResponse(
                enabled: false, ttsModel: "test-tts", ttsVoice: "af_heart",
                sttModel: "test-stt", sampleRate: 24000
            ),
            power: PowerConfigResponse(
                enabled: true, batteryPauseThreshold: 20,
                batteryDeferHeavyThreshold: 50, thermalPauseThreshold: 3,
                thermalDeferHeavyThreshold: 2, modelIdleTimeoutMinutes: 20
            ),
            mail: MailConfigResponse(
                mailboxes: ["INBOX"], batchSize: 50,
                syncIntervalMinutes: 15, initialSyncMonths: 4,
                deepSyncMaxMonths: 24
            ),
            calendar: CalendarConfigResponse(
                syncWindowPastDays: 7, syncWindowFutureDays: 30,
                syncIntervalMinutes: 15
            ),
            agents: AgentsConfigResponse(
                enabled: true, routingEnabled: true, maxExecutionSeconds: 60
            ),
            goals: GoalsConfigResponse(
                strategyIntervalHours: 6, dailyReviewHour: 18,
                maxStrategiesPerRun: 1, planHorizonDays: 7
            ),
            vlm: VlmConfigResponse(
                enabled: false, model: "",
                pollIntervalSeconds: 3, actionDelayMs: 800
            )
        )
    )
    var updateConfigResult: Result<[String: Any], Error> = .success(["success": true])

    // MARK: - Error injection

    var shouldThrow: Error?

    private func throwOrReturn<T>(_ result: Result<T, Error>) throws -> T {
        if let error = shouldThrow { throw error }
        switch result {
        case .success(let value): return value
        case .failure(let error): throw error
        }
    }

    // MARK: - Protocol Implementation

    func health() async throws -> HealthResponse {
        healthCallCount += 1
        return try throwOrReturn(healthResult)
    }

    func getStatus() async throws -> StatusResponse {
        getStatusCallCount += 1
        return try throwOrReturn(getStatusResult)
    }

    func getProfile() async throws -> ProfileResponse {
        getProfileCallCount += 1
        return try throwOrReturn(getProfileResult)
    }

    func getConfig() async throws -> ConfigResponse {
        getConfigCallCount += 1
        return try throwOrReturn(getConfigResult)
    }

    func updateConfig(updates: [String: Any]) async throws -> [String: Any] {
        updateConfigCallCount += 1
        return try throwOrReturn(updateConfigResult)
    }

    func getTasks(status: String?, limit: Int) async throws -> TaskListResponse {
        getTasksCallCount += 1
        return try throwOrReturn(getTasksResult)
    }

    func updateTaskStatus(taskId: Int, status: String) async throws -> UpdateTaskStatusResponse {
        updateTaskStatusCallCount += 1
        return try throwOrReturn(updateTaskStatusResult)
    }

    func getDismissedTasks(limit: Int) async throws -> DismissedTaskListResponse {
        getDismissedTasksCallCount += 1
        return try throwOrReturn(getDismissedTasksResult)
    }

    func restoreTask(taskId: Int) async throws -> RestoreTaskResponse {
        restoreTaskCallCount += 1
        return try throwOrReturn(restoreTaskResult)
    }

    func triggerSync() async throws -> SyncResponse {
        triggerSyncCallCount += 1
        return try throwOrReturn(triggerSyncResult)
    }

    func triggerExtract() async throws -> ExtractResponse {
        triggerExtractCallCount += 1
        return try throwOrReturn(triggerExtractResult)
    }

    func triggerReset() async throws -> ResetResponse {
        triggerResetCallCount += 1
        return try throwOrReturn(triggerResetResult)
    }

    func getSession() async throws -> SessionStateResponse {
        getSessionCallCount += 1
        if let result = getSessionResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No session result configured"])
    }

    func getAvailableModels() async throws -> AvailableModelsResponse {
        getAvailableModelsCallCount += 1
        if let result = getAvailableModelsResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No models result configured"])
    }

    func selectModels(assistant: String, filter: String, vlm: String) async throws -> ModelSelectResponse {
        selectModelsCallCount += 1
        return try throwOrReturn(selectModelsResult)
    }

    func getReviewStatus() async throws -> ReviewStatusResponse {
        getReviewStatusCallCount += 1
        return try throwOrReturn(getReviewStatusResult)
    }

    func getAgentQueue(status: String?, limit: Int) async throws -> AgentQueueResponse {
        getAgentQueueCallCount += 1
        return try throwOrReturn(getAgentQueueResult)
    }

    func confirmAgent(jobId: String) async throws {
        confirmAgentCallCount += 1
        if let error = shouldThrow { throw error }
    }

    func cancelAgent(jobId: String) async throws {
        cancelAgentCallCount += 1
        if let error = shouldThrow { throw error }
    }

    func taskAI(taskId: Int) async throws -> [String: Any] {
        taskAICallCount += 1
        return try throwOrReturn(taskAIResult)
    }

    var getTaskDetailCallCount = 0
    var getTaskDetailResult: Result<TaskDetailResponse, Error>?
    func getTaskDetail(taskId: Int) async throws -> TaskDetailResponse {
        getTaskDetailCallCount += 1
        if let result = getTaskDetailResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No task detail result configured"])
    }

    func streamTaskChat(taskId: Int, query: String) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func getTaskMessages(taskId: Int, limit: Int) async throws -> GoalMessagesResponse {
        getTaskMessagesCallCount += 1
        return try throwOrReturn(getTaskMessagesResult)
    }

    func transcribe(audioData: Data, filename: String) async throws -> String {
        transcribeCallCount += 1
        return try throwOrReturn(transcribeResult)
    }

    func streamTranscribe(
        audioData: Data, filename: String, chunkId: String
    ) -> AsyncThrowingStream<SSEEvent, Error> {
        streamTranscribeCallCount += 1
        let events = streamTranscribeEvents
        return AsyncThrowingStream { continuation in
            for event in events {
                continuation.yield(event)
            }
            continuation.finish()
        }
    }

    // MARK: - SSE Streaming (return empty streams by default)

    func streamChat(query: String, voice: Bool) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func streamSession() -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func streamSessionRespond(response: String) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func streamSuggest() -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    // MARK: - Goals

    func getGoals(tier: String?, status: String?) async throws -> GoalListResponse {
        getGoalsCallCount += 1
        return try throwOrReturn(getGoalsResult)
    }

    func createGoal(request: GoalRequest) async throws -> GoalItem {
        createGoalCallCount += 1
        if let result = createGoalResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No createGoal result configured"])
    }

    func getGoal(id: Int) async throws -> GoalItem {
        getGoalCallCount += 1
        if let result = getGoalResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No getGoal result configured"])
    }

    func updateGoal(id: Int, request: GoalUpdateRequest) async throws -> GoalItem {
        updateGoalCallCount += 1
        if let result = updateGoalResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No updateGoal result configured"])
    }

    func updateGoalStatus(id: Int, status: String) async throws -> GoalItem {
        updateGoalStatusCallCount += 1
        if let result = updateGoalStatusResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No updateGoalStatus result configured"])
    }

    func addGoalProgress(id: Int, note: String, source: String) async throws -> GoalProgressItem {
        addGoalProgressCallCount += 1
        if let result = addGoalProgressResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No addGoalProgress result configured"])
    }

    func getGoalMessages(goalId: Int, limit: Int) async throws -> GoalMessagesResponse {
        getGoalMessagesCallCount += 1
        return try throwOrReturn(getGoalMessagesResult)
    }

    func streamInferGoals() -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func streamStrategy(goalId: Int) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func acceptStrategy(goalId: Int, strategyId: Int) async throws -> [String: Any] {
        acceptStrategyCallCount += 1
        return try throwOrReturn(acceptStrategyResult)
    }

    func streamPlan(goalId: Int) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func acceptPlan(goalId: Int, planJson: String) async throws -> [String: Any] {
        acceptPlanCallCount += 1
        return try throwOrReturn(acceptPlanResult)
    }

    func streamPlanReview() -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func streamGoalChat(goalId: Int, query: String) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func streamReviewStart() -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func respondReview(reviewId: Int, response: String) async throws -> ReviewSummaryResponse {
        respondReviewCallCount += 1
        return try throwOrReturn(respondReviewResult)
    }

    func goalBrainstorm(goalId: Int) async throws -> [String: Any] {
        goalBrainstormCallCount += 1
        return try throwOrReturn(goalBrainstormResult)
    }

    // MARK: - Conversation History

    var getConversationDatesResult: Result<ConversationDatesResponse, Error> =
        .success(ConversationDatesResponse(dates: [], count: 0))
    var getConversationMessagesResult: Result<ConversationMessagesResponse, Error> =
        .success(ConversationMessagesResponse(messages: [], count: 0))

    func getConversationDates(limit: Int) async throws -> ConversationDatesResponse {
        return try throwOrReturn(getConversationDatesResult)
    }

    func getConversationMessages(date: String, limit: Int) async throws -> ConversationMessagesResponse {
        return try throwOrReturn(getConversationMessagesResult)
    }

    // MARK: - Bootstrap

    func getBootstrapStatus() async throws -> BootstrapStatusResponse {
        getBootstrapStatusCallCount += 1
        if let result = getBootstrapStatusResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No bootstrap status configured"])
    }

    func startBootstrap() async throws -> BootstrapStatusResponse {
        startBootstrapCallCount += 1
        if let result = startBootstrapResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No start bootstrap result configured"])
    }

    func retryBootstrap() async throws -> BootstrapStatusResponse {
        retryBootstrapCallCount += 1
        if let result = retryBootstrapResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No retry bootstrap result configured"])
    }

    func streamBootstrapStatus() -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { $0.finish() }
    }

    func triggerUpgrade(projectRoot: String) async throws -> UpgradeResponse {
        triggerUpgradeCallCount += 1
        if let result = triggerUpgradeResult {
            return try throwOrReturn(result)
        }
        throw NSError(domain: "MockAPIService", code: -1,
                       userInfo: [NSLocalizedDescriptionKey: "No upgrade result configured"])
    }
}
