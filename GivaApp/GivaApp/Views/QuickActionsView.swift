// QuickActionsView.swift - Bottom action bar with icon buttons for common operations.

import SwiftUI

struct QuickActionsView: View {
    @EnvironmentObject var viewModel: GivaViewModel

    var body: some View {
        HStack(spacing: 0) {
            // Primary actions
            ActionButton(
                icon: "arrow.triangle.2.circlepath",
                label: "Sync",
                isLoading: viewModel.isLoading
            ) {
                Task { await viewModel.triggerSync() }
            }

            ActionButton(icon: "sparkles", label: "Suggest") {
                viewModel.triggerSuggest()
            }
            .disabled(viewModel.isStreaming)

            ActionButton(icon: "checklist", label: "Extract") {
                Task { await viewModel.triggerExtract() }
            }
            .disabled(viewModel.isLoading)

            ActionButton(icon: "person.circle", label: "Profile") {
                Task { await viewModel.loadProfile() }
            }

            Divider()
                .frame(height: 20)
                .padding(.horizontal, 4)

            // Open CLI
            ActionButton(icon: "terminal", label: "CLI") {
                viewModel.openCLI()
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
    }
}

struct ActionButton: View {
    let icon: String
    let label: String
    var isLoading: Bool = false
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
        .foregroundColor(.secondary)
        .help(label)
    }
}
