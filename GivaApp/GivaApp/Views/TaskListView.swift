// TaskListView.swift - List of pending tasks with priority indicators and actions.

import SwiftUI

struct TaskListView: View {
    @Environment(GivaViewModel.self) private var viewModel
    /// Callback when a task is selected (main window sidebar navigation).
    var onSelectTask: ((Int) -> Void)? = nil

    var body: some View {
        Group {
            if viewModel.isLoadingTasks {
                VStack {
                    ProgressView()
                        .controlSize(.small)
                    Text("Loading tasks...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if viewModel.tasks.isEmpty && viewModel.dismissedTasks.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "checkmark.circle")
                        .font(.system(size: 32))
                        .foregroundColor(.secondary.opacity(0.5))
                    Text("No pending tasks")
                        .font(.callout)
                        .foregroundColor(.secondary)
                    Text("Tasks will appear as Giva identifies\nthem from your emails and calendar.")
                        .font(.caption)
                        .foregroundColor(.secondary.opacity(0.7))
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(.top, 40)
            } else {
                ScrollView {
                    LazyVStack(spacing: 4) {
                        ForEach(viewModel.tasks) { task in
                            TaskRow(task: task, onStatusChange: { status in
                                Task {
                                    await viewModel.updateTaskStatus(taskId: task.id, status: status)
                                }
                            }, onSelect: {
                                if let select = onSelectTask {
                                    select(task.id)
                                } else {
                                    // In popover: open full window with task selected
                                    viewModel.pendingTaskChatId = task.id
                                }
                            })
                        }
                    }
                    .padding(8)

                    // Dismissed tasks undo queue
                    if !viewModel.dismissedTasks.isEmpty {
                        DismissedTasksSection()
                    }
                }
            }
        }
        .onAppear {
            Task {
                await viewModel.loadTasks()
                await viewModel.loadDismissedTasks()
            }
        }
    }
}

// MARK: - Dismissed Tasks Undo Queue

struct DismissedTasksSection: View {
    @Environment(GivaViewModel.self) private var viewModel

    var body: some View {
        @Bindable var vm = viewModel
        VStack(spacing: 0) {
            // Toggle header
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    vm.showDismissedTasks.toggle()
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: vm.showDismissedTasks
                          ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(.secondary)
                    Text("Dismissed")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(.secondary)
                    Text("(\(viewModel.dismissedTasks.count))")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary.opacity(0.7))
                    Spacer()
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 6)
            }
            .buttonStyle(.plain)

            if vm.showDismissedTasks {
                LazyVStack(spacing: 2) {
                    ForEach(viewModel.dismissedTasks) { task in
                        DismissedTaskRow(task: task, onRestore: {
                            Task { await viewModel.restoreTask(taskId: task.id) }
                        })
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 8)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }
}

struct DismissedTaskRow: View {
    let task: DismissedTaskItem
    let onRestore: () -> Void

    @State private var isHovering = false

    var body: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(task.title)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .strikethrough(true, color: .secondary.opacity(0.4))

                if !task.dismissalReason.isEmpty {
                    Text(task.dismissalReason)
                        .font(.system(size: 9))
                        .foregroundColor(.secondary.opacity(0.6))
                        .lineLimit(1)
                }
            }

            Spacer()

            if isHovering {
                Button(action: onRestore) {
                    Text("Restore")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(.accentColor)
                }
                .buttonStyle(.borderless)
                .help("Restore this task")
                .transition(.opacity)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .background(isHovering ? Color.primary.opacity(0.03) : Color.clear)
        .cornerRadius(4)
        .onHover { hovering in
            withAnimation(.easeInOut(duration: 0.15)) {
                isHovering = hovering
            }
        }
    }
}

// MARK: - Task Row

struct TaskRow: View {
    let task: TaskItem
    let onStatusChange: (String) -> Void
    var onSelect: (() -> Void)? = nil

    @State private var isHovering = false

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            // Priority indicator
            Circle()
                .fill(priorityColor)
                .frame(width: 8, height: 8)
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: 2) {
                Text(task.title)
                    .font(.system(size: 12, weight: .medium))
                    .lineLimit(2)

                HStack(spacing: 6) {
                    if let dueDate = task.formattedDueDate {
                        Label(dueDate, systemImage: "calendar")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }

                    Text(task.priority.capitalized)
                        .font(.system(size: 9, weight: .medium))
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(priorityColor.opacity(0.15))
                        .foregroundColor(priorityColor)
                        .cornerRadius(3)

                    Text(task.sourceType.capitalized)
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                }
            }

            Spacer()

            // Action buttons (visible on hover)
            if isHovering {
                HStack(spacing: 4) {
                    Button(action: { onStatusChange("done") }) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 16))
                            .foregroundColor(.green)
                    }
                    .buttonStyle(.borderless)
                    .help("Mark as done")

                    Button(action: { onStatusChange("dismissed") }) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 16))
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .help("Dismiss")
                }
                .transition(.opacity)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(isHovering ? Color.primary.opacity(0.04) : Color.clear)
        )
        .onHover { hovering in
            withAnimation(.easeInOut(duration: 0.15)) {
                isHovering = hovering
            }
        }
        .onTapGesture {
            if let select = onSelect { select() }
        }
        .contextMenu {
            Button {
                if let select = onSelect { select() }
            } label: {
                Label("View Details", systemImage: "info.circle")
            }

            Divider()

            Button {
                onStatusChange("done")
            } label: {
                Label("Mark as Done", systemImage: "checkmark.circle")
            }

            Button {
                onStatusChange("dismissed")
            } label: {
                Label("Dismiss", systemImage: "xmark.circle")
            }
        }
    }

    private var priorityColor: Color {
        switch task.priority {
        case "high": return .red
        case "medium": return .orange
        case "low": return .gray
        default: return .primary
        }
    }
}
