// GoalsViewModel.swift - State management for the Goals window.

import SwiftUI

@MainActor
class GoalsViewModel: ObservableObject {
    let apiService: APIService

    // Goal list
    @Published var goals: [GoalItem] = []
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?

    // Selection
    @Published var selectedGoalId: Int?
    @Published var selectedGoal: GoalItem?

    // Daily review
    @Published var isDailyReviewDue: Bool = false
    @Published var isReviewStreaming: Bool = false
    @Published var reviewStreamText: String = ""
    @Published var reviewId: Int?

    // Intelligence streaming
    @Published var isInferring: Bool = false
    @Published var inferStreamText: String = ""
    @Published var isStrategyStreaming: Bool = false
    @Published var strategyStreamText: String = ""
    @Published var isPlanStreaming: Bool = false
    @Published var planStreamText: String = ""
    @Published var isPlanReviewStreaming: Bool = false
    @Published var planReviewStreamText: String = ""

    // Goal chat
    @Published var goalChatMessages: [ChatMessage] = []
    @Published var isGoalChatStreaming: Bool = false
    @Published var goalChatInput: String = ""

    // Create/Edit sheet
    @Published var showCreateSheet: Bool = false
    @Published var showEditSheet: Bool = false

    // Active streaming task (for cancellation)
    private var streamTask: Task<Void, Never>?

    init(apiService: APIService) {
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

    /// Fetch and display a goal's full detail (children, strategies, tasks, progress).
    /// Triggered by `.onChange(of: selectedGoalId)` in the view.
    func selectGoal(id: Int) async {
        do {
            selectedGoal = try await apiService.getGoal(id: id)
            goalChatInput = ""

            // Load persisted chat history for this goal
            let history = try await apiService.getGoalMessages(goalId: id)
            goalChatMessages = history.messages.map { msg in
                ChatMessage(role: msg.role, content: msg.content)
            }
        } catch {
            errorMessage = error.localizedDescription
            selectedGoal = nil
            goalChatMessages = []
        }
    }

    func refreshSelectedGoal() async {
        guard let id = selectedGoalId else { return }
        do {
            selectedGoal = try await apiService.getGoal(id: id)
        } catch {
            // Non-critical
        }
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
            selectedGoalId = newGoal.id  // triggers .onChange → selectGoal(id:)
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
            if selectedGoalId == id {
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
        strategyStreamText = ""
        errorMessage = nil

        streamTask = Task {
            do {
                let stream = apiService.streamStrategy(goalId: goalId)
                for try await event in stream {
                    if event.event == "token" {
                        strategyStreamText += event.data
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            isStrategyStreaming = false
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
        planStreamText = ""
        errorMessage = nil

        streamTask = Task {
            do {
                let stream = apiService.streamPlan(goalId: goalId)
                for try await event in stream {
                    if event.event == "token" {
                        planStreamText += event.data
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            isPlanStreaming = false
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

    func sendGoalChat() {
        let query = goalChatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, !isGoalChatStreaming, let goalId = selectedGoalId else { return }

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
        guard let data = json.data(using: .utf8),
              let actions = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return }

        for action in actions {
            guard let type = action["type"] as? String else { continue }
            switch type {
            case "task_created":
                if let title = action["title"] as? String {
                    appendSystemChatMessage("✓ Created task: \(title)")
                }
            case "objective_created":
                if let title = action["title"] as? String {
                    appendSystemChatMessage("✓ Created objective: \(title)")
                }
            case "task_completed":
                if let title = action["title"] as? String {
                    appendSystemChatMessage("✓ Completed task: \(title)")
                }
            case "goal_progress":
                if let note = action["note"] as? String {
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
        isPlanStreaming = false
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
