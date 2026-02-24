// GivaViewModelTests.swift - Tests for GivaViewModel computed properties and core methods.

import Foundation
import Testing
@testable import GivaApp

@Suite("GivaViewModel")
@MainActor
struct GivaViewModelTests {
    // MARK: - Computed Properties

    @Test("isChatEnabled is true for operational phase")
    func chatEnabledOperational() {
        let vm = GivaViewModel()
        vm.serverPhase = .operational
        #expect(vm.isChatEnabled == true)
    }

    @Test("isChatEnabled is true for onboarding phase")
    func chatEnabledOnboarding() {
        let vm = GivaViewModel()
        vm.serverPhase = .onboarding
        #expect(vm.isChatEnabled == true)
    }

    @Test("isChatEnabled is false for syncing phase")
    func chatDisabledSyncing() {
        let vm = GivaViewModel()
        vm.serverPhase = .syncing
        #expect(vm.isChatEnabled == false)
    }

    @Test("isChatEnabled is false when system is busy")
    func chatDisabledSystemBusy() {
        let vm = GivaViewModel()
        vm.serverPhase = .operational
        vm.isRestarting = true
        #expect(vm.isChatEnabled == false)
    }

    @Test("areActionsEnabled only for operational and not busy")
    func actionsEnabled() {
        let vm = GivaViewModel()

        vm.serverPhase = .operational
        #expect(vm.areActionsEnabled == true)

        vm.serverPhase = .onboarding
        #expect(vm.areActionsEnabled == false)

        vm.serverPhase = .operational
        vm.isResetting = true
        #expect(vm.areActionsEnabled == false)
    }

    @Test("isOnboarding derives from serverPhase")
    func isOnboarding() {
        let vm = GivaViewModel()
        vm.serverPhase = .onboarding
        #expect(vm.isOnboarding == true)

        vm.serverPhase = .operational
        #expect(vm.isOnboarding == false)
    }

    @Test("isOperational derives from serverPhase")
    func isOperational() {
        let vm = GivaViewModel()
        vm.serverPhase = .operational
        #expect(vm.isOperational == true)

        vm.serverPhase = .syncing
        #expect(vm.isOperational == false)
    }

    @Test("isSyncing includes manual sync")
    func isSyncingManual() {
        let vm = GivaViewModel()
        vm.serverPhase = .operational
        vm.isSyncingManual = true
        #expect(vm.isSyncing == true)
    }

    @Test("isSyncing from phase")
    func isSyncingPhase() {
        let vm = GivaViewModel()
        vm.serverPhase = .syncing
        #expect(vm.isSyncing == true)
    }

    @Test("isSystemBusy reflects transient action flags")
    func systemBusy() {
        let vm = GivaViewModel()

        #expect(vm.isSystemBusy == false)

        vm.isRestarting = true
        #expect(vm.isSystemBusy == true)
        vm.isRestarting = false

        vm.isResetting = true
        #expect(vm.isSystemBusy == true)
        vm.isResetting = false

        vm.isUpgrading = true
        #expect(vm.isSystemBusy == true)
    }

    // MARK: - sendMessage

    @Test("sendMessage guards on empty input")
    func sendMessageEmptyInput() {
        let vm = GivaViewModel()
        let mock = MockAPIService()
        vm.apiService = mock
        vm.serverPhase = .operational

        vm.currentInput = ""
        vm.sendMessage()
        #expect(vm.messages.isEmpty)
    }

    @Test("sendMessage guards on whitespace-only input")
    func sendMessageWhitespaceInput() {
        let vm = GivaViewModel()
        let mock = MockAPIService()
        vm.apiService = mock
        vm.serverPhase = .operational

        vm.currentInput = "   \n  "
        vm.sendMessage()
        #expect(vm.messages.isEmpty)
    }

    @Test("sendMessage guards while streaming")
    func sendMessageWhileStreaming() {
        let vm = GivaViewModel()
        let mock = MockAPIService()
        vm.apiService = mock
        vm.serverPhase = .operational

        vm.currentInput = "Hello"
        vm.isStreaming = true
        vm.sendMessage()
        // Input should not be consumed
        #expect(vm.currentInput == "Hello")
    }

    @Test("sendMessage clears input and adds messages")
    func sendMessageAddsMessages() {
        let vm = GivaViewModel()
        let mock = MockAPIService()
        vm.apiService = mock
        vm.serverPhase = .operational

        vm.currentInput = "Hello Giva"
        vm.sendMessage()

        #expect(vm.currentInput.isEmpty)
        // Should have user message + streaming assistant message
        #expect(vm.messages.count == 2)
        #expect(vm.messages[0].role == "user")
        #expect(vm.messages[0].content == "Hello Giva")
        #expect(vm.messages[1].role == "assistant")
        #expect(vm.messages[1].isStreaming == true)
    }

    // MARK: - cancelStreaming

    @Test("cancelStreaming stops streaming and finalizes last message")
    func cancelStreaming() {
        let vm = GivaViewModel()
        vm.isStreaming = true
        vm.messages.append(ChatMessage(role: "assistant", content: "partial", isStreaming: true))

        vm.cancelStreaming()

        #expect(vm.isStreaming == false)
        #expect(vm.messages.last?.isStreaming == false)
    }

    // MARK: - loadTasks

    @Test("loadTasks populates tasks from API")
    func loadTasksSuccess() async {
        let vm = GivaViewModel()
        let mock = MockAPIService()
        mock.getTasksResult = .success(TaskListResponse(
            tasks: [
                TaskItem(id: 1, title: "Task 1", description: "Desc",
                         sourceType: "email", sourceId: 10,
                         priority: "high", dueDate: nil, status: "pending",
                         createdAt: nil),
                TaskItem(id: 2, title: "Task 2", description: "Desc 2",
                         sourceType: "event", sourceId: 20,
                         priority: "low", dueDate: nil, status: "pending",
                         createdAt: nil)
            ],
            count: 2
        ))
        vm.apiService = mock

        await vm.loadTasks()

        #expect(vm.tasks.count == 2)
        #expect(vm.tasks[0].title == "Task 1")
        #expect(vm.tasks[1].title == "Task 2")
        #expect(vm.isLoadingTasks == false)
        #expect(mock.getTasksCallCount == 1)
    }

    @Test("loadTasks sets error on failure")
    func loadTasksFailure() async {
        let vm = GivaViewModel()
        let mock = MockAPIService()
        mock.getTasksResult = .failure(NSError(domain: "Test", code: 42,
                                                userInfo: [NSLocalizedDescriptionKey: "Network error"]))
        vm.apiService = mock

        await vm.loadTasks()

        #expect(vm.tasks.isEmpty)
        #expect(vm.errorMessage != nil)
        #expect(vm.isLoadingTasks == false)
    }

    @Test("loadTasks guards when no apiService")
    func loadTasksNoApi() async {
        let vm = GivaViewModel()
        vm.apiService = nil

        await vm.loadTasks()

        #expect(vm.tasks.isEmpty)
        #expect(vm.isLoadingTasks == false)
    }

    // MARK: - Phase behavior

    @Test("All phases produce correct isChatEnabled")
    func allPhasesChatEnabled() {
        let vm = GivaViewModel()

        let enabledPhases: [ServerPhase] = [.operational, .onboarding]
        let disabledPhases: [ServerPhase] = [.unknown, .ready, .syncing, .validating,
                                              .downloadingDefaultModel, .awaitingModelSelection,
                                              .downloadingUserModels]

        for phase in enabledPhases {
            vm.serverPhase = phase
            #expect(vm.isChatEnabled == true, "Expected chat enabled for \(phase)")
        }

        for phase in disabledPhases {
            vm.serverPhase = phase
            #expect(vm.isChatEnabled == false, "Expected chat disabled for \(phase)")
        }
    }
}
