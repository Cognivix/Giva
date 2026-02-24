// GoalsViewModelTests.swift - Tests for GoalsViewModel.

import Foundation
import Testing
@testable import GivaApp

@Suite("GoalsViewModel")
@MainActor
struct GoalsViewModelTests {
    // MARK: - Helpers

    private func makeGoal(
        id: Int, title: String, tier: String, status: String = "active"
    ) -> GoalItem {
        GoalItem(
            id: id, title: title, tier: tier, description: "",
            category: "general", parentId: nil, status: status,
            priority: "medium", targetDate: nil, createdAt: nil,
            updatedAt: nil, progress: [], children: [], strategies: [], tasks: []
        )
    }

    // MARK: - Computed Filters

    @Test("longTermGoals filters correctly")
    func longTermFilter() {
        let mock = MockAPIService()
        let vm = GoalsViewModel(apiService: mock)
        vm.goals = [
            makeGoal(id: 1, title: "Goal A", tier: "long_term"),
            makeGoal(id: 2, title: "Goal B", tier: "mid_term"),
            makeGoal(id: 3, title: "Goal C", tier: "long_term"),
        ]

        #expect(vm.longTermGoals.count == 2)
        #expect(vm.longTermGoals.map(\.id) == [1, 3])
    }

    @Test("midTermGoals filters correctly")
    func midTermFilter() {
        let mock = MockAPIService()
        let vm = GoalsViewModel(apiService: mock)
        vm.goals = [
            makeGoal(id: 1, title: "Goal A", tier: "long_term"),
            makeGoal(id: 2, title: "Goal B", tier: "mid_term"),
            makeGoal(id: 3, title: "Goal C", tier: "short_term"),
        ]

        #expect(vm.midTermGoals.count == 1)
        #expect(vm.midTermGoals[0].id == 2)
    }

    @Test("shortTermGoals filters correctly")
    func shortTermFilter() {
        let mock = MockAPIService()
        let vm = GoalsViewModel(apiService: mock)
        vm.goals = [
            makeGoal(id: 1, title: "Goal A", tier: "short_term"),
            makeGoal(id: 2, title: "Goal B", tier: "short_term"),
        ]

        #expect(vm.shortTermGoals.count == 2)
    }

    @Test("Empty goals list returns empty filters")
    func emptyGoals() {
        let mock = MockAPIService()
        let vm = GoalsViewModel(apiService: mock)

        #expect(vm.longTermGoals.isEmpty)
        #expect(vm.midTermGoals.isEmpty)
        #expect(vm.shortTermGoals.isEmpty)
    }

    // MARK: - loadDetail

    @Test("loadDetail(nil) clears state")
    func loadDetailNilClears() async {
        let mock = MockAPIService()
        let vm = GoalsViewModel(apiService: mock)
        vm.goalDetail = makeGoal(id: 1, title: "Old", tier: "long_term")
        vm.goalChatInput = "some text"
        vm.goalChatMessages = [ChatMessage(role: "user", content: "hi")]
        vm.isLoadingDetail = true

        await vm.loadDetail(for: nil)

        #expect(vm.goalDetail == nil)
        #expect(vm.goalChatInput.isEmpty)
        #expect(vm.goalChatMessages.isEmpty)
        #expect(vm.isLoadingDetail == false)
    }

    // MARK: - loadGoals

    @Test("loadGoals calls API and populates goals")
    func loadGoalsSuccess() async {
        let mock = MockAPIService()
        mock.getGoalsResult = .success(GoalListResponse(
            goals: [
                makeGoal(id: 1, title: "Learn Swift", tier: "long_term"),
                makeGoal(id: 2, title: "Exercise", tier: "short_term"),
            ],
            count: 2
        ))
        let vm = GoalsViewModel(apiService: mock)

        await vm.loadGoals()

        #expect(vm.goals.count == 2)
        #expect(vm.isLoading == false)
        #expect(vm.errorMessage == nil)
        #expect(mock.getGoalsCallCount == 1)
    }

    @Test("loadGoals sets error on failure")
    func loadGoalsFailure() async {
        let mock = MockAPIService()
        mock.getGoalsResult = .failure(NSError(domain: "Test", code: 1,
                                                userInfo: [NSLocalizedDescriptionKey: "API error"]))
        let vm = GoalsViewModel(apiService: mock)

        await vm.loadGoals()

        #expect(vm.goals.isEmpty)
        #expect(vm.isLoading == false)
        #expect(vm.errorMessage != nil)
    }

    // MARK: - Streaming state

    @Test("Initial streaming state is all false")
    func initialStreamingState() {
        let mock = MockAPIService()
        let vm = GoalsViewModel(apiService: mock)

        #expect(vm.isGoalChatStreaming == false)
        #expect(vm.isStrategyStreaming == false)
        #expect(vm.isPlanStreaming == false)
        #expect(vm.isInferring == false)
        #expect(vm.isReviewStreaming == false)
    }

    // MARK: - navigateTo

    @Test("navigateTo sets pendingSelection")
    func navigateTo() {
        let mock = MockAPIService()
        let vm = GoalsViewModel(apiService: mock)
        vm.navigateTo(goalId: 42)
        #expect(vm.pendingSelection == 42)
    }
}
