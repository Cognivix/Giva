// QuickActionsView.swift - Bottom action bar with icon buttons for common operations.

import SwiftUI

struct QuickActionsView: View {
    @EnvironmentObject var viewModel: GivaViewModel
    @State private var showResetConfirmation = false

    var body: some View {
        VStack(spacing: 0) {
            // Inline reset confirmation (avoids .alert which breaks in MenuBarExtra)
            if showResetConfirmation {
                VStack(spacing: 8) {
                    Text("Reset All Data?")
                        .font(.system(size: 12, weight: .semibold))

                    Text("This will delete all data, caches, and settings. You will need to set up models again.")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .lineLimit(3)

                    HStack(spacing: 12) {
                        Button("Cancel") {
                            showResetConfirmation = false
                        }
                        .buttonStyle(.plain)
                        .foregroundColor(.secondary)

                        Button("Reset") {
                            showResetConfirmation = false
                            Task { await viewModel.triggerReset() }
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.red)
                        .controlSize(.small)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity)
                .background(Color(nsColor: .controlBackgroundColor))

                Divider()
            }

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

                // Reset all data
                ActionButton(
                    icon: "arrow.counterclockwise",
                    label: "Reset",
                    isLoading: viewModel.isResetting
                ) {
                    showResetConfirmation = true
                }
                .disabled(viewModel.isResetting || viewModel.isStreaming)

                // Open CLI
                ActionButton(icon: "terminal", label: "CLI") {
                    viewModel.openCLI()
                }

                // Upgrade backend
                ActionButton(
                    icon: "arrow.triangle.2.circlepath.circle",
                    label: "Upgrade",
                    isLoading: viewModel.isUpgrading
                ) {
                    viewModel.triggerUpgrade()
                }
                .disabled(viewModel.isUpgrading || viewModel.isStreaming)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
        }
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
