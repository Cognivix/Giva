// GivaMainWindowView.swift - Full-app window with sidebar navigation.
//
// Three-column NavigationSplitView:
//   Sidebar: Chat, Goals (by tier), Tasks (individual items)
//   Content: Detail view for the selected sidebar item
//   Inspector: Agent activity panel (when agents are active)

import SwiftUI

/// Sidebar navigation items for the main window.
enum SidebarItem: Hashable {
    case chat                   // current/new conversation
    case chatHistory(String)    // past chat by date (YYYY-MM-DD)
    case goal(Int)
    case task(Int)              // individual task → detail view
}

/// System actions that require confirmation in the full window.
private enum SystemAction: Equatable {
    case restart
    case upgrade
    case reset
    case quit
}

struct GivaMainWindowView: View {
    @Environment(GivaViewModel.self) private var viewModel
    @Environment(\.openWindow) private var openWindow

    @State private var sidebarSelection: SidebarItem? = .chat
    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    @State private var pendingSystemAction: SystemAction?

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            sidebarContent
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 300)
        } content: {
            VStack(spacing: 0) {
                detailContent
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                if let goalsVM = viewModel.goalsViewModel, goalsVM.isDailyReviewDue {
                    Divider()
                    DailyReviewBanner(viewModel: goalsVM)
                }
            }
        } detail: {
            Group {
                if !viewModel.activeJobs.isEmpty {
                    AgentActivityPanel()
                        .environment(viewModel)
                        .transition(.move(edge: .trailing).combined(with: .opacity))
                }
            }
            .animation(.easeInOut(duration: 0.25), value: viewModel.activeJobs.isEmpty)
        }
        .navigationTitle("Giva")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                toolbarButtons
            }

            // Connection status indicator
            ToolbarItem(placement: .status) {
                connectionIndicator
            }
        }
        .sheet(isPresented: goalCreateSheetBinding) {
            if let goalsVM = viewModel.goalsViewModel {
                GoalCreateSheet(viewModel: goalsVM)
            }
        }
        .sheet(isPresented: goalEditSheetBinding) {
            if let goalsVM = viewModel.goalsViewModel,
               let goal = goalsVM.goalDetail {
                GoalEditSheet(goal: goal, viewModel: goalsVM)
            }
        }
        .overlay {
            if let goalsVM = viewModel.goalsViewModel, goalsVM.isInferring {
                InferOverlay(viewModel: goalsVM)
            }
        }
        .onAppear {
            viewModel.isMainWindowOpen = true
            NSApp.setActivationPolicy(.regular)
        }
        .onDisappear {
            viewModel.isMainWindowOpen = false
            NSApp.setActivationPolicy(.accessory)
        }
        .task {
            if let goalsVM = viewModel.goalsViewModel {
                await goalsVM.loadGoals()
                await goalsVM.checkReviewStatus()
            }
            await viewModel.loadTasks()
        }
        // Confirmation dialogs for system actions
        .confirmationDialog(
            systemActionTitle,
            isPresented: showSystemDialog,
            titleVisibility: .visible
        ) {
            if let action = pendingSystemAction {
                if action == .quit {
                    Button("Quit") {
                        Task { await viewModel.quitApp(stopServer: false) }
                    }
                    Button("Quit & Stop Server") {
                        Task { await viewModel.quitApp(stopServer: true) }
                    }
                    Button("Cancel", role: .cancel) { }
                } else {
                    Button(systemActionConfirmLabel(action),
                           role: action == .reset ? .destructive : nil) {
                        Task { await performSystemAction(action) }
                    }
                    Button("Cancel", role: .cancel) { }
                }
            }
        } message: {
            if let action = pendingSystemAction {
                Text(systemActionMessage(action))
            }
        }
        // Handle goal pending selection from GoalsViewModel
        .onChange(of: viewModel.goalsViewModel?.pendingSelection) { _, newId in
            if let id = newId {
                sidebarSelection = .goal(id)
                viewModel.goalsViewModel?.pendingSelection = nil
            }
        }
        // Handle task selection from popover
        .onChange(of: viewModel.pendingTaskChatId) { _, newId in
            if let id = newId {
                sidebarSelection = .task(id)
                viewModel.pendingTaskChatId = nil
            }
        }
    }

    // MARK: - Connection Indicator

    private var connectionIndicator: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(connectionDotColor)
                .frame(width: 7, height: 7)
            Text(viewModel.serverManager.connectionState.rawValue)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
    }

    private var connectionDotColor: Color {
        switch viewModel.serverManager.connectionState {
        case .connected: return .green
        case .connecting: return .yellow
        case .offline: return .red
        }
    }

    // MARK: - Bindings

    private var goalCreateSheetBinding: Binding<Bool> {
        Binding(
            get: { viewModel.goalsViewModel?.showCreateSheet ?? false },
            set: { viewModel.goalsViewModel?.showCreateSheet = $0 }
        )
    }

    private var goalEditSheetBinding: Binding<Bool> {
        Binding(
            get: { viewModel.goalsViewModel?.showEditSheet ?? false },
            set: { viewModel.goalsViewModel?.showEditSheet = $0 }
        )
    }

    // MARK: - Sidebar

    private var sidebarContent: some View {
        List(selection: $sidebarSelection) {
            // Chat section
            Section("Conversations") {
                Label("New Chat", systemImage: "plus.bubble")
                    .tag(SidebarItem.chat)

                // Past conversation dates
                ForEach(viewModel.conversationDates) { entry in
                    HStack {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(entry.displayLabel)
                                .font(.caption)
                                .fontWeight(.medium)
                            if let preview = entry.preview {
                                Text(preview)
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        Spacer()
                        Text("\(entry.messageCount)")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                    .tag(SidebarItem.chatHistory(entry.date))
                }
            }

            // Goals section
            if let goalsVM = viewModel.goalsViewModel {
                goalsSection(goalsVM)
            }

            // Tasks section — individual tasks in sidebar
            Section("Tasks") {
                if viewModel.tasks.isEmpty && !viewModel.isLoadingTasks {
                    Text("No pending tasks")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.leading, 4)
                } else {
                    ForEach(viewModel.tasks) { task in
                        taskSidebarRow(task)
                            .tag(SidebarItem.task(task.id))
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .toolbar {
            ToolbarItem(placement: .automatic) {
                if let goalsVM = viewModel.goalsViewModel {
                    Button {
                        Task { await goalsVM.loadGoals() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .help("Refresh goals")
                }
            }
        }
    }

    private func taskSidebarRow(_ task: TaskItem) -> some View {
        HStack(spacing: 6) {
            Circle()
                .fill(taskPriorityColor(task.priority))
                .frame(width: 8, height: 8)

            VStack(alignment: .leading, spacing: 1) {
                Text(task.title)
                    .font(.caption)
                    .lineLimit(2)

                HStack(spacing: 4) {
                    if let dueDate = task.formattedDueDate {
                        Text(dueDate)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                    Text(task.sourceType.capitalized)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }

            Spacer()
        }
        .contextMenu {
            Button {
                Task { await viewModel.updateTaskStatus(taskId: task.id, status: "done") }
            } label: {
                Label("Mark as Done", systemImage: "checkmark.circle")
            }

            Button {
                Task { await viewModel.updateTaskStatus(taskId: task.id, status: "dismissed") }
            } label: {
                Label("Dismiss", systemImage: "xmark.circle")
            }
        }
    }

    private func taskPriorityColor(_ priority: String) -> Color {
        switch priority {
        case "high": return .red
        case "medium": return .orange
        case "low": return .gray
        default: return .primary
        }
    }

    @ViewBuilder
    private func goalsSection(_ goalsVM: GoalsViewModel) -> some View {
        if !goalsVM.longTermGoals.isEmpty {
            Section("Long-term Goals") {
                ForEach(goalsVM.longTermGoals) { goal in
                    goalRow(goal, viewModel: goalsVM)
                        .tag(SidebarItem.goal(goal.id))
                }
            }
        }

        if !goalsVM.midTermGoals.isEmpty {
            Section("Mid-term Goals") {
                ForEach(goalsVM.midTermGoals) { goal in
                    goalRow(goal, viewModel: goalsVM)
                        .tag(SidebarItem.goal(goal.id))
                }
            }
        }

        if !goalsVM.shortTermGoals.isEmpty {
            Section("Short-term Goals") {
                ForEach(goalsVM.shortTermGoals) { goal in
                    goalRow(goal, viewModel: goalsVM)
                        .tag(SidebarItem.goal(goal.id))
                }
            }
        }

        if goalsVM.goals.isEmpty && !goalsVM.isLoading {
            Section("Goals") {
                Text("No goals yet")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .padding(.leading, 4)
            }
        }
    }

    private func goalRow(_ goal: GoalItem, viewModel goalsVM: GoalsViewModel) -> some View {
        HStack(spacing: 6) {
            Circle()
                .fill(goalPriorityColor(goal.priority))
                .frame(width: 8, height: 8)

            Text(goal.title)
                .lineLimit(1)

            Spacer()

            if !goal.children.isEmpty {
                Text("\(goal.children.count)")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .background(Capsule().fill(Color.secondary.opacity(0.15)))
            }
        }
        .contextMenu {
            Button("Complete") { Task { await goalsVM.updateGoalStatus(id: goal.id, status: "completed") } }
            Button("Pause") { Task { await goalsVM.updateGoalStatus(id: goal.id, status: "paused") } }
            Button("Abandon") { Task { await goalsVM.updateGoalStatus(id: goal.id, status: "abandoned") } }
        }
    }

    private func goalPriorityColor(_ priority: String) -> Color {
        switch priority {
        case "high": return .red
        case "medium": return .orange
        case "low": return .gray
        default: return .primary
        }
    }

    // MARK: - Detail Content

    @ViewBuilder
    private var detailContent: some View {
        switch sidebarSelection {
        case .chat:
            chatContent
        case .chatHistory(let dateString):
            chatHistoryContent(date: dateString)
        case .goal(let goalId):
            goalContent(goalId: goalId)
        case .task(let taskId):
            TaskDetailView(taskId: taskId)
                .environment(viewModel)
                .id(taskId)
        case nil:
            ContentUnavailableView(
                "Select an Item",
                systemImage: "sidebar.left",
                description: Text("Choose from the sidebar to get started.")
            )
        }
    }

    @ViewBuilder
    private var chatContent: some View {
        VStack(spacing: 0) {
            if showPhaseBanner {
                phaseBanner
                Divider()
            }

            ChatView()
                .environment(viewModel)
        }
    }

    @ViewBuilder
    private func chatHistoryContent(date: String) -> some View {
        ChatHistoryView(dateString: date)
            .environment(viewModel)
            .id(date)
    }

    @ViewBuilder
    private func goalContent(goalId: Int) -> some View {
        if let goalsVM = viewModel.goalsViewModel {
            GoalDetailView(
                goal: goalsVM.goalDetail ?? goalsVM.goals.first { $0.id == goalId }
                      ?? GoalItem.placeholder,
                goalId: goalId,
                viewModel: goalsVM
            )
            .id(goalId)
            .task(id: goalId) {
                await goalsVM.loadDetail(for: goalId)
            }
        } else {
            ContentUnavailableView(
                "Server Not Ready",
                systemImage: "exclamationmark.circle",
                description: Text("Goals require an active server connection.")
            )
        }
    }

    // MARK: - Phase Banner

    private var showPhaseBanner: Bool {
        viewModel.serverPhase == .syncing
            || viewModel.isSyncingManual
            || viewModel.isLoadingModel
    }

    private var phaseBanner: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)

            if viewModel.isLoadingModel {
                Text("Loading AI model...")
                    .font(.callout)
                    .foregroundColor(.secondary)
            } else if let progress = viewModel.syncProgress {
                Text(progress.displayText)
                    .font(.callout)
                    .foregroundColor(.secondary)
            } else {
                Text("Working...")
                    .font(.callout)
                    .foregroundColor(.secondary)
            }

            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color.secondary.opacity(0.05))
    }

    // MARK: - Toolbar

    @ViewBuilder
    private var toolbarButtons: some View {
        // New Chat
        Button {
            sidebarSelection = .chat
            viewModel.newChat()
        } label: {
            Label("New Chat", systemImage: "plus.bubble")
        }
        .keyboardShortcut("n", modifiers: .command)

        // Sync button
        Button {
            Task { await viewModel.triggerSync() }
        } label: {
            Label("Sync", systemImage: "arrow.triangle.2.circlepath")
        }
        .disabled(!viewModel.areActionsEnabled || viewModel.isSyncing)

        // New Goal button
        if let goalsVM = viewModel.goalsViewModel {
            Button {
                goalsVM.showCreateSheet = true
            } label: {
                Label("New Goal", systemImage: "plus")
            }
            .disabled(!viewModel.areActionsEnabled)

            Button {
                goalsVM.inferGoals()
            } label: {
                Label("Infer Goals", systemImage: "sparkles")
            }
            .disabled(goalsVM.isInferring || !viewModel.areActionsEnabled)
            .help("Use AI to suggest goals from your data")
        }

        // Daily Review
        if viewModel.isDailyReviewDue {
            Button {
                // Navigate to goals and show review
                if let first = viewModel.goalsViewModel?.longTermGoals.first {
                    sidebarSelection = .goal(first.id)
                }
            } label: {
                Label("Review", systemImage: "text.badge.checkmark")
            }
        }

        // Minimize to popover
        Button {
            NSApp.keyWindow?.close()
        } label: {
            Label("Minimize to Menu Bar", systemImage: "arrow.down.forward.and.arrow.up.backward")
        }
        .help("Switch to menu bar popover")

        // System gear menu
        Menu {
            Button {
                openSettingsWindow()
            } label: {
                Label("Settings...", systemImage: "slider.horizontal.3")
            }
            .keyboardShortcut(",", modifiers: .command)

            Button {
                viewModel.selectedSettingsTab = .profile
                openSettingsWindow()
            } label: {
                Label("Profile...", systemImage: "person.circle")
            }

            Button {
                viewModel.openCLI()
            } label: {
                Label("Open CLI", systemImage: "terminal")
            }

            Divider()

            Button {
                pendingSystemAction = .restart
            } label: {
                Label("Restart Server...", systemImage: "arrow.clockwise")
            }
            .disabled(viewModel.isSystemBusy)

            Button {
                pendingSystemAction = .upgrade
            } label: {
                Label("Upgrade Code...", systemImage: "arrow.up.circle")
            }
            .disabled(viewModel.isSystemBusy)

            Divider()

            Button(role: .destructive) {
                pendingSystemAction = .reset
            } label: {
                Label("Reset All Data...", systemImage: "trash")
            }
            .disabled(viewModel.isSystemBusy || viewModel.isStreaming)

            Divider()

            Button {
                pendingSystemAction = .quit
            } label: {
                Label("Quit Giva...", systemImage: "power")
            }
        } label: {
            Label("Settings", systemImage: "gearshape")
        }
    }
    // MARK: - System Action Confirmation

    private var showSystemDialog: Binding<Bool> {
        Binding(
            get: { pendingSystemAction != nil },
            set: { if !$0 { pendingSystemAction = nil } }
        )
    }

    private var systemActionTitle: String {
        switch pendingSystemAction {
        case .restart: return "Restart Server"
        case .upgrade: return "Upgrade Code"
        case .reset: return "Reset All Data"
        case .quit: return "Quit Giva"
        case nil: return ""
        }
    }

    private func systemActionConfirmLabel(_ action: SystemAction) -> String {
        switch action {
        case .restart: return "Restart"
        case .upgrade: return "Upgrade"
        case .reset: return "Erase Everything"
        case .quit: return "Quit"
        }
    }

    private func systemActionMessage(_ action: SystemAction) -> String {
        switch action {
        case .restart:
            return "Active requests will be interrupted. No data is lost."
        case .upgrade:
            return "Re-installs from source and restarts the server. Data is preserved."
        case .reset:
            return "Deletes emails, events, tasks, goals, profile, and settings. Models are kept."
        case .quit:
            return "The background server can keep running for CLI access, or stop with the app."
        }
    }

    private func performSystemAction(_ action: SystemAction) async {
        switch action {
        case .restart: await viewModel.triggerRestart()
        case .upgrade: await viewModel.triggerUpgrade()
        case .reset: await viewModel.triggerReset()
        case .quit: break  // handled by dedicated quit confirmation buttons
        }
    }

    private func openSettingsWindow() {
        openWindow(id: "settings-window")
    }
}


#Preview {
    Text(String(describing: SidebarItem.chat))
}
