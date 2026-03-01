// GoalsWindowView.swift - Goals UI components shared between main window and sheets.
//
// Components: GoalDetailView, GoalCreateSheet, GoalEditSheet, DailyReviewBanner,
// DailyReviewSheet, InferOverlay, AddProgressButton.
//
// The main goals window has been replaced by the sidebar in GivaMainWindowView.

import SwiftUI

// MARK: - Goal Detail

struct GoalDetailView: View {
    let goal: GoalItem
    let goalId: Int
    @Bindable var viewModel: GoalsViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // Header
                goalHeader

                Divider()

                // Description
                if !goal.description.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Description")
                            .font(.headline)
                        Text(goal.description)
                            .foregroundColor(.secondary)
                    }
                }

                // Strategy section (for long-term goals)
                if goal.tier == "long_term" {
                    strategySection
                }

                // Tactical plan section (for mid-term goals)
                if goal.tier == "mid_term" {
                    tacticalPlanSection
                }

                // Linked tasks
                if !goal.tasks.isEmpty {
                    tasksSection
                }

                // Progress history
                progressSection

                Divider()

                // Goal chat
                goalChatSection
            }
            .padding()
        }
    }

    // MARK: - Header

    private var goalHeader: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(goal.title)
                    .font(.title2.bold())

                Spacer()

                Button {
                    Task { await viewModel.requestGoalBrainstorm(goalId: goal.id) }
                } label: {
                    Label("AI Brainstorm", systemImage: "sparkles")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .tint(.purple)
                .help("Use AI to brainstorm next steps for this goal")

                Button("Edit") {
                    viewModel.showEditSheet = true
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Menu {
                    Button("Complete") { Task { await viewModel.updateGoalStatus(id: goal.id, status: "completed") } }
                    Button("Pause") { Task { await viewModel.updateGoalStatus(id: goal.id, status: "paused") } }
                    Divider()
                    Button("Abandon", role: .destructive) { Task { await viewModel.updateGoalStatus(id: goal.id, status: "abandoned") } }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
            }

            HStack(spacing: 12) {
                Label(goal.tierLabel, systemImage: tierIcon(goal.tier))
                    .font(.caption)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Color.accentColor.opacity(0.1)))

                if !goal.category.isEmpty {
                    Label(goal.category.capitalized, systemImage: "tag")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Label(goal.priority.capitalized, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundColor(priorityTextColor(goal.priority))

                if let target = goal.formattedTargetDate {
                    Label(target, systemImage: "calendar")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Label(goal.status.capitalized, systemImage: "circle.fill")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            // Children
            if !goal.children.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Sub-objectives (\(goal.children.count))")
                        .font(.caption.bold())
                        .foregroundColor(.secondary)
                    ForEach(goal.children) { child in
                        HStack(spacing: 6) {
                            Circle()
                                .fill(priorityTextColor(child.priority))
                                .frame(width: 6, height: 6)
                            Text(child.title)
                                .font(.caption)
                            Spacer()
                            Text(child.status)
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            viewModel.navigateTo(goalId: child.id)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Strategy

    private var strategySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Strategy")
                    .font(.headline)
                Spacer()
                if !goal.strategies.isEmpty || viewModel.isStrategyStreaming {
                    Button {
                        viewModel.generateStrategy(goalId: goal.id)
                    } label: {
                        Label(viewModel.isStrategyStreaming ? "Generating..." : "Regenerate",
                              systemImage: "lightbulb")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(viewModel.isStrategyStreaming)
                }
            }

            if viewModel.isStrategyStreaming {
                if viewModel.isStrategyThinking && viewModel.strategyStreamText.isEmpty {
                    HStack(spacing: 6) {
                        ProgressView()
                            .controlSize(.small)
                        Text("Thinking…")
                            .font(.callout)
                            .foregroundColor(.secondary)
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.05)))
                } else {
                    Text(viewModel.strategyStreamText)
                        .font(.body)
                        .foregroundColor(.secondary)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.05)))
                }
            }

            // Empty state CTA when no strategies exist
            if goal.strategies.isEmpty && !viewModel.isStrategyStreaming {
                VStack(spacing: 10) {
                    Image(systemName: "lightbulb.max")
                        .font(.largeTitle)
                        .foregroundColor(.accentColor.opacity(0.6))

                    Text("No strategy yet")
                        .font(.callout.bold())

                    Text("Let Giva brainstorm an approach for this goal and suggest mid-term objectives to work toward.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 300)

                    Button {
                        viewModel.generateStrategy(goalId: goal.id)
                    } label: {
                        Label("Brainstorm Strategy", systemImage: "sparkles")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.regular)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(
                    RoundedRectangle(cornerRadius: 10)
                        .fill(Color.accentColor.opacity(0.04))
                        .strokeBorder(Color.accentColor.opacity(0.15), lineWidth: 1)
                )
            }

            ForEach(goal.strategies) { strategy in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(strategy.status.capitalized)
                            .font(.caption.bold())
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(
                                Capsule().fill(strategy.status == "accepted"
                                    ? Color.green.opacity(0.15)
                                    : Color.secondary.opacity(0.1))
                            )

                        if let date = strategy.createdAt {
                            Text(String(date.prefix(10)))
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }

                        Spacer()

                        if strategy.status == "proposed" {
                            Button("Accept") {
                                Task { await viewModel.acceptStrategy(goalId: goal.id, strategyId: strategy.id) }
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.mini)
                        }
                    }

                    Text(strategy.strategyText)
                        .font(.callout)

                    if !strategy.actionItems.isEmpty {
                        ForEach(Array(strategy.actionItems.enumerated()), id: \.offset) { _, item in
                            HStack(alignment: .top, spacing: 6) {
                                Image(systemName: "arrow.right.circle")
                                    .font(.caption)
                                    .foregroundColor(.accentColor)
                                Text(item["description"] ?? "")
                                    .font(.caption)
                            }
                        }
                    }

                    // Show suggested objectives for proposed strategies
                    if strategy.status == "proposed" && !strategy.suggestedObjectives.isEmpty {
                        Divider()
                            .padding(.vertical, 2)

                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 4) {
                                Image(systemName: "flag")
                                    .font(.caption)
                                    .foregroundColor(.accentColor)
                                Text("Suggested Objectives (\(strategy.suggestedObjectives.count))")
                                    .font(.caption.bold())
                                    .foregroundColor(.accentColor)
                            }

                            ForEach(Array(strategy.suggestedObjectives.enumerated()), id: \.offset) { _, obj in
                                HStack(alignment: .top, spacing: 6) {
                                    Image(systemName: "plus.circle.fill")
                                        .font(.caption)
                                        .foregroundColor(.green.opacity(0.7))
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(obj.title)
                                            .font(.caption)
                                        if let desc = obj.description, !desc.isEmpty {
                                            Text(desc)
                                                .font(.caption2)
                                                .foregroundColor(.secondary)
                                                .lineLimit(2)
                                        }
                                    }
                                }
                            }

                            Text("Accepting will create \(strategy.suggestedObjectives.count) mid-term objective\(strategy.suggestedObjectives.count == 1 ? "" : "s")")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                                .italic()
                                .padding(.top, 2)
                        }
                    }
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.05)))
            }
        }
    }

    // MARK: - Tactical Plan

    private var tacticalPlanSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Tactical Plan")
                    .font(.headline)
                Spacer()
                Button {
                    viewModel.generatePlan(goalId: goal.id)
                } label: {
                    Label(viewModel.isPlanStreaming ? "Planning..." : "Generate Plan",
                          systemImage: "list.bullet.clipboard")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(viewModel.isPlanStreaming)
            }

            if viewModel.isPlanStreaming {
                if viewModel.isPlanThinking && viewModel.planStreamText.isEmpty {
                    HStack(spacing: 6) {
                        ProgressView()
                            .controlSize(.small)
                        Text("Thinking…")
                            .font(.callout)
                            .foregroundColor(.secondary)
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.05)))
                } else {
                    Text(viewModel.planStreamText)
                        .font(.body)
                        .foregroundColor(.secondary)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.05)))
                }
            }
        }
    }

    // MARK: - Tasks

    private var tasksSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Linked Tasks (\(goal.tasks.count))")
                .font(.headline)

            ForEach(goal.tasks) { task in
                HStack(spacing: 8) {
                    Image(systemName: task.status == "done" ? "checkmark.circle.fill" : "circle")
                        .foregroundColor(task.status == "done" ? .green : .secondary)
                        .font(.body)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.title)
                            .font(.callout)
                            .strikethrough(task.status == "done")
                        if let due = task.dueDate {
                            Text("Due: \(String(due.prefix(10)))")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }

                    Spacer()

                    Text(task.priority)
                        .font(.caption2)
                        .foregroundColor(priorityTextColor(task.priority))
                }
            }
        }
    }

    // MARK: - Progress

    private var progressSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Progress History")
                    .font(.headline)
                Spacer()
                AddProgressButton(goalId: goal.id, viewModel: viewModel)
            }

            if goal.progress.isEmpty {
                Text("No progress entries yet.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                ForEach(goal.progress) { entry in
                    HStack(alignment: .top, spacing: 8) {
                        VStack(spacing: 2) {
                            Circle()
                                .fill(sourceColor(entry.source))
                                .frame(width: 8, height: 8)
                            Rectangle()
                                .fill(Color.secondary.opacity(0.2))
                                .frame(width: 1)
                        }
                        .frame(width: 8)

                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(entry.formattedDate)
                                    .font(.caption.bold())
                                Text(entry.sourceBadge)
                                    .font(.caption2)
                                    .padding(.horizontal, 5)
                                    .padding(.vertical, 1)
                                    .background(Capsule().fill(sourceColor(entry.source).opacity(0.15)))
                            }
                            Text(entry.note)
                                .font(.callout)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Goal Chat

    private var goalChatSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Chat with Giva")
                .font(.headline)

            if !viewModel.goalChatMessages.isEmpty {
                ForEach(viewModel.goalChatMessages) { msg in
                    if msg.role == "system" {
                        // Check for agent confirmation cards
                        if msg.content.hasPrefix("[AGENT_CONFIRM:"),
                           let conf = viewModel.pendingConfirmation,
                           msg.content.contains(conf.id) {
                            AgentConfirmationCard(
                                confirmation: conf,
                                onApprove: { viewModel.approveAgent(jobId: conf.id) },
                                onDismiss: { viewModel.dismissAgent(jobId: conf.id) }
                            )
                        } else {
                            // Agent action notifications — compact inline style
                            Text(msg.content)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .padding(.leading, 28)
                        }
                    } else {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: msg.role == "user"
                                  ? "person.circle" : "brain.head.profile")
                                .foregroundColor(msg.role == "user"
                                                 ? .accentColor : .secondary)
                                .font(.body)

                            if msg.role == "user" {
                                Text(msg.content)
                                    .font(.callout)
                            } else {
                                MarkdownText(msg.content)
                                    .font(.callout)
                                    .textSelection(.enabled)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
            }

            HStack(spacing: 8) {
                TextField("Ask about this goal...", text: $viewModel.goalChatInput)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { viewModel.sendGoalChat(goalId: goalId) }
                    .disabled(viewModel.isGoalChatStreaming)

                Button {
                    viewModel.sendGoalChat(goalId: goalId)
                } label: {
                    Image(systemName: "paperplane.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(viewModel.goalChatInput.trimmingCharacters(in: .whitespaces).isEmpty
                          || viewModel.isGoalChatStreaming)
            }
        }
    }

    // MARK: - Helpers

    private func tierIcon(_ tier: String) -> String {
        switch tier {
        case "long_term": return "mountain.2"
        case "mid_term": return "flag"
        case "short_term": return "checkmark.circle"
        default: return "target"
        }
    }

    private func priorityTextColor(_ priority: String) -> Color {
        switch priority {
        case "high": return .red
        case "medium": return .orange
        case "low": return .gray
        default: return .primary
        }
    }

    private func sourceColor(_ source: String) -> Color {
        switch source {
        case "sync": return .blue
        case "review": return .purple
        case "chat": return .green
        case "user": return .orange
        default: return .secondary
        }
    }
}

// MARK: - Add Progress Button

struct AddProgressButton: View {
    let goalId: Int
    @Bindable var viewModel: GoalsViewModel
    @State private var showPopover = false
    @State private var noteText = ""

    var body: some View {
        Button {
            showPopover = true
        } label: {
            Label("Add Note", systemImage: "plus.circle")
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .popover(isPresented: $showPopover) {
            VStack(spacing: 10) {
                Text("Add Progress Note")
                    .font(.headline)
                TextField("What progress did you make?", text: $noteText, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(3...6)
                    .frame(width: 250)
                HStack {
                    Button("Cancel") { showPopover = false }
                        .buttonStyle(.plain)
                    Spacer()
                    Button("Save") {
                        let note = noteText.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !note.isEmpty else { return }
                        Task {
                            await viewModel.addProgress(goalId: goalId, note: note)
                            noteText = ""
                            showPopover = false
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(noteText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .padding()
        }
    }
}

// MARK: - Create Sheet

struct GoalCreateSheet: View {
    @Bindable var viewModel: GoalsViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var title = ""
    @State private var tier = "long_term"
    @State private var category = ""
    @State private var description = ""
    @State private var priority = "medium"
    @State private var targetDateString = ""
    @State private var parentId: Int?

    let tiers = ["long_term", "mid_term", "short_term"]
    let priorities = ["high", "medium", "low"]
    let categories = ["career", "personal", "health", "financial", "networking", "learning", ""]

    var body: some View {
        VStack(spacing: 0) {
            Text("New Goal")
                .font(.title3.bold())
                .padding()

            Form {
                TextField("Title", text: $title)
                Picker("Tier", selection: $tier) {
                    Text("Long-term").tag("long_term")
                    Text("Mid-term").tag("mid_term")
                    Text("Short-term").tag("short_term")
                }
                Picker("Priority", selection: $priority) {
                    Text("High").tag("high")
                    Text("Medium").tag("medium")
                    Text("Low").tag("low")
                }
                Picker("Category", selection: $category) {
                    Text("None").tag("")
                    ForEach(categories.filter { !$0.isEmpty }, id: \.self) { cat in
                        Text(cat.capitalized).tag(cat)
                    }
                }
                TextField("Description (optional)", text: $description, axis: .vertical)
                    .lineLimit(3...6)
                TextField("Target Date (YYYY-MM-DD)", text: $targetDateString)

                if tier != "long_term" {
                    Picker("Parent Goal", selection: $parentId) {
                        Text("None").tag(Optional<Int>.none)
                        ForEach(viewModel.goals.filter { $0.tier == parentTier(for: tier) }) { goal in
                            Text(goal.title).tag(Optional(goal.id))
                        }
                    }
                }
            }
            .formStyle(.grouped)

            HStack {
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Create") {
                    Task {
                        await viewModel.createGoal(
                            title: title,
                            tier: tier,
                            description: description,
                            category: category,
                            parentId: parentId,
                            priority: priority,
                            targetDate: targetDateString.isEmpty ? nil : targetDateString
                        )
                        dismiss()
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding()
        }
        .frame(width: 400)
    }

    private func parentTier(for tier: String) -> String {
        switch tier {
        case "mid_term": return "long_term"
        case "short_term": return "mid_term"
        default: return ""
        }
    }
}

// MARK: - Edit Sheet

struct GoalEditSheet: View {
    let goal: GoalItem
    @Bindable var viewModel: GoalsViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var title: String
    @State private var description: String
    @State private var category: String
    @State private var priority: String
    @State private var targetDateString: String

    let categories = ["career", "personal", "health", "financial", "networking", "learning", ""]

    init(goal: GoalItem, viewModel: GoalsViewModel) {
        self.goal = goal
        self.viewModel = viewModel
        _title = State(initialValue: goal.title)
        _description = State(initialValue: goal.description)
        _category = State(initialValue: goal.category)
        _priority = State(initialValue: goal.priority)
        _targetDateString = State(initialValue: goal.targetDate?.prefix(10).description ?? "")
    }

    var body: some View {
        VStack(spacing: 0) {
            Text("Edit Goal")
                .font(.title3.bold())
                .padding()

            Form {
                TextField("Title", text: $title)
                Picker("Priority", selection: $priority) {
                    Text("High").tag("high")
                    Text("Medium").tag("medium")
                    Text("Low").tag("low")
                }
                Picker("Category", selection: $category) {
                    Text("None").tag("")
                    ForEach(categories.filter { !$0.isEmpty }, id: \.self) { cat in
                        Text(cat.capitalized).tag(cat)
                    }
                }
                TextField("Description", text: $description, axis: .vertical)
                    .lineLimit(3...6)
                TextField("Target Date (YYYY-MM-DD)", text: $targetDateString)
            }
            .formStyle(.grouped)

            HStack {
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Save") {
                    Task {
                        await viewModel.updateGoal(
                            id: goal.id,
                            title: title != goal.title ? title : nil,
                            description: description != goal.description ? description : nil,
                            category: category != goal.category ? category : nil,
                            priority: priority != goal.priority ? priority : nil,
                            targetDate: targetDateString.isEmpty ? nil : targetDateString
                        )
                        dismiss()
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding()
        }
        .frame(width: 400)
    }
}

// MARK: - Daily Review Banner

struct DailyReviewBanner: View {
    @Bindable var viewModel: GoalsViewModel
    @State private var showReviewSheet = false

    var body: some View {
        HStack {
            Image(systemName: "checkmark.circle.badge.questionmark")
                .foregroundColor(.accentColor)
            Text("Daily review is due")
                .font(.callout.bold())
            Spacer()
            Button("Start Review") {
                showReviewSheet = true
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .sheet(isPresented: $showReviewSheet) {
            DailyReviewSheet(viewModel: viewModel)
        }
    }
}

// MARK: - Daily Review Sheet

struct DailyReviewSheet: View {
    @Bindable var viewModel: GoalsViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var userResponse = ""
    @State private var summary: String?

    var body: some View {
        VStack(spacing: 16) {
            Text("Daily Review")
                .font(.title2.bold())

            if viewModel.isReviewStreaming || !viewModel.reviewStreamText.isEmpty {
                ScrollView {
                    Text(viewModel.reviewStreamText)
                        .font(.body)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding()
                }
                .frame(maxHeight: 300)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.05)))
            } else if summary == nil {
                ContentUnavailableView(
                    "Ready for Review",
                    systemImage: "text.document",
                    description: Text("Tap 'Generate' to create your daily review.")
                )
            }

            if let summary {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Summary")
                        .font(.headline)
                    Text(summary)
                        .font(.body)
                        .foregroundColor(.secondary)
                }
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.green.opacity(0.05)))
            }

            if !viewModel.isReviewStreaming && !viewModel.reviewStreamText.isEmpty && summary == nil {
                TextEditor(text: $userResponse)
                    .font(.body)
                    .frame(height: 100)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Color.secondary.opacity(0.3))
                    )
                    .overlay(alignment: .topLeading) {
                        if userResponse.isEmpty {
                            Text("How did your day go? What progress did you make?")
                                .font(.body)
                                .foregroundColor(.secondary.opacity(0.5))
                                .padding(.horizontal, 5)
                                .padding(.vertical, 8)
                                .allowsHitTesting(false)
                        }
                    }
            }

            HStack {
                Button("Close") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Spacer()

                if viewModel.reviewStreamText.isEmpty && summary == nil {
                    Button("Generate Review") {
                        viewModel.startReview()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(viewModel.isReviewStreaming)
                } else if summary == nil {
                    Button("Submit Response") {
                        Task {
                            summary = await viewModel.respondReview(response: userResponse)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(userResponse.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                              || viewModel.isReviewStreaming)
                }
            }
        }
        .padding()
        .frame(width: 500)
        .frame(minHeight: 400)
    }
}

// MARK: - Infer Overlay

struct InferOverlay: View {
    @Bindable var viewModel: GoalsViewModel

    var body: some View {
        ZStack {
            Color.black.opacity(0.3)
                .ignoresSafeArea()

            VStack(spacing: 16) {
                Text("Inferring Goals...")
                    .font(.title3.bold())

                ScrollView {
                    Text(viewModel.inferStreamText)
                        .font(.body)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 300)
                .padding()
                .background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor)))

                HStack {
                    if viewModel.isInferring {
                        ProgressView()
                            .controlSize(.small)
                        Button("Cancel") {
                            viewModel.cancelStreaming()
                        }
                        .buttonStyle(.bordered)
                    } else {
                        Button("Done") {
                            Task { await viewModel.loadGoals() }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
            }
            .padding(24)
            .frame(width: 450)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color(nsColor: .windowBackgroundColor)))
            .shadow(radius: 20)
        }
    }
}
