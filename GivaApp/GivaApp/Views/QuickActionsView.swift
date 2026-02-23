// QuickActionsView.swift - Minimal bottom action bar with primary daily-use buttons.
//
// Follows Apple HIG: only essential, frequently-used actions are in the bottom bar.
// System actions (Restart, Upgrade, Reset, CLI) live in the header gear menu
// (see MainPanelView.swift).
//
// Primary actions:
//   Sync   — trigger manual email + calendar sync
//   Goals  — open the Goals & Objectives window
//   Review — start daily review (only visible when due)

import SwiftUI

struct QuickActionsView: View {
    @EnvironmentObject var viewModel: GivaViewModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        HStack(spacing: 0) {
            // Sync
            ActionButton(
                icon: "arrow.triangle.2.circlepath",
                label: "Sync",
                isLoading: viewModel.isSyncing
            ) {
                Task { await viewModel.triggerSync() }
            }
            .disabled(!viewModel.areActionsEnabled || viewModel.isSyncing)

            // Goals
            ActionButton(
                icon: viewModel.isDailyReviewDue ? "target.badge.clock" : "target",
                label: "Goals"
            ) {
                openWindow(id: "goals")
            }
            .disabled(!viewModel.areActionsEnabled)

            // Daily Review (conditionally visible when due)
            if viewModel.isDailyReviewDue {
                ActionButton(
                    icon: "text.badge.checkmark",
                    label: "Review"
                ) {
                    openWindow(id: "goals")
                }
                .disabled(!viewModel.areActionsEnabled)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
    }
}

// MARK: - Action Button

struct ActionButton: View {
    let icon: String
    let label: String
    var isLoading: Bool = false
    var tint: Color? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 2) {
                if isLoading {
                    ProgressView()
                        .controlSize(.small)
                        .frame(width: 18, height: 18)
                } else {
                    Image(systemName: icon)
                        .font(.system(size: 14))
                        .frame(width: 18, height: 18)
                }
                Text(label)
                    .font(.system(size: 9))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundColor(tint ?? .secondary)
        .help(label)
    }
}
