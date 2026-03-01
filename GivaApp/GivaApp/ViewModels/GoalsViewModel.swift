// GoalsViewModel.swift - State management for the Goals window.

import SwiftUI
import Observation

@MainActor @Observable
class GoalsViewModel {
    let apiService: any APIServiceProtocol

    // Goal list
    var goals: [GoalItem] = []
    var isLoading: Bool = false
    var errorMessage: String?

    // Selection
    var goalDetail: GoalItem?
    var isLoadingDetail: Bool = false

    // Daily review
    var isDailyReviewDue: Bool = false
    var isReviewStreaming: Bool = false
    var reviewStreamText: String = ""
    var reviewId: Int?

    // Intelligence streaming
    var isInferring: Bool = false
    var inferStreamText: String = ""
    var isStrategyStreaming: Bool = false
    var isStrategyThinking: Bool = false
    var strategyStreamText: String = ""
    var isPlanStreaming: Bool = false
    var isPlanThinking: Bool = false
    var planStreamText: String = ""
    var isPlanReviewStreaming: Bool = false
    var planReviewStreamText: String = ""

    // Goal chat
    var goalChatMessages: [ChatMessage] = []
    var isGoalChatStreaming: Bool = false
    var goalChatInput: String = ""

    // Create/Edit sheet
    var showCreateSheet: Bool = false
    var showEditSheet: Bool = false

    // Agent state for goal chat
    var pendingConfirmation: AgentConfirmation?

    // Programmatic selection
    var pendingSelection: Int?

    // Active streaming task (for cancellation)
    private var streamTask: Task<Void, Never>?
    // Detail fetch task (cancelled on re-selection)
    private var detailTask: Task<Void, Never>?
    // The goal ID we're currently loading detail for
    private var loadingGoalId: Int?

    init(apiService: any APIServiceProtocol) {
        self.apiService = apiService
    }

    // MARK: - Goal List

    func loadGoals() async {
        isLoading = true
        errorMessage = nil
        do {
            let response = try await apiService.getGoals(status: "active")
            goals = response.goals
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    // MARK: - Selection

    /// Called by the view's `.task(id:)` when sidebar selection changes.
    /// Fetches full detail for the selected goal asynchronously.
    /// Safe to call repeatedly with the same ID — early-returns if already loaded.
    func loadDetail(for goalId: Int?) async {
        guard let goalId else {
            // Deselected — clear everything
            loadingGoalId = nil
            goalDetail = nil
            isLoadingDetail = false
            goalChatInput = ""
            goalChatMessages = []
            return
        }

        // Already showing this goal — nothing to do
        if loadingGoalId == goalId, goalDetail?.id == goalId {
            return
        }

        loadingGoalId = goalId
        isLoadingDetail = true

        // Show lightweight placeholder from sidebar data immediately
        // (keeps the detail pane populated while the API call runs)
        if goalDetail?.id != goalId {
            goalDetail = goals.first { $0.id == goalId }
            goalChatInput = ""
            goalChatMessages = []
        }

        do {
            let detail = try await apiService.getGoal(id: goalId)
            guard loadingGoalId == goalId else { return }
            goalDetail = detail

            let history = try await apiService.getGoalMessages(goalId: goalId)
            guard loadingGoalId == goalId else { return }
            goalChatMessages = ChatMessage.fromHistory(
                history.messages.map { (role: $0.role, content: $0.content, type: $0.type) }
            )
        } catch is CancellationError {
            return
        } catch {
            guard loadingGoalId == goalId else { return }
            errorMessage = error.localizedDescription
            // On error, keep the placeholder rather than showing "Select a Goal"
        }
        guard loadingGoalId == goalId else { return }
        isLoadingDetail = false
    }

    func refreshSelectedGoal() async {
        guard let id = loadingGoalId else { return }
        do {
            let detail = try await apiService.getGoal(id: id)
            guard loadingGoalId == id else { return }
            goalDetail = detail
        } catch {
            // Non-critical
        }
    }

    /// Navigate to a goal programmatically (e.g. after create, child tap).
    /// Sets pendingSelection which the view picks up.
    func navigateTo(goalId: Int) {
        pendingSelection = goalId
    }

    // MARK: - Goal CRUD

    func createGoal(
        title: String,
        tier: String,
        description: String = "",
        category: String = "",
        parentId: Int? = nil,
        priority: String = "medium",
        targetDate: String? = nil
    ) async {
        errorMessage = nil
        do {
            let request = GoalRequest(
                title: title,
                tier: tier,
                description: description,
                category: category,
                parentId: parentId,
                priority: priority,
                targetDate: targetDate
            )
            let newGoal = try await apiService.createGoal(request: request)
            await loadGoals()
            navigateTo(goalId: newGoal.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func updateGoal(
        id: Int,
        title: String? = nil,
        description: String? = nil,
        category: String? = nil,
        priority: String? = nil,
        targetDate: String? = nil
    ) async {
        errorMessage = nil
        do {
            let request = GoalUpdateRequest(
                title: title,
                description: description,
                category: category,
                priority: priority,
                targetDate: targetDate
            )
            _ = try await apiService.updateGoal(id: id, request: request)
            await loadGoals()
            await refreshSelectedGoal()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func updateGoalStatus(id: Int, status: String) async {
        errorMessage = nil
        do {
            _ = try await apiService.updateGoalStatus(id: id, status: status)
            await loadGoals()
            if loadingGoalId == id {
                await refreshSelectedGoal()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addProgress(goalId: Int, note: String) async {
        errorMessage = nil
        do {
            _ = try await apiService.addGoalProgress(id: goalId, note: note)
            await refreshSelectedGoal()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    // MARK: - Intelligence: Infer Goals

    func inferGoals() {
        guard !isInferring else { return }
        isInferring = true
        inferStreamText = ""
        errorMessage = nil

        streamTask = Task {
            do {
                let stream = apiService.streamInferGoals()
                for try await event in stream {
                    if event.event == "token" {
                        inferStreamText += event.data
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            isInferring = false
        }
    }

    // MARK: - Intelligence: Strategy

    func generateStrategy(goalId: Int) {
        guard !isStrategyStreaming else { return }
        isStrategyStreaming = true
        isStrategyThinking = false
        strategyStreamText = ""
        errorMessage = nil

        streamTask = Task {
            do {
                let stream = apiService.streamStrategy(goalId: goalId)
                for try await event in stream {
                    switch event.event {
                    case "token":
                        isStrategyThinking = false
                        strategyStreamText += event.data
                    case "thinking":
                        isStrategyThinking = true
                    case "error":
                        errorMessage = event.data
                    default:
                        break
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            isStrategyThinking = false
            isStrategyStreaming = false
            // Refresh goal to pick up newly saved strategy from DB
            await refreshSelectedGoal()
        }
    }

    func acceptStrategy(goalId: Int, strategyId: Int) async {
        errorMessage = nil
        do {
            _ = try await apiService.acceptStrategy(goalId: goalId, strategyId: strategyId)
            await refreshSelectedGoal()
            await loadGoals()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    // MARK: - Intelligence: Tactical Plan

    func generatePlan(goalId: Int) {
        guard !isPlanStreaming else { return }
        isPlanStreaming = true
        isPlanThinking = false
        planStreamText = ""
        errorMessage = nil

        streamTask = Task {
            do {
                let stream = apiService.streamPlan(goalId: goalId)
                for try await event in stream {
                    switch event.event {
                    case "token":
                        isPlanThinking = false
                        planStreamText += event.data
                    case "thinking":
                        isPlanThinking = true
                    case "error":
                        errorMessage = event.data
                    default:
                        break
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            isPlanThinking = false
            isPlanStreaming = false
            await refreshSelectedGoal()
        }
    }

    func acceptPlan(goalId: Int, planJson: String) async {
        errorMessage = nil
        do {
            _ = try await apiService.acceptPlan(goalId: goalId, planJson: planJson)
            await refreshSelectedGoal()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    // MARK: - Intelligence: Plan Review

    func reviewPlans() {
        guard !isPlanReviewStreaming else { return }
        isPlanReviewStreaming = true
        planReviewStreamText = ""
        errorMessage = nil

        streamTask = Task {
            do {
                let stream = apiService.streamPlanReview()
                for try await event in stream {
                    if event.event == "token" {
                        planReviewStreamText += event.data
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            isPlanReviewStreaming = false
        }
    }

    // MARK: - Goal Chat

    func sendGoalChat(goalId: Int) {
        let query = goalChatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, !isGoalChatStreaming else { return }

        goalChatInput = ""
        goalChatMessages.append(ChatMessage(role: "user", content: query))
        goalChatMessages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
        isGoalChatStreaming = true

        streamTask = Task {
            do {
                let stream = apiService.streamGoalChat(goalId: goalId, query: query)
                for try await event in stream {
                    if event.event == "token" {
                        guard !goalChatMessages.isEmpty else { continue }
                        goalChatMessages[goalChatMessages.count - 1].content += event.data
                    } else if event.event == "agent_actions" {
                        handleAgentActions(event.data)
                    } else if event.event == "agent_confirm" {
                        handleAgentConfirmation(event.data)
                    } else if event.event == "agent_queued" {
                        handleAgentQueued(event.data)
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }

            if !goalChatMessages.isEmpty {
                goalChatMessages[goalChatMessages.count - 1].isStreaming = false
            }
            isGoalChatStreaming = false

            // Refresh goal + sidebar to pick up new tasks/objectives/progress
            await refreshSelectedGoal()
            await loadGoals()
        }
    }

    // MARK: - Agent Action Handling

    private func handleAgentActions(_ json: String) {
        for action in AgentActionHandler.parseActions(json) {
            switch action.type {
            case "task_created":
                if let title = action.title { appendSystemChatMessage("✓ Created task: \(title)") }
            case "objective_created":
                if let title = action.title {
                    appendSystemChatMessage("✓ Created objective: \(title)")
                }
            case "objective_enriched":
                appendSystemChatMessage("✓ Objective details saved")
                Task { await refreshSelectedGoal() }
            case "task_completed":
                if let title = action.title {
                    appendSystemChatMessage("✓ Completed task: \(title)")
                }
            case "goal_progress":
                if let note = action.note {
                    appendSystemChatMessage("✓ Progress logged: \(note)")
                }
            default:
                break
            }
        }
    }

    private func appendSystemChatMessage(_ text: String) {
        goalChatMessages.append(ChatMessage(role: "system", content: text))
    }

    private func handleAgentConfirmation(_ json: String) {
        guard let confirmation = AgentActionHandler.parseConfirmation(json) else { return }
        pendingConfirmation = confirmation
        goalChatMessages.append(ChatMessage(
            role: "system", content: "[AGENT_CONFIRM:\(confirmation.id)]"
        ))
    }

    private func handleAgentQueued(_ json: String) {
        guard let name = AgentActionHandler.parseQueuedAgentName(json) else { return }
        appendSystemChatMessage("⚡ \(name) is working in the background…")
    }

    /// Approve a pending agent confirmation in goal chat.
    func approveAgent(jobId: String) {
        pendingConfirmation = nil
        Task {
            do {
                try await apiService.confirmAgent(jobId: jobId)
                appendSystemChatMessage("Agent approved — working in background…")
            } catch {
                appendSystemChatMessage("Failed to approve agent: \(error.localizedDescription)")
            }
        }
    }

    /// Dismiss a pending agent confirmation in goal chat.
    func dismissAgent(jobId: String) {
        pendingConfirmation = nil
        Task {
            do {
                try await apiService.cancelAgent(jobId: jobId)
            } catch {
                // Silently fail — job may have already been cancelled
            }
        }
    }

    /// Request AI brainstorm for a goal via the orchestrator.
    func requestGoalBrainstorm(goalId: Int) async {
        do {
            let result = try await apiService.goalBrainstorm(goalId: goalId)
            if let _ = result["job_id"] as? String {
                appendSystemChatMessage(
                    "✨ AI brainstorm plan ready — check the agent activity panel to approve."
                )
            }
        } catch {
            errorMessage = "Failed to create brainstorm plan: \(error.localizedDescription)"
        }
    }

    // MARK: - Daily Review

    func checkReviewStatus() async {
        do {
            let status = try await apiService.getReviewStatus()
            isDailyReviewDue = status.due
        } catch {
            // Non-critical
        }
    }

    func startReview() {
        guard !isReviewStreaming else { return }
        isReviewStreaming = true
        reviewStreamText = ""
        errorMessage = nil

        streamTask = Task {
            do {
                let stream = apiService.streamReviewStart()
                for try await event in stream {
                    if event.event == "token" {
                        reviewStreamText += event.data
                    } else if event.event == "review_id", let id = Int(event.data) {
                        reviewId = id
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            isReviewStreaming = false
        }
    }

    func respondReview(response: String) async -> String? {
        guard let reviewId else { return nil }
        errorMessage = nil
        do {
            let result = try await apiService.respondReview(reviewId: reviewId, response: response)
            isDailyReviewDue = false
            await loadGoals()
            return result.summary
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    // MARK: - Cancellation

    func cancelStreaming() {
        streamTask?.cancel()
        streamTask = nil
        isInferring = false
        isStrategyStreaming = false
        isStrategyThinking = false
        isPlanStreaming = false
        isPlanThinking = false
        isPlanReviewStreaming = false
        isGoalChatStreaming = false
        isReviewStreaming = false
    }

    // MARK: - Computed

    var longTermGoals: [GoalItem] {
        goals.filter { $0.tier == "long_term" }
    }

    var midTermGoals: [GoalItem] {
        goals.filter { $0.tier == "mid_term" }
    }

    var shortTermGoals: [GoalItem] {
        goals.filter { $0.tier == "short_term" }
    }
}
